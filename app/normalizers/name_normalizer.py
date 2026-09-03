"""Institution name normalization and evidence-based matching.

``"JPMORGAN CHASE BANK, N.A."``, ``"JPMorgan Chase Bank NA"`` and
``"JPMorgan Chase"`` may describe the same institution — but they may equally
describe a legal entity, its retail brand, and an unrelated firm with a similar
name. This module therefore separates two jobs:

* *normalization* — a deterministic canonical form used for indexing;
* *matching* — a score plus the evidence behind it, which the deduplication
  service uses to decide whether a merge is defensible.

Names are never merged on string similarity alone; see
:class:`app.normalizers.confidence.MatchScore`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlparse

from app.normalizers.confidence import (
    MatchEvidence,
    MatchScore,
    combine,
    string_similarity,
    token_overlap,
)
from app.normalizers.reference import (
    ABBREVIATIONS,
    GENERIC_TOKENS,
    LEGAL_SUFFIXES,
    STOPWORDS,
)
from app.normalizers.text import (
    collapse_whitespace,
    initialism,
    sanitise_text,
    squash,
    strip_accents,
)

#: Suffixes sorted longest-first so "national association" wins over "na".
_SUFFIX_TOKENS: tuple[tuple[str, ...], ...] = tuple(
    sorted((tuple(suffix.split()) for suffix in LEGAL_SUFFIXES), key=len, reverse=True)
)

_BRANCH_NOISE = re.compile(
    r"\b(branch|bank branch|head office|main office|hq|headquarters|division|dept|department)\b"
)
_PAREN = re.compile(r"\([^)]*\)")


@dataclass(frozen=True, slots=True)
class NormalizedName:
    """The canonical forms derived from one raw institution name."""

    raw: str
    display: str
    normalized: str
    core: str
    tokens: tuple[str, ...]
    core_tokens: tuple[str, ...]
    acronym: str
    dropped_suffixes: tuple[str, ...]

    @property
    def is_empty(self) -> bool:
        return not self.normalized


class NameNormalizer:
    """Canonicalises institution names and scores candidate matches."""

    def normalize(self, value: str | None) -> NormalizedName:
        """Canonical forms for one raw name.

        Memoized because a real BIN list names the same handful of banks over
        and over — 343,000 rows resolved to 12,405 institutions — and the
        derivation underneath runs a few dozen regular expressions each time.
        The result is a frozen dataclass of strings and tuples, so a cached one
        cannot be mutated by whoever receives it.

        The shared cache is keyed on the string alone, so it is used only by
        the canonical normalizer. A subclass that changed the derivation would
        otherwise silently get the base class's answers.
        """
        if type(self) is not NameNormalizer:
            return self._normalize_uncached(str(value or ""))
        return _normalize_cached(str(value or ""))

    def _normalize_uncached(self, value: str) -> NormalizedName:
        raw = str(value or "")
        display = self.clean_display(raw)
        squashed = squash(display)
        squashed = _BRANCH_NOISE.sub(" ", squashed)
        squashed = collapse_whitespace(squashed)

        all_tokens = self._expand_abbreviations(squashed.split())
        core_tokens, dropped = self._strip_suffixes(all_tokens)
        # ``core`` additionally drops stopwords, which is what name matching
        # compares; ``normalized`` keeps them so the index stays predictable.
        core = " ".join(token for token in core_tokens if token not in STOPWORDS)

        return NormalizedName(
            raw=raw,
            display=display,
            normalized=" ".join(all_tokens),
            core=core or squashed,
            tokens=tuple(all_tokens),
            core_tokens=tuple(core.split()),
            acronym=initialism(" ".join(core_tokens)),
            dropped_suffixes=tuple(dropped),
        )

    def normalized_form(self, value: str | None) -> str:
        """Shorthand for the value stored in ``institutions.normalized_name``."""
        return self.normalize(value).normalized

    def core_form(self, value: str | None) -> str:
        return self.normalize(value).core

    @staticmethod
    def clean_display(value: str) -> str:
        """Tidy a name for display without altering the words themselves."""
        # Control characters are stripped and the length bounded before
        # anything else: a NUL is accepted by SQLite but truncates the
        # value wherever C string handling meets it, and an unbounded
        # paste would be stored whole despite the column declaring 256.
        text = collapse_whitespace((sanitise_text(value) or "").replace(" ", " "))
        text = _PAREN.sub(lambda match: match.group(0), text)
        # Trim trailing punctuation left over from CSV exports.
        return text.strip(" ,;:-–—")

    @staticmethod
    def _expand_abbreviations(tokens: list[str]) -> list[str]:
        """Expand whole-token banking abbreviations (``cu`` → ``credit union``).

        Only exact tokens are expanded, so a name that genuinely contains the
        letters is untouched — the abbreviation has to stand alone.
        """
        expanded: list[str] = []
        for token in tokens:
            replacement = ABBREVIATIONS.get(token)
            expanded.extend(replacement.split() if replacement else [token])
        return expanded

    @staticmethod
    def _strip_suffixes(tokens: list[str]) -> tuple[list[str], list[str]]:
        """Remove trailing legal-form suffixes, repeatedly (``"Bank AG SE"``)."""
        remaining = list(tokens)
        dropped: list[str] = []
        changed = True
        while changed and len(remaining) > 1:
            changed = False
            for suffix in _SUFFIX_TOKENS:
                length = len(suffix)
                if length < len(remaining) and tuple(remaining[-length:]) == suffix:
                    dropped.append(" ".join(suffix))
                    del remaining[-length:]
                    changed = True
                    break
        return remaining, dropped

    # -- alias generation -------------------------------------------------
    def candidate_aliases(self, value: str | None) -> list[str]:
        """Additional spellings worth indexing for a given name.

        These are *search* aliases, not claims about the institution; they only
        make an existing record findable under a form a user might type.
        """
        normalized = self.normalize(value)
        if normalized.is_empty:
            return []
        aliases: list[str] = []
        display = normalized.display
        no_accents = strip_accents(display)
        if no_accents != display:
            aliases.append(no_accents)
        if normalized.core and normalized.core != normalized.normalized:
            aliases.append(normalized.core)
        if len(normalized.core_tokens) >= 3 and len(normalized.acronym) >= 3:
            aliases.append(normalized.acronym)
        if "&" in display:
            aliases.append(display.replace("&", "and"))
        elif " and " in display.lower():
            aliases.append(re.sub(r"(?i)\band\b", "&", display))
        seen: set[str] = set()
        unique: list[str] = []
        for alias in aliases:
            key = squash(alias)
            if key and key != squash(display) and key not in seen:
                seen.add(key)
                unique.append(collapse_whitespace(alias))
        return unique

    # -- matching ---------------------------------------------------------
    def similarity(self, left: str | None, right: str | None) -> float:
        """Name-only similarity in ``[0, 1]``. Never sufficient to merge."""
        first, second = self.normalize(left), self.normalize(right)
        if first.is_empty or second.is_empty:
            return 0.0
        if first.normalized == second.normalized:
            return 1.0
        if first.core and first.core == second.core:
            return 0.97

        weights = {token: 0.35 for token in GENERIC_TOKENS}
        overlap = token_overlap(list(first.core_tokens), list(second.core_tokens), weights=weights)
        literal = string_similarity(first.core, second.core)

        # A shorter name that is a clean prefix of a longer one ("JPMorgan
        # Chase" vs "JPMorgan Chase Bank") is a strong but not decisive signal.
        prefix_bonus = 0.0
        short, long = sorted((first.core_tokens, second.core_tokens), key=len)
        if short and long[: len(short)] == short:
            prefix_bonus = 0.9

        acronym_bonus = 0.0
        if first.acronym and second.acronym:
            if first.acronym == second.acronym and len(first.acronym) >= 3:
                acronym_bonus = 0.75
            elif first.acronym == second.core.replace(" ", "").upper():
                acronym_bonus = 0.7

        return combine(
            (overlap, 3.0),
            (literal, 2.0),
            (prefix_bonus, 1.5 if prefix_bonus else 0.0),
            (acronym_bonus, 1.0 if acronym_bonus else 0.0),
        )

    def match(
        self,
        left_name: str | None,
        right_name: str | None,
        *,
        left_country: str | None = None,
        right_country: str | None = None,
        left_website: str | None = None,
        right_website: str | None = None,
        left_swift: str | None = None,
        right_swift: str | None = None,
        shared_bins: int = 0,
        alias_match: bool = False,
        left_postal: str | None = None,
        right_postal: str | None = None,
    ) -> MatchScore:
        """Score two institutions and record the evidence behind the score."""
        evidence = MatchEvidence(
            same_country=bool(left_country and right_country and left_country == right_country),
            shared_bins=shared_bins,
            same_website_host=self._same_host(left_website, right_website),
            same_swift_bic=bool(
                left_swift and right_swift and left_swift[:6].upper() == right_swift[:6].upper()
            ),
            same_postal_code=bool(left_postal and right_postal and left_postal == right_postal),
            alias_match=alias_match,
        )

        name_score = 1.0 if alias_match else self.similarity(left_name, right_name)

        # Different countries are a strong negative signal, so a coincidental
        # name collision across markets never reaches the merge threshold.
        if left_country and right_country and left_country != right_country:
            name_score *= 0.55
            evidence.notes.append("Countries differ")

        boost = 0.0
        if evidence.same_swift_bic:
            boost += 0.06
        if evidence.shared_bins:
            boost += 0.04
        if evidence.same_website_host:
            boost += 0.04
        score = min(1.0, name_score + boost)

        reason = self._describe(name_score, evidence)
        return MatchScore(score=score, evidence=evidence, reason=reason)

    @staticmethod
    def _same_host(left: str | None, right: str | None) -> bool:
        def host(url: str | None) -> str:
            if not url:
                return ""
            candidate = url if "//" in url else f"https://{url}"
            netloc = urlparse(candidate).netloc.lower()
            return netloc[4:] if netloc.startswith("www.") else netloc

        left_host, right_host = host(left), host(right)
        return bool(left_host) and left_host == right_host

    @staticmethod
    def _describe(name_score: float, evidence: MatchEvidence) -> str:
        parts = [f"name similarity {name_score:.2f}"]
        if evidence.alias_match:
            parts.append("known alias")
        if evidence.same_swift_bic:
            parts.append("same SWIFT/BIC")
        if evidence.shared_bins:
            parts.append(f"{evidence.shared_bins} shared BIN(s)")
        if evidence.same_website_host:
            parts.append("same website")
        if evidence.same_country:
            parts.append("same country")
        parts.extend(evidence.notes)
        return "; ".join(parts)


name_normalizer = NameNormalizer()


@lru_cache(maxsize=100_000)
def _normalize_cached(raw: str) -> NormalizedName:
    """The memoized body of :meth:`NameNormalizer.normalize`.

    Bounded rather than unbounded: an import of unique names must not be able
    to grow this without limit.
    """
    return name_normalizer._normalize_uncached(raw)
