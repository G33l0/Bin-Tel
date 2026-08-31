"""Input validation for BIN/IIN identifiers and free-text search terms.

These functions are the *only* place that decides what a valid BIN looks like,
so the UI, the CLI and the importers all agree.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.errors import ValidationError

#: An IIN (Issuer Identification Number) is 6 or 8 digits under ISO/IEC 7812;
#: Bin-Tel accepts 4-11 digit prefixes so partial-prefix searching works.
MIN_BIN_LENGTH = 4
MAX_BIN_LENGTH = 11
CANONICAL_IIN_LENGTHS = (6, 8)

_DIGITS_ONLY = re.compile(r"^\d+$")
_NON_DIGIT = re.compile(r"\D+")
_RANGE_SEPARATORS = re.compile(r"\s*(?:-{1,2}|–|—|to|\.\.)\s*", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class BinInput:
    """A validated BIN/IIN query."""

    digits: str

    @property
    def length(self) -> int:
        return len(self.digits)

    @property
    def is_canonical_iin(self) -> bool:
        return self.length in CANONICAL_IIN_LENGTHS

    @property
    def prefix6(self) -> str:
        return self.digits[:6].ljust(6, "0") if self.length >= 6 else self.digits

    def padded_low(self, width: int = 8) -> int:
        """Lower bound of the numeric range this prefix covers."""
        return int(self.digits.ljust(width, "0")[:width])

    def padded_high(self, width: int = 8) -> int:
        """Upper bound of the numeric range this prefix covers."""
        return int(self.digits.ljust(width, "9")[:width])


def clean_digits(value: str) -> str:
    """Strip spaces, dashes and any other separator from a numeric input."""
    return _NON_DIGIT.sub("", value or "")


def is_valid_bin(value: str) -> bool:
    """Whether *value* can be used as a BIN/IIN lookup term."""
    digits = clean_digits(value)
    return bool(digits) and MIN_BIN_LENGTH <= len(digits) <= MAX_BIN_LENGTH


def validate_bin(value: str) -> BinInput:
    """Validate and normalise a BIN/IIN, raising :class:`ValidationError`."""
    raw = (value or "").strip()
    if not raw:
        raise ValidationError("Enter a BIN or IIN to search for.")
    digits = clean_digits(raw)
    if not digits:
        raise ValidationError(
            "That does not look like a BIN. A BIN is made up of digits only, for example 414720."
        )
    if len(digits) < MIN_BIN_LENGTH:
        raise ValidationError(
            f"A BIN needs at least {MIN_BIN_LENGTH} digits. You entered {len(digits)}."
        )
    if len(digits) > MAX_BIN_LENGTH:
        raise ValidationError(
            "That looks longer than a BIN. Bin-Tel only searches issuer identification "
            "numbers — never full card numbers. Enter the first 6 or 8 digits."
        )
    return BinInput(digits=digits)


def validate_iin_length(value: int | None) -> int | None:
    """IIN length must be one of the ISO/IEC 7812 canonical lengths."""
    if value is None:
        return None
    if value not in CANONICAL_IIN_LENGTHS:
        raise ValidationError(f"IIN length must be one of {CANONICAL_IIN_LENGTHS}, got {value}.")
    return value


def parse_bin_range(value: str) -> tuple[str, str]:
    """Parse ``"414720-414729"`` into its two endpoints.

    A single BIN is treated as a one-element range, which lets the importers
    accept both shapes from the same column.
    """
    raw = (value or "").strip()
    if not raw:
        raise ValidationError("Enter a BIN range.")
    parts = [part for part in _RANGE_SEPARATORS.split(raw) if part]
    if len(parts) == 1:
        single = clean_digits(parts[0])
        if not single:
            raise ValidationError(f"Could not read a BIN range from {raw!r}.")
        return single, single
    if len(parts) != 2:
        raise ValidationError(f"Could not read a BIN range from {raw!r}.")
    low, high = clean_digits(parts[0]), clean_digits(parts[1])
    if not low or not high:
        raise ValidationError(f"Could not read a BIN range from {raw!r}.")
    if len(low) != len(high):
        width = max(len(low), len(high))
        low = low.ljust(width, "0")
        high = high.ljust(width, "9")
    if int(low) > int(high):
        low, high = high, low
    return low, high


def validate_search_term(value: str, *, minimum: int = 2) -> str:
    """Validate an institution-name search term."""
    term = " ".join((value or "").split())
    if len(term) < minimum:
        raise ValidationError(
            f"Enter at least {minimum} characters to search for a financial institution."
        )
    return term


def looks_like_bin(value: str) -> bool:
    """Heuristic used to auto-select the lookup mode as the user types."""
    stripped = (value or "").strip()
    if not stripped:
        return False
    digits = clean_digits(stripped)
    return bool(_DIGITS_ONLY.match(stripped.replace(" ", "").replace("-", ""))) and bool(digits)


def is_sensitive_length(value: str) -> bool:
    """True when a numeric input is long enough to be a real card number.

    Used to refuse the input outright rather than silently truncating it.
    """
    return len(clean_digits(value)) >= 12
