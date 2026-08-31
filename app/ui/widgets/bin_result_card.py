"""The BIN lookup result surface.

Shows the BIN, its issuer and the card attributes, plus copy and export
actions. Fields with no data are omitted rather than shown blank, except for a
small always-visible core so the layout stays predictable.

This view deliberately contains no data-source, provenance or internal-notes
section. That information lives in the database for data-quality work; it is
never part of a normal lookup result.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QLabel, QPushButton, QSizePolicy, QWidget

from app.core.constants import UNKNOWN_DISPLAY
from app.models.schemas import BinLookupResult, BinRecord
from app.services.export_service import summarise_for_clipboard
from app.ui.themes.icons import IconProvider
from app.ui.widgets.cards import Card, Chip, FieldRow, KeyValueCard, SectionHeader
from app.ui.widgets.states import StateBanner, StateKind
from app.utils.formatting import format_bin, format_datetime
from app.utils.qt_helpers import copy_to_clipboard, expanding_spacer, hbox, vbox

#: Always rendered, even when unknown, so the card never looks truncated.
CORE_FIELDS = ("BIN", "IIN Length", "Network", "Card Type", "Funding Type", "Issuer", "Country", "Status")


class BinResultCard(QWidget):
    """A polished, copyable presentation of one BIN record."""

    copied = pyqtSignal(str)
    export_requested = pyqtSignal(object)  # BinRecord
    institution_selected = pyqtSignal(int)
    watch_requested = pyqtSignal(object)  # BinRecord
    favorite_toggled = pyqtSignal(object)  # BinRecord

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._record: BinRecord | None = None

        layout = vbox(self, spacing=14)

        # -- headline -----------------------------------------------------
        self.headline = Card(self, padding=20, spacing=16)
        header_row = hbox(spacing=14)

        headline_column = vbox(spacing=6)
        self.bin_label = QLabel("—", self.headline)
        self.bin_label.setProperty("role", "mono")
        self.bin_label.setStyleSheet("font-size: 26pt; font-weight: 700; letter-spacing: 3px;")
        self.bin_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.bin_label.setAccessibleName("Bank Identification Number")
        headline_column.addWidget(self.bin_label)

        self.issuer_label = QLabel(UNKNOWN_DISPLAY, self.headline)
        self.issuer_label.setProperty("role", "sectionTitle")
        self.issuer_label.setWordWrap(True)
        headline_column.addWidget(self.issuer_label)

        self.chip_row = hbox(spacing=6)
        headline_column.addLayout(self.chip_row)

        # How the answer was reached and how well it is evidenced. Kept to the
        # match's *specificity* and a confidence word — never a source name.
        self.match_label = QLabel("", self.headline)
        self.match_label.setProperty("role", "muted")
        self.match_label.setWordWrap(True)
        self.match_label.setAccessibleName("How this BIN was matched")
        self.match_label.hide()
        headline_column.addWidget(self.match_label)

        header_row.addLayout(headline_column, 1)

        actions = vbox(spacing=6)
        actions.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.copy_bin_button = self._action("Copy BIN", self._copy_bin)
        self.copy_result_button = self._action("Copy Result", self._copy_result)
        self.export_button = self._action("Export Result…", self._export, primary=True)
        self.watch_button = self._action("Add to watchlist", self._watch)
        self.favorite_button = self._action("Add to favourites", self._favorite)
        for button in (
            self.copy_bin_button,
            self.copy_result_button,
            self.export_button,
            self.watch_button,
            self.favorite_button,
        ):
            actions.addWidget(button)
        header_row.addLayout(actions)
        self.headline.body.addLayout(header_row)
        layout.addWidget(self.headline)

        # -- advisory -----------------------------------------------------
        # Shown when the data disagrees with itself, when nothing resolves, or
        # when a more specific assignment exists beneath what was searched for.
        # All three are things a reader has to know to use the answer.
        self.advisory = StateBanner("", StateKind.INFO, self)
        self.advisory.hide()
        layout.addWidget(self.advisory)

        # -- detail grid --------------------------------------------------
        self.details = KeyValueCard("Record details", self, columns=3)
        layout.addWidget(self.details)

        # -- location -----------------------------------------------------
        self.location_card = Card(self, padding=18, spacing=12)
        self.location_card.body.addWidget(SectionHeader("Issuer location", parent=self.location_card))
        self.location_value = QLabel(UNKNOWN_DISPLAY, self.location_card)
        self.location_value.setProperty("role", "fieldValue")
        self.location_value.setWordWrap(True)
        self.location_value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.location_card.body.addWidget(self.location_value)
        layout.addWidget(self.location_card)

        # -- related institutions ----------------------------------------
        self.institutions_card = Card(self, padding=18, spacing=12)
        self.institutions_header = SectionHeader(
            "Associated institutions",
            "Every recorded relationship is shown.",
            parent=self.institutions_card,
        )
        self.institutions_card.body.addWidget(self.institutions_header)
        self._institutions_layout = vbox(spacing=8)
        self.institutions_card.body.addLayout(self._institutions_layout)
        self.institutions_card.hide()
        layout.addWidget(self.institutions_card)

        self.footnote = QLabel("", self)
        self.footnote.setProperty("role", "muted")
        layout.addWidget(self.footnote)
        layout.addItem(expanding_spacer(horizontal=False))

    def _action(self, text: str, handler: object, *, primary: bool = False) -> QPushButton:
        button = QPushButton(text, self)
        button.setProperty("variant", "primary" if primary else "")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setMinimumWidth(140)
        button.setAccessibleName(text)
        button.clicked.connect(handler)  # type: ignore[arg-type]
        return button

    # -- content ----------------------------------------------------------
    @property
    def record(self) -> BinRecord | None:
        return self._record

    def show_record(self, record: BinRecord) -> None:
        self._record = record
        provider = IconProvider.instance()

        self.bin_label.setText(format_bin(record.bin))
        self.bin_label.setAccessibleName(f"BIN {record.bin}")
        self.issuer_label.setText(record.issuer_name)

        # Chips summarise the record at a glance.
        while self.chip_row.count():
            item = self.chip_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        chips: list[tuple[str, str | None]] = []
        if record.network:
            chips.append((record.network.label, record.network.accent_color or provider.theme.primary))
        if record.card_type:
            chips.append((record.card_type, None))
        if record.is_prepaid:
            chips.append(("Prepaid", provider.theme.info))
        if record.is_commercial:
            chips.append(("Commercial", provider.theme.info))
        if record.status and record.status.lower() != "active":
            chips.append((record.status, provider.theme.warning))
        elif record.status:
            chips.append((record.status, provider.theme.success))
        for text, accent in chips:
            self.chip_row.addWidget(Chip(text, self.headline, accent=accent))
        self.chip_row.addItem(expanding_spacer())

        pairs = [
            (label, value)
            for label, value in record.to_field_pairs()
            if label not in ("BIN", "Issuer", "Address")
        ]
        keep = [
            (label, value)
            for label, value in pairs
            if value != UNKNOWN_DISPLAY or label in CORE_FIELDS
        ]
        self.details.set_fields(keep, hide_unknown=False)

        block = record.address.block if record.address else UNKNOWN_DISPLAY
        self.location_value.setText(block)
        self.location_card.setVisible(block != UNKNOWN_DISPLAY)

        self._render_institutions(record)

        updated = format_datetime(record.last_updated)
        self.footnote.setText(
            f"Record last updated {updated}." if updated != UNKNOWN_DISPLAY else ""
        )

    def _render_institutions(self, record: BinRecord) -> None:
        while self._institutions_layout.count():
            item = self._institutions_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        # A single current issuer needs no list. Anything else does — a
        # historical relationship or a second claim is exactly what a reader
        # must not be left to discover for themselves.
        if len(record.institutions) < 2:
            self.institutions_card.hide()
            return

        provider = IconProvider.instance()
        current = len(record.current_institutions)
        historical = len(record.historical_institutions)
        subtitle = f"{current} current"
        if historical:
            subtitle += f", {historical} historical"
        self.institutions_header.set_subtitle(
            f"{subtitle}. Every recorded relationship is shown."
        )

        for institution in record.institutions:
            row = QWidget(self.institutions_card)
            row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            row_layout = hbox(row, spacing=10)
            field = FieldRow(
                institution.relationship_label,
                institution.display_name,
                row,
                selectable=False,
            )
            row_layout.addWidget(field, 1)

            # Standing first: a former issuer read as a current one is the
            # most misleading thing this card could show.
            standing = Chip(
                institution.standing_label,
                row,
                accent=provider.theme.success
                if institution.is_current
                else provider.theme.warning,
            )
            standing.setToolTip(
                "This relationship applies now"
                if institution.is_current
                else "This relationship has been superseded"
            )
            row_layout.addWidget(standing, 0, Qt.AlignmentFlag.AlignVCenter)

            period = institution.effective_period
            if period != UNKNOWN_DISPLAY:
                period_label = QLabel(period, row)
                period_label.setProperty("role", "muted")
                period_label.setAccessibleName(
                    f"{institution.display_name} effective period"
                )
                row_layout.addWidget(period_label, 0, Qt.AlignmentFlag.AlignVCenter)

            if institution.confidence_level:
                row_layout.addWidget(
                    Chip(
                        institution.confidence_level.capitalize(),
                        row,
                        accent=_confidence_accent(institution.confidence_level),
                    ),
                    0,
                    Qt.AlignmentFlag.AlignVCenter,
                )

            open_button = QPushButton("View institution", row)
            open_button.setProperty("variant", "link")
            open_button.setAccessibleName(f"View {institution.display_name}")
            open_button.clicked.connect(
                lambda _=False, identifier=institution.id: self.institution_selected.emit(identifier)
            )
            row_layout.addWidget(open_button, 0, Qt.AlignmentFlag.AlignVCenter)
            self._institutions_layout.addWidget(row)

        self.institutions_card.show()

    # -- lookup-level context ---------------------------------------------
    def show_lookup(self, result: BinLookupResult) -> None:
        """Present the winning record together with how it was reached."""
        record = result.best
        if record is None:
            self.clear()
            return
        self.show_record(record)

        parts: list[str] = []
        if result.match_label:
            parts.append(f"Match: {result.match_label}")
        if result.confidence_level and result.confidence_level != "unknown":
            parts.append(
                f"Confidence: {result.confidence_level.capitalize()} "
                f"({result.confidence_percent}%)"
            )
        self.match_label.setText(" · ".join(parts))
        self.match_label.setVisible(bool(parts))
        self._render_advisory(result)

    def _render_advisory(self, result: BinLookupResult) -> None:
        """Say plainly when the answer needs qualifying."""
        if result.is_conflicted:
            names = ", ".join(
                item.display_name for item in result.conflicting_institutions
            )
            self._advise(
                "Records disagree about the issuing institution"
                + (f": also named — {names}." if names else ".")
                + " Both readings are shown; neither has been discarded.",
                StateKind.WARNING,
            )
            return
        if not result.resolved:
            self._advise(
                "This prefix is in the database, but no institution "
                "relationship is recorded for it.",
                StateKind.INFO,
            )
            return
        if result.more_specific_count:
            self._advise(
                f"{result.more_specific_count} more specific assignment(s) exist "
                "beneath this prefix, and may belong to other institutions.",
                StateKind.INFO,
            )
            return
        self.advisory.hide()

    def _advise(self, message: str, kind: StateKind) -> None:
        self.advisory.set_kind(kind)
        self.advisory.message_label.setText(message)
        self.advisory.show()

    def clear(self) -> None:
        self._record = None
        self.bin_label.setText("—")
        self.issuer_label.setText(UNKNOWN_DISPLAY)
        self.match_label.setText("")
        self.match_label.hide()
        self.advisory.hide()
        self.details.set_fields([])
        self.location_card.hide()
        self.institutions_card.hide()
        self.footnote.setText("")

    # -- actions ----------------------------------------------------------
    def _copy_bin(self) -> None:
        if self._record and copy_to_clipboard(self._record.bin):
            self.copied.emit("BIN copied")

    def _copy_result(self) -> None:
        if self._record and copy_to_clipboard(summarise_for_clipboard(self._record)):
            self.copied.emit("Result copied")

    def _export(self) -> None:
        if self._record:
            self.export_requested.emit(self._record)

    def _watch(self) -> None:
        if self._record:
            self.watch_requested.emit(self._record)

    def _favorite(self) -> None:
        if self._record:
            self.favorite_toggled.emit(self._record)

    def set_watch_state(self, watched: bool) -> None:
        self.watch_button.setText("On a watchlist" if watched else "Add to watchlist")

    def set_favorite_state(self, favorite: bool) -> None:
        self.favorite_button.setText(
            "Remove from favourites" if favorite else "Add to favourites"
        )


def _confidence_accent(level: str) -> str:
    """Colour for a confidence chip, taken from the active theme."""
    theme = IconProvider.instance().theme
    return {
        "verified": theme.success,
        "high": theme.success,
        "medium": theme.info,
        "low": theme.warning,
        "conflicted": theme.warning,
        "unknown": theme.text_muted,
    }.get(level.lower(), theme.info)
