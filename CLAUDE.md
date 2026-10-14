# CLAUDE.md

Guidance for working on this repository. User-facing docs are in `README.md`.

## What this is

`friends` is a local-first Python CLI for keeping in touch with roughly 30–60 friends in different countries. It stores everything in SQLite and makes no network calls. It's a personal tool with one user, so prefer correctness, simplicity and maintainability over abstractions.

Stack: Python 3.12+, uv, Typer, Rich, SQLAlchemy 2.x (ORM), platformdirs and pytest. The build backend is hatchling with a src layout. The executable is `friends`, mapped to `friends.cli:main`.

## Commands

```bash
uv sync                               # install deps + dev group
uv run pytest                         # full suite (~1.5 s); must stay green
uv run pytest -W error                # also passes; keep it warning-free
uv run friends <cmd>                  # run from source
uv tool install --reinstall .         # update the user's installed `friends` after changes
```

**Never touch the user's real database during development or demos.** Always point at a scratch file:

```bash
FRIENDS_DB=/tmp/x.db FRIENDS_TODAY=2026-10-11 COLUMNS=110 uv run friends import
```

* `FRIENDS_DB` overrides the database path. The real one is in the platformdirs user data dir; on macOS that's `~/Library/Application Support/friends/friends.db`.
* `FRIENDS_TODAY=YYYY-MM-DD` pins "today". It's read via `friends.clock`, and code must use `clock.today()` / `clock.now()`, never `date.today()`.
* `COLUMNS` controls Rich's width when output isn't a TTY.

`./friends.csv` at the repo root is the user's real spreadsheet export and is gitignored. Treat it as personal data. It's fine to import it into a scratch database.

## Layout

```
src/friends/
  models.py        Friend, Interaction ORM; enums Group, InteractionType, Source;
                   group labels, default intervals (_GROUP_INTERVALS) and ranking
                   weights (_GROUP_WEIGHTS)
  db.py            Database(path): engine, create_all, PRAGMA foreign_keys,
                   session() context = one transaction (commit / rollback / dry_run)
  services.py      friend CRUD, find_friends/resolve_friend (name lookup),
                   last_contacts() (derived), resolve_override()
  interactions.py  log_interaction (dup + future checks), history, latest_manual
  ranking.py       FriendStatus (pure dataclass), load_statuses, rank, recommend
  csv_io.py        parse_csv, import_text/import_csv (reconciliation), write_export, write_template
  digest.py        Digest dataclass + to_markdown
  ui.py            all Rich rendering + HELP_SECTIONS (command overview/order)
  cli.py           Typer commands only; thin glue
  dates.py, names.py, clock.py, errors.py
tests/             pytest; conftest has db/session/make_friend/write_csv/cli fixtures
examples/sample_friends.csv   demo data (semicolon, European dates, BOM)
```

Only `cli.py` and `ui.py` know about the terminal. Keep application logic out of them so a future web dashboard or Beeper integration can reuse `services`, `ranking`, `interactions` and `csv_io` directly. Neither of those is in scope right now.

## Core rules and invariants

* **Last contact is derived** from `MAX(interactions.occurred_at)` and never stored on `Friend`.
* **All interaction types reset the same timer:** `meeting`, `call`, `message`, and the internal `imported_contact`. `imported_contact` comes only from CSV imports and is never accepted by `friends log`.
* **Ranking** (in `ranking.py`):
  * `ratio = days_since / interval`
  * `priority = ratio` while not due (ratio < 1), and `ratio × group.weight` once due (ratio ≥ 1). The user asked for the weight to apply only to overdue friends. Lists are sorted by priority descending, so every due friend (≥ 1.0) ranks above every not-yet-due friend (< 1.0).
  * ties are broken by days_since descending, then name
  * the group weights are Inner circle 1.6, Family 1.4, Close friends 1.4, Friends 1.2, Extended circle 1.0. The user chose these values explicitly. Weights must stay ≥ 1 so an overdue friend never scores below 1.0.
  * "overdue/due" and the colors use the **unweighted** ratio (≥ 1.0 means due)
  * friends with no known contact aren't ranked and are shown separately
  * snoozed friends (`snoozed_until > today`) are hidden by default
* **Intervals:** `interval_override` is stored only when it differs from the group default (`resolve_override`). Otherwise the friend follows the group default.
* **Timestamps:** manual entries for today use the actual time. Date-only entries are stored at 12:00. Local naive datetimes are used for `occurred_at`; `created_at` and `updated_at` are naive UTC.
* **Duplicate protection:** the same friend, type and day is skipped unless `--force`. Future dates need `--allow-future`.
* **Name identity vs search:**
  * `normalize_name` (NFC, casefold, collapsed whitespace) is the identity key, and accents count.
  * `fold_name` also strips accents and is used only for search.
  * Lookup stages: exact → folded exact → prefix → word prefix → all-words prefix → substring → UUID prefix.
  * Several matches go to the interactive chooser.

## CSV import reconciliation (most important feature)

* The headers are `Friend,Group,Location,Interval,Last contact`, plus an optional `ID` column.
* Parsing is tolerant: case and whitespace in headers, BOM, UTF-8 or cp1252, `,` `;` or tab delimiters, empty rows, and day-first dates (`DD.MM.YY` is what the user's Excel produces).
* Matching uses the ID column if present, otherwise the normalized name.
* Blank cells never erase existing values. A new friend requires a group.
* A `Last contact` date that's already recorded is skipped. A newer date is added and becomes the last contact. An older one is kept as history, or dropped with `--ignore-older`.
* Imports never delete anything.
* Imported interaction IDs are `uuid5(IMPORT_NAMESPACE, f"{friend_id}:{date}")`, which makes re-imports idempotent.
* Duplicate names within the CSV and ambiguous database names are **conflicts**: they're reported and skipped, never merged.
* Any validation **error** aborts the whole import (one transaction, rolled back). Dry runs use the same code path and roll back at the end.

## Gotchas

* **Typer ≥ 0.2x vendors Click** (`typer._click`), so `import click` fails. Use `typer.prompt` / `typer.confirm`, and the `prompt_number` helper in `cli.py` for numbered choices.
* Commands are wrapped with `@guard`, which turns `FriendsError` into a red message and exit code 1. Raise `ValidationError`, `FriendNotFoundError` or `FriendsError` for user-facing failures.
* Don't add `from __future__ import annotations` to `cli.py`, because Typer resolves annotations through the `@guard` wrapper.
* `friends path` uses `typer.echo`, not Rich, so `$(friends path)` never gets wrapped.
* New commands must be added to `ui.HELP_SECTIONS`. That list drives `friends help`, the order of `friends options` and the "Unknown command" hint.
* Rich tables wrap in narrow terminals. Use `no_wrap=True` on short value columns.
* Schema changes: there are no migrations yet. `create_all` plus `PRAGMA user_version` (`SCHEMA_VERSION` in `db.py`) is all there is. Adding columns to an existing user database needs a manual migration step.

## Conventions

* Match the existing style: small, typed functions; dataclasses for results (`LogOutcome`, `ImportReport`, `Digest`); module docstrings explaining the rules.
* Every behavior change gets a test. CLI behavior is tested end to end with the `cli` fixture, which uses a temporary database, a pinned date and `COLUMNS=200`.
* Update `README.md` when commands, options or rules change.
