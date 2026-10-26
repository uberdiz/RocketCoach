"""Per-play feedback: how each 50/50 went and what to do differently next time.

Keeps a short history of where you were versus where the positioning guide wanted you, then explains key
moments using that history: every challenge you're part of, goals for and against, and turnovers that led
to a shot. Goal reviews are timed for the goal replay / countdown, when there's time to read them.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from rocketcoach.analysis.context import Context
from rocketcoach.analysis.events import Event
from rocketcoach.analysis.guide import Guidance
from rocketcoach.analysis.observations import Observation

ACTION_WORDS = {
    "shadow": "shadowing goal-side", "back_post": "rotating to the back post", "save": "getting into the net",
    "support": "supporting behind the play", "cover": "covering behind the 50/50", "challenge": "challenging",
    "attack": "playing the ball", "kickoff": "taking the kickoff", "cheat": "hanging back",
}


@dataclass(slots=True)
class Snap:
    t: float
    action: str
    dist: float
    on_spot: bool
    goal_side: bool
    last_man: bool
    boost: float


class PlayReviewer:
    def __init__(self, my_index: int) -> None:
        self.my_index = my_index
        self.history: deque[Snap] = deque(maxlen=60)  # ~6 s at 10 Hz
        self._last_snap = -1e9
        self._turnover: tuple[float, float] | None = None  # (time, my boost) when my touch gave the ball away
        self.challenges = {"won": 0, "lost": 0, "neutral": 0}
        self.team: int | None = None

    def update(self, ctx: Context | None, guide: Guidance | None, events: list[Event]) -> list[Observation]:
        if ctx is not None and guide is not None and ctx.frame.time - self._last_snap >= 0.1:
            from rocketcoach.analysis.context import goal_side
            self._last_snap = ctx.frame.time
            self.team = ctx.team
            self.history.append(Snap(ctx.frame.time, guide.action, guide.dist, guide.on_spot,
                                     goal_side(ctx.me, ctx.ball), ctx.i_am_last_man, ctx.me.boost))
        out: list[Observation] = []
        for e in events:
            if e.type == "challenge":
                out.append(self._challenge(e))
            elif e.type == "goal":  # the ball can be missing from goal-explosion packets, so no ctx needed
                out.append(self._goal(e))
            elif e.type == "possession_change" and e.data.get("from_car") == self.my_index:
                self._turnover = (e.time, ctx.me.boost if ctx else 0.0)
            elif e.type == "shot" and self.team is not None and e.data["team"] != self.team and self._turnover:
                if e.time - self._turnover[0] < 3.0:
                    out.append(self._turnover_review(e))
                self._turnover = None
        return out

    # ---- challenges ---------------------------------------------------------------------------
    def _challenge(self, e: Event) -> Observation:
        d = e.data
        res = d["result"]
        self.challenges[res] = self.challenges.get(res, 0) + 1
        reasons, fixes = [], []
        if d["boost"] < 20:
            reasons.append(f"went in with {d['boost']} boost")
            fixes.append("arrive with boost")
        if d["approach"] < 0.2 and not d["airborne"]:
            reasons.append("hit it from the side")
            fixes.append("square up, nose upfield")
        if d["speed"] < 900:
            reasons.append("nearly stopped")
            fixes.append("keep speed or wait for them")
        if d["last_man"] and not d["mate_cover"]:
            reasons.append("last back, no cover")
            fixes.append("last back: only go if clearly first")
        if res == "won":
            tip = "50/50 won: ball went upfield"
            if d["last_man"] and not d["mate_cover"]:
                tip = "50/50 won, but it was risky (last back)"
            expl = "Good contact." + (f" Risk: {reasons[-1]}." if d["last_man"] and not d["mate_cover"] else "")
            return Observation("challenge_won", "challenge", e.time, tip, expl, 0.35, 0.7, kind="strength",
                               evidence=d, cooldown=8.0,
                               next_time=fixes[-1].capitalize() + "." if d["last_man"] and not d["mate_cover"] else "")
        if res == "lost":
            why = reasons[0] if reasons else "they got better contact"  # the main reason only; the report keeps all
            nxt = (fixes[0].capitalize() + ".") if fixes else "Square up, hit the ball's center."
            return Observation("challenge_lost", "challenge", e.time, "50/50 lost", f"Why: {why}.",
                               0.55 if d["our_half"] else 0.4, 0.65, evidence=d, cooldown=6.0, next_time=nxt)
        return Observation("challenge_neutral", "challenge", e.time, "50/50 even: ball is loose",
                           "Neither side won it clearly.", 0.2, 0.6, kind="info", evidence=d, cooldown=10.0,
                           next_time="Recover fast and read where it drops.")

    # ---- goals --------------------------------------------------------------------------------
    def _window(self, t: float, start: float, end: float) -> list[Snap]:
        return [s for s in self.history if t - start <= s.t <= t - end]

    def _goal(self, e: Event) -> Observation:
        if e.data["for_me"]:
            mine = e.data.get("last_toucher") == self.my_index
            return Observation("goal_for", "offense", e.time, "Goal! Nice finish" if mine else "Goal: good support",
                               "Keep doing what led to it.", 0.3, 0.8, kind="strength", cooldown=0.0)
        snaps = self._window(e.time, 3.0, 0.8) or list(self.history)[-10:]
        if not snaps:
            return Observation("goal_against", "defense", e.time, "Goal against", "No positional history.",
                               0.9, 0.4, kind="review", cooldown=0.0)
        on = sum(1 for s in snaps if s.on_spot) / len(snaps)
        gs = sum(1 for s in snaps if s.goal_side) / len(snaps)
        avg_d = sum(s.dist for s in snaps) / len(snaps)
        wanted = max(set(s.action for s in snaps), key=lambda a: sum(1 for s in snaps if s.action == a))
        doing = ACTION_WORDS.get(wanted, wanted)
        if on >= 0.6 and gs >= 0.6:
            return Observation(
                "goal_against_in_position", "defense", e.time, "Goal against: you were in position",
                f"You were {doing} in the right spot.", 0.6, 0.6,
                kind="review", cooldown=0.0,
                next_time="Face the ball, stay grounded until they shoot.")
        where = "ahead of the ball" if gs < 0.5 else f"{avg_d:.0f}uu off"
        return Observation(
            "goal_against_out_of_position", "defense", e.time, "Goal against: you were out of position",
            f"Job: {doing}. You were {where}.",
            0.9, 0.7, kind="review", cooldown=0.0, evidence={"on_spot": round(on, 2), "goal_side": round(gs, 2),
                                                              "avg_dist": round(avg_d), "job": wanted},
            next_time="Their ball in your half: back post first.")

    def _turnover_review(self, e: Event) -> Observation:
        boost = self._turnover[1] if self._turnover else 0
        return Observation(
            "turnover_shot", "offense", e.time, "Your touch gave them a shot",
            "They shot within 3s of your touch.", 0.6, 0.6, kind="review",
            cooldown=20.0, evidence={"boost": round(boost)},
            next_time="Can't control it? Hit it to a corner.")
