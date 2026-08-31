"""BIN/IIN normalization.

Every BIN entering the database passes through :class:`BinNormalizer`, which
guarantees a single canonical shape: digits only, a numeric form padded to a
fixed width for range comparison, and derived 6/8-digit prefixes.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.errors import ValidationError
from app.utils.validators import (
    CANONICAL_IIN_LENGTHS,
    MAX_BIN_LENGTH,
    MIN_BIN_LENGTH,
    clean_digits,
    parse_bin_range,
)

#: Width every BIN is padded to for integer range comparison. Eight digits
#: covers ISO/IEC 7812 IINs with room for scheme sub-ranges.
RANGE_WIDTH = 8


@dataclass(frozen=True, slots=True)
class NormalizedBin:
    bin: str
    bin_int: int
    prefix6: str
    prefix8: str
    iin: str
    iin_length: int
    range_low: int
    range_high: int

    @property
    def is_canonical_iin(self) -> bool:
        return self.iin_length in CANONICAL_IIN_LENGTHS


@dataclass(frozen=True, slots=True)
class NormalizedRange:
    low: str
    high: str
    low_int: int
    high_int: int
    width: int

    @property
    def size(self) -> int:
        return self.high_int - self.low_int + 1

    def contains(self, value: int) -> bool:
        return self.low_int <= value <= self.high_int


class BinNormalizer:
    """Stateless normalizer for BIN, IIN and BIN-range values."""

    width: int = RANGE_WIDTH

    def normalize(self, value: str | int) -> NormalizedBin:
        digits = clean_digits(str(value))
        if not digits:
            raise ValidationError(f"{value!r} does not contain any digits.")
        if len(digits) < MIN_BIN_LENGTH:
            raise ValidationError(
                f"{value!r} is too short to be a BIN (minimum {MIN_BIN_LENGTH} digits)."
            )
        if len(digits) > MAX_BIN_LENGTH:
            raise ValidationError(
                f"{value!r} is longer than a BIN. Bin-Tel stores issuer prefixes only."
            )
        padded_low = digits.ljust(self.width, "0")[: self.width]
        padded_high = digits.ljust(self.width, "9")[: self.width]
        iin_length = len(digits) if len(digits) in CANONICAL_IIN_LENGTHS else self._nearest_iin(digits)
        return NormalizedBin(
            bin=digits,
            bin_int=int(padded_low),
            prefix6=digits[:6].ljust(6, "0") if len(digits) >= 6 else digits.ljust(6, "0"),
            prefix8=digits[:8].ljust(8, "0"),
            iin=digits[:iin_length].ljust(iin_length, "0"),
            iin_length=iin_length,
            range_low=int(padded_low),
            range_high=int(padded_high),
        )

    @staticmethod
    def _nearest_iin(digits: str) -> int:
        """Map a non-canonical length onto the nearest ISO/IEC 7812 length."""
        return 6 if len(digits) <= 7 else 8

    def normalize_range(self, value: str, high: str | None = None) -> NormalizedRange:
        """Normalize ``"414720-414729"`` or an explicit ``(low, high)`` pair."""
        if high is not None:
            low_digits, high_digits = clean_digits(str(value)), clean_digits(str(high))
            if not low_digits or not high_digits:
                raise ValidationError(f"Could not read a BIN range from {value!r}–{high!r}.")
            if len(low_digits) != len(high_digits):
                width = max(len(low_digits), len(high_digits))
                low_digits = low_digits.ljust(width, "0")
                high_digits = high_digits.ljust(width, "9")
            if int(low_digits) > int(high_digits):
                low_digits, high_digits = high_digits, low_digits
        else:
            low_digits, high_digits = parse_bin_range(str(value))

        width = max(len(low_digits), len(high_digits), MIN_BIN_LENGTH)
        return NormalizedRange(
            low=low_digits,
            high=high_digits,
            low_int=int(low_digits.ljust(self.width, "0")[: self.width]),
            high_int=int(high_digits.ljust(self.width, "9")[: self.width]),
            width=width,
        )

    def to_search_bounds(self, value: str) -> tuple[int, int]:
        """Integer bounds a prefix query must scan, for the range index."""
        normalized = self.normalize(value)
        return normalized.range_low, normalized.range_high

    def format_range(self, low: str, high: str) -> str:
        """Human-readable range string, collapsing a single-BIN range."""
        return low if low == high else f"{low}–{high}"


#: Shared, stateless instance.
bin_normalizer = BinNormalizer()
