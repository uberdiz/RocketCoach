"""Builders for hand-crafted telemetry situations."""

from __future__ import annotations

from rocketcoach.telemetry.models import (
    AirState,
    BallState,
    CarState,
    Controls,
    GameFrame,
    MatchPhase,
    PredictionPoint,
    Touch,
    Vec3,
)


def car(i: int, team: int, loc: tuple[float, float, float], vel: tuple[float, float, float] = (0, 0, 0),
        boost: float = 50, bot: bool = True, air: AirState = AirState.ON_GROUND, touch: Touch | None = None,
        boosting: bool = False, demo: bool = False, name: str | None = None) -> CarState:
    return CarState(
        index=i, name=name or f"car{i}", team=team, is_bot=bot, location=Vec3(*loc), velocity=Vec3(*vel),
        rotation=Vec3(), angular_velocity=Vec3(), boost=boost, air_state=air, latest_touch=touch,
        is_demolished=demo, controls=Controls(throttle=1.0, boost=boosting))


def frame(t: float, cars: list[CarState], ball: tuple[float, float, float] = (0, 0, 93),
          ball_vel: tuple[float, float, float] = (0, 0, 0), phase: MatchPhase = MatchPhase.ACTIVE,
          scores: tuple[int, int] = (0, 0), fn: int | None = None,
          prediction: list[PredictionPoint] | None = None) -> GameFrame:
    return GameFrame(time=t, frame_num=fn if fn is not None else int(t * 120), phase=phase, time_remaining=300 - t,
                     is_overtime=False, ball=BallState(Vec3(*ball), Vec3(*ball_vel)), cars=cars,
                     pads_active=[True] * 34, scores=scores, prediction=prediction or [])


def two_v_two(me: CarState, mate_loc, opp_locs, mate_vel=(0, 0, 0), opp_vels=((0, 0, 0), (0, 0, 0))):  # noqa: ANN001
    return [me, car(1, 0, mate_loc, mate_vel), car(2, 1, opp_locs[0], opp_vels[0]), car(3, 1, opp_locs[1], opp_vels[1])]
