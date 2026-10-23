"""YAML snapshot: round trip, restore and validation."""

from datetime import date, datetime

import pytest

from friends.csv_io import imported_interaction_id
from friends.errors import FriendsError, ValidationError
from friends.interactions import history, log_interaction
from friends.models import Group, InteractionType, Source
from friends.services import all_friends
from friends.snapshot import dump_friends, from_yaml, read_snapshot, restore, to_yaml, write_snapshot

TODAY = date(2026, 10, 11)


def seed(session, make_friend):
    sophie = make_friend("Sophie", Group.CLOSE_FRIENDS, location="Berlin", interval=21, notes="likes tea")
    sophie.snoozed_until = date(2026, 12, 1)
    log_interaction(session, sophie, InteractionType.MEETING, on=date(2026, 9, 1), today=TODAY, note="dinner: nice")
    log_interaction(session, sophie, InteractionType.MESSAGE, on=date(2026, 9, 2), today=TODAY, note='said "hi", ø')
    log_interaction(
        session, sophie, InteractionType.CALL, on=TODAY, today=TODAY, now=datetime(2026, 10, 11, 18, 30, 15)
    )
    max_ = make_friend("Max", Group.INNER_CIRCLE)
    from friends.models import Interaction

    session.add(
        Interaction(
            id=imported_interaction_id(max_.id, date(2026, 6, 10)),
            friend_id=max_.id,
            type=InteractionType.IMPORTED_CONTACT,
            occurred_at=datetime(2026, 6, 10, 12, 0),
            source=Source.CSV_IMPORT,
        )
    )
    make_friend("Zoë (Oslo)", Group.FRIENDS)
    session.flush()
    return sophie, max_


def test_yaml_layout(session, make_friend):
    seed(session, make_friend)
    text = to_yaml(dump_friends(session))
    assert text.startswith("# friends snapshot")
    assert "- name: Max\n" in text and text.index("Max") < text.index("Sophie") < text.index("Zoë")
    assert "  group: Close friends\n" in text
    assert "  interval: 21\n" in text and "  snoozed_until: 2026-12-01\n" in text
    assert '    - {date: 2026-09-01, type: meeting, note: "dinner: nice"}\n' in text
    assert "    - {date: 2026-10-11 18:30:15, type: call}\n" in text
    assert "    - {date: 2026-06-10, type: imported}\n" in text
    assert "interval:" not in text.split("- name: Max")[1].split("- name: Sophie")[0]  # group default not written


def test_round_trip_is_exact_and_idempotent(session, make_friend, tmp_path):
    sophie, max_ = seed(session, make_friend)
    path = tmp_path / "data" / "friends.yaml"
    assert write_snapshot(session, path) == 3
    first = path.read_text(encoding="utf-8")

    records = read_snapshot(path)
    assert restore(session, records) == (3, 4)
    friends = {f.name: f for f in all_friends(session)}
    assert friends["Sophie"].id == sophie.id and friends["Max"].id == max_.id
    assert friends["Sophie"].interval_override == 21 and friends["Sophie"].notes == "likes tea"
    assert friends["Sophie"].snoozed_until == date(2026, 12, 1)
    assert friends["Max"].interval_override is None
    rows = history(session, friend_id=friends["Sophie"].id)
    assert [(r.type, r.occurred_at, r.note, r.source) for r in rows] == [
        (InteractionType.CALL, datetime(2026, 10, 11, 18, 30, 15), None, Source.MANUAL),
        (InteractionType.MESSAGE, datetime(2026, 9, 2, 12, 0), 'said "hi", ø', Source.MANUAL),
        (InteractionType.MEETING, datetime(2026, 9, 1, 12, 0), "dinner: nice", Source.MANUAL),
    ]
    imported = history(session, friend_id=max_.id)[0]
    assert imported.id == imported_interaction_id(max_.id, date(2026, 6, 10))
    assert imported.source == Source.CSV_IMPORT

    write_snapshot(session, path)
    assert path.read_text(encoding="utf-8") == first


def test_restore_replaces_everything(session, make_friend):
    seed(session, make_friend)
    restore(session, from_yaml("- name: Only\n  group: family\n  contacts:\n    - {date: 01.10.2026, type: call}\n"))
    friends = all_friends(session)
    assert [f.name for f in friends] == ["Only"]
    assert friends[0].group is Group.FAMILY
    assert history(session)[0].occurred_at == datetime(2026, 10, 1, 12, 0)


def test_from_yaml_accepts_hand_written_variants():
    records = from_yaml(
        "- name: Anna\n  group: close\n  interval: 30\n  contacts:\n"
        "    - date: 2026-10-01\n      type: msg\n    - {date: '2026-09-01 09:15:00', type: meeting}\n"
    )
    assert records[0].interval is None  # 30 is the Close friends default
    assert [c.type for c in records[0].contacts] == [InteractionType.MESSAGE, InteractionType.MEETING]
    assert records[0].contacts[1].on == datetime(2026, 9, 1, 9, 15)


@pytest.mark.parametrize(
    "text, message",
    [
        ("- group: family\n", "needs a 'name'"),
        ("- name: Anna\n", "needs a 'group'"),
        ("- name: Anna\n  group: strangers\n", "Unknown group"),
        ("- name: Anna\n  group: family\n  interval: soon\n", "interval"),
        ("- name: Anna\n  group: family\n  contacts:\n    - {type: call}\n", "needs 'date'"),
        ("- name: Anna\n  group: family\n  contacts:\n    - {date: 2026-10-01, type: imported_contact}\n", None),
    ],
)
def test_from_yaml_validation(text, message):
    if message is None:
        assert from_yaml(text)[0].contacts[0].type is InteractionType.IMPORTED_CONTACT
        return
    with pytest.raises(ValidationError, match=message):
        from_yaml(text)


def test_from_yaml_rejects_non_list_and_bad_yaml():
    with pytest.raises(FriendsError, match="list"):
        from_yaml("name: Anna\n")
    with pytest.raises(FriendsError, match="not valid YAML"):
        from_yaml("- name: [\n")
    with pytest.raises(FriendsError, match="not found"):
        read_snapshot(__import__("pathlib").Path("/nonexistent/friends.yaml"))
