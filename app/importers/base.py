"""Importer contract and shared field mapping.

Importers are *streaming*: :meth:`BaseImporter.iter_records` is a generator, so
a multi-gigabyte source file is processed a row at a time and never loaded into
memory. Writing is batched and committed periodically so a long run does not
hold one enormous transaction open.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.errors import ImportError_
from app.core.logging_config import get_logger
from app.database.engine import DatabaseManager
from app.services.ingest_service import IngestResult, IngestService, RawBinRecord

logger = get_logger(__name__)

#: Column names seen in the wild, mapped onto :class:`RawBinRecord` fields.
FIELD_ALIASES: dict[str, str] = {
    "bin": "bin", "iin": "bin", "bin_number": "bin", "binnumber": "bin",
    "bin_low": "bin", "range_low": "bin", "start": "bin", "bin_start": "bin",
    "prefix": "bin", "number": "bin",
    "bin_high": "bin_high", "range_high": "bin_high", "end": "bin_high",
    "bin_end": "bin_high",
    "iin_length": "iin_length", "bin_length": "iin_length", "length": "iin_length",
    "network": "network", "scheme": "network", "card_scheme": "network",
    "payment_network": "network", "card_network": "network", "association": "network",
    "brand": "brand", "card_brand": "brand", "product": "brand",
    "product_name": "brand", "card_product": "brand",
    "type": "card_type", "card_type": "card_type", "cardtype": "card_type",
    "funding": "funding_type", "funding_type": "funding_type", "funding_source": "funding_type",
    "prepaid": "prepaid", "is_prepaid": "prepaid",
    "commercial": "commercial", "is_commercial": "commercial", "business": "commercial",
    "bank": "issuer", "issuer": "issuer", "bank_name": "issuer",
    "issuer_name": "issuer", "institution": "issuer", "institution_name": "issuer",
    "issuing_bank": "issuer", "issuing_institution": "issuer",
    "legal_name": "issuer_legal_name", "issuer_legal_name": "issuer_legal_name",
    "bank_legal_name": "issuer_legal_name", "registered_name": "issuer_legal_name",
    "parent": "parent_institution", "parent_institution": "parent_institution",
    "parent_company": "parent_institution", "group": "parent_institution",
    "institution_type": "institution_type", "bank_type": "institution_type",
    "url": "website", "website": "website", "bank_url": "website", "homepage": "website",
    "swift": "swift_bic", "bic": "swift_bic", "swift_bic": "swift_bic",
    "country": "country", "country_name": "country", "country_code": "country",
    "alpha_2": "country", "alpha2": "country", "iso_country": "country",
    "country_iso": "country", "issuer_country": "country",
    "currency": "currency", "currency_code": "currency",
    "state": "state", "province": "state", "region": "state",
    "state_province": "state", "bank_state": "state",
    "city": "city", "town": "city", "bank_city": "city", "locality": "city",
    "zip": "postal_code", "zipcode": "postal_code", "postal": "postal_code",
    "postal_code": "postal_code", "postcode": "postal_code", "post_code": "postal_code",
    "address": "address_line1", "address_line1": "address_line1", "street": "address_line1",
    "address1": "address_line1", "bank_address": "address_line1",
    "address_line2": "address_line2", "address2": "address_line2",
    "phone": "phone", "telephone": "phone", "bank_phone": "phone",
    "status": "status", "record_status": "status", "state_of_record": "status",
    "aliases": "aliases", "alias": "aliases", "other_names": "aliases",
    "confidence": "confidence",
}

#: Nested shapes commonly used by JSON BIN APIs, flattened on the way in.
NESTED_PATHS: dict[str, tuple[str, ...]] = {
    "network": ("scheme",),
    "brand": ("brand",),
    "card_type": ("type",),
    "funding_type": ("funding",),
    "prepaid": ("prepaid",),
    "issuer": ("bank", "name"),
    "website": ("bank", "url"),
    "phone": ("bank", "phone"),
    "city": ("bank", "city"),
    "country": ("country", "alpha2"),
    "currency": ("country", "currency"),
}


@dataclass(slots=True)
class ImportOptions:
    """CLI-facing options shared by every importer."""

    source: Path
    dry_run: bool = False
    update: bool = True
    dedupe: bool = True
    batch_size: int = 1000
    limit: int | None = None
    source_code: str = "import"
    source_name: str = "File import"
    encoding: str = "utf-8"
    delimiter: str | None = None
    record_normalization: bool = False


@dataclass(slots=True)
class ImportSummary:
    """Outcome of an import run."""

    options: ImportOptions
    result: IngestResult = field(default_factory=IngestResult)
    dedupe_summary: str = ""
    elapsed_seconds: float = 0.0

    @property
    def summary(self) -> str:
        rate = (
            f" · {self.result.processed / self.elapsed_seconds:,.0f} rows/s"
            if self.elapsed_seconds > 0.05
            else ""
        )
        return f"{self.result.summary}{rate}"


ProgressCallback = Callable[[int, str], None]


class BaseImporter(ABC):
    """Streams records from a source and writes them via :class:`IngestService`."""

    #: File extensions this importer claims.
    extensions: tuple[str, ...] = ()
    #: Identifier used by the registry and the CLI.
    name: str = "base"

    def __init__(self, options: ImportOptions) -> None:
        self.options = options
        if not options.source.exists():
            raise ImportError_(
                "The file you selected could not be found.",
                detail=f"Missing source {options.source}",
            )

    # -- to implement -----------------------------------------------------
    @abstractmethod
    def iter_records(self) -> Iterator[RawBinRecord]:
        """Yield one :class:`RawBinRecord` per source row, lazily."""

    def estimated_total(self) -> int | None:
        """Row count when it is cheap to know; ``None`` otherwise."""
        return None

    # -- shared machinery -------------------------------------------------
    def run(
        self,
        manager: DatabaseManager,
        *,
        progress: ProgressCallback | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> ImportSummary:
        """Import everything, committing in batches of ``batch_size``."""
        import time

        started = time.perf_counter()
        summary = ImportSummary(options=self.options)
        options = self.options
        processed = 0

        session = manager.new_session()
        try:
            ingest = IngestService(
                session,
                source_code=options.source_code,
                source_name=options.source_name,
                dry_run=options.dry_run,
                record_normalization=options.record_normalization,
            )
            ingest.seed_reference_data()
            session.commit()

            for record in self.iter_records():
                if cancelled is not None and cancelled():
                    session.rollback()
                    raise KeyboardInterrupt("Import cancelled")
                ingest.ingest(record, summary.result)
                processed += 1
                if processed % options.batch_size == 0:
                    if options.dry_run:
                        session.rollback()
                    else:
                        session.commit()
                    if progress is not None:
                        progress(processed, f"{processed:,} records processed")
                if options.limit is not None and processed >= options.limit:
                    break

            if options.dry_run:
                session.rollback()
            else:
                session.commit()

            if options.dedupe and not options.dry_run:
                from app.services.dedupe_service import DedupeService

                report = DedupeService(session).run()
                session.commit()
                summary.dedupe_summary = report.summary
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        summary.elapsed_seconds = time.perf_counter() - started
        if progress is not None:
            progress(processed, summary.summary)
        logger.info(
            "Import finished",
            extra={
                "context": {
                    "importer": self.name,
                    "source": self.options.source.name,
                    "summary": summary.summary,
                    "dry_run": options.dry_run,
                }
            },
        )
        return summary

    # -- mapping helpers --------------------------------------------------
    @staticmethod
    def map_row(row: Mapping[str, Any]) -> dict[str, Any]:
        """Translate arbitrary source columns onto ``RawBinRecord`` fields."""
        mapped: dict[str, Any] = {}
        for raw_key, value in row.items():
            if raw_key is None:
                continue
            key = str(raw_key).strip().lower().replace(" ", "_").replace("-", "_")
            target = FIELD_ALIASES.get(key)
            if target is None or value in (None, ""):
                continue
            if target == "aliases":
                mapped[target] = _split_aliases(value)
            elif target in mapped and target == "bin":
                continue  # first BIN-like column wins
            else:
                mapped[target] = value
        return mapped

    @classmethod
    def map_nested(cls, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Flatten a nested JSON object, then apply the flat alias mapping."""
        flat: dict[str, Any] = {
            key: value
            for key, value in payload.items()
            if not isinstance(value, dict | list)
        }
        mapped = cls.map_row(flat)
        for target, path in NESTED_PATHS.items():
            if target in mapped:
                continue
            cursor: Any = payload
            for step in path:
                if not isinstance(cursor, Mapping):
                    cursor = None
                    break
                cursor = cursor.get(step)
            if cursor not in (None, "", {}, []):
                mapped[target] = cursor
        if "aliases" in payload and isinstance(payload["aliases"], list):
            mapped["aliases"] = [str(item) for item in payload["aliases"] if item]
        return mapped

    @classmethod
    def to_record(cls, mapped: Mapping[str, Any]) -> RawBinRecord | None:
        """Build a validated record, or ``None`` when there is no usable BIN."""
        if not mapped.get("bin"):
            return None
        try:
            return RawBinRecord.model_validate(dict(mapped))
        except Exception:  # noqa: BLE001 - one bad row must not stop the run
            return None


def _split_aliases(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value)
    for separator in ("|", ";", ","):
        if separator in text:
            return [part.strip() for part in text.split(separator) if part.strip()]
    return [text.strip()] if text.strip() else []
