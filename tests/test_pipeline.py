"""End-to-end on simulated data: engine, storage, recording, report, history comparison, missing data."""

import gzip
import json
from pathlib import Path

from rocketcoach.config import AppConfig, load_config, save_config
from rocketcoach.engine import CoachSession
from rocketcoach.report.postmatch import to_html, to_markdown
from rocketcoach.storage.db import Database
from rocketcoach.telemetry.file_source import FileSource
from rocketcoach.telemetry.mock import MockSource
from rocketcoach.telemetry.recorder import read_recording


def play(tmp: Path, seed: int, seconds: float = 150, drop: float = 0.0, db: Database | None = None):  # noqa: ANN201
    cfg = AppConfig()
    cfg.data.data_dir = str(tmp)
    cfg.telemetry.mock_match_seconds = seconds
    db = db or Database(tmp / "t.db")
    src = MockSource(seed=seed, match_seconds=seconds, speed=0, drop_rate=drop)
    s = CoachSession(cfg, src, db)
    tips = []
    s.on_tip = tips.append
    rng = src.sim.rng
    while not s.finished:
        f = src.sim.step()
        if rng.random() >= drop:
            s.handle_frame(f)
        if f.time > 2000:
            break
    return s, db, tips


def test_full_simulated_match(tmp_path):
    s, db, tips = play(tmp_path, seed=3)
    m = db.matches()[0]
    assert m["complete"] == 1 and m["simulated"] == 1
    rep = json.loads(db.report(m["id"])["json"])
    assert rep["data_source"]["warning"].startswith("SIMULATED")
    assert len(rep["mistakes"]) <= 3 and len(rep["strengths"]) <= 3
    assert {g["area"] for g in rep["grades"]} >= {"Positioning & defense", "Boost management"}
    assert rep["comparison"]["5"] is None  # first match: no history
    # rule-based tips keep the global gap; event feedback (50/50 results, reviews) may follow its moment directly
    rule_tips = [t for t in tips if t.obs.category not in ("challenge", "defense") and t.obs.kind != "review"
                 and t.obs.rule_id != "goal_for"]
    times = [t.shown_at for t in rule_tips]
    assert all(b - a >= 8 for a, b in zip(times, times[1:]))
    assert len(tips) <= rep["metrics"]["active_s"] / 3 + 3
    assert rep["metrics"]["in_position_pct"] >= 0 and "play_reviews" in rep
    html = Path(db.report(m["id"])["html_path"]).read_text(encoding="utf-8")
    assert "<svg" in html and "prefers-color-scheme" in html
    # recording exists and can be read back
    frames = list(read_recording(Path(m["recording_path"])))
    assert len(frames) > 100


def test_history_comparison_after_several_matches(tmp_path):
    _, db, _ = play(tmp_path, seed=1, seconds=60)
    for seed in (2, 3):
        _, db, _ = play(tmp_path, seed=seed, seconds=60, db=db)
    rep = json.loads(db.report(db.matches()[0]["id"])["json"])
    assert rep["comparison"]["5"] and rep["comparison"]["5"][0]["matches"] == 2


def test_missing_telemetry_frames(tmp_path):
    s, db, _ = play(tmp_path, seed=5, seconds=60, drop=0.4)
    m = db.matches()[0]
    assert m["complete"] == 1  # still produces a report with 40% of packets lost


def test_stop_mid_match_produces_incomplete_report(tmp_path):
    cfg = AppConfig()
    cfg.data.data_dir = str(tmp_path)
    db = Database(tmp_path / "t.db")
    src = MockSource(seed=9, match_seconds=300, speed=0)
    s = CoachSession(cfg, src, db)
    for _ in range(60 * 40):
        s.handle_frame(src.sim.step())
    rep = s.finish(complete=False)
    assert rep is not None and db.matches()[0]["complete"] == 0
    assert "n/a" in {g["grade"] for g in rep["grades"]}  # 40 s is too little to grade positioning


def test_replay_of_truncated_recording(tmp_path):
    s, db, _ = play(tmp_path, seed=4, seconds=60)
    rec = Path(db.matches()[0]["recording_path"])
    raw = gzip.decompress(rec.read_bytes())
    cut = tmp_path / "cut.jsonl.gz"
    data = gzip.compress(raw[: len(raw) // 2] + b'{"broken json\n')
    cut.write_bytes(data[: len(data) - 20])  # truncated gzip stream
    frames = list(read_recording(cut))
    assert 10 < len(frames)
    cfg = AppConfig()
    cfg.data.data_dir = str(tmp_path / "re")
    db2 = Database(tmp_path / "re.db")
    src = FileSource(cut)
    s2 = CoachSession(cfg, src, db2)
    s2.start()
    src.join(30)
    rep = s2.finish(complete=False)
    assert rep is not None and rep["data_source"]["complete"] is False


def test_report_renders(tmp_path):
    s, db, _ = play(tmp_path, seed=6, seconds=60)
    rep = json.loads(db.report(db.matches()[0]["id"])["json"])
    md = to_markdown(rep)
    assert "## Next session training plan" in md and "## Timeline" in md
    assert to_html(rep, md).startswith("<!doctype html>")


def test_export_and_delete(tmp_path):
    s, db, _ = play(tmp_path, seed=2, seconds=60)
    out = db.export_all(tmp_path / "export.json")
    data = json.loads(out.read_text())
    assert data["matches"] and data["observations"] is not None
    paths = db.delete_all()
    assert db.matches() == [] and paths


def test_config_roundtrip_and_validation(tmp_path):
    cfg = AppConfig()
    cfg.coach.min_confidence = 0.7
    cfg.coach.disabled_categories = ["mechanics"]
    p = tmp_path / "c.toml"
    save_config(cfg, p)
    back = load_config(p)
    assert back.coach.min_confidence == 0.7 and back.coach.disabled_categories == ["mechanics"]
    p.write_text('[coach]\nmin_confidence = "high"\n')
    try:
        load_config(p)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "min_confidence" in str(e)
