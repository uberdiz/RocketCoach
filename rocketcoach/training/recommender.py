"""Personalised training plan from observed weaknesses.

Weaknesses come from the post-match metrics (and, when available, the trend over recent matches). A
skill is only recommended when there is enough evidence for it; the plan is capped at two focus skills
plus a warm-up, following deliberate-practice principles (one or two specific, measurable goals).
"""

from __future__ import annotations

import html
import json
import logging
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

PACKS_FILE = Path(__file__).with_name("packs.json")
SOURCE_URL = "https://prejump.com/training-packs/{code}"
RANK_ORDER = ["Bronze", "Silver", "Gold", "Platinum", "Diamond", "Champion", "Grand Champion"]

SUCCESS = {
    "shadow_defense": "Stay goal-side and make the save/clear on 7 of 10 shots in two runs in a row.",
    "saves": "Save 7 of 10 with the first touch going to a side wall or upfield (not back to the middle).",
    "backboard_defense": "Read the bounce and clear 6 of 10 without letting the ball touch the floor in front of goal.",
    "wall_play": "Clear 7 of 10 to the opposite half on the first touch.",
    "aerials": "Hit 7 of 10 with the first touch on target; then switch to the next pack.",
    "fast_aerials": "Reach the ball on 8 of 10 using jump-tilt-second-jump within 0.2s; time yourself, not only hits.",
    "redirects": "Redirect 6 of 10 on target; prioritise clean contact over power.",
    "shooting": "Score 8 of 10 with the ball going to a corner (not the keeper's middle third).",
    "dribbling": "Catch and carry 6 of 10 for at least 2 seconds without the ball touching the ground.",
    "recoveries": "Land wheels-down and be driving at speed within 1 second on 8 of 10.",
    "kickoffs": "Do 10 speed-flip kickoffs in a row reaching the ball under 2.3s (use the game clock).",
}

FREEPLAY = {
    "rotation": {
        "name": "Rotation focus (play, not a pack)",
        "drill": "Play 2 offline matches with a single rule: when your teammate goes for the ball, turn away "
                 "immediately and drive to your back post or midfield support. Count double commits afterwards.",
        "success": "Zero double commits flagged by the coach in a full match.",
    },
    "challenges": {
        "name": "50/50 drill (Free Play)",
        "drill": "In Free Play, roll the ball against a wall and meet it on the rebound with your nose square to the "
                 "ball and a small jump at contact. 20 reps, alternating sides.",
        "success": "Ball travels forward off 15 of 20 contacts.",
    },
    "boost": {
        "name": "Small-pad route laps (Free Play)",
        "drill": "Drive from your goal to midfield and back for 3 minutes using only small pads, never dropping "
                 "below 30 boost and without detouring to a corner 100.",
        "success": "Finish 3 minutes without going under 30 boost and without touching a corner pad.",
    },
    "kickoffs": {
        "name": "Kickoff reps (Free Play)",
        "drill": "Reset with the kickoff key and practise your kickoff from each spawn, 5 reps each.",
        "success": "Reach the ball first-flip on 4 of 5 from every spawn.",
    },
    "shadow_defense": {
        "name": "Shadow-defense habit (in matches)",
        "drill": "When an opponent dribbles at you and you're last back, drive backwards at their pace, stay "
                 "goal-side, and only challenge after their touch or when your teammate is behind you.",
        "success": "No 'Last back' or 'Net open' tips in a full match.",
    },
}

WARMUP = {
    "name": "Warm-up (5 min Free Play)",
    "drill": "Hit the ball around the field at speed, two powershots, two aerials, one kickoff from each spawn. "
             "No scores, just get your timing in.",
}


@dataclass
class Weakness:
    skill: str
    score: float  # severity x confidence
    evidence: str


def load_packs() -> list[dict]:
    data = json.loads(PACKS_FILE.read_text(encoding="utf-8"))
    for p in data["packs"]:
        p["source_url"] = SOURCE_URL.format(code=p["code"])
    return data["packs"]


