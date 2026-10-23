"""Human-readable YAML snapshot of the whole database, meant for a private git repo.

One list item per friend (sorted by name), contacts nested underneath, oldest first::

    - name: Sophie
      id: 3f2a9c1e-...
      group: Close friends
      location: Berlin
      interval: 21            # only when it differs from the group default
      snoozed_until: 2026-12-01
      contacts:
        - {date: 2026-06-10, type: imported}
        - {date: 2026-09-01, type: meeting, note: "dinner"}
        - {date: 2026-10-23 18:30:15, type: call}

The SQLite database stays the working store. The snapshot is rewritten after
every change (see :mod:`friends.sync`) and ``friends restore`` rebuilds the
database from it. The round trip is exact for everything the user can see.
Not kept: friend ``created_at``/``updated_at`` and interaction IDs, which are
regenerated on restore (imported contacts get their deterministic ID back, so
CSV re-imports stay idempotent). A date-only ``date`` means 12:00, like everywhere else.
"""

import json
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from friends.csv_io import imported_interaction_id
from friends.dates import parse_calendar_date
from friends.errors import FriendsError, ValidationError
from friends.interactions import DATE_ONLY_TIME
from friends.models import Friend, Group, Interaction, InteractionType, Source
from friends.services import resolve_override

HEADER = "# friends snapshot. Rewritten after every change; rebuild the database with: friends restore\n"


@dataclass
class ContactRecord:
    on: datetime
    type: InteractionType
    note: str | None = None


@dataclass
class FriendRecord:
    name: str
    group: Group
    id: str | None = None
    location: str | None = None
    interval: int | None = None  # override only; None follows the group default
    notes: str | None = None
    snoozed_until: date | None = None
    contacts: list[ContactRecord] = field(default_factory=list)


# --------------------------------------------------------------------------
# Database -> records -> YAML
# --------------------------------------------------------------------------


def dump_friends(session: Session) -> list[FriendRecord]:
    friends = session.scalars(
        select(Friend).options(selectinload(Friend.interactions)).order_by(Friend.normalized_name, Friend.id)
    )
    records = []
    for friend in friends:
        contacts = sorted(friend.interactions, key=lambda i: (i.occurred_at, i.created_at, i.id))
        records.append(
            FriendRecord(
                name=friend.name,
                group=friend.group,
                id=friend.id,
                location=friend.location,
                interval=friend.interval_override,
                notes=friend.notes,
                snoozed_until=friend.snoozed_until,
                contacts=[ContactRecord(i.occurred_at, i.type, i.note) for i in contacts],
            )
        )
    return records


def _yaml(value, **kwargs) -> str:
    return yaml.safe_dump(value, sort_keys=False, allow_unicode=True, width=10**6, **kwargs)


def _contact_line(contact: ContactRecord) -> str:
    # Hand-built flow mapping: PyYAML would quote the time (colons) in flow style, and the
    # key must not be `on`, which YAML 1.1 reads as the boolean True. Notes are JSON-quoted,
    # which is valid YAML and survives any character.
    when = contact.on.date().isoformat() if contact.on.time() == DATE_ONLY_TIME else contact.on.isoformat(" ")
    fields = [f"date: {when}", f"type: {contact.type.label}"]
    if contact.note:
        fields.append(f"note: {json.dumps(contact.note, ensure_ascii=False)}")
    return "{" + ", ".join(fields) + "}"


def to_yaml(records: list[FriendRecord]) -> str:
    chunks = [HEADER]
    for r in records:
        head = {"name": r.name, "id": r.id, "group": r.group.label}
        if r.location:
            head["location"] = r.location
        if r.interval is not None:
            head["interval"] = r.interval
        if r.notes:
            head["notes"] = r.notes
        if r.snoozed_until:
            head["snoozed_until"] = r.snoozed_until
        # Each friend is serialised on its own so untouched friends never change between saves.
        chunks.append(_yaml([head]))
        if r.contacts:
            chunks.append("  contacts:\n")
            chunks.extend(f"    - {_contact_line(c)}\n" for c in r.contacts)
    return "".join(chunks)


