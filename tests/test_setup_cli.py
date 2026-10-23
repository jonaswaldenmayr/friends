"""CLI: database location, snapshot, restore, autocommit, banner style and colour."""

import subprocess
from pathlib import Path

import pytest

from tests.test_cli import seed


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


@pytest.fixture
def data_repo(tmp_path, monkeypatch) -> Path:
    repo = tmp_path / "data"
    repo.mkdir()
    git(repo, "init", "-q")
    for key in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(key, "Test")
    for key in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(key, "test@example.com")
    return repo


def test_path_shows_and_moves_database(cli, tmp_path, monkeypatch):
    seed(cli)
    assert cli("path").output.strip() == str(tmp_path / "cli.db")
    monkeypatch.delenv("FRIENDS_DB")  # otherwise the env override wins over the saved path
    target = tmp_path / "sync" / "friends.db"
    assert cli("path", str(target)).output.strip() == str(target)
    assert target.exists() and (tmp_path / "cli.db").exists()  # copied, old file untouched
    assert "Max" in cli("list").output
    assert cli("path").output.strip() == str(target)


def test_snapshot_written_after_every_change(cli, tmp_path):
    seed(cli)
    assert "No snapshot file set" in cli("snapshot", ok=False).output
    snap = tmp_path / "data" / "friends.yaml"
    assert "Snapshot of 6 friends" in cli("snapshot", "--file", str(snap)).output
    assert "- name: Max\n" in snap.read_text(encoding="utf-8")

    cli("log", "max", "call", "-m", "hi")
    assert 'type: call, note: "hi"' in snap.read_text(encoding="utf-8")
    cli("snooze", "emily", "30d")
    assert "snoozed_until: 2026-11-10" in snap.read_text(encoding="utf-8")
    cli("remove", "emily", "--yes")
    assert "Emily" not in snap.read_text(encoding="utf-8")
    cli("add", "Nora", "-g", "family")
    assert "- name: Nora\n" in snap.read_text(encoding="utf-8")
    cli("edit", "nora", "--location", "Oslo")
    assert "location: Oslo" in snap.read_text(encoding="utf-8")
    cli("undo", "--yes")
    assert 'note: "hi"' not in snap.read_text(encoding="utf-8")
    assert "Snapshot of 6 friends" in cli("snapshot").output


def test_restore(cli, tmp_path):
    seed(cli)
    snap = tmp_path / "friends.yaml"
    cli("snapshot", "--file", str(snap))
    backup = tmp_path / "backup.yaml"
    backup.write_bytes(snap.read_bytes())  # the live snapshot follows every change, a copy does not
    cli("remove", "max", "--yes")
    assert "Max" not in cli("list").output
    assert "Cancelled" in cli("restore", str(backup), input="n\n", ok=False).output
    assert "Restored 6 friends" in cli("restore", str(backup), input="y\n").output
    assert "Max" in cli("list").output
    assert "Restored 6 friends" in cli("restore", "--yes").output  # default: the configured file
    other = tmp_path / "other.yaml"
    other.write_text("- name: Solo\n  group: family\n", encoding="utf-8")
    assert "Restored 1 friend and 0 contacts" in cli("restore", str(other), "--yes").output
    assert "Solo" in cli("list").output and "Max" not in cli("list").output
    assert "not found" in cli("restore", "missing.yaml", "--yes", ok=False).output


def test_autocommit(cli, data_repo):
    seed(cli)
    assert "Set a snapshot file first" in cli("autocommit", "on", ok=False).output
    cli("snapshot", "--file", str(data_repo / "friends.yaml"))
    assert "autocommit: off" in cli("autocommit").output
    assert "autocommit: on" in cli("autocommit", "on").output

    cli("log", "max", "call")
    cli("log", "max", "call")  # duplicate: skipped, nothing to commit
    cli("add", "Nora", "-g", "family")
    log = git(data_repo, "log", "--format=%s")
    assert log.splitlines() == ["friends add Nora", "friends log Max: call"]
    assert git(data_repo, "status", "--porcelain") == ""

    out = cli("autocommit", "push").output
    assert "autocommit: push" in out
    assert "git failed" in cli("log", "emily", "call").output  # no remote to push to
    assert "Unknown autocommit" in cli("autocommit", "maybe", ok=False).output


def test_autocommit_outside_git_repo_warns(cli, tmp_path):
    seed(cli)
    cli("snapshot", "--file", str(tmp_path / "plain" / "friends.yaml"))
    cli("autocommit", "on")
    assert "not a git repository" in cli("add", "Nora", "-g", "family").output
    assert "Nora" in cli("list").output  # the change itself went through


def test_style_and_color(cli):
    assert "banner style: plain" in cli("style").output
    assert "banner style: big" in cli("style", "big").output
    assert "Unknown banner style" in cli("style", "goth", ok=False).output
    assert "colour: blue" in cli("color").output
    assert "colour: peach" in cli("colour", "peach").output
    assert "colour: sunset" in cli("color", "sunset").output
    assert "Unknown colour" in cli("color", "mauve", ok=False).output


def test_banner_appears_on_next_digest_and_help(cli):
    seed(cli)
    for args in (["next"], ["digest"], ["help"], []):
        out = cli(*args).output
        assert "█" in out, args
    out = cli("next").output
    assert "6 friends · 1 overdue" in out and "Who to contact" not in out and "Oct" not in out.split("━")[0]
    assert "Inner circle · 1 friend · 0 overdue" in cli("next", "-g", "inner").output
    assert "█" not in cli("list").output and "█" not in cli("log", "max", "call").output
    cli("style", "big")
    assert "░" in cli("next").output
