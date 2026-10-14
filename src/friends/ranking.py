"""Deterministic contact-priority engine.

ratio    = days_since_last_contact / desired_interval_days
priority = ratio                    while not yet due (ratio < 1)
           ratio * group_weight     once due (ratio >= 1)

The group weight (Inner circle 1.6, Family/Close friends 1.4, Friends 1.2,
Extended 1.0) only applies to friends who are due, so closer groups rise
faster once overdue. Since weights are >= 1, every due friend scores >= 1.0
and therefore always ranks above everyone who is not yet due.

Friends with no known contact are reported separately rather than being
treated as infinitely overdue.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from friends.models import Friend, Group
from friends.services import all_friends, last_contacts


@dataclass(frozen=True, slots=True)
class FriendStatus:
    friend_id: str
    name: str
    group: Group
    location: str | None
    interval: int
    interval_is_override: bool
    last_contact: date | None
    snoozed_until: date | None
    today: date

    @property
    def known(self) -> bool:
        return self.last_contact is not None

    @property
    def days_since(self) -> int | None:
        if self.last_contact is None:
            return None
        # Future-dated interactions (explicitly allowed) count as "today".
        return max(0, (self.today - self.last_contact).days)

    @property
    def ratio(self) -> float | None:
        days = self.days_since
        return None if days is None else days / self.interval

    @property
    def priority(self) -> float | None:
        ratio = self.ratio
        if ratio is None:
            return None
        return ratio * self.group.weight if self.is_due else ratio

    @property
    def days_overdue(self) -> int | None:
        days = self.days_since
        return None if days is None else days - self.interval

    @property
    def due_date(self) -> date | None:
        if self.last_contact is None:
            return None
        return self.last_contact + timedelta(days=self.interval)

    @property
    def is_snoozed(self) -> bool:
        return self.snoozed_until is not None and self.snoozed_until > self.today

    @property
    def is_due(self) -> bool:
        overdue = self.days_overdue
        return overdue is not None and overdue >= 0


def status_for(friend: Friend, last: datetime | date | None, today: date) -> FriendStatus:
    if isinstance(last, datetime):
        last = last.date()
    return FriendStatus(
        friend_id=friend.id,
        name=friend.name,
        group=friend.group,
        location=friend.location,
        interval=friend.interval,
        interval_is_override=friend.interval_override is not None,
        last_contact=last,
        snoozed_until=friend.snoozed_until,
        today=today,
    )


def load_statuses(session: Session, today: date) -> list[FriendStatus]:
    contacts = last_contacts(session)
    return [status_for(f, contacts.get(f.id), today) for f in all_friends(session)]


def priority_key(status: FriendStatus) -> tuple:
    """Highest priority first; ties broken by days elapsed, then name."""
    return (-(status.priority or 0.0), -(status.days_since or 0), status.name.casefold())


def rank(statuses: list[FriendStatus]) -> list[FriendStatus]:
    return sorted((s for s in statuses if s.known), key=priority_key)


@dataclass
class Recommendations:
    ranked: list[FriendStatus] = field(default_factory=list)
    unknown: list[FriendStatus] = field(default_factory=list)
    hidden_snoozed: int = 0
    truncated: int = 0


def recommend(
    statuses: list[FriendStatus],
    *,
    group: Group | None = None,
    include_snoozed: bool = False,
    limit: int | None = None,
) -> Recommendations:
    pool = [s for s in statuses if group is None or s.group == group]
    hidden = 0 if include_snoozed else sum(1 for s in pool if s.is_snoozed)
    if not include_snoozed:
        pool = [s for s in pool if not s.is_snoozed]
    ranked = rank(pool)
    truncated = 0
    if limit is not None and len(ranked) > limit:
        truncated = len(ranked) - limit
        ranked = ranked[:limit]
    unknown = sorted((s for s in pool if not s.known), key=lambda s: (s.group.order, s.name.casefold()))
    return Recommendations(ranked, unknown, hidden, truncated)
