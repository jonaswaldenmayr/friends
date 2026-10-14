"""Typer command-line interface. Thin layer over the application modules."""

import enum
import functools
import sys
from pathlib import Path
from typing import Annotated

import typer

from friends import clock
from friends.csv_io import import_csv, write_export, write_template
from friends.dates import parse_date, parse_snooze_until
from friends.db import Database, default_db_path
from friends.digest import build_digest, to_markdown
from friends.errors import FriendsError, ValidationError
from friends.interactions import history as load_history
from friends.interactions import latest_manual, log_interaction
from friends.models import Friend, Group, InteractionType
from friends.ranking import load_statuses, priority_key, recommend, status_for
from friends.services import (
    create_friend,
    delete_friend,
    friends_named,
    last_contact,
    resolve_friend,
    resolve_override,
)
from friends.ui import console, error, success, warn
from friends import ui

app = typer.Typer(
    name="friends",
    help="Keep in touch with the people who matter. Run [bold]friends help[/] for an overview.",
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["-h", "--help"]},
    add_completion=False,
    pretty_exceptions_show_locals=False,
)

GROUP_HELP = "Family, Inner circle, Close friends, Friends or Extended circle (prefixes like 'close' work)."


def open_db() -> Database:
    return Database(default_db_path())


def guard(fn):
    """Turn expected domain errors into a red message and exit code 1."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except FriendsError as exc:
            error(str(exc))
            raise typer.Exit(1) from None

    return wrapper


def choose_friend(query: str, candidates: list[Friend]) -> Friend:
    """Interactive disambiguation when a name matches several friends."""
    candidates = sorted(candidates, key=lambda f: (f.name.casefold(), f.id))
    console.print(f"[yellow]{query!r} matches {len(candidates)} friends:[/]")
    for index, friend in enumerate(candidates, 1):
        details = ", ".join(x for x in (friend.group.label, friend.location) if x)
        console.print(f"  [bold]{index}[/]. {friend.name} [dim]({details})[/]")
    choice = prompt_number("Which one? (0 to cancel)", 0, len(candidates))
    if choice == 0:
        raise FriendsError("Cancelled.")
    return candidates[choice - 1]


def prompt_number(label: str, low: int, high: int, default: int | None = None) -> int:
    """Ask for a number in [low, high] until a valid one is given."""
    while True:
        value = typer.prompt(label, type=int, default=default)
        if low <= value <= high:
            return value
        ui.warn(f"Please enter a number from {low} to {high}.")


def parse_group(value: str | None) -> Group | None:
    return Group.parse(value) if value else None


def confirm_overwrite(path: Path, force: bool) -> None:
    if path.exists() and not force:
        if not typer.confirm(f"{path} already exists. Overwrite?", default=False):
            raise FriendsError("Not overwritten. Use --force or choose another --output.")


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


# --------------------------------------------------------------------------
# Root / help
# --------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        ui.render_help()


def _subcommand(ctx: typer.Context, name: str):
    """Look up a sibling command by name, with a helpful error if unknown."""
    group = ctx.find_root().command
    command = group.get_command(ctx.find_root(), name)
    if command is None:
        known = ", ".join(ui.command_order())
        raise FriendsError(f"Unknown command {name!r}. Commands: {known}")
    return command


def _describe_params(command) -> list[ui.OptionRow]:
    rows = []
    for param in command.params:
        if getattr(param, "hidden", False):
            continue
        if param.param_type_name == "argument":
            label = param.metavar or param.name.upper()
        else:
            label = ", ".join([*param.opts, *param.secondary_opts])
            if not param.is_flag:
                choices = getattr(param.type, "choices", None)
                value = "|".join(str(getattr(c, "value", c)) for c in choices) if choices else None
                label += f" {param.metavar or value or param.name.rstrip('_').upper()}"
        default = None
        if not getattr(param, "is_flag", False) and param.default not in (None, (), []):
            default = str(getattr(param.default, "value", param.default))
        rows.append(ui.OptionRow(label, param.help or "", default, param.param_type_name == "argument"))
    return rows


@app.command("help")
@guard
def help_(
    ctx: typer.Context,
    command: Annotated[str | None, typer.Argument(help="Show the full help page of this command.")] = None,
) -> None:
    """Show an overview of all commands, or the full help of one command.

    [dim]Examples: friends help · friends help log[/]
    """
    if command is None:
        ui.render_help()
        return
    sub = _subcommand(ctx, command)
    sub_ctx = sub.context_class(sub, info_name=command, parent=ctx.find_root())
    typer.echo(sub.get_help(sub_ctx), nl=False)


@app.command("options")
@guard
def options(
    ctx: typer.Context,
    command: Annotated[str | None, typer.Argument(help="Only show this command.")] = None,
) -> None:
    """List the arguments and options of every command (or of one).

    [dim]Examples: friends options · friends options log[/]
    """
    names = [command] if command else ui.command_order()
    entries = [(name, _subcommand(ctx, name)) for name in names]
    ui.render_options([(name, cmd.help or "", _describe_params(cmd)) for name, cmd in entries])


@app.command("path")
def path_() -> None:
    """Show the location of the database file."""
    typer.echo(str(default_db_path()))


# --------------------------------------------------------------------------
# next
# --------------------------------------------------------------------------


@app.command("next")
@guard
def next_(
    limit: Annotated[int, typer.Option("--limit", "-n", min=1, help="How many friends to show.")] = 10,
    group: Annotated[str | None, typer.Option("--group", "-g", help=GROUP_HELP)] = None,
    all_: Annotated[bool, typer.Option("--all", "-a", help="Show everyone, including snoozed friends.")] = False,
) -> None:
    """Show who to contact next, ranked by days since contact / interval (× group weight once due).

    [dim]Examples: friends next · friends next -n 5 · friends next -g "close friends" · friends next --all[/]
    """
    selected = parse_group(group)
    today = clock.today()
    with open_db().session() as session:
        statuses = load_statuses(session, today)
    recs = recommend(statuses, group=selected, include_snoozed=all_, limit=None if all_ else limit)
    title = "Who to contact next" + (f" · {selected.label}" if selected else "")
    ui.render_next(recs, show_snoozed=all_, title=title)


# --------------------------------------------------------------------------
# log / undo
# --------------------------------------------------------------------------


@app.command()
@guard
def log(
    args: Annotated[
        list[str],
        typer.Argument(metavar="NAMES TYPE", help="Friend name(s), comma-separated, then meeting/call/message."),
    ],
    date: Annotated[str, typer.Option("--date", "-d", help="today, yesterday, YYYY-MM-DD or DD.MM.YYYY.")] = "today",
    note: Annotated[str | None, typer.Option("--note", "-m", help="Optional note.")] = None,
    allow_future: Annotated[bool, typer.Option("--allow-future", help="Permit dates after today.")] = False,
    force: Annotated[bool, typer.Option("--force", "-f", help="Log even if the same entry exists that day.")] = False,
) -> None:
    """Log a meaningful interaction. Meetings, calls and messages all reset the timer.

    [dim]Examples: friends log max call · friends log anna meeting --date yesterday ·
    friends log max,lukas,anna meeting · friends log "nora h" message -m "birthday"[/]
    """
    if len(args) < 2:
        raise ValidationError("Usage: friends log NAME[,NAME...] TYPE   e.g. friends log max call")
    try:
        itype = InteractionType.parse_manual(args[-1])
    except ValidationError as exc:
        raise ValidationError(f"{exc} The type comes last: friends log max call") from None
    queries = [q.strip() for q in " ".join(args[:-1]).split(",") if q.strip()]
    if not queries:
        raise ValidationError("Name at least one friend.")

    today = clock.today()
    on = parse_date(date, today)
    if on > today and not allow_future:
        raise ValidationError(f"{on.isoformat()} is in the future. Pass --allow-future if that is intended.")

    with open_db().session() as session:
        friends: list[Friend] = []
        for query in queries:
            friend = resolve_friend(session, query, choose_friend)
            if friend not in friends:
                friends.append(friend)
        outcomes = [
            log_interaction(
                session, friend, itype, on=on, today=today, now=clock.now(), note=note,
                allow_future=allow_future, allow_duplicate=force,
            )
            for friend in friends
        ]
        lasts = {f.id: last_contact(session, f.id) for f in friends}

    when = "today" if on == today else "yesterday" if (today - on).days == 1 else on.isoformat()
    for outcome in outcomes:
        friend = outcome.friend
        if outcome.logged:
            status = status_for(friend, lasts[friend.id], today)
            due = status.days_overdue
            nxt = f"next due in {-due} days" if due < 0 else "still due – log a newer date to reset"
            success(f"Logged [bold]{itype.label}[/] with [bold]{friend.name}[/] ({when}) [dim]· {nxt}[/]")
        else:
            warn(
                f"{itype.label.capitalize()} with {friend.name} on {on.isoformat()} is already logged – skipped "
                "[dim](use --force to log it again)[/]"
            )


@app.command()
@guard
def undo(yes: Annotated[bool, typer.Option("--yes", "-y", help="Don't ask for confirmation.")] = False) -> None:
    """Delete the most recently logged manual interaction."""
    with open_db().session() as session:
        interaction = latest_manual(session)
        if interaction is None:
            raise FriendsError("Nothing to undo.")
        label = f"{interaction.type.label} with {interaction.friend.name} on {interaction.occurred_on}"
        if not yes and not typer.confirm(f"Remove {label}?", default=True):
            raise FriendsError("Cancelled.")
        session.delete(interaction)
    success(f"Removed {label}.")


# --------------------------------------------------------------------------
# list / show / history
# --------------------------------------------------------------------------


class SortKey(enum.StrEnum):
    name = "name"
    group = "group"
    location = "location"
    last = "last"
    priority = "priority"
    interval = "interval"


@app.command("list")
@guard
def list_(
    group: Annotated[str | None, typer.Option("--group", "-g", help=GROUP_HELP)] = None,
    location: Annotated[str | None, typer.Option("--location", "-l", help="Filter by location (substring).")] = None,
    sort: Annotated[SortKey, typer.Option("--sort", "-s", help="Sort order.")] = SortKey.group,
    reverse: Annotated[bool, typer.Option("--reverse", "-r", help="Reverse the sort order.")] = False,
    snoozed: Annotated[bool, typer.Option("--snoozed", help="Only snoozed friends.")] = False,
    unknown: Annotated[bool, typer.Option("--unknown", help="Only friends without a known last contact.")] = False,
    ids: Annotated[bool, typer.Option("--ids", help="Show stable IDs.")] = False,
) -> None:
    """List all friends with group, location, interval and last contact.

    [dim]Examples: friends list · friends list --sort last · friends list -g family · friends list -l oslo[/]
    """
    selected = parse_group(group)
    with open_db().session() as session:
        statuses = load_statuses(session, clock.today())
    if selected:
        statuses = [s for s in statuses if s.group == selected]
    if location:
        needle = location.casefold()
        statuses = [s for s in statuses if s.location and needle in s.location.casefold()]
    if snoozed:
        statuses = [s for s in statuses if s.is_snoozed]
    if unknown:
        statuses = [s for s in statuses if not s.known]

    by_name = lambda s: s.name.casefold()  # noqa: E731
    keys = {
        SortKey.name: lambda s: by_name(s),
        SortKey.group: lambda s: (s.group.order, by_name(s)),
        SortKey.location: lambda s: ((s.location or "~").casefold(), by_name(s)),
        # Oldest contact first; unknown at the end.
        SortKey.last: lambda s: (s.last_contact is None, s.last_contact or s.today, by_name(s)),
        SortKey.priority: lambda s: (s.last_contact is None, priority_key(s)),
        SortKey.interval: lambda s: (s.interval, by_name(s)),
    }
    statuses.sort(key=keys[sort], reverse=reverse)
    if not statuses:
        console.print("[dim]No friends match.[/] Add some with [bold]friends add[/] or [bold]friends import[/].")
        return
    ui.render_list(statuses, show_ids=ids)


@app.command()
@guard
def show(name: Annotated[list[str], typer.Argument(help="Friend name (prefix is enough).")]) -> None:
    """Show a friend's profile and complete interaction history."""
    today = clock.today()
    with open_db().session() as session:
        friend = resolve_friend(session, " ".join(name), choose_friend)
        interactions = load_history(session, friend_id=friend.id)
        status = status_for(friend, last_contact(session, friend.id), today)
        ui.render_profile(friend, status, interactions)


