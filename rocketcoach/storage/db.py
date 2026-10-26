"""Local SQLite storage for matches, coaching observations, metrics and training recommendations."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from rocketcoach.analysis.observations import Observation

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at REAL NOT NULL,
    ended_at REAL,
    source TEXT NOT NULL,
    simulated INTEGER NOT NULL,
    player_name TEXT,
    team INTEGER,
    score_for INTEGER,
    score_against INTEGER,
    duration_s REAL,
    complete INTEGER NOT NULL DEFAULT 0,
    recording_path TEXT,
    capabilities TEXT
);
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    game_time REAL, rule_id TEXT, category TEXT, kind TEXT, tip TEXT, explanation TEXT,
    impact REAL, confidence REAL, shown INTEGER, evidence TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    game_time REAL, type TEXT, data TEXT
);
CREATE TABLE IF NOT EXISTS metrics (
    match_id INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    name TEXT NOT NULL, value REAL, PRIMARY KEY (match_id, name)
);
CREATE TABLE IF NOT EXISTS reports (
    match_id INTEGER PRIMARY KEY REFERENCES matches(id) ON DELETE CASCADE,
    json TEXT NOT NULL, markdown TEXT, html_path TEXT, llm_summary TEXT
);
CREATE TABLE IF NOT EXISTS recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    skill TEXT, pack_code TEXT, pack_name TEXT, reason TEXT
);
CREATE TABLE IF NOT EXISTS llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at REAL, provider TEXT, model TEXT, input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL,
    cache_key TEXT, response TEXT
);
CREATE TABLE IF NOT EXISTS decision_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER REFERENCES matches(id) ON DELETE CASCADE,
    simulated INTEGER NOT NULL, actor TEXT, bucket TEXT, action TEXT, value REAL, game_time REAL
);
CREATE TABLE IF NOT EXISTS kickoffs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER REFERENCES matches(id) ON DELETE CASCADE,
    simulated INTEGER NOT NULL, spawn TEXT, kind TEXT, role TEXT, my_arrival REAL, opp_arrival REAL,
    result TEXT, side TEXT
);
CREATE INDEX IF NOT EXISTS idx_dec_bucket ON decision_samples(simulated, bucket, action);
CREATE INDEX IF NOT EXISTS idx_obs_match ON observations(match_id);
CREATE INDEX IF NOT EXISTS idx_llm_key ON llm_usage(cache_key);
"""


