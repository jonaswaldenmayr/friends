"""Logging and querying meaningful interactions."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from friends.errors import ValidationError
from friends.models import MANUAL_TYPES, Friend, Interaction, InteractionType, Source

# Date-only entries are stored at midday so they never shift across a day boundary.
DATE_ONLY_TIME = time(12, 0)


@dataclass
class LogOutcome:
    friend: Friend
    interaction: Interaction | None
    duplicate_of: Interaction | None = None

    @property
    def logged(self) -> bool:
        return self.interaction is not None


def day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min)
    return start, start + timedelta(days=1)


def log_interaction(
    session: Session,
    friend: Friend,
    itype: InteractionType,
    *,
    on: date,
    today: date,
    now: datetime | None = None,
    note: str | None = None,
    allow_future: bool = False,
    allow_duplicate: bool = False,
) -> LogOutcome:
    """Record a meeting, call or message. All three reset the contact timer.

    An identical entry (same friend, type and day) is treated as an accidental
    resubmission and skipped unless ``allow_duplicate`` is set.
    """
    if itype not in MANUAL_TYPES:
        raise ValidationError(f"{itype.value!r} cannot be logged manually.")
    if on > today and not allow_future:
        raise ValidationError(
            f"{on.isoformat()} is in the future. Pass --allow-future if that is intended."
        )

    if not allow_duplicate:
        start, end = day_bounds(on)
        existing = session.scalars(
            select(Interaction).where(
                Interaction.friend_id == friend.id,
                Interaction.type == itype,
                Interaction.occurred_at >= start,
                Interaction.occurred_at < end,
            )
        ).first()
        if existing is not None:
            return LogOutcome(friend, None, existing)

    if on == today and now is not None and now.date() == today:
        occurred = now
    else:
        occurred = datetime.combine(on, DATE_ONLY_TIME)
    interaction = Interaction(
        friend_id=friend.id,
        type=itype,
        occurred_at=occurred,
        source=Source.MANUAL,
        note=(note or "").strip() or None,
    )
    session.add(interaction)
    session.flush()
    session.expire(friend, ["interactions"])
    return LogOutcome(friend, interaction)


def history(
    session: Session,
    *,
    friend_id: str | None = None,
    itype: InteractionType | None = None,
    limit: int | None = None,
) -> list[Interaction]:
    query = (
        select(Interaction)
        .options(joinedload(Interaction.friend))
        .order_by(Interaction.occurred_at.desc(), Interaction.created_at.desc())
    )
    if friend_id is not None:
        query = query.where(Interaction.friend_id == friend_id)
    if itype is not None:
        query = query.where(Interaction.type == itype)
    if limit is not None:
        query = query.limit(limit)
    return list(session.scalars(query))


def latest_manual(session: Session) -> Interaction | None:
    """The most recently *entered* manual interaction (for undo)."""
    return session.scalars(
        select(Interaction)
        .options(joinedload(Interaction.friend))
        .where(Interaction.source == Source.MANUAL)
        .order_by(Interaction.created_at.desc())
        .limit(1)
    ).first()
