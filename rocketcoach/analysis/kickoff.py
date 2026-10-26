"""Kickoff coach: which spawn you're on, who goes, what the other player does, and how it went.

Spawns are read from car positions during the countdown. Left/right are from your own point of view facing
the opponent goal (RLBot: blue's right corner is x = -2048). In 2v2 the closer car goes; on equal distance
(both diagonals) the left car goes, the usual convention. Arrival times are measured from the end of the
countdown until the car first gets within reach of the ball.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rocketcoach.analysis.events import Event
from rocketcoach.analysis.observations import Observation
from rocketcoach.telemetry.models import CarState, GameFrame, MatchPhase, Vec3, team_sign

REACH = 450.0  # closest approach (uu) that still counts as having gone for the ball
# Blue-side spawns (orange is mirrored). Blue faces +y; its right is -x.
SPAWNS = {(-2048, -2560): ("right diagonal", "diagonal"), (2048, -2560): ("left diagonal", "diagonal"),
          (-256, -3840): ("right off-center", "off-center"), (256, -3840): ("left off-center", "off-center"),
          (0, -4608): ("back center", "back center")}

@dataclass
class KickoffPlan:
    spawn: str  # e.g. "left diagonal"
    kind: str  # diagonal | off-center | back center
    role: str  # go | cheat | boost
    text: str
    target: Vec3
    mate_spawn: str = ""


@dataclass
class KickoffState:
    start: float | None = None  # countdown end (kickoff phase start)
    arrivals: dict[int, float] = field(default_factory=dict)
    closest: dict[int, tuple[float, float]] = field(default_factory=dict)  # car -> (min dist, time)
    first_touch_t: float | None = None
    plan: KickoffPlan | None = None
    kickers: dict[int, int] = field(default_factory=dict)  # team -> car index expected to go


def _side(rel_x: float, dead: float = 100.0) -> str:
    return "right" if rel_x < -dead else "left" if rel_x > dead else "center"


def spawn_of(car: CarState) -> tuple[str, str] | None:
    """The kickoff spawn the car is standing on, or None if it isn't on one (e.g. replay still running)."""
    s = team_sign(car.team)
    x, y = car.location.x * s, car.location.y * s
    for (sx, sy), label in SPAWNS.items():
        if abs(x - sx) < 200 and abs(y - sy) < 200:
            return label
    return None


def kicker_for(team: int, cars: list[CarState]) -> int | None:
    mates = [c for c in cars if c.team == team and not c.is_demolished]
    if not mates:
        return None
    mates.sort(key=lambda c: c.location.flat().length())
    if len(mates) > 1 and abs(mates[0].location.flat().length() - mates[1].location.flat().length()) < 60:
        lefts = [c for c in mates[:2] if _side(c.location.x * team_sign(team)) == "left"]
        if lefts:
            return lefts[0].index  # tie on the diagonals: left goes
    return mates[0].index


def _go_text(kind: str, level: str) -> str:
    if level == "new":
        return "Your kickoff: full boost at the ball, flip in at the end"
    if level == "mid":
        return "Your kickoff: boost, front-flip into the ball's center"
    if kind == "diagonal":
        return "Your kickoff: speedflip, hit the ball's center"
    if kind == "off-center":
        return "Your kickoff: speedflip through the small pad, hit center"
    return "Your kickoff: take the pad in front, then flip in"


def make_plan(f: GameFrame, my_index: int, level: str) -> KickoffPlan | None:
    me = f.car(my_index)
    if me is None or spawn_of(me) is None:
        return None
    spawn, kind = spawn_of(me)  # type: ignore[misc]
    mate = next((c for c in f.cars if c.team == me.team and c.index != me.index), None)
    goer = kicker_for(me.team, f.cars)
    s = team_sign(me.team)
    mate_spawn = (spawn_of(mate) or ("",))[0] if mate else ""
    if goer == me.index or mate is None:
        return KickoffPlan(spawn, kind, "go", _go_text(kind, level), Vec3(0, 0, 93), mate_spawn)
    if level == "new":
        side_x = 3072.0 if me.location.x * s > 0 else -3072.0
        return KickoffPlan(spawn, kind, "boost", "Teammate goes: grab your back corner boost, stay back",
                           Vec3(side_x * s, -4096 * s, 17), mate_spawn)
    return KickoffPlan(spawn, kind, "cheat", "Teammate goes: cheat up behind midfield, read the 50/50",
                       Vec3(0, -1700 * s, 17), mate_spawn)


