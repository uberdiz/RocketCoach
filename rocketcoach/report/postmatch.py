"""Post-match report: summary, mistakes with evidence, strengths, grades, boost heatmap, history, plan."""

from __future__ import annotations

import html
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from rocketcoach.analysis.events import Event
from rocketcoach.analysis.observations import Observation
from rocketcoach.report.markdown import inline as md_inline
from rocketcoach.report.markdown import to_html as md_to_html
from rocketcoach.training.recommender import build_plan, find_weaknesses

MIN_ACTIVE_FOR_GRADES = 120.0  # seconds of live play before positioning/boost grades are given
COMPARE_KEYS = [
    ("pct_below_20", "Time under 20 boost (%)", "lower"),
    ("overcommits_per5", "Last-back over-commits / 5 min", "lower"),
    ("double_commits_per5", "Double commits / 5 min", "lower"),
    ("exposed_pct", "Time with net exposed (%)", "lower"),
    ("in_position_pct", "Time at the recommended spot (%)", "higher"),
    ("aerial_contact_rate", "Aerial contact rate", "higher"),
    ("avg_recovery_s", "Avg recovery time (s)", "lower"),
    ("shots_on_target", "Shots on target (est.)", "higher"),
    ("goals_against", "Goals conceded", "lower"),
    ("avg_speed", "Average speed (uu/s)", "higher"),
    ("rank_est_tier", "Match-level estimate (tier)", "higher"),
]


@dataclass
class MatchData:
    match_id: int
    simulated: bool
    source: str
    player: str
    team: int
    scores: tuple[int, int]
    start_time: float
    end_time: float
    complete: bool
    capabilities: list[str]
    observations: list[Observation]
    shown_ids: set[int]
    events: list[Event]
    boost: dict
    mech: dict
    tactical: dict
    telemetry_gaps: int = 0
    suppressed: dict[str, int] = field(default_factory=dict)
    decisions: dict = field(default_factory=dict)  # decisions.summarize() + learned insights
    kickoffs: list[dict] = field(default_factory=list)  # this match's kickoffs
    kickoff_history: list[dict] = field(default_factory=list)  # per spawn, all matches
    rank: dict = field(default_factory=dict)  # rank.estimate()
    level: str = "high"


def clock(t: float, start: float) -> str:
    s = max(0, int(t - start))
    return f"{s // 60}:{s % 60:02d}"


def flat_metrics(d: MatchData) -> dict[str, float | None]:
    tac, b, m = d.tactical, d.boost, d.mech
    active = max(tac.get("active_s", 0.0), 1.0)
    per5 = 300.0 / active
    counts = defaultdict(int)
    for o in d.observations:
        counts[o.rule_id] += 1
    my_goals = d.scores[d.team]
    return {
        "active_s": round(active, 1),
        "goals_for": my_goals, "goals_against": d.scores[1 - d.team],
        "pct_below_20": b["pct_below"]["20"], "pct_below_40": b["pct_below"]["40"], "pct_below_60": b["pct_below"]["60"],
        "boost_collected": b["collected"], "boost_spent": b["spent"], "spent_supersonic": b["spent_supersonic"],
        "big_pads": b["big_pads"], "small_pads": b["small_pads"], "small_pad_misses": b["small_pad_misses"],
        "starved_out_of_play_s": b["starved_out_of_play_s"],
        "overcommits_per5": round(counts["last_man_overcommit"] * per5, 2),
        "double_commits_per5": round(counts["double_commit"] * per5, 2),
        "exposed_pct": round(100 * tac.get("exposed_s", 0) / active, 1),
        "goal_side_pct": round(100 * tac.get("goal_side_s", 0) / active, 1),
        "in_position_pct": round(100 * tac.get("guide_on_spot_s", 0) / max(tac.get("guide_total_s", 0), 1.0), 1),
        "shadow_s": round(tac.get("shadow_s", 0), 1),
        "threat_seconds": round(tac.get("exposed_s", 0), 1),
        "aerial_attempts": m["aerial_attempts"], "aerial_contacts": m["aerial_contacts"],
        "aerial_double_jump_takeoffs": m["aerial_double_jump_takeoffs"],
        "aerial_contact_rate": round(m["aerial_contacts"] / m["aerial_attempts"], 2) if m["aerial_attempts"] else None,
        "recoveries": m["recoveries"], "avg_recovery_s": m["avg_recovery_s"],
        "shots_on_target": m["shots_on_target"], "attacking_touches_off_target": m["attacking_touches_off_target"],
        "kickoffs_won": m["kickoffs_won"], "kickoffs_lost": m["kickoffs_lost"],
        "challenges_won": m["challenges_won"], "challenges_lost": m["challenges_lost"],
        "touches": m["touches"],
        "avg_speed": round(tac["speed_sum"] / active) if tac.get("speed_sum") else None,
        "supersonic_pct": round(100 * tac.get("supersonic_s", 0) / active, 1) if tac.get("speed_sum") else None,
        "boost_per_min": round(b["collected"] / (active / 60)) if b else None,
        "rank_est_tier": (d.rank or {}).get("this_match_tier"),
    }


