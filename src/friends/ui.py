"""Rich rendering for the CLI. No database access happens here."""

from dataclasses import dataclass
from datetime import date

from rich import box
from rich.console import Console, Group as RenderGroup
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from friends.csv_io import ImportReport
from friends.digest import Digest
from friends.models import Friend, Interaction, Source
from friends.ranking import FriendStatus, Recommendations

console = Console()
err_console = Console(stderr=True)


# --------------------------------------------------------------------------
# Formatting helpers
# --------------------------------------------------------------------------


def ratio_style(ratio: float | None) -> str:
    if ratio is None:
        return "dim"
    if ratio >= 2:
        return "bold red"
    if ratio >= 1:
        return "red"
    if ratio >= 0.75:
        return "yellow"
    return "green"


def _days(n: int) -> str:
    return f"{n} day" if n == 1 else f"{n} days"


def fmt_date(day: date | None) -> str:
    return day.isoformat() if day else "—"


def fmt_ago(days: int | None) -> str:
    if days is None:
        return "unknown"
    if days == 0:
        return "today"
    if days == 1:
        return "yesterday"
    return f"{days} days ago"


def fmt_overdue(days: int | None) -> Text:
    if days is None:
        return Text("—", style="dim")
    if days > 0:
        return Text(f"+{days}", style="red")
    if days == 0:
        return Text("due today", style="yellow")
    return Text(f"in {-days}", style="dim")


def fmt_interval(status: FriendStatus) -> str:
    return f"{status.interval}d"


def success(message: str) -> None:
    console.print(f"[green]✓[/] {message}")


def warn(message: str) -> None:
    console.print(f"[yellow]![/] {message}")


def error(message: str) -> None:
    err_console.print(f"[bold red]Error:[/] {message}")


# --------------------------------------------------------------------------
# next
# --------------------------------------------------------------------------


def render_next(recs: Recommendations, *, show_snoozed: bool, title: str) -> None:
    if recs.ranked:
        table = Table(title=title, box=box.SIMPLE_HEAVY, title_justify="left", title_style="bold")
        table.add_column("#", justify="right", style="dim")
        table.add_column("Friend", style="bold")
        table.add_column("Group")
        table.add_column("Days since", justify="right")
        table.add_column("Interval", justify="right")
        table.add_column("Overdue", justify="right")
        table.add_column("Priority", justify="right")
        if show_snoozed:
            table.add_column("Snoozed")
        for index, s in enumerate(recs.ranked, 1):
            style = ratio_style(s.ratio)
            row = [
                str(index),
                s.name,
                s.group.label,
                str(s.days_since),
                fmt_interval(s),
                fmt_overdue(s.days_overdue),
                Text(f"{s.priority:.2f}", style=style),
            ]
            if show_snoozed:
                row.append(f"until {s.snoozed_until}" if s.is_snoozed else "")
            table.add_row(*row, style="dim" if s.is_snoozed else None)
        console.print(table)
    elif not recs.unknown:
        console.print("[dim]Nobody to show.[/]")

    if recs.unknown:
        names = ", ".join(
            f"{s.name} [dim]({s.group.label})[/]" + (" [dim]💤[/]" if s.is_snoozed else "")
            for s in recs.unknown
        )
        console.print(Panel(names, title="Unknown last contact", title_align="left", border_style="dim", expand=False))

    notes = []
    if recs.truncated:
        notes.append(f"{recs.truncated} more not shown (use --limit or --all)")
    if recs.hidden_snoozed:
        notes.append(f"{recs.hidden_snoozed} snoozed hidden")
    if notes:
        console.print("[dim]" + " · ".join(notes) + "[/]")


# --------------------------------------------------------------------------
# list / show / history
# --------------------------------------------------------------------------


