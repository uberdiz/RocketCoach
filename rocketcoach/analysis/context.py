"""Per-frame derived 2v2 context: roles, time-to-ball, possession, threat."""

from __future__ import annotations

from dataclasses import dataclass

from rocketcoach.telemetry.models import (
    FIELD_Y,
    GOAL_HALF_WIDTH,
    GOAL_HEIGHT,
    CarState,
    GameFrame,
    Vec3,
    own_goal,
    team_sign,
)

AVG_DRIVE_SPEED = 1500.0


def time_to_ball(car: CarState, ball: Vec3) -> float:
    """Rough arrival estimate. Penalises facing away / needing to turn. Not a physics solver."""
    if car.is_demolished:
        return 99.0
    to_ball = ball - car.location
    dist = max(0.0, to_ball.flat().length() - 120)
    v_toward = car.velocity.flat().dot(to_ball.flat().normalized())
    speed = max(AVG_DRIVE_SPEED, v_toward, 0.0) + car.boost * 4  # boost helps reach top speed
    height_pen = max(0.0, ball.z - 250) / 600  # high balls take longer to reach
    turn_pen = 0.35 if v_toward < -300 else 0.0
    return dist / speed + height_pen + turn_pen


def goal_side(car: CarState, ball: Vec3) -> bool:
    """True if the car is between the ball and its own goal (along the length of the field)."""
    return team_sign(car.team) * (ball.y - car.location.y) > 0


def team_y(team: int, y: float) -> float:
    """Distance-along-field from your own back wall (0) to opponent back wall (2*FIELD_Y)."""
    return team_sign(team) * y + FIELD_Y


@dataclass(slots=True)
class Context:
    frame: GameFrame
    me: CarState
    mate: CarState | None
    opps: list[CarState]
    ball: Vec3
    ball_vel: Vec3
    ttb_me: float
    ttb_mate: float
    ttb_opp: float
    closest_opp: CarState | None
    i_am_first_man: bool
    i_am_last_man: bool
    ball_in_our_half: bool
    ball_dist_own_goal: float
    our_possession: bool
    their_possession: bool
    goal_threat: float  # 0..1 how likely the ball ends in our net soon
    predicted_own_goal_time: float | None

    @property
    def team(self) -> int:
        return self.me.team

    @property
    def is_2v2(self) -> bool:
        return self.mate is not None


def predicted_goal_time(frame: GameFrame, defending_team: int) -> float | None:
    """Seconds until the ball prediction enters defending_team's goal, or None."""
    gy = own_goal(defending_team).y
    for p in frame.prediction:
        if abs(p.location.y) >= FIELD_Y and p.location.y * gy > 0:
            if abs(p.location.x) < GOAL_HALF_WIDTH and p.location.z < GOAL_HEIGHT:
                return p.time - frame.time
            return None
    return None


def build_context(frame: GameFrame, my_index: int) -> Context | None:
    me = frame.car(my_index)
    if me is None or frame.ball is None:
        return None
    ball = frame.ball.location
    mates = [c for c in frame.cars if c.team == me.team and c.index != me.index]
    opps = [c for c in frame.cars if c.team != me.team]
    mate = mates[0] if mates else None
    ttb_me = time_to_ball(me, ball)
    ttb_mate = time_to_ball(mate, ball) if mate else 99.0
    opp_ttbs = [(time_to_ball(o, ball), o) for o in opps]
    ttb_opp, closest_opp = min(opp_ttbs, key=lambda t: t[0]) if opp_ttbs else (99.0, None)
    my_depth = team_y(me.team, me.location.y)
    mate_depth = team_y(me.team, mate.location.y) if mate and not mate.is_demolished else 1e9
    goal = own_goal(me.team)
    ours = min(ttb_me, ttb_mate)
    goal_t = predicted_goal_time(frame, me.team)
    ball_dist = ball.flat_dist(goal)
    threat = 0.0
    if goal_t is not None:
        threat = 1.0 if goal_t < 2.0 else 0.7
    elif team_y(me.team, ball.y) < FIELD_Y:  # ball in our half
        threat = max(0.0, 1.0 - ball_dist / 5000) * (0.8 if ttb_opp < ours else 0.3)
    return Context(
        frame=frame, me=me, mate=mate, opps=opps, ball=ball, ball_vel=frame.ball.velocity,
        ttb_me=ttb_me, ttb_mate=ttb_mate, ttb_opp=ttb_opp, closest_opp=closest_opp,
        i_am_first_man=ttb_me <= ttb_mate,
        i_am_last_man=my_depth <= mate_depth,
        ball_in_our_half=team_y(me.team, ball.y) < FIELD_Y,
        ball_dist_own_goal=ball_dist,
        our_possession=ours + 0.25 < ttb_opp,
        their_possession=ttb_opp + 0.25 < ours,
        goal_threat=threat,
        predicted_own_goal_time=goal_t,
    )
