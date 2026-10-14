"""Parsing of dates and durations typed by humans or exported by Excel."""

import re
from datetime import date, datetime, timedelta

from friends.errors import ValidationError

# Day-first (European) formats only: 03.04.26 is 3 April, never March 4.
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y.%m.%d",
    "%d.%m.%Y",
    "%d.%m.%y",
    "%d/%m/%Y",
    "%d/%m/%y",
    "%d-%m-%Y",
    "%d-%m-%y",
)


def parse_calendar_date(value: str) -> date:
    """Parse ISO or common European dates, ignoring a trailing time part."""
    text = value.strip()
    # Excel sometimes writes "2026-10-11 00:00:00" or "2026-10-11T00:00".
    text = re.split(r"[ T]", text, maxsplit=1)[0] if text else text
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValidationError(
        f"Unrecognised date {value!r}. Use YYYY-MM-DD, DD.MM.YYYY or DD.MM.YY."
    )


def parse_date(value: str, today: date) -> date:
    """Parse 'today', 'yesterday', ISO or European dates."""
    key = value.strip().casefold()
    if key in {"today", "now"}:
        return today
    if key == "yesterday":
        return today - timedelta(days=1)
    return parse_calendar_date(value)


_DURATION = re.compile(r"^(\d+)\s*(d|day|days|w|wk|week|weeks|m|mo|month|months)?$")
_UNIT_DAYS = {"d": 1, "w": 7, "m": 30}


def parse_snooze_until(value: str, today: date) -> date:
    """Turn '30d', '2w', '3m' (30-day months), '14' or a date into an end date."""
    key = value.strip().casefold()
    match = _DURATION.match(key)
    if match:
        amount = int(match.group(1))
        unit = (match.group(2) or "d")[0]
        if amount <= 0:
            raise ValidationError("Snooze duration must be positive.")
        return today + timedelta(days=amount * _UNIT_DAYS[unit])
    try:
        until = parse_calendar_date(value)
    except ValidationError:
        raise ValidationError(
            f"Unrecognised duration {value!r}. Use e.g. 30d, 2w, 3m or a date."
        ) from None
    if until <= today:
        raise ValidationError("Snooze end date must be in the future.")
    return until
