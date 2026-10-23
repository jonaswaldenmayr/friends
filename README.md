```text
  ██▀▀▀  ██▀▀█▄  ▀█▀  ██▀▀▀  ██▄  ██  ██▀▀█▄  ▄█▀▀▀
  ██▄▄   ██▄▄█▀░  █░  ██▄▄   ██▀█▄██░ ██░ ██░ ▀█▄▄
  ██░░░  ██░▀█▄   █░  ██░░░  ██░ ▀██░ ██░ ██░   ░██
  ██░    ██░ ██░ ▄█▄  ██▄▄▄  ██░  ██░ ██▄▄█▀░ ▄▄▄█▀░
   ░░     ░░  ░░  ░░░  ░░░░░  ░░   ░░  ░░░░░   ░░░░   
```

A local-first command-line tool for keeping in touch with the people who matter, wherever they live.

It tells you who you haven't contacted in too long relative to how often you *want* to contact them. Everything runs locally in one SQLite file. There's no account, no cloud and no network access.

```
$ friends next -n 4
  #   Friend          Group             Days since   Interval   Overdue   Priority
 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  1   Sophie          Close friends             71        30d       +41       3.31
  2   Max             Inner circle              21        14d        +7       2.40
  3   Jørgen          Friends                   88        45d       +43       2.35
  4   Zoë             Extended circle          163        90d       +73       1.81

$ friends log sophie call
✓ Logged call with Sophie (today) · next due in 30 days
```

## Installation

