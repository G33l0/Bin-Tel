"""Writing CSV that a spreadsheet will not execute.

Excel, LibreOffice and Google Sheets treat a cell beginning ``=``, ``+``, ``-``
or ``@`` as a *formula*, not as text. So a bank name of

    =cmd|'/c calc'!A1

is a command that runs when somebody opens the exported file. The value never
has to be malicious in Bin-Tel to be dangerous in Excel, and an export is
precisely the thing people open in Excel and send to each other.

The defence is the standard one: prefix such a cell with an apostrophe, which
every spreadsheet reads as "this is text". The apostrophe is visible in the
cell, which is the accepted cost — a visibly quoted name is better than a
silently executed one.

This is applied when *writing* only. Nothing is rewritten on the way into the
database: the list is the user's own record and it stays exactly as they typed
it. The escaping belongs to the file format that has the problem.
"""

from __future__ import annotations

from collections.abc import Iterable

#: Characters a spreadsheet treats as the start of a formula.
#:
#: The tab and carriage return are here because they let a payload begin at
#: what a spreadsheet considers a new cell or line.
FORMULA_PREFIXES: tuple[str, ...] = ("=", "+", "-", "@", "\t", "\r")


def escape_cell(value: object) -> str:
    """Return *value* as text a spreadsheet will never evaluate.

    A negative number is left alone: ``-3`` is a number, not a formula, and
    quoting it would turn a figure into a string in every spreadsheet that
    opens the file.
    """
    if value is None:
        return ""
    text = str(value)
    if not text or not text.startswith(FORMULA_PREFIXES):
        return text
    if _is_number(text):
        return text
    return f"'{text}"


def escape_row(row: Iterable[object]) -> list[str]:
    """Escape every cell in one row."""
    return [escape_cell(cell) for cell in row]


def escape_rows(rows: Iterable[Iterable[object]]) -> list[list[str]]:
    """Escape every cell in every row."""
    return [escape_row(row) for row in rows]


def _is_number(text: str) -> bool:
    """Whether the text is an ordinary number, which needs no quoting."""
    try:
        float(text)
    except ValueError:
        return False
    return True


__all__ = ["FORMULA_PREFIXES", "escape_cell", "escape_row", "escape_rows"]
