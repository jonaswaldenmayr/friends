import csv
from datetime import date

from friends.csv_io import CSV_HEADERS, import_csv, import_text, write_export, write_template
from friends.db import Database
from friends.interactions import log_interaction
from friends.models import Group, InteractionType
from friends.services import all_friends, create_friend, last_contacts

from .conftest import TODAY


def snapshot(db):
    with db.session() as s:
        contacts = last_contacts(s)
        return sorted(
            (f.name, f.group, f.location, f.interval, contacts[f.id].date() if f.id in contacts else None)
            for f in all_friends(s)
        )


def seed(db):
    with db.session() as s:
        jorgen = create_friend(s, name="Jørgen Ødegård", group=Group.FRIENDS, location="Oslo", interval=21)
        create_friend(s, name="Zoë (Zürich)", group=Group.CLOSE_FRIENDS, location="Zürich")
        create_friend(s, name="李小龍", group=Group.EXTENDED_CIRCLE)
        log_interaction(s, jorgen, InteractionType.CALL, on=date(2026, 10, 3), today=TODAY)
        log_interaction(s, jorgen, InteractionType.MEETING, on=date(2026, 9, 1), today=TODAY)


def test_template_has_exact_headers_and_round_trips(db, tmp_path):
    path = tmp_path / "template.csv"
    write_template(path)
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")  # BOM so Excel detects UTF-8
    with path.open(encoding="utf-8-sig", newline="") as fh:
        assert next(csv.reader(fh)) == list(CSV_HEADERS)

    # Fill the template the way a user would, then import it.
    with path.open("a", encoding="utf-8", newline="") as fh:
        csv.writer(fh).writerow(["Max", "Inner circle", "Freiburg", "7", "11.10.26"])
    report = import_csv(db, path, today=TODAY)
    assert report.ok and report.created == 1


def test_empty_template_imports_cleanly(db, tmp_path):
    path = tmp_path / "template.csv"
    write_template(path)
    report = import_csv(db, path, today=TODAY)
    assert report.ok and report.created == 0


def test_export_contains_effective_last_contact(db, tmp_path):
    seed(db)
    path = tmp_path / "out.csv"
    with db.session() as s:
        assert write_export(s, path) == 3
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == list(CSV_HEADERS)
    assert ["Jørgen Ødegård", "Friends", "Oslo", "21", "2026-10-03"] in rows
    assert ["Zoë (Zürich)", "Close friends", "Zürich", "30", ""] in rows
    assert ["李小龍", "Extended circle", "", "90", ""] in rows


def test_export_and_reimport_into_fresh_database(db, tmp_path):
    seed(db)
    path = tmp_path / "backup.csv"
    with db.session() as s:
        write_export(s, path)

    fresh = Database(tmp_path / "fresh.db")
    report = import_csv(fresh, path, today=TODAY)
    assert report.ok and report.created == 3
    assert snapshot(fresh) == snapshot(db)

    # Re-importing the export into the original changes nothing.
    again = import_csv(db, path, today=TODAY)
    assert again.unchanged == 3 and again.interactions_imported == 0


def test_export_with_ids_and_european_dates(db, tmp_path):
    seed(db)
    path = tmp_path / "ids.csv"
    with db.session() as s:
        write_export(s, path, with_ids=True, date_style="european")
        ids = {f.name: f.id for f in all_friends(s)}
    text = path.read_text(encoding="utf-8-sig")
    assert text.splitlines()[0] == "Friend,Group,Location,Interval,Last contact,ID"
    assert f"Jørgen Ødegård,Friends,Oslo,21,03.10.2026,{ids['Jørgen Ødegård']}" in text

    # Renaming in the spreadsheet is applied when matched by ID.
    renamed = text.replace("Jørgen Ødegård", "Jørgen Ø.")
    report = import_text(db, renamed, today=TODAY)
    assert report.ok and report.created == 0
    names = [n for n, *_ in snapshot(db)]
    assert "Jørgen Ø." in names and "Jørgen Ødegård" not in names
