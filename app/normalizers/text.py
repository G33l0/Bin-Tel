"""Low-level text canonicalisation shared by every normalizer."""

from __future__ import annotations

import re
import unicodedata

_WHITESPACE = re.compile(r"\s+")
_PUNCTUATION = re.compile(r"[^\w\s]", re.UNICODE)
_AMPERSAND = re.compile(r"\s*&\s*")


def strip_accents(value: str) -> str:
    """``Société`` → ``Societe`` so accented and plain spellings compare equal."""
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def collapse_whitespace(value: str) -> str:
    return _WHITESPACE.sub(" ", value).strip()


#: Control characters have no place in a bank name. A NUL in particular is
#: accepted by SQLite but truncates the value everywhere it meets C string
#: handling, so a row that looks fine in the database reads as corrupt later.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

#: Longest text Bin-Tel will store for a single field. The ORM declares
#: String(256) but SQLite does not enforce it, so a 10,000-character paste
#: would otherwise be stored whole and shown in full.
MAX_TEXT_LENGTH = 256


def sanitise_text(value: str | None, *, limit: int = MAX_TEXT_LENGTH) -> str | None:
    """Make an untrusted string safe to store: no control codes, bounded length.

    Returns ``None`` for anything that is empty once cleaned, so a field of
    control characters becomes an honest absence rather than a blank that
    looks like a real value.
    """
    if value is None:
        return None
    text = collapse_whitespace(_CONTROL.sub("", str(value)))
    if not text:
        return None
    return text[:limit] if len(text) > limit else text


def squash(value: str | None) -> str:
    """Canonical comparison form: accent-free, punctuation-free, lower case."""
    if not value:
        return ""
    text = strip_accents(str(value))
    text = _AMPERSAND.sub(" and ", text)
    text = _PUNCTUATION.sub(" ", text)
    return collapse_whitespace(text).lower()


def tokens(value: str | None) -> list[str]:
    squashed = squash(value)
    return squashed.split() if squashed else []


def clean_display(value: str | None) -> str:
    """Tidy a value for display without changing its meaning."""
    if not value:
        return ""
    return collapse_whitespace(str(value).replace(" ", " "))


def initialism(value: str | None) -> str:
    """``Bank of the West`` → ``BOTW`` (used to match abbreviations)."""
    parts = tokens(value)
    return "".join(part[0] for part in parts if part).upper()
