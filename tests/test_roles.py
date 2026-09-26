from __future__ import annotations

from majors_lair_bot.roles import match_protected_roles, split_setting

SERVER = [
    {"id": "1", "name": "Ethernals"},
    {"id": "2", "name": "Nucleus ✅"},
    {"id": "3", "name": "Nucleus 🚫"},
    {"id": "4", "name": "Friend"},
    {"id": "5", "name": "Server Booster"},
]


def test_a_typo_is_not_matched_but_is_suggested() -> None:
    """The case that happened: the setting said Eternals, the server role is Ethernals."""
    match = match_protected_roles(SERVER, [], ["Eternals"])
    assert match.roles == {}
    assert match.unmatched_names == [{"configured": "Eternals", "did_you_mean": ["Ethernals"]}]


def test_a_missing_emoji_suggests_both_lookalikes_and_protects_neither() -> None:
    match = match_protected_roles(SERVER, [], ["Nucleus"])
    assert match.roles == {}
    assert match.unmatched_names[0]["did_you_mean"] == ["Nucleus ✅", "Nucleus 🚫"]


def test_exact_names_match_whatever_the_case() -> None:
    match = match_protected_roles(SERVER, [], ["friend", "Nucleus ✅"])
    assert match.roles == {"4": "Friend", "2": "Nucleus ✅"}
    assert match.unmatched_names == []


def test_picked_roles_survive_a_rename_and_deleted_ones_are_reported() -> None:
    renamed = [{**role, "name": "Eternals" if role["id"] == "1" else role["name"]} for role in SERVER]
    match = match_protected_roles(renamed, ["1", "99"], [])
    assert match.roles == {"1": "Eternals"}
    assert match.missing_ids == ["99"]


def test_unrelated_names_get_no_suggestion() -> None:
    match = match_protected_roles(SERVER, [], ["Moderator"])
    assert match.unmatched_names == [{"configured": "Moderator", "did_you_mean": []}]


def test_split_setting_ignores_blanks() -> None:
    assert split_setting(" a, ,b ,") == ["a", "b"]
    assert split_setting("") == []
