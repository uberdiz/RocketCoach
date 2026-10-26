"""Rank tiers and skill-level groups, so coaching wording and thresholds scale from Bronze to SSL."""

from __future__ import annotations

import re

RANKS = ["Bronze", "Silver", "Gold", "Platinum", "Diamond", "Champion", "Grand Champion"]
ROMAN = {"1": 1, "2": 2, "3": 3, "i": 1, "ii": 2, "iii": 3}
SSL_TIER = 21  # tiers: Bronze I = 0 ... Grand Champion III = 20, Supersonic Legend = 21


def parse_rank(text: str) -> int | None:
    """'Diamond 1', 'diamond I', 'Plat 3', 'GC2', 'SSL', 'Champ' -> tier index (0..21). Division defaults to I."""
    t = text.strip().lower()
    if not t:
        return None
    if t in ("ssl", "supersonic legend"):
        return SSL_TIER
    aliases = {"plat": "platinum", "champ": "champion", "gc": "grand champion", "grand champ": "grand champion",
               "diam": "diamond", "dia": "diamond"}
    m = re.match(r"^([a-z ]+?)\s*(i{1,3}|[123])?$", t)
    if not m:
        return None
    name = aliases.get(m.group(1).strip(), m.group(1).strip())
    if name not in [r.lower() for r in RANKS]:
        return None
    base = [r.lower() for r in RANKS].index(name) * 3
    return base + ROMAN.get(m.group(2) or "1", 1) - 1


def rank_name(tier: float) -> str:
    t = max(0, min(SSL_TIER, int(round(tier))))
    if t == SSL_TIER:
        return "Supersonic Legend"
    return f"{RANKS[t // 3]} {['I', 'II', 'III'][t % 3]}"


def rank_family(tier: float) -> str:
    """'Diamond' etc., the granularity the training-pack difficulty uses."""
    t = max(0, min(SSL_TIER, int(round(tier))))
    return "Grand Champion" if t >= 18 else RANKS[t // 3]


def level_group(tier: float | None) -> str:
    """new (Bronze-Silver), mid (Gold-Platinum), high (Diamond-Champion), elite (GC-SSL)."""
    if tier is None:
        return "high"
    if tier < 6:
        return "new"
    if tier < 12:
        return "mid"
    if tier < 18:
        return "high"
    return "elite"


# How close to the guide's target counts as "in position": looser for newer players, tighter at the top.
ON_SPOT_BY_LEVEL = {"new": 1300.0, "mid": 1050.0, "high": 900.0, "elite": 750.0}
