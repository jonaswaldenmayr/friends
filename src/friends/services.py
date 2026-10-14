"""Friend CRUD and name lookup. UI-agnostic: reusable by a future web app."""

import difflib
from collections.abc import Callable
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from friends.errors import FriendNotFoundError, ValidationError
from friends.models import Friend, Group, Interaction
from friends.names import fold_name, normalize_name, words

Chooser = Callable[[str, list[Friend]], Friend]


def resolve_override(group: Group, interval: int | None) -> int | None:
    """Store an interval only when it differs from the group default."""
    if interval is None:
        return None
    if interval <= 0:
        raise ValidationError("Interval must be a positive number of days.")
    return None if interval == group.default_interval else interval


def all_friends(session: Session) -> list[Friend]:
    return list(session.scalars(select(Friend).order_by(Friend.normalized_name, Friend.id)))


def friends_named(session: Session, name: str) -> list[Friend]:
    key = normalize_name(name)
    return list(session.scalars(select(Friend).where(Friend.normalized_name == key)))


def create_friend(
    session: Session,
    *,
    name: str,
    group: Group,
    location: str | None = None,
    interval: int | None = None,
    notes: str | None = None,
    friend_id: str | None = None,
) -> Friend:
    friend = Friend(
        name=name,
        group=group,
        location=(location or "").strip() or None,
        interval_override=resolve_override(group, interval),
        notes=(notes or "").strip() or None,
    )
    if friend_id:
        friend.id = friend_id
    session.add(friend)
    session.flush()
    return friend


def last_contacts(session: Session) -> dict[str, datetime]:
    """Effective last contact per friend, derived from interaction history."""
    rows = session.execute(
        select(Interaction.friend_id, func.max(Interaction.occurred_at)).group_by(
            Interaction.friend_id
        )
    )
    return {friend_id: occurred for friend_id, occurred in rows}


def last_contact(session: Session, friend_id: str) -> datetime | None:
    return session.scalar(
        select(func.max(Interaction.occurred_at)).where(Interaction.friend_id == friend_id)
    )


def find_friends(session: Session, query: str) -> list[Friend]:
    """Return the best-matching candidates for a typed name.

    Stages, first non-empty wins: exact name, exact ignoring accents, prefix,
    word prefix, all-words prefix, substring, then UUID prefix.
    """
    key = normalize_name(query)
    if not key:
        return []
    friends = all_friends(session)
    exact = [f for f in friends if f.normalized_name == key]
    if exact:
        return exact

    folded_query = fold_name(query)
    query_words = words(folded_query)
    folded = [(f, fold_name(f.name)) for f in friends]
    stages: list[Callable[[str], bool]] = [
        lambda n: n == folded_query,
        lambda n: n.startswith(folded_query),
        lambda n: any(w.startswith(folded_query) for w in words(n)),
        # "johanne stg" -> "Johanne (Stg)": every typed word starts a name word.
        lambda n: bool(query_words) and all(any(w.startswith(q) for w in words(n)) for q in query_words),
        lambda n: folded_query in n,
    ]
    for matches_stage in stages:
        found = [f for f, n in folded if matches_stage(n)]
        if found:
            return found

    if len(key) >= 4 and all(c in "0123456789abcdef-" for c in key):
        return [f for f in friends if f.id.startswith(key)]
    return []


def resolve_friend(session: Session, query: str, choose: Chooser) -> Friend:
    """Find exactly one friend, asking ``choose`` to break ties."""
    candidates = find_friends(session, query)
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        return choose(query, candidates)

    names = {fold_name(f.name): f.name for f in all_friends(session)}
    close = difflib.get_close_matches(fold_name(query), list(names), n=3, cutoff=0.6)
    hint = f" Did you mean: {', '.join(names[c] for c in close)}?" if close else ""
    raise FriendNotFoundError(f"No friend matches {query!r}.{hint}")


def snooze(friend: Friend, until: date | None) -> None:
    friend.snoozed_until = until


def delete_friend(session: Session, friend: Friend) -> None:
    session.delete(friend)
    session.flush()
