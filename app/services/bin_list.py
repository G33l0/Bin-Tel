"""The personal BIN list — plain files in a folder, read strictly.

Bin-Tel's database is not authored by hand. It is *built* from lists you
maintain yourself: :data:`BIN_LIST_FILENAME` alongside anything you drop into
the :data:`EXTRA_LIST_DIRNAME` folder next to it. Add rows, rebuild, and the
application is looking at the new data. That is the whole workflow.

The reader is deliberately strict about the shape of a file and forgiving
about its contents:

* only the BIN column is required — everything else may be absent or blank;
* an unrecognised column stops the read rather than being guessed at, because
  silently ignoring a column is how a list ends up half-imported;
* a row that cannot be understood is reported with its line number and skipped,
  and the rest of the file still loads;
* a BIN may appear on **several** rows. Two banks that both use one BIN, and a
  predecessor that used it until 2024, are separate facts and all of them are
  kept. Only a row that repeats the *same* institution, relationship and period
  is treated as a correction of the earlier one.

Real lists arrive in whatever shape their compiler used, so three things are
accommodated on purpose:

**Several vocabularies.** ``Issuer``, ``Emetteur`` and ``bank_name`` all name
the same column. :data:`COLUMN_ALIASES` is where that knowledge lives, and it
is a lookup table rather than fuzzy matching: a spelling nobody has taught the
reader is an error, not a guess.

**Several files in one.** A blank line followed by a fresh header starts a new
section with its own columns, so three lists can be pasted into one file
without reconciling their headers first.

**Spreadsheet damage.** A list that has been through Excel comes back with
leading zeros stripped from the BIN column and long phone numbers rendered as
``5.5E+11``. Neither is repaired silently: the mangled phone is dropped and
counted, and a short BIN is refused unless you say explicitly that the zeros
were lost, because ``42410`` and ``042410`` are different BINs and quietly
choosing one would be a fabrication.

Nothing here validates a payment card. The BIN is a prefix, not a number: input
longer than a BIN is refused rather than truncated, so a full card number
cannot be entered into a list by accident.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from app.core.errors import ImportError_
from app.core.logging_config import get_logger
from app.services.ingest_service import RawBinRecord

logger = get_logger(__name__)

#: The file the whole workflow revolves around, relative to the data folder.
BIN_LIST_FILENAME = "bin-list.csv"

#: A folder beside the main list. Every list file in it is read as well, so a
#: new dataset is added by dropping the file in rather than by merging it into
#: someone else's columns.
EXTRA_LIST_DIRNAME = "bin-lists"

#: Extensions treated as list files in that folder.
LIST_SUFFIXES: tuple[str, ...] = (".csv", ".tsv", ".txt")

#: The one column a row cannot do without.
#:
#: The institution used to be required too. It is not any more: a row that
#: gives a BIN, its scheme and its country but no bank is a real fact, and
#: recording it with the issuer unknown beats discarding it and answering
#: "not found" to a BIN the list plainly contains.
REQUIRED_COLUMNS = ("bin",)

#: Columns that carry no field of their own but feed one that does. They are
#: not "ignored" — reporting them that way would suggest a file's country was
#: thrown away when in fact it was used.
RESOLVED_COLUMNS: frozenset[str] = frozenset({"country_alpha3", "country_name"})

#: Delimiters sniffed from a file's header line, in preference order on a tie.
DELIMITERS: tuple[str, ...] = (",", "\t", ";", "|")

#: Every column the list understands, in the order the template writes them.
#:
#: Each maps onto a :class:`~app.services.ingest_service.RawBinRecord` field.
#: ``None`` means the column is recognised and deliberately not ingested.
#: Adding a column here is how the format grows; anything not listed is an
#: error, not a silent no-op.
KNOWN_COLUMNS: dict[str, str | None] = {
    "bin": "bin",
    "bank": "issuer",
    "bin_high": "bin_high",
    "range_type": "range_type",
    "network": "network",
    "brand": "brand",
    "card_type": "card_type",
    "card_level": "card_level",
    "funding_type": "funding_type",
    "prepaid": "prepaid",
    "commercial": "commercial",
    "country": "country",
    # Resolved into ``country`` rather than ingested on their own: a file
    # often carries the code, the three-letter code and the name of the same
    # country, and the most specific one present wins.
    "country_alpha3": None,
    "country_name": None,
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
    # Recognised so a file carrying them still loads, never stored. In every
    # dataset seen so far these hold the *country's* centroid repeated on
    # every row, not the bank's address, and storing a country centroid as an
    # institution's location would be a fabrication with a decimal point on it.
    "latitude": None,
    "longitude": None,
}

#: Column spellings people actually type, folded onto the canonical name.
#:
#: Three real datasets are represented here: an English one, a French one
#: (``Pays``/``Emetteur``/``Marque``/``Niveau``) and a lowercase one that
#: spells countries three ways in the same header.
COLUMN_ALIASES: dict[str, str] = {
    # -- the BIN itself
    "iin": "bin",
    "bin_number": "bin",
    "binnumber": "bin",
    "prefix": "bin",
    "bin_low": "bin",
    "range_low": "bin",
    "range_high": "bin_high",
    "bin_end": "bin_high",
    # -- the institution
    "issuer": "bank",
    "bank_name": "bank",
    "institution": "bank",
    "issuer_name": "bank",
    "issuing_bank": "bank",
    "emetteur": "bank",
    "émetteur": "bank",
    "banque": "bank",
    # -- the scheme
    "scheme": "network",
    "card_scheme": "network",
    "marque": "network",
    "reseau": "network",
    "réseau": "network",
    # -- the product
    "type": "card_type",
    "funding": "funding_type",
    "category": "card_level",
    "niveau": "card_level",
    "level": "card_level",
    "tier": "card_level",
    "product_level": "card_level",
    "card_category": "card_level",
    # -- where it is issued
    "country_code": "country",
    "iso_country": "country",
    "isocode2": "country",
    "iso_code2": "country",
    "alpha_2": "country",
    "alpha2": "country",
    "iso2": "country",
    "isocode3": "country_alpha3",
    "iso_code3": "country_alpha3",
    "alpha_3": "country_alpha3",
    "alpha3": "country_alpha3",
    "iso3": "country_alpha3",
    "countryname": "country_name",
    "country_label": "country_name",
    "pays": "country_name",
    # -- contact
    "issuerurl": "website",
    "issuer_url": "website",
    "bank_url": "website",
    "url": "website",
    "homepage": "website",
    "issuerphone": "phone",
    "issuer_phone": "phone",
    "bank_phone": "phone",
    "telephone": "phone",
    "tel": "phone",
    "bank_city": "city",
    # -- misc
    "comment": "notes",
    "note": "notes",
    "lat": "latitude",
    "lon": "longitude",
    "lng": "longitude",
}

#: A BIN is 6 to 8 digits. Six is the historic length, eight the current one;
#: both are first-class here, and neither is derived from the other.
_BIN_PATTERN = re.compile(r"^\d{6,8}$")

#: The shortest real BIN.
_MIN_BIN_LENGTH = 6

#: Anything longer than a BIN is refused outright. A 12+ digit string in this
#: file would be a card number, and Bin-Tel never accepts one.
_TOO_LONG = 9

#: What a spreadsheet leaves behind when it treats a long number as a float.
#: ``5.51732E+11`` was a phone number before Excel opened the file.
_SCIENTIFIC = re.compile(r"^[+-]?\d+(?:\.\d+)?[eE][+-]?\d+$")


@dataclass(slots=True)
class RowProblem:
    """One row that could not be read, and why."""

    line: int
    value: str
    reason: str
    source: str = ""

    def __str__(self) -> str:
        where = f"{self.source} " if self.source else ""
        return f"{where}line {self.line}: {self.reason}"


@dataclass(slots=True)
class BinListReport:
    """What one read of the list produced."""

    path: Path
    records: list[RawBinRecord] = field(default_factory=list)
    problems: list[RowProblem] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    #: Every file that contributed, in the order they were read.
    sources: list[Path] = field(default_factory=list)
    #: Columns understood but deliberately not stored, so a reader can see
    #: that a coordinate pair was noticed rather than missed.
    ignored_columns: list[str] = field(default_factory=list)
    #: Rows that restated a fact already recorded — the same BIN, institution,
    #: relationship and period. The later row wins as a correction, and the
    #: collision is reported rather than hidden.
    duplicates: int = 0
    #: BINs that more than one row describes. Not a problem: a shared BIN and a
    #: succession are both expressed this way.
    shared_bins: int = 0
    #: Rows whose BIN was shorter than six digits.
    short_bins: int = 0
    #: Short BINs left-padded with zeros because the caller asked for it.
    padded_bins: int = 0
    #: Accepted rows that name no institution.
    unnamed_issuers: int = 0
    #: Values dropped because a spreadsheet had already destroyed them.
    damaged_values: int = 0

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
        if self.unnamed_issuers:
            parts.append(f"{self.unnamed_issuers:,} without a named bank")
        if self.padded_bins:
            parts.append(f"{self.padded_bins:,} zero-padded")
        if self.damaged_values:
            parts.append(f"{self.damaged_values:,} value(s) a spreadsheet had destroyed")
        if self.duplicates:
            parts.append(f"{self.duplicates:,} duplicate(s) superseded")
        if self.rejected:
            parts.append(f"{self.rejected:,} row(s) skipped")
        return " · ".join(parts)


#: The header a freshly seeded list carries. Two columns is a complete file.
TEMPLATE_HEADER = "bin,bank\n"

#: What a new list says, so an empty one explains itself.
TEMPLATE_PREAMBLE = """\
# Bin-Tel — your BIN list.
#
# The database is built from this file. Add one line per BIN below the
# `bin,bank` line, then rebuild (Database → Rebuild from BIN list):
#
#     414720,Chase Bank
#     37828224,American Express
#
# A BIN is 6 or 8 digits. Lines starting with # are ignored.
# Never put a full card number, CVV or PIN in this file — anything longer
# than 8 digits is refused on purpose.
#
# A whole dataset is easier to keep as its own file: drop it into the
# `bin-lists` folder beside this one and it is read too, with its own
# columns. Tab-separated files are fine.
#
"""


def bundled_bin_list_path() -> Path:
    """The read-only template shipped with the application, if there is one."""
    from app.core.paths import bundle_root

    return bundle_root() / "data" / BIN_LIST_FILENAME


def seed_bin_list(path: Path) -> Path:
    """Make sure *path* exists, copying the bundled template when it does not.

    A packaged application unpacks its resources somewhere temporary, so the
    shipped file is a template rather than the working copy. This puts a
    writable one where the user can actually edit it.
    """
    if path.exists():
        return path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        template = bundled_bin_list_path()
        if template.exists() and template.resolve() != path.resolve():
            path.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            path.write_text(TEMPLATE_PREAMBLE + TEMPLATE_HEADER, encoding="utf-8")
        logger.info("Seeded a BIN list at %s", path)
    except OSError:  # pragma: no cover - a read-only volume is reported later
        logger.warning("Could not create a BIN list at %s", path, exc_info=True)
    return path


def default_bin_list_path() -> Path:
    """The working BIN list: the user's own copy, seeded if it is missing."""
    from app.core.paths import get_paths

    working = get_paths().data_dir / BIN_LIST_FILENAME
    return seed_bin_list(working)