@app.command()
@guard
def history(
    friend: Annotated[str | None, typer.Option("--friend", "-f", help="Only this friend.")] = None,
    type_: Annotated[str | None, typer.Option("--type", "-t", help="meeting, call, message or imported.")] = None,
    limit: Annotated[int, typer.Option("--limit", "-n", min=1, help="Maximum rows.")] = 20,
    all_: Annotated[bool, typer.Option("--all", "-a", help="Show everything.")] = False,
) -> None:
    """Show recent interactions, newest first.

    [dim]Examples: friends history · friends history -f max · friends history -t call -n 50[/]
    """
    itype = InteractionType.parse_filter(type_) if type_ else None
    with open_db().session() as session:
        friend_id = resolve_friend(session, friend, choose_friend).id if friend else None
        rows = load_history(session, friend_id=friend_id, itype=itype, limit=None if all_ else limit)
        if not rows:
            console.print("[dim]No interactions found.[/]")
            return
        console.print(ui.history_table(rows, show_friend=friend_id is None, title="Recent interactions"))


# --------------------------------------------------------------------------
# add / edit / remove / snooze
# --------------------------------------------------------------------------


def prompt_group(default: Group | None = None) -> Group:
    groups = list(Group)
    for index, group in enumerate(groups, 1):
        console.print(f"  [bold]{index}[/]. {group.label} [dim](every {group.default_interval} days)[/]")
    default_index = groups.index(default) + 1 if default else groups.index(Group.FRIENDS) + 1
    choice = prompt_number("Group", 1, len(groups), default_index)
    return groups[choice - 1]


