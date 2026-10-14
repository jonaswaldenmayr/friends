from datetime import date, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from friends.db import Database
from friends.models import Group
from friends.services import create_friend

TODAY = date(2026, 10, 11)
NOW = datetime(2026, 10, 11, 18, 30)


@pytest.fixture
def today() -> date:
    return TODAY


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "test.db")


@pytest.fixture
def session(db: Database):
    with db.session() as s:
        yield s


@pytest.fixture
def make_friend(session):
    def _make(name: str, group: Group = Group.FRIENDS, **kwargs):
        return create_friend(session, name=name, group=group, **kwargs)

    return _make


@pytest.fixture
def write_csv(tmp_path: Path):
    def _write(text: str, name: str = "friends.csv", encoding: str = "utf-8") -> Path:
        path = tmp_path / name
        path.write_bytes(text.encode(encoding))
        return path

    return _write


@pytest.fixture
def cli(tmp_path: Path, monkeypatch):
    """Run CLI commands against a temporary database on a pinned date."""
    monkeypatch.setenv("FRIENDS_DB", str(tmp_path / "cli.db"))
    monkeypatch.setenv("FRIENDS_TODAY", TODAY.isoformat())
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.chdir(tmp_path)
    from friends.cli import app

    runner = CliRunner()

    def _run(*args: str, input: str | None = None, ok: bool = True):
        result = runner.invoke(app, list(args), input=input)
        if ok:
            assert result.exit_code == 0, result.output
        return result

    return _run
