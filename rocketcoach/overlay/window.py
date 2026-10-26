"""Transparent, always-on-top, click-through coaching overlay (PySide6).

Works over Rocket League in Borderless or Windowed display mode. Exclusive fullscreen bypasses the desktop
compositor, so no normal window can draw over it.

Click-through uses both Qt's WindowTransparentForInput and the Win32 extended styles
WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE, so mouse input reaches the game and the overlay never
steals focus. Press the interact hotkey (default Ctrl+Alt+O) to make it draggable/resizable and open settings.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

from PySide6.QtCore import QObject, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from rocketcoach.coach.prioritizer import LiveTip
from rocketcoach.config import OverlayConfig

log = logging.getLogger(__name__)

IS_WIN = sys.platform == "win32"
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WM_HOTKEY = 0x0312
MOD = {"alt": 0x1, "ctrl": 0x2, "shift": 0x4, "win": 0x8}
MOD_NOREPEAT = 0x4000

GO_COLORS = {"BALL": "#42A5F5", "BOOST": "#FFA726"}
CATEGORY_COLORS = {
    "defense": "#EF5350", "rotation": "#FFA726", "challenge": "#FFCA28", "boost": "#66BB6A",
    "kickoff": "#AB47BC", "mechanics": "#42A5F5", "offense": "#26C6DA",
}


def parse_hotkey(spec: str) -> tuple[int, int]:
    """'ctrl+alt+o' -> (modifiers, virtual key code)."""
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    mods = MOD_NOREPEAT
    vk = 0
    for p in parts:
        if p in MOD:
            mods |= MOD[p]
        elif len(p) == 1 and p.isalnum():
            vk = ord(p.upper())
        elif p.startswith("f") and p[1:].isdigit():
            vk = 0x70 + int(p[1:]) - 1
        else:
            raise ValueError(f"Unsupported hotkey key '{p}' in '{spec}'")
    if not vk:
        raise ValueError(f"Hotkey '{spec}' has no key")
    return mods, vk


def brief(text: str, limit: int = 70) -> str:
    """First sentence only, capped, so the overlay never shows a paragraph."""
    if not text:
        return ""
    first = text.split(". ")[0].strip().rstrip(".")
    return first if len(first) <= limit else first[: limit - 1].rstrip() + "…"


class Bridge(QObject):
    """Thread-safe hand-off from the telemetry thread to the Qt thread."""

    status = Signal(object)
    tip = Signal(object)
    report = Signal(object, object)


class OverlayWindow(QWidget):
    hotkey = Signal(str)

    def __init__(self, cfg: OverlayConfig) -> None:
        super().__init__(None)
        self.cfg = cfg
        self.interactive = False
        self.tip: LiveTip | None = None
        self.tip_visible_until = 0.0
        self.status = None
        self.stale = True
        self.muted = False
        self.voice_enabled = False
        self.banner = ""
        self._drag: QPoint | None = None
        self._resize = False
        self._hotkey_ids: dict[int, str] = {}
        self.setWindowTitle("Rocket Coach overlay")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowOpacity(cfg.opacity)
        self._place()
        self._fade = QTimer(self)
        self._fade.timeout.connect(self.update)
        self._fade.start(250)

    # ---- geometry -----------------------------------------------------------------------------
    def _place(self) -> None:
        screen = QGuiApplication.primaryScreen().availableGeometry()
        w = int(self.cfg.width * self.cfg.scale)
        h = int(170 * self.cfg.scale)
        x = self.cfg.x if self.cfg.x >= 0 else screen.left() + int(screen.width() * 0.015)
        y = self.cfg.y if self.cfg.y >= 0 else screen.top() + int(screen.height() * 0.02)
        self.setGeometry(QRect(x, y, w, h))

    # ---- click-through ------------------------------------------------------------------------
    def showEvent(self, e) -> None:  # noqa: ANN001, N802
        super().showEvent(e)
        self._apply_native_styles()  # changing Qt flags recreates the native window; re-apply every show

    def set_click_through(self, on: bool) -> None:
        self.interactive = not on
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, on)
        self.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, on)
        self.show()  # re-apply flags; showEvent applies the Win32 styles
        self._apply_native_styles()

    def _apply_native_styles(self) -> None:
        on = not self.interactive
        if IS_WIN:
            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            get = user32.GetWindowLongPtrW
            get.restype = ctypes.c_ssize_t
            style = get(hwnd, GWL_EXSTYLE)
            style |= WS_EX_LAYERED | WS_EX_TOOLWINDOW
            if on:
                style |= WS_EX_TRANSPARENT | WS_EX_NOACTIVATE
            else:
                style &= ~(WS_EX_TRANSPARENT | WS_EX_NOACTIVATE)
            user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ctypes.c_ssize_t(style))
            self.register_hotkeys()  # flag changes can recreate the native window
        self.update()

    def is_click_through(self) -> bool:
        """Reads back the real Win32 style (used by tests)."""
        if not IS_WIN:
            return bool(self.windowFlags() & Qt.WindowType.WindowTransparentForInput)
        get = ctypes.windll.user32.GetWindowLongPtrW
        get.restype = ctypes.c_ssize_t
        return bool(get(int(self.winId()), GWL_EXSTYLE) & WS_EX_TRANSPARENT)

    # ---- global hotkeys -----------------------------------------------------------------------
    def register_hotkeys(self) -> None:
        if not IS_WIN:
            return
        user32 = ctypes.windll.user32
        hwnd = int(self.winId())
        for hid in list(self._hotkey_ids):
            user32.UnregisterHotKey(None, hid)
            user32.UnregisterHotKey(hwnd, hid)
        self._hotkey_ids.clear()
        specs = {"interact": self.cfg.hotkey_interact, "mute": self.cfg.hotkey_mute,
                 "hide": self.cfg.hotkey_hide, "quit": self.cfg.hotkey_quit}
        for i, (name, spec) in enumerate(specs.items(), start=0xBC00):
            try:
                mods, vk = parse_hotkey(spec)
            except ValueError as exc:
                log.error("%s", exc)
                continue
            if user32.RegisterHotKey(hwnd, i, mods, vk):
                self._hotkey_ids[i] = name
            else:
                log.warning("Hotkey %s (%s) is already in use by another program", spec, name)

    def nativeEvent(self, event_type, message):  # noqa: ANN001, N802
        if IS_WIN and event_type == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam in self._hotkey_ids:
                self.hotkey.emit(self._hotkey_ids[msg.wParam])
                return True, 0
        return super().nativeEvent(event_type, message)

    def closeEvent(self, e) -> None:  # noqa: ANN001, N802
        if IS_WIN:
            for hid in self._hotkey_ids:
                ctypes.windll.user32.UnregisterHotKey(int(self.winId()), hid)
        super().closeEvent(e)

    # ---- data in ------------------------------------------------------------------------------
    def show_tip(self, tip: LiveTip, now_wall: float, seconds: float) -> None:
        self.tip = tip
        self.tip_visible_until = now_wall + seconds
        self.update()

    def set_status(self, status, stale: bool = False) -> None:  # noqa: ANN001
        self.status = status
        self.stale = stale
        self.update()

    # ---- interaction (only when not click-through) --------------------------------------------
    def mousePressEvent(self, e) -> None:  # noqa: ANN001, N802
        if not self.interactive:
            return
        if e.position().x() > self.width() - 18 and e.position().y() > self.height() - 18:
            self._resize = True
        self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e) -> None:  # noqa: ANN001, N802
        if not self.interactive or self._drag is None:
            return
        if self._resize:
            w = max(260, int(e.position().x()))
            self.cfg.width = int(w / self.cfg.scale)
            self.resize(w, self.height())
        else:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e) -> None:  # noqa: ANN001, N802
        if self._drag is not None and not self._resize:
            self.cfg.x, self.cfg.y = self.x(), self.y()
        self._drag, self._resize = None, False

    # ---- painting -----------------------------------------------------------------------------
    def paintEvent(self, _e) -> None:  # noqa: ANN001, N802
        import time as _t
        s = self.cfg.scale
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = QColor(self.cfg.background_color)
        bg.setAlphaF(0.9)
        text = QColor(self.cfg.text_color)
        accent = QColor(self.cfg.accent_color)
        muted = QColor(text)
        muted.setAlphaF(0.65)
        pad = int(10 * s)
        f_small = QFont(self.cfg.font_family, max(7, int((self.cfg.font_size - 3) * s)))
        f_tip = QFont(self.cfg.font_family, int((self.cfg.font_size + 3) * s), QFont.Weight.DemiBold)
        f_body = QFont(self.cfg.font_family, int(self.cfg.font_size * s))
        w = self.width()
        y = pad
        st = self.status
        live = st is not None and not self.stale
        showing_tip = self.tip is not None and _t.monotonic() < self.tip_visible_until
        mm_w, mm_h = int(96 * s), int(124 * s)
        show_map = False  # the minimap lives in its own window (MinimapWindow)
        col_w = w - 2 * pad - ((mm_w + pad) if show_map else 0)
        wrap = Qt.TextFlag.TextWordWrap

        def text_h(font: QFont, txt: str, width: int) -> int:
            return QFontMetrics(font).boundingRect(QRect(0, 0, width, 2000), wrap, txt).height() if txt else 0

        guide = f"Now: {st.guide}" if live and st.guide else ""
        top_h = int(44 * s) + (text_h(f_body, guide, col_w) + int(6 * s) if guide else 0)
        if show_map:
            top_h = max(top_h, mm_h + int(4 * s))
        tip_h = 0
        expl = nxt = ""
        if showing_tip:
            o = self.tip.obs
            expl = brief(o.explanation) if self.cfg.show_explanation else ""
            nxt = f"Next: {brief(o.next_time, 60)}" if o.next_time else ""
            tip_h = int(18 * s) + text_h(f_tip, o.tip, w - 2 * pad) + int(6 * s)
            tip_h += text_h(f_body, expl, w - 2 * pad) + (int(4 * s) if expl else 0)
            tip_h += text_h(f_body, nxt, w - 2 * pad) + (int(4 * s) if nxt else 0)
        total_h = pad + top_h + tip_h + pad + (int(20 * s) if self.interactive else 0)
        if self.height() != total_h:
            self.resize(w, total_h)
        body = QPainterPath()
        body.addRoundedRect(0, 0, w, total_h, 10 * s, 10 * s)
        p.fillPath(body, bg)
        if self.interactive:
            p.setPen(QPen(accent, 2 * s, Qt.PenStyle.DashLine))
            p.drawRoundedRect(1, 1, w - 2, total_h - 2, 10 * s, 10 * s)

        # header: status line
        p.setFont(f_small)
        if not live:
            p.setPen(QColor("#FFB74D"))
            p.drawText(QRect(pad, y, w - 2 * pad, int(16 * s)), Qt.TextFlag.TextSingleLine,
                       self.banner or "No telemetry: waiting for an RLBot match")
        else:
            badge = "SIMULATED" if st.simulated else "LIVE"
            p.setPen(QColor("#FFB74D") if st.simulated else QColor("#81C784"))
            p.drawText(pad, y + int(12 * s), badge)
            p.setPen(muted)
            extra = "  muted" if self.muted and self.voice_enabled else ""
            p.drawText(pad + int(70 * s), y + int(12 * s), f"{st.score[0]}-{st.score[1]}  {st.role}{extra}")
        y += int(20 * s)

        # boost bar
        boost = st.boost if live else 0.0
        bar_w = min(int(120 * s), col_w - int(36 * s))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 40))
        p.drawRoundedRect(pad, y, bar_w, int(10 * s), 4, 4)
        col = QColor("#EF5350") if boost < 20 else QColor("#FFCA28") if boost < 40 else QColor("#66BB6A")
        p.setBrush(col)
        p.drawRoundedRect(pad, y, int(bar_w * boost / 100), int(10 * s), 4, 4)
        p.setPen(text)
        p.drawText(pad + bar_w + int(6 * s), y + int(10 * s), f"{boost:3.0f}")
        if live and st.go_for:  # BALL / BOOST badge
            pill = QRect(pad + bar_w + int(40 * s), y - int(5 * s), int(66 * s), int(20 * s))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(GO_COLORS.get(st.go_for, self.cfg.accent_color)))
            p.drawRoundedRect(pill, 6 * s, 6 * s)
            p.setFont(QFont(self.cfg.font_family, max(8, int((self.cfg.font_size - 1) * s)), QFont.Weight.Bold))
            p.setPen(QColor("#111111"))
            p.drawText(pill, Qt.AlignmentFlag.AlignCenter, st.go_for)
            if st.go_why:
                p.setFont(f_small)
                p.setPen(muted)
                why_w = w - pad - pill.right() - int(6 * s)
                why = QFontMetrics(f_small).elidedText(st.go_why, Qt.TextElideMode.ElideRight, why_w)
                p.drawText(QRect(pill.right() + int(6 * s), pill.top(), why_w, pill.height()),
                           Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextSingleLine, why)
        y += int(24 * s)

        # "Now:" positioning instruction
        if guide:
            colr = QColor("#81C784") if st.guide_ok else QColor("#EF5350") if st.urgency >= 3 else                 QColor("#FFB74D") if st.urgency >= 2 else QColor(self.cfg.text_color)
            p.setFont(f_body)
            p.setPen(colr)
            p.drawText(QRect(pad, y, col_w, 2000), wrap, guide)
        if show_map:
            self._paint_minimap(p, QRect(w - pad - mm_w, pad + int(2 * s), mm_w, mm_h), st)
        y = pad + top_h

        if showing_tip:
            o = self.tip.obs
            chip = CATEGORY_COLORS.get(o.category, self.cfg.accent_color)
            p.setFont(f_small)
            mark = "  ✓" if o.kind == "strength" else "  REVIEW" if o.kind == "review" else ""
            p.setPen(QColor(chip))
            p.drawText(pad, y + int(10 * s), f"{o.category.upper()}  {int(o.confidence * 100)}%{mark}")
            y += int(18 * s)
            p.setFont(f_tip)
            p.setPen(text)
            r = QRect(pad, y, w - 2 * pad, 2000)
            y += p.boundingRect(r, wrap, o.tip).height() + int(6 * s)
            p.drawText(r, wrap, o.tip)
            p.setFont(f_body)
            for line, pen in ((expl, muted), (nxt, accent)):
                if line:
                    p.setPen(pen)
                    r = QRect(pad, y, w - 2 * pad, 2000)
                    y += p.boundingRect(r, wrap, line).height() + int(4 * s)
                    p.drawText(r, wrap, line)
        if self.interactive:
            p.setFont(f_small)
            p.setPen(accent)
            p.drawText(pad, total_h - int(8 * s), "Drag to move · corner to resize · Ctrl+Alt+O to lock")
            p.drawLine(w - 14, total_h - 4, w - 4, total_h - 14)
        p.end()

    def _paint_minimap(self, p: QPainter, r: QRect, st) -> None:  # noqa: ANN001
        """Top-down field, your goal at the bottom. Ring = where the coach wants you."""
        m = st.minimap

        def pt(xy) -> QPoint:  # noqa: ANN001
            x = r.left() + (xy[0] + 4096) / 8192 * r.width()
            y = r.top() + (1 - (xy[1] + 5120) / 10240) * r.height()
            return QPoint(int(x), int(y))
        p.setPen(QPen(QColor(255, 255, 255, 90), 1))
        p.setBrush(QColor(255, 255, 255, 18))
        p.drawRoundedRect(r, 6, 6)
        p.drawLine(r.left(), r.center().y(), r.right(), r.center().y())
        gw = int(r.width() * 1786 / 8192)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#4FC3F7"))
        p.drawRect(r.center().x() - gw // 2, r.bottom() - 2, gw, 3)
        p.setBrush(QColor("#FF8A50"))
        p.drawRect(r.center().x() - gw // 2, r.top(), gw, 3)
        me, tgt = pt(m["me"]), pt(m["target"])
        ok = st.guide_ok
        if not ok:
            p.setPen(QPen(QColor("#FFB74D"), 1, Qt.PenStyle.DashLine))
            p.drawLine(me, tgt)
        p.setPen(QPen(QColor("#81C784") if ok else QColor("#FFB74D"), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(tgt, 7, 7)
        if m.get("pad"):  # the pad the BOOST call wants
            p.setPen(QPen(QColor("#111111"), 1))
            p.setBrush(QColor(GO_COLORS["BOOST"]))
            p.drawEllipse(pt(m["pad"]), 5, 5)
        p.setPen(Qt.PenStyle.NoPen)
        for o in m["opps"]:
            p.setBrush(QColor("#FF8A50"))
            p.drawEllipse(pt(o), 3, 3)
        if m["mate"]:
            p.setBrush(QColor("#4FC3F7"))
            p.drawEllipse(pt(m["mate"]), 3, 3)
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(pt(m["ball"]), 3, 3)
        p.setBrush(QColor(self.cfg.accent_color))
        p.setPen(QPen(QColor("#FFFFFF"), 1))
        p.drawEllipse(me, 4, 4)


class MinimapWindow(OverlayWindow):
    """The top-down minimap as its own small click-through window (default: bottom-left corner)."""

    def __init__(self, cfg: OverlayConfig) -> None:
        super().__init__(cfg)
        self.setWindowTitle("Rocket Coach minimap")

    def _place(self) -> None:
        screen = QGuiApplication.primaryScreen().availableGeometry()
        h = int(self.cfg.minimap_size * self.cfg.scale)
        w = int(h * 0.8)
        x = self.cfg.minimap_x if self.cfg.minimap_x >= 0 else screen.left() + int(screen.width() * 0.015)
        y = self.cfg.minimap_y if self.cfg.minimap_y >= 0 else screen.bottom() - h - int(screen.height() * 0.08)
        self.setGeometry(QRect(x, y, w, h))

    def register_hotkeys(self) -> None:  # the main overlay owns the hotkeys
        return

    def mouseMoveEvent(self, e) -> None:  # noqa: ANN001, N802
        if self.interactive and self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e) -> None:  # noqa: ANN001, N802
        if self._drag is not None:
            self.cfg.minimap_x, self.cfg.minimap_y = self.x(), self.y()
        self._drag, self._resize = None, False

    def paintEvent(self, _e) -> None:  # noqa: ANN001, N802
        st = self.status
        if self.stale or st is None or st.minimap is None:
            if not self.interactive:
                return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = QColor(self.cfg.background_color)
        bg.setAlphaF(0.75)
        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width(), self.height(), 8, 8)
        p.fillPath(path, bg)
        if self.interactive:
            p.setPen(QPen(QColor(self.cfg.accent_color), 2, Qt.PenStyle.DashLine))
            p.drawRoundedRect(1, 1, self.width() - 2, self.height() - 2, 8, 8)
        if st is not None and st.minimap is not None and not self.stale:
            self._paint_minimap(p, QRect(6, 6, self.width() - 12, self.height() - 12), st)
        p.end()
