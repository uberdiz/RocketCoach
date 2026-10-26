"""Configuration: defaults in code, overridden by config.toml next to the project (or --config)."""

from __future__ import annotations

import logging
import tomllib
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.toml"


@dataclass
class CoachConfig:
    min_confidence: float = 0.55
    min_seconds_between_tips: float = 8.0  # global: keep tips sparse while playing
    rule_cooldown_seconds: float = 45.0  # same rule can't repeat sooner than this
    tip_display_seconds: float = 6.0
    show_strengths: bool = True
    disabled_categories: list[str] = field(default_factory=list)
    focus: str = "auto"  # one session goal, e.g. "defense", "boost"; "auto" picks from history
    player_name: str = ""  # which car is you; empty = first human car
    real_rank: str = "Diamond 1"  # your ranked 2v2 rank; sets coaching level (any rank: "Bronze 2" ... "SSL")


@dataclass
class OverlayConfig:
    x: int = -1  # -1 = automatic: left edge, below the quick-chat area
    y: int = -1
    width: int = 400
    minimap_x: int = -1  # separate minimap window; -1 = automatic (bottom-left corner)
    minimap_y: int = -1
    minimap_size: int = 150  # height in px (width is 0.8 x height)
    scale: float = 1.0
    opacity: float = 0.95
    font_family: str = "Segoe UI"
    font_size: int = 13
    accent_color: str = "#4FC3F7"
    text_color: str = "#FFFFFF"
    background_color: str = "#101418"
    show_explanation: bool = True
    show_minimap: bool = True
    hotkey_interact: str = "ctrl+alt+o"
    hotkey_mute: str = "ctrl+alt+n"
    hotkey_hide: str = "ctrl+alt+h"
    hotkey_quit: str = "ctrl+alt+q"


@dataclass
class VoiceConfig:
    enabled: bool = False
    rate: int = 185  # words per minute (SAPI5 via pyttsx3)
    volume: float = 0.9


@dataclass
class LLMConfig:
    provider: str = "none"  # "none" (local rules only) | "anthropic"
    model: str = "claude-opus-5-5"  # change to claude-haiku-5-5 for ~40x lower cost
    api_key_env: str = "ANTHROPIC_API_KEY"
    max_requests_per_hour: int = 10
    timeout_seconds: float = 20.0
    max_output_tokens: int = 600
    max_retries: int = 2
    monthly_budget_usd: float = 2.0
    live_explanations: bool = False  # LLM is used for post-match reports only unless enabled


@dataclass
class DataConfig:
    data_dir: str = "data"
    record_telemetry: bool = True
    record_hz: float = 15.0


@dataclass
class TelemetryConfig:
    source: str = "mock"  # "mock" | "rlbot" | "file"
    analysis_hz: float = 30.0
    rlbot_agent_id: str = "rocketcoach/live-coach"
    mock_speed: float = 1.0
    mock_match_seconds: float = 300.0


@dataclass
class AppConfig:
    coach: CoachConfig = field(default_factory=CoachConfig)
    overlay: OverlayConfig = field(default_factory=OverlayConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    data: DataConfig = field(default_factory=DataConfig)
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)

    @property
    def data_path(self) -> Path:
        p = Path(self.data.data_dir)
        return p if p.is_absolute() else PROJECT_ROOT / p


def _merge(obj: Any, values: dict[str, Any], where: str) -> None:
    known = {f.name: f for f in fields(obj)}
    for key, val in values.items():
        if key not in known:
            log.warning("Unknown config key %s.%s (ignored)", where, key)
            continue
        cur = getattr(obj, key)
        if is_dataclass(cur):
            if isinstance(val, dict):
                _merge(cur, val, f"{where}.{key}")
            continue
        if isinstance(cur, bool) and not isinstance(val, bool):
            raise ValueError(f"Config {where}.{key} must be true/false, got {val!r}")
        if isinstance(cur, (int, float)) and not isinstance(cur, bool):
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                raise ValueError(f"Config {where}.{key} must be a number, got {val!r}")
            val = type(cur)(val)
        setattr(obj, key, val)


def load_config(path: Path | None = None) -> AppConfig:
    cfg = AppConfig()
    path = path or DEFAULT_CONFIG_PATH
    if path.exists():
        with open(path, "rb") as fh:
            _merge(cfg, tomllib.load(fh), "config")
    return cfg


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def save_config(cfg: AppConfig, path: Path | None = None) -> None:
    path = path or DEFAULT_CONFIG_PATH
    lines = ["# Rocket Coach AI configuration. Edit while the app is closed, or use the settings panel."]
    for section, values in asdict(cfg).items():
        lines.append(f"\n[{section}]")
        lines += [f"{k} = {_toml_value(v)}" for k, v in values.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
