from datetime import date, datetime

import pytest

from friends.errors import ValidationError
from friends.interactions import history, log_interaction
from friends.models import Group, InteractionType, Source
from friends.ranking import load_statuses
from friends.services import last_contact, last_contacts

from .conftest import NOW, TODAY


def status_of(session, friend):
    return next(s for s in load_statuses(session, TODAY) if s.friend_id == friend.id)


@pytest.mark.parametrize("itype", [InteractionType.MEETING, InteractionType.CALL, InteractionType.MESSAGE])
def test_each_type_resets_timer(session, make_friend, itype):
    friend = make_friend("Max", Group.CLOSE_FRIENDS)
    log_interaction(session, friend, InteractionType.MEETING, on=date(2026, 8, 1), today=TODAY)
    assert status_of(session, friend).days_since == 71

    outcome = log_interaction(session, friend, itype, on=TODAY, today=TODAY, now=NOW)
    assert outcome.logged
    assert outcome.interaction.source == Source.MANUAL
    assert outcome.interaction.occurred_at == NOW
    status = status_of(session, friend)
    assert status.days_since == 0
    assert status.ratio == 0


def test_latest_interaction_wins_regardless_of_type(session, make_friend):
    friend = make_friend("Anna")
    log_interaction(session, friend, InteractionType.CALL, on=date(2026, 10, 5), today=TODAY)
    log_interaction(session, friend, InteractionType.MESSAGE, on=date(2026, 10, 9), today=TODAY)
    log_interaction(session, friend, InteractionType.MEETING, on=date(2026, 9, 1), today=TODAY)
    assert last_contact(session, friend.id).date() == date(2026, 10, 9)
    assert last_contacts(session)[friend.id].date() == date(2026, 10, 9)


def test_past_dates_stored_at_midday(session, make_friend):
    friend = make_friend("Anna")
    outcome = log_interaction(session, friend, InteractionType.CALL, on=date(2026, 10, 10), today=TODAY, now=NOW)
    assert outcome.interaction.occurred_at == datetime(2026, 10, 10, 12, 0)


def test_duplicate_submission_skipped(session, make_friend):
    friend = make_friend("Max")
    first = log_interaction(session, friend, InteractionType.CALL, on=TODAY, today=TODAY, now=NOW)
    second = log_interaction(session, friend, InteractionType.CALL, on=TODAY, today=TODAY, now=NOW)
    assert first.logged and not second.logged
    assert second.duplicate_of.id == first.interaction.id
    # A different type on the same day is a separate interaction.
    assert log_interaction(session, friend, InteractionType.MESSAGE, on=TODAY, today=TODAY, now=NOW).logged
    # Explicit override.
    assert log_interaction(session, friend, InteractionType.CALL, on=TODAY, today=TODAY, allow_duplicate=True).logged
    assert len(history(session, friend_id=friend.id)) == 3


def test_future_dates_need_permission(session, make_friend):
    friend = make_friend("Max")
    with pytest.raises(ValidationError, match="future"):
        log_interaction(session, friend, InteractionType.CALL, on=date(2026, 10, 12), today=TODAY)
    outcome = log_interaction(
        session, friend, InteractionType.CALL, on=date(2026, 10, 12), today=TODAY, allow_future=True
    )
    assert outcome.logged
    assert status_of(session, friend).days_since == 0  # clamped, never negative


def test_imported_contact_not_loggable_manually(session, make_friend):
    friend = make_friend("Max")
    with pytest.raises(ValidationError):
        log_interaction(session, friend, InteractionType.IMPORTED_CONTACT, on=TODAY, today=TODAY)
    with pytest.raises(ValidationError):
        InteractionType.parse_manual("imported_contact")
    assert InteractionType.parse_manual("Call") is InteractionType.CALL
    assert InteractionType.parse_manual("msg") is InteractionType.MESSAGE
    assert InteractionType.parse_filter("imported") is InteractionType.IMPORTED_CONTACT


def test_unknown_last_contact(session, make_friend):
    friend = make_friend("Newbie")
    status = status_of(session, friend)
    assert not status.known
    assert status.days_since is None and status.ratio is None and status.days_overdue is None


def test_history_filters(session, make_friend):
    max_ = make_friend("Max")
    anna = make_friend("Anna")
    log_interaction(session, max_, InteractionType.CALL, on=date(2026, 10, 1), today=TODAY)
    log_interaction(session, anna, InteractionType.MEETING, on=date(2026, 10, 5), today=TODAY)
    log_interaction(session, max_, InteractionType.MESSAGE, on=date(2026, 10, 9), today=TODAY)

    assert [i.occurred_on.day for i in history(session)] == [9, 5, 1]  # newest first
    assert [i.type for i in history(session, friend_id=max_.id)] == [InteractionType.MESSAGE, InteractionType.CALL]
    assert [i.friend.name for i in history(session, itype=InteractionType.MEETING)] == ["Anna"]
    assert len(history(session, limit=2)) == 2


def test_deleting_friend_removes_history(session, make_friend):
    from friends.services import delete_friend

    friend = make_friend("Max")
    log_interaction(session, friend, InteractionType.CALL, on=TODAY, today=TODAY)
    delete_friend(session, friend)
    assert history(session) == []
