from rocketcoach.analysis.observations import Observation
from rocketcoach.coach.prioritizer import Coach
from rocketcoach.config import CoachConfig


def obs(rule="r", cat="rotation", t=0.0, conf=0.8, impact=0.6, tip=None, kind="mistake"):  # noqa: ANN001
    return Observation(rule, cat, t, tip or f"tip {rule}", "why", impact, conf, kind)


def test_one_tip_and_global_gap():
    c = Coach(CoachConfig(min_seconds_between_tips=8, tip_display_seconds=6))
    assert c.offer([obs("a", t=0), obs("b", t=0, impact=0.9)], 0).obs.rule_id == "b"  # highest priority wins
    assert c.offer([obs("c", t=7)], 7) is None  # still within global gap
    assert c.offer([obs("c", t=9)], 9) is not None


def test_rule_cooldown_and_duplicate_suppression():
    c = Coach(CoachConfig(min_seconds_between_tips=1, rule_cooldown_seconds=45))
    assert c.offer([obs("a")], 0)
    assert c.offer([obs("a")], 10) is None
    assert c.suppressed["rule_cooldown"] == 1
    assert c.offer([obs("a2", tip="different")], 20) is not None
    assert c.offer([obs("a3", tip="different")], 30) is None  # new rule, same advice text
    assert c.suppressed["duplicate"] == 1


def test_min_confidence_and_disabled_categories():
    c = Coach(CoachConfig(min_confidence=0.6, disabled_categories=["boost"]))
    assert c.offer([obs("low", conf=0.5)], 0) is None
    assert c.offer([obs("b", cat="boost")], 0) is None
    assert c.suppressed["low_confidence"] == 1 and c.suppressed["category_disabled"] == 1
    assert len(c.history) == 2  # still recorded for the post-match report


def test_defense_can_interrupt_but_minor_cannot():
    c = Coach(CoachConfig(min_seconds_between_tips=8, tip_display_seconds=6))
    c.offer([obs("m", cat="mechanics", impact=0.3, conf=0.6)], 0)
    assert c.offer([obs("o", cat="offense", impact=0.35)], 2) is None
    d = c.offer([obs("d", cat="defense", impact=0.95, conf=0.9)], 2)
    assert d is not None and d.obs.rule_id == "d"


def test_strengths_can_be_turned_off():
    c = Coach(CoachConfig(show_strengths=False))
    assert c.offer([obs("s", kind="strength")], 0) is None