def parse_interval_input(value: str, group: Group) -> int | None:
    """Returns an override (None means group default)."""
    text = value.strip().casefold()
    if text in {"", "default", "-"}:
        return None
    try:
        return resolve_override(group, int(text.rstrip("d")))
    except ValueError:
        raise ValidationError(f"Interval {value!r} must be a whole number of days or 'default'.") from None


@app.command()
@guard
def add(
    name: Annotated[str | None, typer.Argument(help="Name; prompted if omitted.")] = None,
    group: Annotated[str | None, typer.Option("--group", "-g", help=GROUP_HELP)] = None,
    location: Annotated[str | None, typer.Option("--location", "-l")] = None,
    interval: Annotated[int | None, typer.Option("--interval", "-i", min=1, help="Days; default from group.")] = None,
    notes: Annotated[str | None, typer.Option("--notes")] = None,
    last: Annotated[str | None, typer.Option("--last", help="Date of last contact, if known.")] = None,
    via: Annotated[str, typer.Option("--via", help="Type of that last contact.")] = "meeting",
) -> None:
    """Add a friend. Prompts for anything not given as an option.

    [dim]Examples: friends add · friends add "Anna (Berlin)" -g close -l Berlin --last 2026-09-01 --via call[/]
    """
    interactive = name is None
    if name is None:
        name = typer.prompt("Name").strip()
    chosen_group = parse_group(group) if group else (prompt_group() if interactive else Group.FRIENDS)
    if interactive:
        if location is None:
            location = typer.prompt("Location", default="", show_default=False).strip() or None
        if interval is None:
            text = typer.prompt("Interval in days", default=str(chosen_group.default_interval))
            interval = parse_interval_input(text, chosen_group)
        if last is None:
            last = typer.prompt("Last contact (blank if unknown)", default="", show_default=False).strip() or None
            if last:
                via = typer.prompt("How? (meeting/call/message)", default=via)

    today = clock.today()
    last_date = parse_date(last, today) if last else None
    itype = InteractionType.parse_manual(via)

    with open_db().session() as session:
        if friends_named(session, name):
            warn(f"A friend called {name!r} already exists. Consider a distinguishing name like '{name} (City)'.")
            if not typer.confirm("Add another person with the same name?", default=False):
                raise FriendsError("Cancelled.")
        friend = create_friend(session, name=name, group=chosen_group, location=location, interval=interval, notes=notes)
        if last_date:
            log_interaction(session, friend, itype, on=last_date, today=today, now=clock.now())
    success(f"Added [bold]{friend.name}[/] ({friend.group.label}, every {friend.interval} days).")


