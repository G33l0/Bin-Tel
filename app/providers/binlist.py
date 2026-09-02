"""binlist.net — an optional second opinion on a single BIN.

What this is, and what it deliberately is not.

binlist.net publishes a free lookup service at ``lookup.binlist.net``. It is
useful for checking one BIN against another source when your own list is
silent or you want a second reading. It is **not** a way to populate a
database, and this module is written so it cannot be used as one:

* The service is throttled at **five requests an hour**, which its own page
  states. A limiter here enforces that ceiling locally and *refuses* the sixth
  request rather than making it and being refused. The budget is written to
  disk, so restarting the application does not hand you a fresh five.
* Nothing it returns is written into the intelligence database. A result is
  presented as an external reading, and the only way it reaches your data is
  if you copy it into ``data/bin-list.csv`` yourself. Your list stays the
  single source of truth.
* There is no bulk mode, and there cannot be. Enriching even a hundred BINs
  would take twenty hours of continuous polling, which is not what a five-an-
  hour allowance is for.
* The service supports six-digit BINs on the free tier; eight-digit lookups
  are a paid feature. An eight-digit query is sent as-is and its answer, if
  any, is reported for what it is.

Nothing longer than eight digits is ever transmitted. The value is truncated
to a BIN before the request is built, so a full card number pasted into the
search box cannot leave the machine.

Licensing note: binlist.net publishes no terms of use, and its companion data
repository ``github.com/binlist/data`` carries no licence file. Bin-Tel
therefore treats it as :data:`LicenseStatus.REVIEW_REQUIRED` — fine to consult
interactively, never to bulk-copy or redistribute.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import httpx

from app.core.constants import USER_AGENT
from app.core.errors import NetworkError
from app.core.logging_config import get_logger

logger = get_logger(__name__)

#: The service, and the API version its documentation specifies.
BINLIST_ENDPOINT = "https://lookup.binlist.net"
BINLIST_API_VERSION = "3"

#: The published allowance: five an hour, with a burst of five.
REQUESTS_PER_HOUR = 5
WINDOW_SECONDS = 3600.0

#: A single interactive lookup should not hang the interface.
REQUEST_TIMEOUT = 10.0

#: Longest value ever put on the wire. A BIN, never a card number.
MAX_QUERY_DIGITS = 8


class LicenseStatus(StrEnum):
    """How settled a source's terms are. Unclear is not the same as free."""

    VERIFIED = "verified"
    REVIEW_REQUIRED = "review_required"
    RESTRICTED = "restricted"
    UNKNOWN = "unknown"


#: What Bin-Tel records about this provider. binlist.net states no terms of
#: use and its data repository carries no licence, so redistribution is not
#: established and the status says so rather than assuming permission.
PROVIDER_LICENSE = LicenseStatus.REVIEW_REQUIRED


class RateLimited(NetworkError):
    """The local allowance is spent, or the service returned 429."""

    def __init__(self, message: str, *, retry_after_seconds: float = 0.0) -> None:
        super().__init__(message, detail=None, retryable=True)
        self.retry_after_seconds = retry_after_seconds


