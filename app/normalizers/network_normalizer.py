"""Card-network / scheme normalization.

Providers spell the same scheme a dozen ways (``VISA``, ``Visa Inc.``,
``visa credit``). This maps them onto a stable internal code so filters and
statistics group correctly.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.normalizers.text import squash


@dataclass(frozen=True, slots=True)
class NetworkDefinition:
    code: str
    name: str
    display_name: str
    scheme_type: str
    is_global: bool
    accent_color: str


#: Canonical scheme catalogue. ``accent_color`` is Bin-Tel's own palette entry
#: for the network chip in the result card — not a scheme's brand colour.
NETWORKS: tuple[NetworkDefinition, ...] = (
    NetworkDefinition("visa", "Visa", "Visa", "global", True, "#3B6FD4"),
    NetworkDefinition("mastercard", "Mastercard", "Mastercard", "global", True, "#D9743B"),
    NetworkDefinition("amex", "American Express", "American Express", "global", True, "#2F8FA8"),
    NetworkDefinition("discover", "Discover", "Discover", "global", True, "#C7833B"),
    NetworkDefinition("jcb", "JCB", "JCB", "global", True, "#4A8A63"),
    NetworkDefinition("unionpay", "UnionPay", "UnionPay", "global", True, "#B75C63"),
    NetworkDefinition("diners", "Diners Club", "Diners Club", "global", True, "#6C7BB5"),
    NetworkDefinition("maestro", "Maestro", "Maestro", "global", True, "#5E86C4"),
    NetworkDefinition("rupay", "RuPay", "RuPay", "domestic", False, "#7E9A4F"),
    NetworkDefinition("mir", "Mir", "Mir", "domestic", False, "#5A8C8C"),
    NetworkDefinition("elo", "Elo", "Elo", "domestic", False, "#A76B9B"),
    NetworkDefinition("hipercard", "Hipercard", "Hipercard", "domestic", False, "#B25C5C"),
    NetworkDefinition("interac", "Interac", "Interac", "domestic", False, "#C08A4A"),
    NetworkDefinition("bancontact", "Bancontact", "Bancontact", "domestic", False, "#4E7FA6"),
    NetworkDefinition("cartes_bancaires", "Cartes Bancaires", "Cartes Bancaires", "domestic", False, "#6E8FB8"),
    NetworkDefinition("dankort", "Dankort", "Dankort", "domestic", False, "#A45C6B"),
    NetworkDefinition("troy", "Troy", "Troy", "domestic", False, "#8A7CB8"),
    NetworkDefinition("verve", "Verve", "Verve", "domestic", False, "#5F9E7A"),
    NetworkDefinition("bc_card", "BC Card", "BC Card", "domestic", False, "#8C7A5E"),
    NetworkDefinition("napas", "NAPAS", "NAPAS", "domestic", False, "#5C8FA0"),
    NetworkDefinition("uatp", "UATP", "UATP", "specialised", False, "#7A7A8C"),
    NetworkDefinition("private_label", "Private Label", "Private Label", "specialised", False, "#7A7A8C"),
    NetworkDefinition("unknown", "Unknown", "Unknown", "unknown", False, "#8A8A8A"),
)

BY_CODE: dict[str, NetworkDefinition] = {network.code: network for network in NETWORKS}

#: Aliases in squashed form → canonical code.
_ALIASES: dict[str, str] = {
    "visa": "visa", "visa inc": "visa", "visa international": "visa",
    "visa credit": "visa", "visa debit": "visa", "visa electron": "visa",
    "vis": "visa", "v": "visa", "visa card": "visa",
    "mastercard": "mastercard", "master card": "mastercard", "mc": "mastercard",
    "mastercard worldwide": "mastercard", "mastercard international": "mastercard",
    "eurocard": "mastercard", "master": "mastercard", "mcw": "mastercard",
    "amex": "amex", "american express": "amex", "americanexpress": "amex",
    "ax": "amex", "amex card": "amex",
    "discover": "discover", "discover card": "discover", "disc": "discover",
    "discover financial services": "discover", "novus": "discover",
    "jcb": "jcb", "japan credit bureau": "jcb",
    "unionpay": "unionpay", "union pay": "unionpay", "china unionpay": "unionpay",
    "cup": "unionpay", "upi": "unionpay",
    "diners": "diners", "diners club": "diners", "diners club international": "diners",
    "dinersclub": "diners", "dci": "diners",
    "maestro": "maestro", "switch": "maestro", "maestro international": "maestro",
    "rupay": "rupay", "ru pay": "rupay",
    "mir": "mir", "nspk mir": "mir",
    "elo": "elo", "hipercard": "hipercard",
    "interac": "interac",
    "bancontact": "bancontact", "bancontact mistercash": "bancontact", "mister cash": "bancontact",
    "cartes bancaires": "cartes_bancaires", "cb": "cartes_bancaires", "carte bancaire": "cartes_bancaires",
    "dankort": "dankort",
    "troy": "troy",
    "verve": "verve",
    "bc card": "bc_card", "bccard": "bc_card",
    "napas": "napas",
    "uatp": "uatp", "universal air travel plan": "uatp",
    "private label": "private_label", "privatelabel": "private_label",
    "store card": "private_label", "own brand": "private_label",
}


class NetworkNormalizer:
    """Maps a free-text scheme name onto a canonical :class:`NetworkDefinition`."""

    def normalize(self, value: str | None) -> NetworkDefinition:
        key = squash(value)
        if not key:
            return BY_CODE["unknown"]
        if key in _ALIASES:
            return BY_CODE[_ALIASES[key]]
        # Longest-alias containment, so "visa gold credit card" resolves.
        for alias in sorted(_ALIASES, key=len, reverse=True):
            if len(alias) >= 3 and alias in key:
                return BY_CODE[_ALIASES[alias]]
        return BY_CODE["unknown"]

    def code(self, value: str | None) -> str:
        return self.normalize(value).code

    def display(self, value: str | None) -> str:
        return self.normalize(value).display_name

    def is_known(self, value: str | None) -> bool:
        return self.normalize(value).code != "unknown"


network_normalizer = NetworkNormalizer()
