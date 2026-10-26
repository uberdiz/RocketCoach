"""What to do with the ball: shoot, pass, center, control, clear or push, learned from every player.

Every touch in a match (yours, your teammate's and the opponents') is turned into a sample:
  situation  = zone on the field + pressure + whether a defender is back + whether a teammate is open,
               always seen from the toucher's own side of the field;
  action     = what the touch did to the ball (classified from the ball's motion right after it);
  value      = what happened in the next 4 s (goal +1, shot on target +0.5, keeping the ball +0.15,
               losing it -0.15, conceding a shot -0.6, conceding a goal -1).
Per situation the model averages values per action, starting from a coaching prior that real data overrides
as samples accumulate. Bots and you both feed it, so it learns from how goals are actually created.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from rocketcoach.analysis.events import Event
from rocketcoach.analysis.observations import Observation
from rocketcoach.telemetry.models import FIELD_Y, CarState, GameFrame, Vec3, opp_goal, team_sign

ACTIONS = ("shoot", "center", "pass", "control", "clear", "push")
PRIOR_WEIGHT = 4.0  # pseudo-samples given to the prior
OUTCOME_WINDOW = 4.0
MIN_EVIDENCE = 6  # real samples needed before the model contradicts you

LABEL = {"shoot": "Shooting", "center": "Centering", "pass": "Passing", "control": "Controlling it",
         "clear": "Clearing", "push": "Pushing it upfield", "other": "That touch"}
DO = {
    "shoot": "Shoot, aim far post",
    "center": "Center it in front of goal",
    "pass": "Pass, teammate is open",
    "control": "Control it, you have space",
    "clear": "Clear it to the side wall",
    "push": "Push it upfield to the corner",
}
DO_NEW = {  # simpler wording for newer players
    "shoot": "Shoot at the goal",
    "center": "Hit it to the middle in front of goal",
    "pass": "Pass to your teammate",
    "control": "Slow down, take a controlled touch",
    "clear": "Hit it away to the side wall",
    "push": "Hit it toward their corner",
}

_PRIOR = {
    "att_center": {"shoot": .45, "center": .15, "pass": .2, "control": .2, "push": .1, "clear": -.1},
    "att_wide": {"shoot": .15, "center": .3, "pass": .25, "control": .2, "push": .1, "clear": -.1},
    "att_corner": {"shoot": -.05, "center": .35, "pass": .25, "control": .15, "push": .05, "clear": -.1},
    "mid_center": {"shoot": .05, "center": .05, "pass": .15, "control": .2, "push": .15, "clear": .05},
    "mid_wide": {"shoot": -.05, "center": .1, "pass": .15, "control": .15, "push": .15, "clear": .05},
    "def_center": {"shoot": -.2, "center": -.2, "pass": .1, "control": .05, "push": .05, "clear": .1},
    "def_wide": {"shoot": -.2, "center": -.2, "pass": .1, "control": .05, "push": .05, "clear": .15},
}


@dataclass(slots=True)
class Situation:
    zone: str
    pressured: bool
    keeper: bool  # an opponent is back between the ball and their goal
    mate_open: bool
    airborne: bool

    @property
    def key(self) -> str:
        return f"{self.zone}|{'press' if self.pressured else 'space'}|{'keeper' if self.keeper else 'open'}|" \
               f"{'mate' if self.mate_open else 'nomate'}"

    def allowed(self) -> tuple[str, ...]:
        if self.zone.startswith("def"):
            return tuple(a for a in ACTIONS if a not in ("center", "shoot"))
        return ACTIONS


def zone_of(ball: Vec3, team: int) -> str:
    s = team_sign(team)
    y, x = ball.y * s, abs(ball.x)
    if y > FIELD_Y * 0.3:
        if y > 3800 and x > 1500:
            return "att_corner"
        return "att_center" if x < 1300 else "att_wide"
    if y > -FIELD_Y * 0.3:
        return "mid_center" if x < 1300 else "mid_wide"
    return "def_center" if x < 1300 else "def_wide"


def situation(f: GameFrame, toucher: CarState) -> Situation:
    ball = f.ball.location if f.ball else toucher.location
    s = team_sign(toucher.team)
    opps = [c for c in f.cars if c.team != toucher.team and not c.is_demolished]
    mate = next((c for c in f.cars if c.team == toucher.team and c.index != toucher.index and not c.is_demolished),
                None)
    press = any(o.location.dist(ball) < 1200 for o in opps)
    goal = opp_goal(toucher.team)
    keeper = any(s * o.location.y > s * ball.y and o.location.flat_dist(goal) < 3000 for o in opps)
    # open = in a useful spot (level with the ball, or anywhere in the attacking area) and unmarked
    mate_open = mate is not None and s * mate.location.y > min(s * ball.y - 500, 1500) and \
        all(o.location.dist(mate.location) > 1100 for o in opps)
    return Situation(zone_of(ball, toucher.team), press, keeper, mate_open, ball.z > 300)


def classify(f: GameFrame, toucher: CarState, on_target: bool) -> str:
    if f.ball is None:
        return "other"
    b, v = f.ball.location, f.ball.velocity
    s = team_sign(toucher.team)
    if on_target:
        return "shoot"
    if s * b.y < -1500 and s * v.y > 500:
        return "clear"
    p1 = b + v * 1.0  # where the ball is heading within a second: the area in front of goal, post to post+
    toward_mouth = abs(p1.x) < 1600 and s * p1.y > 3000
    if s * b.y > 1500 and toward_mouth and (abs(b.x) > 1300 or s * b.y > 4200):
        return "center"
    mate = next((c for c in f.cars if c.team == toucher.team and c.index != toucher.index), None)
    if mate is not None and v.flat().length() > 600:
        to_mate = (mate.location - b).flat()
        if to_mate.length() > 800 and v.flat().normalized().dot(to_mate.normalized()) > 0.9:
            return "pass"
    if (v - toucher.velocity).length() < 550 and v.length() < 1600:
        return "control"
    if s * v.y > 400:
        return "push"
    return "other"


@dataclass
class _Stat:
    n: int = 0
    total: float = 0.0
    good: int = 0


class DecisionModel:
    def __init__(self) -> None:
        self.stats: dict[tuple[str, str], _Stat] = defaultdict(_Stat)
        self.samples = 0

    def add(self, key: str, action: str, value: float) -> None:
        st = self.stats[(key, action)]
        st.n += 1
        st.total += value
        st.good += int(value >= 0.3)
        self.samples += 1

    def load(self, rows: list[tuple[str, str, int, float, int]]) -> None:
        """rows: (bucket, action, n, sum_value, good) aggregated from the database."""
        for key, action, n, total, good in rows:
            st = self.stats[(key, action)]
            st.n += int(n)
            st.total += float(total)
            st.good += int(good)
            self.samples += int(n)

    @staticmethod
    def prior(sit: Situation, action: str) -> float:
        p = _PRIOR.get(sit.zone, {}).get(action, 0.0)
        if action == "control" and sit.pressured:
            p -= 0.15
        if action == "control" and sit.airborne:
            p -= 0.1
        if action == "shoot":
            p += -0.15 if sit.keeper else 0.15
        if action in ("pass", "center"):
            p += 0.1 if sit.mate_open else -0.15
        return p

    def expected(self, sit: Situation, action: str) -> float:
        st = self.stats.get((sit.key, action))
        n, total = (st.n, st.total) if st else (0, 0.0)
        return (self.prior(sit, action) * PRIOR_WEIGHT + total) / (PRIOR_WEIGHT + n)

    def evidence(self, sit: Situation, action: str) -> _Stat:
        return self.stats.get((sit.key, action)) or _Stat()

    def ranked(self, sit: Situation) -> list[tuple[str, float]]:
        return sorted(((a, self.expected(sit, a)) for a in sit.allowed()), key=lambda t: -t[1])


@dataclass
class _Pending:
    t: float
    team: int
    car: int
    actor: str
    sit: Situation
    action: str
    value: float = 0.0
    shot_for: bool = False
    shot_against: bool = False
    last_team: int | None = None
    done: bool = False


@dataclass
class DecisionTracker:
    my_index: int
    model: DecisionModel
    level: str = "high"
    finished: list[dict] = field(default_factory=list)  # samples to persist
    mine: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    _pending: list[_Pending] = field(default_factory=list)
    _feedback_at: float = -1e9

    def update(self, f: GameFrame, events: list[Event]) -> list[Observation]:
        out: list[Observation] = []
        me = f.car(self.my_index)
        for e in events:
            if e.type == "touch":
                car = f.car(e.data["car"])
                if car is None:
                    continue
                for p in self._pending:  # a new touch settles who had the ball for earlier samples
                    p.last_team = car.team
                actor = "me" if car.index == self.my_index else \
                    "mate" if me is not None and car.team == me.team else "opp"
                sit = situation(f, car)
                self._pending.append(_Pending(e.time, car.team, car.index, actor, sit,
                                              classify(f, car, e.data["on_target"]), last_team=car.team))
            elif e.type == "shot":
                for p in self._pending:
                    if e.data["team"] == p.team:
                        p.shot_for = True
                    else:
                        p.shot_against = True
            elif e.type == "goal":
                for p in self._pending:
                    p.value = 1.0 if e.data["team"] == p.team else -1.0
                    p.done = True
        for p in self._pending:
            if not p.done and f.time - p.t >= OUTCOME_WINDOW:
                v = (0.5 if p.shot_for else 0.0) - (0.6 if p.shot_against else 0.0)
                v += 0.15 if p.last_team == p.team else -0.15
                p.value = max(-1.0, min(1.0, v))
                p.done = True
        still = []
        for p in self._pending:
            if not p.done:
                still.append(p)
                continue
            if p.action == "other":
                continue
            fb = self._feedback(p) if p.actor == "me" else None
            self.model.add(p.sit.key, p.action, p.value)
            self.finished.append({"actor": p.actor, "bucket": p.sit.key, "action": p.action,
                                  "value": round(p.value, 2), "zone": p.sit.zone, "t": round(p.t, 1)})
            if p.actor == "me":
                self.mine[p.action].append(p.value)
            if fb:
                out.append(fb)
        self._pending = still
        return out

    def _feedback(self, p: _Pending) -> Observation | None:
        """Compare what you did with what has worked best from the same situation (before adding this sample)."""
        if p.t - self._feedback_at < 20:
            return None
        ranked = self.model.ranked(p.sit)
        best, best_v = ranked[0]
        mine_v = self.model.expected(p.sit, p.action)
        ev_best, ev_mine = self.model.evidence(p.sit, best), self.model.evidence(p.sit, p.action)
        if best != p.action and best_v - mine_v > 0.2 and ev_best.n >= MIN_EVIDENCE and p.value <= 0.15:
            self._feedback_at = p.t
            why = f"{LABEL[p.action]} here worked {ev_mine.good}/{ev_mine.n}; {LABEL[best].lower()} " \
                  f"{ev_best.good}/{ev_best.n}." if ev_mine.n else \
                  f"{LABEL[best]} from there worked {ev_best.good}/{ev_best.n} times."
            return Observation("decision_better", "offense", p.t, f"Better there: {best}", why, 0.4, 0.6,
                               kind="review", cooldown=20.0, evidence={"bucket": p.sit.key, "did": p.action,
                                                                       "better": best},
                               next_time=(DO_NEW if self.level == "new" else DO)[best] + ".")
        if p.action == best and p.value >= 0.5:
            self._feedback_at = p.t
            return Observation("decision_good", "offense", p.t, f"Good {p.action}", "Best option there.",
                               0.25, 0.7, kind="strength", cooldown=25.0)
        return None

    def advise(self, f: GameFrame, me: CarState) -> tuple[str, str] | None:
        """Best action if you were to touch the ball now: (action, overlay text)."""
        if f.ball is None:
            return None
        sit = situation(f, me)
        action = self.model.ranked(sit)[0][0]
        return action, (DO_NEW if self.level == "new" else DO)[action]


def summarize(samples: list[dict], model: DecisionModel) -> dict[str, Any]:
    """Your decisions this match vs what the model (all players, all matches) rates best."""
    mine = [s for s in samples if s["actor"] == "me"]
    by_action: dict[str, list[float]] = defaultdict(list)
    for s in mine:
        by_action[s["action"]].append(s["value"])
    return {
        "my_touches_classified": len(mine),
        "by_action": {a: {"n": len(v), "avg_value": round(sum(v) / len(v), 2)} for a, v in by_action.items()},
        "samples_this_match": len(samples),
        "model_samples": model.samples,
    }
