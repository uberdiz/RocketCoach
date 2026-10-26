"""Overlay: real Win32 window styles (click-through), painting, hotkey parsing. Needs a desktop session."""

import sys
import time

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication  # noqa: E402

from rocketcoach.analysis.observations import Observation  # noqa: E402
from rocketcoach.coach.prioritizer import LiveTip  # noqa: E402
from rocketcoach.config import OverlayConfig  # noqa: E402
from rocketcoach.engine import Status  # noqa: E402
from rocketcoach.overlay.window import OverlayWindow, parse_hotkey  # noqa: E402


@pytest.fixture(scope="module")
def app():  # noqa: ANN201
    return QApplication.instance() or QApplication([])


def test_parse_hotkey():
    mods, vk = parse_hotkey("ctrl+alt+o")
    assert vk == ord("O") and mods & 0x3 == 0x3
    assert parse_hotkey("shift+f9")[1] == 0x78
    with pytest.raises(ValueError):
        parse_hotkey("ctrl+alt")


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 click-through")
def test_click_through_toggles_real_window_style(app):  # noqa: ANN001
    w = OverlayWindow(OverlayConfig())
    w.show()
    app.processEvents()
    assert w.is_click_through()
    w.set_click_through(False)
    app.processEvents()
    assert not w.is_click_through() and w.interactive
    w.set_click_through(True)
    app.processEvents()
    assert w.is_click_through()
    w.close()


def test_paints_tip_and_status(app):  # noqa: ANN001
    w = OverlayWindow(OverlayConfig())
    w.show()
    w.set_status(Status(True, True, "active", 35, "last back", "goal-side", "defense", (1, 0), 120.0))
    o = Observation("last_man_overcommit", "defense", 1.0, "Last back: shadow, don't dive",
                    "You challenged as last back while the opponent arrived first.", 0.9, 0.8)
    w.show_tip(LiveTip(o, 0, 6), time.monotonic(), 6)
    img = w.grab().toImage()
    app.processEvents()
    assert img.width() > 200 and w.height() > 120  # grew to fit the tip + explanation
    w.close()