def _letter(score: float) -> str:
    return "A" if score >= 85 else "B" if score >= 70 else "C" if score >= 55 else "D" if score >= 40 else "F"


def grades(fm: dict[str, Any]) -> list[dict]:
    out = []
    active = fm["active_s"]
    enough = active >= MIN_ACTIVE_FOR_GRADES

    def grade(name: str, score: float | None, criteria: str, samples: str, ok: bool, low: bool = False) -> None:
        if not ok or score is None:
            out.append({"area": name, "grade": "n/a", "criteria": criteria, "samples": samples,
                        "confidence": "insufficient data"})
        else:
            score = max(0.0, min(100.0, score))
            out.append({"area": name, "grade": _letter(score), "score": round(score), "criteria": criteria,
                        "samples": samples, "confidence": "low" if low else "normal"})

    grade("Positioning & defense",
          100 - 10 * fm["overcommits_per5"] - 1.2 * fm["exposed_pct"] - max(0, 55 - fm["goal_side_pct"]) * 0.5,
          "Start 100; -10 per last-back over-commit per 5 min; -1.2 per % of time with the net exposed; "
          "-0.5 per % goal-side time below 55%.", f"{active:.0f}s of play", enough, active < 240)
    grade("Rotation & decisions",
          100 - 12 * fm["double_commits_per5"] - 0.4 * fm["exposed_pct"],
          "Start 100; -12 per double commit per 5 min; -0.4 per % of time nobody covered the net.",
          f"{active:.0f}s of play", enough, active < 240)
    grade("Boost management",
          100 - max(0, fm["pct_below_20"] - 25) * 1.2 - fm["spent_supersonic"] / 5 - fm["starved_out_of_play_s"] / 3,
          "Start 100; -1.2 per % of time under 20 boost beyond 25%; -1 per 5 boost burned while supersonic; "
          "-1 per 3s starved and out of the play.", f"{active:.0f}s of play", enough, active < 240)
    att = fm["aerial_attempts"]
    grade("Aerials", None if not att else 30 + 70 * fm["aerial_contacts"] / att,
          "30 + 70 x contact rate (contact = touch while airborne after a takeoff toward a ball above 250uu).",
          f"{att} attempts", att >= 5, att < 10)
    rec = fm["recoveries"]
    grade("Recoveries", None if not rec else 100 - max(0, (fm["avg_recovery_s"] or 0) - 0.8) * 60,
          "100 - 60 per second of average time-to-1200uu/s after landing beyond 0.8s.", f"{rec} landings", rec >= 5,
          rec < 10)
    k = fm["kickoffs_won"] + fm["kickoffs_lost"]
    grade("Kickoffs (estimate)", None if not k else 100 * fm["kickoffs_won"] / k,
          "Share of decided kickoffs where the ball ended in the opponents' half after 2s.", f"{k} decided", k >= 4,
          k < 8)
    return out


