"""Mechanics measurements built from telemetry events.

Everything here is labelled by how it was obtained:
- "measured": read directly from telemetry (touch times, heights, ball speed, air state, boost).
- "estimate": derived with an approximation (shot-on-target projection, kickoff/challenge outcome).
- "not_measured": needs inputs or video we don't have (air-roll skill, wall-read quality, wavedash).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from rocketcoach.analysis.context import Context
from rocketcoach.analysis.events import Event
from rocketcoach.analysis.observations import Observation
from rocketcoach.telemetry.models import FIELD_Y, team_sign

NOT_MEASURED = {
    "air_roll": "Controller roll input alone doesn't show skill; would need orientation-goal analysis.",
    "wavedash_halfflip": "Needs reliable dodge-direction + landing analysis; not yet validated on real data.",
    "wall_read_quality": "Needs replay/video review of approach paths; only wall touch counts are logged.",
    "flip_reset": "Detectable from jump state but not validated; ignored for grading.",
}


@dataclass
class MechStats:
    touches: int = 0
    shots: int = 0
    shots_on_target: int = 0
    shot_speeds: list[int] = field(default_factory=list)
    corner_placements: int = 0
    attacking_touches_off_target: int = 0
    aerials: list[dict] = field(default_factory=list)
    recoveries: list[float] = field(default_factory=list)
    recoveries_regained: int = 0
    kickoffs: list[dict] = field(default_factory=list)
    challenges: list[str] = field(default_factory=list)
    dribbles: int = 0
    flicks: int = 0
    backboard_touches: int = 0
    wall_touches: int = 0
    goals: int = 0
    goals_against: int = 0

    def to_dict(self) -> dict:
        aer = self.aerials
        contacts = sum(1 for a in aer if a["contact"])
        return {
            "touches": self.touches,
            "shots": self.shots, "shots_on_target": self.shots_on_target,
            "avg_shot_speed": round(sum(self.shot_speeds) / len(self.shot_speeds)) if self.shot_speeds else None,
            "corner_placements": self.corner_placements,
            "attacking_touches_off_target": self.attacking_touches_off_target,
            "aerial_attempts": len(aer), "aerial_contacts": contacts,
            "aerial_double_jump_takeoffs": sum(1 for a in aer if a.get("double_jump")),
            "avg_takeoff_dist_hit": _avg([a["takeoff_dist"] for a in aer if a["contact"]]),
            "avg_takeoff_dist_miss": _avg([a["takeoff_dist"] for a in aer if not a["contact"]]),
            "recoveries": len(self.recoveries), "avg_recovery_s": _avg(self.recoveries, 2),
            "recoveries_regained": self.recoveries_regained,
            "kickoffs": len(self.kickoffs),
            "kickoffs_won": sum(1 for k in self.kickoffs if k["result"] == "won"),
            "kickoffs_lost": sum(1 for k in self.kickoffs if k["result"] == "lost"),
            "challenges": len(self.challenges),
            "challenges_won": self.challenges.count("won"), "challenges_lost": self.challenges.count("lost"),
            "dribbles": self.dribbles, "flicks": self.flicks,
            "backboard_touches": self.backboard_touches, "wall_touches": self.wall_touches,
            "goals": self.goals, "goals_against": self.goals_against,
            "not_measured": NOT_MEASURED,
        }


def _avg(xs: list[float], nd: int = 0) -> float | None:
    return round(sum(xs) / len(xs), nd) if xs else None


class MechanicsAnalyzer:
    def __init__(self, my_index: int) -> None:
        self.my_index = my_index
        self.s = MechStats()
        self._recent_aerials: deque[dict] = deque(maxlen=5)
        self._recent_recov: deque[float] = deque(maxlen=4)
        self._aerial_tip_at = -1e9
        self._recov_tip_at = -1e9

    def update(self, ctx: Context | None, events: list[Event]) -> list[Observation]:
        out: list[Observation] = []
        me_team = ctx.me.team if ctx else None
        for e in events:
            d = e.data
            if e.type == "touch" and d["car"] == self.my_index:
                self.s.touches += 1
                x, y, z = d["loc"]
                if abs(x) > 3900 and z > 250:
                    self.s.wall_touches += 1
                if me_team is not None and team_sign(me_team) * y < -(FIELD_Y - 300) and z > 700:
                    self.s.backboard_touches += 1
                if me_team is not None and team_sign(me_team) * y > FIELD_Y * 0.45 and not d["on_target"]:
                    self.s.attacking_touches_off_target += 1
            elif e.type == "shot" and d["car"] == self.my_index:
                self.s.shots += 1
                self.s.shots_on_target += 1  # 'shot' events are on-target projections
                self.s.shot_speeds.append(d["ball_speed"])
                if d["cross_x"] is not None and abs(d["cross_x"]) > 500:
                    self.s.corner_placements += 1
            elif e.type == "aerial" and d["car"] == self.my_index:
                self.s.aerials.append(d)
                self._recent_aerials.append(d)
                out += self._aerial_tip(e.time)
            elif e.type == "recovery":
                self.s.recoveries.append(d["seconds"])
                self.s.recoveries_regained += int(d["regained"])
                self._recent_recov.append(d["seconds"])
                out += self._recovery_tip(e.time)
            elif e.type == "kickoff_result":
                self.s.kickoffs.append(d)
                # live kickoff feedback comes from analysis/kickoff.py (spawn, arrival times, direction)
            elif e.type == "challenge":
                self.s.challenges.append(d["result"])
            elif e.type == "dribble":
                self.s.dribbles += 1
                self.s.flicks += int(d["flick"])
            elif e.type == "goal" and ctx is not None:
                if d["for_me"]:
                    if d.get("last_toucher") == self.my_index:
                        self.s.goals += 1
                else:
                    self.s.goals_against += 1
        return out

    def _aerial_tip(self, t: float) -> list[Observation]:
        rec = list(self._recent_aerials)
        misses = [a for a in rec if not a["contact"]]
        if len(rec) >= 4 and len(misses) >= 3 and t - self._aerial_tip_at > 120:
            self._aerial_tip_at = t
            far = _avg([a["takeoff_dist"] for a in misses])
            tip = "Aerials: jump later, closer in" if far and far > 900 else "Aerials: line up before takeoff"
            return [Observation(
                "aerial_whiffs", "mechanics", t, tip,
                f"Missed {len(misses)} of your last {len(rec)} aerials.",
                impact=0.4, confidence=0.6, evidence={"recent": rec})]
        return []

    def _recovery_tip(self, t: float) -> list[Observation]:
        rec = list(self._recent_recov)
        slow = [r for r in rec if r > 1.5]
        if len(rec) >= 4 and len(slow) >= 3 and t - self._recov_tip_at > 120:
            self._recov_tip_at = t
            return [Observation(
                "slow_recoveries", "mechanics", t, "Land on wheels, keep momentum",
                f"{len(slow)} of {len(rec)} landings slow to recover.",
                impact=0.35, confidence=0.55, evidence={"recent_s": rec})]
        return []