You need [uv](https://docs.astral.sh/uv/). It installs Python 3.12+ for you if necessary.

```bash
cd path/to/friends
uv tool install .
friends help
```

This puts a `friends` executable on your `PATH` (usually `~/.local/bin`). It works from any directory. If the shell can't find it, run `uv tool update-shell` once.

### Updating

After pulling or editing the source:

```bash
uv tool install --reinstall .
```

Your data lives outside the project directory, so reinstalling never touches it.

### Uninstalling

```bash
uv tool uninstall friends    # the database file is kept
```

## Initial setup from your spreadsheet

1. In Excel/Numbers, export your sheet as **CSV UTF-8** (this keeps characters like ø and ü intact) and save it as `friends.csv`.
2. Preview what would happen:
   ```bash
   cd ~/where/the/csv/is
   friends import --dry-run
   ```
3. Import it for real:
   ```bash
   friends import
   ```
4. Check the result with `friends list` and `friends next`.

If you don't have a spreadsheet yet, run `friends export-template` to get an empty CSV with the right headers.

## CSV format

```
Friend,Group,Location,Interval,Last contact
Max,Inner circle,Freiburg,7,11.10.26
Paula,Inner circle,Helsinki,14,2026-10-11
Lutz,Friends,,,
```

| Column         | Required            | Notes |
|----------------|---------------------|-------|
| `Friend`       | yes                 | Display name. Use a distinguishing suffix for namesakes, e.g. `Johanne (Oslo)`. |
| `Group`        | for new friends     | `Family`, `Inner circle`, `Close friends`, `Friends`, `Extended circle`. Case-insensitive; unambiguous prefixes such as `close` work. |
| `Location`     | no                  | Free text. |
| `Interval`     | no                  | Desired days between contacts. Blank means the group default. |
| `Last contact` | no                  | `YYYY-MM-DD`, `DD.MM.YYYY`, `DD.MM.YY`, `DD/MM/YYYY`, `DD-MM-YYYY`. Dates are always day-first. |
| `ID`           | no (optional extra) | Stable friend ID, written by `friends export --with-ids`. |

The importer tolerates:

* header capitalization and surrounding whitespace
* a UTF-8 BOM, UTF-8 with or without BOM, and Windows-1252
* comma, semicolon or tab delimiters
* blank optional cells and fully empty rows (such as `,,,,` spacer rows)
* quoted values that contain the delimiter
* a time part after the date, which Excel sometimes adds

### Group defaults

| Group           | Default interval | Ranking weight |
|-----------------|------------------|----------------|
| Inner circle    | 14 days          | × 1.6          |
| Family          | 14 days          | × 1.4          |
| Close friends   | 30 days          | × 1.4          |
| Friends         | 60 days          | × 1.2          |
| Extended circle | 90 days          | × 1.0          |

The weight only applies once a friend is due. All weights are at least 1, so every due friend scores 1.0 or more and ranks above everyone who isn't due yet.

Weights are set in `_GROUP_WEIGHTS` in `src/friends/models.py`.

A friend's interval is either an individual override or the group default. An override equal to the group default isn't stored, so the friend keeps following the group default.

## Import reconciliation

The database is the source of truth for interaction history. The CSV is a convenient way to bulk-edit profiles and to seed or extend the history. Rows are reconciled like this:

1. **Matching.** A row matches a friend by its `ID` column if present, otherwise by normalized name. Matching ignores case and extra whitespace but not accents, so *Jørgen* and *Jorgen* are different people.
2. **New names** create a friend. A group is required for these.
3. **Existing friends** get their group, location and interval from the CSV.
4. **Blank cells never erase** existing values.
5. **`Last contact`** is a candidate interaction:
   * if it's newer than anything in the database, an `imported_contact` interaction is added and becomes the effective last contact;
   * if the database already has an interaction on that date (of any type), nothing is added;
   * if it's older than the latest known contact, it's kept as history (or dropped with `--ignore-older`). It can never replace a newer contact, because the effective last contact is always the newest interaction.
6. **Nothing is ever deleted** by an import.
7. **Idempotent.** Imported interactions get a deterministic ID derived from friend + date, so re-importing the same file changes nothing.
8. **Conflicts are reported, never merged:**
   * two CSV rows with the same normalized name;
   * a name that matches several friends in the database.

   Conflicting rows are skipped and the rest of the file is imported. To resolve a conflict, either rename people distinctly (`Anna (Berlin)`, `Anna (Oslo)`), or run `friends export --with-ids`, edit that file and re-import it so that rows match by ID.
9. **All or nothing.** Validation errors abort the whole import and nothing is written. Examples are an unknown group, a non-numeric interval, an invalid or future date, or a new friend without a group. The whole import runs in a single database transaction.

The summary lists friends created, updated and unchanged, interactions imported, older dates preserved or ignored, dates already known, conflicts and errors. It also shows a table of every change.

## Commands

Run `friends help` for an overview. To see options:

* `friends options` lists the arguments and options of every command in one table.
* `friends options log` lists them for one command.
* `friends help log` or `friends log --help` shows the full help page of one command.

| Command | What it does |
|---|---|
| `friends next [-n 10] [-g GROUP] [--all]` | Ranked list of who to contact. Snoozed friends are hidden unless you pass `--all`, which also removes the limit. Friends with no known contact are listed separately. |
| `friends log NAMES TYPE [-d DATE] [-m NOTE]` | Log a `meeting`, `call` or `message`. Separate several names with commas. Dates can be `today`, `yesterday`, ISO or European. Future dates need `--allow-future`. Logging the same friend, type and day twice is skipped unless you pass `--force`. |
| `friends undo` | Remove the most recently logged manual interaction. |
| `friends list [-g GROUP] [-l LOCATION] [-s name/group/location/last/priority/interval] [-r] [--snoozed] [--unknown] [--ids]` | All friends with group, location, interval, last contact and status. |
| `friends show NAME` | Profile and complete interaction history. |
| `friends history [-f NAME] [-t TYPE] [-n 20] [--all]` | Recent interactions, newest first. `TYPE` can also be `imported`. |
| `friends add [NAME] [-g …] [-l …] [-i DAYS] [--last DATE --via TYPE]` | Add a friend. Anything you don't pass as an option is asked for. |
| `friends edit NAME [--name …] [-g …] [-l …] [-i DAYS\|default] [--notes …]` | Edit a friend. With no options, every field is prompted. `-` clears a field. |
| `friends snooze NAME 30d\|2w\|3m\|DATE` / `friends snooze NAME --clear` | Hide someone from recommendations until a date. |
| `friends remove NAME` | Delete a friend and their history, after confirmation. |
| `friends digest [--md] [-o FILE] [--days 7]` | Weekly overview of overdue friends, friends due soon, unknown contacts, snoozes ending soon and recent contacts. Shown in the terminal or as Markdown. |
| `friends import [FILE] [--dry-run] [--ignore-older]` | Import or merge a CSV. Defaults to `./friends.csv`. |
| `friends export [-o friends_export.csv] [--with-ids] [--european] [--force]` | Export all friends in the import format. `Last contact` is the effective last contact. |
| `friends export-template [-o friends_template.csv] [--force]` | Write an empty CSV with the exact headers. |
| `friends path [NEW]` | Print the database location, or move the database there. |
| `friends snapshot [--file PATH]` | Write the YAML snapshot of everything; `--file` also remembers where it lives. |
| `friends restore [FILE] [-y]` | Replace the database with a snapshot. |
| `friends autocommit [off\|on\|push]` | Git-commit the snapshot after every change. |
| `friends style [plain\|shadow\|big]` | Show or switch the banner style. |
| `friends color [blue\|peach\|honey\|sunset]` | Show or switch the colour palette (`colour` works too). |
| `friends options [COMMAND]` | List the options of every command, or of one. |
| `friends help [COMMAND]` | Command overview, or the full help page of one command. |

**Name matching.** You never have to type full names. The lookup tries these in order: exact name, exact name without accents (`jorgen` → *Jørgen*), prefix (`han` → *Hanna*), word prefix, all words (`johanne stg` → *Johanne (Stg)*), substring, and finally an ID prefix. If several friends match, you choose from a numbered list.

**Ranking.** `ratio = days since last contact ÷ desired interval`. A ratio of 1.0 means due today and 2.0 means twice as long as you wanted. Before a friend is due, their priority is just the ratio. From the due date on, it's `ratio × group weight` (see the table below), so closer groups rise faster once they're overdue. Since every weight is at least 1, all due friends rank above everyone who isn't due yet. Ties are broken by days elapsed, then by name. Meetings, calls, messages and imported contacts all reset the same timer. Only the newest one counts.

## Daily usage

```bash
friends next                          # who's up?
friends log paula call                # just called Paula
friends log max,lukas,anna meeting    # dinner with three people
friends log sophie message -m "sent birthday wishes"
friends log mum call -d yesterday
friends snooze valeska 3m             # travelling, revisit in 3 months
friends digest                        # Sunday-evening overview
friends digest -o ~/Desktop/week.md   # ...or as Markdown
```

Only log a message when it was a meaningful exchange. A logged message resets the timer just like a meeting does.

## Where is my data?

```bash
friends path
```

| OS      | Default location |
|---------|------------------|
| macOS   | `~/Library/Application Support/friends/friends.db` |
| Linux   | `~/.local/share/friends/friends.db` |
| Windows | `%LOCALAPPDATA%\friends\friends.db` |

Set `FRIENDS_DB=/some/path/friends.db` to use a different file for one run, for example a test
database. To move the database for good, for example into a synced folder:

```bash
friends path ~/Sync/friends.db     # copies the current file there if it doesn't exist yet
```

Settings such as that path, the snapshot file, autocommit and the theme live in
`settings.yaml` in the same directory as the default database. `FRIENDS_CONFIG=/some/file`
overrides it for one run.

## Keeping your data in a private repo

The database is binary, so it makes a poor git history. Instead, `friends` can keep a
human-readable YAML **snapshot** of everything next to it and commit that after every change.
A tidy setup is a public tool repo plus a private data repo cloned into `data/`, which is
gitignored here:

```bash
gh repo create friends-data --private
git clone git@github.com:<you>/friends-data.git data
friends snapshot --file ~/Code/_tools/friends/data/friends.yaml   # write it and remember the path
friends autocommit on                                             # or: push
```

From then on every `log`, `add`, `edit`, `remove`, `snooze`, `undo` and `import` rewrites the
snapshot and commits it with a message like `friends log Sophie: call`. Only the snapshot file
is committed; other changes in that repo are left alone. A failed commit or push is shown as a
warning and never undoes the change itself.

The snapshot looks like this, one friend per item with their contacts underneath, oldest
first. It is fine to edit by hand:

```yaml
- name: Sophie
  id: 3f2a9c1e-…
  group: Close friends
  location: Berlin
  interval: 21               # only written when it differs from the group default
  snoozed_until: 2026-12-01
  contacts:
    - {date: 2026-06-10, type: imported}
    - {date: 2026-09-01, type: meeting, note: "dinner"}
    - {date: 2026-10-23 18:30:15, type: call}
```

To rebuild the database from it, on a new machine or after a mistake:

```bash
friends restore                              # from the configured snapshot (asks first)
friends restore ~/Downloads/friends.yaml     # from any file
git -C data checkout HEAD~3 -- friends.yaml && friends restore   # from an older revision
```

A restore replaces everything in the database. Friend IDs survive, imported contacts keep
their deterministic IDs so CSV re-imports stay idempotent, and other interaction IDs are
regenerated. Friend `created_at`/`updated_at` timestamps are not kept.

## Backup and restore

The database is a single SQLite file. Use any of these:

```bash
# 1. Copy the file (when friends isn't running)
cp "$(friends path)" ~/Backups/friends-$(date +%F).db

# 2. Consistent online copy with sqlite3
sqlite3 "$(friends path)" ".backup '$HOME/Backups/friends.db'"

# 3. Human-readable CSV (profiles + last contact, not the full history)
friends export -o ~/Backups/friends-$(date +%F).csv --with-ids

# 4. Human-readable YAML with the full history (see "Keeping your data in a private repo")
friends snapshot --file ~/Backups/friends.yaml
```

To restore:

```bash
# from a .db backup: copy it back over the database file
cp ~/Backups/friends-2026-10-11.db "$(friends path)"

# from a CSV backup: import into a fresh or existing database
friends import ~/Backups/friends-2026-10-11.csv

# from a YAML snapshot: replace the database with it
friends restore ~/Backups/friends.yaml
```

A file copy or a YAML snapshot keeps the complete interaction history. A CSV keeps only the
latest contact per friend.

## Banner and colours

`friends next`, `friends digest` and `friends help` start with a small FRIENDS banner. Three
styles: `plain` (default), `shadow` and `big`. Four palettes: `blue`, `peach`, `honey` and
`sunset`. The palette only changes the chrome (banner, rules, table titles and headers);
overdue red, due-soon yellow, on-track green and snoozed blue stay the same everywhere.

```sh
friends style            # show the current style and the available ones
friends style big        # switch: plain | shadow | big
friends color            # show the current palette
friends color peach      # switch: blue | peach | honey | sunset   (`friends colour` works too)
```

The palettes live in the `THEMES` dictionary at the top of `src/friends/ui.py`.

## Development

```bash
uv sync                      # create .venv with dev dependencies
uv run pytest                # run the test suite
uv run friends next          # run from source

# try things against a throwaway database
FRIENDS_DB=/tmp/demo.db uv run friends import examples/sample_friends.csv
FRIENDS_DB=/tmp/demo.db FRIENDS_TODAY=2026-12-01 uv run friends digest   # pretend it's another day
FRIENDS_CONFIG=/tmp/demo-settings.yaml uv run friends color peach          # keep settings out of the real file
```

### Architecture

```
src/friends/
  models.py        ORM models (Friend, Interaction) and enums (Group, InteractionType, Source)
  db.py            database location, engine, transactions
  services.py      friend CRUD, name lookup, effective last contact
  interactions.py  logging, duplicate protection, history queries
  ranking.py       priority engine (pure functions over FriendStatus)
  csv_io.py        CSV parsing, import reconciliation, export
  digest.py        weekly digest data + Markdown rendering
  snapshot.py      YAML snapshot: dump, parse, restore
  sync.py          after every change: rewrite the snapshot, optionally git commit/push it
  settings.py      settings.yaml: db and snapshot paths, autocommit, style, colour
  ui.py            Rich rendering (tables, panels, help, banner and themes)
  cli.py           Typer commands – thin glue
  dates.py, names.py, clock.py, errors.py
```

Everything except `cli.py` and `ui.py` is independent of the terminal. A future web dashboard or a messaging integration (for example Beeper) can import `friends.db`, `friends.services`, `friends.ranking` and `friends.interactions` directly and share the same database. An integration would log interactions with a new `Source` value.

The schema has two tables, `friends` and `interactions`, both keyed by UUID. The last contact isn't stored anywhere. It's always computed as `MAX(interactions.occurred_at)`, so it can't drift out of sync. Manual entries logged for today keep the actual time. Date-only entries are stored at 12:00 local time.