def render_list(statuses: list[FriendStatus], *, show_ids: bool = False) -> None:
    table = Table(box=box.SIMPLE_HEAVY, title=f"{len(statuses)} friends", title_justify="left", title_style="bold")
    table.add_column("Friend", style="bold")
    table.add_column("Group")
    table.add_column("Location")
    table.add_column("Interval", justify="right")
    table.add_column("Last contact", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    if show_ids:
        table.add_column("ID", style="dim")
    for s in statuses:
        if s.is_snoozed:
            status = Text(f"snoozed until {s.snoozed_until}", style="blue")
        elif not s.known:
            status = Text("unknown", style="dim")
        elif s.is_due:
            status = Text(f"overdue {s.days_overdue}d" if s.days_overdue else "due today", style=ratio_style(s.ratio))
        else:
            status = Text(f"due in {-s.days_overdue}d", style=ratio_style(s.ratio))
        last = f"{fmt_date(s.last_contact)} [dim]{s.days_since}d ago[/]" if s.known else "[dim]—[/]"
        row = [s.name, s.group.label, s.location or "", fmt_interval(s), last, status]
        if show_ids:
            row.append(s.friend_id)
        table.add_row(*row)
    console.print(table)


def history_table(interactions: list[Interaction], *, show_friend: bool, title: str | None = None) -> Table:
    table = Table(box=box.SIMPLE, title=title, title_justify="left", title_style="bold")
    table.add_column("Date")
    if show_friend:
        table.add_column("Friend", style="bold")
    table.add_column("Type")
    table.add_column("Source", style="dim")
    table.add_column("Note")
    type_styles = {"meeting": "magenta", "call": "cyan", "message": "green", "imported_contact": "dim"}
    for i in interactions:
        row = [i.occurred_at.strftime("%Y-%m-%d %H:%M") if i.source == Source.MANUAL else i.occurred_on.isoformat()]
        if show_friend:
            row.append(i.friend.name)
        row += [
            Text(i.type.label, style=type_styles.get(i.type.value, "")),
            "manual" if i.source == Source.MANUAL else "CSV import",
            i.note or "",
        ]
        table.add_row(*row)
    return table


def render_profile(friend: Friend, status: FriendStatus, interactions: list[Interaction]) -> None:
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim")
    grid.add_column()
    grid.add_row("Group", friend.group.label)
    grid.add_row("Location", friend.location or "—")
    interval_note = "individual" if friend.interval_override is not None else "group default"
    grid.add_row("Interval", f"every {friend.interval} days ({interval_note})")
    if status.known:
        grid.add_row("Last contact", f"{fmt_date(status.last_contact)} ({fmt_ago(status.days_since)})")
        due = status.days_overdue
        when = f"overdue by {_days(due)}" if due > 0 else "today" if due == 0 else f"in {_days(-due)}"
        grid.add_row("Next due", f"{fmt_date(status.due_date)} ({when})")
        grid.add_row(
            "Priority",
            Text(f"{status.priority:.2f}", style=ratio_style(status.ratio))
            + Text(
                f"  ({status.ratio:.2f} × {status.group.weight:g} {status.group.label} weight)"
                if status.is_due
                else "  (not due yet – group weight applies once due)",
                style="dim",
            ),
        )
    else:
        grid.add_row("Last contact", "[dim]unknown[/]")
    if status.is_snoozed:
        grid.add_row("Snoozed", f"until {status.snoozed_until}")
    if friend.notes:
        grid.add_row("Notes", friend.notes)
    grid.add_row("ID", f"[dim]{friend.id}[/]")

    body = [grid]
    if interactions:
        body += [Text(""), history_table(interactions, show_friend=False, title=f"History ({len(interactions)})")]
    else:
        body += [Text(""), Text("No interactions recorded yet.", style="dim")]
    console.print(Panel(RenderGroup(*body), title=f"[bold]{friend.name}[/]", title_align="left", border_style="cyan", expand=False))


# --------------------------------------------------------------------------
# import
# --------------------------------------------------------------------------


def render_import(report: ImportReport) -> None:
    summary = Table.grid(padding=(0, 2))
    summary.add_column(style="dim")
    summary.add_column(justify="right")

    def count(label: str, value: int, style: str = "") -> None:
        summary.add_row(label, Text(str(value), style=style if value else "dim"))

    count("Friends created", report.created, "green")
    count("Friends updated", report.updated, "cyan")
    count("Friends unchanged", report.unchanged)
    count("Interactions imported", report.interactions_imported, "green")
    count("  new last contacts", report.new_last_contacts)
    count("  older dates preserved", report.older_preserved)
    if report.older_ignored:
        count("Older dates ignored", report.older_ignored, "yellow")
    count("Dates already known", report.already_known)
    count("Conflicts", len(report.conflicts), "yellow")
    count("Errors", len(report.errors), "bold red")

    title = "Import summary" + (" (dry run)" if report.dry_run else "")
    if report.path:
        title += f" · {report.path.name}"
    console.print(Panel(summary, title=title, title_align="left", border_style="cyan", expand=False))

    changed = [r for r in report.rows if r.action in {"created", "updated"}]
    if changed:
        table = Table(box=box.SIMPLE, title="Changes", title_justify="left", title_style="bold")
        table.add_column("Line", justify="right", style="dim")
        table.add_column("Friend", style="bold")
        table.add_column("Action")
        table.add_column("Details")
        for r in changed:
            details = "; ".join([*r.changes, *([r.contact] if r.contact else [])])
            table.add_row(str(r.line), r.name, Text(r.action, style="green" if r.action == "created" else "cyan"), details)
        console.print(table)

    for heading, issues, style in (
        ("Conflicts", report.conflicts, "yellow"),
        ("Errors", report.errors, "red"),
        ("Warnings", report.warnings, "dim"),
    ):
        if not issues:
            continue
        table = Table(box=box.SIMPLE, title=heading, title_justify="left", title_style=f"bold {style}")
        table.add_column("Line", justify="right", style="dim")
        table.add_column("Friend")
        table.add_column("Problem", style=style)
        for issue in issues:
            table.add_row(str(issue.line or ""), issue.name, issue.message)
        console.print(table)

    if report.errors:
        error("Import aborted – nothing was written. Fix the errors above and try again.")
    elif report.dry_run:
        warn("Dry run – no changes were written.")
    elif report.committed:
        success("Import complete.")


# --------------------------------------------------------------------------
# digest
# --------------------------------------------------------------------------


def render_digest(d: Digest) -> None:
    console.rule(f"[bold]Friends digest · {d.today:%A %d %B %Y}[/]")
    console.print(
        f"[dim]{d.total} friends · [green]{d.on_track} on track[/] · [red]{len(d.overdue)} overdue[/] · "
        f"{len(d.unknown)} unknown · {d.snoozed_count} snoozed[/]\n"
    )

    if d.overdue:
        table = Table(box=box.SIMPLE_HEAVY, title="Reach out this week", title_justify="left", title_style="bold red")
        table.add_column("#", justify="right", style="dim")
        table.add_column("Friend", style="bold")
        table.add_column("Group")
        table.add_column("Last contact")
        table.add_column("Overdue", justify="right")
        table.add_column("Priority", justify="right")
        for i, s in enumerate(d.overdue, 1):
            table.add_row(
                str(i), s.name, s.group.label, fmt_ago(s.days_since), fmt_overdue(s.days_overdue),
                Text(f"{s.priority:.2f}", style=ratio_style(s.ratio)),
            )
        console.print(table)
    else:
        console.print("[green]Nobody is overdue. 🎉[/]\n")

    if d.due_soon:
        console.print(f"[bold yellow]Coming up (next {d.horizon_days} days)[/]")
        for s in d.due_soon:
            console.print(f"  • [bold]{s.name}[/] [dim]({s.group.label})[/] – due {s.due_date:%a %d %b}")
        console.print()

    if d.unknown:
        console.print("[bold]Unknown last contact[/]")
        console.print("  " + ", ".join(s.name for s in d.unknown) + "\n")

    if d.snooze_ending:
        console.print("[bold blue]Snoozes ending soon[/]")
        for s in d.snooze_ending:
            console.print(f"  • {s.name} – back on {s.snoozed_until}")
        console.print()

    console.print(f"[bold]Contacts in the last {d.horizon_days} days[/] [dim]({len(d.recent)})[/]")
    if d.recent:
        for r in d.recent:
            console.print(f"  [dim]{r.on}[/] {r.name} [dim]· {r.type.label}[/]")
    else:
        console.print("  [dim]No interactions logged.[/]")


# --------------------------------------------------------------------------
# help
# --------------------------------------------------------------------------

HELP_SECTIONS: list[tuple[str, list[tuple[str, str, str]]]] = [
    (
        "Daily use",
        [
            ("next", "Who to contact next, ranked by priority", "friends next --limit 5"),
            ("log", "Log a meeting, call or message", "friends log max,anna meeting -d yesterday"),
            ("digest", "Weekly overview (terminal or Markdown)", "friends digest --output week.md"),
            ("undo", "Remove the last interaction you logged", "friends undo"),
        ],
    ),
    (
        "Friends",
        [
            ("list", "All friends with groups and last contact", "friends list --sort priority"),
            ("show", "Profile and full history of one friend", "friends show max"),
            ("history", "Recent interactions, newest first", "friends history --type call"),
            ("add", "Add a friend (interactive)", "friends add"),
            ("edit", "Change name, group, location, interval, notes", "friends edit max --interval 21"),
            ("snooze", "Hide someone from 'next' for a while", "friends snooze max 30d"),
            ("remove", "Delete a friend and their history", "friends remove max"),
        ],
    ),
    (
        "Spreadsheet",
        [
            ("import", "Import/merge a CSV (default ./friends.csv)", "friends import --dry-run"),
            ("export", "Export all friends to CSV", "friends export -o backup.csv"),
            ("export-template", "Write an empty CSV with the right headers", "friends export-template"),
        ],
    ),
    (
        "Other",
        [
            ("path", "Show where the database lives", "friends path"),
            ("options", "List the options of every command", "friends options log"),
            ("help", "This overview, or full help of a command", "friends help log"),
        ],
    ),
]


def command_order() -> list[str]:
    return [name for _, commands in HELP_SECTIONS for name, _, _ in commands]


@dataclass
class OptionRow:
    label: str
    help: str
    default: str | None
    is_argument: bool


def render_options(entries: list[tuple[str, str, list[OptionRow]]]) -> None:
    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    table.add_column(no_wrap=True)
    table.add_column()
    for index, (name, doc, rows) in enumerate(entries):
        if index:
            table.add_row()
        summary = doc.strip().splitlines()[0] if doc.strip() else ""
        table.add_row(Text(f"friends {name}", style="bold green"), Text(summary, style="dim"))
        if not rows:
            table.add_row(Text("  (no options)", style="dim"), "")
        for row in rows:
            text = Text(row.help)
            if row.default is not None:
                text.append(f"  [default: {row.default}]", style="dim")
            table.add_row(Text(f"  {row.label}", style="cyan" if row.is_argument else "yellow"), text)
    console.print(table)
    console.print("[dim]Every command also accepts -h/--help. Full page: friends help <command>[/]")


def render_help() -> None:
    console.print("[bold cyan]friends[/] – keep in touch with the people who matter\n")
    table = Table(box=None, show_header=False, padding=(0, 2))
    table.add_column(style="bold green", no_wrap=True)
    table.add_column()
    table.add_column(style="dim", no_wrap=True)
    for index, (section, commands) in enumerate(HELP_SECTIONS):
        if index:
            table.add_row()
        table.add_row(Text(section, style="bold"))
        for name, description, example in commands:
            table.add_row(f"  {name}", description, example)
    console.print(table)
    console.print(
        "\n[dim]Groups:   Inner circle (14d, ×1.6), Family (14d, ×1.4), Close friends (30d, ×1.4), "
        "Friends (60d, ×1.2), Extended circle (90d, ×1.0)\n"
        "Priority: days since contact ÷ interval; × group weight once due\nTypes:  meeting, call, message – each resets the contact timer\n"
        "Dates:  today, yesterday, 2026-10-01, 01.10.2026, 01.10.26\n"
        "Options: 'friends options' (all) · 'friends help <command>' (one).[/]"
    )