def _top_mistakes(d: MatchData, n: int = 3) -> list[dict]:
    groups: dict[str, list[Observation]] = defaultdict(list)
    for o in d.observations:
        if o.kind == "mistake":
            groups[o.rule_id].append(o)
    ranked = sorted(groups.values(), key=lambda g: -sum(o.priority for o in g))
    out = []
    for g in ranked[:n]:
        worst = max(g, key=lambda o: o.priority)
        out.append({
            "rule": worst.rule_id, "category": worst.category, "tip": worst.tip, "count": len(g),
            "explanation": worst.explanation, "avg_confidence": round(sum(o.confidence for o in g) / len(g), 2),
            "timestamps": [clock(o.time, d.start_time) for o in g[:8]],
            "next_time": worst.next_time,
            "evidence": worst.evidence, "low_confidence": sum(o.confidence for o in g) / len(g) < 0.6 or len(g) < 2,
        })
    return out


def _strengths(d: MatchData, fm: dict) -> list[dict]:
    s: list[tuple[float, str, str]] = []
    shadows = [o for o in d.observations if o.rule_id == "good_shadow"]
    if shadows:
        s.append((0.6 + 0.05 * len(shadows), "Patient shadow defense",
                  f"{len(shadows)} controlled retreats; {fm['shadow_s']:.0f}s shadowing goal-side."))
    if fm["goal_side_pct"] >= 60:
        s.append((0.55, "Goal-side discipline", f"Goal-side of the ball {fm['goal_side_pct']:.0f}% of the time."))
    if fm["aerial_attempts"] >= 4 and (fm["aerial_contact_rate"] or 0) >= 0.6:
        s.append((0.5, "Aerial contact", f"{fm['aerial_contacts']}/{fm['aerial_attempts']} aerials connected."))
    k = fm["kickoffs_won"] + fm["kickoffs_lost"]
    if k >= 3 and fm["kickoffs_won"] > fm["kickoffs_lost"]:
        s.append((0.45, "Kickoffs (estimate)", f"Won {fm['kickoffs_won']} of {k} decided kickoffs."))
    if fm["spent_supersonic"] < 15 and fm["active_s"] > 120:
        s.append((0.4, "Efficient boost use", "Almost no boost wasted at supersonic speed."))
    if fm["pct_below_20"] < 25 and fm["active_s"] > 120:
        s.append((0.4, "Healthy boost levels", f"Under 20 boost only {fm['pct_below_20']:.0f}% of the time."))
    c = fm["challenges_won"] + fm["challenges_lost"]
    if c >= 4 and fm["challenges_won"] > fm["challenges_lost"]:
        s.append((0.45, "50/50s (estimate)", f"Won {fm['challenges_won']} of {c} decided challenges."))
    if fm["recoveries"] >= 5 and (fm["avg_recovery_s"] or 9) < 1.0:
        s.append((0.4, "Fast recoveries", f"Average {fm['avg_recovery_s']}s back to speed after landing."))
    s.sort(key=lambda x: -x[0])
    return [{"title": t, "evidence": e} for _, t, e in s[:3]]


def _play_reviews(d: MatchData) -> list[dict]:
    """Every moment with concrete 'next time' advice: lost/risky 50-50s, goals against, turnovers."""
    out = []
    for o in d.observations:
        if o.next_time and (o.kind == "review" or o.category == "challenge"):
            out.append({"clock": clock(o.time, d.start_time), "what": o.tip, "why": o.explanation,
                        "next_time": o.next_time, "shown": id(o) in d.shown_ids})
    return out[:30]


