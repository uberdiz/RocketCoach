"""Rank estimate from how you played, your past matches and your real rank.

The per-rank benchmarks below are ROUGH approximations of typical ranked-2v2 values (average speed,
time supersonic, boost collected per minute). They are not measured from ranked data
here, and matches against bots move them (bots play slower). The estimate therefore leans on your real rank
and past matches, shows a range, and is labelled as an estimate everywhere it appears.
"""

from __future__ import annotations

from rocketcoach.analysis.levels import SSL_TIER, rank_name

# (metric value, rank tier) points; tiers: Bronze I = 0, Diamond I = 12, Grand Champion I = 18, SSL = 21
BENCHMARKS: dict[str, list[tuple[float, float]]] = {
    "avg_speed": [(1000, 0), (1150, 5), (1250, 8), (1350, 11), (1430, 13), (1500, 15), (1580, 18), (1650, 21)],
    "supersonic_pct": [(3, 0), (6, 5), (8, 8), (10, 11), (12, 13), (15, 16), (19, 19), (22, 21)],
    "boost_per_min": [(220, 0), (280, 5), (320, 8), (350, 11), (380, 13), (410, 16), (440, 19), (470, 21)],
}
WEIGHTS = {"avg_speed": 1.0, "supersonic_pct": 0.8, "boost_per_min": 0.8}
LABELS = {"avg_speed": "Average speed (uu/s)", "supersonic_pct": "Time supersonic (%)",
          "boost_per_min": "Boost collected per minute"}


def _interp(points: list[tuple[float, float]], x: float) -> float:
    descending = points[0][0] > points[-1][0]  # lower is better (kickoff time): flip so values rise with tier
    pts = sorted(((-v if descending else v), t) for v, t in points)
    x = -x if descending else x
    if x <= pts[0][0]:
        return pts[0][1]
    if x >= pts[-1][0]:
        return pts[-1][1]
    for (x0, t0), (x1, t1) in zip(pts, pts[1:]):
        if x0 <= x <= x1:
            return t0 + (t1 - t0) * (x - x0) / (x1 - x0)
    return pts[-1][1]


def match_tier(metrics: dict[str, float | None]) -> tuple[float | None, dict[str, float]]:
    parts = {k: _interp(BENCHMARKS[k], v) for k, v in metrics.items() if k in BENCHMARKS and v is not None}
    if not parts:
        return None, {}
    w = sum(WEIGHTS[k] for k in parts)
    return sum(WEIGHTS[k] * t for k, t in parts.items()) / w, parts


def estimate(metrics: dict[str, float | None], past: list[float], real_tier: int | None,
             active_s: float, vs_bots: bool = True) -> dict:
    m_tier, parts = match_tier(metrics)
    pieces: list[tuple[float, float, str]] = []
    if real_tier is not None:
        pieces.append((0.45, real_tier, "your real rank"))
    if past:
        pieces.append((min(0.3, 0.06 * len(past)), sum(past) / len(past), f"your last {len(past)} matches"))
    if m_tier is not None:
        pieces.append((0.3 if active_s >= 120 else 0.1, m_tier, "this match"))
    if not pieces:
        return {"available": False}
    w = sum(p[0] for p in pieces)
    tier = sum(p[0] * p[1] for p in pieces) / w
    spread = (max(parts.values()) - min(parts.values())) if len(parts) > 1 else 4.0
    half = 1.5 + spread / 3 + (1.0 if vs_bots else 0.0)
    confidence = "low" if len(parts) < 2 or active_s < 120 else "medium"
    return {
        "available": True,
        "tier": round(tier, 1), "name": rank_name(tier),
        "range": [rank_name(max(0, tier - half)), rank_name(min(SSL_TIER, tier + half))],
        "this_match_tier": None if m_tier is None else round(m_tier, 1),
        "this_match_name": None if m_tier is None else rank_name(m_tier),
        "components": {LABELS[k]: {"value": metrics[k], "looks_like": rank_name(t)} for k, t in parts.items()},
        "blend": [{"weight": round(p[0] / w, 2), "rank": rank_name(p[1]), "source": p[2]} for p in pieces],
        "confidence": confidence,
        "note": "Rough estimate from movement stats vs approximate rank benchmarks; matches against bots read "
                "slower than ranked play." if vs_bots else "Rough estimate from approximate rank benchmarks.",
    }