@dataclass(slots=True)
class ExternalReading:
    """One external answer about a BIN. Never a database record.

    Deliberately a separate type from :class:`~app.models.schemas.BinRecord`:
    it is somebody else's reading, it is not stored, and it must not be
    mistakable for something Bin-Tel's own data supports.
    """

    query: str
    source: str = "binlist.net"
    scheme: str | None = None
    card_type: str | None = None
    brand: str | None = None
    prepaid: bool | None = None
    bank_name: str | None = None
    bank_url: str | None = None
    bank_phone: str | None = None
    bank_city: str | None = None
    country_name: str | None = None
    country_alpha2: str | None = None
    country_currency: str | None = None
    retrieved_at: float = field(default_factory=time.time)

    @property
    def has_content(self) -> bool:
        """Whether the answer said anything at all. Every field may be null."""
        return any(
            (
                self.scheme,
                self.card_type,
                self.brand,
                self.bank_name,
                self.country_alpha2,
            )
        )

    @property
    def names_an_institution(self) -> bool:
        return bool((self.bank_name or "").strip())

    def as_list_row(self) -> str:
        """The reading rendered as a row you could paste into your BIN list.

        Offered as text for *you* to paste deliberately. Bin-Tel does not write
        it anywhere: an external reading becomes your data only when you decide
        it should.
        """
        columns = ["bin", "bank"]
        values = [self.query, (self.bank_name or "").strip() or "UNKNOWN — fill this in"]
        for column, value in (
            ("network", self.scheme),
            ("card_type", self.card_type),
            ("brand", self.brand),
            ("country", self.country_alpha2),
            ("currency", self.country_currency),
            ("city", self.bank_city),
            ("website", self.bank_url),
            ("phone", self.bank_phone),
        ):
            cleaned = (value or "").strip()
            if cleaned:
                columns.append(column)
                values.append(cleaned)
        return f"{','.join(columns)}\n{','.join(values)}"

    def differences_from(self, record: Any) -> list[str]:
        """Where this reading disagrees with what Bin-Tel already holds.

        Reported, never acted on. A disagreement is a prompt to go and check,
        not a reason to overwrite a list you maintain by hand.
        """
        if record is None:
            return []
        differences: list[str] = []

        def compare(label: str, mine: str | None, theirs: str | None) -> None:
            left = (mine or "").strip().casefold()
            right = (theirs or "").strip().casefold()
            if left and right and left != right:
                differences.append(f"{label}: yours “{mine}”, binlist.net “{theirs}”")

        compare(
            "Network",
            record.network.label if getattr(record, "network", None) else None,
            self.scheme,
        )
        compare("Card type", getattr(record, "card_type", None), self.card_type)
        compare(
            "Country",
            record.country.iso2 if getattr(record, "country", None) else None,
            self.country_alpha2,
        )
        issuers = getattr(record, "current_issuers", ()) or ()
        if issuers and self.bank_name:
            mine = {item.display_name.strip().casefold() for item in issuers}
            if self.bank_name.strip().casefold() not in mine:
                names = " · ".join(item.display_name for item in issuers)
                differences.append(
                    f"Issuer: yours “{names}”, binlist.net “{self.bank_name}”"
                )
        return differences