def _timeline(d: MatchData) -> list[dict]:
    items = []
    for e in d.events:
        if e.type == "goal":
            items.append((e.time, "goal", "Goal for your team" if e.data["for_me"] else "Goal against"))
        elif e.type == "kickoff_result":
            items.append((e.time, "kickoff", f"Kickoff {e.data['result']} (estimate)"))
        elif e.type == "demolished":
            items.append((e.time, "demo", f"Car {e.data['car']} demolished"))
        elif e.type == "telemetry_gap":
            items.append((e.time, "data", f"Telemetry gap {e.data['seconds']}s"))
    for o in d.observations:
        if o.kind == "mistake" and o.priority >= 0.35 or id(o) in d.shown_ids:
            items.append((o.time, o.category, f"{o.tip}{' (shown live)' if id(o) in d.shown_ids else ''}"))
    items.sort(key=lambda x: x[0])
    return [{"clock": clock(t, d.start_time), "type": typ, "text": txt} for t, typ, txt in items[:60]]


def _compare(fm: dict, prev: list[dict]) -> dict:
    out = {}
    for n in (5, 20):
        window = prev[:n]
        if not window:
            out[str(n)] = None
            continue
        rows = []
        for key, label, better in COMPARE_KEYS:
            vals = [w[key] for w in window if w.get(key) is not None]
            cur = fm.get(key)
            if not vals or cur is None:
                continue
            avg = sum(vals) / len(vals)
            trend = "same"
            if abs(cur - avg) > max(0.05 * abs(avg), 1e-6):
                improved = cur < avg if better == "lower" else cur > avg
                trend = "better" if improved else "worse"
            rows.append({"metric": label, "now": round(cur, 2), "avg": round(avg, 2), "trend": trend,
                         "matches": len(vals)})
        out[str(n)] = rows
    return out


def build_report(d: MatchData, previous_metrics: list[dict], player_rank: str = "Diamond") -> dict:
    fm = flat_metrics(d)
    counts: dict[str, int] = defaultdict(int)
    for o in d.observations:
        counts[o.rule_id] += 1
    weak = find_weaknesses(fm, counts)
    plan = build_plan(weak, player_rank)
    gf, ga = d.scores[d.team], d.scores[1 - d.team]
    result = "won" if gf > ga else "lost" if ga > gf else "drew"
    mistakes = _top_mistakes(d)
    est = d.rank.get("name") if d.rank.get("available") else None
    summary = (f"You {result} {gf}-{ga}. " + (f"Estimated level: {est} (range {d.rank['range'][0]} to "
                                               f"{d.rank['range'][1]}). " if est else "")
               + f"You were at the coach's recommended spot {fm['in_position_pct']:.0f}% of the time. "
               + (f"Biggest issue: {mistakes[0]['tip'].lower()} ({mistakes[0]['count']}x). " if mistakes else "")
               + f"Boost under 20 for {fm['pct_below_20']:.0f}% of play; goal-side {fm['goal_side_pct']:.0f}% of the time.")
    return {
        "match_id": d.match_id,
        "data_source": {"source": d.source, "simulated": d.simulated, "complete": d.complete,
                        "capabilities": d.capabilities, "telemetry_gaps": d.telemetry_gaps,
                        "warning": "SIMULATED DATA: not from a real Rocket League match." if d.simulated else None},
        "summary": summary,
        "result": {"for": gf, "against": ga, "result": result, "duration": clock(d.end_time, d.start_time)},
        "mistakes": mistakes,
        "strengths": _strengths(d, fm),
        "grades": grades(fm),
        "boost": {k: v for k, v in d.boost.items() if k not in ("heat_time", "pickups", "timeline")},
        "boost_visual": {"heat_time": d.boost["heat_time"], "pickups": d.boost["pickups"],
                         "timeline": d.boost["timeline"], "team": d.team},
        "mechanics": d.mech,
        "timeline": _timeline(d),
        "play_reviews": _play_reviews(d),
        "comparison": _compare(fm, previous_metrics),
        "weaknesses": [{"skill": w.skill, "score": round(w.score, 2), "evidence": w.evidence} for w in weak],
        "training_plan": plan,
        "rank_estimate": d.rank,
        "kickoffs": {"this_match": d.kickoffs, "history": d.kickoff_history},
        "decisions": d.decisions,
        "coaching_level": d.level,
        "metrics": fm,
        "live_coaching": {"tips_shown": len(d.shown_ids), "observations": len(d.observations),
                          "suppressed": d.suppressed},
    }


