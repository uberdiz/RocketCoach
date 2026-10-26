"""Continuous positioning guide: what to do *right now* and where to stand.

Every analysis tick this works out the player's job in the current 2v2 situation (take the kickoff,
challenge, shadow, rotate back post, support, save) and a target spot on the field. The overlay shows the
instruction as a single "Now:" line and draws the target on a minimap. Hysteresis keeps the text from
flickering when the situation sits on a boundary.
"""

from __future__ import annotations

from dataclasses import dataclass

from rocketcoach.analysis.context import Context, goal_side
from rocketcoach.telemetry.models import FIELD_X, FIELD_Y, GOAL_HALF_WIDTH, MatchPhase, Vec3, own_goal, team_sign

ON_SPOT = 900.0  # uu from the target that counts as "in position"
HOLD = 2.0  # seconds an instruction stays up unless something more urgent replaces it
SETTLE = 0.3  # a different instruction must be suggested this long in a row before it is shown


@dataclass(slots=True)
class Guidance:
    action: str  # short machine label: kickoff, cheat, save, challenge, shadow, back_post, support, attack, cover
    text: str  # what the overlay shows after "Now:"
    target: Vec3
    urgency: int  # 0 calm .. 3 emergency
    dist: float = 0.0
    radius: float = ON_SPOT

    @property
    def on_spot(self) -> bool:
        return self.action in ("challenge", "attack", "kickoff") or self.dist < self.radius


def _clamp_field(v: Vec3) -> Vec3:
    return Vec3(max(-FIELD_X + 200, min(FIELD_X - 200, v.x)), max(-FIELD_Y + 150, min(FIELD_Y - 150, v.y)), 17.0)


def _far_post(team: int, ball: Vec3) -> Vec3:
    g = own_goal(team)
    side = -1.0 if ball.x > 0 else 1.0
    return Vec3(side * (GOAL_HALF_WIDTH - 150), g.y + team_sign(team) * 250, 17.0)


def _shadow_spot(team: int, ball: Vec3, dist_goal: float) -> Vec3:
    g = own_goal(team)
    to_goal = (g - ball).flat().normalized()
    back = max(900.0, min(1800.0, dist_goal * 0.45))
    spot = ball + to_goal * back
    # never deeper than just in front of our own goal line
    s = team_sign(team)
    if s * spot.y < s * (g.y + s * 200):
        spot = Vec3(spot.x, g.y + s * 200, 0)
    return _clamp_field(spot)


def _support_spot(team: int, ball: Vec3) -> Vec3:
    s = team_sign(team)
    y = max(-FIELD_Y + 1500, min(FIELD_Y - 1500, ball.y - s * 2300))
    return _clamp_field(Vec3(ball.x * 0.35, y, 0))


def target_for(action: str, ctx: Context) -> Vec3:
    """Where `action` wants you right now (used to keep a held instruction's marker up to date)."""
    team, ball = ctx.team, ctx.ball
    if action in ("kickoff", "challenge", "attack"):
        return ball
    if action == "cheat":
        return _support_spot(team, Vec3())
    if action == "save":
        return _clamp_field(Vec3(max(-GOAL_HALF_WIDTH, min(GOAL_HALF_WIDTH, ball.x)), own_goal(team).y, 0))
    if action == "back_post":
        return _far_post(team, ball)
    if action == "shadow":
        return _shadow_spot(team, ball, ctx.ball_dist_own_goal)
    if action == "support":
        return _support_spot(team, ball)
    return _clamp_field(Vec3(ball.x * 0.3, ball.y - team_sign(team) * 2000, 0))  # cover


