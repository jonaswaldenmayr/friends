from datetime import date, datetime

import pytest

from friends.csv_io import import_csv, import_text, parse_csv
from friends.interactions import history, log_interaction
from friends.models import Friend, Group, InteractionType, Source
from friends.services import all_friends, create_friend, last_contacts

from .conftest import TODAY

HEADER = "Friend,Group,Location,Interval,Last contact\n"


def run(db, text, **kwargs):
    return import_text(db, text, today=TODAY, **kwargs)


def friends_by_name(db):
    with db.session() as s:
        contacts = last_contacts(s)
        return {
            f.name: (f.group, f.location, f.interval, contacts.get(f.id).date() if f.id in contacts else None)
            for f in all_friends(s)
        }


def interaction_count(db):
    with db.session() as s:
        return len(history(s))


def test_basic_import_creates_friends(db):
    report = run(
        db,
        HEADER
        + "Max,Inner circle,Freiburg,7,11.10.26\n"
        + "Paula,Inner circle,Helsinki,14,2026-10-01\n"
        + "Lutz,Friends,,,\n",
    )
    assert report.ok and report.committed
    assert (report.created, report.updated, report.unchanged) == (3, 0, 0)
    assert report.interactions_imported == 2
    assert friends_by_name(db) == {
        "Max": (Group.INNER_CIRCLE, "Freiburg", 7, date(2026, 10, 11)),
        "Paula": (Group.INNER_CIRCLE, "Helsinki", 14, date(2026, 10, 1)),
        "Lutz": (Group.FRIENDS, None, 60, None),
    }
    with db.session() as s:
        (imported,) = history(s, itype=InteractionType.IMPORTED_CONTACT, limit=1)
        assert imported.source == Source.CSV_IMPORT
        max_ = next(f for f in all_friends(s) if f.name == "Max")
        assert max_.interval_override == 7
        paula = next(f for f in all_friends(s) if f.name == "Paula")
        assert paula.interval_override is None  # 14 == group default


def test_repeated_import_is_idempotent(db):
    text = HEADER + "Max,Inner circle,Freiburg,7,11.10.26\nAnna,Friends,Oslo,,01.09.26\n"
    first = run(db, text)
    assert first.created == 2
    snapshot = friends_by_name(db)
    for _ in range(3):
        again = run(db, text)
        assert (again.created, again.updated, again.unchanged) == (0, 0, 2)
        assert again.interactions_imported == 0
        assert again.already_known == 2
    assert friends_by_name(db) == snapshot
    assert interaction_count(db) == 2


def test_newer_database_date_wins(db):
    run(db, HEADER + "Max,Friends,,,01.09.26\n")
    with db.session() as s:
        (max_,) = all_friends(s)
        log_interaction(s, max_, InteractionType.CALL, on=date(2026, 10, 5), today=TODAY)

    report = run(db, HEADER + "Max,Friends,,,15.09.26\n")
    assert report.older_preserved == 1 and report.new_last_contacts == 0
    assert friends_by_name(db)["Max"][3] == date(2026, 10, 5)
    assert interaction_count(db) == 3  # 01.09 + call 05.10 + preserved 15.09


def test_older_dates_can_be_ignored(db):
    run(db, HEADER + "Max,Friends,,,01.10.26\n")
    report = run(db, HEADER + "Max,Friends,,,01.09.26\n", preserve_older=False)
    assert report.older_ignored == 1
    assert interaction_count(db) == 1
    assert friends_by_name(db)["Max"][3] == date(2026, 10, 1)


def test_newer_csv_date_wins(db):
    run(db, HEADER + "Max,Friends,,,01.09.26\n")
    with db.session() as s:
        (max_,) = all_friends(s)
        log_interaction(s, max_, InteractionType.MEETING, on=date(2026, 9, 10), today=TODAY)
    report = run(db, HEADER + "Max,Friends,,,08.10.26\n")
    assert report.new_last_contacts == 1 and report.updated == 1
    assert friends_by_name(db)["Max"][3] == date(2026, 10, 8)
    with db.session() as s:
        assert len(history(s)) == 3  # nothing deleted


def test_same_date_as_manual_interaction_not_duplicated(db):
    run(db, HEADER + "Max,Friends,,,\n")
    with db.session() as s:
        (max_,) = all_friends(s)
        log_interaction(s, max_, InteractionType.CALL, on=date(2026, 10, 5), today=TODAY)
    report = run(db, HEADER + "Max,Friends,,,05.10.2026\n")
    assert report.already_known == 1 and report.interactions_imported == 0
    assert interaction_count(db) == 1


def test_profile_updates_and_blank_fields_preserve_values(db):
    run(db, HEADER + "Max,Friends,Freiburg,21,01.09.26\n")
    report = run(db, HEADER + "max ,Close friends,,,\n")
    assert report.updated == 1
    (row,) = report.rows
    assert any("group" in c for c in row.changes)
    group, location, interval, last = friends_by_name(db)["Max"]
    assert group == Group.CLOSE_FRIENDS
    assert location == "Freiburg"  # blank did not erase
    assert interval == 21  # individual override kept
    assert last == date(2026, 9, 1)

    run(db, HEADER + "Max,Close friends,Berlin,30,\n")
    assert friends_by_name(db)["Max"][1:3] == ("Berlin", 30)


