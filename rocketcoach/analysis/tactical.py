"""Deterministic 2v2 positioning and decision rules.

Each rule looks at the whole situation (ball trajectory, who arrives first, where the teammate is,
opponent threat) rather than a single fixed condition, and requires the condition to persist for a
short time before reporting, which removes most single-frame noise. Confidence grows with how clear
the situation is.
"""

from __future__ import annotations

from rocketcoach.analysis.context import Context, goal_side, team_y
from rocketcoach.analysis.events import Event
from rocketcoach.analysis.observations import Observation
from rocketcoach.telemetry.models import FIELD_Y, own_goal, team_sign


SAME_SITUATION_S = 4.0  # re-triggers closer together than this are the same scramble, not a new mistake


class Persist:
    """Tracks how long a condition has been continuously true."""

    def __init__(self) -> None:
        self.since: float | None = None

    def update(self, cond: bool, t: float) -> float:
        if not cond:
            self.since = None
            return 0.0
        if self.since is None:
            self.since = t
        return t - self.since


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


class TacticalRules:
    def __init__(self) -> None:
        self.p_overcommit = Persist()
        self.p_double = Persist()
        self.p_exposed = Persist()
        self.p_ballside = Persist()
        self.p_passive = Persist()
        self.p_shadow = Persist()
        self.p_cover = Persist()
        self.stats = {"last_man_challenges": 0, "last_man_good": 0, "double_commits": 0, "exposed_s": 0.0,
                      "shadow_s": 0.0, "second_man_s": 0.0, "goal_side_s": 0.0, "active_s": 0.0}
        self._last_t: float | None = None
        self._last_report: dict[str, float] = {}

    def update(self, ctx: Context, events: list[Event]) -> list[Observation]:
        t = ctx.frame.time
        dt = 0.0 if self._last_t is None else min(0.2, max(0.0, t - self._last_t))
        self._last_t = t
        self.stats["active_s"] += dt
        if not ctx.me.is_demolished:
            speed = ctx.me.velocity.length()
            self.stats["speed_sum"] = self.stats.get("speed_sum", 0.0) + speed * dt
            if speed >= 2200:
                self.stats["supersonic_s"] = self.stats.get("supersonic_s", 0.0) + dt
        if goal_side(ctx.me, ctx.ball):
            self.stats["goal_side_s"] += dt
        out: list[Observation] = []
        for rule in (self._last_man_overcommit, self._double_commit, self._net_exposed,
                     self._ball_side_rotation, self._passive_support, self._shadow_defense):
            obs = rule(ctx, t, dt)
            if obs and t - self._last_report.get(obs.rule_id, -1e9) >= SAME_SITUATION_S:
                self._last_report[obs.rule_id] = t
                out.append(obs)
        return out

    # ---- rules --------------------------------------------------------------------------------
    def _last_man_overcommit(self, ctx: Context, t: float, dt: float) -> Observation | None:
        """Committing as last back when the opponent arrives first and nobody covers."""
        me, mate = ctx.me, ctx.mate
        if not ctx.is_2v2 or not ctx.i_am_last_man or ctx.ball_dist_own_goal > 6500:
            self.p_overcommit.update(False, t)
            return None
        to_ball = (ctx.ball - me.location).flat()
        closing = me.velocity.flat().dot(to_ball.normalized()) > 900 and to_ball.length() < 1800
        opp_first = ctx.ttb_opp <= ctx.ttb_me + 0.15
        ball_coming = team_sign(me.team) * ctx.ball_vel.y < 300  # not already being cleared upfield
        mate_cover = mate is not None and goal_side(mate, ctx.ball) and \
            mate.location.flat_dist(own_goal(me.team)) < ctx.ball_dist_own_goal
        cond = closing and opp_first and ball_coming and not mate_cover
        held = self.p_overcommit.update(cond, t)
        if closing and not opp_first and not mate_cover and held == 0.0 and ctx.ttb_me + 0.4 < ctx.ttb_opp:
            self.stats["last_man_good"] += dt  # you had it first: that's a correct challenge
        if held < 0.25 or held - dt >= 0.25:
            return None  # report once at the moment the condition becomes established
        self.stats["last_man_challenges"] += 1
        margin = ctx.ttb_me - ctx.ttb_opp
        conf = _clamp(0.55 + margin * 0.4 + (0.15 if ctx.ball_in_our_half else 0.0))
        arrival = "same time as you" if margin < 0.2 else f"{margin:.1f}s sooner"
        mate_note = "teammate upfield" if mate and not goal_side(mate, ctx.ball) else \
            "no cover"
        return Observation(
            "last_man_overcommit", "defense", t, "Last back: shadow, don't dive",
            f"Last back, they arrived {arrival}, {mate_note}.",
            impact=0.9 if ctx.ball_in_our_half else 0.65, confidence=conf,
            next_time="Last back: let them touch first, then challenge.",
            evidence={"ttb_me": round(ctx.ttb_me, 2), "ttb_opp": round(ctx.ttb_opp, 2),
                      "ball_dist_goal": round(ctx.ball_dist_own_goal)})

    def _double_commit(self, ctx: Context, t: float, dt: float) -> Observation | None:
        me, mate = ctx.me, ctx.mate
        if mate is None or mate.is_demolished:
            self.p_double.update(False, t)
            return None
        def closing(c):  # noqa: E306
            d = (ctx.ball - c.location).flat()
            return d.length() < 1000 and c.velocity.flat().dot(d.normalized()) > 700
        cond = closing(me) and closing(mate) and ctx.ttb_me > ctx.ttb_mate + 0.1 and ctx.ttb_mate < 1.0
        held = self.p_double.update(cond, t)
        if held < 0.3 or held - dt >= 0.3:
            return None
        self.stats["double_commits"] += 1
        conf = _clamp(0.5 + (ctx.ttb_me - ctx.ttb_mate) * 0.6)
        return Observation(
            "double_commit", "rotation", t, "Teammate's ball: peel off",
            f"Both of you went; teammate was {ctx.ttb_me - ctx.ttb_mate:.1f}s closer.",
            impact=0.7 if ctx.ball_in_our_half else 0.5, confidence=conf,
            next_time="Teammate closer? Peel back early.",
            evidence={"ttb_me": round(ctx.ttb_me, 2), "ttb_mate": round(ctx.ttb_mate, 2)})

    def _net_exposed(self, ctx: Context, t: float, dt: float) -> Observation | None:
        me, mate = ctx.me, ctx.mate
        dangerous = ctx.their_possession and ctx.ball_dist_own_goal < 4500
        nobody_back = not goal_side(me, ctx.ball) and (mate is None or not goal_side(mate, ctx.ball)
                                                       or mate.is_demolished)
        # Only coach the player who should be going back: the one nearer their own goal.
        responsible = mate is None or ctx.i_am_last_man or mate.is_demolished
        cond = dangerous and nobody_back and responsible
        held = self.p_exposed.update(cond, t)
        if cond:
            self.stats["exposed_s"] += dt
        if held < 0.4 or held - dt >= 0.4:
            return None
        return Observation(
            "net_exposed", "defense", t, "Net open: get goal-side now",
            "Their ball in your half, nobody goal-side.",
            impact=0.95, confidence=_clamp(0.6 + ctx.goal_threat * 0.35),
            next_time="Their ball in your half: back post first.",
            evidence={"ball_dist_goal": round(ctx.ball_dist_own_goal), "threat": round(ctx.goal_threat, 2)})

    def _ball_side_rotation(self, ctx: Context, t: float, dt: float) -> Observation | None:
        me = ctx.me
        retreating = team_sign(me.team) * me.velocity.y < -600
        wide_ball = abs(ctx.ball.x) > 1300
        same_side = me.location.x * ctx.ball.x > 0 and abs(me.location.x) > 900
        near_goal = me.location.flat_dist(own_goal(me.team)) < 3800
        cond = (retreating and wide_ball and same_side and near_goal and goal_side(me, ctx.ball)
                and not ctx.i_am_first_man and ctx.their_possession)
        held = self.p_ballside.update(cond, t)
        if held < 0.5 or held - dt >= 0.5:
            return None
        return Observation(
            "ball_side_rotation", "rotation", t, "Rotate back post",
            "You rotated on the ball side.",
            impact=0.5, confidence=0.6, next_time="Rotate on the far side.",
            evidence={"my_x": round(me.location.x), "ball_x": round(ctx.ball.x)})

    def _passive_support(self, ctx: Context, t: float, dt: float) -> Observation | None:
        me = ctx.me
        if not ctx.i_am_first_man:
            self.stats["second_man_s"] += dt
        attacking = ctx.our_possession and team_y(me.team, ctx.ball.y) > FIELD_Y * 1.3
        too_deep = team_y(me.team, me.location.y) < FIELD_Y * 0.55 and me.location.dist(ctx.ball) > 5000
        cond = ctx.is_2v2 and attacking and too_deep and not ctx.i_am_first_man and me.boost > 20
        held = self.p_passive.update(cond, t)
        if held < 3.0 or held - dt >= 3.0:
            return None
        return Observation(
            "passive_support", "offense", t, "Push up to midfield",
            "Teammate attacking, you're deep in your half.",
            impact=0.35, confidence=0.55, next_time="Follow up to midfield.",
            evidence={"dist_ball": round(me.location.dist(ctx.ball))})

    def _shadow_defense(self, ctx: Context, t: float, dt: float) -> Observation | None:
        """Positive reinforcement: controlled retreat goal-side while the opponent dribbles at you."""
        me = ctx.me
        d = me.location.flat_dist(ctx.ball)
        retreat_match = team_sign(me.team) * me.velocity.y < -300 and team_sign(me.team) * ctx.ball_vel.y < -300
        cond = (ctx.their_possession and goal_side(me, ctx.ball) and 700 < d < 2200 and retreat_match
                and ctx.ball_in_our_half)
        held = self.p_shadow.update(cond, t)
        if cond:
            self.stats["shadow_s"] += dt
        if held < 1.0 or held - dt >= 1.0:
            return None
        return Observation(
            "good_shadow", "defense", t, "Good shadow, stay patient",
            "Retreated at the ball's pace.", impact=0.4, confidence=0.6,
            kind="strength")
