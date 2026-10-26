"""Observation: the unit of coaching output produced by every rule."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CATEGORIES = ("defense", "rotation", "boost", "challenge", "kickoff", "mechanics", "offense")

# Priority order when impact is equal: preventing goals first, advanced mechanics last.
CATEGORY_WEIGHT = {
    "defense": 1.0, "rotation": 0.9, "challenge": 0.9, "boost": 0.7, "kickoff": 0.6,
    "offense": 0.55, "mechanics": 0.5,
}


@dataclass(slots=True)
class Observation:
    rule_id: str
    category: str
    time: float
    tip: str  # <= ~40 chars, imperative, shown on the overlay
    explanation: str  # one or two sentences using the actual situation
    impact: float  # 0..1 estimated effect on goals for/against
    confidence: float  # 0..1 how sure the rule is that this really happened
    kind: str = "mistake"  # "mistake" | "strength" | "info" | "review"
    evidence: dict[str, Any] = field(default_factory=dict)
    live: bool = True  # eligible for live display (some are post-match only)
    cooldown: float | None = None  # per-rule cooldown override (e.g. short for per-challenge feedback)
    next_time: str = ""  # "what to do differently next time", shown under the explanation

    @property
    def priority(self) -> float:
        return self.impact * self.confidence * CATEGORY_WEIGHT.get(self.category, 0.5)