def find_weaknesses(m: dict[str, float], obs_counts: dict[str, int]) -> list[Weakness]:
    """m: flat match metrics (see report.flat_metrics). Returns weaknesses sorted by score."""
    w: list[Weakness] = []

    def add(skill: str, score: float, ev: str) -> None:
        w.append(Weakness(skill, score, ev))

    lm = obs_counts.get("last_man_overcommit", 0) + obs_counts.get("net_exposed", 0)
    if lm >= 2:
        add("shadow_defense", 0.5 + 0.1 * lm, f"{lm} last-back over-commit / open-net situations")
    dc = obs_counts.get("double_commit", 0) + obs_counts.get("ball_side_rotation", 0)
    if dc >= 2:
        add("rotation", 0.4 + 0.08 * dc, f"{dc} double commits / ball-side rotations")
    if m.get("goals_against", 0) >= 3 and m.get("threat_seconds", 0) > 20:
        add("saves", 0.45, f"{int(m['goals_against'])} goals conceded")
    att = m.get("aerial_attempts", 0)
    if att >= 4:
        rate = m.get("aerial_contacts", 0) / att
        if rate < 0.55:
            add("aerials", 0.3 + (0.55 - rate), f"{m.get('aerial_contacts', 0):.0f}/{att:.0f} aerials made contact")
        if m.get("aerial_double_jump_takeoffs", 0) / att < 0.3:
            add("fast_aerials", 0.25, f"only {m.get('aerial_double_jump_takeoffs', 0):.0f}/{att:.0f} aerials used a "
                                      "double-jump takeoff")
    shots, off = m.get("shots_on_target", 0), m.get("attacking_touches_off_target", 0)
    if shots + off >= 5 and off / (shots + off) > 0.6:
        add("shooting", 0.3 + 0.3 * (off / (shots + off) - 0.6),
            f"{off:.0f} of {shots + off:.0f} attacking-third touches were not on target (estimate)")
    if m.get("recoveries", 0) >= 4 and (m.get("avg_recovery_s") or 0) > 1.3:
        add("recoveries", 0.3 + min(0.3, (m["avg_recovery_s"] - 1.3) * 0.3),
            f"average {m['avg_recovery_s']:.2f}s to regain speed after landing")
    kw, kl = m.get("kickoffs_won", 0), m.get("kickoffs_lost", 0)
    if kw + kl >= 3 and kl > kw:
        add("kickoffs", 0.25 + 0.05 * (kl - kw), f"kickoffs won {kw:.0f} / lost {kl:.0f} (estimate)")
    cw, cl = m.get("challenges_won", 0), m.get("challenges_lost", 0)
    if cw + cl >= 4 and cl > cw * 1.5:
        add("challenges", 0.3, f"50/50s won {cw:.0f} / lost {cl:.0f} (estimate)")
    boost_bad = obs_counts.get("corner_boost_last_man", 0) + obs_counts.get("boost_starved", 0)
    if boost_bad >= 2 or m.get("starved_out_of_play_s", 0) > 30:
        add("boost", 0.3 + 0.05 * boost_bad, f"{boost_bad} poor boost routes, "
                                             f"{m.get('starved_out_of_play_s', 0):.0f}s starved out of play")
    if m.get("spent_supersonic", 0) > 60:
        add("boost", 0.2, f"{m['spent_supersonic']:.0f} boost spent while already supersonic")
    # merge duplicates (same skill) keeping the highest score
    best: dict[str, Weakness] = {}
    for x in w:
        if x.skill not in best or x.score > best[x.skill].score:
            best[x.skill] = x
    return sorted(best.values(), key=lambda x: -x.score)


