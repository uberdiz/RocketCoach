"""Chooses at most one live tip at a time.

Ranking = impact x confidence x category weight, boosted when a rule fires repeatedly (frequency) and
for the session focus. Suppression: minimum confidence, disabled categories, per-rule cooldown,
duplicate text, and a global minimum gap so the player can concentrate.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from rocketcoach.analysis.observations import Observation
from rocketcoach.config import CoachConfig


@dataclass(slots=True)
class LiveTip:
    obs: Observation
    shown_at: float
    until: float


class Coach:
    def __init__(self, cfg: CoachConfig) -> None:
        self.cfg = cfg
        self.history: list[Observation] = []  # everything observed (for the report)
        self.shown: list[LiveTip] = []
        self.suppressed = Counter[str]()
        self.rule_counts = Counter[str]()
        self._last_rule_time: dict[str, float] = {}
        self._last_tip_time = -1e9
        self._last_tip_text = ""
        self.current: LiveTip | None = None

    def score(self, o: Observation) -> float:
        freq = 1.0 + min(0.5, 0.1 * (self.rule_counts[o.rule_id] - 1))
        focus = 1.25 if self.cfg.focus not in ("auto", "") and o.category == self.cfg.focus else 1.0
        return o.priority * freq * focus

    def offer(self, observations: list[Observation], now: float, dead_ball: bool = False) -> LiveTip | None:
        """Record observations and return a new tip to show, or None.

        Event feedback (challenge results, play reviews) is tied to a moment that has just happened, so it may
        skip the global gap; during dead-ball time (goal replay, countdown) reviews also replace whatever is
        showing and stay up longer, because that's when there's time to read.
        """
        if self.current and now >= self.current.until:
            self.current = None
        candidates = []
        for o in observations:
            self.history.append(o)
            self.rule_counts[o.rule_id] += 1
            reason = self._reject_reason(o, now)
            if reason:
                self.suppressed[reason] += 1
                continue
            candidates.append(o)
        if not candidates:
            return None
        best = max(candidates, key=self.score)
        review = best.kind == "review" and dead_ball
        event_feedback = best.category == "challenge" or best.kind == "review" or best.rule_id == "goal_for"
        if not review and self.current is not None and self.score(best) <= self.score(self.current.obs) * 1.5:
            self.suppressed["busy"] += 1
            return None  # only interrupt a showing tip for something much more important
        if (now - self._last_tip_time < self.cfg.min_seconds_between_tips and best.category != "defense"
                and not event_feedback):
            self.suppressed["global_gap"] += 1
            return None
        seconds = self.cfg.tip_display_seconds * (1.8 if review else 1.0)
        tip = LiveTip(best, now, now + seconds)
        self.current = tip
        self.shown.append(tip)
        self._last_tip_time = now
        self._last_tip_text = best.tip
        self._last_rule_time[best.rule_id] = now
        return tip

    def _reject_reason(self, o: Observation, now: float) -> str | None:
        if not o.live:
            return "post_match_only"
        if o.category in self.cfg.disabled_categories:
            return "category_disabled"
        if o.kind == "strength" and not self.cfg.show_strengths:
            return "strengths_off"
        if o.confidence < self.cfg.min_confidence:
            return "low_confidence"
        last = self._last_rule_time.get(o.rule_id)
        cooldown = self.cfg.rule_cooldown_seconds if o.cooldown is None else o.cooldown
        if last is not None and now - last < cooldown:
            return "rule_cooldown"
        if o.tip == self._last_tip_text and now - self._last_tip_time < cooldown * 2:
            return "duplicate"
        # Defensive emergencies may bypass the global gap, but nothing else may (checked in offer()).
        return None
