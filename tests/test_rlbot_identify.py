"""Identifying the human car when RLBot's match configuration is missing or doesn't list the cars."""

import pytest

flat = pytest.importorskip("rlbot.flat")

from rocketcoach.telemetry.rlbot_source import identify_humans  # noqa: E402


def player(name: str, pid: int, team: int = 0, is_bot: bool = False):  # noqa: ANN201
    phys = flat.Physics(flat.Vector3(), flat.Rotator(), flat.Vector3(), flat.Vector3())
    return flat.PlayerInfo(physics=phys, name=name, team=team, is_bot=is_bot, player_id=pid, demolished_timeout=-1.0)


PLAYERS = [player("Centice", 11, 1), player("Khan", 12, 0), player("Raja (2)", 13, 1), player("uberdiz", 0, 0)]


def test_match_configuration_wins():
    cfg = flat.MatchConfiguration(player_configurations=[flat.PlayerConfiguration(flat.Human(), 0, 0)])
    assert identify_humans(PLAYERS, cfg, set()) == ({0}, "match configuration")


def test_empty_configuration_falls_back_to_learned_name():
    empty = flat.MatchConfiguration()
    assert identify_humans(PLAYERS, empty, {"uberdiz"}) == ({0}, "name learned from an earlier match")


def test_player_name_setting():
    assert identify_humans(PLAYERS, None, set(), "Uber")[0] == {0}


def test_only_non_psyonix_name_is_the_human():
    ids, method = identify_humans(PLAYERS, None, set())
    assert ids == {0} and "Psyonix" in method


def test_ambiguous_waits_instead_of_guessing():
    two_humans = PLAYERS + [player("friend", 5, 1)]
    assert identify_humans(two_humans, None, set()) == (set(), "unknown")
