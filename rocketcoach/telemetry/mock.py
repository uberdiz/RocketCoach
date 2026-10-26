"""Mock 2v2 telemetry: a small kinematic Rocket League stand-in.

This is NOT a physics-accurate simulator. It produces plausible 2v2 situations (kickoffs, challenges,
rotations, boost pickups, aerials, goals) so the coach, overlay and reports can be developed and tested
without the game. The human-controlled car ("You") follows a configurable *flaw profile* so the
coaching rules have real mistakes to find.
"""

from __future__ import annotations

import math
import random
import threading
import time
from dataclasses import dataclass

from rocketcoach.telemetry import source as src
from rocketcoach.telemetry.models import (
    BALL_RADIUS,
    CEILING_Z,
    FIELD_X,
    FIELD_Y,
    GOAL_HALF_WIDTH,
    GOAL_HEIGHT,
    AirState,
    BallState,
    CarState,
    Controls,
    GameFrame,
    MatchPhase,
    PredictionPoint,
    Touch,
    Vec3,
    opp_goal,
    own_goal,
    standard_boost_pads,
)

GRAVITY = -650.0
DT = 1.0 / 60.0
CAR_Z = 17.0
THROTTLE_SPEED = 1410.0
BOOST_SPEED = 2300.0
BOOST_USE_PER_S = 33.3
KICKOFF_SPOTS = [Vec3(-2048, -2560, CAR_Z), Vec3(2048, -2560, CAR_Z), Vec3(-256, -3840, CAR_Z),
                 Vec3(256, -3840, CAR_Z), Vec3(0, -4608, CAR_Z)]


@dataclass
class FlawProfile:
    """How the simulated 'You' plays. Probabilities are evaluated per decision (~2 Hz)."""

    ballchase: float = 0.35  # go for the ball even when the teammate is first man
    boost_greed: float = 0.5  # detour to a corner 100 when below 30 boost, even as last man
    boost_while_supersonic: float = 0.3  # keep holding boost at top speed
    aerial_skill: float = 0.5  # chance an aerial attempt connects


@dataclass
class _Car:
    state: CarState
    is_you: bool
    touch_cooldown: float = 0.0
    decision_timer: float = 0.0
    target: Vec3 = Vec3()
    want_boost: bool = False
    aerial: bool = False
    attacking: bool = False
    hold_boost: bool = False
    demo_timer: float = -1.0