# ---- rendering --------------------------------------------------------------------------------
def to_markdown(r: dict) -> str:
    L = [f"# Match {r['match_id']} report", ""]
    if r["data_source"]["warning"]:
        L += [f"> **{r['data_source']['warning']}**", ""]
    L += [r["summary"], ""]
    L += ["## Highest-impact mistakes"]
    for i, m in enumerate(r["mistakes"], 1):
        lc = " *(low confidence: needs more samples)*" if m["low_confidence"] else ""
        ev = ", ".join(f"{k}={v}" for k, v in list((m["evidence"] or {}).items())[:5]
                       if k != "time" and not isinstance(v, (list, dict)))
        L += [f"{i}. **{m['tip']}** ({m['category']}, {m['count']}x, confidence {m['avg_confidence']}){lc}",
              f"   {m['explanation']}"]
        if m.get("next_time"):
            L.append(f"   **Next time:** {m['next_time']}")
        L.append(f"   At: {', '.join(m['timestamps'])}" + (f". Evidence: `{ev}`" if ev else ""))
    if not r["mistakes"]:
        L.append("None detected with enough confidence.")
    L += ["", "## Strengths"]
    L += [f"- **{s['title']}**: {s['evidence']}" for s in r["strengths"]] or ["- Not enough evidence yet."]
    L += ["", "## Grades", "| Area | Grade | Samples | Confidence | Criteria |", "|---|---|---|---|---|"]
    L += [f"| {g['area']} | {g['grade']} | {g['samples']} | {g['confidence']} | {g['criteria']} |" for g in r["grades"]]
    b = r["boost"]
    L += ["", "## Boost", f"- Under 20 / 40 / 60: {b['pct_below']['20']}% / {b['pct_below']['40']}% / {b['pct_below']['60']}%",
          f"- Collected {b['collected']}, spent {b['spent']} (supersonic waste {b['spent_supersonic']}, "
          f"in air {b['spent_air']}, while defending {b['spent_defending']})",
          f"- Pads: {b['big_pads']} big, {b['small_pads']} small; {b['small_pad_misses']} small pads passed close by "
          f"while under 50",
          f"- Corner-100 trips: {len(b['full_trips'])} "
          f"({sum(1 for t in b['full_trips'] if t['last_man'])} while last back)",
          f"- Low boost but well positioned: {b['low_boost_ok_s']}s (fine); starved and out of play: "
          f"{b['starved_out_of_play_s']}s (wasteful)",
          f"- Average boost entering 50/50s: {b['avg_challenge_boost']} ({b['challenges_low_boost']} under 20)"]
    m = r["mechanics"]
    L += ["", "## Mechanics (measured unless marked estimate)",
          f"- Touches {m['touches']}; shots on target (estimate) {m['shots_on_target']}, avg ball speed "
          f"{m['avg_shot_speed']}; corner placements {m['corner_placements']}",
          f"- Aerials: {m['aerial_contacts']}/{m['aerial_attempts']} contact; double-jump takeoffs "
          f"{m['aerial_double_jump_takeoffs']}; takeoff distance hit {m['avg_takeoff_dist_hit']} vs miss "
          f"{m['avg_takeoff_dist_miss']}",
          f"- Recoveries: {m['recoveries']} landings, avg {m['avg_recovery_s']}s to 1200uu/s",
          f"- Kickoffs (estimate): won {m['kickoffs_won']}, lost {m['kickoffs_lost']} of {m['kickoffs']}",
          f"- 50/50s (estimate): won {m['challenges_won']}, lost {m['challenges_lost']} of {m['challenges']}",
          f"- Dribbles {m['dribbles']} (flicks {m['flicks']}); wall touches {m['wall_touches']}; "
          f"backboard touches {m['backboard_touches']}",
          "- Not measured: " + "; ".join(f"{k} ({v})" for k, v in m["not_measured"].items())]
    L += ["", "## Compared with previous matches"]
    for n in ("5", "20"):
        rows = r["comparison"].get(n)
        if not rows:
            L.append(f"- Last {n}: no previous matches yet.")
            continue
        L += [f"**Last {n}** ({rows[0]['matches']} matches)", "| Metric | This match | Average | Trend |", "|---|---|---|---|"]
        L += [f"| {x['metric']} | {x['now']} | {x['avg']} | {x['trend']} |" for x in rows]
    p = r["training_plan"]
    L += ["", "## Next session training plan", f"*{p['structure']}*", "",
          f"1. **{p['warmup']['name']}**: {p['warmup']['drill']}"]
    for i, f in enumerate(p["focus"], 2):
        line = f"{i}. **{f['skill'].replace('_', ' ').title()}**: because {f['why']}."
        if f.get("code"):
            ver = "verified" if f["verified"] else "UNVERIFIED (run `rocketcoach verify-packs`)"
            line += (f"\n   Pack: **{f['name']}** `{f['code']}` by {f['creator']} ({f['difficulty']}), {ver}, "
                     f"source {f['source_url']}\n   Success: {f['success']} {f['progress_when']}"
                     f"\n   If the code fails: {f['search_alternative']}")
            if f.get("next_pack"):
                line += f"\n   Next: {f['next_pack']['name']} `{f['next_pack']['code']}` ({f['next_pack']['difficulty']})"
        if f.get("freeplay"):
            fp = f["freeplay"]
            line += f"\n   Drill: **{fp['name']}**: {fp['drill']} Success: {fp['success']}"
        L.append(line)
    if p["note"]:
        L.append(p["note"])
    re_ = r.get("rank_estimate") or {}
    L += ["", "## Rank estimate"]
    if re_.get("available"):
        L += [f"**{re_['name']}** (likely {re_['range'][0]} to {re_['range'][1]}, {re_['confidence']} confidence). "
              f"{re_['note']}", ""]
        if re_.get("this_match_name"):
            L.append(f"- This match alone looks like **{re_['this_match_name']}**")
        L += [f"- {k}: {v['value']} → looks like {v['looks_like']}" for k, v in re_["components"].items()]
        L += ["- Blend: " + ", ".join(f"{b['source']} ({b['rank']}, {int(b['weight'] * 100)}%)" for b in re_["blend"])]
    else:
        L.append("- Not enough data for an estimate yet.")
    ko = r.get("kickoffs") or {}
    L += ["", "## Kickoffs"]
    if ko.get("this_match"):
        L += ["| Spawn | Your job | You | Them | Result |", "|---|---|---|---|---|"]
        L += [f"| {k['spawn']} | {k['role']} | {k['my_arrival'] if k['my_arrival'] is not None else '-'}s | "
              f"{k['opp_arrival'] if k['opp_arrival'] is not None else '-'}s | {k['result']} {k['side']} |"
              for k in ko["this_match"]]
    else:
        L.append("- No kickoffs recorded.")
    if ko.get("history"):
        L += ["", "**All matches, by spawn** (arrival = seconds from GO to contact; compare yours with theirs)",
              "| Spawn | Job | Kickoffs | Won | Lost | Your avg arrival | Their avg |",
              "|---|---|---|---|---|---|---|"]
        L += [f"| {h['spawn']} | {h['role']} | {h['n']} | {h['won']} | {h['lost']} | "
              f"{round(h['my_arrival'], 2) if h['my_arrival'] else '-'} | "
              f"{round(h['opp_arrival'], 2) if h['opp_arrival'] else '-'} |" for h in ko["history"]]
    dec = r.get("decisions") or {}
    L += ["", "## Decisions with the ball"]
    if dec.get("by_action"):
        L.append(f"Learned from {dec['model_samples']} touches by you, your teammates and opponents "
                 f"({dec['samples_this_match']} this match). Value: +1 goal, +0.5 shot, -0.6 conceded shot, "
                 "-1 conceded goal (next 4 s).")
        L += ["| What you did | Times | Avg outcome |", "|---|---|---|"]
        L += [f"| {a} | {v['n']} | {v['avg_value']:+.2f} |" for a, v in sorted(dec["by_action"].items(),
                                                                             key=lambda kv: -kv[1]["n"])]
    else:
        L.append("- No classified touches yet.")
    L += ["", "## Play reviews: what to do differently"]
    L += [f"- `{x['clock']}` **{x['what']}**: {x['why']} **Next time:** {x['next_time']}"
          for x in r.get("play_reviews", [])] or ["- No reviewed plays (no lost 50/50s, goals against or turnovers)."]
    L += ["", "## Timeline"] + [f"- `{t['clock']}` [{t['type']}] {t['text']}" for t in r["timeline"]]
    ds = r["data_source"]
    L += ["", "## Data", f"Source: {ds['source']} (simulated={ds['simulated']}, complete={ds['complete']}, "
          f"gaps={ds['telemetry_gaps']}). Capabilities: {', '.join(ds['capabilities'])}.",
          f"Live tips shown: {r['live_coaching']['tips_shown']} of {r['live_coaching']['observations']} observations; "
          f"suppressed: {r['live_coaching']['suppressed']}"]
    return re.sub(r"\bNone\b(?! detected)", "n/a", "\n".join(L)) + "\n"


