"""Telemetry source interface shared by the RLBot adapter, mock simulator and file replayer."""

from __future__ import annotations

import abc
from collections.abc import Callable

from rocketcoach.telemetry.models import BoostPadInfo, GameFrame

FrameCallback = Callable[[GameFrame], None]

# Capability flags. Analysis code checks these instead of assuming a field exists.
CAP_INPUTS = "controller_inputs"  # Controls are the player's real inputs
CAP_PREDICTION = "ball_prediction"
CAP_BOOST_PADS = "boost_pad_states"
CAP_TOUCHES = "latest_touch"
CAP_JUMP_STATE = "jump_dodge_state"
CAP_WHEEL_CONTACT = "per_wheel_contact"  # never available in RLBot v5; listed so reports can say so

ALL_CAPABILITIES = [CAP_INPUTS, CAP_PREDICTION, CAP_BOOST_PADS, CAP_TOUCHES, CAP_JUMP_STATE, CAP_WHEEL_CONTACT]


class TelemetrySource(abc.ABC):
    """Pushes GameFrames to a callback from its own thread until stopped."""

    name: str = "abstract"
    capabilities: frozenset[str] = frozenset()
    simulated: bool = True

    def __init__(self) -> None:
        self.boost_pads: list[BoostPadInfo] = []

    @abc.abstractmethod
    def start(self, on_frame: FrameCallback) -> None:
        """Begin delivering frames (non-blocking)."""

    @abc.abstractmethod
    def stop(self) -> None:
        """Stop delivering frames and release resources."""

    def is_alive(self) -> bool:
        return True
