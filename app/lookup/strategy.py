"""How a match was found, and how specific it is.

Specificity is the backbone of the engine: when several records could answer a
query, the *narrowest* allocation that legitimately contains it wins. A
six-digit root must never override an eight-digit assignment or an account
range beneath it, because in modern eight-digit issuance the root can be shared
between issuers — answering from the root would name the wrong bank.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum


class LookupStrategy(StrEnum):
    """Which route produced a match. Internal; not shown as a data source."""

    EXACT_8 = "exact_8_digit"
    EXACT_ASSIGNED = "exact_assigned_bin"
    ACCOUNT_RANGE = "account_range"
    EXACT_6 = "exact_6_digit"
    BROADER_RANGE = "broader_range"
    ROOT_PREFIX = "root_prefix"
    CANONICAL_INSTITUTION = "canonical_institution"
    WEAK_INFERENCE = "weak_inference"
    NONE = "none"

    @property
    def label(self) -> str:
        return {
            LookupStrategy.EXACT_8: "8-digit BIN",
            LookupStrategy.EXACT_ASSIGNED: "Assigned BIN",
            LookupStrategy.ACCOUNT_RANGE: "Account range",
            LookupStrategy.EXACT_6: "6-digit IIN",
            LookupStrategy.BROADER_RANGE: "Issuer range",
            LookupStrategy.ROOT_PREFIX: "6-digit root",
            LookupStrategy.CANONICAL_INSTITUTION: "Institution record",
            LookupStrategy.WEAK_INFERENCE: "Inferred",
            LookupStrategy.NONE: "No match",
        }[self]

    @property
    def specificity(self) -> MatchSpecificity:
        return _SPECIFICITY[self]

    @property
    def is_exact(self) -> bool:
        """Whether the query matched an assignment of its own length."""
        return self in (
            LookupStrategy.EXACT_8,
            LookupStrategy.EXACT_ASSIGNED,
            LookupStrategy.EXACT_6,
        )


class MatchSpecificity(IntEnum):
    """How narrow the matched allocation is. Higher wins.

    Ordering here *is* the precedence rule. Comparing members is how the
    engine decides between competing records, so a change to this order is a
    change to what the application will answer.
    """

    NONE = 0
    INFERRED = 1
    #: The six-digit root, where nothing more specific was found.
    ROOT = 2
    #: A range wider than the query.
    BROAD_RANGE = 3
    #: A six-digit assignment matching a six-digit query exactly.
    EXACT_ROOT = 4
    #: A network account range — the most specific authoritative allocation.
    ACCOUNT_RANGE = 5
    #: An eight-digit assignment matching the query exactly.
    EXACT_EXTENDED = 6

    @property
    def label(self) -> str:
        return {
            MatchSpecificity.NONE: "No match",
            MatchSpecificity.INFERRED: "Inferred",
            MatchSpecificity.ROOT: "6-digit root",
            MatchSpecificity.BROAD_RANGE: "Broader range",
            MatchSpecificity.EXACT_ROOT: "Exact 6-digit",
            MatchSpecificity.ACCOUNT_RANGE: "Account range",
            MatchSpecificity.EXACT_EXTENDED: "Exact 8-digit",
        }[self]


_SPECIFICITY: dict[LookupStrategy, MatchSpecificity] = {
    LookupStrategy.EXACT_8: MatchSpecificity.EXACT_EXTENDED,
    LookupStrategy.EXACT_ASSIGNED: MatchSpecificity.EXACT_EXTENDED,
    LookupStrategy.ACCOUNT_RANGE: MatchSpecificity.ACCOUNT_RANGE,
    LookupStrategy.EXACT_6: MatchSpecificity.EXACT_ROOT,
    LookupStrategy.BROADER_RANGE: MatchSpecificity.BROAD_RANGE,
    LookupStrategy.ROOT_PREFIX: MatchSpecificity.ROOT,
    LookupStrategy.CANONICAL_INSTITUTION: MatchSpecificity.INFERRED,
    LookupStrategy.WEAK_INFERENCE: MatchSpecificity.INFERRED,
    LookupStrategy.NONE: MatchSpecificity.NONE,
}