def list_sources(path: Path) -> list[Path]:
    """The main list, then every list file in the folder beside it.

    Keeping a dataset in its own file is better than merging it into another
    one's columns: the shapes stay separate, and removing a dataset is
    deleting a file rather than editing thousands of lines out of one.
    """
    sources = [path] if path.exists() else []
    folder = path.parent / EXTRA_LIST_DIRNAME
    if folder.is_dir():
        sources.extend(
            sorted(
                item
                for item in folder.iterdir()
                if item.is_file() and item.suffix.lower() in LIST_SUFFIXES
            )
        )
    return sources


def sniff_delimiter(line: str) -> str:
    """Pick the delimiter a header line is using.

    Counting on the *header* rather than on a data row is deliberate: header
    names do not contain commas, whereas ``BDO UNIBANK, INC.`` does.
    """
    counts = {delimiter: line.count(delimiter) for delimiter in DELIMITERS}
    best = max(counts, key=lambda delimiter: (counts[delimiter], -DELIMITERS.index(delimiter)))
    return best if counts[best] else ","


def canonical_column(raw: str) -> str:
    """Fold one header cell onto its canonical name (which may be unknown)."""
    name = (raw or "").strip().lstrip("﻿").lower()
    name = name.replace(" ", "_").replace("-", "_")
    while "__" in name:
        name = name.replace("__", "_")
    return COLUMN_ALIASES.get(name, name)


