"""Turn a printed BIN table into a CSV the BIN list reader can take.

Some BIN lists arrive as a report rather than as data: a fixed-pitch table
with a header line, wrapped long rows, and no delimiter at all between the
card level and the bank. ``341261      CREDIT`` and ``370034      CREDIT
PERSONAL GREEN REVOLVE WELLS FARGO`` are the same four columns, and only one
of them makes that obvious.

Column offsets do not survive such a table. Card types are different lengths,
so the level starts in a different place on every other row, and the gap
before the bank name is six spaces here and one space there. What does
survive is that *most* rows kept a wide gap at the level/bank boundary, and
those rows are enough to recover the rest:

1.  Take the BIN and the card type off the front — they are always the first
    two tokens, whatever the gaps around them are.
2.  In what remains, a run of two or more spaces is the level/bank boundary.
    That is the only place the table ever put one, so where it survived, the
    row needs no interpretation at all.
3.  Those rows give two vocabularies: the closed set of level phrases the
    column uses, and the bank names as *this* file spells them.
4.  For the rows whose gap collapsed, grow the two vocabularies against each
    other until they stop growing. A row ending in a bank we know hands us a
    new level; a row starting with a level we know hands us a new bank.
5.  Whatever is still undecided is peeled by level vocabulary and flagged,
    rather than guessed at.

A new level is only believed when every word in it is already level
vocabulary. Without that rule "VANTAGE BANK" becomes level "VANTAGE", bank
"BANK", because BANK is a name real datasets hold — a trap worth keeping in
mind before loosening it.

Nothing is discarded. The level and bank text is also written verbatim to a
``notes`` column, which the reader carries into the source-row archive and
never ingests as a field, so every split stays checkable against what was
actually read:

    python scripts/split_printed_bin_table.py report.txt out.csv \\
        --country US --known data/bin-lists/binlist-data.csv

The output is BIN, bank, card type, card level and country. Nothing else is
read out of the source and nothing is inferred beyond the split itself.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from pathlib import Path

#: Level words seen across the card-product vocabularies these reports use.
#: Only ever consulted for the text *in front of* a name, never to name
#: anything, so a word missing from here costs a flagged row, not a wrong one.
SEED_LEVEL_WORDS: frozenset[str] = frozenset({
    "CLASSIC", "ENHANCED", "STANDARD", "PLATINUM", "WORLD", "ELITE", "PREPAID", "PERSONAL",
    "BUSINESS", "CONSUMER", "GOLD", "SIGNATURE", "GIFT", "OPTIMA", "PURCHASING", "MIXED",
    "OPEN", "PROFESSIONAL", "REVOLVE", "GREEN", "CCSG", "INFINITE", "TITANIUM", "CORPORATE",
    "COMMERCIAL", "FLEET", "PAYROLL", "PREMIUM", "SELECT", "REWARDS", "US", "LENDING",
    "CHARGE", "PRODUCT", "BLACK", "LOYALTY", "CARD", "LIMITS", "EXPANSION", "EXECUTIVE",
    "WITH", "FOR", "RELOADABLE", "GOVERNMENT", "INCENTIVE", "EMPLOYEE", "FLEX", "BENEFIT",
    "WORKPLACE", "B2B", "HSA", "NON-SUBSTANTIATED",
})

#: The BIN and the card type, then everything else.
_ROW = re.compile(r"\s*(\S+)\s+(\S+)\s*(.*)$")
_GAP = re.compile(r"  +")
_MAX_LEVEL_WORDS = 5


def normalized(value: str) -> str:
    """Fold a name to letters, digits and single spaces, for comparison only."""
    return " ".join(re.sub(r"[^A-Z0-9 ]", " ", (value or "").upper()).split())


def read_known_names(path: Path, column: str) -> set[str]:
    """Institution names from a dataset already held, as extra evidence."""
    names: set[str] = set()
    with path.open(encoding="utf-8", errors="replace", newline="") as handle:
        for row in csv.DictReader(handle):
            name = normalized(row.get(column, ""))
            if name:
                names.add(name)
    return names


def read_rows(path: Path, skip_header: int) -> list[str]:
    """One string per record, with wrapped continuation lines rejoined."""
    rows: list[str] = []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[skip_header:]
    for line in (raw.rstrip() for raw in lines):
        if not line.strip():
            continue
        if line[:8].strip().isdigit():
            rows.append(line)
        elif rows:
            # A continuation: the table wrapped a long name onto its own line.
            rows[-1] += " " + line.strip()
    return rows


def split_fields(row: str) -> list[str]:
    """``[bin, card type]`` plus the level and bank the wide gap separates."""
    match = _ROW.match(row)
    if match is None:  # pragma: no cover - read_rows only yields matching rows
        return []
    digits, card_type, tail = match.groups()
    parts = [part.strip() for part in _GAP.split(tail.strip(), maxsplit=1)]
    return [digits, card_type] + [part for part in parts if part]


class Vocabularies:
    """The level phrases and bank names recovered from a report."""

    def __init__(self, known: set[str]) -> None:
        self.known = known
        self.levels: set[str] = set()
        self.banks: set[str] = set(known)
        self.level_words: set[str] = set(SEED_LEVEL_WORDS)

    def learn_from_intact_rows(self, fields: list[list[str]]) -> int:
        """Take the rows whose gap survived at face value."""
        intact = 0
        for row in fields:
            if len(row) < 4:
                continue
            intact += 1
            level, bank = row[2], " ".join(row[3:])
            # A "level" of five words or more, or one a real dataset knows as
            # an institution, is a bank in the wrong column, not a level.
            if len(level.split()) >= _MAX_LEVEL_WORDS or normalized(level) in self.known:
                continue
            self.levels.add(level)
            self.banks.add(normalized(bank))
        self.level_words |= {word for phrase in self.levels for word in phrase.split()}
        return intact

    def grow(self, fused: list[str], rounds: int = 8) -> int:
        """Grow levels and banks against each other until they settle."""
        for round_number in range(1, rounds + 1):
            before = (len(self.levels), len(self.banks))
            for text in fused:
                self._grow_one(text.split())
            if (len(self.levels), len(self.banks)) == before:
                return round_number
        return rounds

    def _grow_one(self, words: list[str]) -> None:
        if normalized(" ".join(words)) in self.banks:
            return
        # A bank we know at the end hands us a level. Tried first: reaching
        # for a new BANK first lets "PERSONAL GREEN REVOLVE WELLS FARGO"
        # enrol "GREEN REVOLVE WELLS FARGO" as an institution, because
        # "PERSONAL" on its own is a level we already hold.
        for start in range(1, len(words)):
            if normalized(" ".join(words[start:])) not in self.banks:
                continue
            head = words[:start]
            if len(head) < _MAX_LEVEL_WORDS and all(w in self.level_words for w in head):
                self.levels.add(" ".join(head))
            return
        # Nothing we know ends this row: a level we know at the front hands
        # us the bank name instead.
        for size in range(min(len(words), _MAX_LEVEL_WORDS), 0, -1):
            if " ".join(words[:size]) in self.levels and size < len(words):
                self.banks.add(normalized(" ".join(words[size:])))
                return

    def split(self, fields: list[str]) -> tuple[str, str, str]:
        """Return ``(level, bank, why)`` for one row."""
        if len(fields) < 3:
            return "", "", "no bank named"
        if len(fields) >= 4:
            return fields[2], " ".join(fields[3:]), "the file kept its gap"

        text = fields[2]
        words = text.split()
        if normalized(text) in self.banks:
            return "", text, "a name we hold, with no level"
        for size in range(min(len(words), _MAX_LEVEL_WORDS), 0, -1):
            head, tail = " ".join(words[:size]), " ".join(words[size:])
            if head in self.levels and tail and normalized(tail) in self.banks:
                return head, tail, "a level and a name we both hold"
        for start in range(1, len(words)):
            if normalized(" ".join(words[start:])) not in self.banks:
                continue
            if all(word in self.level_words for word in words[:start]):
                return " ".join(words[:start]), " ".join(words[start:]), "a name we hold at the end"
        for size in range(min(len(words), _MAX_LEVEL_WORDS), 0, -1):
            head, tail = " ".join(words[:size]), " ".join(words[size:])
            if head in self.levels and tail:
                return head, tail, "a level we hold; the rest is the name"
        taken = 0
        while taken < len(words) and words[taken] in self.level_words:
            taken += 1
        tail = " ".join(words[taken:])
        return (" ".join(words[:taken]), tail,
                "undecided — peeled by vocabulary" if tail else "no bank named")


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path, help="the printed table to read")
    parser.add_argument("destination", type=Path, help="the CSV to write")
    parser.add_argument("--country", default="", help="ISO country code every row belongs to")
    parser.add_argument("--known", type=Path, action="append", default=[],
                        help="a CSV of institution names already held, as extra evidence")
    parser.add_argument("--known-column", default="issuer",
                        help="the column those names are in (default: issuer)")
    parser.add_argument("--skip-header", type=int, default=1,
                        help="lines to drop from the top (default: 1)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_arguments(argv)

    known: set[str] = set()
    for path in arguments.known:
        known |= read_known_names(path, arguments.known_column)
    if arguments.known:
        print(f"institution names already held: {len(known):,}")

    rows = read_rows(arguments.source, arguments.skip_header)
    fields = [split_fields(row) for row in rows]
    print(f"rows: {len(rows):,}")

    vocabularies = Vocabularies(known)
    intact = vocabularies.learn_from_intact_rows(fields)
    fused = [row[2] for row in fields if len(row) == 3]
    settled = vocabularies.grow(fused)
    print(f"{intact:,} rows kept their gap and needed no interpretation")
    print(f"{len(vocabularies.levels)} level phrases, "
          f"{len(vocabularies.banks) - len(known):,} bank names recovered "
          f"(settled after round {settled})")

    written, how = [], Counter()
    for row, raw in zip(fields, rows):
        level, bank, why = vocabularies.split(row)
        how[why] += 1
        remainder = " ".join(raw.split()[2:])
        rebuilt = " ".join(part for part in (level, bank) if part)
        if rebuilt != remainder:  # pragma: no cover - a split must lose nothing
            raise AssertionError(f"split lost text: {raw!r} -> {rebuilt!r}")
        written.append({"bin": row[0], "bank": bank, "card_type": row[1],
                        "card_level": level, "country": arguments.country,
                        "notes": remainder})

    print("\nhow each row was split:")
    for reason, count in how.most_common():
        print(f"  {reason:<40} {count:>6,}")

    named = [entry["bank"] for entry in written if entry["bank"]]
    print(f"\nbanks named {len(named):,} · distinct {len(set(named)):,}"
          f" · one word {sum(1 for name in named if len(name.split()) == 1):,}")
    print(f"every one of the {len(written):,} rows rebuilds its source line exactly")

    with arguments.destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["bin", "bank", "card_type", "card_level", "country", "notes"]
        )
        writer.writeheader()
        writer.writerows(written)
    print(f"\nwrote {arguments.destination} ({len(written):,} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
