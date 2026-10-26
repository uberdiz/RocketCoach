from rocketcoach.analysis.context import build_context
from rocketcoach.analysis.events import EventDetector, shot_on_target
from rocketcoach.telemetry.models import AirState, MatchPhase, Touch, Vec3
from tests.helpers import car, frame, two_v_two


def feed(det, frames):  # noqa: ANN001
    out = []
    for f in frames:
        out += det.update(f, build_context(f, 0))
    return out


def base(t, me=None, **kw):  # noqa: ANN001
    me = me or car(0, 0, (0, -2000, 17), bot=False)
    return frame(t, two_v_two(me, (1000, -3000, 17), [(0, 2000, 17), (500, 3000, 17)]), **kw)


def test_kickoff_touch_and_result():
    det = EventDetector(0)
    frames = [base(0.0, phase=MatchPhase.COUNTDOWN), base(3.0, phase=MatchPhase.KICKOFF)]
    me = car(0, 0, (0, -150, 17), (0, 1800, 0), bot=False, touch=Touch(5.1, Vec3(0, -90, 93), Vec3(0, 1, 0)))
    frames.append(frame(5.1, two_v_two(me, (1000, -3000, 17), [(0, 150, 17), (500, 3000, 17)]),
                        ball=(0, 100, 93), ball_vel=(0, 1500, 300), phase=MatchPhase.ACTIVE))
    frames.append(frame(7.2, two_v_two(me, (1000, -3000, 17), [(0, 150, 17), (500, 3000, 17)]),
                        ball=(0, 2600, 93), ball_vel=(0, 900, 0)))
    types = [e.type for e in feed(det, frames)]
    assert "kickoff_start" in types and "kickoff_touch" in types
    res = [e for e in det.events if e.type == "kickoff_result"][0]
    assert res.data["result"] == "won"


def test_goal_event_and_possession_change():
    det = EventDetector(0)
    me = car(0, 0, (0, 3000, 17), bot=False, touch=Touch(10.0, Vec3(0, 3100, 93), Vec3(0, 1, 0)))
    opp_touch = car(2, 1, (0, 3500, 17), touch=Touch(10.5, Vec3(0, 3400, 93), Vec3(0, -1, 0)))
    f1 = frame(10.0, [me, car(1, 0, (0, 0, 17)), car(2, 1, (0, 3500, 17)), car(3, 1, (0, 4000, 17))])
    f2 = frame(10.5, [me, car(1, 0, (0, 0, 17)), opp_touch, car(3, 1, (0, 4000, 17))])
    f3 = frame(11.0, [me, car(1, 0, (0, 0, 17)), opp_touch, car(3, 1, (0, 4000, 17))], scores=(1, 0))
    me0 = car(0, 0, (0, 3000, 17), bot=False)
    evs = feed(det, [frame(9.9, [me0] + f1.cars[1:]), f1, f2, f3])
    assert any(e.type == "possession_change" and e.data["to_team"] == 1 for e in evs)
    goal = [e for e in evs if e.type == "goal"][0]
    assert goal.data["for_me"] is True


def test_duplicate_frames_ignored_and_gap_reported():
    det = EventDetector(0)
    f = base(1.0)
    assert det.update(f, None) != []  # first frame -> phase event
    assert det.update(base(1.0, fn=f.frame_num), None) == []  # duplicate packet
    evs = det.update(base(3.0), None)
    assert any(e.type == "telemetry_gap" for e in evs)
    assert det.gaps == 1


def test_time_going_backwards_is_a_new_match():
    det = EventDetector(0)
    det.update(base(100.0), None)
    evs = det.update(base(1.0), None)
    assert evs[0].type == "match_restart"


def test_aerial_and_recovery():
    det = EventDetector(0)
    frames = []
    t = 50.0
    frames.append(base(t, ball=(0, -1500, 900)))
    for k in range(1, 40):  # airborne 1.3 s
        me = car(0, 0, (0, -2000, 100 + k * 15), (0, 300, 400), bot=False, air=AirState.IN_AIR)
        frames.append(base(t + k / 30, me=me, ball=(0, -1500, 900)))
    land_t = t + 40 / 30
    frames.append(base(land_t, me=car(0, 0, (0, -2000, 17), (0, 400, 0), bot=False), ball=(0, -1500, 900)))
    for k in range(1, 9):  # regain speed over 0.8 s
        v = 400 + k * 113
        frames.append(base(land_t + k / 10, me=car(0, 0, (0, -2000 + k * 50, 17), (0, v, 0), bot=False),
                           ball=(0, 0, 93)))
    evs = feed(det, frames)
    aer = [e for e in evs if e.type == "aerial"]
    assert aer and aer[0].data["contact"] is False
    rec = [e for e in evs if e.type == "recovery"]
    assert rec and 0.7 < rec[0].data["seconds"] < 0.9 and rec[0].data["regained"]


def test_shot_on_target_projection():
    on, x = shot_on_target(Vec3(0, 3000, 93), Vec3(100, 2000, 300), team=0)
    assert on and abs(x) < 400
    off, _ = shot_on_target(Vec3(0, 3000, 93), Vec3(2000, 1000, 0), team=0)
    assert not off
    away, _ = shot_on_target(Vec3(0, 3000, 93), Vec3(0, -2000, 0), team=0)
    assert not away
