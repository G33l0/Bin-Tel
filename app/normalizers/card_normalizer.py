"""Card type, funding type, status and the prepaid/commercial indicators."""

from __future__ import annotations

from app.models.entities import CardType, FundingType, RecordStatus
from app.normalizers.text import sanitise_text, squash

_CARD_TYPE_ALIASES: dict[str, CardType] = {
    "credit": CardType.CREDIT, "credit card": CardType.CREDIT, "cr": CardType.CREDIT,
    "revolving": CardType.CREDIT, "revolving credit": CardType.CREDIT,
    "debit": CardType.DEBIT, "debit card": CardType.DEBIT, "dr": CardType.DEBIT,
    "check card": CardType.DEBIT, "cheque card": CardType.DEBIT,
    "immediate debit": CardType.DEBIT, "atm": CardType.DEBIT,
    "prepaid": CardType.PREPAID, "prepaid card": CardType.PREPAID,
    "stored value": CardType.PREPAID, "gift": CardType.PREPAID,
    "gift card": CardType.PREPAID, "reloadable": CardType.PREPAID,
    "payroll": CardType.PREPAID, "pre paid": CardType.PREPAID,
    "charge": CardType.CHARGE, "charge card": CardType.CHARGE,
    "deferred debit": CardType.DEFERRED_DEBIT, "deferred": CardType.DEFERRED_DEBIT,
}

_FUNDING_ALIASES: dict[str, FundingType] = {
    "credit": FundingType.CREDIT, "debit": FundingType.DEBIT,
    "prepaid": FundingType.PREPAID, "pre paid": FundingType.PREPAID,
    "charge": FundingType.CHARGE, "stored value": FundingType.PREPAID,
    "revolving": FundingType.CREDIT, "immediate debit": FundingType.DEBIT,
    "deferred debit": FundingType.CREDIT,
}

_STATUS_ALIASES: dict[str, RecordStatus] = {
    "active": RecordStatus.ACTIVE, "live": RecordStatus.ACTIVE,
    "in use": RecordStatus.ACTIVE, "current": RecordStatus.ACTIVE,
    "valid": RecordStatus.ACTIVE, "issued": RecordStatus.ACTIVE,
    "inactive": RecordStatus.INACTIVE, "dormant": RecordStatus.INACTIVE,
    "suspended": RecordStatus.INACTIVE, "not in use": RecordStatus.INACTIVE,
    "retired": RecordStatus.RETIRED, "withdrawn": RecordStatus.RETIRED,
    "closed": RecordStatus.RETIRED, "decommissioned": RecordStatus.RETIRED,
    "expired": RecordStatus.RETIRED,
    "reassigned": RecordStatus.REASSIGNED, "transferred": RecordStatus.REASSIGNED,
    "migrated": RecordStatus.REASSIGNED,
}

_TRUE_TOKENS = {"1", "true", "t", "yes", "y", "commercial", "business", "corporate", "prepaid"}
_FALSE_TOKENS = {"0", "false", "f", "no", "n", "consumer", "personal", "individual", "retail"}

_COMMERCIAL_HINTS = (
    "commercial", "business", "corporate", "corp card", "purchasing", "purchase",
    "fleet", "procurement", "b2b", "small business", "sme",
)


class CardNormalizer:
    """Normalizes the card attribute vocabulary into the stored enums."""

    def card_type(self, value: str | None) -> CardType:
        key = squash(value)
        if not key:
            return CardType.UNKNOWN
        if key in _CARD_TYPE_ALIASES:
            return _CARD_TYPE_ALIASES[key]
        for alias in sorted(_CARD_TYPE_ALIASES, key=len, reverse=True):
            if alias in key:
                return _CARD_TYPE_ALIASES[alias]
        return CardType.UNKNOWN

    def funding_type(self, value: str | None, card_type: CardType | None = None) -> FundingType:
        key = squash(value)
        if key:
            if key in _FUNDING_ALIASES:
                return _FUNDING_ALIASES[key]
            for alias in sorted(_FUNDING_ALIASES, key=len, reverse=True):
                if alias in key:
                    return _FUNDING_ALIASES[alias]
        # Fall back to the card type rather than guessing.
        if card_type is not None:
            mapping = {
                CardType.CREDIT: FundingType.CREDIT,
                CardType.DEBIT: FundingType.DEBIT,
                CardType.PREPAID: FundingType.PREPAID,
                CardType.CHARGE: FundingType.CHARGE,
                CardType.DEFERRED_DEBIT: FundingType.CREDIT,
            }
            return mapping.get(card_type, FundingType.UNKNOWN)
        return FundingType.UNKNOWN

    def status(self, value: str | None) -> RecordStatus:
        key = squash(value)
        if not key:
            return RecordStatus.UNKNOWN
        if key in _STATUS_ALIASES:
            return _STATUS_ALIASES[key]
        for alias in sorted(_STATUS_ALIASES, key=len, reverse=True):
            if alias in key:
                return _STATUS_ALIASES[alias]
        return RecordStatus.UNKNOWN

    def tri_state(self, value: object) -> bool | None:
        """Parse a possibly-missing boolean without ever guessing ``False``."""
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, int | float):
            return bool(value)
        key = squash(str(value))
        if not key:
            return None
        if key in _TRUE_TOKENS:
            return True
        if key in _FALSE_TOKENS:
            return False
        return None

    def is_prepaid(self, value: object, card_type: CardType | None = None) -> bool | None:
        parsed = self.tri_state(value)
        if parsed is not None:
            return parsed
        if card_type is CardType.PREPAID:
            return True
        return None

    def is_commercial(self, value: object, product_name: str | None = None) -> bool | None:
        parsed = self.tri_state(value)
        if parsed is not None:
            return parsed
        key = squash(product_name)
        if key and any(hint in key for hint in _COMMERCIAL_HINTS):
            return True
        return None

    def card_level(self, value: str | None) -> str | None:
        """Tidy a product tier without translating it.

        Sources write ``GOLD``, ``PREPAID CLASSIC``, ``PROPRIETARY ATM``. The
        words are the source's and they stay the source's — only the spacing
        and the shouting are normalized, so nothing is claimed that the row did
        not say. Mapping "World" onto "Platinum" because they sound alike is
        exactly the kind of tidying that invents facts.
        """
        text = sanitise_text(value, limit=48)
        if not text:
            return None
        words = [
            word if word.isupper() and len(word) <= 3 else word.capitalize()
            for word in text.split(" ")
        ]
        return " ".join(words)[:48]

    def currency(self, value: str | None) -> str | None:
        """ISO 4217 alpha-3, upper-cased; anything else becomes ``None``."""
        if not value:
            return None
        code = "".join(char for char in str(value).strip().upper() if char.isalpha())
        return code if len(code) == 3 else None


card_normalizer = CardNormalizer()
