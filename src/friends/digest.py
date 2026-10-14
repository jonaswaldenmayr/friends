"""Weekly digest: a data structure plus a Markdown renderer.

The Rich (terminal) renderer lives in :mod:`friends.ui`.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy.orm import Session

from friends.interactions import history
from friends.models import InteractionType
from friends.ranking import FriendStatus, load_statuses, rank


@dataclass
class RecentContact:
    on: date
    name: str
    type: InteractionType


@dataclass
class Digest:
    today: date
    horizon_days: int
    total: int
    overdue: list[FriendStatus] = field(default_factory=list)
    due_soon: list[FriendStatus] = field(default_factory=list)
    unknown: list[FriendStatus] = field(default_factory=list)
    snooze_ending: list[FriendStatus] = field(default_factory=list)
    snoozed_count: int = 0
    recent: list[RecentContact] = field(default_factory=list)

    @property
    def period_end(self) -> date:
        return self.today + timedelta(days=self.horizon_days)

    @property
    def on_track(self) -> int:
        return self.total - len(self.overdue) - len(self.unknown) - self.snoozed_count


def build_digest(session: Session, today: date, horizon_days: int = 7) -> Digest:
    statuses = load_statuses(session, today)
    active = [s for s in statuses if not s.is_snoozed]
    snoozed = [s for s in statuses if s.is_snoozed]
    end = today + timedelta(days=horizon_days)

    digest = Digest(today=today, horizon_days=horizon_days, total=len(statuses))
    for status in rank(active):
        overdue = status.days_overdue
        if overdue >= 0:
            digest.overdue.append(status)
        elif -overdue <= horizon_days:
            digest.due_soon.append(status)
    digest.due_soon.sort(key=lambda s: (s.due_date, s.name.casefold()))
    digest.unknown = sorted((s for s in active if not s.known), key=lambda s: (s.group.order, s.name.casefold()))
    digest.snoozed_count = len(snoozed)
    digest.snooze_ending = sorted((s for s in snoozed if s.snoozed_until <= end), key=lambda s: s.snoozed_until)

    since = today - timedelta(days=horizon_days)
    for interaction in history(session):
        if interaction.occurred_on <= since:
            break
        digest.recent.append(RecentContact(interaction.occurred_on, interaction.friend.name, interaction.type))
    return digest


def _days(n: int) -> str:
    return f"{n} day" if n == 1 else f"{n} days"


def to_markdown(digest: Digest) -> str:
    d = digest
    lines = [
        f"# Friends digest – {d.today:%d %b %Y}",
        "",
        f"_Next {_days(d.horizon_days)} ({d.today.isoformat()} → {d.period_end.isoformat()}) · "
        f"{d.total} friends · {d.on_track} on track · {len(d.overdue)} overdue · "
        f"{len(d.unknown)} unknown · {d.snoozed_count} snoozed_",
        "",
        "## Reach out this week",
        "",
    ]
    if d.overdue:
        lines += [
            "| # | Friend | Group | Last contact | Interval | Overdue | Priority |",
            "|---|---|---|---|---|---|---|",
        ]
        for i, s in enumerate(d.overdue, 1):
            lines.append(
                f"| {i} | {s.name} | {s.group.label} | {_days(s.days_since)} ago | "
                f"{s.interval}d | {s.days_overdue:+d}d | {s.priority:.2f} |"
            )
    else:
        lines.append("Nobody is overdue. 🎉")

    lines += ["", f"## Coming up (due within {_days(d.horizon_days)})", ""]
    if d.due_soon:
        for s in d.due_soon:
            lines.append(f"- **{s.name}** ({s.group.label}) – due {s.due_date:%a %d %b}, in {_days(-s.days_overdue)}")
    else:
        lines.append("Nothing else coming due.")

    if d.unknown:
        lines += ["", "## Unknown last contact", ""]
        lines += [f"- {s.name} ({s.group.label})" for s in d.unknown]

    if d.snooze_ending:
        lines += ["", "## Snoozes ending soon", ""]
        lines += [f"- {s.name} – back on {s.snoozed_until.isoformat()}" for s in d.snooze_ending]

    lines += ["", f"## Contacts in the last {_days(d.horizon_days)}", ""]
    if d.recent:
        lines += [f"- {r.on.isoformat()} · {r.name} · {r.type.label}" for r in d.recent]
    else:
        lines.append("No interactions logged.")
    return "\n".join(lines) + "\n"