@app.command()
@guard
def edit(
    name: Annotated[str, typer.Argument(help="Friend to edit.")],
    new_name: Annotated[str | None, typer.Option("--name", help="Rename.")] = None,
    group: Annotated[str | None, typer.Option("--group", "-g", help=GROUP_HELP)] = None,
    location: Annotated[str | None, typer.Option("--location", "-l", help="'' or '-' clears it.")] = None,
    interval: Annotated[str | None, typer.Option("--interval", "-i", help="Days, or 'default'.")] = None,
    notes: Annotated[str | None, typer.Option("--notes", help="'' or '-' clears it.")] = None,
) -> None:
    """Edit a friend. Without options, prompts for each field.

    [dim]Examples: friends edit max --interval 21 · friends edit max -g inner · friends edit max --interval default[/]
    """
    with open_db().session() as session:
        friend = resolve_friend(session, name, choose_friend)
        if all(v is None for v in (new_name, group, location, interval, notes)):
            console.print(f"Editing [bold]{friend.name}[/] – press Enter to keep, '-' to clear.")
            new_name = typer.prompt("Name", default=friend.name)
            group_value = prompt_group(friend.group)
            location = typer.prompt("Location", default=friend.location or "", show_default=bool(friend.location))
            interval = typer.prompt(
                "Interval in days ('default' for group default)",
                default=str(friend.interval_override) if friend.interval_override else "default",
            )
            notes = typer.prompt("Notes", default=friend.notes or "", show_default=bool(friend.notes))
        else:
            group_value = parse_group(group)

        before = friend.interval
        if new_name is not None and new_name.strip() != friend.name:
            friend.name = new_name
        if group_value is not None:
            friend.group = group_value
        if location is not None:
            friend.location = None if location.strip() in {"", "-"} else location.strip()
        if notes is not None:
            friend.notes = None if notes.strip() in {"", "-"} else notes.strip()
        if interval is not None:
            friend.interval_override = parse_interval_input(interval, friend.group)
        elif group_value is not None and friend.interval_override == friend.group.default_interval:
            friend.interval_override = None
        session.flush()
        change = f" · interval {before}d → {friend.interval}d" if friend.interval != before else ""
    success(f"Updated [bold]{friend.name}[/] ({friend.group.label}, every {friend.interval} days){change}.")


