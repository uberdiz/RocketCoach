"""Boost or ball: a one-word call the overlay shows as a badge.

BALL means play the ball or stay with the play. BOOST means take a pad now, and names which one (a pad on the
way to where the guide wants you, so the detour is small). The call follows the positioning guide: when the
guide says the ball is yours, it's BALL. When you're rotating with low boost, it's BOOST if a safe pad is close
to your path. Last back, only pads goal-side of the ball count, so the corner 100 never pulls you out of net.
One override: a contested 50/50 at very low boost while your teammate is behind you is BOOST, because going in
empty usually loses it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from rocketcoach.analysis.context import Context, goal_side, team_y
from rocketcoach.analysis.guide import Guidance
from rocketcoach.telemetry.models import BoostPadInfo, MatchPhase, Vec3

LOW_BY_LEVEL = {"new": 35.0, "mid": 30.0, "high": 25.0, "elite": 20.0}
EMPTY = 15.0  # below this a contested 50/50 is usually lost
BALL_ACTIONS = ("kickoff", "save", "challenge", "attack")
HOLD = 1.5  # seconds a call stays up unless the guide's urgency rises


@dataclass(slots=True)
class BoostCall:
    call: str  # "BALL", "BOOST" or "" (nothing to say, e.g. a dead ball)
    reason: str = ""
    pad: Vec3 | None = None
    big: bool = False


def _direction(ctx: Context, target: Vec3) -> str:
    """'ahead', 'left', 'right' or 'behind' as seen from the driver's seat."""
    fwd = ctx.me.forward().flat()
    to = (target - ctx.me.location).flat()
    if to.length() < 1 or fwd.length() < 1e-3:
        return "here"
    ang = math.degrees(math.atan2(fwd.x * to.y - fwd.y * to.x, fwd.x * to.x + fwd.y * to.y))
    if abs(ang) < 40:
        return "ahead"
    if abs(ang) > 135:
        return "behind"
    # Rocket League's world is left-handed (x mirrored on screen), so a positive cross product is your right.
    return "right" if ang > 0 else "left"


def best_pad(ctx: Context, pads: list[BoostPadInfo], dest: Vec3, max_detour: float) -> tuple[int, float] | None:
    """The active pad with the smallest detour on the way to `dest` (big pads count a bit cheaper)."""
    me, f = ctx.me, ctx.frame
    direct = me.location.flat_dist(dest)
    last_back = ctx.i_am_last_man and ctx.mate is not None
    best: tuple[int, float] | None = None
    for i, p in enumerate(pads):
        if i < len(f.pads_active) and not f.pads_active[i]:
            continue
        if last_back and team_y(ctx.team, p.location.y) > team_y(ctx.team, ctx.ball.y) - 300:
            continue  # last back: never a pad that's level with or ahead of the ball
        detour = me.location.flat_dist(p.location) + p.location.flat_dist(dest) - direct
        cost = detour - (500 if p.is_full else 0)
        if detour <= max_detour and (best is None or cost < best[1]):
            best = (i, cost)
    return best


def decide_call(ctx: Context, g: Guidance, pads: list[BoostPadInfo], level: str = "high") -> BoostCall:
    phase = ctx.frame.phase
    me = ctx.me
    if phase in (MatchPhase.COUNTDOWN, MatchPhase.KICKOFF):
        if g.action == "kickoff":
            return BoostCall("BALL", "your kickoff")
        return BoostCall("BOOST", "grab boost, read the kickoff") if "boost" in g.text.lower() else BoostCall("")
    if phase != MatchPhase.ACTIVE or me.is_demolished:
        return BoostCall("")
    low = LOW_BY_LEVEL.get(level, 25.0)
    mate = ctx.mate
    mate_covers = mate is not None and not mate.is_demolished and goal_side(mate, ctx.ball)

    if g.action in BALL_ACTIONS:
        contested = not ctx.our_possession and ctx.ttb_opp < ctx.ttb_me + 0.3
        if g.action == "challenge" and contested and me.boost < EMPTY and mate_covers:
            pick = best_pad(ctx, pads, ctx.ball, 700)
            if pick is not None:
                pad = pads[pick[0]]
                return BoostCall("BOOST", f"{me.boost:.0f} boost: don't 50 empty, pad {_direction(ctx, pad.location)}",
                                 pad.location, pad.is_full)
        why = {"save": "save it", "attack": "your ball", "kickoff": "your kickoff"}.get(g.action, "go now")
        return BoostCall("BALL", why)

    if me.boost >= low:
        return BoostCall("BALL", "boost OK, stay in the play")
    if ctx.goal_threat > 0.6 and ctx.i_am_last_man:
        return BoostCall("BALL", "no time for boost, defend")
    detour = 1400.0 if g.urgency <= 1 else 600.0
    pick = best_pad(ctx, pads, g.target, detour)
    if pick is None:
        return BoostCall("BALL", "no safe pad close, stay in the play")
    pad = pads[pick[0]]
    kind = "big pad" if pad.is_full else "pad"
    return BoostCall("BOOST", f"{kind} {_direction(ctx, pad.location)}", pad.location, pad.is_full)


class BoostBallCaller:
    """Holds a call briefly so the badge doesn't flicker on boundaries."""

    def __init__(self, pads: list[BoostPadInfo], level: str = "high") -> None:
        self.pads = pads
        self.level = level
        self.current = BoostCall("")
        self._since = -1e9
        self._urgency = 0
        self.counts = {"BALL": 0.0, "BOOST": 0.0}
        self._last_t: float | None = None

    def update(self, ctx: Context, g: Guidance) -> BoostCall:
        t = ctx.frame.time
        raw = decide_call(ctx, g, self.pads, self.level)
        cur = self.current
        if raw.call == cur.call or not cur.call or g.urgency > self._urgency or t - self._since >= HOLD:
            if raw.call != cur.call:
                self._since = t
            self.current = raw  # a same call still refreshes the pad and direction
        self._urgency = g.urgency
        dt = 0.0 if self._last_t is None else min(0.2, max(0.0, t - self._last_t))
        self._last_t = t
        if self.current.call in self.counts and ctx.frame.phase == MatchPhase.ACTIVE:
            self.counts[self.current.call] += dt
        return self.current
