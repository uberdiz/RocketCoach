"""Command-line entry point: `python -m rocketcoach <command>`."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import webbrowser
from pathlib import Path

from rocketcoach.config import PROJECT_ROOT, AppConfig, load_config, save_config

log = logging.getLogger("rocketcoach")


def _setup_logging(cfg: AppConfig, verbose: bool) -> None:
    logdir = cfg.data_path / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    # Handlers live on the "rocketcoach" logger, not the root: importing rlbot clears the root logger's handlers.
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handlers: list[logging.Handler] = [logging.FileHandler(logdir / "rocketcoach.log", encoding="utf-8")]
    if sys.stdout is not None:  # None under pythonw.exe (how RLBot starts the script)
        handlers.append(logging.StreamHandler(sys.stdout))
    for h in handlers:
        h.setFormatter(fmt)
        log.addHandler(h)
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    log.propagate = False


def _db(cfg: AppConfig):  # noqa: ANN202
    from rocketcoach.storage.db import Database
    return Database(cfg.data_path / "rocketcoach.db")


def _make_source(cfg: AppConfig, args: argparse.Namespace):  # noqa: ANN202
    kind = args.source or cfg.telemetry.source
    if kind == "mock":
        from rocketcoach.telemetry.mock import MockSource
        return MockSource(seed=args.seed, match_seconds=cfg.telemetry.mock_match_seconds,
                          speed=args.speed or cfg.telemetry.mock_speed)
    if kind == "rlbot":
        from rocketcoach.telemetry.rlbot_source import RLBotSource
        return RLBotSource(cfg.telemetry.rlbot_agent_id, cfg.data_path / "known_humans.json",
                           cfg.coach.player_name)
    if kind == "file":
        from rocketcoach.telemetry.file_source import FileSource
        if not args.file:
            raise SystemExit("--source file needs --file <recording.jsonl.gz>")
        return FileSource(Path(args.file), speed=args.speed or 1.0)
    raise SystemExit(f"Unknown source '{kind}' (use mock, rlbot or file)")


INSTANCE_NAME = "rocketcoach-overlay"


def _take_over_single_instance(app):  # noqa: ANN001, ANN202
    """Only one coach overlay at a time: a newly started coach asks any older one to quit, then takes over.

    RLBot starts a fresh coach for each match it launches; an older coach (e.g. from a previous match) would
    otherwise stay on screen with a dead connection next to the new one.
    """
    from PySide6.QtNetwork import QLocalServer, QLocalSocket

    old = QLocalSocket()
    old.connectToServer(INSTANCE_NAME)
    if old.waitForConnected(300):
        old.write(b"quit")
        old.flush()
        old.waitForBytesWritten(500)
        old.disconnectFromServer()
        log.info("Asked the previous coach overlay to close")
        time.sleep(1.0)
    QLocalServer.removeServer(INSTANCE_NAME)
    server = QLocalServer()
    if not server.listen(INSTANCE_NAME):
        log.warning("Single-instance guard unavailable: %s", server.errorString())
        return server

    def on_connection() -> None:
        conn = server.nextPendingConnection()
        if conn is not None and conn.waitForReadyRead(500) and conn.readAll().data() == b"quit":
            log.info("A newer coach overlay started; closing this one")
            app.quit()
    server.newConnection.connect(on_connection)
    return server


def _quit_with_console(app):  # noqa: ANN001, ANN202
    """Closing the command prompt (or Ctrl+C in it) shuts the overlay down cleanly instead of leaving it behind.

    Windows tells console programs about Ctrl+C and the window being closed through a console control
    handler that runs on its own thread. It flags a quit for the Qt thread and then waits (Windows allows
    about 5 s on close) until the app has finished the match report and closed the database.
    Returns an event to set once cleanup is done, or None when there is no console (pythonw).
    """
    import threading

    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    from PySide6.QtCore import QTimer
    kernel32 = ctypes.windll.kernel32
    if not kernel32.GetConsoleWindow():
        return None
    requested, done = threading.Event(), threading.Event()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
    def handler(event: int) -> bool:
        log.info("Console %s: closing the coach", "closed" if event == 2 else "interrupted")
        requested.set()
        done.wait(4.0)
        return True
    kernel32.SetConsoleCtrlHandler(handler, True)
    poll = QTimer(app)
    poll.timeout.connect(lambda: requested.is_set() and app.quit())
    poll.start(200)
    app._console_handler = handler  # keep the callback alive for the app's lifetime
    return done


def cmd_run(cfg: AppConfig, args: argparse.Namespace) -> int:
    """Live overlay + coaching."""
    from PySide6.QtWidgets import QApplication

    from rocketcoach.coach.llm import compact_report, make_provider
    from rocketcoach.coach.voice import Voice
    from rocketcoach.engine import CoachSession
    from rocketcoach.overlay.settings import SettingsDialog
    from rocketcoach.overlay.window import Bridge, MinimapWindow, OverlayWindow

    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    instance_server = _take_over_single_instance(app)  # noqa: F841 - kept alive for the app's lifetime
    console_done = _quit_with_console(app)
    db = _db(cfg)
    source = _make_source(cfg, args)
    session = CoachSession(cfg, source, db, player_rank=args.rank)
    voice = Voice(cfg.voice.enabled, cfg.voice.rate, cfg.voice.volume)
    llm = make_provider(cfg.llm, db)
    bridge = Bridge()
    win = OverlayWindow(cfg.overlay)
    minimap = MinimapWindow(cfg.overlay)
    win.voice_enabled = cfg.voice.enabled
    if source.name == "rlbot":
        win.banner = "Waiting for RLBot: start an offline match from the RLBot launcher"
    settings: list[SettingsDialog] = []

    session.on_status = bridge.status.emit
    session.on_tip = bridge.tip.emit
    session.on_report = bridge.report.emit

    def on_tip(tip) -> None:  # noqa: ANN001
        win.show_tip(tip, time.monotonic(), cfg.coach.tip_display_seconds)
        voice.say(tip.obs.tip)

    def on_report(report: dict, path) -> None:  # noqa: ANN001
        summary = llm.summarize(compact_report(report)) if llm.name != "none" else None
        if summary and report.get("match_id"):
            db.save_llm_summary(report["match_id"], summary)
            if path:
                md_path = Path(path).with_suffix(".md")
                md_path.write_text(md_path.read_text(encoding="utf-8") + "\n## AI debrief\n" + summary + "\n",
                                   encoding="utf-8")
        log.info("Post-match report: %s", path)
        if path and not args.no_browser:
            webbrowser.open(Path(path).as_uri())

    def apply_settings() -> None:
        save_config(cfg, args.config)
        win.setWindowOpacity(cfg.overlay.opacity)
        win.resize(int(cfg.overlay.width * cfg.overlay.scale), win.height())
        session.coach.cfg = cfg.coach
        voice.enabled, voice.rate = cfg.voice.enabled, cfg.voice.rate
        win.voice_enabled = cfg.voice.enabled
        win.update()

    def on_hotkey(name: str) -> None:
        if name == "interact":
            interactive = not win.interactive
            win.set_click_through(not interactive)
            if cfg.overlay.show_minimap:
                minimap.set_click_through(not interactive)
            if interactive:
                dlg = SettingsDialog(cfg, apply_settings)
                settings[:] = [dlg]
                dlg.move(win.x() + win.width() + 12, win.y())
                dlg.show()
            else:
                for d in settings:
                    d.close()
                save_config(cfg, args.config)
        elif name == "mute":
            win.muted = voice.toggle_mute()
            win.update()
        elif name == "hide":
            win.setVisible(not win.isVisible())
            minimap.setVisible(win.isVisible() and cfg.overlay.show_minimap)
        elif name == "quit":
            app.quit()

    bridge.status.connect(lambda s: win.set_status(s, stale=False))
    bridge.status.connect(lambda s: minimap.set_status(s, stale=False))
    bridge.tip.connect(on_tip)
    bridge.report.connect(on_report)
    win.hotkey.connect(on_hotkey)

    from PySide6.QtCore import QTimer
    health = QTimer()

    def check_health() -> None:
        if session.is_stale():
            state = getattr(source, "status", None)
            if state in ("connected", "live"):
                win.banner = "Connected to RLBot: waiting for a match to start"
            elif state == "reconnecting":
                win.banner = getattr(source, "error", None) or "Reconnecting to RLBot..."
            win.set_status(win.status, stale=True)
            minimap.set_status(minimap.status, stale=True)
    health.timeout.connect(check_health)
    health.start(1000)

    win.show()
    if cfg.overlay.show_minimap:
        minimap.show()
    session.start()
    log.info("Overlay running (source=%s). Hotkeys: %s interact, %s mute, %s hide, %s quit", source.name,
             cfg.overlay.hotkey_interact, cfg.overlay.hotkey_mute, cfg.overlay.hotkey_hide, cfg.overlay.hotkey_quit)
    if args.duration:
        QTimer.singleShot(int(args.duration * 1000), app.quit)
    code = app.exec()
    session.stop()
    voice.close()
    db.close()
    if console_done is not None:
        console_done.set()
    return code


def cmd_analyze(cfg: AppConfig, args: argparse.Namespace) -> int:
    """Headless: run a simulated match or a recording through the coach and write the report."""
    from rocketcoach.engine import CoachSession
    db = _db(cfg)
    if args.file:
        from rocketcoach.telemetry.file_source import FileSource
        source = FileSource(Path(args.file), speed=0.0, simulated=not args.real)
        session = CoachSession(cfg, source, db, player_rank=args.rank)
        session.start()
        source.join()
        report = session.finish(complete=False) or {}
    else:
        from rocketcoach.telemetry.mock import MockSource
        source = MockSource(seed=args.seed, match_seconds=cfg.telemetry.mock_match_seconds, speed=0)
        session = CoachSession(cfg, source, db, player_rank=args.rank)
        tips = []
        session.on_tip = lambda t: tips.append(t)
        while True:
            f = source.sim.step()
            session.handle_frame(f)
            if session.finished or f.time > 3600:
                break
        report = session.finish(complete=True) or db.report(session.match_id or 0) and json.loads(
            db.report(session.match_id)["json"]) or {}
        print(f"Live tips that would have been shown: {len(tips)}")
        for t in tips:
            print(f"  {t.obs.time - session.start_time:6.1f}s [{t.obs.category}] {t.obs.tip}")
    if report:
        print(report.get("summary", ""))
        r = db.report(report["match_id"]) if report.get("match_id") else None
        if r:
            print(f"Report: {r['html_path']}")
    db.close()
    return 0


def cmd_learn(cfg: AppConfig, args: argparse.Namespace) -> int:
    """Replay every recording (yours, your teammates' and opponents' touches) into the decision/kickoff model."""
    from rocketcoach.analysis.context import build_context
    from rocketcoach.analysis.decisions import DecisionModel, DecisionTracker
    from rocketcoach.analysis.events import EventDetector
    from rocketcoach.analysis.kickoff import KickoffCoach
    from rocketcoach.telemetry.recorder import read_recording
    db = _db(cfg)
    done = {r["recording_path"] for r in db.conn.execute(
        "SELECT DISTINCT m.recording_path FROM matches m JOIN decision_samples d ON d.match_id = m.id")}
    rows = list(db.conn.execute("SELECT id, recording_path, simulated, player_name FROM matches "
                                "WHERE recording_path IS NOT NULL ORDER BY id"))
    total = 0
    for r in rows:
        path = Path(r["recording_path"])
        if r["recording_path"] in done or not path.exists():
            continue
        frames = list(read_recording(path))
        me = next((c.index for f in frames for c in f.cars if c.name == r["player_name"]), None)
        if me is None:
            continue
        det, tracker, kick = EventDetector(me), DecisionTracker(me, DecisionModel()), KickoffCoach(me, "high")
        for f in frames:
            evs = det.update(f, build_context(f, me))
            tracker.update(f, evs)
            kick.update(f, evs)
        db.add_decisions(r["id"], bool(r["simulated"]), tracker.finished)
        db.add_kickoffs(r["id"], bool(r["simulated"]), kick.samples)
        total += len(tracker.finished)
        print(f"match {r['id']}: {len(tracker.finished)} touches, {len(kick.samples)} kickoffs")
    real = sum(n for _, _, n, _, _ in db.decision_aggregates(False))
    print(f"Added {total} touch samples. Real-match model now has {real} samples.")
    db.close()
    return 0


def cmd_list(cfg: AppConfig, args: argparse.Namespace) -> int:
    db = _db(cfg)
    for m in db.matches(args.limit):
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(m["started_at"]))
        tag = "SIM " if m["simulated"] else "REAL"
        print(f"#{m['id']:<4} {when} {tag} {m['source']:<6} {m['score_for']}-{m['score_against']} "
              f"complete={bool(m['complete'])}")
    db.close()
    return 0


def cmd_report(cfg: AppConfig, args: argparse.Namespace) -> int:
    db = _db(cfg)
    r = db.report(args.match_id)
    if not r:
        print(f"No report for match {args.match_id}")
        return 1
    print(r["markdown"])
    if r.get("llm_summary"):
        print("\n## AI debrief\n" + r["llm_summary"])
    if args.open and r["html_path"]:
        webbrowser.open(Path(r["html_path"]).as_uri())
    db.close()
    return 0


def cmd_export(cfg: AppConfig, args: argparse.Namespace) -> int:
    db = _db(cfg)
    out = Path(args.out or cfg.data_path / f"export-{time.strftime('%Y%m%d-%H%M%S')}.json")
    print(f"Exported to {db.export_all(out)}")
    db.close()
    return 0


def cmd_delete(cfg: AppConfig, args: argparse.Namespace) -> int:
    db = _db(cfg)
    if args.all:
        if not args.yes and input("Delete ALL matches, reports and recordings? Type 'delete': ") != "delete":
            print("Cancelled.")
            return 1
        paths = db.delete_all()
    else:
        p = db.delete_match(args.match_id)
        paths = [p] if p else []
    for p in paths:
        Path(p).unlink(missing_ok=True)
    print(f"Deleted. Removed {len(paths)} recording file(s).")
    db.close()
    return 0


def cmd_verify_packs(cfg: AppConfig, args: argparse.Namespace) -> int:
    from rocketcoach.training.recommender import load_packs, verify_packs
    res = verify_packs()
    packs = {p["code"]: p for p in load_packs()}
    ok = sum(1 for r in res.values() if r["ok"])
    for code, r in res.items():
        print(f"{'OK  ' if r['ok'] else 'FAIL'} {code} {packs[code]['name']}: {r['detail']}")
    print(f"{ok}/{len(res)} packs verified against their source pages.")
    return 0 if ok == len(res) else 2


def cmd_launch_match(cfg: AppConfig, args: argparse.Namespace) -> int:
    """Start an offline RLBot match from a match.toml (needs RLBot v5 installed)."""
    from rlbot.managers import MatchManager
    toml = Path(args.match)
    with MatchManager() as mm:
        mm.ensure_server_started()
        mm.start_match(toml, wait_for_start=True)
        print("Match started. The coach script runs inside RLBot; keep this window open, Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            mm.stop_match()
    return 0


def cmd_bench(cfg: AppConfig, args: argparse.Namespace) -> int:
    """CPU / memory / per-frame cost of the analysis pipeline on this machine (simulated data)."""
    import os

    import psutil

    from rocketcoach.engine import CoachSession
    from rocketcoach.telemetry.mock import MockSimulator
    cfg.data.record_telemetry = False
    proc = psutil.Process(os.getpid())
    sim = MockSimulator(seed=1, match_seconds=120)
    frames = sim.run_frames(400)
    session = CoachSession(cfg, _NullSource(), None)
    rss0 = proc.memory_info().rss
    cpu0 = proc.cpu_times()
    t0 = time.perf_counter()
    for f in frames:
        session.handle_frame(f)
    el = time.perf_counter() - t0
    cpu1 = proc.cpu_times()
    cpu = (cpu1.user - cpu0.user) + (cpu1.system - cpu0.system)
    game_s = frames[-1].time - frames[0].time
    print(f"Frames: {len(frames)} ({game_s:.0f}s of game at 60 Hz, analysis at {cfg.telemetry.analysis_hz} Hz)")
    print(f"Analysis time: {1000 * el / len(frames):.3f} ms/frame; CPU {100 * cpu / game_s:.2f}% of one core "
          f"in real time")
    print(f"Memory: {proc.memory_info().rss / 1e6:.0f} MB RSS incl. the pre-generated test frames (+{(proc.memory_info().rss - rss0) / 1e6:.1f} MB)")
    return 0


class _NullSource:
    name, simulated, capabilities, boost_pads = "bench", True, frozenset(), []

    def start(self, cb) -> None: ...  # noqa: ANN001
    def stop(self) -> None: ...


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rocketcoach", description="Rocket Coach AI: live Rocket League coaching")
    p.add_argument("--config", type=Path, default=PROJECT_ROOT / "config.toml")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="live overlay")
    r.add_argument("--source", choices=["mock", "rlbot", "file"])
    r.add_argument("--file")
    r.add_argument("--seed", type=int, default=7)
    r.add_argument("--speed", type=float, default=0.0, help="mock/file playback speed (1 = real time)")
    r.add_argument("--rank", default=None, help="training-pack difficulty override (default: from real_rank)")
    r.add_argument("--duration", type=float, default=0.0, help="auto-quit after N seconds (testing)")
    r.add_argument("--no-browser", action="store_true", help="don't open the report when the match ends")
    r.set_defaults(func=cmd_run)

    a = sub.add_parser("analyze", help="headless analysis of a simulated match or a recording")
    a.add_argument("--file")
    a.add_argument("--real", action="store_true", help="mark the recording as real gameplay")
    a.add_argument("--seed", type=int, default=7)
    a.add_argument("--rank", default=None)
    a.set_defaults(func=cmd_analyze)

    s = sub.add_parser("list", help="list stored matches")
    s.add_argument("--limit", type=int, default=30)
    s.set_defaults(func=cmd_list)
    s = sub.add_parser("report", help="print a stored report")
    s.add_argument("match_id", type=int)
    s.add_argument("--open", action="store_true")
    s.set_defaults(func=cmd_report)
    s = sub.add_parser("export", help="export all stored data to JSON")
    s.add_argument("--out")
    s.set_defaults(func=cmd_export)
    s = sub.add_parser("delete", help="delete one match or everything")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--match-id", type=int)
    g.add_argument("--all", action="store_true")
    s.add_argument("--yes", action="store_true")
    s.set_defaults(func=cmd_delete)
    s = sub.add_parser("learn", help="build the decision/kickoff model from all recorded matches")
    s.set_defaults(func=cmd_learn)
    s = sub.add_parser("verify-packs", help="check every training-pack code against its source page")
    s.set_defaults(func=cmd_verify_packs)
    s = sub.add_parser("launch-match", help="start an offline RLBot match from a match.toml")
    s.add_argument("--match", default=str(PROJECT_ROOT / "rlbot" / "match_2v2.toml"))
    s.set_defaults(func=cmd_launch_match)
    s = sub.add_parser("bench", help="measure CPU/memory of the analysis pipeline")
    s.set_defaults(func=cmd_bench)

    args = p.parse_args(argv)
    try:
        cfg = load_config(args.config)
    except (ValueError, OSError) as exc:
        print(f"Config error in {args.config}: {exc}")
        return 2
    _setup_logging(cfg, args.verbose)
    return int(args.func(cfg, args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