def _heatmap_svg(v: dict) -> str:
    heat = v["heat_time"]
    rows, cols = len(heat), len(heat[0])
    mx = max(max(r) for r in heat) or 1.0
    W, H = 240, 300
    cw, ch = W / cols, H / rows
    flip = v["team"] == 1  # always draw your goal at the bottom
    parts = [f'<svg viewBox="-10 -14 {W + 20} {H + 28}" class="heat" role="img" aria-label="Boost heatmap">']
    for r in range(rows):
        for c in range(cols):
            val = heat[r][c] / mx
            rr = r if flip else rows - 1 - r
            cc = cols - 1 - c if flip else c
            parts.append(f'<rect x="{cc * cw:.1f}" y="{rr * ch:.1f}" width="{cw:.1f}" height="{ch:.1f}" '
                         f'fill="var(--heat)" fill-opacity="{0.05 + 0.9 * val:.2f}"/>')
    for x, y, big in v["pickups"]:
        px = (x + 4096) / 8192 * W
        py = (y + 5120) / 10240 * H
        if flip:
            px = W - px
        else:
            py = H - py
        parts.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="{4 if big else 2.2}" '
                     f'fill="{"var(--big)" if big else "var(--small)"}"/>')
    parts.append(f'<rect x="0" y="0" width="{W}" height="{H}" fill="none" stroke="var(--line)"/>')
    parts.append(f'<rect x="{W / 2 - 22}" y="{H}" width="44" height="6" fill="var(--line)"/>')
    parts.append(f'<text x="{W / 2}" y="{H + 13}" text-anchor="middle" class="lbl">your goal</text>')
    parts.append(f'<text x="{W / 2}" y="-4" text-anchor="middle" class="lbl">opponent goal</text></svg>')
    return "".join(parts)


