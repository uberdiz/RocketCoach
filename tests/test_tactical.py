from rocketcoach.analysis.context import build_context
from rocketcoach.analysis.tactical import TacticalRules
from tests.helpers import car, frame, two_v_two


def run(rules, frames):  # noqa: ANN001
    out = []
    for f in frames:
        out += rules.update(build_context(f, 0), [])
    return out


def overcommit_frames(me_vel=(0, 1500, 0), opp=(0, -2200, 17), mate=(1500, 1500, 17), n=20):  # noqa: ANN001
    """Ball in our half; I'm last back driving at it; an opponent is right on it; teammate upfield."""
    frames = []
    for k in range(n):
        t = 10 + k / 30
        me = car(0, 0, (0, -4000 + k * 40, 17), me_vel, bot=False)
        frames.append(frame(t, two_v_two(me, mate, [opp, (2000, 2000, 17)], opp_vels=((0, -800, 0), (0, 0, 0))),
                            ball=(0, -2400, 93), ball_vel=(0, -600, 0)))
    return frames


def test_last_man_overcommit_detected():
    obs = run(TacticalRules(), overcommit_frames())
    hits = [o for o in obs if o.rule_id == "last_man_overcommit"]
    assert len(hits) == 1, "reported exactly once per situation"
    assert hits[0].confidence >= 0.55
    assert "teammate" in hits[0].explanation


def test_no_overcommit_warning_when_i_arrive_first():
    # Opponent far away: challenging as last man is correct here.
    obs = run(TacticalRules(), overcommit_frames(opp=(0, 1500, 17)))
    assert not [o for o in obs if o.rule_id == "last_man_overcommit"]


def test_no_overcommit_warning_when_teammate_covers():
    # Teammate is goal-side and nearer our goal than the ball: I'm not last back anymore.
    obs = run(TacticalRules(), overcommit_frames(mate=(800, -4800, 17)))
    assert not [o for o in obs if o.rule_id == "last_man_overcommit"]


def test_single_frame_noise_does_not_trigger():
    obs = run(TacticalRules(), overcommit_frames(n=3))  # 0.1 s < 0.25 s persistence
    assert not obs


def test_double_commit():
    frames = []
    for k in range(20):
        t = 20 + k / 30
        me = car(0, 0, (-700, -200 + k * 10, 17), (900, 900, 0), bot=False)
        cars = two_v_two(me, (0, -350 + k * 15, 17), [(3000, 3000, 17), (-3000, 3000, 17)], mate_vel=(0, 1400, 0))
        frames.append(frame(t, cars, ball=(0, 300, 93)))
    obs = run(TacticalRules(), frames)
    assert [o.rule_id for o in obs].count("double_commit") == 1


def test_no_double_commit_when_i_am_closer():
    frames = []
    for k in range(20):
        me = car(0, 0, (0, -200 + k * 15, 17), (0, 1400, 0), bot=False)
        cars = two_v_two(me, (-700, -200, 17), [(3000, 3000, 17), (-3000, 3000, 17)], mate_vel=(900, 900, 0))
        frames.append(frame(20 + k / 30, cars, ball=(0, 300, 93)))
    assert not [o for o in run(TacticalRules(), frames) if o.rule_id == "double_commit"]


def test_net_exposed():
    frames = []
    for k in range(20):
        me = car(0, 0, (0, 0, 17), (0, -500, 0), bot=False)
        cars = two_v_two(me, (1000, 500, 17), [(0, -2500, 17), (2000, 0, 17)], opp_vels=((0, -1200, 0), (0, 0, 0)))
        frames.append(frame(30 + k / 30, cars, ball=(0, -2800, 93), ball_vel=(0, -900, 0)))
    obs = run(TacticalRules(), frames)
    assert [o.rule_id for o in obs].count("net_exposed") == 1


def test_good_shadow_is_strength():
    frames = []
    for k in range(45):
        me = car(0, 0, (0, -3000 - k * 10, 17), (0, -600, 0), bot=False)
        cars = two_v_two(me, (2000, -4500, 17), [(0, -1500 - k * 10, 17), (2500, 1000, 17)],
                         opp_vels=((0, -600, 0), (0, 0, 0)))
        frames.append(frame(40 + k / 30, cars, ball=(0, -1700 - k * 10, 93), ball_vel=(0, -600, 0)))
    obs = run(TacticalRules(), frames)
    s = [o for o in obs if o.rule_id == "good_shadow"]
    assert s and s[0].kind == "strength"