class MockSimulator:
    def __init__(self, seed: int = 7, match_seconds: float = 300.0, flaws: FlawProfile | None = None) -> None:
        self.rng = random.Random(seed)
        self.flaws = flaws or FlawProfile()
        self.match_seconds = match_seconds
        self.pads = standard_boost_pads()
        self.pad_timers = [0.0] * len(self.pads)
        self.t = 0.0
        self.frame_num = 0
        self.remaining = match_seconds
        self.scores = [0, 0]
        self.phase = MatchPhase.COUNTDOWN
        self.phase_timer = 3.0
        self.ball = BallState(Vec3(0, 0, BALL_RADIUS), Vec3())
        names = [("You", 0, False), ("Teammate", 0, True), ("Opponent A", 1, True), ("Opponent B", 1, True)]
        self.cars = [
            _Car(CarState(i, n, team, bot, Vec3(), Vec3(), Vec3(), Vec3(), 33.0), is_you=(i == 0))
            for i, (n, team, bot) in enumerate(names)
        ]
        self._reset_kickoff()

    # ---- public -------------------------------------------------------------------------------
    def step(self) -> GameFrame:
        self.t += DT
        self.frame_num += 2  # 120 Hz frame counter at 60 Hz steps
        if self.phase in (MatchPhase.COUNTDOWN, MatchPhase.GOAL_SCORED):
            self.phase_timer -= DT
            if self.phase_timer <= 0:
                if self.phase == MatchPhase.GOAL_SCORED:
                    self._reset_kickoff()
                    self.phase, self.phase_timer = MatchPhase.COUNTDOWN, 3.0
                else:
                    self.phase = MatchPhase.KICKOFF
        elif self.phase in (MatchPhase.KICKOFF, MatchPhase.ACTIVE):
            if self.phase == MatchPhase.ACTIVE:
                self.remaining -= DT
                if self.remaining <= 0 and self.ball.location.z < 200:
                    self.phase = MatchPhase.ENDED
            self._update_cars()
            self._update_ball()
            self._update_pads()
        return self.frame()

    def frame(self) -> GameFrame:
        return GameFrame(
            time=self.t,
            frame_num=self.frame_num,
            phase=self.phase,
            time_remaining=max(0.0, self.remaining),
            is_overtime=False,
            ball=BallState(self.ball.location, self.ball.velocity),
            cars=[self._snapshot(c) for c in self.cars],
            pads_active=[tm <= 0 for tm in self.pad_timers],
            scores=(self.scores[0], self.scores[1]),
            prediction=self._cached_prediction(),
        )

    def run_frames(self, seconds: float) -> list[GameFrame]:
        out = []
        for _ in range(int(seconds / DT)):
            f = self.step()
            out.append(f)
            if f.phase == MatchPhase.ENDED:
                break
        return out

    # ---- internals ----------------------------------------------------------------------------
    def _snapshot(self, c: _Car) -> CarState:
        s = c.state
        return CarState(
            s.index, s.name, s.team, s.is_bot, s.location, s.velocity, s.rotation, s.angular_velocity,
            s.boost, s.air_state, s.has_jumped, s.has_double_jumped, s.has_dodged, s.dodge_dir,
            c.demo_timer > 0, s.velocity.length() > 2200, s.latest_touch,
            Controls(s.controls.throttle, s.controls.steer, jump=s.controls.jump, boost=s.controls.boost)
            if s.controls else None,
            s.goals, s.saves, s.shots,
        )

    def _reset_kickoff(self) -> None:
        spots = self.rng.sample(KICKOFF_SPOTS, 2)
        for c in self.cars:
            k = c.state.index % 2
            p = spots[k]
            if c.state.team == 1:
                p = Vec3(-p.x, -p.y, p.z)
            c.state.location = p
            c.state.velocity = Vec3()
            c.state.rotation = Vec3(0, math.atan2(-p.y, -p.x), 0)
            c.state.boost = 33.0
            c.state.air_state = AirState.ON_GROUND
            c.aerial = False
            c.demo_timer = -1.0
        self.ball = BallState(Vec3(0, 0, BALL_RADIUS), Vec3())
        self.pad_timers = [0.0] * len(self.pads)

    def _first_man(self, team: int) -> int:
        mates = [c for c in self.cars if c.state.team == team]
        return min(mates, key=lambda c: c.state.location.flat_dist(self.ball.location)).state.index

    def _decide(self, c: _Car) -> None:
        s = c.state
        ball = self.ball.location
        is_first = self._first_man(s.team) == s.index or self.phase == MatchPhase.KICKOFF and s.index % 2 == 0
        if c.is_you and not is_first and self.rng.random() < self.flaws.ballchase:
            is_first = True
        c.want_boost = False
        c.attacking = is_first
        c.hold_boost = c.is_you and self.rng.random() < self.flaws.boost_while_supersonic
        if s.boost < 30 and not is_first:
            greedy = self.rng.random() < (self.flaws.boost_greed if c.is_you else 0.15)
            pads = [i for i, p in enumerate(self.pads) if self.pad_timers[i] <= 0 and (p.is_full or not greedy)]
            if greedy:
                pads = [i for i in pads if self.pads[i].is_full]
            if pads:
                i = min(pads, key=lambda i: self.pads[i].location.flat_dist(s.location))
                c.target, c.want_boost, c.attacking = self.pads[i].location, True, False
                return
        if is_first:
            aim = opp_goal(s.team)
            behind = (ball - aim).flat().normalized() * 120
            c.target = Vec3(ball.x + behind.x, ball.y + behind.y, CAR_Z)
            if ball.z > 250 and s.location.flat_dist(ball) < 1100 and s.on_ground and s.boost > 20:
                c.aerial = True
        else:
            g = own_goal(s.team)
            back_post_x = -math.copysign(700, ball.x) if abs(ball.x) > 300 else 0
            depth = 0.55
            c.target = Vec3(back_post_x * (1 - depth) + ball.x * depth * 0.4, g.y + (ball.y - g.y) * depth, CAR_Z)

    def _update_cars(self) -> None:
        for c in self.cars:
            s = c.state
            if c.demo_timer > 0:
                c.demo_timer -= DT
                if c.demo_timer <= 0:
                    s.location, s.velocity = Vec3(0, own_goal(s.team).y * 0.9, CAR_Z), Vec3()
                continue
            c.decision_timer -= DT
            if c.decision_timer <= 0:
                self._decide(c)
                c.decision_timer = 0.5
            c.touch_cooldown = max(0.0, c.touch_cooldown - DT)
            if c.aerial and s.on_ground:
                s.air_state, s.has_jumped = AirState.JUMPING, True
                s.velocity = Vec3(s.velocity.x, s.velocity.y, 900.0)
            to_target = (c.target - s.location).flat()
            dist = to_target.length()
            boosting = s.boost > 0 and (dist > 1200 or c.aerial) and (
                s.velocity.length() < 2150 or c.hold_boost)
            top = BOOST_SPEED if boosting else THROTTLE_SPEED
            if dist < 300 and not c.aerial and not c.attacking:
                top = max(300.0, dist * 2)
            desired = to_target.normalized() * top
            k = min(1.0, DT * (4.0 if boosting else 2.5))
            v = s.velocity
            vx, vy = v.x + (desired.x - v.x) * k, v.y + (desired.y - v.y) * k
            vz = v.z
            if not s.on_ground:
                vz += GRAVITY * DT
                if c.aerial and boosting:
                    to_ball = (self.ball.location - s.location).normalized()
                    vz += to_ball.z * 1000 * DT
            if boosting:
                s.boost = max(0.0, s.boost - BOOST_USE_PER_S * DT)
            s.velocity = Vec3(vx, vy, vz)
            loc = s.location + s.velocity * DT
            if loc.z <= CAR_Z:
                loc = Vec3(loc.x, loc.y, CAR_Z)
                if not s.on_ground and s.velocity.z <= 0:
                    s.air_state, s.has_jumped, c.aerial = AirState.ON_GROUND, False, False
                    s.velocity = Vec3(s.velocity.x * 0.8, s.velocity.y * 0.8, 0)
            elif s.air_state == AirState.JUMPING and loc.z > 120:
                s.air_state = AirState.IN_AIR
            s.location = Vec3(max(-FIELD_X + 50, min(FIELD_X - 50, loc.x)),
                              max(-FIELD_Y + 50, min(FIELD_Y - 50, loc.y)), min(loc.z, CEILING_Z - 20))
            if s.velocity.flat().length() > 50:
                s.rotation = Vec3(0, math.atan2(s.velocity.y, s.velocity.x), 0)
            s.controls = Controls(throttle=1.0, boost=boosting, jump=s.air_state == AirState.JUMPING)
            self._maybe_touch(c)
        self._maybe_demo()

    def _maybe_touch(self, c: _Car) -> None:
        s = c.state
        if c.touch_cooldown > 0 or s.location.dist(self.ball.location) > 170:
            return
        if c.aerial and c.is_you and self.rng.random() > self.flaws.aerial_skill:
            c.touch_cooldown = 0.6  # whiffed
            return
        aim = opp_goal(s.team) + Vec3(self.rng.uniform(-1400, 1400), 0, self.rng.uniform(0, 500))
        direction = (aim - self.ball.location).normalized()
        power = max(900.0, s.velocity.length() * 1.15 + self.rng.uniform(100, 600))
        self.ball.velocity = Vec3(direction.x * power, direction.y * power, abs(direction.z) * power + 250)
        normal = (self.ball.location - s.location).normalized()
        s.latest_touch = Touch(self.t, self.ball.location, normal)
        c.touch_cooldown = 0.25
        if self.phase == MatchPhase.KICKOFF:
            self.phase = MatchPhase.ACTIVE
        if (self.ball.velocity.y > 0) == (s.team == 0) and abs(self.ball.location.y) > 2500:
            s.shots += 1 if self.rng.random() < 0.3 else 0

    def _maybe_demo(self) -> None:
        for a in self.cars:
            if a.state.velocity.length() < 2200 or a.demo_timer > 0:
                continue
            for b in self.cars:
                if b.state.team != a.state.team and b.demo_timer <= 0 and a.state.location.dist(b.state.location) < 120:
                    b.demo_timer = 3.0

    def _update_ball(self) -> None:
        loc, vel = self._ball_step(self.ball.location, self.ball.velocity)
        if abs(loc.y) > FIELD_Y and abs(loc.x) < GOAL_HALF_WIDTH and loc.z < GOAL_HEIGHT:
            scorer_team = 0 if loc.y > 0 else 1
            self.scores[scorer_team] += 1
            last = max((c for c in self.cars if c.state.latest_touch), key=lambda c: c.state.latest_touch.time,
                       default=None)
            if last and last.state.team == scorer_team:
                last.state.goals += 1
            self.phase, self.phase_timer = MatchPhase.GOAL_SCORED, 3.0
        self.ball = BallState(loc, vel)

    @staticmethod
    def _ball_step(loc: Vec3, vel: Vec3) -> tuple[Vec3, Vec3]:
        vel = Vec3(vel.x * 0.9995, vel.y * 0.9995, vel.z + GRAVITY * DT)
        loc = loc + vel * DT
        x, y, z = loc.x, loc.y, loc.z
        vx, vy, vz = vel.x, vel.y, vel.z
        if z < BALL_RADIUS:
            z, vz = BALL_RADIUS, -vz * 0.6 if abs(vz) > 60 else 0.0
            vx, vy = vx * 0.98, vy * 0.98
        if z > CEILING_Z - BALL_RADIUS:
            z, vz = CEILING_Z - BALL_RADIUS, -abs(vz) * 0.6
        if abs(x) > FIELD_X - BALL_RADIUS:
            x, vx = math.copysign(FIELD_X - BALL_RADIUS, x), -vx * 0.6
        in_goal_mouth = abs(x) < GOAL_HALF_WIDTH and z < GOAL_HEIGHT
        if abs(y) > FIELD_Y - BALL_RADIUS and not in_goal_mouth:
            y, vy = math.copysign(FIELD_Y - BALL_RADIUS, y), -vy * 0.6
        return Vec3(x, y, z), Vec3(vx, vy, vz)

    def _cached_prediction(self) -> list[PredictionPoint]:
        # Recomputing 6 s of ball path every 60 Hz step dominated CPU; refresh at 10 Hz like a sane consumer.
        if self.frame_num % 12 == 0 or not getattr(self, "_pred", None):
            self._pred = self._predict()
        return self._pred

    def _predict(self) -> list[PredictionPoint]:
        loc, vel = self.ball.location, self.ball.velocity
        out = []
        for k in range(1, 361):
            loc, vel = self._ball_step(loc, vel)
            if k % 3 == 0:  # 20 Hz like the RLBot adapter
                out.append(PredictionPoint(self.t + k * DT, loc, vel))
        return out

    def _update_pads(self) -> None:
        for i, pad in enumerate(self.pads):
            if self.pad_timers[i] > 0:
                self.pad_timers[i] = max(0.0, self.pad_timers[i] - DT)
                continue
            r = 208 if pad.is_full else 144
            for c in self.cars:
                s = c.state
                if c.demo_timer <= 0 and s.boost < 100 and s.location.flat_dist(pad.location) < r and s.location.z < 300:
                    s.boost = 100.0 if pad.is_full else min(100.0, s.boost + 12)
                    self.pad_timers[i] = 10.0 if pad.is_full else 4.0
                    break


