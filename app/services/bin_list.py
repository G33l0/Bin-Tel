"""The personal BIN list — one file in the repository, read strictly.

Bin-Tel's database is not authored by hand. It is *built* from a plain list you
maintain yourself: :data:`BIN_LIST_FILENAME` in the repository's ``data``
folder, with one row per BIN. Add rows, rebuild, and the application is looking
at the new data. That is the whole workflow.

The reader is deliberately strict about the shape of the file and forgiving
about its contents:

* only two columns are required — the BIN digits and the institution;
* every other known column is optional and may be left blank;
* an unrecognised column stops the read rather than being guessed at, because
  silently ignoring a column is how a list ends up half-imported;
* a row that cannot be understood is reported with its line number and skipped,
  and the rest of the file still loads;
* a BIN may appear on **several** rows. Two banks that both use one BIN, and a
  predecessor that used it until 2024, are separate facts and all of them are
  kept. Only a row that repeats the *same* institution, relationship and period
  is treated as a correction of the earlier one.

Nothing here validates a payment card. The BIN is a prefix, not a number: input
longer than a BIN is refused rather than truncated, so a full card number
cannot be entered into the list by accident.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from app.core.errors import ImportError_
from app.core.logging_config import get_logger
from app.services.ingest_service import RawBinRecord

logger = get_logger(__name__)

#: The file the whole workflow revolves around, relative to the repository root.
BIN_LIST_FILENAME = "bin-list.csv"

#: The two columns a row cannot do without.
REQUIRED_COLUMNS = ("bin", "bank")

#: Every column the list understands, in the order the template writes them.
#:
#: Each maps onto a :class:`~app.services.ingest_service.RawBinRecord` field.
#: Adding a column here is how the format grows; anything not listed is an
#: error, not a silent no-op.
KNOWN_COLUMNS: dict[str, str] = {
    "bin": "bin",
    "bank": "issuer",
    "bin_high": "bin_high",
    "range_type": "range_type",
    "network": "network",
    "brand": "brand",
    "card_type": "card_type",
    "funding_type": "funding_type",
    "prepaid": "prepaid",
    "commercial": "commercial",
    "country": "country",
    "currency": "currency",
    "city": "city",
    "state": "state",
    "website": "website",
    "phone": "phone",
    "legal_name": "issuer_legal_name",
    "parent": "parent_institution",
    "relationship": "relationship",
    "effective_from": "effective_from",
    "effective_to": "effective_to",
    "notes": None,  # carried for the reader's benefit, never ingested
}

#: Column spellings people actually type, folded onto the canonical name.
COLUMN_ALIASES: dict[str, str] = {
    "iin": "bin",
    "bin_number": "bin",
    "binnumber": "bin",
    "prefix": "bin",
    "issuer": "bank",
    "bank_name": "bank",
    "institution": "bank",
    "issuer_name": "bank",
    "issuing_bank": "bank",
    "scheme": "network",
    "card_scheme": "network",
    "type": "card_type",
    "funding": "funding_type",
    "country_code": "country",
    "comment": "notes",
    "note": "notes",
}

#: A BIN is 6 to 8 digits. Six is the historic length, eight the current one;
#: both are first-class here, and neither is derived from the other.
_BIN_PATTERN = re.compile(r"^\d{6,8}$")

#: Anything longer than a BIN is refused outright. A 12+ digit string in this
#: file would be a card number, and Bin-Tel never accepts one.
_TOO_LONG = 9


@dataclass(slots=True)
class RowProblem:
    """One row that could not be read, and why."""

    line: int
    value: str
    reason: str

    def __str__(self) -> str:
        return f"line {self.line}: {self.reason}"


@dataclass(slots=True)
class BinListReport:
    """What one read of the list produced."""

    path: Path
    records: list[RawBinRecord] = field(default_factory=list)
    problems: list[RowProblem] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    #: Rows that restated a fact already recorded — the same BIN, institution,
    #: relationship and period. The later row wins as a correction, and the
    #: collision is reported rather than hidden.
    duplicates: int = 0
    #: BINs that more than one row describes. Not a problem: a shared BIN and a
    #: succession are both expressed this way.
    shared_bins: int = 0

    @property
    def accepted(self) -> int:
        return len(self.records)

    @property
    def rejected(self) -> int:
        return len(self.problems)

    @property
    def distinct_bins(self) -> int:
        return len({record.bin for record in self.records})

    @property
    def summary(self) -> str:
        parts = [f"{self.accepted:,} row(s)", f"{self.distinct_bins:,} BIN(s)"]
        if self.shared_bins:
            parts.append(f"{self.shared_bins:,} with more than one entry")
        if self.duplicates:
            parts.append(f"{self.duplicates:,} duplicate(s) superseded")
        if self.rejected:
            parts.append(f"{self.rejected:,} row(s) skipped")
        return " · ".join(parts)


def default_bin_list_path() -> Path:
    """``data/bin-list.csv`` beside the application or the repository."""
    from app.core.paths import bundle_root

    return bundle_root() / "data" / BIN_LIST_FILENAME


def resolve_columns(header: list[str]) -> dict[int, str]:
    """Map each column position onto a canonical name, or refuse the file.

    Refusing is the point. A renamed or unexpected column means the file is not
    the format this reader knows, and guessing at it would produce a database
    that looks fine and is wrong.
    """
    resolved: dict[int, str] = {}
    unknown: list[str] = []
    for index, raw in enumerate(header):
        name = (raw or "").strip().lstrip("﻿").lower().replace(" ", "_").replace("-", "_")
        if not name:
            continue
        name = COLUMN_ALIASES.get(name, name)
        if name not in KNOWN_COLUMNS:
            unknown.append(raw.strip())
            continue
        resolved[index] = name

    if unknown:
        raise ImportError_(
            "The BIN list has column(s) Bin-Tel does not recognise.",
            detail=(
                f"Unrecognised: {', '.join(unknown)}. "
                f"Known columns: {', '.join(KNOWN_COLUMNS)}. "
                "Rename or remove the column, or add it to KNOWN_COLUMNS if the "
                "list really should carry it."
            ),
        )

    present = set(resolved.values())
    missing = [column for column in REQUIRED_COLUMNS if column not in present]
    if missing:
        raise ImportError_(
            "The BIN list is missing a required column.",
            detail=(
                f"Missing: {', '.join(missing)}. Every row needs at least a BIN "
                "and the institution it belongs to."
            ),
        )
    return resolved


def normalise_bin(value: str) -> str:
    """Return the BIN digits, or raise with a reason a person can act on."""
    digits = re.sub(r"[\s\-]", "", value or "")
    if not digits:
        raise ValueError("the BIN is blank")
    if not digits.isdigit():
        raise ValueError(f"{value!r} is not a run of digits")
    if len(digits) >= _TOO_LONG:
        # Never echo the value back: at this length it may be a card number.
        raise ValueError(
            f"{len(digits)} digits is longer than a BIN — a BIN is 6 to 8 digits, "
            "and Bin-Tel never stores a card number"
        )
    if not _BIN_PATTERN.match(digits):
        raise ValueError(f"{digits!r} is {len(digits)} digits; a BIN is 6 to 8")
    return digits


def _row_record(values: dict[str, str], line: int) -> RawBinRecord:
    """Turn one resolved row into a record, or raise ``ValueError``."""
    digits = normalise_bin(values.get("bin", ""))
    bank = (values.get("bank") or "").strip()
    if not bank:
        raise ValueError(f"{digits} names no institution")

    fields: dict[str, object] = {"bin": digits, "issuer": bank}
    for column, target in KNOWN_COLUMNS.items():
        if target is None or column in ("bin", "bank"):
            continue
        value = (values.get(column) or "").strip()
        if value:
            fields[target] = value

    high = fields.get("bin_high")
    if isinstance(high, str):
        try:
            fields["bin_high"] = normalise_bin(high)
        except ValueError as exc:
            raise ValueError(f"the range end is unusable — {exc}") from exc

    # The list is hand-maintained and specific, so it is trusted more than a
    # bulk third-party feed, but never asserted as verified.
    fields["confidence"] = 0.9
    return RawBinRecord.model_validate(fields)


def iter_rows(path: Path, *, encoding: str = "utf-8") -> Iterator[tuple[int, list[str]]]:
    """Yield ``(line_number, cells)`` for every content row, skipping comments."""
    try:
        with path.open("r", encoding=encoding, errors="replace", newline="") as handle:
            for line, cells in enumerate(csv.reader(handle), start=1):
                if not cells:
                    continue
                first = (cells[0] or "").strip().lstrip("﻿")
                if first.startswith("#"):
                    continue
                if not any((cell or "").strip() for cell in cells):
                    continue
                yield line, cells
    except OSError as exc:
        raise ImportError_(
            "The BIN list could not be read.", detail=str(exc)
        ) from exc


def _fact_key(record: RawBinRecord) -> tuple[str, str, str, str, str]:
    """What makes two rows the same assertion rather than two assertions."""
    return (
        record.bin,
        " ".join((record.issuer or "").split()).casefold(),
        (record.relationship or "issuer").strip().casefold(),
        record.effective_from.isoformat() if record.effective_from else "",
        record.effective_to.isoformat() if record.effective_to else "",
    )


def read_bin_list(path: Path | None = None, *, encoding: str = "utf-8") -> BinListReport:
    """Read the list into records, reporting what could not be understood.

    Raises :class:`~app.core.errors.ImportError_` when the *file* is wrong — it
    is missing, empty, has no header, or carries a column the reader does not
    know. Individual bad *rows* are collected into
    :attr:`BinListReport.problems` instead, so one typo never costs you the
    whole list.
    """
    path = path or default_bin_list_path()
    if not path.exists():
        raise ImportError_(
            "There is no BIN list to build from.",
            detail=(
                f"Expected it at {path}. Create the file with a `bin,bank` header "
                "and one row per BIN."
            ),
        )

    rows = iter_rows(path, encoding=encoding)
    try:
        header_line, header = next(rows)
    except StopIteration:
        raise ImportError_(
            "The BIN list is empty.", detail=f"{path} has no header row."
        ) from None

    columns = resolve_columns(header)
    report = BinListReport(path=path, columns=sorted(set(columns.values())))
    # Keyed on the whole *fact*, not on the BIN. Two banks on one BIN, or an
    # issuer and the predecessor it replaced, are different facts that must
    # both survive; only a literal restatement is a duplicate.
    seen: dict[tuple[str, str, str, str, str], int] = {}

    for line, cells in rows:
        values = {
            name: cells[index] if index < len(cells) else ""
            for index, name in columns.items()
        }
        try:
            record = _row_record(values, line)
        except ValueError as exc:
            # The raw cell is echoed only when it is short enough to be a BIN.
            raw = (values.get("bin") or "").strip()
            report.problems.append(
                RowProblem(line=line, value=raw if len(raw) < _TOO_LONG else "", reason=str(exc))
            )
            continue

        key = _fact_key(record)
        previous = seen.get(key)
        if previous is not None:
            report.duplicates += 1
            report.records[previous] = record
            continue
        seen[key] = len(report.records)
        report.records.append(record)

    counts: dict[str, int] = {}
    for record in report.records:
        counts[record.bin] = counts.get(record.bin, 0) + 1
    report.shared_bins = sum(1 for count in counts.values() if count > 1)

    if not report.records:
        detail = (
            f"{report.rejected} row(s) after line {header_line} could not be "
            f"understood: {report.problems[0]}"
            if report.problems
            else f"Add rows to {path.name} — one BIN and institution per line."
        )
        raise ImportError_(
            "The BIN list has a header but no rows to build from.", detail=detail
        )

    logger.info(
        "BIN list read",
        extra={
            "context": {
                "accepted": report.accepted,
                "rejected": report.rejected,
                "duplicates": report.duplicates,
            }
        },
    )
    return report
