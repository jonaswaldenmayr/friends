"""Database location, engine setup and transactions."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import platformdirs
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from friends.models import Base
from friends.settings import load_settings

APP_NAME = "friends"
SCHEMA_VERSION = 1


def default_db_path() -> Path:
    """``$FRIENDS_DB`` if set, else the path saved with ``friends path``, else the
    platform's user data directory.

    macOS:   ~/Library/Application Support/friends/friends.db
    Linux:   ~/.local/share/friends/friends.db
    Windows: %LOCALAPPDATA%\\friends\\friends.db
    """
    override = os.environ.get("FRIENDS_DB")
    if override:
        return Path(override).expanduser()
    configured = load_settings().get("db")
    if configured:
        return Path(configured).expanduser()
    return Path(platformdirs.user_data_dir(APP_NAME, appauthor=False)) / "friends.db"


def _create_engine(path: Path) -> Engine:
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path}")

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql(f"PRAGMA user_version={SCHEMA_VERSION}")
    return engine


class Database:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_db_path()
        self.engine = _create_engine(self.path)
        self.Session = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def session(self, *, dry_run: bool = False) -> Iterator[Session]:
        """One transaction: committed on success, rolled back on error or dry run."""
        session = self.Session()
        try:
            yield session
            if dry_run:
                session.rollback()
            else:
                session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()
