"""Boost management analysis.

Distinguishes wasteful starvation (low boost *and* out of the play or late to challenges) from
fine low-boost play (low boost while correctly positioned and not needing speed).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rocketcoach.analysis.context import Context, goal_side
from rocketcoach.analysis.events import Event
from rocketcoach.analysis.observations import Observation
from rocketcoach.analysis.tactical import Persist
from rocketcoach.telemetry.models import FIELD_X, FIELD_Y, SUPERSONIC_SPEED, BoostPadInfo, Vec3

HEAT_COLS, HEAT_ROWS = 8, 10


@dataclass
class PadTrip:
    start: float
    start_loc: Vec3
    pad: int
    was_last_man: bool
    ball_dist: float
    distance: float = 0.0


@dataclass
class BoostStats:
    active_s: float = 0.0
    below: dict[int, float] = field(default_factory=lambda: {20: 0.0, 40: 0.0, 60: 0.0})
    collected: float = 0.0
    spent: float = 0.0
    spent_supersonic: float = 0.0
    spent_air: float = 0.0
    spent_defending: float = 0.0
    big_pads: int = 0
    small_pads: int = 0
    small_pad_misses: int = 0
    full_trips: list[dict] = field(default_factory=list)
    starved_out_of_play_s: float = 0.0
    low_boost_ok_s: float = 0.0
    challenge_boost: list[float] = field(default_factory=list)
    timeline: list[tuple[float, float]] = field(default_factory=list)
    heat_time: list[list[float]] = field(default_factory=lambda: [[0.0] * HEAT_COLS for _ in range(HEAT_ROWS)])
    pickups: list[tuple[float, float, bool]] = field(default_factory=list)

    def to_dict(self) -> dict:
        a = max(self.active_s, 1e-6)
        return {
            "active_seconds": round(self.active_s, 1),
            "pct_below": {str(k): round(100 * v / a, 1) for k, v in self.below.items()},
            "collected": round(self.collected), "spent": round(self.spent),
            "spent_supersonic": round(self.spent_supersonic), "spent_air": round(self.spent_air),
            "spent_defending": round(self.spent_defending),
            "big_pads": self.big_pads, "small_pads": self.small_pads, "small_pad_misses": self.small_pad_misses,
            "full_trips": self.full_trips, "starved_out_of_play_s": round(self.starved_out_of_play_s, 1),
            "low_boost_ok_s": round(self.low_boost_ok_s, 1),
            "avg_challenge_boost": round(sum(self.challenge_boost) / len(self.challenge_boost), 1)
            if self.challenge_boost else None,
            "challenges_low_boost": sum(1 for b in self.challenge_boost if b < 20),
            "timeline": self.timeline, "heat_time": self.heat_time, "pickups": self.pickups,
        }


class BoostCoach:
    def __init__(self, pads: list[BoostPadInfo]) -> None:
        self.pads = pads
        self.s = BoostStats()
        self._prev_boost: float | None = None
        self._prev_loc: Vec3 | None = None
        self._last_t: float | None = None
        self._trip: PadTrip | None = None
        self._near_small: dict[int, float] = {}
        self._last_timeline = -1e9
        self.p_starved = Persist()
        self.p_supersonic = Persist()
        self._supersonic_spent_window = 0.0

    def update(self, ctx: Context, events: list[Event]) -> list[Observation]:
        me, f, t = ctx.me, ctx.frame, ctx.frame.time
        dt = 0.0 if self._last_t is None else min(0.2, max(0.0, t - self._last_t))
        self._last_t = t
        out: list[Observation] = []
        for e in events:
            if e.type == "challenge":
                self.s.challenge_boost.append(e.data["boost"])
            if e.type == "telemetry_gap":
                self._prev_boost, self._trip = None, None
        if me.is_demolished:
            self._prev_boost = None
            return out
        s = self.s
        s.active_s += dt
        for k in s.below:
            if me.boost < k:
                s.below[k] += dt
        col = min(HEAT_COLS - 1, max(0, int((me.location.x + FIELD_X) / (2 * FIELD_X) * HEAT_COLS)))
        row = min(HEAT_ROWS - 1, max(0, int((me.location.y + FIELD_Y) / (2 * FIELD_Y) * HEAT_ROWS)))
        s.heat_time[row][col] += dt
        if t - self._last_timeline >= 1.0:
            s.timeline.append((round(t, 1), round(me.boost)))
            self._last_timeline = t
        if self._prev_boost is not None:
            delta = me.boost - self._prev_boost
            if delta > 0.5:
                s.collected += delta
                big = delta > 40 or me.boost >= 99.5 and self._nearest_pad(me.location, full=True)[1] < 400
                if big:
                    s.big_pads += 1
                else:
                    s.small_pads += 1
                s.pickups.append((round(me.location.x), round(me.location.y), big))
                out += self._finish_trip(ctx, picked=True, big=big)
            elif delta < 0:
                spent = -delta
                s.spent += spent
                if me.velocity.length() >= SUPERSONIC_SPEED - 30 and me.on_ground:
                    s.spent_supersonic += spent
                    self._supersonic_spent_window += spent
                if not me.on_ground:
                    s.spent_air += spent
                if ctx.their_possession and ctx.ball_in_our_half:
                    s.spent_defending += spent
        self._prev_boost = me.boost
        if self._prev_loc is not None and self._trip is not None:
            self._trip.distance += me.location.flat_dist(self._prev_loc)
        self._prev_loc = me.location
        self._track_trip(ctx)
        self._track_small_misses(ctx)
        out += self._live_rules(ctx, t, dt)
        return out

    # ---- helpers ------------------------------------------------------------------------------
    def _nearest_pad(self, loc: Vec3, full: bool) -> tuple[int, float]:
        best, bd = -1, 1e9
        for i, p in enumerate(self.pads):
            if p.is_full == full:
                d = p.location.flat_dist(loc)
                if d < bd:
                    best, bd = i, d
        return best, bd

    def _track_trip(self, ctx: Context) -> None:
        me = ctx.me
        if self._trip is not None:
            pad = self.pads[self._trip.pad]
            heading = me.velocity.flat().normalized().dot((pad.location - me.location).flat().normalized())
            if heading < 0.3 and me.location.flat_dist(pad.location) > 600:
                self._finish_trip(ctx, picked=False, big=True)
            return
        if me.boost > 45 or me.velocity.flat().length() < 800:
            return
        for i, p in enumerate(self.pads):
            if not p.is_full or not (i < len(ctx.frame.pads_active) and ctx.frame.pads_active[i]):
                continue
            d = me.location.flat_dist(p.location)
            if 1200 < d < 5000:
                heading = me.velocity.flat().normalized().dot((p.location - me.location).flat().normalized())
                if heading > 0.95 and p.location.flat_dist(ctx.ball) > 2500:
                    self._trip = PadTrip(ctx.frame.time, me.location, i, ctx.i_am_last_man,
                                         p.location.flat_dist(ctx.ball))
                    return

    def _finish_trip(self, ctx: Context, picked: bool, big: bool) -> list[Observation]:
        trip, self._trip = self._trip, None
        if trip is None or not big or not picked:
            return []  # an abandoned heading toward a corner is not evidence of a boost trip
        dur = ctx.frame.time - trip.start
        rec = {"start": round(trip.start, 1), "seconds": round(dur, 1), "distance": round(trip.distance),
               "picked": picked, "last_man": trip.was_last_man, "ball_dist": round(trip.ball_dist),
               "goal_against_risk": round(ctx.goal_threat, 2)}
        self.s.full_trips.append(rec)
        if trip.was_last_man and (ctx.their_possession or ctx.goal_threat > 0.3) and dur > 1.2:
            return [Observation(
                "corner_boost_last_man", "boost", ctx.frame.time, "Take small pads when last back",
                f"{dur:.1f}s to a corner 100 while last back.",
                impact=0.7, confidence=0.7, evidence=rec,
                next_time="Last back: take small pads by your goal.")]
        return []

    def _track_small_misses(self, ctx: Context) -> None:
        me = ctx.me
        if me.boost > 50 or not me.on_ground:
            self._near_small.clear()
            return
        for i, p in enumerate(self.pads):
            if p.is_full or i >= len(ctx.frame.pads_active) or not ctx.frame.pads_active[i]:
                continue
            d = me.location.flat_dist(p.location)
            if d < 450:
                self._near_small[i] = min(self._near_small.get(i, 1e9), d)
            elif i in self._near_small:
                closest = self._near_small.pop(i)
                if 170 < closest < 450:  # passed close by without driving over it
                    self.s.small_pad_misses += 1

    def _live_rules(self, ctx: Context, t: float, dt: float) -> list[Observation]:
        me = ctx.me
        out = []
        low = me.boost < 15
        well_placed = goal_side(me, ctx.ball) and (ctx.i_am_last_man or not ctx.i_am_first_man)
        out_of_play = me.location.flat_dist(ctx.ball) > 3500 and not well_placed
        if low and well_placed:
            self.s.low_boost_ok_s += dt
        if low and out_of_play:
            self.s.starved_out_of_play_s += dt
        idx, d = self._nearest_pad(me.location, full=False)
        held = self.p_starved.update(low and out_of_play and d < 1500, t)
        if held >= 5.0 and held - dt < 5.0:
            out.append(Observation(
                "boost_starved", "boost", t, "Grab the small pads on your path",
                f"Under 15 boost for {held:.0f}s, pads nearby.",
                impact=0.4, confidence=0.65, evidence={"boost": round(me.boost), "nearest_small": round(d)},
                next_time="Plan your rotation path over small pads."))
        ss = me.on_ground and me.velocity.length() >= SUPERSONIC_SPEED - 30 and \
            me.controls is not None and me.controls.boost
        held = self.p_supersonic.update(ss, t)
        if not ss:
            self._supersonic_spent_window = 0.0
        if held >= 1.5 and held - dt < 1.5 and self._supersonic_spent_window > 15:
            out.append(Observation(
                "boost_while_supersonic", "boost", t, "Release boost: already max speed",
                f"{self._supersonic_spent_window:.0f} boost burned at top speed.",
                impact=0.3, confidence=0.85, evidence={"spent": round(self._supersonic_spent_window)},
                next_time="Let go of boost once the supersonic trail appears."))
        return out