def _pick_pack(packs: list[dict], skill: str, player_rank: str, used: set[str]) -> dict | None:
    cands = [p for p in packs if p["skill"] == skill and p["code"] not in used]
    if not cands:
        return None
    target = RANK_ORDER.index(player_rank) if player_rank in RANK_ORDER else 4

    def dist(p: dict) -> tuple[int, int]:
        r = RANK_ORDER.index(p["difficulty"]) if p["difficulty"] in RANK_ORDER else target
        return (abs(r - target) + (1 if r > target else 0), -p.get("shots", 0))
    return min(cands, key=dist)


def build_plan(weak: list[Weakness], player_rank: str = "Diamond", max_focus: int = 2,
               verification: dict[str, dict] | None = None) -> dict:
    packs = load_packs()
    verification = verification if verification is not None else load_verification()
    items = []
    used: set[str] = set()
    for wk in weak[:max_focus]:
        pack = _pick_pack(packs, wk.skill, player_rank, used)
        item: dict = {"skill": wk.skill, "why": wk.evidence, "score": round(wk.score, 2)}
        if pack:
            used.add(pack["code"])
            v = verification.get(pack["code"], {})
            item.update({
                "name": pack["name"], "code": pack["code"], "creator": pack["creator"],
                "difficulty": pack["difficulty"], "source_url": pack["source_url"],
                "verified": bool(v.get("ok")), "verified_at": v.get("at"),
                "success": SUCCESS.get(wk.skill, "7 of 10 clean attempts in two consecutive runs."),
                "progress_when": "When you hit the success target in two separate sessions, move to a harder pack "
                                 "for the same skill.",
                "search_alternative": f"In game: Training > Custom Training > Search, type '{pack['name']}'.",
            })
            nxt = _pick_pack([p for p in packs if RANK_ORDER.index(p["difficulty"]) >
                              RANK_ORDER.index(pack["difficulty"])], wk.skill, "Grand Champion", used)
            if nxt:
                item["next_pack"] = {"name": nxt["name"], "code": nxt["code"], "difficulty": nxt["difficulty"]}
        if wk.skill in FREEPLAY:
            item["freeplay"] = FREEPLAY[wk.skill]
        items.append(item)
    return {
        "warmup": WARMUP,
        "focus": items,
        "session_minutes": 25 if items else 10,
        "structure": "5 min warm-up, ~8 min per focus pack (stop when the success target is reached), then play "
                     "an offline match concentrating only on the first focus skill.",
        "note": None if items else "Not enough evidence yet for a specific weakness. Play another match.",
    }


# ---- verification -----------------------------------------------------------------------------
def verification_path() -> Path:
    from rocketcoach.config import PROJECT_ROOT
    return PROJECT_ROOT / "data" / "pack_verification.json"


def load_verification() -> dict[str, dict]:
    p = verification_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            return {}
    return {}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(s)).lower()


def verify_packs(timeout: float = 15.0) -> dict[str, dict]:
    """Fetch each pack's source page and check the code and name appear. Network access required."""
    results: dict[str, dict] = {}
    for p in load_packs():
        req = urllib.request.Request(p["source_url"], headers={"User-Agent": "RocketCoach/0.1 (pack verifier)"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https host
                page = _norm(resp.read().decode("utf-8", "replace"))
            ok = p["code"].lower() in page and _norm(p["name"]) in page
            results[p["code"]] = {"ok": ok, "at": time.strftime("%Y-%m-%d"),
                                  "detail": "code and name found" if ok else "page found but name/code mismatch"}
        except urllib.error.HTTPError as e:
            results[p["code"]] = {"ok": False, "at": time.strftime("%Y-%m-%d"), "detail": f"HTTP {e.code}"}
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            results[p["code"]] = {"ok": False, "at": time.strftime("%Y-%m-%d"), "detail": f"network: {e}"}
        log.info("%s %s: %s", p["code"], p["name"], results[p["code"]]["detail"])
    path = verification_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, indent=1), encoding="utf-8")
    return results
