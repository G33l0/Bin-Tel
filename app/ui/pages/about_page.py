"""About page — identity, versions and where to get help."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QPushButton, QWidget

from app.core.constants import (
    APP_NAME,
    APP_TAGLINE,
    APP_VERSION,
    COPYRIGHT,
    DOCS_URL,
    SUPPORT_URL,
    WEBSITE_URL,
)
from app.core.paths import is_frozen, portable_mode_enabled
from app.ui.pages.base_page import BasePage
from app.ui.widgets.brand import BrandMark
from app.ui.widgets.cards import Card, FieldRow, SectionHeader
from app.utils.formatting import format_bytes
from app.utils.qt_helpers import copy_to_clipboard, grid, hbox, open_url, vbox


class AboutPage(BasePage):
    """Minimal and professional: what this is, which versions, where to go next."""

    key = "about"
    title = ""

    def __init__(self, context, parent: QWidget | None = None) -> None:
        super().__init__(context, parent)

        identity = Card(self.surface, padding=26, spacing=16)
        row = hbox(spacing=20)

        self.mark = BrandMark(76, identity)
        row.addWidget(self.mark, 0, Qt.AlignmentFlag.AlignTop)

        column = vbox(spacing=6)
        self.name_label = QLabel(identity)
        self.name_label.setTextFormat(Qt.TextFormat.RichText)
        column.addWidget(self.name_label)

        tagline = QLabel(APP_TAGLINE, identity)
        tagline.setProperty("role", "pageSubtitle")
        column.addWidget(tagline)

        self.version_label = QLabel(f"Version {APP_VERSION}", identity)
        self.version_label.setProperty("role", "muted")
        column.addWidget(self.version_label)

        links = hbox(spacing=8)
        for label, url in (
            ("Website", WEBSITE_URL),
            ("Documentation", DOCS_URL),
            ("Support", SUPPORT_URL),
        ):
            button = QPushButton(label, identity)
            button.setProperty("variant", "ghost")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setAccessibleName(f"Open {label}")
            button.clicked.connect(lambda _=False, target=url: open_url(target))
            links.addWidget(button)
        links.addStretch(1)
        column.addLayout(links)
        row.addLayout(column, 1)
        identity.body.addLayout(row)
        self.content.addWidget(identity)

        # -- versions -------------------------------------------------------
        self.details = Card(self.surface, padding=20, spacing=14)
        self.details.body.addWidget(
            SectionHeader(
                "Version information",
                parent=self.details,
                action=self._copy_button(),
            )
        )
        self._details_holder = QWidget(self.details)
        self._details_grid = grid(self._details_holder, spacing=14)
        self.details.body.addWidget(self._details_holder)
        self.content.addWidget(self.details)

        # -- scope note -----------------------------------------------------
        scope = Card(self.surface, padding=20, spacing=10)
        scope.body.addWidget(SectionHeader("Scope and data handling", parent=scope))
        note = QLabel(
            f"{APP_NAME} works with BIN/IIN and issuer metadata only. It does not store, "
            "process, transmit or display full card numbers, security codes, PINs, "
            "magnetic-stripe data, cardholder names or account credentials, and exports "
            "carry the same restriction.\n\n"
            "After the initial download the database is entirely local: lookups work "
            "offline, and the update server is contacted only when you check for an update.",
            scope,
        )
        note.setWordWrap(True)
        note.setProperty("role", "pageSubtitle")
        scope.body.addWidget(note)
        self.content.addWidget(scope)

        self.copyright_label = QLabel(COPYRIGHT, self.surface)
        self.copyright_label.setProperty("role", "muted")
        self.content.addWidget(self.copyright_label)

        self.add_stretch()
        self.refresh_identity()

    def _copy_button(self) -> QPushButton:
        button = QPushButton("Copy version info", self.surface)
        button.setProperty("variant", "ghost")
        button.setAccessibleName("Copy version information")
        button.clicked.connect(self._copy_details)
        return button

    def refresh_identity(self) -> None:
        from app.ui.themes.icons import IconProvider

        accent = IconProvider.instance().theme.primary
        self.name_label.setText(
            f'<span style="font-size:26pt;font-weight:700;letter-spacing:-0.6px;">'
            f'Bin<span style="color:{accent};">-</span>Tel</span>'
        )
        self.mark.refresh()

    # -- data -------------------------------------------------------------
    def refresh(self) -> None:
        while self._details_grid.count():
            item = self._details_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        info = self.context.stats.info() if self.context.database.is_open else None
        import platform
        import sys

        from PyQt6.QtCore import QT_VERSION_STR

        rows: list[tuple[str, str]] = [
            ("Application version", APP_VERSION),
            ("Database version", (info.version if info and info.version else "Not installed")),
            ("Database records", f"{info.stats.bins:,}" if info else "—"),
            ("Database size", format_bytes(info.size_bytes) if info else "—"),
            ("Schema version", str(info.schema_version) if info and info.schema_version else "—"),
            ("Python", platform.python_version()),
            ("Qt", QT_VERSION_STR),
            ("Platform", f"{platform.system()} {platform.release()}"),
            ("Architecture", platform.machine()),
            ("Build", "Packaged" if is_frozen() else "Source"),
            ("Mode", "Portable" if portable_mode_enabled() else "Installed"),
            ("Data folder", str(self.context.paths.data_dir)),
        ]
        _ = sys  # platform module already covers what is displayed

        for index, (label, value) in enumerate(rows):
            self._details_grid.addWidget(
                FieldRow(label, value, self._details_holder), index // 3, index % 3
            )
        for column in range(3):
            self._details_grid.setColumnStretch(column, 1)
        self._details_rows = rows

    def _copy_details(self) -> None:
        rows = getattr(self, "_details_rows", [])
        text = "\n".join(f"{label}: {value}" for label, value in rows)
        if copy_to_clipboard(f"{APP_NAME}\n{text}"):
            self.toast("Version information copied")

    def on_theme_changed(self) -> None:
        self.refresh_identity()
