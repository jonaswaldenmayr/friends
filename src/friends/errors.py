"""Domain errors. The CLI turns these into friendly messages."""


class FriendsError(Exception):
    """Base class for expected, user-facing errors."""


class ValidationError(FriendsError):
    """Invalid user input (bad date, unknown group, ...)."""


class FriendNotFoundError(FriendsError):
    """No friend matches the given name."""