class KickoffCoach:
    def __init__(self, my_index: int, level: str) -> None:
        self.my_index = my_index
        self.level = level
        self.st = KickoffState()
        self.samples: list[dict] = []
        self._prev_phase: MatchPhase | None = None

    @property
    def plan(self) -> KickoffPlan | None:
        return self.st.plan

    def update(self, f: GameFrame, events: list[Event]) -> list[Observation]:
        out: list[Observation] = []
        if f.phase == MatchPhase.COUNTDOWN and self._prev_phase != MatchPhase.COUNTDOWN:
            self.st = KickoffState()
        if f.phase == MatchPhase.COUNTDOWN and self.st.plan is None:
            # cars are teleported to the spawns a moment into the countdown: retry until they're on one
            self.st.plan = make_plan(f, self.my_index, self.level)
            if self.st.plan is not None:
                self.st.kickers = {t: k for t in (0, 1) if (k := kicker_for(t, f.cars)) is not None}
        if f.phase == MatchPhase.KICKOFF and self._prev_phase != MatchPhase.KICKOFF:
            self.st.start = f.time
            if self.st.plan is None:  # joined mid-countdown
                self.st.plan = make_plan(f, self.my_index, self.level)
                self.st.kickers = {t: k for t in (0, 1) if (k := kicker_for(t, f.cars)) is not None}
        track = self.st.start is not None and f.ball is not None and (
            self.st.first_touch_t is None or f.time - self.st.first_touch_t < 0.6)
        if track:
            for c in f.cars:
                t = c.latest_touch
                if t is not None and t.time >= self.st.start and c.index not in self.st.arrivals:
                    self.st.arrivals[c.index] = round(t.time - self.st.start, 2)  # exact contact time
                    if self.st.first_touch_t is None:
                        self.st.first_touch_t = t.time
                d = c.location.dist(f.ball.location)
                if d < self.st.closest.get(c.index, (1e9, 0.0))[0]:
                    self.st.closest[c.index] = (d, f.time)
        if self.st.start is not None and self.st.first_touch_t is not None and not track:
            for i, (d, t) in self.st.closest.items():  # cars that went but didn't touch: closest approach
                if i not in self.st.arrivals and d < REACH:
                    self.st.arrivals[i] = round(t - self.st.start, 2)
        self._prev_phase = f.phase
        for e in events:
            if e.type == "kickoff_result":
                obs = self._result(f, e)
                if obs:
                    out.append(obs)
        return out

    def _result(self, f: GameFrame, e: Event) -> Observation | None:
        plan, me = self.st.plan, f.car(self.my_index)
        if plan is None or me is None or f.ball is None:
            return None
        s = team_sign(me.team)
        bx, by = f.ball.location.x * s, f.ball.location.y * s
        side = _side(bx, 700)
        res = e.data["result"]
        my_arr = self.st.arrivals.get(self.my_index)
        opp_kicker = self.st.kickers.get(1 - me.team)
        opp_arr = self.st.arrivals.get(opp_kicker) if opp_kicker is not None else None
        sample = {"time": round(e.time, 1), "spawn": plan.spawn, "kind": plan.kind, "role": plan.role,
                  "my_arrival": my_arr, "opp_arrival": opp_arr, "result": res, "side": side,
                  "ball_y": round(by)}
        self.samples.append(sample)
        where = {"won": f"won {side}" if side != "center" else "won", "lost": f"lost {side}" if side != "center"
                 else "lost", "neutral": "even"}[res]
        tip = f"Kickoff {where}"
        why, nxt = "", ""
        if plan.role == "go" and my_arr is None:
            tip = f"Kickoff {where}: you were the kicker"
            why = f"You were closest ({plan.spawn}) but never reached the ball."
            nxt = "Closest car goes; on equal diagonals, left goes."
        elif plan.role == "go" and my_arr is not None and opp_arr is not None:
            diff = my_arr - opp_arr
            if diff > 0.08:
                why = f"They got there {diff:.2f}s sooner ({my_arr:.2f}s vs {opp_arr:.2f}s)."
                nxt = ("Hold boost all the way, flip a bit earlier." if self.level in ("new", "mid")
                       else "Speedflip, and don't turn before the flip.")
            elif diff < -0.08 and res != "won":
                why = f"You got there first ({my_arr:.2f}s) but lost the hit."
                nxt = "Hit through the ball's center, not its edge."
            elif res == "lost":
                why = f"Same arrival ({my_arr:.2f}s), they won the hit."
                nxt = "Flip later, right into the ball's center."
            elif res == "won":
                why = f"Arrived in {my_arr:.2f}s."
        elif plan.role != "go" and res == "lost":
            why = f"Ball went {side} into your half."
            nxt = "Be ready to pick it up there next kickoff." if plan.role == "cheat" else ""
        kind = "strength" if res == "won" else "mistake" if res == "lost" and plan.role == "go" else "info"
        return Observation("kickoff_feedback", "kickoff", e.time, tip, why or f"From {plan.spawn}.",
                           0.35 if res == "lost" else 0.25, 0.7, kind=kind, evidence=sample, cooldown=0.0,
                           next_time=nxt)
