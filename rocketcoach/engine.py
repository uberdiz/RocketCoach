"""Coaching session: telemetry frames in, live tips + status out, post-match report at the end.

Runs on the telemetry source's thread. Analysis is throttled to `analysis_hz`; recording happens at its
own rate. UI code subscribes via the `on_status` / `on_tip` / `on_report` callbacks.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from rocketcoach.analysis.boost import BoostCoach
from rocketcoach.analysis.boostball import BALL_ACTIONS, BoostBallCaller, BoostCall
from rocketcoach.analysis.context import build_context, goal_side
from rocketcoach.analysis.decisions import DecisionModel, DecisionTracker, summarize
from rocketcoach.analysis.events import Event, EventDetector
from rocketcoach.analysis.guide import PositionGuide
from rocketcoach.analysis.kickoff import KickoffCoach
from rocketcoach.analysis.levels import ON_SPOT_BY_LEVEL, level_group, parse_rank, rank_family
from rocketcoach.analysis.mechanics import MechanicsAnalyzer
from rocketcoach.analysis.observations import Observation
from rocketcoach.analysis.plays import PlayReviewer
from rocketcoach.analysis.rank import estimate as estimate_rank
from rocketcoach.analysis.tactical import TacticalRules
from rocketcoach.coach.prioritizer import Coach, LiveTip
from rocketcoach.config import AppConfig
from rocketcoach.report.postmatch import MatchData, build_report, flat_metrics, to_html, to_markdown
from rocketcoach.storage.db import Database
from rocketcoach.telemetry.models import GameFrame, MatchPhase, standard_boost_pads
from rocketcoach.telemetry.recorder import Recorder
from rocketcoach.telemetry.source import ALL_CAPABILITIES, TelemetrySource

log = logging.getLogger(__name__)
STALE_AFTER = 2.0  # seconds without frames before the overlay shows "no telemetry"
STATUS_INTERVAL = 0.1  # wall-clock seconds between overlay status updates
LIVE_PHASES = (MatchPhase.COUNTDOWN, MatchPhase.KICKOFF, MatchPhase.ACTIVE)


@dataclass
class Status:
    connected: bool
    simulated: bool
    phase: str
    boost: float
    role: str
    position: str
    focus: str
    score: tuple[int, int]
    fps: float
    message: str = ""
    guide: str = ""  # "Now:" instruction
    guide_ok: bool = True  # already at the recommended spot
    urgency: int = 0
    minimap: dict | None = None  # team-relative positions for the overlay minimap
    go_for: str = ""  # "BALL" / "BOOST" badge
    go_why: str = ""


class CoachSession:
    def __init__(self, cfg: AppConfig, source: TelemetrySource, db: Database | None = None,
                 player_rank: str | None = None) -> None:
        self.cfg = cfg
        self.source = source
        self.db = db
        self.real_tier = parse_rank(cfg.coach.real_rank)
        self.level = level_group(self.real_tier)
        # Training-pack difficulty follows your real rank unless a caller overrides it.
        self._rank_override = player_rank
        self.player_rank = player_rank or (rank_family(self.real_tier) if self.real_tier is not None else "Diamond")
        # The decision model learns across matches: load everything recorded so far (real and simulated kept apart).
        self.decision_model = DecisionModel()
        if db is not None:
            self.decision_model.load(db.decision_aggregates(source.simulated))
        self.on_status: Callable[[Status], None] = lambda s: None
        self.on_tip: Callable[[LiveTip], None] = lambda t: None
        self.on_report: Callable[[dict, Path | None], None] = lambda r, p: None
        self._lock = threading.RLock()
        self._fps_count = 0
        self._fps_t = time.monotonic()
        self.fps = 0.0
        self.last_frame_wall = 0.0
        self._last_status_wall = 0.0
        self.focus = cfg.coach.focus
        self._reset_match()

    # ---- lifecycle ----------------------------------------------------------------------------
    def _reset_match(self) -> None:
        self.my_index: int | None = None
        self.match_id: int | None = None
        self.start_time = 0.0
        self.last_frame: GameFrame | None = None
        self.detector: EventDetector | None = None
        self.tactical = TacticalRules()
        self.boost: BoostCoach | None = None
        self.mech: MechanicsAnalyzer | None = None
        self.guide = PositionGuide(ON_SPOT_BY_LEVEL[self.level])
        self.plays: PlayReviewer | None = None
        self.kickoff: KickoffCoach | None = None
        self.decisions: DecisionTracker | None = None
        self.guidance = None
        self.caller = BoostBallCaller(standard_boost_pads(), self.level)
        self.call = BoostCall("")
        self.coach = Coach(self.cfg.coach)
        self.recorder: Recorder | None = None
        self._last_analysis = -1e9
        self._pending_events: list[Event] = []
        self.finished = False

    def start(self) -> None:
        self.source.start(self.handle_frame)

    def stop(self) -> dict | None:
        self.source.stop()
        return self.finish(complete=False)

    def _resolve_player(self, f: GameFrame) -> int | None:
        name = self.cfg.coach.player_name.strip().lower()
        for c in f.cars:
            if name and c.name.lower().startswith(name):
                return c.index
        for c in f.cars:
            if not c.is_bot:
                return c.index
        return None

    # ---- per frame ----------------------------------------------------------------------------
    def handle_frame(self, f: GameFrame) -> None:
        with self._lock:
            self._handle(f)

    def _handle(self, f: GameFrame) -> None:
        now = time.monotonic()
        self.last_frame_wall = now
        self._fps_count += 1
        if now - self._fps_t >= 1.0:
            self.fps = self._fps_count / (now - self._fps_t)
            self._fps_count, self._fps_t = 0, now
        if self.finished:
            if f.phase in (MatchPhase.COUNTDOWN, MatchPhase.KICKOFF) and f.time < 5:
                self._reset_match()  # a new match started on the same connection
            else:
                return
        if self.last_frame is not None and f.time < self.last_frame.time - 1.0:
            log.info("Game time went backwards: treating as a new match")
            self.finish(complete=False)
            self._reset_match()
        if self.my_index is None:
            self.my_index = self._resolve_player(f)
            if self.my_index is None:
                self._emit_status(f, message="Waiting for a human player in the match...")
                return
            self._begin_match(f)
        assert self.detector and self.boost and self.mech and self.plays and self.kickoff and self.decisions
        if self.recorder:
            self.recorder.write(f)
        ctx = build_context(f, self.my_index)
        events = self.detector.update(f, ctx)
        self._pending_events += events
        self.last_frame = f
        if f.phase == MatchPhase.ENDED:
            self.finish(complete=True)
            return
        if f.time - self._last_analysis < 1.0 / self.cfg.telemetry.analysis_hz:
            return
        self._last_analysis = f.time
        events, self._pending_events = self._pending_events, []
        obs: list[Observation] = []
        live_play = f.phase in (MatchPhase.ACTIVE, MatchPhase.KICKOFF)
        obs += self.kickoff.update(f, events)
        obs += self.decisions.update(f, events)
        if ctx is not None and (live_play or f.phase == MatchPhase.COUNTDOWN):
            advice = None
            if f.phase == MatchPhase.ACTIVE and ctx.i_am_first_man and not ctx.their_possession and \
                    ctx.me.location.dist(ctx.ball) < 2500:
                adv = self.decisions.advise(f, ctx.me)
                advice = adv[1] if adv else None
            kick = self.kickoff.plan if f.phase in (MatchPhase.COUNTDOWN, MatchPhase.KICKOFF) else None
            self.guidance = self.guide.update(ctx, kick, advice)
            self.call = self.caller.update(ctx, self.guidance)
        if ctx is not None and live_play:
            if f.phase == MatchPhase.ACTIVE:
                obs += self.tactical.update(ctx, events)
            obs += self.boost.update(ctx, events)
        obs += self.mech.update(ctx, events)
        obs += self.plays.update(ctx, self.guidance if live_play else None, events)
        dead_ball = f.phase in (MatchPhase.GOAL_SCORED, MatchPhase.REPLAY, MatchPhase.COUNTDOWN)
        tip = self.coach.offer(obs, f.time, dead_ball=dead_ball)
        if tip:
            self.on_tip(tip)
        self._emit_status(f, ctx)

    def _begin_match(self, f: GameFrame) -> None:
        me = f.car(self.my_index)  # type: ignore[arg-type]
        assert me is not None
        self.start_time = f.time
        self.detector = EventDetector(self.my_index)  # type: ignore[arg-type]
        self.boost = BoostCoach(self.source.boost_pads or standard_boost_pads())
        self.mech = MechanicsAnalyzer(self.my_index)  # type: ignore[arg-type]
        self.plays = PlayReviewer(self.my_index)  # type: ignore[arg-type]
        # Re-read the rank each match so a change in the settings panel applies without restarting.
        self.real_tier = parse_rank(self.cfg.coach.real_rank)
        self.level = level_group(self.real_tier)
        self.guide.radius = ON_SPOT_BY_LEVEL[self.level]
        self.caller = BoostBallCaller(self.source.boost_pads or standard_boost_pads(), self.level)
        if self._rank_override is None and self.real_tier is not None:
            self.player_rank = rank_family(self.real_tier)
        self.kickoff = KickoffCoach(self.my_index, self.level)  # type: ignore[arg-type]
        self.decisions = DecisionTracker(self.my_index, self.decision_model, self.level)  # type: ignore[arg-type]
        rec_path = None
        if self.cfg.data.record_telemetry:
            rec_path = self.cfg.data_path / "recordings" / time.strftime("%Y%m%d-%H%M%S.jsonl.gz")
            self.recorder = Recorder(rec_path, self.cfg.data.record_hz)
        if self.db:
            caps = [c for c in ALL_CAPABILITIES if c in self.source.capabilities]
            self.match_id = self.db.start_match(self.source.name, self.source.simulated, me.name, me.team, caps,
                                                str(rec_path) if rec_path else None)
        if self.focus == "auto" and self.db:
            self.focus = self._auto_focus()
            self.coach.cfg.focus = self.focus
        log.info("Coaching %s (team %d), match id %s, focus %s, level %s (%s), decision model %d samples", me.name,
                 me.team, self.match_id, self.focus, self.level, self.cfg.coach.real_rank, self.decision_model.samples)

    def _auto_focus(self) -> str:
        """Pick the session goal from the most frequent mistake category in recent matches."""
        assert self.db
        rows = self.db.conn.execute(
            "SELECT category, COUNT(*) n FROM observations WHERE kind='mistake' AND match_id IN "
            "(SELECT id FROM matches WHERE simulated=? ORDER BY id DESC LIMIT 5) GROUP BY category ORDER BY n DESC "
            "LIMIT 1", (int(self.source.simulated),)).fetchone()
        return rows["category"] if rows else "defense"

    def _emit_status(self, f: GameFrame, ctx=None, message: str = "") -> None:  # noqa: ANN001
        now = time.monotonic()
        if now - self._last_status_wall < STATUS_INTERVAL and not message:
            return  # the overlay doesn't need more than ~10 repaints/s
        self._last_status_wall = now
        role, pos, boost = "-", "-", 0.0
        if ctx is not None:
            boost = ctx.me.boost
            if ctx.is_2v2:
                role = "1st man" if ctx.i_am_first_man else ("last back" if ctx.i_am_last_man else "2nd man")
            else:
                role = "solo"
            pos = "goal-side" if goal_side(ctx.me, ctx.ball) else "ahead of ball"
            if ctx.goal_threat > 0.6:
                pos = "DANGER: " + pos
        team = ctx.team if ctx else 0
        score = (f.scores[team], f.scores[1 - team])
        g = self.guidance
        guide, ok, urgency, minimap = "", True, 0, None
        if ctx is not None and g is not None:
            guide, ok, urgency = g.text, g.on_spot, g.urgency
            target = g.target
            c = self.call
            if c.call == "BOOST" and c.pad is not None:
                target = c.pad
                if g.action in BALL_ACTIONS:  # the 50/50 override: keep the text in line with the badge
                    guide, ok = "Low boost: let your teammate take it, grab the pad", False
            flip = -1.0 if team == 1 else 1.0  # draw your own goal at the bottom for both teams

            def xy(v) -> tuple[float, float]:  # noqa: ANN001
                return (v.x * flip, v.y * flip)
            minimap = {"me": xy(ctx.me.location), "ball": xy(ctx.ball), "target": xy(target),
                       "pad": xy(self.call.pad) if self.call.pad is not None else None,
                       "mate": xy(ctx.mate.location) if ctx.mate else None,
                       "opps": [xy(o.location) for o in ctx.opps if not o.is_demolished]}
        self.on_status(Status(True, self.source.simulated, f.phase.value, boost, role, pos, self.focus, score,
                              self.fps, message, guide, ok, urgency, minimap,
                              self.call.call if ctx is not None and f.phase in LIVE_PHASES else "", self.call.reason))

    # ---- end of match -------------------------------------------------------------------------
    def finish(self, complete: bool) -> dict | None:
        with self._lock:
            if self.finished or self.detector is None or self.last_frame is None:
                return None
            self.finished = True
            if self.recorder:
                self.recorder.close()
            f = self.last_frame
            me = f.car(self.my_index)  # type: ignore[arg-type]
            team = me.team if me else 0
            shown = {id(t.obs) for t in self.coach.shown}
            self.tactical.stats["guide_on_spot_s"] = self.guide.on_spot_s
            self.tactical.stats["guide_total_s"] = self.guide.total_s
            self.tactical.stats["call_ball_s"] = round(self.caller.counts["BALL"], 1)
            self.tactical.stats["call_boost_s"] = round(self.caller.counts["BOOST"], 1)
            data = MatchData(
                match_id=self.match_id or 0, simulated=self.source.simulated, source=self.source.name,
                player=me.name if me else "?", team=team, scores=f.scores, start_time=self.start_time,
                end_time=f.time, complete=complete,
                capabilities=[c for c in ALL_CAPABILITIES if c in self.source.capabilities],
                observations=self.coach.history, shown_ids=shown, events=self.detector.events,
                boost=self.boost.s.to_dict() if self.boost else {}, mech=self.mech.s.to_dict() if self.mech else {},
                tactical=self.tactical.stats, telemetry_gaps=self.detector.gaps,
                suppressed=dict(self.coach.suppressed))
            self._attach_learning(data)
            if data.tactical.get("active_s", 0) < 5:
                log.info("Match too short to report (%.1fs of play)", data.tactical.get("active_s", 0))
                return None
            prev = []
            if self.db and self.match_id:
                prev = self.db.recent_metrics(20, before_match=self.match_id, simulated=self.source.simulated)
            report = build_report(data, prev, self.player_rank)
            md = to_markdown(report)
            html_path: Path | None = None
            out_dir = self.cfg.data_path / "reports"
            out_dir.mkdir(parents=True, exist_ok=True)
            stem = f"match-{self.match_id or int(time.time())}"
            html_path = out_dir / f"{stem}.html"
            html_path.write_text(to_html(report, md), encoding="utf-8")
            (out_dir / f"{stem}.md").write_text(md, encoding="utf-8")
            if self.db and self.match_id:
                gf, ga = f.scores[team], f.scores[1 - team]
                self.db.finish_match(self.match_id, gf, ga, f.time - self.start_time, complete)
                self.db.add_observations(self.match_id, self.coach.history, shown)
                self.db.add_events(self.match_id, [(e.time, e.type, e.data) for e in self.detector.events
                                                   if e.type not in ("phase",)])
                self.db.set_metrics(self.match_id, flat_metrics(data))
                self.db.save_report(self.match_id, report, md, str(html_path))
                self.db.add_recommendations(self.match_id, report["training_plan"]["focus"])
            if self.db and self.decisions:
                self.db.add_decisions(self.match_id, self.source.simulated, self.decisions.finished)
                self.db.add_kickoffs(self.match_id, self.source.simulated, self.kickoff.samples if self.kickoff
                                     else [])
            log.info("Report written: %s", html_path)
        self.on_report(report, html_path)
        return report

    def _attach_learning(self, data) -> None:  # noqa: ANN001
        """Decisions, kickoffs and the rank estimate for the report."""
        tac, b = data.tactical, data.boost
        active = max(tac.get("active_s", 0.0), 1.0)
        data.level = self.level
        if self.decisions:
            data.decisions = summarize(self.decisions.finished, self.decision_model)
        if self.kickoff:
            data.kickoffs = self.kickoff.samples
        if self.db:
            data.kickoff_history = self.db.kickoff_history(self.source.simulated)
        metrics = {
            "avg_speed": tac.get("speed_sum", 0.0) / active if tac.get("speed_sum") else None,
            "supersonic_pct": 100 * tac.get("supersonic_s", 0.0) / active if tac.get("speed_sum") else None,
            "boost_per_min": b.get("collected", 0) / (active / 60) if b else None,
        }
        past: list[float] = []
        if self.db and self.match_id:
            past = [m["rank_est_tier"] for m in self.db.recent_metrics(10, before_match=self.match_id,
                                                                       simulated=self.source.simulated)
                    if m.get("rank_est_tier") is not None]
        data.rank = estimate_rank({k: (round(v, 2) if v is not None else None) for k, v in metrics.items()},
                                  past, self.real_tier, active, vs_bots=True)

    def is_stale(self) -> bool:
        return time.monotonic() - self.last_frame_wall > STALE_AFTER
