"""Source-independent telemetry model.

Every telemetry source (RLBot, mock simulator, recorded file) converts its native data into these
types, so the analysis code never touches RLBot flatbuffers directly. Units are Unreal units (uu),
seconds and radians, matching RLBot. Blue team (0) defends the goal at y = -5120.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum


@dataclass(frozen=True, slots=True)
class Vec3:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    def __add__(self, o: Vec3) -> Vec3:
        return Vec3(self.x + o.x, self.y + o.y, self.z + o.z)

    def __sub__(self, o: Vec3) -> Vec3:
        return Vec3(self.x - o.x, self.y - o.y, self.z - o.z)

    def __mul__(self, k: float) -> Vec3:
        return Vec3(self.x * k, self.y * k, self.z * k)

    def dot(self, o: Vec3) -> float:
        return self.x * o.x + self.y * o.y + self.z * o.z

    def length(self) -> float:
        return math.sqrt(self.x * self.x + self.y * self.y + self.z * self.z)

    def flat(self) -> Vec3:
        return Vec3(self.x, self.y, 0.0)

    def dist(self, o: Vec3) -> float:
        return (self - o).length()

    def flat_dist(self, o: Vec3) -> float:
        return (self - o).flat().length()

    def normalized(self) -> Vec3:
        n = self.length()
        return Vec3() if n < 1e-9 else self * (1.0 / n)

    def to_list(self) -> list[float]:
        return [round(self.x, 1), round(self.y, 1), round(self.z, 1)]

    @staticmethod
    def from_list(v: list[float]) -> Vec3:
        return Vec3(float(v[0]), float(v[1]), float(v[2]))


class MatchPhase(str, Enum):
    INACTIVE = "inactive"
    COUNTDOWN = "countdown"
    KICKOFF = "kickoff"
    ACTIVE = "active"
    GOAL_SCORED = "goal_scored"
    REPLAY = "replay"
    PAUSED = "paused"
    ENDED = "ended"


class AirState(str, Enum):
    ON_GROUND = "on_ground"
    JUMPING = "jumping"
    DOUBLE_JUMPING = "double_jumping"
    DODGING = "dodging"
    IN_AIR = "in_air"


@dataclass(slots=True)
class Controls:
    """Controller input. Only present when the source actually exposes it (RLBot `last_input`)."""

    throttle: float = 0.0
    steer: float = 0.0
    pitch: float = 0.0
    yaw: float = 0.0
    roll: float = 0.0
    jump: bool = False
    boost: bool = False
    handbrake: bool = False


@dataclass(slots=True)
class Touch:
    time: float
    location: Vec3
    normal: Vec3


@dataclass(slots=True)
class CarState:
    index: int
    name: str
    team: int
    is_bot: bool
    location: Vec3
    velocity: Vec3
    rotation: Vec3  # pitch, yaw, roll in radians (stored in x, y, z)
    angular_velocity: Vec3
    boost: float
    air_state: AirState = AirState.ON_GROUND
    has_jumped: bool = False
    has_double_jumped: bool = False
    has_dodged: bool = False
    dodge_dir: tuple[float, float] = (0.0, 0.0)
    is_demolished: bool = False
    is_supersonic: bool = False
    latest_touch: Touch | None = None
    controls: Controls | None = None
    goals: int = 0
    saves: int = 0
    shots: int = 0
    accolades: tuple[str, ...] = ()

    @property
    def on_ground(self) -> bool:
        return self.air_state == AirState.ON_GROUND

    @property
    def speed(self) -> float:
        return self.velocity.length()

    def forward(self) -> Vec3:
        p, y = self.rotation.x, self.rotation.y
        return Vec3(math.cos(p) * math.cos(y), math.cos(p) * math.sin(y), math.sin(p))

    def up(self) -> Vec3:
        p, y, r = self.rotation.x, self.rotation.y, self.rotation.z
        # Unreal rotator -> up vector (third column of the rotation matrix used by RLBot docs).
        return Vec3(
            -math.cos(r) * math.sin(p) * math.cos(y) + math.sin(r) * math.sin(y),
            -math.cos(r) * math.sin(p) * math.sin(y) - math.sin(r) * math.cos(y),
            math.cos(r) * math.cos(p),
        )


@dataclass(slots=True)
class BallState:
    location: Vec3
    velocity: Vec3
    angular_velocity: Vec3 = field(default_factory=Vec3)


@dataclass(slots=True)
class PredictionPoint:
    time: float
    location: Vec3
    velocity: Vec3


@dataclass(slots=True)
class BoostPadInfo:
    location: Vec3
    is_full: bool


@dataclass(slots=True)
class GameFrame:
    """One telemetry snapshot."""

    time: float  # game seconds elapsed (monotonic within a match)
    frame_num: int
    phase: MatchPhase
    time_remaining: float
    is_overtime: bool
    ball: BallState | None
    cars: list[CarState]
    pads_active: list[bool]
    scores: tuple[int, int] = (0, 0)
    prediction: list[PredictionPoint] = field(default_factory=list)

    def car(self, index: int) -> CarState | None:
        for c in self.cars:
            if c.index == index:
                return c
        return None


# ---- Standard Soccar field constants (RLBot wiki "useful game values") ----
FIELD_X = 4096.0
FIELD_Y = 5120.0
CEILING_Z = 2044.0
GOAL_HALF_WIDTH = 892.75
GOAL_HEIGHT = 642.775
BALL_RADIUS = 92.75
SUPERSONIC_SPEED = 2200.0
MAX_CAR_SPEED = 2300.0


def own_goal(team: int) -> Vec3:
    return Vec3(0.0, -FIELD_Y if team == 0 else FIELD_Y, 0.0)


def opp_goal(team: int) -> Vec3:
    return own_goal(1 - team)


def team_sign(team: int) -> float:
    """+1 for blue (attacks +y), -1 for orange. Multiply y by this to get 'towards the opponent goal'."""
    return 1.0 if team == 0 else -1.0


_FULL = [(-3072, -4096), (3072, -4096), (-3584, 0), (3584, 0), (-3072, 4096), (3072, 4096)]
_SMALL = [
    (0, -4240), (-1792, -4184), (1792, -4184), (-940, -3308), (940, -3308), (0, -2816),
    (-3584, -2484), (3584, -2484), (-1788, -2300), (1788, -2300), (-2048, -1036), (0, -1024),
    (2048, -1036), (-1024, 0), (1024, 0), (-2048, 1036), (0, 1024), (2048, 1036),
    (-1788, 2300), (1788, 2300), (-3584, 2484), (3584, 2484), (0, 2816), (-940, 3310),
    (940, 3308), (-1792, 4184), (1792, 4184), (0, 4240),
]


def standard_boost_pads() -> list[BoostPadInfo]:
    """Standard Soccar pads, ordered by y then x like RLBot's FieldInfo."""
    pads = [BoostPadInfo(Vec3(x, y, 73.0), True) for x, y in _FULL]
    pads += [BoostPadInfo(Vec3(x, y, 70.0), False) for x, y in _SMALL]
    pads.sort(key=lambda p: (p.location.y, p.location.x))
    return pads