class MockSource(src.TelemetrySource):
    """Runs the MockSimulator in real time (or faster) on a background thread."""

    name = "mock"
    simulated = True
    capabilities = frozenset({src.CAP_INPUTS, src.CAP_PREDICTION, src.CAP_BOOST_PADS, src.CAP_TOUCHES,
                              src.CAP_JUMP_STATE})

    def __init__(self, seed: int = 7, match_seconds: float = 300.0, speed: float = 1.0,
                 flaws: FlawProfile | None = None, drop_rate: float = 0.0) -> None:
        super().__init__()
        self.sim = MockSimulator(seed, match_seconds, flaws)
        self.boost_pads = self.sim.pads
        self.speed = speed
        self.drop_rate = drop_rate  # fraction of frames dropped, to exercise missing-telemetry handling
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, on_frame: src.FrameCallback) -> None:
        def run() -> None:
            next_t = time.perf_counter()
            while not self._stop.is_set():
                f = self.sim.step()
                if self.sim.rng.random() >= self.drop_rate:
                    on_frame(f)
                if f.phase == MatchPhase.ENDED:
                    break
                if self.speed > 0:
                    next_t += DT / self.speed
                    delay = next_t - time.perf_counter()
                    if delay > 0:
                        time.sleep(delay)

        self._thread = threading.Thread(target=run, name="mock-source", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
