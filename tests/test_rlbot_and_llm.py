"""RLBot packet conversion (against the real rlbot_flatbuffers types) and the optional Claude provider."""

from types import SimpleNamespace

import pytest

from rocketcoach.coach.llm import ClaudeProvider, NoneProvider, compact_report
from rocketcoach.config import LLMConfig
from rocketcoach.storage.db import Database
from rocketcoach.telemetry.models import AirState, MatchPhase

flat = pytest.importorskip("rlbot.flat")


def make_packet():  # noqa: ANN201
    phys = flat.Physics(flat.Vector3(100, -2000, 17), flat.Rotator(0, 1.57, 0), flat.Vector3(0, 1200, 0),
                        flat.Vector3(0, 0, 0))
    me = flat.PlayerInfo(physics=phys, name="Sebastian", team=0, boost=42.0, is_bot=False,
                         air_state=flat.AirState.InAir, demolished_timeout=-1.0,
                         latest_touch=flat.Touch(12.5, flat.Vector3(0, -1900, 93), flat.Vector3(0, 1, 0), 0),
                         last_input=flat.ControllerState(throttle=1.0, boost=True, jump=True))
    bot = flat.PlayerInfo(physics=phys, name="Bot", team=1, boost=100.0, is_bot=True, demolished_timeout=2.0)
    ball = flat.BallInfo(physics=flat.Physics(flat.Vector3(0, 0, 93), flat.Rotator(), flat.Vector3(0, 500, 0),
                                              flat.Vector3()))
    mi = flat.MatchInfo(seconds_elapsed=33.0, game_time_remaining=200.0, match_phase=flat.MatchPhase.Active,
                        frame_num=3960)
    pkt = flat.GamePacket(players=[me, bot], boost_pads=[flat.BoostPadState(True, 0.0)] * 34, balls=[ball],
                          match_info=mi, teams=[flat.TeamInfo(0, 2), flat.TeamInfo(1, 1)])
    slices = [flat.PredictionSlice(33.0 + i / 120, flat.Physics(flat.Vector3(0, i, 93), flat.Rotator(),
                                                                 flat.Vector3(), flat.Vector3())) for i in range(720)]
    return pkt, flat.BallPrediction(slices)


def test_convert_packet_reads_real_flatbuffer_types():
    from rocketcoach.telemetry.rlbot_source import convert_packet
    pkt, pred = make_packet()
    f = convert_packet(pkt, pred)
    me, bot = f.cars
    assert f.phase == MatchPhase.ACTIVE and f.time == 33.0 and f.scores == (2, 1)
    assert me.name == "Sebastian" and not me.is_bot and me.boost == 42.0
    assert me.air_state == AirState.IN_AIR and me.controls.boost and me.controls.jump
    assert me.latest_touch.time == 12.5 and not me.is_demolished
    assert bot.is_demolished
    assert abs(me.rotation.y - 1.57) < 1e-6
    assert len(f.prediction) == 120 and len(f.pads_active) == 34


# ---- LLM provider -----------------------------------------------------------------------------
class FakeMessages:
    def __init__(self, fail=None):  # noqa: ANN001
        self.calls = 0
        self.fail = fail

    def create(self, **kw):  # noqa: ANN003, ANN201
        self.calls += 1
        if self.fail:
            raise self.fail
        assert kw["max_tokens"] == 600 and kw["fallbacks"] == "default"
        return SimpleNamespace(usage=SimpleNamespace(input_tokens=2000, output_tokens=300), stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text="Stay goal-side.")])


def provider(tmp_path, fail=None, **cfgkw):  # noqa: ANN001, ANN201
    anthropic = pytest.importorskip("anthropic")
    p = ClaudeProvider.__new__(ClaudeProvider)
    p.cfg = LLMConfig(provider="anthropic", **cfgkw)
    p.db = Database(tmp_path / "llm.db")
    p._anthropic = anthropic
    p.client = SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages(fail)))
    return p


def test_claude_summary_cost_and_cache(tmp_path):
    p = provider(tmp_path)
    assert p.summarize({"x": 1}) == "Stay goal-side."
    assert p.summarize({"x": 1}) == "Stay goal-side."  # cached
    assert p.client.beta.messages.calls == 1
    cost, n = p.db.llm_spend_since(0)
    assert n == 1 and abs(cost - (2000 * 4 + 300 * 20) / 1e6) < 1e-9


def test_claude_budget_and_rate_limit(tmp_path):
    p = provider(tmp_path, max_requests_per_hour=1)
    p.summarize({"a": 1})
    assert p.summarize({"b": 2}) is None  # second distinct request within the hour is refused locally
    assert p.client.beta.messages.calls == 1


def test_claude_offline_failure_is_graceful(tmp_path):
    anthropic = pytest.importorskip("anthropic")
    import httpx2
    err = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com"))
    p = provider(tmp_path, fail=err)
    assert p.summarize({"a": 1}) is None


def test_none_provider_and_compact_report_has_no_raw_telemetry():
    assert NoneProvider().summarize({}) is None
    rep = {
        "result": {}, "data_source": {"simulated": True}, "mistakes": [], "strengths": [], "grades": [],
        "boost": {"pct_below": {}, "spent_supersonic": 0, "starved_out_of_play_s": 0, "low_boost_ok_s": 0,
                  "avg_challenge_boost": None, "timeline": [1]},
        "mechanics": {"not_measured": {}}, "comparison": {}, "training_plan": {"focus": []},
    }
    c = compact_report(rep)
    assert "timeline" not in c["boost"] and "player" not in c


def test_psyonix_bots_reported_as_non_bots_are_still_excluded():
    """RLBot v5 sends is_bot=False for Psyonix bots; the Human entry in the match config decides."""
    from rocketcoach.telemetry.rlbot_source import convert_packet, human_player_ids
    phys = flat.Physics(flat.Vector3(0, 0, 17), flat.Rotator(), flat.Vector3(), flat.Vector3())
    psyonix = flat.PlayerInfo(physics=phys, name="Centice", team=1, is_bot=False, player_id=-622783296,
                              demolished_timeout=-1.0)
    human = flat.PlayerInfo(physics=phys, name="uberdiz", team=0, is_bot=False, player_id=0, demolished_timeout=-1.0)
    cfg = flat.MatchConfiguration(player_configurations=[
        flat.PlayerConfiguration(flat.PsyonixBot(), 1, -622783296), flat.PlayerConfiguration(flat.Human(), 0, 0)])
    ids = human_player_ids(cfg)
    assert ids == {0}
    f = convert_packet(flat.GamePacket(players=[psyonix, human], match_info=flat.MatchInfo()), None, ids)
    assert [c.is_bot for c in f.cars] == [True, False]
