"""Live positioning guide, per-play reviews, challenge detection and report Markdown rendering."""

from rocketcoach.analysis.context import build_context
from rocketcoach.analysis.events import Event, EventDetector
from rocketcoach.analysis.guide import PositionGuide, decide
from rocketcoach.analysis.plays import PlayReviewer
from rocketcoach.report.markdown import to_html
from rocketcoach.telemetry.models import Touch, Vec3
from tests.helpers import car, frame, two_v_two


def ctx_for(me_loc, mate_loc, opp_locs, ball, ball_vel=(0, 0, 0), me_vel=(0, 0, 0), opp_vels=((0, 0, 0), (0, 0, 0)),
            t=10.0):  # noqa: ANN001, ANN201
    me = car(0, 0, me_loc, me_vel, bot=False)
    f = frame(t, two_v_two(me, mate_loc, opp_locs, opp_vels=opp_vels), ball=ball, ball_vel=ball_vel)
    return build_context(f, 0)


def test_last_back_ahead_of_ball_is_sent_to_back_post():
    # Opponent dribbling at our goal, I'm upfield of the ball and deeper than my teammate.
    g = decide(ctx_for((0, -500, 17), (1500, 1500, 17), [(0, -2300, 17), (2000, 2000, 17)], (0, -2500, 93),
                       ball_vel=(0, -800, 0), opp_vels=((0, -800, 0), (0, 0, 0))))
    assert g.action == "back_post" and g.target.y < -4500 and g.urgency == 2


def test_last_back_goal_side_shadows():
    g = decide(ctx_for((0, -4300, 17), (1500, 1500, 17), [(0, -2200, 17), (2000, 2000, 17)], (0, -2400, 93),
                       opp_vels=((0, -800, 0), (0, 0, 0))))
    assert g.action == "shadow" and "goal-side" in g.text
    assert -4400 < g.target.y < -2400  # between the ball and our goal


def test_second_man_supports_when_teammate_has_it():
    g = decide(ctx_for((0, -3500, 17), (0, 1800, 17), [(2500, 4000, 17), (-2500, 4500, 17)], (0, 2000, 93)))
    assert g.action == "support" and g.target.y < 2000


def test_my_ball_in_attacking_third():
    g = decide(ctx_for((0, 2800, 17), (0, -2000, 17), [(3000, 4800, 17), (-3000, 4800, 17)], (0, 3000, 93)))
    assert g.action == "attack" and "shoot" in g.text


def test_guide_holds_instruction_against_flicker():
    pg = PositionGuide()
    a = ctx_for((0, 2800, 17), (0, -2000, 17), [(3000, 4800, 17), (-3000, 4800, 17)], (0, 3000, 93), t=1.0)
    b = ctx_for((0, -3500, 17), (0, 1800, 17), [(2500, 4000, 17), (-2500, 4500, 17)], (0, 2000, 93), t=1.1)
    assert pg.update(a).action == "attack"
    assert pg.update(b).action == "attack"  # 0.1 s later: not settled yet, keep the current instruction


def test_challenge_detected_with_reasons_and_review():
    det = EventDetector(0)
    rev = PlayReviewer(0)
    # I'm last back, 5 boost, nearly stopped; opponent and I both on the ball; ball then goes toward our goal.
    me = car(0, 0, (0, -2000, 17), (300, 100, 0), boost=5, bot=False)
    opp = car(2, 1, (0, -1700, 17), (0, -1500, 0))
    frames = [frame(10.0, [me, car(1, 0, (0, 1000, 17)), opp, car(3, 1, (0, 3000, 17))], ball=(0, -1850, 93))]
    opp_t = car(2, 1, (0, -1700, 17), (0, -1500, 0), touch=Touch(10.1, Vec3(0, -1850, 93), Vec3(0, -1, 0)))
    for k in range(1, 15):
        frames.append(frame(10.0 + k / 10, [me, car(1, 0, (0, 1000, 17)), opp_t, car(3, 1, (0, 3000, 17))],
                            ball=(0, -1850 - k * 100, 93), ball_vel=(0, -1000, 0)))
    obs = []
    for f in frames:
        evs = det.update(f, build_context(f, 0))
        obs += rev.update(build_context(f, 0), None, evs)
    ch = [e for e in det.events if e.type == "challenge"]
    assert ch and ch[0].data["result"] == "lost" and ch[0].data["last_man"]
    lost = [o for o in obs if o.rule_id == "challenge_lost"]
    assert lost and "boost" in lost[0].explanation and lost[0].next_time


def test_goal_against_review_without_ball_context():
    rev = PlayReviewer(0)
    obs = rev.update(None, None, [Event(50.0, "goal", {"team": 1, "for_me": False, "last_toucher": 2})])
    assert obs and obs[0].rule_id.startswith("goal_against")


def test_markdown_renders_tables_lists_and_inline():
    md = ("# T\n\n> **SIM**\n\nIntro *x* and `code`.\n\n## Grades\n| A | B |\n|---|---|\n| 1 | **2** |\n\n"
          "1. **First**\n   more text\n2. Second\n\n- a\n- b <script>\n")
    out = to_html(md)
    assert "<table>" in out and "<td><strong>2</strong></td>" in out
    assert "<ol><li><strong>First</strong><br>more text</li><li>Second</li></ol>" in out
    assert "<em>x</em>" in out and "<code>code</code>" in out and "<blockquote>" in out
    assert "<script>" not in out and "&lt;script&gt;" in out
