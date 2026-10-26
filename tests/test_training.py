import json
import re

from rocketcoach.training.recommender import PACKS_FILE, build_plan, find_weaknesses, load_packs

CODE_RE = re.compile(r"^[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}$")


def test_pack_catalog_is_well_formed():
    packs = load_packs()
    codes = [p["code"] for p in packs]
    assert len(codes) == len(set(codes))
    for p in packs:
        assert CODE_RE.match(p["code"]), p
        assert p["source_url"].endswith(p["code"])
        assert p["difficulty"] in ("Bronze", "Silver", "Gold", "Platinum", "Diamond", "Champion", "Grand Champion")


def test_no_evidence_no_recommendation():
    plan = build_plan(find_weaknesses({}, {}), verification={})
    assert plan["focus"] == [] and plan["note"]


def test_recommendations_follow_observed_weaknesses():
    m = {"aerial_attempts": 8, "aerial_contacts": 2, "aerial_double_jump_takeoffs": 0, "recoveries": 2}
    weak = find_weaknesses(m, {"last_man_overcommit": 3, "net_exposed": 1})
    skills = [w.skill for w in weak]
    assert skills[0] == "shadow_defense"
    assert "aerials" in skills and "recoveries" not in skills  # too few landings to judge
    plan = build_plan(weak, "Diamond", verification={})
    assert len(plan["focus"]) == 2  # manageable session
    known = {p["code"] for p in load_packs()}
    for f in plan["focus"]:
        if f.get("code"):
            assert f["code"] in known  # never invented
            assert f["verified"] is False and "Search" in f["search_alternative"]
            assert f["success"]


def test_verification_flag_comes_from_verification_results():
    weak = find_weaknesses({}, {"last_man_overcommit": 4})
    code = build_plan(weak, verification={})["focus"][0]["code"]
    plan = build_plan(weak, verification={code: {"ok": True, "at": "2026-10-25"}})
    assert plan["focus"][0]["verified"] is True


def test_rank_appropriate_difficulty():
    weak = find_weaknesses({"shots_on_target": 1, "attacking_touches_off_target": 9}, {})
    f = build_plan(weak, "Platinum", verification={})["focus"][0]
    assert f["difficulty"] in ("Platinum", "Gold")


def test_packs_file_has_source_note():
    assert "source_url" in json.loads(PACKS_FILE.read_text(encoding="utf-8"))["_about"]