class RequestBudget:
    """Five an hour, enforced here and remembered across restarts.

    A limiter that resets when the process does is not a limit. The timestamps
    are kept in a small JSON file so closing the application does not hand you
    a fresh allowance.
    """

    def __init__(
        self,
        state_path: Path | None = None,
        *,
        limit: int = REQUESTS_PER_HOUR,
        window_seconds: float = WINDOW_SECONDS,
    ) -> None:
        self._path = state_path
        self._limit = limit
        self._window = window_seconds
        self._times: list[float] = self._load()

    # -- state ------------------------------------------------------------
    def _load(self) -> list[float]:
        if self._path is None or not self._path.exists():
            return []
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(raw, list):
            return []
        return [float(value) for value in raw if isinstance(value, int | float)]

    def _save(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._times), encoding="utf-8")
        except OSError:  # pragma: no cover - a read-only volume must not break a lookup
            logger.debug("Could not persist the request budget", exc_info=True)

    def _prune(self, now: float) -> None:
        cutoff = now - self._window
        self._times = [value for value in self._times if value > cutoff]

    # -- the allowance ------------------------------------------------------
    def remaining(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        self._prune(now)
        return max(0, self._limit - len(self._times))

    def seconds_until_free(self, now: float | None = None) -> float:
        now = time.time() if now is None else now
        self._prune(now)
        if len(self._times) < self._limit:
            return 0.0
        return max(0.0, self._times[0] + self._window - now)

    def claim(self, now: float | None = None) -> None:
        """Take one request from the allowance, or refuse."""
        now = time.time() if now is None else now
        if self.remaining(now) <= 0:
            wait = self.seconds_until_free(now)
            raise RateLimited(
                "binlist.net allows five lookups an hour, and this hour's are used. "
                f"The next one is available in {_friendly(wait)}.",
                retry_after_seconds=wait,
            )
        self._times.append(now)
        self._times.sort()
        self._save()

    def penalise(self, seconds: float, now: float | None = None) -> None:
        """Record that the service itself refused us, and back off.

        A 429 means our accounting disagreed with theirs. Theirs is the one
        that counts, so the allowance is emptied for the window they name.
        """
        now = time.time() if now is None else now
        span = max(seconds, 0.0)
        self._times = [now - self._window + span + 1.0] * self._limit
        self._save()


class BinlistProvider:
    """Consults binlist.net for one BIN, within the published allowance."""

    name = "binlist.net"
    license_status = PROVIDER_LICENSE

    def __init__(
        self,
        *,
        endpoint: str = BINLIST_ENDPOINT,
        budget: RequestBudget | None = None,
        client_factory: Any = None,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._budget = budget or RequestBudget()
        # Injected in tests so the suite never touches the network. The
        # headers are deliberately *not* set here: they belong to the request,
        # so swapping the transport still exercises the real ones.
        self._client_factory = client_factory or (
            lambda: httpx.Client(timeout=REQUEST_TIMEOUT)
        )

    @staticmethod
    def request_headers() -> dict[str, str]:
        """What every request carries. The API version is documented as a header."""
        return {
            "User-Agent": USER_AGENT,
            "Accept-Version": BINLIST_API_VERSION,
            "Accept": "application/json",
        }

    @property
    def budget(self) -> RequestBudget:
        return self._budget

    def remaining(self) -> int:
        return self._budget.remaining()

    def status_line(self) -> str:
        remaining = self._budget.remaining()
        if remaining:
            return f"{remaining} of {REQUESTS_PER_HOUR} lookups left this hour"
        return f"No lookups left — next in {_friendly(self._budget.seconds_until_free())}"

    # -- the lookup ---------------------------------------------------------
    def lookup(self, query: str) -> ExternalReading | None:
        """Ask binlist.net about one BIN. ``None`` means it has no record.

        Raises :class:`RateLimited` when the allowance is spent and
        :class:`~app.core.errors.NetworkError` when the service cannot be
        reached. Neither is fatal: Bin-Tel's own answer never depends on this.
        """
        digits = _bin_digits(query)
        self._budget.claim()

        url = f"{self._endpoint}/{digits}"
        try:
            with self._client_factory() as client:
                response = client.get(url, headers=self.request_headers())
        except httpx.TransportError as exc:
            raise NetworkError(
                "binlist.net could not be reached.",
                detail=str(exc),
                retryable=True,
            ) from exc

        if response.status_code == 404:
            logger.info("binlist.net has no record for this BIN")
            return None
        if response.status_code == 429:
            wait = _retry_after(response)
            self._budget.penalise(wait)
            raise RateLimited(
                "binlist.net is rate-limiting this machine. It allows five lookups "
                f"an hour; the next one is available in {_friendly(wait)}.",
                retry_after_seconds=wait,
            )
        if response.status_code >= 400:
            raise NetworkError(
                f"binlist.net returned an error ({response.status_code}).",
                retryable=response.status_code >= 500,
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise NetworkError(
                "binlist.net returned something that is not JSON.", detail=str(exc)
            ) from exc
        if not isinstance(payload, dict):
            raise NetworkError("binlist.net returned an unexpected response shape.")
        return parse_reading(digits, payload)


def parse_reading(query: str, payload: dict[str, Any]) -> ExternalReading:
    """Map the documented response onto a reading, tolerating null fields.

    Every field in the response may be null — the service says so — and an
    absent field is left absent rather than defaulted into something that
    looks like an answer.
    """
    bank = payload.get("bank") if isinstance(payload.get("bank"), dict) else {}
    country = payload.get("country") if isinstance(payload.get("country"), dict) else {}

    return ExternalReading(
        query=query,
        scheme=_text(payload.get("scheme")),
        card_type=_text(payload.get("type")),
        brand=_text(payload.get("brand")),
        prepaid=payload.get("prepaid") if isinstance(payload.get("prepaid"), bool) else None,
        bank_name=_text(bank.get("name")),
        bank_url=_text(bank.get("url")),
        bank_phone=_text(bank.get("phone")),
        bank_city=_text(bank.get("city")),
        country_name=_text(country.get("name")),
        country_alpha2=_text(country.get("alpha2")),
        country_currency=_text(country.get("currency")),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bin_digits(value: str) -> str:
    """The BIN to send — at most eight digits, never a card number."""
    digits = "".join(character for character in str(value or "") if character.isdigit())
    if len(digits) < 6:
        from app.core.errors import ValidationError

        raise ValidationError(
            "A BIN is at least six digits. Enter the first 6 or 8 of the number."
        )
    # Truncation is the safety property: whatever was pasted, only a BIN
    # leaves this machine.
    return digits[:MAX_QUERY_DIGITS]


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _retry_after(response: httpx.Response) -> float:
    raw = response.headers.get("Retry-After", "")
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return WINDOW_SECONDS


def _friendly(seconds: float) -> str:
    if seconds <= 0:
        return "a moment"
    minutes = int(seconds // 60)
    if minutes < 1:
        return f"{int(seconds)} seconds"
    if minutes == 1:
        return "a minute"
    if minutes < 60:
        return f"{minutes} minutes"
    return "an hour"


__all__ = [
    "BINLIST_ENDPOINT",
    "PROVIDER_LICENSE",
    "REQUESTS_PER_HOUR",
    "BinlistProvider",
    "ExternalReading",
    "LicenseStatus",
    "RateLimited",
    "RequestBudget",
    "parse_reading",
]
