from datetime import date

import pytest

from friends.errors import FriendNotFoundError, ValidationError
from friends.models import Friend, Group
from friends.services import create_friend, find_friends, resolve_friend


def test_create_friend_defaults(session, make_friend):
    friend = make_friend("  Max  Mustermann ", Group.CLOSE_FRIENDS, location="Freiburg")
    assert friend.id and len(friend.id) == 36
    assert friend.name == "Max Mustermann"
    assert friend.normalized_name == "max mustermann"
    assert friend.interval_override is None
    assert friend.interval == 30
    assert session.get(Friend, friend.id) is friend


@pytest.mark.parametrize(
    ("group", "days"),
    [
        (Group.FAMILY, 14),
        (Group.INNER_CIRCLE, 14),
        (Group.CLOSE_FRIENDS, 30),
        (Group.FRIENDS, 60),
        (Group.EXTENDED_CIRCLE, 90),
    ],
)
def test_group_default_intervals(make_friend, group, days):
    assert make_friend("X", group).interval == days


def test_interval_override(make_friend):
    friend = make_friend("Emily", Group.FRIENDS, interval=9)
    assert friend.interval_override == 9
    assert friend.interval == 9
    # An override equal to the group default is not stored.
    same = make_friend("Bela", Group.FRIENDS, interval=60)
    assert same.interval_override is None
    # Changing group keeps the individual override.
    friend.group = Group.EXTENDED_CIRCLE
    assert friend.interval == 9


def test_invalid_interval(make_friend):
    with pytest.raises(ValidationError):
        make_friend("Bad", interval=0)


def test_empty_name_rejected(make_friend):
    with pytest.raises(ValidationError):
        make_friend("   ")


@pytest.mark.parametrize(
    ("text", "group"),
    [
        ("Close friends", Group.CLOSE_FRIENDS),
        ("close FRIENDS", Group.CLOSE_FRIENDS),
        ("close", Group.CLOSE_FRIENDS),
        ("inner", Group.INNER_CIRCLE),
        ("inner_circle", Group.INNER_CIRCLE),
        ("Friends", Group.FRIENDS),
        ("extended-circle", Group.EXTENDED_CIRCLE),
    ],
)
def test_group_parsing(text, group):
    assert Group.parse(text) == group


@pytest.mark.parametrize("text", ["f", "acquaintances", ""])
def test_group_parsing_rejects_unknown_or_ambiguous(text):
    with pytest.raises(ValidationError):
        Group.parse(text)


def test_unicode_names(session, make_friend):
    friend = make_friend("Jørgen Ødegård")
    assert friend.name == "Jørgen Ødegård"
    assert find_friends(session, "jørgen") == [friend]
    assert find_friends(session, "jorgen") == [friend]
    assert find_friends(session, "odegard") == [friend]
    make_friend("李小龍")
    assert [f.name for f in find_friends(session, "李小")] == ["李小龍"]


def test_find_friends_stages(session, make_friend):
    hanna = make_friend("Hanna")
    malin = make_friend("Malin (Hanna)")
    oslo = make_friend("Johanne (Oslo)")
    stg = make_friend("Johanne (Stg)")
    nora = make_friend("Nora H")

    assert find_friends(session, "hanna") == [hanna]  # exact beats word match
    assert find_friends(session, "HAN") == [hanna]  # prefix
    assert find_friends(session, "malin") == [malin]
    assert set(find_friends(session, "johanne")) == {oslo, stg}  # ambiguous
    assert find_friends(session, "johanne stg") == [stg]
    assert find_friends(session, "nora h") == [nora]
    assert find_friends(session, "zzz") == []
    assert find_friends(session, hanna.id[:8]) == [hanna]


def test_resolve_friend_uses_chooser_for_ambiguity(session, make_friend):
    make_friend("Anna (Berlin)")
    oslo = make_friend("Anna (Oslo)")
    seen = []

    def chooser(query, candidates):
        seen.append((query, len(candidates)))
        return oslo

    assert resolve_friend(session, "anna", chooser) is oslo
    assert seen == [("anna", 2)]


def test_resolve_friend_not_found_suggests(session, make_friend):
    make_friend("Max")
    with pytest.raises(FriendNotFoundError, match="Did you mean: Max"):
        resolve_friend(session, "maxx", lambda q, c: c[0])


def test_same_name_people_can_coexist(session, make_friend):
    a = make_friend("Anna")
    b = make_friend("Anna")
    assert a.id != b.id
    assert set(find_friends(session, "anna")) == {a, b}


def test_snooze_flag(make_friend):
    friend = make_friend("Max")
    friend.snoozed_until = date(2026, 11, 1)
    assert friend.is_snoozed(date(2026, 10, 11))
    assert not friend.is_snoozed(date(2026, 11, 1))