def resolve_columns(header: list[str]) -> dict[int, str]:
    """Map each column position onto a canonical name, or refuse the file.

    Refusing is the point. A renamed or unexpected column means the file is not
    a shape this reader knows, and guessing at it would produce a database that
    looks fine and is wrong.

    Two positions may map to the same name — a file that carries ``alpha_2``,
    ``alpha_3`` and ``country`` is describing one country three ways. The row
    reader takes the first non-empty of them.
    """
    resolved: dict[int, str] = {}
    unknown: list[str] = []
    for index, raw in enumerate(header):
        name = canonical_column(raw)
        if not name:
            continue
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
                f"Missing: {', '.join(missing)}. Every row needs at least the BIN "
                "digits; everything else may be blank."
            ),
        )
    return resolved


#: How much of a row has to read as column names before it is judged a header
#: rather than a data row. Two independent hits, and half the row.
_HEADER_QUORUM = 2
_HEADER_SHARE = 0.5


def starts_a_new_section(cells: list[str], columns: dict[int, str]) -> bool:
    """Whether *cells* introduces a new section rather than being data.

    Two things have to be told apart, and getting either wrong is costly:

    * a mistyped header (``bin,bank,mystery_field``) must raise, naming the
      column it did not understand — treating it as data would report an empty
      file instead of a typo;
    * a row with a bad BIN (``not-a-bin,Nobody,US``) must be one skipped row,
      not the end of the file.

    So a run of digits where the BIN goes settles it as data, and otherwise a
    row is only a header when enough of it actually reads as column names.
    """
    for index, name in columns.items():
        if name == "bin":
            value = (cells[index] if index < len(cells) else "").strip()
            if value and value.replace("-", "").replace(" ", "").isdigit():
                return False
            break

    filled = [cell for cell in cells if (cell or "").strip()]
    if not filled:
        return False
    known = sum(1 for cell in filled if canonical_column(cell) in KNOWN_COLUMNS)
    return known >= _HEADER_QUORUM and known >= len(filled) * _HEADER_SHARE