def test_empty_rows_and_whitespace_tolerated(db):
    text = (
        "﻿  friend ; GROUP;Location ;  interval;LAST CONTACT \r\n"
        ";;;;\r\n"
        "  Anna  ; close friends ; Oslo ; 30 ; 01.10.26 \r\n"
        ";;;;\r\n"
    )
    report = run(db, text)
    assert report.ok and report.created == 1
    assert friends_by_name(db) == {"Anna": (Group.CLOSE_FRIENDS, "Oslo", 30, date(2026, 10, 1))}


def test_file_encodings(db, write_csv):
    text = HEADER + "Jørgen,Friends,Oslo,21,10.10.26\nJoão,Friends,Lisboa,,\n"
    for encoding in ("utf-8", "utf-8-sig", "cp1252"):
        path = write_csv(text, f"f-{encoding}.csv", encoding)
        report = import_csv(db, path, today=TODAY)
        assert report.ok, report.errors
    assert set(friends_by_name(db)) == {"Jørgen", "João"}


def test_semicolon_excel_export(db, write_csv):
    path = write_csv("Friend;Group;Location;Interval;Last contact\r\nLuc;Friends;Paris, FR;60;1/8/2026\r\n")
    report = import_csv(db, path, today=TODAY)
    assert report.ok
    assert friends_by_name(db)["Luc"] == (Group.FRIENDS, "Paris, FR", 60, date(2026, 8, 1))


def test_quoted_commas_in_comma_file(db):
    report = run(db, HEADER + '"Smith, Anna",Friends,"Oslo, NO",,\n')
    assert report.ok
    assert friends_by_name(db)["Smith, Anna"][1] == "Oslo, NO"


def test_duplicate_names_in_csv_are_conflicts(db):
    report = run(db, HEADER + "Anna,Friends,Oslo,,\nanna ,Family,Berlin,,\nBob,Friends,,,\n")
    assert report.ok
    assert len(report.conflicts) == 2
    assert report.created == 1
    assert set(friends_by_name(db)) == {"Bob"}


def test_ambiguous_database_names_are_conflicts(db):
    with db.session() as s:
        create_friend(s, name="Anna", group=Group.FRIENDS, location="Oslo")
        create_friend(s, name="Anna", group=Group.FRIENDS, location="Berlin")
    report = run(db, HEADER + "Anna,Family,,,01.10.26\n")
    assert len(report.conflicts) == 1
    assert report.updated == 0 and report.interactions_imported == 0
    with db.session() as s:
        assert {f.group for f in all_friends(s)} == {Group.FRIENDS}


def test_id_column_disambiguates_same_names(db):
    with db.session() as s:
        oslo = create_friend(s, name="Anna", group=Group.FRIENDS, location="Oslo").id
        berlin = create_friend(s, name="Anna", group=Group.FRIENDS, location="Berlin").id
    report = run(
        db,
        "Friend,Group,Location,Interval,Last contact,ID\n"
        f"Anna,Family,,,01.10.26,{oslo}\n"
        f"Anna,,,,05.10.26,{berlin}\n",
    )
    assert report.ok and not report.conflicts and report.updated == 2
    with db.session() as s:
        contacts = last_contacts(s)
        assert s.get(Friend, oslo).group == Group.FAMILY
        assert contacts[oslo].date() == date(2026, 10, 1)
        assert contacts[berlin].date() == date(2026, 10, 5)


def test_invalid_import_rolls_back_everything(db):
    run(db, HEADER + "Max,Friends,,,01.09.26\n")
    before = (friends_by_name(db), interaction_count(db))
    report = run(
        db,
        HEADER
        + "Max,Family,Berlin,7,05.10.26\n"  # valid update...
        + "New,Friends,,,\n"  # ...valid create...
        + "Broken,Friends,,abc,31.02.26\n",  # ...invalid row
    )
    assert not report.ok and not report.committed
    assert {e.name for e in report.errors} == {"Broken"}
    assert (friends_by_name(db), interaction_count(db)) == before


def test_new_friend_without_group_aborts_import(db):
    report = run(db, HEADER + "Max,Friends,,,\nNoGroup,,,,\n")
    assert not report.ok and not report.committed
    assert friends_by_name(db) == {}


def test_future_csv_date_is_an_error(db):
    report = run(db, HEADER + "Max,Friends,,,12.10.26\n")
    assert not report.ok
    assert friends_by_name(db) == {}


def test_missing_friend_column(db):
    report = run(db, "Name2,Group\nMax,Friends\n")
    assert not report.ok
    assert "Missing 'Friend' column" in report.errors[0].message


def test_dry_run_writes_nothing(db):
    report = run(db, HEADER + "Max,Friends,,,01.09.26\n", dry_run=True)
    assert report.ok and not report.committed and report.created == 1
    assert friends_by_name(db) == {}
    real = run(db, HEADER + "Max,Friends,,,01.09.26\n")
    assert real.created == 1


def test_imported_ids_are_deterministic(db):
    run(db, HEADER + "Max,Friends,,,01.09.26\n")
    with db.session() as s:
        (interaction,) = history(s)
        from friends.csv_io import imported_interaction_id

        assert interaction.id == imported_interaction_id(interaction.friend_id, date(2026, 9, 1))
        assert interaction.occurred_at == datetime(2026, 9, 1, 12, 0)


def test_parse_reports_warnings_for_nameless_rows():
    records, errors, warnings, conflicts = parse_csv(HEADER + ",Friends,Oslo,,\nMax,Friends,,,\n", TODAY)
    assert [r.name for r in records] == ["Max"]
    assert not errors and not conflicts
    assert len(warnings) == 1
