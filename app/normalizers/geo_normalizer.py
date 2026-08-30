"""Country, subdivision, city and postal-code normalization."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.normalizers.reference import (
    BY_ISO2,
    BY_ISO3,
    BY_NUMERIC,
    COUNTRY_ALIASES,
    SUBDIVISION_NAMES,
    SUBDIVISIONS,
    CountryRecord,
)
from app.normalizers.text import collapse_whitespace, squash, strip_accents

_POSTAL_CLEAN = re.compile(r"[^A-Z0-9]")

#: Postal-code shapes for the markets where a canonical format matters.
_POSTAL_FORMATTERS: dict[str, tuple[re.Pattern[str], str]] = {
    "US": (re.compile(r"^(\d{5})(\d{4})?$"), r"\1-\2"),
    "CA": (re.compile(r"^([A-Z]\d[A-Z])(\d[A-Z]\d)$"), r"\1 \2"),
    "GB": (re.compile(r"^([A-Z]{1,2}\d[A-Z\d]?)(\d[A-Z]{2})$"), r"\1 \2"),
    "NL": (re.compile(r"^(\d{4})([A-Z]{2})$"), r"\1 \2"),
    "PL": (re.compile(r"^(\d{2})(\d{3})$"), r"\1-\2"),
    "JP": (re.compile(r"^(\d{3})(\d{4})$"), r"\1-\2"),
    "BR": (re.compile(r"^(\d{5})(\d{3})$"), r"\1-\2"),
    "PT": (re.compile(r"^(\d{4})(\d{3})$"), r"\1-\2"),
}


@dataclass(frozen=True, slots=True)
class NormalizedRegion:
    code: str | None
    name: str | None


class GeoNormalizer:
    """Resolves free-text geography onto ISO codes without guessing."""

    # -- countries --------------------------------------------------------
    def country(self, value: str | None) -> CountryRecord | None:
        """Resolve a country from an alpha-2/3 code, numeric code or name."""
        if value is None:
            return None
        raw = str(value).strip()
        if not raw:
            return None

        upper = raw.upper()
        if len(upper) == 2 and upper in BY_ISO2:
            return BY_ISO2[upper]
        if len(upper) == 3:
            if upper in BY_ISO3:
                return BY_ISO3[upper]
            if upper.isdigit() and upper in BY_NUMERIC:
                return BY_NUMERIC[upper]
        if upper.isdigit():
            padded = upper.zfill(3)
            if padded in BY_NUMERIC:
                return BY_NUMERIC[padded]

        alias_key = collapse_whitespace(
            _POSTAL_CLEAN.sub(" ", strip_accents(upper))
        ).strip()
        for candidate in (alias_key, alias_key.replace(" ", "")):
            if candidate in COUNTRY_ALIASES:
                return BY_ISO2[COUNTRY_ALIASES[candidate]]

        squashed = squash(raw)
        for record in BY_ISO2.values():
            if squash(record.name) == squashed:
                return record
        # Prefix match handles "Korea, Republic of" vs "Korea Republic".
        candidates = [record for record in BY_ISO2.values() if squash(record.name).startswith(squashed)]
        if len(candidates) == 1 and len(squashed) >= 4:
            return candidates[0]
        return None

    def country_code(self, value: str | None) -> str | None:
        record = self.country(value)
        return record.iso2 if record else None

    def country_name(self, value: str | None) -> str | None:
        record = self.country(value)
        return record.name if record else None

    def default_currency(self, country: str | None) -> str | None:
        record = self.country(country)
        return record.currency or None if record else None

    # -- subdivisions -----------------------------------------------------
    def region(self, value: str | None, country: str | None = None) -> NormalizedRegion:
        """Resolve a state/province to ``(code, name)`` where the market is known."""
        if not value:
            return NormalizedRegion(None, None)
        raw = collapse_whitespace(str(value))
        iso2 = self.country_code(country)
        table = SUBDIVISIONS.get(iso2 or "", {})
        names = SUBDIVISION_NAMES.get(iso2 or "", {})

        upper = raw.upper()
        if table and upper in table:
            return NormalizedRegion(upper, table[upper])
        if names:
            key = strip_accents(upper)
            if key in names:
                code = names[key]
                return NormalizedRegion(code, table[code])
        # Unknown market: keep the value verbatim rather than inventing a code.
        return NormalizedRegion(upper if len(upper) <= 3 else None, self.title(raw))

    # -- cities and postal codes -----------------------------------------
    def city(self, value: str | None) -> str | None:
        if not value:
            return None
        return self.title(collapse_whitespace(str(value)))

    def normalized_city(self, value: str | None) -> str | None:
        key = squash(value)
        return key or None

    def postal_code(self, value: str | None, country: str | None = None) -> str | None:
        """Canonical postal code, formatted for the market when it is known."""
        if not value:
            return None
        cleaned = _POSTAL_CLEAN.sub("", str(value).upper())
        if not cleaned:
            return None
        iso2 = self.country_code(country)
        if iso2 in _POSTAL_FORMATTERS:
            pattern, template = _POSTAL_FORMATTERS[iso2]
            match = pattern.match(cleaned)
            if match:
                formatted = pattern.sub(template, cleaned)
                # US ZIP+0 groups can leave a trailing separator.
                return formatted.rstrip("-").rstrip()
        return cleaned

    def normalized_postal_code(self, value: str | None) -> str | None:
        if not value:
            return None
        cleaned = _POSTAL_CLEAN.sub("", str(value).upper())
        return cleaned or None

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def title(value: str) -> str:
        """Title-case place names while preserving short particles."""
        particles = {"of", "the", "de", "del", "la", "le", "du", "van", "der", "am", "an"}
        words = value.split()
        out: list[str] = []
        for index, word in enumerate(words):
            lowered = word.lower()
            if index > 0 and lowered in particles:
                out.append(lowered)
            elif word.isupper() and len(word) <= 3:
                out.append(word)
            else:
                out.append(word.capitalize())
        return " ".join(out)


geo_normalizer = GeoNormalizer()