def looks_damaged(value: str) -> bool:
    """Whether a spreadsheet already destroyed this value.

    ``5.51732E+11`` is what Excel leaves of a twelve-digit phone number. The
    digits it stood for are gone, so the only honest thing to do is drop it.
    """
    return bool(_SCIENTIFIC.match((value or "").strip()))


def normalise_bin(value: str, *, pad_short: bool = False) -> str:
    """Return the BIN digits, or raise with a reason a person can act on."""
    raw = (value or "").strip()
    digits = re.sub(r"[\s\-]", "", raw)
    if not digits:
        raise ValueError("the BIN is blank")
    if looks_damaged(digits):
        raise ValueError(
            f"{raw!r} is scientific notation — a spreadsheet has already lost "
            "these digits, so the BIN cannot be recovered from this file"
        )
    if not digits.isdigit():
        raise ValueError(f"{value!r} is not a run of digits")
    if len(digits) >= _TOO_LONG:
        # Never echo the value back: at this length it may be a card number.
        raise ValueError(
            f"{len(digits)} digits is longer than a BIN — a BIN is 6 to 8 digits, "
            "and Bin-Tel never stores a card number"
        )
    if len(digits) < _MIN_BIN_LENGTH:
        if not pad_short:
            raise ValueError(
                f"{digits!r} is {len(digits)} digits; a BIN is 6 to 8. A spreadsheet "
                f"strips leading zeros from a numeric column, so this may be "
                f"{digits.zfill(_MIN_BIN_LENGTH)!r} — rebuild with --pad-short-bins "
                "if you know that is what happened to this file"
            )
        digits = digits.zfill(_MIN_BIN_LENGTH)
    if not _BIN_PATTERN.match(digits):
        raise ValueError(f"{digits!r} is {len(digits)} digits; a BIN is 6 to 8")
    return digits


def _resplit(cells: list[str]) -> list[str]:
    """Re-split a row that the file's delimiter did not divide.

    Sections pasted into one file do not always agree on their separator, and
    a tab-separated block inside a comma-separated file arrives as a single
    cell. Rather than reject it, split that cell on whatever delimiter it does
    contain — the alternative is a wall of unreadable rows whose real problem
    is one character wide.
    """
    if len(cells) != 1:
        return cells
    only = cells[0]
    for delimiter in DELIMITERS:
        if delimiter in only:
            return next(csv.reader([only], delimiter=delimiter), cells)
    return cells


def _row_values(cells: list[str], columns: dict[int, str]) -> dict[str, str]:
    """Collapse a row onto canonical names, first non-empty column winning."""
    values: dict[str, str] = {}
    for index in sorted(columns):
        name = columns[index]
        cell = (cells[index] if index < len(cells) else "").strip()
        if not values.get(name):
            values[name] = cell
    return values