@app.command()
@guard
def remove(
    name: Annotated[str, typer.Argument(help="Friend to delete.")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Don't ask for confirmation.")] = False,
) -> None:
    """Delete a friend and their whole interaction history."""
    with open_db().session() as session:
        friend = resolve_friend(session, name, choose_friend)
        count = len(friend.interactions)
        if not yes and not typer.confirm(
            f"Delete {friend.name} and {_plural(count, 'interaction')}? This cannot be undone.", default=False
        ):
            raise FriendsError("Cancelled.")
        friend_name = friend.name
        delete_friend(session, friend)
    success(f"Deleted {friend_name}.")


@app.command()
@guard
def snooze(
    name: Annotated[str, typer.Argument(help="Friend to snooze.")],
    duration: Annotated[str | None, typer.Argument(help="30d, 2w, 3m, a date, or 'off'.")] = None,
    clear: Annotated[bool, typer.Option("--clear", "-c", help="Remove the snooze.")] = False,
) -> None:
    """Temporarily hide a friend from recommendations.

    [dim]Examples: friends snooze max 30d · friends snooze max 2026-12-24 · friends snooze max --clear[/]
    """
    if duration and duration.strip().casefold() in {"off", "clear", "none", "0"}:
        clear = True
    if not clear and not duration:
        raise ValidationError("Give a duration like 30d, 2w or a date – or --clear.")
    today = clock.today()
    until = None if clear else parse_snooze_until(duration, today)
    with open_db().session() as session:
        friend = resolve_friend(session, name, choose_friend)
        friend.snoozed_until = until
    if until is None:
        success(f"{friend.name} is no longer snoozed.")
    else:
        success(f"Snoozed [bold]{friend.name}[/] until {until.isoformat()} ({(until - today).days} days).")


# --------------------------------------------------------------------------
# digest
# --------------------------------------------------------------------------


@app.command()
@guard
def digest(
    markdown: Annotated[bool, typer.Option("--markdown", "--md", help="Print Markdown instead of a rich view.")] = False,
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Write Markdown to this file.")] = None,
    days: Annotated[int, typer.Option("--days", min=1, help="Look-ahead/look-back window.")] = 7,
    force: Annotated[bool, typer.Option("--force", help="Overwrite --output without asking.")] = False,
) -> None:
    """Weekly overview of who to contact, in the terminal or as Markdown.

    [dim]Examples: friends digest · friends digest --md · friends digest -o weekly.md[/]
    """
    with open_db().session() as session:
        data = build_digest(session, clock.today(), horizon_days=days)
    if output:
        confirm_overwrite(output, force)
        output.write_text(to_markdown(data), encoding="utf-8")
        success(f"Digest written to {output}")
    elif markdown:
        typer.echo(to_markdown(data), nl=False)
    else:
        ui.render_digest(data)


# --------------------------------------------------------------------------
# CSV import / export
# --------------------------------------------------------------------------


@app.command("import")
@guard
def import_(
    path: Annotated[Path, typer.Argument(help="CSV file to import.")] = Path("friends.csv"),
    dry_run: Annotated[bool, typer.Option("--dry-run", "-n", help="Show what would change; write nothing.")] = False,
    ignore_older: Annotated[
        bool, typer.Option("--ignore-older", help="Don't keep CSV dates older than the latest known contact.")
    ] = False,
) -> None:
    """Import or merge friends from a CSV (default: ./friends.csv).

    Matches people by name (or ID column), updates group/location/interval,
    and adds 'Last contact' to the history without ever deleting anything.
    Safe to run repeatedly.

    [dim]Examples: friends import · friends import --dry-run · friends import ~/Downloads/friends.csv[/]
    """
    if not path.exists():
        hint = " Pass a path, or create one with 'friends export-template'." if path == Path("friends.csv") else ""
        raise FriendsError(f"{path} not found in {Path.cwd()}.{hint}")
    report = import_csv(open_db(), path, today=clock.today(), dry_run=dry_run, preserve_older=not ignore_older)
    ui.render_import(report)
    if not report.ok:
        raise typer.Exit(1)


@app.command()
@guard
def export(
    output: Annotated[Path, typer.Option("--output", "-o", help="Destination file.")] = Path("friends_export.csv"),
    force: Annotated[bool, typer.Option("--force", "-f", help="Overwrite without asking.")] = False,
    with_ids: Annotated[bool, typer.Option("--with-ids", help="Add an ID column for exact re-import matching.")] = False,
    european: Annotated[bool, typer.Option("--european", help="Write dates as DD.MM.YYYY instead of ISO.")] = False,
) -> None:
    """Export all friends to CSV (same format the importer reads).

    [dim]Examples: friends export · friends export -o backup.csv --with-ids[/]
    """
    confirm_overwrite(output, force)
    with open_db().session() as session:
        count = write_export(session, output, with_ids=with_ids, date_style="european" if european else "iso")
    success(f"Exported {_plural(count, 'friend')} to {output}")


@app.command("export-template")
@guard
def export_template(
    output: Annotated[Path, typer.Option("--output", "-o", help="Destination file.")] = Path("friends_template.csv"),
    force: Annotated[bool, typer.Option("--force", "-f", help="Overwrite without asking.")] = False,
) -> None:
    """Create an empty CSV with the headers the importer expects."""
    confirm_overwrite(output, force)
    write_template(output)
    success(f"Template written to {output}")


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        sys.exit(130)
