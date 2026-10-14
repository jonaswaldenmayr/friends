from datetime import date

import pytest

from friends.dates import parse_calendar_date, parse_date, parse_snooze_until
from friends.errors import ValidationError
from friends.names import fold_name, normalize_name

TODAY = date(2026, 10, 11)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-10-01", date(2026, 10, 1)),
        ("01.10.2026", date(2026, 10, 1)),
        ("01.10.26", date(2026, 10, 1)),
        ("1.8.26", date(2026, 8, 1)),
        ("03/04/2026", date(2026, 4, 3)),  # day first: 3 April
        ("03/04/26", date(2026, 4, 3)),
        ("03-04-2026", date(2026, 4, 3)),
        ("2026/04/03", date(2026, 4, 3)),
        ("2026-10-11 00:00:00", date(2026, 10, 11)),
        (" 11.10.26 ", date(2026, 10, 11)),
    ],
)
def test_european_and_iso_dates(text, expected):
    assert parse_calendar_date(text) == expected


@pytest.mark.parametrize("text", ["31.02.2026", "10/31/2026", "next week", ""])
def test_invalid_dates_rejected(text):
    with pytest.raises(ValidationError):
        parse_calendar_date(text)


def test_relative_dates():
    assert parse_date("today", TODAY) == TODAY
    assert parse_date("Yesterday", TODAY) == date(2026, 10, 10)
    assert parse_date("2026-09-01", TODAY) == date(2026, 9, 1)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("30d", date(2026, 11, 10)),
        ("2w", date(2026, 10, 25)),
        ("1m", date(2026, 11, 10)),
        ("14", date(2026, 10, 25)),
        ("2026-12-24", date(2026, 12, 24)),
    ],
)
def test_snooze_durations(text, expected):
    assert parse_snooze_until(text, TODAY) == expected


@pytest.mark.parametrize("text", ["0d", "2026-01-01", "forever"])
def test_bad_snooze_durations(text):
    with pytest.raises(ValidationError):
        parse_snooze_until(text, TODAY)


def test_name_normalisation():
    assert normalize_name("  Anna   Maria ") == "anna maria"
    assert normalize_name("JØRGEN") == "jørgen"
    # Accents are part of identity but not of loose search.
    assert normalize_name("Jørgen") != normalize_name("Jorgen")
    assert fold_name("Jørgen") == fold_name("jorgen")
    assert fold_name("Joāo Müller") == "joao muller"
    # NFC and NFD spellings are the same person.
    assert normalize_name("Jõao") == normalize_name("Jõao")
