"""Stateful event detection from consecutive frames.

Events are facts derived directly from telemetry (touches, goals, kickoffs, airborne periods), not
judgements. Rules and reports build on them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rocketcoach.analysis.context import Context
from rocketcoach.telemetry.models import (
    FIELD_Y,
    GOAL_HALF_WIDTH,
    GOAL_HEIGHT,
    GameFrame,
    MatchPhase,
    Vec3,
    opp_goal,
    team_sign,
)

MAX_GAP = 0.5  # seconds of missing telemetry before continuity-dependent trackers reset
CHALLENGE_RADIUS = 450.0  # uu from the touch point: both cars this close = a contested ball


@dataclass(slots=True)
class Event:
    time: float
    type: str
    data: dict[str, Any] = field(default_factory=dict)


def shot_on_target(ball: Vec3, vel: Vec3, team: int) -> tuple[bool, float | None]:
    """Straight-line (gravity-adjusted) projection of the ball to the opponent goal line.

    Returns (on_target, crossing_x). An approximation: ignores bounces and walls.
    """
    gy = opp_goal(team).y
    vy = vel.y
    if vy == 0 or (gy - ball.y) / vy <= 0:
        return False, None
    t = (gy - ball.y) / vy
    if t > 3.0:
        return False, None
    x = ball.x + vel.x * t
    z = ball.z + vel.z * t - 325 * t * t
    z = abs(z)  # one floor bounce keeps it roughly in play
    return abs(x) < GOAL_HALF_WIDTH - 90 and z < GOAL_HEIGHT - 60, x


class EventDetector:
    def __init__(self, my_index: int) -> None:
        self.my_index = my_index
        self.prev: GameFrame | None = None
        self.events: list[Event] = []
        self.gaps = 0
        self._air_start: dict[int, float] = {}
        self._air_touch: dict[int, bool] = {}
        self._takeoff: dict[int, dict[str, float]] = {}
        self._kickoff_t: float | None = None
        self._kickoff_first: int | None = None
        self._last_toucher: int | None = None
        self._landing: tuple[float, float] | None = None  # (time, speed at landing)
        self._dribble_start: float | None = None
        self._pending_kick_result: float | None = None
        self._pending_challenge: dict[str, Any] | None = None

    def reset(self) -> None:
        self.__init__(self.my_index)  # type: ignore[misc]

    def update(self, f: GameFrame, ctx: Context | None) -> list[Event]:
        out: list[Event] = []
        p = self.prev
        if p is not None:
            if f.frame_num == p.frame_num:
                return out  # duplicate packet
            if f.time < p.time - 1.0:
                out.append(Event(f.time, "match_restart"))
                self.reset()
                self.prev = f
                self.events.extend(out)
                return out
            if f.time - p.time > MAX_GAP:
                self.gaps += 1
                out.append(Event(f.time, "telemetry_gap", {"seconds": round(f.time - p.time, 2)}))
                self._air_start.clear()
                self._landing = None
                self._dribble_start = None
        if p is None or p.phase != f.phase:
            out += self._phase_change(p, f)
        if p is not None and f.phase in (MatchPhase.KICKOFF, MatchPhase.ACTIVE):
            out += self._touches(p, f)
            out += self._air(p, f)
            out += self._demos(p, f)
            if ctx is not None:
                out += self._my_motion(f, ctx)
        out += self._resolve_pending(f)
        if p is not None and f.scores != p.scores:
            team = 0 if f.scores[0] > p.scores[0] else 1
            me = f.car(self.my_index)
            out.append(Event(f.time, "goal", {
                "team": team, "for_me": me is not None and me.team == team,
                "last_toucher": self._last_toucher}))
        self.prev = f
        self.events.extend(out)
        return out

    # ---- detectors ----------------------------------------------------------------------------
    def _phase_change(self, p: GameFrame | None, f: GameFrame) -> list[Event]:
        out = [Event(f.time, "phase", {"from": p.phase.value if p else None, "to": f.phase.value})]
        if f.phase == MatchPhase.KICKOFF:
            self._kickoff_t, self._kickoff_first = f.time, None
            out.append(Event(f.time, "kickoff_start"))
        if f.phase == MatchPhase.ENDED:
            out.append(Event(f.time, "match_end", {"scores": list(f.scores)}))
        return out

    def _touches(self, p: GameFrame, f: GameFrame) -> list[Event]:
        out = []
        for c in f.cars:
            pc = p.car(c.index)
            t = c.latest_touch
            if t is None or (pc and pc.latest_touch and pc.latest_touch.time == t.time):
                continue
            prev_owner = self._last_toucher
            self._last_toucher = c.index
            ball_vel = f.ball.velocity if f.ball else Vec3()
            on_target, cross_x = shot_on_target(f.ball.location if f.ball else t.location, ball_vel, c.team)
            airborne = not c.on_ground and t.location.z > 250
            data = {
                "car": c.index, "team": c.team, "loc": t.location.to_list(), "ball_speed": round(ball_vel.length()),
                "on_target": on_target, "cross_x": None if cross_x is None else round(cross_x),
                "aerial": airborne, "height": round(t.location.z), "boost": round(c.boost),
            }
            out.append(Event(f.time, "touch", data))
            if airborne:
                self._air_touch[c.index] = True
            if on_target:
                out.append(Event(f.time, "shot", data))
            if prev_owner is not None and f.car(prev_owner) and f.car(prev_owner).team != c.team:
                out.append(Event(f.time, "possession_change", {"to_team": c.team, "car": c.index,
                                                               "from_car": prev_owner}))
            if self._pending_challenge is None:
                self._maybe_challenge(f, c, t.location)
            if self._kickoff_t is not None and self._kickoff_first is None:
                self._kickoff_first = c.index
                self._pending_kick_result = f.time + 2.0
                out.append(Event(f.time, "kickoff_touch", {"car": c.index, "team": c.team,
                                                           "time_to_ball": round(f.time - self._kickoff_t, 2)}))
        return out

    def _maybe_challenge(self, f: GameFrame, toucher, loc: Vec3) -> None:  # noqa: ANN001
        """A touch is a 50/50 involving you when you and an opponent are both on the ball at that moment."""
        me = f.car(self.my_index)
        if me is None or me.is_demolished:
            return
        if toucher.index == me.index:
            rivals = [o for o in f.cars if o.team != me.team and not o.is_demolished]
            contested = any(o.location.dist(loc) < CHALLENGE_RADIUS for o in rivals)
        elif toucher.team != me.team:
            contested = me.location.dist(loc) < CHALLENGE_RADIUS
        else:
            return
        if not contested:
            return
        s = team_sign(me.team)
        mate = next((c for c in f.cars if c.team == me.team and c.index != me.index), None)
        ball = f.ball.location if f.ball else loc
        approach = me.velocity.flat().normalized().dot((opp_goal(me.team) - me.location).flat().normalized())
        last_man = mate is None or mate.is_demolished or s * me.location.y <= s * mate.location.y
        mate_cover = mate is not None and not mate.is_demolished and s * (ball.y - mate.location.y) > 0
        self._pending_challenge = {
            "time": f.time, "i_touched": toucher.index == me.index, "boost": round(me.boost),
            "speed": round(me.velocity.flat().length()), "approach": round(approach, 2),
            "airborne": not me.on_ground, "last_man": last_man, "mate_cover": mate_cover,
            "our_half": s * ball.y < 0,
        }

    def _air(self, p: GameFrame, f: GameFrame) -> list[Event]:
        out = []
        for c in f.cars:
            pc = p.car(c.index)
            if pc is None:
                continue
            if pc.on_ground and not c.on_ground:
                self._air_start[c.index] = f.time
                self._air_touch[c.index] = False
                if f.ball:
                    self._takeoff[c.index] = {"dist": c.location.flat_dist(f.ball.location),
                                              "ball_z": f.ball.location.z, "boost": c.boost, "max_z": c.location.z}
            elif not c.on_ground and c.index in self._takeoff:
                tk = self._takeoff[c.index]
                tk["max_z"] = max(tk.get("max_z", 0.0), c.location.z)
            if not pc.on_ground and c.on_ground and c.index in self._air_start:
                start = self._air_start.pop(c.index)
                dur = f.time - start
                touched = self._air_touch.pop(c.index, False)
                take = self._takeoff.pop(c.index, {})
                # An aerial attempt = got well off the ground toward a high ball that was within reach,
                # not every hop, flip or wall drive (those inflated the count on real data).
                if (dur > 0.6 and take.get("ball_z", 0) > 250 and take.get("max_z", 0) > 350
                        and take.get("dist", 1e9) < 2200) or touched:
                    out.append(Event(f.time, "aerial", {
                        "car": c.index, "duration": round(dur, 2), "contact": touched,
                        "takeoff_dist": round(take.get("dist", 0)), "ball_z": round(take.get("ball_z", 0)),
                        "double_jump": c.has_double_jumped or pc.has_double_jumped}))
                if c.index == self.my_index and dur > 0.5:
                    self._landing = (f.time, c.velocity.flat().length())
                    out.append(Event(f.time, "landing", {"car": c.index, "air_time": round(dur, 2),
                                                         "speed": round(c.velocity.flat().length())}))
        return out

    def _demos(self, p: GameFrame, f: GameFrame) -> list[Event]:
        out = []
        for c in f.cars:
            pc = p.car(c.index)
            if pc and c.is_demolished and not pc.is_demolished:
                out.append(Event(f.time, "demolished", {"car": c.index, "team": c.team}))
        return out

    def _my_motion(self, f: GameFrame, ctx: Context) -> list[Event]:
        out = []
        me = ctx.me
        if self._landing is not None:
            t0, _ = self._landing
            speed = me.velocity.flat().length()
            if speed >= 1200 or f.time - t0 > 2.5:
                out.append(Event(f.time, "recovery", {"seconds": round(f.time - t0, 2),
                                                      "regained": speed >= 1200}))
                self._landing = None
        if f.ball is not None:
            rel = f.ball.location - me.location
            carrying = me.on_ground and 100 < rel.z < 280 and rel.flat().length() < 140
            if carrying and self._dribble_start is None:
                self._dribble_start = f.time
            elif not carrying and self._dribble_start is not None:
                dur = f.time - self._dribble_start
                if dur > 0.7:
                    flick = f.ball.velocity.length() > 1400 and f.ball.velocity.z > 300
                    out.append(Event(f.time, "dribble", {"seconds": round(dur, 2), "flick": flick}))
                self._dribble_start = None
        return out

    def _resolve_pending(self, f: GameFrame) -> list[Event]:
        out = []
        me = f.car(self.my_index)
        if self._pending_kick_result is not None and f.time >= self._pending_kick_result and f.ball and me:
            depth = team_sign(me.team) * f.ball.location.y
            result = "won" if depth > 800 else "lost" if depth < -800 else "neutral"
            out.append(Event(f.time, "kickoff_result", {"result": result, "first_touch": self._kickoff_first,
                                                        "my_team": me.team}))
            self._pending_kick_result = None
            self._kickoff_t = None
        pc = self._pending_challenge
        if pc is not None and f.time >= pc["time"] + 1.0 and f.ball and me:
            s = team_sign(me.team)
            toward = s * f.ball.velocity.y
            ours = self._last_toucher is not None and f.car(self._last_toucher) is not None and \
                f.car(self._last_toucher).team == me.team
            result = "won" if toward > 300 or (ours and toward > -200) else \
                "lost" if toward < -300 or not ours else "neutral"
            out.append(Event(f.time, "challenge", {**pc, "result": result, "time": round(pc["time"], 2)}))
            self._pending_challenge = None
        return out
