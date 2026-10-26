"""Replays a recorded telemetry file through the coach (for re-analysis and testing)."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from rocketcoach.telemetry import source as src
from rocketcoach.telemetry.models import standard_boost_pads
from rocketcoach.telemetry.recorder import read_recording


class FileSource(src.TelemetrySource):
    name = "file"
    capabilities = frozenset({src.CAP_INPUTS, src.CAP_BOOST_PADS, src.CAP_TOUCHES})

    def __init__(self, path: Path, speed: float = 0.0, simulated: bool = True) -> None:
        super().__init__()
        self.path = path
        self.speed = speed  # 0 = as fast as possible
        self.simulated = simulated
        self.boost_pads = standard_boost_pads()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, on_frame: src.FrameCallback) -> None:
        def run() -> None:
            prev_t = None
            for f in read_recording(self.path):
                if self._stop.is_set():
                    break
                if self.speed > 0 and prev_t is not None:
                    time.sleep(max(0.0, (f.time - prev_t) / self.speed))
                prev_t = f.time
                on_frame(f)

        self._thread = threading.Thread(target=run, name="file-source", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
