"""Single source of "now" so tests and demos can pin the date.

Set ``FRIENDS_TODAY=YYYY-MM-DD`` to pretend it is a different day.
"""

import os
from datetime import date, datetime


def today() -> date:
    override = os.environ.get("FRIENDS_TODAY")
    if override:
        return date.fromisoformat(override)
    return date.today()


def now() -> datetime:
    current = datetime.now().replace(microsecond=0)
    pinned = today()
    if pinned == current.date():
        return current
    return datetime.combine(pinned, current.time())