def _timeline_svg(tl: list) -> str:
    if len(tl) < 2:
        return "<p>No boost timeline.</p>"
    W, H = 640, 120
    t0, t1 = tl[0][0], tl[-1][0]
    span = max(t1 - t0, 1)
    pts = " ".join(f"{(t - t0) / span * W:.1f},{H - b / 100 * H:.1f}" for t, b in tl)
    y20 = H - 0.2 * H
    return (f'<svg viewBox="0 -4 {W} {H + 8}" class="tl" role="img" aria-label="Boost over time">'
            f'<line x1="0" x2="{W}" y1="{y20}" y2="{y20}" stroke="var(--warn)" stroke-dasharray="4 4"/>'
            f'<polyline points="{pts}" fill="none" stroke="var(--accent)" stroke-width="1.5"/></svg>')


def to_html(r: dict, markdown: str) -> str:
    """Formatted HTML page: each '## ' section of the Markdown report becomes a card; the boost card also
    gets the heatmap and timeline charts."""
    esc = html.escape
    sections = re.split(r"(?m)^## ", markdown)
    cards = []
    for sec in sections[1:]:
        title, _, body = sec.partition("\n")
        extra = ""
        if title.startswith("Boost"):
            extra = (f'<div class="row"><div>{_heatmap_svg(r["boost_visual"])}<p class="muted">Shading = time '
                     'spent; orange = 100 pads, yellow = small pads.</p></div><div style="flex:1;min-width:260px">'
                     f'{_timeline_svg(r["boost_visual"]["timeline"])}<p class="muted">Boost over the match; dashed '
                     'line = 20.</p></div></div>')
        cards.append(f'<section class="card"><h2>{md_inline(title.strip())}</h2>{extra}{md_to_html(body)}</section>')
    warn = f"<p class='warn'>{esc(r['data_source']['warning'])}</p>" if r["data_source"]["warning"] else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Match {r['match_id']} report</title>
