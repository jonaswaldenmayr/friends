from datetime import date, timedelta

import pytest

from friends.interactions import log_interaction
from friends.models import Group, InteractionType
from friends.ranking import load_statuses, rank, recommend

from .conftest import TODAY


def contact(session, friend, days_ago, itype=InteractionType.CALL):
    log_interaction(session, friend, itype, on=TODAY - timedelta(days=days_ago), today=TODAY)


def test_ratio_and_overdue(session, make_friend):
    friend = make_friend("Max", Group.CLOSE_FRIENDS)  # 30 days
    contact(session, friend, 45)
    (status,) = load_statuses(session, TODAY)
    assert status.days_since == 45
    assert status.interval == 30
    assert status.days_overdue == 15
    assert status.ratio == pytest.approx(1.5)
    assert status.due_date == TODAY - timedelta(days=15)
    assert status.is_due


def test_ranking_order(session, make_friend):
    a = make_friend("A", Group.FAMILY)  # 14d, x1.4
    b = make_friend("B", Group.FRIENDS)  # 60d, x1.2
    c = make_friend("C", Group.EXTENDED_CIRCLE)  # 90d, x1.0
    d = make_friend("D", Group.FRIENDS, interval=10)  # override, x1.2
    contact(session, a, 21)  # ratio 1.5 -> 2.1
    contact(session, b, 30)  # ratio 0.5 -> 0.5 (not due: no weight)
    contact(session, c, 180)  # ratio 2.0 -> 2.0
    contact(session, d, 10)  # ratio 1.0 -> 1.2
    ranked = rank(load_statuses(session, TODAY))
    assert [s.name for s in ranked] == ["A", "C", "D", "B"]
    assert [round(s.priority, 3) for s in ranked] == [2.1, 2.0, 1.2, 0.5]
    assert [round(s.ratio, 2) for s in ranked] == [1.5, 2.0, 1.0, 0.5]


def test_group_weights_order_equally_overdue_friends(session, make_friend):
    # Everyone exactly at 2x their interval: the group decides the order.
    people = [
        ("Ext", Group.EXTENDED_CIRCLE),
        ("Fr", Group.FRIENDS),
        ("Close", Group.CLOSE_FRIENDS),
        ("Fam", Group.FAMILY),
        ("Inner", Group.INNER_CIRCLE),
    ]
    for name, group in people:
        contact(session, make_friend(name, group), 2 * group.default_interval)
    ranked = rank(load_statuses(session, TODAY))
    # Close (60 days elapsed) beats Fam (28 days) on the days-elapsed tie-break.
    assert [s.name for s in ranked] == ["Inner", "Close", "Fam", "Fr", "Ext"]
    assert [round(s.priority, 3) for s in ranked] == [3.2, 2.8, 2.8, 2.4, 2.0]
    assert Group.INNER_CIRCLE.weight > Group.FAMILY.weight == Group.CLOSE_FRIENDS.weight
    assert Group.FAMILY.weight > Group.FRIENDS.weight > Group.EXTENDED_CIRCLE.weight


def test_overdue_extended_beats_not_yet_due_friends(session, make_friend):
    nora = make_friend("Nora", Group.EXTENDED_CIRCLE, interval=60)
    tobi = make_friend("Tobi", Group.FRIENDS, interval=90)
    contact(session, nora, 71)  # overdue: 1.18 x 1.0
    contact(session, tobi, 88)  # not due: 0.98 x 1.2
    ranked = rank(load_statuses(session, TODAY))
    assert [s.name for s in ranked] == ["Nora", "Tobi"]
    assert all(s.priority >= 1 for s in ranked if s.is_due)


def test_group_weight_only_applies_once_due(session, make_friend):
    inner_soon = make_friend("InnerSoon", Group.INNER_CIRCLE)  # 14d, x1.6
    inner_due = make_friend("InnerDue", Group.INNER_CIRCLE)
    ext_due = make_friend("ExtDue", Group.EXTENDED_CIRCLE)  # 90d, x1.0
    contact(session, inner_soon, 13)  # ratio 0.93, not due -> 0.93
    contact(session, inner_due, 14)  # ratio 1.0, due today -> 1.6
    contact(session, ext_due, 91)  # ratio 1.01, overdue -> 1.01
    ranked = rank(load_statuses(session, TODAY))
    assert [s.name for s in ranked] == ["InnerDue", "ExtDue", "InnerSoon"]
    soon = ranked[-1]
    assert soon.priority == soon.ratio
    assert [round(s.priority, 2) for s in ranked] == [1.6, 1.01, 0.93]


def test_ties_are_deterministic(session, make_friend):
    for name in ["Zed", "amy", "Bob"]:
        contact(session, make_friend(name, Group.FRIENDS), 30)
    assert [s.name for s in rank(load_statuses(session, TODAY))] == ["amy", "Bob", "Zed"]


def test_override_changes_priority(session, make_friend):
    friend = make_friend("Emily", Group.FRIENDS)
    contact(session, friend, 30)
    assert load_statuses(session, TODAY)[0].ratio == pytest.approx(0.5)
    friend.interval_override = 10
    assert load_statuses(session, TODAY)[0].ratio == pytest.approx(3.0)


def test_priority_updates_after_logging(session, make_friend):
    max_ = make_friend("Max", Group.FAMILY)
    anna = make_friend("Anna", Group.FAMILY)
    contact(session, max_, 30)
    contact(session, anna, 20)
    assert rank(load_statuses(session, TODAY))[0].name == "Max"
    log_interaction(session, max_, InteractionType.MESSAGE, on=TODAY, today=TODAY)
    ranked = rank(load_statuses(session, TODAY))
    assert [s.name for s in ranked] == ["Anna", "Max"]
    assert ranked[1].ratio == 0


def test_unknown_contacts_are_separate(session, make_friend):
    contact(session, make_friend("Known"), 5)
    make_friend("Mystery", Group.FAMILY)
    recs = recommend(load_statuses(session, TODAY))
    assert [s.name for s in recs.ranked] == ["Known"]
    assert [s.name for s in recs.unknown] == ["Mystery"]


def test_snoozed_excluded_by_default(session, make_friend):
    sleepy = make_friend("Sleepy", Group.FAMILY)
    awake = make_friend("Awake", Group.FRIENDS)
    contact(session, sleepy, 100)
    contact(session, awake, 10)
    sleepy.snoozed_until = TODAY + timedelta(days=30)

    recs = recommend(load_statuses(session, TODAY))
    assert [s.name for s in recs.ranked] == ["Awake"]
    assert recs.hidden_snoozed == 1

    everyone = recommend(load_statuses(session, TODAY), include_snoozed=True)
    assert [s.name for s in everyone.ranked] == ["Sleepy", "Awake"]

    # Snooze expires on its end date.
    later = load_statuses(session, TODAY + timedelta(days=30))
    assert "Sleepy" in [s.name for s in recommend(later).ranked]


def test_group_filter_and_limit(session, make_friend):
    for i in range(5):
        contact(session, make_friend(f"F{i}", Group.FRIENDS), 10 * (i + 1))
    contact(session, make_friend("Fam", Group.FAMILY), 1)
    statuses = load_statuses(session, TODAY)

    recs = recommend(statuses, limit=3)
    assert [s.name for s in recs.ranked] == ["F4", "F3", "F2"]
    assert recs.truncated == 3

    family = recommend(statuses, group=Group.FAMILY)
    assert [s.name for s in family.ranked] == ["Fam"]
