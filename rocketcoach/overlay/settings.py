"""Settings panel shown while the overlay is in interact mode (Ctrl+Alt+O)."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSlider,
    QSpinBox,
    QVBoxLayout,
)

from rocketcoach.analysis.levels import SSL_TIER, parse_rank, rank_name
from rocketcoach.analysis.observations import CATEGORIES
from rocketcoach.config import AppConfig


class SettingsDialog(QDialog):
    def __init__(self, cfg: AppConfig, on_apply: Callable[[], None]) -> None:
        super().__init__(None)
        self.cfg = cfg
        self.on_apply = on_apply
        self.setWindowTitle("Rocket Coach settings")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        lay = QVBoxLayout(self)

        ov = QGroupBox("Overlay")
        f = QFormLayout(ov)
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(30, 100)
        self.opacity.setValue(int(cfg.overlay.opacity * 100))
        f.addRow("Opacity", self.opacity)
        self.scale = QDoubleSpinBox()
        self.scale.setRange(0.6, 2.5)
        self.scale.setSingleStep(0.1)
        self.scale.setValue(cfg.overlay.scale)
        f.addRow("Scale", self.scale)
        self.font_size = QSpinBox()
        self.font_size.setRange(8, 28)
        self.font_size.setValue(cfg.overlay.font_size)
        f.addRow("Font size", self.font_size)
        self.explain = QCheckBox("Show explanation under the tip")
        self.explain.setChecked(cfg.overlay.show_explanation)
        f.addRow(self.explain)
        lay.addWidget(ov)

        co = QGroupBox("Coaching")
        f = QFormLayout(co)
        self.min_conf = QDoubleSpinBox()
        self.min_conf.setRange(0.3, 0.95)
        self.min_conf.setSingleStep(0.05)
        self.min_conf.setValue(cfg.coach.min_confidence)
        f.addRow("Minimum confidence", self.min_conf)
        self.gap = QSpinBox()
        self.gap.setRange(3, 120)
        self.gap.setValue(int(cfg.coach.min_seconds_between_tips))
        f.addRow("Seconds between tips", self.gap)
        self.cooldown = QSpinBox()
        self.cooldown.setRange(10, 600)
        self.cooldown.setValue(int(cfg.coach.rule_cooldown_seconds))
        f.addRow("Same-tip cooldown (s)", self.cooldown)
        self.focus = QComboBox()
        self.focus.addItems(["auto", *CATEGORIES])
        self.focus.setCurrentText(cfg.coach.focus)
        f.addRow("Session focus", self.focus)
        self.rank = QComboBox()
        self.rank.addItems([rank_name(t) for t in range(SSL_TIER + 1)])
        tier = parse_rank(cfg.coach.real_rank)
        self.rank.setCurrentText(rank_name(tier if tier is not None else 12))
        f.addRow("Your rank (sets coaching level)", self.rank)
        self.strengths = QCheckBox("Show positive feedback")
        self.strengths.setChecked(cfg.coach.show_strengths)
        f.addRow(self.strengths)
        cats = QHBoxLayout()
        self.cat_boxes = {}
        for c in CATEGORIES:
            b = QCheckBox(c)
            b.setChecked(c not in cfg.coach.disabled_categories)
            self.cat_boxes[c] = b
            cats.addWidget(b)
        f.addRow("Categories", cats)
        lay.addWidget(co)

        vo = QGroupBox("Voice")
        f = QFormLayout(vo)
        self.voice = QCheckBox("Speak tips (offline Windows voice)")
        self.voice.setChecked(cfg.voice.enabled)
        f.addRow(self.voice)
        self.rate = QSpinBox()
        self.rate.setRange(100, 320)
        self.rate.setValue(cfg.voice.rate)
        f.addRow("Speech rate (wpm)", self.rate)
        lay.addWidget(vo)

        lay.addWidget(QLabel("Hotkeys: Ctrl+Alt+O interact/lock · Ctrl+Alt+N mute · Ctrl+Alt+H hide · "
                             "Ctrl+Alt+Q quit (change them in config.toml)"))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Close)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.close)
        lay.addWidget(bb)

    def _save(self) -> None:
        c = self.cfg
        c.overlay.opacity = self.opacity.value() / 100
        c.overlay.scale = round(self.scale.value(), 2)
        c.overlay.font_size = self.font_size.value()
        c.overlay.show_explanation = self.explain.isChecked()
        c.coach.min_confidence = round(self.min_conf.value(), 2)
        c.coach.min_seconds_between_tips = float(self.gap.value())
        c.coach.rule_cooldown_seconds = float(self.cooldown.value())
        c.coach.focus = self.focus.currentText()
        c.coach.real_rank = self.rank.currentText()  # takes effect from the next match
        c.coach.show_strengths = self.strengths.isChecked()
        c.coach.disabled_categories = [k for k, b in self.cat_boxes.items() if not b.isChecked()]
        c.voice.enabled = self.voice.isChecked()
        c.voice.rate = self.rate.value()
        self.on_apply()