def write_snapshot(session: Session, path: Path) -> int:
    """Write the snapshot; returns the number of friends written."""
    records = dump_friends(session)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(to_yaml(records), encoding="utf-8")
    except OSError as exc:
        raise FriendsError(f"Cannot write snapshot to {path}: {exc.strerror}") from None
    return len(records)


# --------------------------------------------------------------------------
# YAML -> records -> database
# --------------------------------------------------------------------------


def _as_datetime(value, where: str) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, DATE_ONLY_TIME)
    if isinstance(value, str):
        text = value.strip()
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return datetime.combine(parse_calendar_date(text), DATE_ONLY_TIME)
    raise ValidationError(f"{where}: 'date' must be a date, got {value!r}.")


def _as_date(value, where: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return parse_calendar_date(value)
    raise ValidationError(f"{where}: expected a date, got {value!r}.")


def _text(value) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def from_yaml(text: str) -> list[FriendRecord]:
    try:
        data = yaml.safe_load(text) or []
    except yaml.YAMLError as exc:
        raise FriendsError(f"Snapshot is not valid YAML: {exc}") from None
    if not isinstance(data, list):
        raise FriendsError("Snapshot must be a YAML list with one item per friend.")

    records = []
    for index, item in enumerate(data, 1):
        where = f"friend {index}"
        if not isinstance(item, dict) or not _text(item.get("name")):
            raise ValidationError(f"{where} needs a 'name'.")
        name = _text(item["name"])
        where = f"{name!r}"
        if not _text(item.get("group")):
            raise ValidationError(f"{where} needs a 'group'.")
        group = Group.parse(str(item["group"]))
        interval = item.get("interval")
        if interval is not None:
            try:
                interval = resolve_override(group, int(interval))
            except (TypeError, ValueError):
                raise ValidationError(f"{where}: interval {interval!r} must be a number of days.") from None
        snoozed = item.get("snoozed_until")
        contacts = item.get("contacts") or []
        if not isinstance(contacts, list):
            raise ValidationError(f"{where}: 'contacts' must be a list.")
        parsed_contacts = []
        for c in contacts:
            if not isinstance(c, dict) or "date" not in c:
                raise ValidationError(f"{where}: each contact needs 'date' and 'type', got {c!r}.")
            itype = InteractionType.parse_filter(str(c.get("type") or "imported"))
            parsed_contacts.append(ContactRecord(_as_datetime(c["date"], where), itype, _text(c.get("note"))))
        records.append(
            FriendRecord(
                name=name,
                group=group,
                id=_text(item.get("id")),
                location=_text(item.get("location")),
                interval=interval,
                notes=_text(item.get("notes")),
                snoozed_until=_as_date(snoozed, where) if snoozed else None,
                contacts=parsed_contacts,
            )
        )
    return records


def read_snapshot(path: Path) -> list[FriendRecord]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise FriendsError(f"Snapshot not found: {path}") from None
    except OSError as exc:
        raise FriendsError(f"Cannot read {path}: {exc.strerror}") from None
    return from_yaml(text)


def restore(session: Session, records: list[FriendRecord]) -> tuple[int, int]:
    """Replace everything in the database with the records. Returns (friends, contacts)."""
    for friend in session.scalars(select(Friend)):
        session.delete(friend)
    session.flush()

    contacts = 0
    for r in records:
        friend = Friend(
            name=r.name,
            group=r.group,
            location=r.location,
            interval_override=r.interval,
            notes=r.notes,
            snoozed_until=r.snoozed_until,
        )
        if r.id:
            friend.id = r.id
        session.add(friend)
        session.flush()
        for c in r.contacts:
            imported = c.type is InteractionType.IMPORTED_CONTACT
            session.add(
                Interaction(
                    id=imported_interaction_id(friend.id, c.on.date()) if imported else None,
                    friend_id=friend.id,
                    type=c.type,
                    occurred_at=c.on,
                    source=Source.CSV_IMPORT if imported else Source.MANUAL,
                    note=c.note,
                    created_at=c.on,  # keeps `friends undo` picking the newest contact
                )
            )
            contacts += 1
    session.flush()
    return len(records), contacts
