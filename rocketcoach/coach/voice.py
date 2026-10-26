"""Optional text-to-speech for live tips (Windows SAPI5 via pyttsx3, fully offline)."""

from __future__ import annotations

import logging
import queue
import threading

log = logging.getLogger(__name__)


class Voice:
    def __init__(self, enabled: bool, rate: int = 185, volume: float = 0.9) -> None:
        self.enabled = enabled
        self.muted = False
        self.rate = rate
        self.volume = volume
        self.available = True
        self._q: queue.Queue[str | None] = queue.Queue(maxsize=2)
        self._thread: threading.Thread | None = None
        if enabled:
            self._start()

    def _start(self) -> None:
        def run() -> None:
            try:
                import pyttsx3  # optional dependency
                engine = pyttsx3.init()
                engine.setProperty("rate", self.rate)
                engine.setProperty("volume", self.volume)
            except Exception as exc:  # noqa: BLE001
                self.available = False
                log.warning("Voice disabled: text-to-speech unavailable (%s)", exc)
                return
            while True:
                text = self._q.get()
                if text is None:
                    break
                try:
                    engine.setProperty("rate", self.rate)
                    engine.say(text)
                    engine.runAndWait()
                except Exception as exc:  # noqa: BLE001
                    log.warning("TTS error: %s", exc)

        self._thread = threading.Thread(target=run, name="voice", daemon=True)
        self._thread.start()

    def say(self, text: str) -> None:
        if not self.enabled or self.muted or not self.available:
            return
        if self._thread is None:
            self._start()
        try:
            self._q.put_nowait(text)  # drop rather than queue stale advice
        except queue.Full:
            pass

    def toggle_mute(self) -> bool:
        self.muted = not self.muted
        return self.muted

    def close(self) -> None:
        if self._thread is not None:
            try:
                self._q.put_nowait(None)
            except queue.Full:
                pass
