"""Kickoff spawns/roles/feedback, learned ball decisions, rank estimate and level parsing."""

from rocketcoach.analysis.decisions import DecisionModel, DecisionTracker, Situation, classify, situation
from rocketcoach.analysis.events import Event
from rocketcoach.analysis.kickoff import KickoffCoach, kicker_for, make_plan, spawn_of
from rocketcoach.analysis.levels import level_group, parse_rank, rank_name
from rocketcoach.analysis.rank import estimate, match_tier
from rocketcoach.telemetry.models import MatchPhase, Touch, Vec3
from tests.helpers import car, frame


def kickoff_cars(me_xy=(2048, -2560), mate_xy=(-2048, -2560)):  # noqa: ANN001, ANN201
    return [car(0, 0, (*me_xy, 17), bot=False), car(1, 0, (*mate_xy, 17)),
            car(2, 1, (-2048, 2560, 17)), car(3, 1, (256, 3840, 17))]


# ---- levels / rank -----------------------------------------------------------------------------
def test_parse_and_name_ranks():
    assert parse_rank("Diamond 1") == 12 and parse_rank("diamond III") == 14
    assert parse_rank("Plat 2") == 10 and parse_rank("GC3") == 20 and parse_rank("SSL") == 21
    assert parse_rank("nonsense") is None
    assert rank_name(12) == "Diamond I" and rank_name(21) == "Supersonic Legend"
    assert [level_group(t) for t in (2, 8, 13, 19)] == ["new", "mid", "high", "elite"]


def test_rank_estimate_blends_and_reports_range():
    t, parts = match_tier({"avg_speed": 1430, "supersonic_pct": 12, "boost_per_min": 380})
    assert 12.5 < t < 13.5 and len(parts) == 3
    est = estimate({"avg_speed": 1430, "supersonic_pct": 12, "boost_per_min": 380}, [11.0, 12.0], 12, 300)
    assert est["available"] and est["name"].startswith(("Diamond", "Platinum"))
    assert est["range"][0] != est["range"][1] and est["confidence"] in ("low", "medium")
    assert {b["source"] for b in est["blend"]} == {"your real rank", "your last 2 matches", "this match"}


def test_rank_estimate_without_data():
    assert estimate({}, [], None, 0)["available"] is False


# ---- kickoffs ----------------------------------------------------------------------------------
def test_spawns_left_right_from_players_view():
    assert spawn_of(car(0, 0, (2048, -2560, 17))) == ("left diagonal", "diagonal")
    assert spawn_of(car(0, 0, (-256, -3840, 17))) == ("right off-center", "off-center")
    assert spawn_of(car(0, 1, (-2048, 2560, 17))) == ("left diagonal", "diagonal")  # orange is mirrored
    assert spawn_of(car(0, 0, (0, -4608, 17)))[0] == "back center"
    assert spawn_of(car(0, 0, (500, -1000, 17))) is None  # not on a spawn (replay still running)


def test_left_goes_on_diagonal_tie_and_plan_text_by_level():
    f = frame(1.0, kickoff_cars(), phase=MatchPhase.COUNTDOWN)
    assert kicker_for(0, f.cars) == 0  # I'm the left diagonal
    plan = make_plan(f, 0, "high")
    assert plan.role == "go" and "speedflip" in plan.text
    assert "flip in at the end" in make_plan(f, 0, "new").text
    f2 = frame(1.0, kickoff_cars(me_xy=(0, -4608), mate_xy=(2048, -2560)), phase=MatchPhase.COUNTDOWN)
    assert make_plan(f2, 0, "high").role == "cheat" and make_plan(f2, 0, "new").role == "boost"


def test_kickoff_feedback_arrival_and_direction():
    kc = KickoffCoach(0, "high")
    kc.update(frame(0.0, kickoff_cars(), phase=MatchPhase.COUNTDOWN), [])
    kc.update(frame(3.0, kickoff_cars(), phase=MatchPhase.KICKOFF), [])
    opp = car(2, 1, (-100, 150, 17), touch=Touch(4.9, Vec3(0, 0, 93), Vec3(0, -1, 0)))
    me = car(0, 0, (100, -150, 17), bot=False, touch=Touch(5.1, Vec3(0, 0, 93), Vec3(0, 1, 0)))
    cars = [me, car(1, 0, (-2048, -2560, 17)), opp, car(3, 1, (256, 3840, 17))]
    kc.update(frame(5.2, cars, phase=MatchPhase.ACTIVE), [])
    late = frame(7.2, cars, ball=(-1500, -2500, 93), phase=MatchPhase.ACTIVE)
    out = kc.update(late, [Event(7.2, "kickoff_result", {"result": "lost", "first_touch": 2, "my_team": 0})])
    assert out and out[0].tip == "Kickoff lost right"  # ball at -x in blue's half = your right
    assert "sooner" in out[0].explanation and out[0].next_time
    assert kc.samples[0]["my_arrival"] == 2.1 and kc.samples[0]["opp_arrival"] == 1.9


# ---- decisions ---------------------------------------------------------------------------------
def test_situation_and_classification():
    me = car(0, 0, (3000, 4300, 17), (0, 800, 0), bot=False)
    f = frame(1.0, [me, car(1, 0, (0, 2500, 17)), car(2, 1, (0, 5000, 17)), car(3, 1, (-3000, 0, 17))],
              ball=(3000, 4400, 93), ball_vel=(-1800, 200, 100))
    sit = situation(f, me)
    assert sit.zone == "att_corner" and sit.keeper and sit.mate_open
    assert classify(f, me, on_target=False) == "center"
    f_clear = frame(1.0, [car(0, 0, (0, -4000, 17), bot=False)], ball=(0, -3900, 93), ball_vel=(300, 2000, 400))
    assert classify(f_clear, f_clear.cars[0], on_target=False) == "clear"


def test_model_learns_from_data_over_prior():
    m = DecisionModel()
    sit = Situation("att_corner", False, True, True, False)
    assert m.ranked(sit)[0][0] == "center"  # prior: center from the corner
    for _ in range(30):
        m.add(sit.key, "control", 0.8)  # but controlling it there keeps creating goals
    assert m.ranked(sit)[0][0] == "control"


def test_tracker_values_outcomes_and_learns_from_opponents():
    m = DecisionModel()
    tr = DecisionTracker(0, m)
    opp = car(2, 1, (0, -3000, 17), touch=Touch(1.0, Vec3(0, -3100, 93), Vec3(0, -1, 0)))
    cars = [car(0, 0, (0, 0, 17), bot=False), car(1, 0, (1000, 1000, 17)), opp, car(3, 1, (0, 3000, 17))]
    f = frame(1.0, cars, ball=(0, -3100, 93), ball_vel=(0, -2500, 0))
    tr.update(f, [Event(1.0, "touch", {"car": 2, "on_target": True}),
                  Event(1.0, "shot", {"car": 2, "team": 1})])
    tr.update(frame(2.0, cars), [Event(2.0, "goal", {"team": 1, "for_me": False})])
    assert tr.finished and tr.finished[0]["actor"] == "opp" and tr.finished[0]["action"] == "shoot"
    assert tr.finished[0]["value"] == 1.0 and m.samples == 1  # opponent's goal counts as their success