<style>
:root {{ --bg:#f7f8fa; --fg:#14181c; --muted:#5b6570; --card:#fff; --line:#d5dbe1; --accent:#0277bd;
  --heat:#0277bd; --big:#e65100; --small:#f9a825; --warn:#c62828; --code:#eef1f4; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#0f1316; --fg:#e8edf1; --muted:#97a3ad; --card:#181e23;
  --line:#33404a; --accent:#4fc3f7; --heat:#4fc3f7; --big:#ff8a50; --small:#ffd54f; --warn:#ef5350;
  --code:#222a31; }} }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.55 "Segoe UI",system-ui,sans-serif; margin:0;
  padding:16px; }}
main {{ max-width:960px; margin:0 auto; }}
h1 {{ margin:.2em 0 .1em; }} h2 {{ margin:0 0 .5em; font-size:1.2em; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 18px;
  margin:14px 0; }}
.muted {{ color:var(--muted); }} .warn {{ color:var(--warn); font-weight:600; }}
.summary {{ font-size:1.05em; }}
.tablewrap {{ overflow-x:auto; }} table {{ border-collapse:collapse; width:100%; font-size:.92em; }}
td,th {{ border-bottom:1px solid var(--line); padding:6px 8px; text-align:left; vertical-align:top; }}
th {{ color:var(--muted); font-weight:600; }}
li {{ margin:.35em 0; }} code {{ background:var(--code); padding:1px 5px; border-radius:4px; font-size:.88em; }}
blockquote {{ margin:0; padding:6px 12px; border-left:3px solid var(--warn); }}
.row {{ display:flex; gap:18px; flex-wrap:wrap; align-items:flex-start; margin-bottom:8px; }}
.heat {{ width:240px; max-width:100%; }} .tl {{ width:100%; height:auto; }}
.lbl {{ font-size:10px; fill:var(--muted); }}
</style></head><body><main>
<h1>Match {r['match_id']}: {esc(r['result']['result'])} {r['result']['for']}-{r['result']['against']}</h1>{warn}
<p class="summary">{esc(r['summary'])}</p>
{"".join(cards)}
</main></body></html>"""