def decide(ctx: Context, kickoff=None, advice: str | None = None) -> Guidance:  # noqa: ANN001
    """kickoff: a KickoffPlan for this kickoff; advice: the learned best action text when you're on the ball."""
    me, mate, ball, team = ctx.me, ctx.mate, ctx.ball, ctx.team
    phase = ctx.frame.phase
    mate_ok = mate is not None and not mate.is_demolished
    mate_back = mate_ok and goal_side(mate, ball)  # type: ignore[arg-type]

    if phase in (MatchPhase.COUNTDOWN, MatchPhase.KICKOFF):
        if kickoff is not None:
            action = "kickoff" if kickoff.role == "go" else "cheat"
            return Guidance(action, f"{kickoff.spawn.capitalize()}. {kickoff.text}", kickoff.target, 1)
        if ctx.i_am_first_man:
            return Guidance("kickoff", "Your kickoff: boost + flip into the ball's center", ball, 1)
        return Guidance("cheat", "Not your kickoff: hang back, read the 50/50", _support_spot(team, Vec3()), 1)

    if ctx.predicted_own_goal_time is not None and ctx.predicted_own_goal_time < 3.0:
        save_spot = _clamp_field(Vec3(max(-GOAL_HALF_WIDTH, min(GOAL_HALF_WIDTH, ball.x)), own_goal(team).y, 0))
        return Guidance("save", "SAVE: get into the net, face the ball", save_spot, 3)

    if ctx.their_possession:
        if ctx.i_am_last_man or not mate_back:
            if not goal_side(me, ball):
                return Guidance("back_post", "Get goal-side: drive to the back post", _far_post(team, ball), 2)
            return Guidance("shadow", "Shadow: stay goal-side, don't dive",
                            _shadow_spot(team, ball, ctx.ball_dist_own_goal), 2)
        if ctx.ttb_me < ctx.ttb_opp + 0.3 and ctx.ttb_me < ctx.ttb_mate:
            return Guidance("challenge", "Pressure them: teammate is covering", ball, 1)
        return Guidance("back_post", "Rotate back post behind your teammate", _far_post(team, ball), 1)

    if ctx.our_possession:
        if ctx.i_am_first_man:
            if advice:
                return Guidance("attack", f"Your ball: {advice[0].lower()}{advice[1:]}", ball, 1)
            attacking_third = team_sign(team) * ball.y > FIELD_Y * 0.35
            text = "Your ball: shoot for the far corner" if attacking_third else "Your ball: take it upfield"
            return Guidance("attack", text, ball, 1)
        text = "Support: stay behind your teammate's play"
        if me.boost < 30:
            text = "Support + grab small pads on the way"
        return Guidance("support", text, _support_spot(team, ball), 0)

    # loose ball / 50-50
    if ctx.i_am_first_man:
        if ctx.i_am_last_man and not mate_back and ctx.ttb_me + 0.2 >= ctx.ttb_opp:
            return Guidance("shadow", "Last back: wait for their touch, then challenge",
                            _shadow_spot(team, ball, ctx.ball_dist_own_goal), 2)
        return Guidance("challenge", "Challenge: square up, nose through the ball", ball, 1)
    s = team_sign(team)
    cover = _clamp_field(Vec3(ball.x * 0.3, ball.y - s * 2000, 0))
    return Guidance("cover", "Cover: stay behind the 50/50", cover, 1)


class PositionGuide:
    def __init__(self, radius: float = ON_SPOT) -> None:
        self.radius = radius
        self.current: Guidance | None = None
        self._since = -1e9
        self.on_spot_s = 0.0
        self.total_s = 0.0
        self._last_t: float | None = None
        self._cand: str | None = None
        self._cand_since = -1e9

    def update(self, ctx: Context, kickoff=None, advice: str | None = None) -> Guidance:  # noqa: ANN001
        t = ctx.frame.time
        raw = decide(ctx, kickoff, advice)
        g = raw
        cur = self.current
        if cur is None:
            self._since = t
        elif raw.action == cur.action:
            self._cand = None  # the situation agrees with what's shown again
        else:
            if raw.action != self._cand:
                self._cand, self._cand_since = raw.action, t
            urgent = raw.urgency > cur.urgency
            settled = t - self._cand_since >= SETTLE and t - self._since >= HOLD
            if urgent or settled:
                self._since, self._cand = t, None
            else:  # keep showing the current instruction, with its target re-evaluated for this frame
                g = Guidance(cur.action, cur.text, target_for(cur.action, ctx), cur.urgency)
        g.dist = ctx.me.location.flat_dist(g.target)
        g.radius = self.radius
        dt = 0.0 if self._last_t is None else min(0.2, max(0.0, t - self._last_t))
        self._last_t = t
        if ctx.frame.phase == MatchPhase.ACTIVE:
            self.total_s += dt
            if g.on_spot:
                self.on_spot_s += dt
        self.current = g
        return g
