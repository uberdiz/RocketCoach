"""Compact telemetry recording (gzip JSON lines) and a tolerant reader.

Recordings are the app's own "replays": post-match analysis can be re-run on them later. Ball
prediction is not stored (it can be large); the reader returns frames without it.
"""

from __future__ import annotations

import gzip
import json
import logging
import zlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from rocketcoach.telemetry.models import (
    AirState,
    BallState,
    CarState,
    Controls,
    GameFrame,
    MatchPhase,
    Touch,
    Vec3,
)

log = logging.getLogger(__name__)


def frame_to_dict(f: GameFrame) -> dict[str, Any]:
    cars = []
    for c in f.cars:
        d: dict[str, Any] = {
            "i": c.index, "n": c.name, "t": c.team, "bot": c.is_bot, "p": c.location.to_list(),
            "v": c.velocity.to_list(), "r": [round(c.rotation.x, 3), round(c.rotation.y, 3), round(c.rotation.z, 3)],
            "b": round(c.boost, 1), "a": c.air_state.value, "dj": c.has_double_jumped, "dg": c.has_dodged,
            "demo": c.is_demolished, "g": c.goals, "sv": c.saves, "sh": c.shots,
        }
        if c.latest_touch:
            d["tch"] = [round(c.latest_touch.time, 3), c.latest_touch.location.to_list(),
                        c.latest_touch.normal.to_list()]
        if c.controls:
            k = c.controls
            d["in"] = [round(k.throttle, 2), round(k.steer, 2), int(k.jump), int(k.boost), int(k.handbrake)]
        cars.append(d)
    return {
        "t": round(f.time, 3), "fn": f.frame_num, "ph": f.phase.value, "rem": round(f.time_remaining, 2),
        "ot": f.is_overtime, "s": list(f.scores), "pads": "".join("1" if a else "0" for a in f.pads_active),
        "ball": [f.ball.location.to_list(), f.ball.velocity.to_list()] if f.ball else None, "cars": cars,
    }


def dict_to_frame(d: dict[str, Any]) -> GameFrame:
    cars = []
    for c in d["cars"]:
        touch = None
        if "tch" in c:
            touch = Touch(c["tch"][0], Vec3.from_list(c["tch"][1]), Vec3.from_list(c["tch"][2]))
        ctl = None
        if "in" in c:
            i = c["in"]
            ctl = Controls(throttle=i[0], steer=i[1], jump=bool(i[2]), boost=bool(i[3]), handbrake=bool(i[4]))
        cars.append(CarState(
            c["i"], c["n"], c["t"], c["bot"], Vec3.from_list(c["p"]), Vec3.from_list(c["v"]),
            Vec3.from_list(c["r"]), Vec3(), c["b"], AirState(c["a"]), False, c["dj"], c["dg"], (0.0, 0.0),
            c["demo"], Vec3.from_list(c["v"]).length() > 2200, touch, ctl, c["g"], c["sv"], c["sh"],
        ))
    ball = BallState(Vec3.from_list(d["ball"][0]), Vec3.from_list(d["ball"][1])) if d.get("ball") else None
    return GameFrame(d["t"], d["fn"], MatchPhase(d["ph"]), d["rem"], d["ot"], ball, cars,
                     [ch == "1" for ch in d["pads"]], tuple(d["s"]))


class Recorder:
    def __init__(self, path: Path, hz: float = 15.0) -> None:
        self.path = path
        self.interval = 1.0 / hz if hz > 0 else 0.0
        self._last = -1e9
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = gzip.open(path, "wt", encoding="utf-8")
        self.frames_written = 0

    def write(self, f: GameFrame) -> None:
        if f.time - self._last < self.interval:
            return
        self._last = f.time
        self._fh.write(json.dumps(frame_to_dict(f), separators=(",", ":")) + "\n")
        self.frames_written += 1

    def close(self) -> None:
        self._fh.close()


def read_recording(path: Path) -> Iterator[GameFrame]:
    """Yield frames; stops cleanly on a truncated file and skips corrupt lines."""
    bad = 0
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                try:
                    yield dict_to_frame(json.loads(line))
                except (ValueError, KeyError, IndexError, TypeError):
                    bad += 1
    except (EOFError, OSError, zlib.error) as exc:
        log.warning("Recording %s is incomplete (%s); using the frames read so far.", path, exc)
    if bad:
        log.warning("Skipped %d corrupt lines in %s", bad, path)
