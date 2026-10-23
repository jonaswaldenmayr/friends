"""Keep the snapshot file, and optionally its git repository, in step with the database.

``after_change`` is called by the CLI after every command that writes. It does
nothing unless a snapshot file is configured (``friends snapshot --file``). With
``friends autocommit on`` the snapshot is committed on its own after each change;
``push`` also pushes. Only the snapshot file is ever staged, other changes in the
repository are left alone. Problems are returned as warnings, never raised: a
failed commit must not undo a successful change.
"""

import subprocess
from pathlib import Path

from friends.db import Database
from friends.settings import load_settings
from friends.snapshot import write_snapshot

AUTOCOMMIT_MODES = {"off": "do nothing", "on": "git commit the snapshot", "push": "commit and push"}


def after_change(db: Database, message: str) -> list[str]:
    settings = load_settings()
    configured = settings.get("snapshot")
    if not configured:
        return []
    path = Path(configured).expanduser()
    with db.session() as session:
        write_snapshot(session, path)
    mode = settings.get("autocommit", "off")
    if mode not in ("on", "push"):
        return []
    return commit(path, message, push=mode == "push")


def commit(path: Path, message: str, *, push: bool = False) -> list[str]:
    """Commit just ``path`` (and maybe push). Returns warnings for the user."""

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(path.parent), *args], capture_output=True, text=True)

    if git("rev-parse", "--is-inside-work-tree").returncode != 0:
        return [f"autocommit is on but {path.parent} is not a git repository."]
    git("add", "--", str(path))
    if not git("status", "--porcelain", "--", str(path)).stdout.strip():
        return []  # nothing changed, e.g. a duplicate log that was skipped
    result = git("commit", "-q", "-m", message[:72], "--", str(path))
    if result.returncode == 0 and push:
        result = git("push", "-q")
    if result.returncode != 0:
        detail = [line for line in (result.stderr + result.stdout).splitlines() if line.strip()]
        return [f"git failed: {detail[0].strip() if detail else 'unknown error'}"]
    return []
