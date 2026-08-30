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
