"""CSV import (with reconciliation) and export.

Format: ``Friend,Group,Location,Interval,Last contact`` plus an optional
``ID`` column carrying the friend's stable UUID.

Reconciliation rules (the database owns interaction history):

* Friends are matched by ``ID`` if present, else by normalised name.
* Unknown friends are created; known ones get group/location/interval from
  the CSV. Blank cells never erase existing values.
* ``Last contact`` becomes an ``imported_contact`` interaction unless that
  date is already recorded. Older dates are kept as history but can never
  replace a newer effective contact, because the effective contact is
  always the newest interaction.
* Imported interaction IDs are derived from (friend, date), so re-importing
  the same file is idempotent.
* Any validation error aborts the whole import inside one transaction.
"""

import csv
import io
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from friends.dates import parse_calendar_date
from friends.db import Database
from friends.errors import FriendsError, ValidationError
from friends.models import Friend, Group, Interaction, InteractionType, Source
from friends.names import clean_name, normalize_name
from friends.services import all_friends, last_contacts, resolve_override

CSV_HEADERS = ("Friend", "Group", "Location", "Interval", "Last contact")
ID_HEADER = "ID"

# Fixed namespace: imported interaction IDs are uuid5(namespace, friend:date).
IMPORT_NAMESPACE = uuid.UUID("6f1c6a52-3f0e-4c4e-9d55-0d6b5b7f2a10")

_HEADER_ALIASES = {
    "friend": "friend",
    "name": "friend",
    "group": "group",
    "location": "location",
    "interval": "interval",
    "interval days": "interval",
    "interval (days)": "interval",
    "last contact": "last_contact",
    "lastcontact": "last_contact",
    "last contacted": "last_contact",
    "last contact date": "last_contact",
    "id": "id",
    "uuid": "id",
}


@dataclass
class CsvRecord:
    line: int
    name: str
    group: Group | None
    location: str | None
    interval: int | None
    last_contact: date | None
    friend_id: str | None = None

    @property
    def key(self) -> str:
        return normalize_name(self.name)


@dataclass
class Issue:
    line: int | None
    name: str
    message: str


@dataclass
class RowResult:
    line: int
    name: str
    action: str  # created | updated | unchanged | conflict
    changes: list[str] = field(default_factory=list)
    contact: str | None = None


@dataclass
class ImportReport:
    path: Path | None
    dry_run: bool
    committed: bool = False
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    new_last_contacts: int = 0
    older_preserved: int = 0
    older_ignored: int = 0
    already_known: int = 0
    rows: list[RowResult] = field(default_factory=list)
    conflicts: list[Issue] = field(default_factory=list)
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)

    @property
    def interactions_imported(self) -> int:
        return self.new_last_contacts + self.older_preserved

    @property
    def ok(self) -> bool:
        return not self.errors


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


