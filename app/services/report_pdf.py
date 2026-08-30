"""PDF rendering for Bin-Tel reports.

Produces a branded, presentation-ready document: the Bin-Tel mark and wordmark
in the header, the report's identity and provenance-free metadata, the summary
and detail blocks, then the result table with repeating headers across pages.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from app.core.constants import APP_NAME, APP_TAGLINE, APP_VERSION, COPYRIGHT, WEBSITE_URL
from app.core.logging_config import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.report_service import ReportContent

logger = get_logger(__name__)

# Bin-Tel brand palette, expressed for print.
INK = "#101A2B"
DEEP = "#2B4C9B"
TEAL = "#158F86"
MUTED = "#5B6B82"
RULE = "#D8E0EC"
BAND = "#F2F6FB"

PAGE_MARGIN = 40
#: Maximum characters rendered in a table cell before it is elided.
CELL_LIMIT = 42


def render_pdf(content: ReportContent) -> bytes:
    """Render *content* to PDF bytes."""
    import io

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        BaseDocTemplate,
        Frame,
        LongTable,
        PageTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
    )

    wide = len(content.table_columns) > 6
    page_size = landscape(A4) if wide else A4
    buffer = io.BytesIO()

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "BinTelTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=19,
        leading=23,
        alignment=TA_LEFT,
        textColor=colors.HexColor(INK),
        spaceAfter=2,
    )
    subtitle_style = ParagraphStyle(
        "BinTelSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10.5,
        leading=14,
        textColor=colors.HexColor(MUTED),
        spaceAfter=10,
    )
    heading_style = ParagraphStyle(
        "BinTelHeading",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=14,
        textColor=colors.HexColor(DEEP),
        spaceBefore=12,
        spaceAfter=5,
    )
    body_style = ParagraphStyle(
        "BinTelBody",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor(INK),
    )
    table_header_style = ParagraphStyle(
        "BinTelTableHeader",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8.5,
        leading=11,
        textColor=colors.white,
    )
    table_cell_style = ParagraphStyle(
        "BinTelTableCell",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor(INK),
    )
    note_style = ParagraphStyle(
        "BinTelNote",
        parent=body_style,
        fontSize=8.5,
        textColor=colors.HexColor(MUTED),
        spaceBefore=8,
    )

    document = BaseDocTemplate(
        buffer,
        pagesize=page_size,
        leftMargin=PAGE_MARGIN,
        rightMargin=PAGE_MARGIN,
        topMargin=PAGE_MARGIN + 46,
        bottomMargin=PAGE_MARGIN + 18,
        title=f"{APP_NAME} — {content.title}",
        author=APP_NAME,
        subject=content.report_type.label,
        creator=f"{APP_NAME} {APP_VERSION}",
    )
    frame = Frame(
        document.leftMargin,
        document.bottomMargin,
        document.width,
        document.height,
        id="body",
        leftPadding=0,
        rightPadding=0,
        topPadding=0,
        bottomPadding=0,
    )
    document.addPageTemplates(
        [
            PageTemplate(
                id="bintel",
                frames=[frame],
                onPage=lambda canvas, doc: _decorate(canvas, doc, content),
            )
        ]
    )

    story: list[Any] = [
        Paragraph(_escape(content.title), title_style),
    ]
    if content.subtitle:
        story.append(Paragraph(_escape(content.subtitle), subtitle_style))

    metadata = [
        ("Generated", content.generated_at.strftime("%d %B %Y, %H:%M UTC")),
        ("Database version", content.database_version or "Unknown"),
        ("Report type", content.report_type.label),
    ]
    story.append(_pairs_table(metadata, document.width, Table, TableStyle, colors, body_style, Paragraph))

    if content.criteria:
        story.append(Paragraph("Search criteria", heading_style))
        story.append(
            _pairs_table(content.criteria, document.width, Table, TableStyle, colors, body_style, Paragraph)
        )

    if content.summary:
        story.append(Paragraph("Summary", heading_style))
        story.append(
            _pairs_table(content.summary, document.width, Table, TableStyle, colors, body_style, Paragraph)
        )

    for section_heading, rows in content.detail_sections:
        if not rows:
            continue
        story.append(Paragraph(_escape(section_heading), heading_style))
        story.append(
            _pairs_table(rows, document.width, Table, TableStyle, colors, body_style, Paragraph)
        )

    if content.table_columns and content.table_rows:
        story.append(Paragraph("Records", heading_style))
        story.append(
            _data_table(
                content,
                document.width,
                LongTable,
                TableStyle,
                colors,
                Paragraph,
                table_cell_style,
                table_header_style,
            )
        )

    if content.notes:
        story.append(Spacer(1, 6 * mm))
        for note in content.notes:
            story.append(Paragraph(_escape(note), note_style))

    document.build(story)
    return buffer.getvalue()


def _pairs_table(
    rows: list[tuple[str, str]],
    width: float,
    Table: Any,
    TableStyle: Any,
    colors: Any,
    body_style: Any,
    Paragraph: Any,
) -> Any:
    """A two-column label/value block."""
    label_width = min(160, width * 0.32)
    data = [
        [Paragraph(f"<b>{_escape(label)}</b>", body_style), Paragraph(_escape(str(value)), body_style)]
        for label, value in rows
    ]
    table = Table(data, colWidths=[label_width, width - label_width], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("LINEBELOW", (0, 0), (-1, -2), 0.4, colors.HexColor(RULE)),
            ]
        )
    )
    return table


def _data_table(
    content: ReportContent,
    width: float,
    LongTable: Any,
    TableStyle: Any,
    colors: Any,
    Paragraph: Any,
    cell_style: Any,
    header_style: Any,
) -> Any:
    """The result table.

    A LongTable is used because a report may run to thousands of rows; it
    splits across pages efficiently and repeats the header on every one.
    Column widths are proportional to the longest value each column actually
    holds, so a BIN column does not get the same space as an address.
    """
    columns = content.table_columns
    widths = _column_widths(columns, content.table_rows, width)
    header = [Paragraph(_escape(name), header_style) for name in columns]
    body = [
        [Paragraph(_escape(_truncate(str(value))), cell_style) for value in row]
        for row in content.table_rows
    ]
    table = LongTable(
        [header, *body],
        colWidths=widths,
        repeatRows=1,
        hAlign="LEFT",
    )
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(INK)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor(RULE)),
    ]
    for index in range(1, len(body) + 1):
        if index % 2 == 0:
            style.append(("BACKGROUND", (0, index), (-1, index), colors.HexColor(BAND)))
    table.setStyle(TableStyle(style))
    return table


def _decorate(canvas: Any, document: Any, content: ReportContent) -> None:
    """Draw the branded header band and the page footer."""
    from reportlab.lib import colors

    canvas.saveState()
    width, height = document.pagesize

    # Header band
    canvas.setFillColor(colors.HexColor(INK))
    canvas.rect(0, height - 46, width, 46, stroke=0, fill=1)

    _draw_mark(canvas, PAGE_MARGIN, height - 34, 22, colors)

    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 13)
    canvas.drawString(PAGE_MARGIN + 30, height - 27, "Bin")
    mark_width = canvas.stringWidth("Bin", "Helvetica-Bold", 13)
    canvas.setFillColor(colors.HexColor("#22C8B4"))
    canvas.drawString(PAGE_MARGIN + 30 + mark_width, height - 27, "-")
    dash_width = canvas.stringWidth("-", "Helvetica-Bold", 13)
    canvas.setFillColor(colors.white)
    canvas.drawString(PAGE_MARGIN + 30 + mark_width + dash_width, height - 27, "Tel")

    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(colors.HexColor("#8FA6C4"))
    canvas.drawString(PAGE_MARGIN + 30, height - 38, APP_TAGLINE)

    canvas.setFont("Helvetica", 8)
    canvas.drawRightString(
        width - PAGE_MARGIN,
        height - 27,
        content.report_type.label,
    )
    canvas.drawRightString(
        width - PAGE_MARGIN,
        height - 38,
        f"Database {content.database_version or 'unknown'}",
    )

    # Footer
    canvas.setStrokeColor(colors.HexColor(RULE))
    canvas.setLineWidth(0.4)
    canvas.line(PAGE_MARGIN, 34, width - PAGE_MARGIN, 34)
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(colors.HexColor(MUTED))
    canvas.drawString(PAGE_MARGIN, 24, f"{COPYRIGHT}   ·   {WEBSITE_URL}")
    canvas.drawCentredString(
        width / 2, 24, content.generated_at.strftime("%d %b %Y %H:%M UTC")
    )
    canvas.drawRightString(width - PAGE_MARGIN, 24, f"Page {canvas.getPageNumber()}")
    canvas.restoreState()


def _draw_mark(canvas: Any, x: float, y: float, size: float, colors: Any) -> None:
    """Draw the Bin-Tel hexagonal badge as vector art."""
    import math

    radius = size / 2
    centre_x, centre_y = x + radius, y + radius / 2

    path = canvas.beginPath()
    for index in range(6):
        angle = math.radians(60 * index - 90)
        px = centre_x + radius * math.cos(angle)
        py = centre_y + radius * math.sin(angle)
        if index == 0:
            path.moveTo(px, py)
        else:
            path.lineTo(px, py)
    path.close()
    canvas.setFillColor(colors.HexColor(TEAL))
    canvas.drawPath(path, stroke=0, fill=1)

    # The three data rows inside the badge.
    canvas.setFillColor(colors.HexColor(INK))
    bar_x = centre_x - radius * 0.46
    for index, factor in enumerate((0.82, 1.0, 0.55)):
        canvas.roundRect(
            bar_x,
            centre_y + radius * 0.28 - index * radius * 0.34,
            radius * 0.92 * factor,
            radius * 0.17,
            radius * 0.08,
            stroke=0,
            fill=1,
        )


#: Approximate width of one character at the table's font size, in points.
_CHAR_WIDTH = 4.9
#: Horizontal cell padding (left + right) applied by the table style.
_CELL_PADDING = 12.0


def _column_widths(
    columns: list[str], rows: list[list[str]], available: float
) -> list[float]:
    """Size columns to their content, so short values do not wrap.

    Width is estimated from the longest value each column actually holds plus
    the cell padding. When the total exceeds the page, columns are scaled down
    — but never below the width their longest single word needs, which is what
    stops a six-digit BIN being split across two lines to make room for an
    address.
    """
    sample = rows[:250]
    desired: list[float] = []
    floors: list[float] = []
    for index, name in enumerate(columns):
        longest = len(name)
        longest_word = max((len(word) for word in name.split()), default=len(name))
        for row in sample:
            if index >= len(row):
                continue
            value = str(row[index])[:CELL_LIMIT]
            longest = max(longest, len(value))
            longest_word = max(longest_word, *(len(word) for word in value.split() or [""]))
        desired.append(longest * _CHAR_WIDTH + _CELL_PADDING)
        floors.append(longest_word * _CHAR_WIDTH + _CELL_PADDING)

    total = sum(desired)
    if total <= available:
        # Spread the surplus so the table fills the frame.
        surplus = (available - total) / len(desired)
        return [width + surplus for width in desired]

    # Pin the columns that cannot shrink, then share what is left.
    widths = list(desired)
    pinned = [False] * len(widths)
    for _ in range(len(widths)):
        free_indices = [i for i, is_pinned in enumerate(pinned) if not is_pinned]
        pinned_total = sum(widths[i] for i, is_pinned in enumerate(pinned) if is_pinned)
        room = available - pinned_total
        flexible_total = sum(desired[i] for i in free_indices)
        if flexible_total <= 0 or room <= 0:
            break
        scale = room / flexible_total
        newly_pinned = False
        for index in free_indices:
            scaled = desired[index] * scale
            if scaled < floors[index]:
                widths[index] = floors[index]
                pinned[index] = True
                newly_pinned = True
            else:
                widths[index] = scaled
        if not newly_pinned:
            break

    # If the floors alone overflow the page, scale everything uniformly; the
    # long values elide rather than the layout breaking.
    overflow = sum(widths)
    if overflow > available:
        widths = [width * available / overflow for width in widths]
    return widths


def _escape(value: str) -> str:
    """Escape the markup ReportLab's Paragraph interprets."""
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _truncate(value: str, limit: int = CELL_LIMIT) -> str:
    return value if len(value) <= limit else f"{value[: limit - 1]}…"


def _unused(value: datetime) -> None:  # pragma: no cover - keeps linters honest
    return None
