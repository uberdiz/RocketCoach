"""BALL / BOOST call: follows the guide, picks a pad on your path, never a forward pad when last back."""

from rocketcoach.analysis.boostball import BoostBallCaller, decide_call
from rocketcoach.analysis.context import build_context
from rocketcoach.analysis.guide import decide
from rocketcoach.telemetry.models import MatchPhase, Vec3, standard_boost_pads
from tests.helpers import car, frame

PADS = standard_boost_pads()


def ctx_for(cars, ball=(0, 0, 93), ball_vel=(0, 0, 0), phase=MatchPhase.ACTIVE, t=1.0):  # noqa: ANN001, ANN201
    return build_context(frame(t, cars, ball=ball, ball_vel=ball_vel, phase=phase), 0)


def test_ball_when_its_yours():
    me = car(0, 0, (0, -1500, 17), (0, 1000, 0), boost=5, bot=False)
    cars = [me, car(1, 0, (0, -4000, 17)), car(2, 1, (0, 4000, 17)), car(3, 1, (500, 4500, 17))]
    c = ctx_for(cars, ball=(0, -800, 93))
    g = decide(c)
    assert g.action == "attack"
    assert decide_call(c, g, PADS).call == "BALL"  # low boost, but the ball is yours and nobody is near


def test_boost_on_rotation_with_low_boost_picks_pad_on_path():
    # teammate has the ball upfield; I'm second man with 10 boost: grab a pad on the way to the support spot
    me = car(0, 0, (1500, -500, 17), (0, -800, 0), boost=10, bot=False)
    cars = [me, car(1, 0, (0, 1800, 17)), car(2, 1, (0, 4000, 17)), car(3, 1, (500, 4500, 17))]
    c = ctx_for(cars, ball=(0, 2000, 93))
    g = decide(c)
    assert g.action == "support"
    call = decide_call(c, g, PADS)
    assert call.call == "BOOST" and call.pad is not None
    assert c.me.location.flat_dist(call.pad) < 2000


def test_last_back_never_sent_upfield_for_boost():
    # I'm last back, ball and opponents in front: the corner/mid pads ahead of the ball don't count
    me = car(0, 0, (0, -4300, 17), boost=5, bot=False)
    cars = [me, car(1, 0, (0, -500, 17)), car(2, 1, (0, -1400, 17), (0, -1200, 0)), car(3, 1, (1500, 0, 17))]
    c = ctx_for(cars, ball=(0, -1800, 93), ball_vel=(0, -800, 0))
    g = decide(c)
    call = decide_call(c, g, PADS)
    if call.call == "BOOST":
        assert call.pad is not None and call.pad.y < -1800 - 300


def test_contested_5050_on_empty_with_cover_is_boost():
    me = car(0, 0, (0, -1000, 17), (0, 800, 0), boost=3, bot=False)
    cars = [me, car(1, 0, (0, -3500, 17)), car(2, 1, (0, 1000, 17), (0, -800, 0)), car(3, 1, (0, 4000, 17))]
    c = ctx_for(cars, ball=(0, 0, 93))
    g = decide(c)
    assert g.action == "challenge"
    call = decide_call(c, g, PADS)
    assert call.call == "BOOST" and "empty" in call.reason
    me.boost = 60
    assert decide_call(c, g, PADS).call == "BALL"


def test_kickoff_and_dead_ball():
    cars = [car(0, 0, (-2048, -2560, 17), bot=False), car(1, 0, (2048, -2560, 17)),
            car(2, 1, (-2048, 2560, 17)), car(3, 1, (2048, 2560, 17))]
    c = ctx_for(cars, phase=MatchPhase.COUNTDOWN)
    g = decide(c)
    assert decide_call(c, g, PADS).call in ("BALL", "")
    c2 = ctx_for(cars, phase=MatchPhase.REPLAY)
    assert decide_call(c2, g, PADS).call == ""


def test_caller_holds_against_flicker():
    me = car(0, 0, (1500, -500, 17), (0, -800, 0), boost=10, bot=False)
    cars = [me, car(1, 0, (0, 1800, 17)), car(2, 1, (0, 4000, 17)), car(3, 1, (500, 4500, 17))]
    caller = BoostBallCaller(PADS)
    c = ctx_for(cars, ball=(0, 2000, 93), t=1.0)
    first = caller.update(c, decide(c)).call
    me.boost = 40  # one frame over the threshold doesn't flip it straight away
    c = ctx_for(cars, ball=(0, 2000, 93), t=1.3)
    assert caller.update(c, decide(c)).call == first == "BOOST"
    c = ctx_for(cars, ball=(0, 2000, 93), t=3.0)
    assert caller.update(c, decide(c)).call == "BALL"


def test_direction_words():
    from rocketcoach.analysis.boostball import _direction
    me = car(0, 0, (0, 0, 17), bot=False)
    me.rotation = Vec3(0, 1.5708, 0)  # facing +y (towards orange)
    c = ctx_for([me])
    assert _direction(c, Vec3(0, 1000, 0)) == "ahead"
    assert _direction(c, Vec3(0, -1000, 0)) == "behind"
    assert _direction(c, Vec3(-1000, 0, 0)) == "right"  # blue facing orange: -x is your right
    assert _direction(c, Vec3(1000, 0, 0)) == "left"
