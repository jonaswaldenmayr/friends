"""End-to-end tests of the `friends` CLI against a temporary database."""

from pathlib import Path

CSV = (
    "﻿Friend,Group,Location,Interval,Last contact\r\n"
    "Max,Inner circle,Freiburg,7,11.10.26\r\n"
    ",,,,\r\n"
    "Emily,Friends,Copenhagen,9,01.08.26\r\n"
    "Johanne (Oslo),Extended circle,Oslo,120,01.10.26\r\n"
    "Johanne (Stg),Extended circle,Stuttgart,90,01.09.26\r\n"
    "Jørgen,Friends,Oslo,21,10.10.26\r\n"
    "Lukas,Close friends,Berlin,,\r\n"
)


def seed(cli):
    Path("friends.csv").write_text(CSV, encoding="utf-8")
    return cli("import")


def test_help_variants(cli):
    for args in (["help"], [], ["--help"], ["log", "--help"], ["import", "-h"]):
        result = cli(*args)
        assert "friends" in result.output.lower() or "Usage" in result.output
    assert "export-template" in cli("help").output


def test_options_and_help_for_command(cli):
    everything = cli("options").output
    for snippet in ("friends log", "--date, -d DATE", "--allow-future", "friends export-template", "[default: 10]"):
        assert snippet in everything
    only_log = cli("options", "log").output
    assert "--note, -m" in only_log and "friends next" not in only_log
    page = cli("help", "log").output
    assert "Usage" in page and "--allow-future" in page
    assert "Unknown command" in cli("options", "nope", ok=False).output
    assert "Unknown command" in cli("help", "nope", ok=False).output


def test_import_from_current_directory(cli):
    result = seed(cli)
    assert "Friends created" in result.output and "Import complete" in result.output
    again = cli("import")
    assert "Dates already known" in again.output


def test_import_missing_file(cli):
    result = cli("import", ok=False)
    assert result.exit_code == 1
    assert "not found" in result.output


def test_import_dry_run_then_errors(cli):
    Path("friends.csv").write_text(CSV, encoding="utf-8")
    assert "Dry run" in cli("import", "--dry-run").output
    assert "No friends match" in cli("list").output

    Path("bad.csv").write_text("Friend,Group\nMax,Strangers\n", encoding="utf-8")
    result = cli("import", "bad.csv", ok=False)
    assert result.exit_code == 1
    assert "Unknown group" in result.output and "nothing was written" in result.output


def test_next_ranks_and_separates_unknown(cli):
    seed(cli)
    out = cli("next").output
    assert out.index("Emily") < out.index("Max")
    assert "Unknown last contact" in out and "Lukas" in out
    assert "9.47" in out  # Emily: 71 days / 9 × 1.2 (Friends)


def test_log_resets_priority(cli):
    seed(cli)
    assert "Logged call with Emily" in cli("log", "emily", "call").output
    out = cli("next", "--limit", "1").output
    assert "Emily" not in out
    shown = cli("show", "emily").output
    assert "call" in shown and "imported" in shown


def test_log_multiple_and_yesterday(cli):
    seed(cli)
    out = cli("log", "max,jorgen,lukas", "meeting", "--date", "yesterday").output
    assert out.count("Logged meeting") == 3
    assert "Jørgen" in out
    history = cli("history", "--type", "meeting").output
    assert "2026-10-10" in history and "Lukas" in history


def test_log_duplicate_and_future(cli):
    seed(cli)
    cli("log", "max", "call")
    assert "already logged" in cli("log", "max", "call").output
    assert "Logged call" in cli("log", "max", "call", "--force").output
    result = cli("log", "max", "call", "--date", "2026-12-01", ok=False)
    assert "future" in result.output
    assert "Logged" in cli("log", "max", "call", "-d", "2026-12-01", "--allow-future").output


def test_log_argument_errors(cli):
    seed(cli)
    assert "type comes last" in cli("log", "call", "max", ok=False).output
    assert "Did you mean" in cli("log", "maxx", "call", ok=False).output
    assert "Usage" in cli("log", "max", ok=False).output


def test_ambiguous_name_interactive_choice(cli):
    seed(cli)
    out = cli("log", "johanne", "message", input="2\n").output
    assert "matches 2 friends" in out
    assert "Logged message with Johanne (Stg)" in out
    cancelled = cli("log", "johanne", "message", input="0\n", ok=False)
    assert "Cancelled" in cancelled.output