def _row_record(
    values: dict[str, str],
    *,
    pad_short: bool = False,
    notes: list[str] | None = None,
) -> RawBinRecord:
    """Turn one resolved row into a record, or raise ``ValueError``."""
    raw_bin = values.get("bin", "")
    digits = normalise_bin(raw_bin, pad_short=pad_short)
    if notes is not None:
        stripped = re.sub(r"[\s\-]", "", (raw_bin or "").strip())
        if stripped.isdigit() and len(stripped) < _MIN_BIN_LENGTH:
            notes.append("short")

    fields: dict[str, object] = {"bin": digits}
    bank = (values.get("bank") or "").strip()
    if bank:
        fields["issuer"] = bank
    elif notes is not None:
        notes.append("unnamed")

    for column, target in KNOWN_COLUMNS.items():
        if target is None or column in ("bin", "bank"):
            continue
        value = (values.get(column) or "").strip()
        if not value:
            continue
        if looks_damaged(value):
            # The digits are gone; storing the float would be worse than
            # storing nothing, because it reads like a real value.
            if notes is not None:
                notes.append(f"damaged:{column}")
            continue
        fields[target] = value

    # One country, spelled up to three ways. The most specific present wins;
    # the name is the last resort because names collide across sources.
    country = (
        (values.get("country") or "").strip()
        or (values.get("country_alpha3") or "").strip()
        or (values.get("country_name") or "").strip()
    )
    if country:
        fields["country"] = country

    # These sources put the scheme in a column called "brand". Reading a
    # scheme name as a scheme is not a guess, and the network normalizer
    # leaves anything it does not recognise as unknown rather than inventing.
    if not fields.get("network") and fields.get("brand"):
        fields["network"] = fields["brand"]

    # "PREPAID GOLD" and "GIFT" say the funding is prepaid as plainly as a
    # column called prepaid would. This reads the row's own words; it does not
    # infer prepaid from anything outside the row.
    level = str(fields.get("card_level") or "").casefold()
    if "prepaid" in level or "gift" in level:
        fields.setdefault("prepaid", True)

    high = fields.get("bin_high")
    if isinstance(high, str):
        try:
            fields["bin_high"] = normalise_bin(high, pad_short=pad_short)
        except ValueError as exc:
            raise ValueError(f"the range end is unusable — {exc}") from exc

    # The list is hand-maintained and specific, so it is trusted more than a
    # bulk third-party feed, but never asserted as verified.
    fields["confidence"] = 0.9
    return RawBinRecord.model_validate(fields)


def iter_rows(
    path: Path, *, encoding: str = "utf-8", delimiter: str | None = None
) -> Iterator[tuple[int, list[str]]]:
    """Yield ``(line_number, cells)`` for every content row, skipping comments."""
    if delimiter is None:
        delimiter = _sniff_file(path, encoding=encoding)
    try:
        with path.open("r", encoding=encoding, errors="replace", newline="") as handle:
            for line, cells in enumerate(csv.reader(handle, delimiter=delimiter), start=1):
                if not cells:
                    continue
                first = (cells[0] or "").strip().lstrip("﻿")
                if first.startswith("#"):
                    continue
                if not any((cell or "").strip() for cell in cells):
                    continue
                yield line, cells
    except OSError as exc:
        raise ImportError_("The BIN list could not be read.", detail=str(exc)) from exc


def _sniff_file(path: Path, *, encoding: str = "utf-8") -> str:
    """The delimiter of *path*, taken from its first content line."""
    try:
        with path.open("r", encoding=encoding, errors="replace", newline="") as handle:
            for raw in handle:
                stripped = raw.strip().lstrip("﻿")
                if not stripped or stripped.startswith("#"):
                    continue
                return sniff_delimiter(raw)
    except OSError as exc:
        raise ImportError_("The BIN list could not be read.", detail=str(exc)) from exc
    return ","


def _fact_key(record: RawBinRecord) -> tuple[str, str, str, str, str]:
    """What makes two rows the same assertion rather than two assertions."""
    return (
        record.bin,
        " ".join((record.issuer or "").split()).casefold(),
        (record.relationship or "issuer").strip().casefold(),
        record.effective_from.isoformat() if record.effective_from else "",
        record.effective_to.isoformat() if record.effective_to else "",
    )


