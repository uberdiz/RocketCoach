from rocketcoach.analysis.boost import BoostCoach
from rocketcoach.analysis.context import build_context
from rocketcoach.telemetry.models import standard_boost_pads
from tests.helpers import car, frame, two_v_two


def run(coach, frames):  # noqa: ANN001
    out = []
    for f in frames:
        out += coach.update(build_context(f, 0), [])
    return out


def test_time_below_thresholds():
    bc = BoostCoach(standard_boost_pads())
    frames = [frame(k / 10, two_v_two(car(0, 0, (0, -3000, 17), boost=10 if k < 50 else 50, bot=False),
                                       (0, 0, 17), [(0, 3000, 17), (100, 3000, 17)])) for k in range(100)]
    run(bc, frames)
    d = bc.s.to_dict()
    assert 45 < d["pct_below"]["20"] < 55
    assert d["pct_below"]["60"] > 95


def test_supersonic_boost_waste_uses_measured_input():
    bc = BoostCoach(standard_boost_pads())
    frames = []
    for k in range(80):
        me = car(0, 0, (0, -3000 + k * 70, 17), (0, 2250, 0), boost=90 - k * 0.6, bot=False, boosting=True)
        frames.append(frame(k / 30, two_v_two(me, (0, -4000, 17), [(0, 3000, 17), (100, 3500, 17)])))
    obs = run(bc, frames)
    assert [o.rule_id for o in obs] == ["boost_while_supersonic"]
    assert bc.s.spent_supersonic > 40


def test_no_supersonic_tip_when_not_holding_boost():
    bc = BoostCoach(standard_boost_pads())
    frames = [frame(k / 30, two_v_two(car(0, 0, (0, -3000 + k * 70, 17), (0, 2250, 0), boost=50, bot=False),
                                      (0, -4000, 17), [(0, 3000, 17), (100, 3500, 17)])) for k in range(80)]
    assert run(bc, frames) == []


def test_low_boost_while_well_positioned_is_not_starvation():
    bc = BoostCoach(standard_boost_pads())
    # Last back, goal-side, 5 boost: fine. Should accumulate low_boost_ok, no tip.
    frames = [frame(k / 10, two_v_two(car(0, 0, (0, -4500, 17), boost=5, bot=False), (0, 1000, 17),
                                      [(0, 3000, 17), (100, 3500, 17)], ), ball=(0, 2000, 93)) for k in range(100)]
    assert run(bc, frames) == []
    assert bc.s.low_boost_ok_s > 9 and bc.s.starved_out_of_play_s == 0


def test_corner_trip_as_last_man_flagged():
    bc = BoostCoach(standard_boost_pads())
    frames = []
    # I'm last back at our half, low boost, driving to the far orange-side corner? use own corner (-3072,-4096)
    # while opponents have the ball near our goal.
    for k in range(60):
        x = -1000 - k * 34
        y = -2000 - k * 35
        me = car(0, 0, (x, y, 17), (-1400, -1400, 0), boost=20, bot=False)
        cars = two_v_two(me, (0, 2000, 17), [(1500, -2600, 17), (0, 1000, 17)], opp_vels=((0, -1000, 0), (0, 0, 0)))
        frames.append(frame(k / 30, cars, ball=(1500, -2900, 93), ball_vel=(0, -800, 0)))
    me = car(0, 0, (-3072, -4096, 17), (0, 0, 0), boost=100, bot=False)
    frames.append(frame(2.1, two_v_two(me, (0, 2000, 17), [(1500, -3000, 17), (0, 1000, 17)]), ball=(1500, -3300, 93)))
    obs = run(bc, frames)
    assert any(o.rule_id == "corner_boost_last_man" for o in obs)
    assert bc.s.big_pads == 1 and bc.s.full_trips[0]["last_man"]