def test_snooze_and_clear(cli):
    seed(cli)
    assert "until 2026-11-10" in cli("snooze", "emily", "30d").output
    out = cli("next").output
    assert "Emily" not in out and "1 snoozed hidden" in out
    assert "Emily" in cli("next", "--all").output
    assert "snoozed until" in cli("list", "--snoozed").output
    cli("snooze", "emily", "--clear")
    assert "Emily" in cli("next").output


def test_list_filters_and_sorting(cli):
    seed(cli)
    out = cli("list", "--location", "oslo", "--sort", "name").output
    assert "Jørgen" in out and "Max" not in out
    assert out.index("Johanne (Oslo)") < out.index("Jørgen")
    family = cli("list", "-g", "inner").output
    assert "Max" in family and "Emily" not in family
    assert "Lukas" in cli("list", "--unknown").output


def test_add_non_interactive_and_duplicate_warning(cli):
    out = cli("add", "Anna (Berlin)", "-g", "close", "-l", "Berlin", "--last", "2026-10-01", "--via", "call").output
    assert "Added Anna (Berlin)" in out
    assert "2026-10-01" in cli("show", "anna").output
    dup = cli("add", "anna (berlin)", "-g", "friends", input="n\n", ok=False)
    assert "already exists" in dup.output


def test_add_interactive(cli):
    answers = "Sophie\n3\nParis\n\n2026-10-05\nmessage\n"
    out = cli("add", input=answers).output
    assert "Added Sophie (Close friends, every 30 days)" in out
    shown = cli("show", "sophie").output
    assert "Paris" in shown and "message" in shown


def test_edit(cli):
    seed(cli)
    out = cli("edit", "max", "--interval", "21", "--group", "close", "--location", "Basel").output
    assert "interval 7d → 21d" in out
    shown = cli("show", "max").output
    assert "Close friends" in shown and "Basel" in shown and "individual" in shown
    cli("edit", "max", "--interval", "default", "--location", "-")
    shown = cli("show", "max").output
    assert "every 30 days (group default)" in shown and "Basel" not in shown


def test_edit_interactive(cli):
    seed(cli)
    cli("edit", "lukas", input="Lukas M\n4\nHamburg\ndefault\nmet at uni\n")
    shown = cli("show", "lukas m").output
    assert "Friends" in shown and "Hamburg" in shown and "met at uni" in shown


def test_undo_and_remove(cli):
    seed(cli)
    cli("log", "max", "call")
    assert "Removed call with Max" in cli("undo", "--yes").output
    assert "Nothing to undo" in cli("undo", "--yes", ok=False).output
    assert "Deleted Emily" in cli("remove", "emily", "--yes").output
    assert "Emily" not in cli("list").output


def test_digest_terminal_and_markdown(cli):
    seed(cli)
    assert "Reach out this week" in cli("digest").output
    md = cli("digest", "--markdown").output
    assert md.startswith("# Friends digest")
    assert "| 1 | Emily |" in md
    cli("digest", "-o", "week.md")
    assert Path("week.md").read_text(encoding="utf-8").startswith("# Friends digest")


def test_export_and_overwrite_protection(cli):
    seed(cli)
    assert "Exported 6 friends" in cli("export").output
    content = Path("friends_export.csv").read_text(encoding="utf-8-sig")
    assert content.splitlines()[0] == "Friend,Group,Location,Interval,Last contact"
    assert "Jørgen,Friends,Oslo,21,2026-10-10" in content

    refused = cli("export", input="n\n", ok=False)
    assert "Not overwritten" in refused.output
    cli("export", input="y\n")
    cli("export", "--force", "-o", "backup.csv", "--with-ids")
    assert Path("backup.csv").read_text(encoding="utf-8-sig").splitlines()[0].endswith(",ID")


def test_export_template(cli):
    cli("export-template")
    assert Path("friends_template.csv").read_text(encoding="utf-8-sig").strip() == (
        "Friend,Group,Location,Interval,Last contact"
    )
    assert "Not overwritten" in cli("export-template", input="n\n", ok=False).output
    cli("export-template", "--output", "mine.csv")
    assert "Friends created" in cli("import", "mine.csv").output


def test_path_command(cli, tmp_path):
    assert str(tmp_path / "cli.db") in cli("path").output.replace("\n", "")