def _read_one(
    path: Path,
    report: BinListReport,
    seen: dict[tuple[str, str, str, str, str], int],
    *,
    encoding: str,
    pad_short: bool,
) -> bool:
    """Read one file into *report*. Returns whether a header was found."""
    columns: dict[int, str] | None = None
    label = path.name
    found_header = False

    for line, raw_cells in iter_rows(path, encoding=encoding):
        cells = _resplit(raw_cells)
        if columns is None or starts_a_new_section(cells, columns):
            # The first content row must be a header, and a fresh header
            # mid-file starts a new section with its own columns. Either way
            # an unrecognised column is raised rather than swallowed: a
            # mistyped header must not be mistaken for a missing one.
            columns = resolve_columns(cells)
            found_header = True
            for name in sorted(set(columns.values())):
                used = KNOWN_COLUMNS.get(name) is not None or name in RESOLVED_COLUMNS
                target = report.columns if used else report.ignored_columns
                if name not in target:
                    target.append(name)
            continue

        values = _row_values(cells, columns)
        notes: list[str] = []
        try:
            record = _row_record(values, pad_short=pad_short, notes=notes)
        except ValueError as exc:
            raw = (values.get("bin") or "").strip()
            if raw.isdigit() and len(raw) < _MIN_BIN_LENGTH:
                report.short_bins += 1
            report.problems.append(
                RowProblem(
                    line=line,
                    value=raw if len(raw) < _TOO_LONG else "",
                    reason=str(exc),
                    source=label,
                )
            )
            continue

        for note in notes:
            if note == "short":
                report.short_bins += 1
                report.padded_bins += 1
            elif note == "unnamed":
                report.unnamed_issuers += 1
            elif note.startswith("damaged:"):
                report.damaged_values += 1

        key = _fact_key(record)
        previous = seen.get(key)
        if previous is not None:
            report.duplicates += 1
            report.records[previous] = record
            continue
        seen[key] = len(report.records)
        report.records.append(record)

    return found_header


def read_bin_list(
    path: Path | None = None,
    *,
    encoding: str = "utf-8",
    pad_short_bins: bool = False,
    sources: Iterable[Path] | None = None,
) -> BinListReport:
    """Read the list(s) into records, reporting what could not be understood.

    Raises :class:`~app.core.errors.ImportError_` when the *files* are wrong —
    none exist, none has a header, or one carries a column the reader does not
    know. Individual bad *rows* are collected into
    :attr:`BinListReport.problems` instead, so one typo never costs you the
    whole list.

    ``pad_short_bins`` left-pads a BIN shorter than six digits with zeros. It
    is off by default and should stay off unless you know the file has been
    through a spreadsheet: ``42410`` and ``042410`` are different BINs, and
    choosing between them without evidence would be a fabrication.
    """
    path = path or default_bin_list_path()
    files = list(sources) if sources is not None else list_sources(path)
    if not files:
        raise ImportError_(
            "There is no BIN list to build from.",
            detail=(
                f"Expected it at {path}. Create the file with a `bin,bank` header "
                f"and one row per BIN, or put list files in "
                f"{path.parent / EXTRA_LIST_DIRNAME}."
            ),
        )

    report = BinListReport(path=path)
    seen: dict[tuple[str, str, str, str, str], int] = {}
    headers_found = 0

    for source in files:
        report.sources.append(source)
        if _read_one(
            source, report, seen, encoding=encoding, pad_short=pad_short_bins
        ):
            headers_found += 1

    report.columns.sort()

    if not headers_found:
        raise ImportError_(
            "The BIN list has no header row.",
            detail=(
                f"{path.name} needs a line naming its columns — `bin,bank` is "
                "enough. Comment lines starting with # are ignored."
            ),
        )

    counts: dict[str, int] = {}
    for record in report.records:
        counts[record.bin] = counts.get(record.bin, 0) + 1
    report.shared_bins = sum(1 for count in counts.values() if count > 1)

    if not report.records:
        detail = (
            f"{report.rejected} row(s) could not be understood: {report.problems[0]}"
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
                "sources": len(report.sources),
                "accepted": report.accepted,
                "rejected": report.rejected,
                "duplicates": report.duplicates,
                "short_bins": report.short_bins,
                "unnamed_issuers": report.unnamed_issuers,
                "damaged_values": report.damaged_values,
            }
        },
    )
    return report