def read_csv_text(path: Path) -> str:
    """Read a CSV as text: UTF-8 (with or without BOM), else Windows-1252."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise FriendsError(f"File not found: {path}") from None
    except OSError as exc:
        raise FriendsError(f"Cannot read {path}: {exc.strerror}") from None
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def detect_delimiter(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            counts = {d: line.count(d) for d in (",", ";", "\t")}
            best = max(counts, key=lambda d: counts[d])
            return best if counts[best] else ","
    return ","


def _header_key(header: str) -> str:
    return " ".join(header.replace("﻿", "").replace("_", " ").split()).casefold()


def parse_interval(value: str) -> int:
    text = value.strip().casefold()
    for suffix in ("days", "day", "d"):
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
            break
    try:
        number = float(text.replace(",", "."))
    except ValueError:
        raise ValidationError(f"Interval {value!r} is not a number of days.") from None
    if not number.is_integer() or number <= 0:
        raise ValidationError(f"Interval {value!r} must be a positive whole number of days.")
    return int(number)


def parse_csv(text: str, today: date) -> tuple[list[CsvRecord], list[Issue], list[Issue], list[Issue]]:
    """Parse and validate CSV text.

    Returns ``(records, errors, warnings, conflicts)``. Conflicting rows
    (several rows for the same person) are removed from ``records``.
    """
    errors: list[Issue] = []
    warnings: list[Issue] = []
    reader = csv.reader(io.StringIO(text), delimiter=detect_delimiter(text))

    columns: dict[str, int] | None = None
    records: list[CsvRecord] = []
    for row in reader:
        line = reader.line_num
        if not any(cell.strip() for cell in row):
            continue

        if columns is None:
            columns = {}
            for index, header in enumerate(row):
                key = _HEADER_ALIASES.get(_header_key(header))
                if key is None:
                    if header.strip():
                        warnings.append(Issue(line, "", f"Ignoring unknown column {header.strip()!r}."))
                    continue
                if key in columns:
                    errors.append(Issue(line, "", f"Column {header.strip()!r} appears twice."))
                columns[key] = index
            if "friend" not in columns:
                errors.append(
                    Issue(line, "", f"Missing 'Friend' column. Expected headers: {','.join(CSV_HEADERS)}")
                )
                return [], errors, warnings, []
            continue

        def cell(key: str) -> str:
            index = columns.get(key)
            if index is None or index >= len(row):
                return ""
            return row[index].strip()

        name = clean_name(cell("friend"))
        if not name:
            warnings.append(Issue(line, "", "Row has data but no friend name – skipped."))
            continue

        record = CsvRecord(line, name, None, cell("location") or None, None, None)
        try:
            if raw := cell("group"):
                record.group = Group.parse(raw)
            if raw := cell("interval"):
                record.interval = parse_interval(raw)
            if raw := cell("last_contact"):
                record.last_contact = parse_calendar_date(raw)
                if record.last_contact > today:
                    raise ValidationError(f"Last contact {raw!r} is in the future.")
            if raw := cell("id"):
                try:
                    record.friend_id = str(uuid.UUID(raw))
                except ValueError:
                    raise ValidationError(f"ID {raw!r} is not a valid UUID.") from None
        except ValidationError as exc:
            errors.append(Issue(line, name, str(exc)))
            continue
        records.append(record)

    if columns is None:
        errors.append(Issue(None, "", "The file is empty."))
        return [], errors, warnings, []

    records, conflicts = _split_duplicates(records)
    return records, errors, warnings, conflicts


def _split_duplicates(records: list[CsvRecord]) -> tuple[list[CsvRecord], list[Issue]]:
    """Rows that claim the same identity are reported, never merged."""
    by_identity: dict[tuple[str, str], list[CsvRecord]] = defaultdict(list)
    for record in records:
        identity = ("id", record.friend_id) if record.friend_id else ("name", record.key)
        by_identity[identity].append(record)

    conflicts: list[Issue] = []
    clashing: set[int] = set()
    for (kind, _value), group in by_identity.items():
        if len(group) < 2:
            continue
        lines = ", ".join(str(r.line) for r in group)
        what = "the same ID" if kind == "id" else "the same name"
        for record in group:
            clashing.add(record.line)
            conflicts.append(
                Issue(
                    record.line,
                    record.name,
                    f"Rows {lines} have {what}. Rename one (e.g. 'Anna (Berlin)') "
                    "or add an ID column to tell them apart.",
                )
            )
    return [r for r in records if r.line not in clashing], conflicts


# --------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------


def import_csv(
    db: Database,
    path: Path,
    *,
    today: date,
    dry_run: bool = False,
    preserve_older: bool = True,
) -> ImportReport:
    return import_text(
        db, read_csv_text(path), today=today, dry_run=dry_run, preserve_older=preserve_older, path=path
    )


def import_text(
    db: Database,
    text: str,
    *,
    today: date,
    dry_run: bool = False,
    preserve_older: bool = True,
    path: Path | None = None,
) -> ImportReport:
    report = ImportReport(path=path, dry_run=dry_run)
    records, errors, warnings, conflicts = parse_csv(text, today)
    report.errors.extend(errors)
    report.warnings.extend(warnings)
    report.conflicts.extend(conflicts)
    for issue in conflicts:
        report.rows.append(RowResult(issue.line or 0, issue.name, "conflict"))
    if report.errors:
        return report

    session = db.Session()
    try:
        _reconcile(session, records, report, preserve_older)
        if report.errors or dry_run:
            session.rollback()
        else:
            session.commit()
            report.committed = True
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
    report.rows.sort(key=lambda r: r.line)
    return report


def _reconcile(session: Session, records: list[CsvRecord], report: ImportReport, preserve_older: bool) -> None:
    friends = list(session.scalars(select(Friend).options(selectinload(Friend.interactions))))
    by_id = {f.id: f for f in friends}
    by_name: dict[str, list[Friend]] = defaultdict(list)
    for friend in friends:
        by_name[friend.normalized_name].append(friend)

    for record in records:
        friend: Friend | None
        if record.friend_id:
            friend = by_id.get(record.friend_id)
        else:
            matches = by_name.get(record.key, [])
            if len(matches) > 1:
                report.conflicts.append(
                    Issue(
                        record.line,
                        record.name,
                        f"{len(matches)} friends in the database are named {record.name!r}. "
                        "Add an ID column (see 'friends export --with-ids') or rename them.",
                    )
                )
                report.rows.append(RowResult(record.line, record.name, "conflict"))
                continue
            friend = matches[0] if matches else None

        if friend is None:
            if record.group is None:
                report.errors.append(Issue(record.line, record.name, "New friend needs a Group."))
                continue
            friend = Friend(
                name=record.name,
                group=record.group,
                location=record.location,
                interval_override=resolve_override(record.group, record.interval),
            )
            if record.friend_id:
                friend.id = record.friend_id
            session.add(friend)
            session.flush()
            result = RowResult(record.line, friend.name, "created")
        else:
            result = RowResult(record.line, friend.name, "unchanged", _apply_profile(friend, record))

        added = _reconcile_contact(session, friend, record, report, result, preserve_older)
        if result.action != "created" and (result.changes or added):
            result.action = "updated"
        setattr(report, result.action, getattr(report, result.action) + 1)
        report.rows.append(result)


def _apply_profile(friend: Friend, record: CsvRecord) -> list[str]:
    changes: list[str] = []
    if record.friend_id and record.name != friend.name:
        changes.append(f"name {friend.name} → {record.name}")
        friend.name = record.name
    if record.group is not None and record.group != friend.group:
        changes.append(f"group {friend.group.label} → {record.group.label}")
        friend.group = record.group
    if record.location and record.location != friend.location:
        changes.append(f"location {friend.location or '—'} → {record.location}")
        friend.location = record.location
    if record.interval is not None:
        override = resolve_override(friend.group, record.interval)
        if override != friend.interval_override:
            before = friend.interval
            friend.interval_override = override
            if friend.interval != before:
                changes.append(f"interval {before}d → {friend.interval}d")
    return changes


def imported_interaction_id(friend_id: str, day: date) -> str:
    return str(uuid.uuid5(IMPORT_NAMESPACE, f"{friend_id}:{day.isoformat()}"))


def _reconcile_contact(
    session: Session,
    friend: Friend,
    record: CsvRecord,
    report: ImportReport,
    result: RowResult,
    preserve_older: bool,
) -> bool:
    day = record.last_contact
    if day is None:
        return False
    known_days = {i.occurred_on for i in friend.interactions}
    if day in known_days:
        report.already_known += 1
        result.contact = f"{day.isoformat()} already recorded"
        return False

    latest = max(known_days, default=None)
    older = latest is not None and day < latest
    if older and not preserve_older:
        report.older_ignored += 1
        result.contact = f"{day.isoformat()} older than {latest.isoformat()} – ignored"
        return False

    interaction_id = imported_interaction_id(friend.id, day)
    if session.get(Interaction, interaction_id) is not None:  # defensive; covered above
        report.already_known += 1
        return False
    session.add(
        Interaction(
            id=interaction_id,
            friend=friend,
            type=InteractionType.IMPORTED_CONTACT,
            occurred_at=datetime.combine(day, time(12, 0)),
            source=Source.CSV_IMPORT,
        )
    )
    if older:
        report.older_preserved += 1
        result.contact = f"{day.isoformat()} kept as history (newer: {latest.isoformat()})"
    else:
        report.new_last_contacts += 1
        result.contact = f"last contact → {day.isoformat()}"
    return True


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------


def format_csv_date(day: date | None, style: str = "iso") -> str:
    if day is None:
        return ""
    return day.strftime("%d.%m.%Y") if style == "european" else day.isoformat()


def write_template(path: Path) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        csv.writer(fh).writerow(CSV_HEADERS)


def write_export(session: Session, path: Path, *, with_ids: bool = False, date_style: str = "iso") -> int:
    """Write all friends in the import format. Returns the number of rows."""
    contacts = last_contacts(session)
    friends = sorted(all_friends(session), key=lambda f: (f.group.order, f.normalized_name))
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([*CSV_HEADERS, *([ID_HEADER] if with_ids else [])])
        for friend in friends:
            last = contacts.get(friend.id)
            row = [
                friend.name,
                friend.group.label,
                friend.location or "",
                friend.interval,
                format_csv_date(last.date() if last else None, date_style),
            ]
            if with_ids:
                row.append(friend.id)
            writer.writerow(row)
    return len(friends)
