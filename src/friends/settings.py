"""Persistent user settings: where the database and the snapshot live, autocommit, theme.

A small YAML file in the platform config directory (on macOS that is
``~/Library/Application Support/friends/settings.yaml``, next to the database).
``FRIENDS_CONFIG=/some/file`` overrides the location for one run, which is how the
tests stay away from the real file.

Keys: ``db``, ``snapshot``, ``autocommit``, ``style``, ``color``.
"""

import os
from pathlib import Path

import platformdirs
import yaml

from friends.errors import FriendsError

APP_NAME = "friends"


def settings_path() -> Path:
    override = os.environ.get("FRIENDS_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path(platformdirs.user_config_dir(APP_NAME, appauthor=False)) / "settings.yaml"


def load_settings() -> dict[str, str]:
    try:
        data = yaml.safe_load(settings_path().read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def save_setting(key: str, value: str | None) -> None:
    """Set one key (``None`` removes it) and write the file back."""
    data = load_settings()
    if value is None:
        data.pop(key, None)
    else:
        data[key] = value
    path = settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(data, sort_keys=True, allow_unicode=True), encoding="utf-8")
    except OSError as exc:
        raise FriendsError(f"Cannot write settings to {path}: {exc.strerror}") from None


def snapshot_path() -> Path | None:
    """The configured snapshot file, or ``None`` when snapshots are not set up."""
    configured = load_settings().get("snapshot")
    return Path(configured).expanduser() if configured else None
