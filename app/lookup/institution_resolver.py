"""Institution entity resolution.

Deciding that two names refer to the same institution is the single most
dangerous operation in this application. Get it wrong in one direction and a
bank's BINs are split across two records; get it wrong in the other and one
bank is credited with another's portfolio.

So resolution is graded, and it is explicit about how sure it is:

=========== =============================================================
Match type  What was established
=========== =============================================================
EXACT       The legal name matched exactly
CANONICAL   The normalized display name matched, in the same country
ALIAS       A recorded alias matched
STRONG      Names agree and independent identifiers corroborate it
POSSIBLE    A plausible candidate — offered, never applied
CONFLICT    Several equally good candidates; the caller must choose
UNKNOWN     Nothing matched well enough to say
=========== =============================================================

Only the first four are safe to act on automatically. ``POSSIBLE`` and
``CONFLICT`` produce candidates for a human or a staging review to settle, and
``UNKNOWN`` means a new institution record, not a guess at an existing one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging_config import get_logger
from app.models.entities import Institution, InstitutionAlias
from app.normalizers.confidence import MERGE_THRESHOLD, MatchScore
from app.normalizers.name_normalizer import name_normalizer
from app.normalizers.text import squash

logger = get_logger(__name__)


class MatchType(StrEnum):
    EXACT = "exact"
    CANONICAL = "canonical"
    ALIAS = "alias"
    STRONG = "strong"
    POSSIBLE = "possible"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        return self.value.capitalize()

    @property
    def is_actionable(self) -> bool:
        """Whether this match may be applied without a human deciding."""
        return self in (
            MatchType.EXACT,
            MatchType.CANONICAL,
            MatchType.ALIAS,
            MatchType.STRONG,
        )


@dataclass(slots=True)
class InstitutionMatch:
    """One candidate institution, with how it was reached."""

    institution_id: int
    display_name: str
    match_type: MatchType
    confidence: float
    reasons: list[str] = field(default_factory=list)

    @property
    def is_actionable(self) -> bool:
        return self.match_type.is_actionable


@dataclass(slots=True)
class Resolution:
    """The outcome of resolving one name."""

    match_type: MatchType = MatchType.UNKNOWN
    institution_id: int | None = None
    display_name: str = ""
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)
    #: Every candidate considered, best first. Populated for POSSIBLE and
    #: CONFLICT so the caller can show what the choice was between.
    candidates: list[InstitutionMatch] = field(default_factory=list)

    @property
    def resolved(self) -> bool:
        return self.institution_id is not None and self.match_type.is_actionable


class InstitutionResolver:
    """Resolves a name to an institution, or declines to."""

    #: Below this, a candidate is not even offered as a possibility.
    #:
    #: Deliberately lower than the merge threshold: this decides what is
    #: *considered*, and a misspelling that a person would recognise should
    #: reach the caller as a candidate. Whether anything is applied is decided
    #: afterwards by corroboration, not by this number.
    CANDIDATE_THRESHOLD = 0.55

    def __init__(self, session: Session) -> None:
        self._session = session

    def resolve(
        self,
        name: str | None,
        *,
        legal_name: str | None = None,
        country_code: str | None = None,
        website: str | None = None,
        swift_bic: str | None = None,
        postal_code: str | None = None,
        shared_bins: int = 0,
    ) -> Resolution:
        """Resolve *name*, using whatever corroborating detail is available."""
        display = name_normalizer.clean_display(name or "")
        if not display:
            return Resolution(reasons=["No institution name was supplied."])

        normalized = name_normalizer.normalize(display)

        # 1. An exact legal-name match is the strongest thing short of an
        #    identifier: legal names are registered, not descriptive.
        if legal_name:
            exact = self._by_legal_name(legal_name, country_code)
            if exact is not None:
                return _resolved(
                    exact,
                    MatchType.EXACT,
                    0.99,
                    "Legal name matched exactly",
                )

        # 2. The canonical normalized name, within the same country.
        canonical = self._by_normalized_name(normalized.normalized, country_code)
        if len(canonical) == 1:
            return _resolved(
                canonical[0],
                MatchType.CANONICAL,
                0.96,
                "Canonical name matched",
            )
        if len(canonical) > 1:
            # The same canonical name in the same market, twice. That is a
            # duplicate in the data, not something to silently pick between.
            return Resolution(
                match_type=MatchType.CONFLICT,
                reasons=[
                    f"{len(canonical)} institutions share this canonical name "
                    "in the same country"
                ],
                candidates=[
                    InstitutionMatch(
                        institution_id=item.id,
                        display_name=item.display_name,
                        match_type=MatchType.CANONICAL,
                        confidence=0.9,
                        reasons=["Canonical name matched"],
                    )
                    for item in canonical
                ],
            )

        # 3. A recorded alias. Aliases are curated, so a hit is trustworthy.
        alias_hit = self._by_alias(normalized.normalized, country_code)
        if alias_hit is not None:
            return _resolved(
                alias_hit,
                MatchType.ALIAS,
                0.93,
                "Matched a recorded alias",
            )

        # 4. Scored comparison against plausible candidates. A high name score
        #    alone is never enough — corroboration decides.
        scored = self._scored_candidates(
            display,
            country_code=country_code,
            website=website,
            swift_bic=swift_bic,
            postal_code=postal_code,
            shared_bins=shared_bins,
        )
        if not scored:
            return Resolution(reasons=["No institution matched this name."])

        best_institution, best_score = scored[0]
        candidates = [
            InstitutionMatch(
                institution_id=item.id,
                display_name=item.display_name,
                match_type=(
                    MatchType.STRONG if score.can_merge else MatchType.POSSIBLE
                ),
                confidence=round(score.score, 4),
                reasons=[score.reason] if score.reason else [],
            )
            for item, score in scored
        ]

        strong = [item for item in candidates if item.match_type is MatchType.STRONG]
        if len(strong) > 1:
            return Resolution(
                match_type=MatchType.CONFLICT,
                reasons=["Several institutions match this name equally well"],
                candidates=candidates,
            )
        if best_score.can_merge:
            return Resolution(
                match_type=MatchType.STRONG,
                institution_id=best_institution.id,
                display_name=best_institution.display_name,
                confidence=round(best_score.score, 4),
                reasons=[best_score.reason or "Name and identifiers agree"],
                candidates=candidates,
            )

        # Plausible, but nothing corroborates it. Offered, not applied.
        return Resolution(
            match_type=MatchType.POSSIBLE,
            confidence=round(best_score.score, 4),
            reasons=[
                "Name is similar, but nothing independent corroborates the match"
            ],
            candidates=candidates,
        )

    # -- lookups -----------------------------------------------------------
    def _by_legal_name(
        self, legal_name: str, country_code: str | None
    ) -> Institution | None:
        normalized = name_normalizer.normalized_form(legal_name)
        if not normalized:
            return None
        statement = select(Institution).where(
            Institution.normalized_legal_name == normalized
        )
        rows = self._session.execute(statement).scalars().all()
        return self._single_in_country(rows, country_code)

    def _by_normalized_name(
        self, normalized: str, country_code: str | None
    ) -> list[Institution]:
        rows = (
            self._session.execute(
                select(Institution).where(Institution.normalized_name == normalized)
            )
            .scalars()
            .all()
        )
        if country_code:
            scoped = [
                item
                for item in rows
                if item.country is not None and item.country.iso2 == country_code
            ]
            # A name that matches in the requested country is the answer; a
            # match in a different country is a different institution.
            if scoped:
                return scoped
            return []
        return list(rows)

    def _by_alias(self, normalized: str, country_code: str | None) -> Institution | None:
        rows = (
            self._session.execute(
                select(Institution)
                .join(InstitutionAlias)
                .where(InstitutionAlias.normalized_alias == normalized)
            )
            .unique()
            .scalars()
            .all()
        )
        return self._single_in_country(rows, country_code)

    def _scored_candidates(
        self,
        display: str,
        *,
        country_code: str | None,
        website: str | None,
        swift_bic: str | None,
        postal_code: str | None,
        shared_bins: int,
    ) -> list[tuple[Institution, MatchScore]]:
        """Score blocked candidates, best first."""
        blocks = _blocking_keys(display)
        if not blocks:
            return []
        condition = Institution.normalized_name.in_(blocks) | Institution.short_name.in_(
            blocks
        )
        lead = _leading_key(display)
        if lead:
            # A typo in the first token would otherwise defeat exact blocking
            # entirely, and the candidate would never be scored at all. This
            # widens what is *considered*; it never widens what may match.
            condition = condition | Institution.normalized_name.startswith(lead)
        rows = (
            self._session.execute(
                select(Institution).where(condition).limit(_CANDIDATE_LIMIT)
            )
            .scalars()
            .all()
        )
        scored: list[tuple[Institution, MatchScore]] = []
        for item in rows:
            score = name_normalizer.match(
                display,
                item.display_name,
                left_country=country_code,
                right_country=item.country.iso2 if item.country else None,
                left_website=website,
                right_website=item.website,
                left_swift=swift_bic,
                right_swift=item.swift_bic,
                left_postal=postal_code,
                right_postal=_primary_postal(item),
                shared_bins=shared_bins,
            )
            if score.score >= self.CANDIDATE_THRESHOLD:
                scored.append((item, score))
        scored.sort(key=lambda pair: pair[1].score, reverse=True)
        return scored

    @staticmethod
    def _single_in_country(
        rows: list[Institution], country_code: str | None
    ) -> Institution | None:
        if not rows:
            return None
        if country_code:
            scoped = [
                item
                for item in rows
                if item.country is not None and item.country.iso2 == country_code
            ]
            if len(scoped) == 1:
                return scoped[0]
            if scoped:
                return None  # ambiguous within the country
            return None
        return rows[0] if len(rows) == 1 else None


#: How many blocked candidates are ever scored for one name. Blocking is a
#: performance device; a name that pulls in more than this is too generic for
#: the extra candidates to be informative.
_CANDIDATE_LIMIT = 200
#: How many leading characters of the first token form the fuzzy block.
_LEADING_KEY_LENGTH = 4


def _leading_key(display: str) -> str:
    """The first few characters of the normalized name, for typo tolerance."""
    normalized = name_normalizer.normalized_form(display)
    return normalized[:_LEADING_KEY_LENGTH] if len(normalized) >= _LEADING_KEY_LENGTH else ""


def _blocking_keys(display: str) -> set[str]:
    """Cheap keys that bring plausible candidates into memory.

    Blocking is a performance device, not a matching rule: everything it
    returns is still scored properly. It never widens what may match.
    """
    normalized = name_normalizer.normalize(display)
    keys = {normalized.normalized, normalized.core}
    if normalized.acronym and len(normalized.acronym) >= 3:
        keys.add(normalized.acronym)
    tokens = normalized.normalized.split()
    if tokens:
        keys.add(tokens[0])
    return {key for key in keys if key}


def _primary_postal(institution: Institution) -> str | None:
    for address in institution.addresses:
        if address.is_primary and address.normalized_postal_code:
            return address.normalized_postal_code
    return None


def _resolved(
    institution: Institution, match_type: MatchType, confidence: float, reason: str
) -> Resolution:
    return Resolution(
        match_type=match_type,
        institution_id=institution.id,
        display_name=institution.display_name,
        confidence=confidence,
        reasons=[reason],
        candidates=[
            InstitutionMatch(
                institution_id=institution.id,
                display_name=institution.display_name,
                match_type=match_type,
                confidence=confidence,
                reasons=[reason],
            )
        ],
    )


__all__ = [
    "InstitutionMatch",
    "InstitutionResolver",
    "MatchType",
    "Resolution",
]