class Database:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # ---- writes -------------------------------------------------------------------------------
    def start_match(self, source: str, simulated: bool, player: str, team: int, caps: list[str],
                    recording: str | None) -> int:
        cur = self.conn.execute(
            "INSERT INTO matches(started_at, source, simulated, player_name, team, recording_path, capabilities)"
            " VALUES (?,?,?,?,?,?,?)", (time.time(), source, int(simulated), player, team, recording, json.dumps(caps)))
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_match(self, match_id: int, score_for: int, score_against: int, duration: float,
                     complete: bool) -> None:
        self.conn.execute(
            "UPDATE matches SET ended_at=?, score_for=?, score_against=?, duration_s=?, complete=? WHERE id=?",
            (time.time(), score_for, score_against, duration, int(complete), match_id))
        self.conn.commit()

    def add_observations(self, match_id: int, obs: list[Observation], shown_ids: set[int]) -> None:
        self.conn.executemany(
            "INSERT INTO observations(match_id, game_time, rule_id, category, kind, tip, explanation, impact,"
            " confidence, shown, evidence) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [(match_id, o.time, o.rule_id, o.category, o.kind, o.tip, o.explanation, o.impact, o.confidence,
              int(id(o) in shown_ids), json.dumps(o.evidence, default=str)) for o in obs])
        self.conn.commit()

    def add_events(self, match_id: int, events: list[tuple[float, str, dict]]) -> None:
        self.conn.executemany("INSERT INTO events(match_id, game_time, type, data) VALUES (?,?,?,?)",
                              [(match_id, t, typ, json.dumps(d, default=str)) for t, typ, d in events])
        self.conn.commit()

    def set_metrics(self, match_id: int, metrics: dict[str, float | None]) -> None:
        self.conn.executemany("INSERT OR REPLACE INTO metrics(match_id, name, value) VALUES (?,?,?)",
                              [(match_id, k, v) for k, v in metrics.items() if v is not None])
        self.conn.commit()

    def save_report(self, match_id: int, report: dict, markdown: str, html_path: str | None) -> None:
        self.conn.execute("INSERT OR REPLACE INTO reports(match_id, json, markdown, html_path) VALUES (?,?,?,?)",
                          (match_id, json.dumps(report, default=str), markdown, html_path))
        self.conn.commit()

    def save_llm_summary(self, match_id: int, text: str) -> None:
        self.conn.execute("UPDATE reports SET llm_summary=? WHERE match_id=?", (text, match_id))
        self.conn.commit()

    def add_recommendations(self, match_id: int, recs: list[dict]) -> None:
        self.conn.executemany(
            "INSERT INTO recommendations(match_id, skill, pack_code, pack_name, reason) VALUES (?,?,?,?,?)",
            [(match_id, r["skill"], r.get("code"), r.get("name"), r.get("why")) for r in recs])
        self.conn.commit()

    def add_decisions(self, match_id: int | None, simulated: bool, samples: list[dict]) -> None:
        self.conn.executemany(
            "INSERT INTO decision_samples(match_id, simulated, actor, bucket, action, value, game_time)"
            " VALUES (?,?,?,?,?,?,?)",
            [(match_id, int(simulated), s["actor"], s["bucket"], s["action"], s["value"], s.get("t")) for s in samples])
        self.conn.commit()

    def add_kickoffs(self, match_id: int | None, simulated: bool, samples: list[dict]) -> None:
        self.conn.executemany(
            "INSERT INTO kickoffs(match_id, simulated, spawn, kind, role, my_arrival, opp_arrival, result, side)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            [(match_id, int(simulated), k["spawn"], k["kind"], k["role"], k["my_arrival"], k["opp_arrival"],
              k["result"], k["side"]) for k in samples])
        self.conn.commit()

    def decision_aggregates(self, simulated: bool) -> list[tuple[str, str, int, float, int]]:
        return [tuple(r) for r in self.conn.execute(
            "SELECT bucket, action, COUNT(*), SUM(value), SUM(value >= 0.3) FROM decision_samples"
            " WHERE simulated=? GROUP BY bucket, action", (int(simulated),))]

    def kickoff_history(self, simulated: bool) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT spawn, kind, role, COUNT(*) n, SUM(result='won') won, SUM(result='lost') lost,"
            " AVG(my_arrival) my_arrival, AVG(opp_arrival) opp_arrival FROM kickoffs WHERE simulated=?"
            " GROUP BY spawn, role ORDER BY n DESC", (int(simulated),))]

    # ---- reads --------------------------------------------------------------------------------
    def recent_metrics(self, n: int, before_match: int | None = None, simulated: bool | None = None,
                       ) -> list[dict[str, float]]:
        q = "SELECT id FROM matches WHERE complete=1"
        args: list[Any] = []
        if before_match is not None:
            q += " AND id < ?"
            args.append(before_match)
        if simulated is not None:
            q += " AND simulated = ?"
            args.append(int(simulated))
        q += " ORDER BY id DESC LIMIT ?"
        args.append(n)
        ids = [r["id"] for r in self.conn.execute(q, args)]
        out = []
        for mid in ids:
            out.append({r["name"]: r["value"] for r in
                        self.conn.execute("SELECT name, value FROM metrics WHERE match_id=?", (mid,))})
        return out

    def match(self, match_id: int) -> dict | None:
        r = self.conn.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()
        return dict(r) if r else None

    def matches(self, limit: int = 50) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM matches ORDER BY id DESC LIMIT ?", (limit,))]

    def report(self, match_id: int) -> dict | None:
        r = self.conn.execute("SELECT * FROM reports WHERE match_id=?", (match_id,)).fetchone()
        return dict(r) if r else None

    def llm_cached(self, key: str) -> str | None:
        r = self.conn.execute("SELECT response FROM llm_usage WHERE cache_key=? AND response IS NOT NULL "
                              "ORDER BY id DESC LIMIT 1", (key,)).fetchone()
        return r["response"] if r else None

    def llm_record(self, provider: str, model: str, tin: int, tout: int, cost: float, key: str,
                   response: str | None) -> None:
        self.conn.execute("INSERT INTO llm_usage(at, provider, model, input_tokens, output_tokens, cost_usd,"
                          " cache_key, response) VALUES (?,?,?,?,?,?,?,?)",
                          (time.time(), provider, model, tin, tout, cost, key, response))
        self.conn.commit()

    def llm_spend_since(self, since: float) -> tuple[float, int]:
        r = self.conn.execute("SELECT COALESCE(SUM(cost_usd),0) c, COUNT(*) n FROM llm_usage WHERE at>=?",
                              (since,)).fetchone()
        return float(r["c"]), int(r["n"])

    # ---- privacy controls ---------------------------------------------------------------------
    def export_all(self, out_path: Path) -> Path:
        data = {}
        for table in ("matches", "observations", "events", "metrics", "reports", "recommendations", "llm_usage"):
            data[table] = [dict(r) for r in self.conn.execute(f"SELECT * FROM {table}")]  # noqa: S608
        out_path.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
        return out_path

    def delete_match(self, match_id: int) -> str | None:
        m = self.match(match_id)
        self.conn.execute("DELETE FROM matches WHERE id=?", (match_id,))
        self.conn.commit()
        return m["recording_path"] if m else None

    def delete_all(self) -> list[str]:
        paths = [r["recording_path"] for r in self.conn.execute("SELECT recording_path FROM matches")
                 if r["recording_path"]]
        for table in ("observations", "events", "metrics", "reports", "recommendations", "llm_usage",
                      "decision_samples", "kickoffs", "matches"):
            self.conn.execute(f"DELETE FROM {table}")  # noqa: S608
        self.conn.commit()
        self.conn.execute("VACUUM")
        return paths
