"""Name normalisation used for matching friends."""

import re
import unicodedata

# Letters that Unicode decomposition does not reduce to ASCII.
_SPECIAL_LETTERS = str.maketrans(
    {"ø": "o", "æ": "ae", "œ": "oe", "ß": "ss", "ł": "l", "đ": "d", "þ": "th", "ð": "d"}
)


def clean_name(name: str) -> str:
    """Canonical display form: NFC, trimmed, single spaces."""
    return " ".join(unicodedata.normalize("NFC", name).split())


def normalize_name(name: str) -> str:
    """Identity key for a name: case-insensitive, whitespace-insensitive.

    Accents are preserved, so "Jørgen" and "Jorgen" are different identities.
    """
    return clean_name(name).casefold()


def fold_name(name: str) -> str:
    """Loose search key: additionally strips accents ("Jørgen" -> "jorgen")."""
    text = normalize_name(name).translate(_SPECIAL_LETTERS)
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def words(name: str) -> list[str]:
    return re.findall(r"\w+", name)
