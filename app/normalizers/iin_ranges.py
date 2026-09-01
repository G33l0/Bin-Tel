"""Published IIN allocations, and what can honestly be derived from them.

The first digits of a card number identify the scheme. That is not a guess: it
is the ISO/IEC 7812 major-industry structure plus each scheme's own published
issuer-identification ranges, and it is the one attribute of a BIN that can be
established from the digits alone.

Nothing else can. The card type, the funding type, the issuing bank, the
country — none of those follow from the number, and none is derived here. A
BIN starting ``4`` is a Visa BIN; it says nothing about who issued it.

Two rules keep this from producing a false positive:

* **Overlaps are refused, not guessed.** ``622126``–``622925`` is claimed by
  both Discover and UnionPay. Where more than one scheme's published range
  covers a prefix, :func:`network_for_prefix` returns nothing rather than
  picking. Ambiguity is reported as unknown, which is the honest answer.
* **A derivation never overrides a stated value.** If the list says which
  scheme it is, the list wins; the table only fills a blank.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: One published allocation: the scheme, and the inclusive prefix range that
#: scheme announces. ``low`` and ``high`` are compared digit-for-digit over
#: their own width, so ``("51", "55")`` covers 51xxxx through 55xxxx.
Allocation = tuple[str, str, str]

#: Scheme-published issuer identification ranges.
#:
#: Deliberately conservative: only allocations a scheme states publicly and
#: unambiguously are listed. Maestro and the co-badged domestic schemes are
#: left out entirely, because their ranges overlap the global schemes in ways
#: that cannot be settled from the digits.
ALLOCATIONS: tuple[Allocation, ...] = (
    # Visa — the whole of major industry identifier 4.
    ("visa", "4", "4"),
    # Mastercard — the historic 51–55 block and the 2-series added in 2017.
    ("mastercard", "51", "55"),
    ("mastercard", "2221", "2720"),
    # American Express.
    ("amex", "34", "34"),
    ("amex", "37", "37"),
    # Diners Club.
    ("diners", "300", "305"),
    ("diners", "3095", "3095"),
    ("diners", "36", "36"),
    ("diners", "38", "39"),
    # JCB.
    ("jcb", "3528", "3589"),
    # Discover — 6011, the 644–649 block, 65, and the 622126–622925 block.
    # That last one is also inside UnionPay's published 62, and both are listed
    # deliberately: a prefix in it is genuinely dual-claimed, and the honest
    # answer is that the digits do not settle it. See network_for_prefix.
    ("discover", "6011", "6011"),
    ("discover", "622126", "622925"),
    ("discover", "644", "649"),
    ("discover", "65", "65"),
    # UnionPay.
    ("unionpay", "62", "62"),
    # Mir.
    ("mir", "2200", "2204"),
    # UATP, which uses major industry identifier 1.
    ("uatp", "1", "1"),
)

_DIGITS = re.compile(r"^\d+$")


@dataclass(frozen=True, slots=True)
class PrefixMatch:
    """What the digits alone establish about a prefix."""

    #: The scheme code, or ``None`` when nothing or more than one thing matched.
    network: str | None
    #: Every scheme whose published range covers the prefix. More than one
    #: means the prefix is genuinely ambiguous.
    candidates: tuple[str, ...] = ()
    #: The allocation that matched, for the audit trail (``"51-55"``).
    rule: str = ""

    @property
    def is_certain(self) -> bool:
        return self.network is not None

    @property
    def is_ambiguous(self) -> bool:
        return len(self.candidates) > 1


def _covers(low: str, high: str, digits: str) -> bool:
    """Whether *digits* falls inside a published range of ``len(low)`` width."""
    width = len(low)
    if len(digits) < width:
        return False
    head = digits[:width]
    return low <= head <= high


def candidates_for_prefix(digits: str) -> tuple[str, ...]:
    """Every scheme whose published range covers *digits*, narrowest first."""
    if not digits or not _DIGITS.match(digits):
        return ()
    matched: list[tuple[int, str]] = []
    for network, low, high in ALLOCATIONS:
        if _covers(low, high, digits):
            matched.append((len(low), network))
    # A longer published prefix is a more specific allocation, but it does not
    # override a shorter one from a *different* scheme: that is the overlap
    # case, and it stays ambiguous.
    seen: set[str] = set()
    ordered: list[str] = []
    for _, network in sorted(matched, key=lambda item: -item[0]):
        if network not in seen:
            seen.add(network)
            ordered.append(network)
    return tuple(ordered)


def network_for_prefix(digits: str) -> PrefixMatch:
    """The scheme the digits establish, or nothing when they do not settle it.

    Returns a match with ``network=None`` for an unallocated prefix *and* for
    one two schemes both claim. Both are "not established", and the caller
    treats them the same way: leave the field alone.
    """
    found = candidates_for_prefix(digits)
    if len(found) != 1:
        return PrefixMatch(network=None, candidates=found)

    network = found[0]
    rule = next(
        (
            f"{low}-{high}" if low != high else low
            for code, low, high in ALLOCATIONS
            if code == network and _covers(low, high, digits)
        ),
        "",
    )
    return PrefixMatch(network=network, candidates=found, rule=f"iin:{network}:{rule}")


__all__ = [
    "ALLOCATIONS",
    "PrefixMatch",
    "candidates_for_prefix",
    "network_for_prefix",
]
