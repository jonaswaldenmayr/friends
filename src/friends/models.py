"""ORM models and domain enums.

The schema is deliberately small:

* ``friends``       – one row per person, keyed by a stable UUID.
* ``interactions``  – append-only contact history.

The effective "last contact" is *derived* from ``interactions`` (MAX of
``occurred_at``) and never stored on the friend, so it cannot drift.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, validates

from friends.errors import ValidationError
from friends.names import clean_name, normalize_name


class Group(enum.StrEnum):
    FAMILY = "family"
    INNER_CIRCLE = "inner_circle"
    CLOSE_FRIENDS = "close_friends"
    FRIENDS = "friends"
    EXTENDED_CIRCLE = "extended_circle"

    @property
    def label(self) -> str:
        return _GROUP_LABELS[self]

    @property
    def default_interval(self) -> int:
        return _GROUP_INTERVALS[self]

    @property
    def weight(self) -> float:
        """Relevance factor multiplied into the priority (higher = ranked sooner)."""
        return _GROUP_WEIGHTS[self]

    @property
    def order(self) -> int:
        return list(Group).index(self)

    @classmethod
    def parse(cls, value: str) -> Group:
        """Accept labels, keys and unambiguous prefixes ("close", "inner")."""
        key = " ".join(value.replace("_", " ").replace("-", " ").split()).casefold()
        if not key:
            raise ValidationError("Group must not be empty.")
        for group in cls:
            if key == group.label.casefold():
                return group
        matches = [g for g in cls if g.label.casefold().startswith(key)]
        if len(matches) == 1:
            return matches[0]
        valid = ", ".join(g.label for g in cls)
        raise ValidationError(f"Unknown group {value!r}. Valid groups: {valid}.")


_GROUP_LABELS = {
    Group.FAMILY: "Family",
    Group.INNER_CIRCLE: "Inner circle",
    Group.CLOSE_FRIENDS: "Close friends",
    Group.FRIENDS: "Friends",
    Group.EXTENDED_CIRCLE: "Extended circle",
}

# Ranking order: Inner circle > Family = Close friends > Friends > Extended circle.
# All weights are >= 1 so an overdue friend (ratio >= 1) never drops below 1.0.
_GROUP_WEIGHTS = {
    Group.INNER_CIRCLE: 1.6,
    Group.FAMILY: 1.4,
    Group.CLOSE_FRIENDS: 1.4,
    Group.FRIENDS: 1.2,
    Group.EXTENDED_CIRCLE: 1.0,
}

_GROUP_INTERVALS = {
    Group.FAMILY: 14,
    Group.INNER_CIRCLE: 14,
    Group.CLOSE_FRIENDS: 30,
    Group.FRIENDS: 60,
    Group.EXTENDED_CIRCLE: 90,
}


class InteractionType(enum.StrEnum):
    MEETING = "meeting"
    CALL = "call"
    MESSAGE = "message"
    # Historical contact from a CSV where the kind of contact is unknown.
    IMPORTED_CONTACT = "imported_contact"

    @property
    def label(self) -> str:
        return "imported" if self is InteractionType.IMPORTED_CONTACT else self.value

    @classmethod
    def parse_manual(cls, value: str) -> InteractionType:
        """Parse a type the user may log by hand (never ``imported_contact``)."""
        found = _TYPE_ALIASES.get(value.strip().casefold())
        if found is None:
            raise ValidationError(
                f"Unknown interaction type {value!r}. Use one of: meeting, call, message."
            )
        return found

    @classmethod
    def parse_filter(cls, value: str) -> InteractionType:
        """Parse a type for filtering history (``imported`` allowed)."""
        if value.strip().casefold() in {"imported", "imported_contact", "import"}:
            return cls.IMPORTED_CONTACT
        return cls.parse_manual(value)


MANUAL_TYPES = (InteractionType.MEETING, InteractionType.CALL, InteractionType.MESSAGE)

_TYPE_ALIASES = {
    "meeting": InteractionType.MEETING,
    "meet": InteractionType.MEETING,
    "met": InteractionType.MEETING,
    "call": InteractionType.CALL,
    "called": InteractionType.CALL,
    "phone": InteractionType.CALL,
    "video": InteractionType.CALL,
    "message": InteractionType.MESSAGE,
    "msg": InteractionType.MESSAGE,
    "text": InteractionType.MESSAGE,
    "texted": InteractionType.MESSAGE,
    "chat": InteractionType.MESSAGE,
}


class Source(enum.StrEnum):
    MANUAL = "manual"
    CSV_IMPORT = "csv_import"


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _enum_column(enum_cls: type[enum.Enum]) -> Enum:
    return Enum(
        enum_cls,
        native_enum=False,
        length=32,
        values_callable=lambda members: [m.value for m in members],
        validate_strings=True,
    )


class Base(DeclarativeBase):
    pass


class Friend(Base):
    __tablename__ = "friends"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    normalized_name: Mapped[str] = mapped_column(String(200), index=True)
    group: Mapped[Group] = mapped_column("group_name", _enum_column(Group))
    location: Mapped[str | None] = mapped_column(String(200))
    interval_override: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)
    snoozed_until: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    interactions: Mapped[list[Interaction]] = relationship(
        back_populates="friend",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by=lambda: Interaction.occurred_at.desc(),
    )

    @validates("name")
    def _validate_name(self, _key: str, value: str) -> str:
        cleaned = clean_name(value)
        if not cleaned:
            raise ValidationError("Name must not be empty.")
        self.normalized_name = normalize_name(cleaned)
        return cleaned

    @property
    def interval(self) -> int:
        """Desired contact interval: individual override, else group default."""
        if self.interval_override is not None:
            return self.interval_override
        return self.group.default_interval

    def is_snoozed(self, today: date) -> bool:
        return self.snoozed_until is not None and self.snoozed_until > today

    def __repr__(self) -> str:
        return f"Friend({self.name!r}, {self.group.value})"


class Interaction(Base):
    __tablename__ = "interactions"
    __table_args__ = (Index("ix_interactions_friend_occurred", "friend_id", "occurred_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    friend_id: Mapped[str] = mapped_column(ForeignKey("friends.id", ondelete="CASCADE"))
    type: Mapped[InteractionType] = mapped_column(
        "interaction_type", _enum_column(InteractionType)
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime)
    source: Mapped[Source] = mapped_column(_enum_column(Source))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    friend: Mapped[Friend] = relationship(back_populates="interactions")

    @property
    def occurred_on(self) -> date:
        return self.occurred_at.date()
