"""Branded splash shown while the application initialises."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QSplashScreen

from app.core.constants import APP_NAME, APP_VERSION
from app.ui.themes.icons import IconProvider


class BinTelSplash(QSplashScreen):
    """Vector splash art with a status line underneath."""

    def __init__(self, width: int = 520) -> None:
        height = int(width * 300 / 520)
        pixmap = IconProvider.instance().brand_wide_pixmap("bintel-splash", width, height)
        super().__init__(pixmap)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self._message = f"{APP_NAME} {APP_VERSION}"

    def status(self, message: str) -> None:
        """Update the status line and process events so it actually paints."""
        from PyQt6.QtWidgets import QApplication

        self._message = message
        self.showMessage(
            message,
            Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
            QColor("#8FA6C4"),
        )
        application = QApplication.instance()
        if application is not None:
            application.processEvents()

    def drawContents(self, painter: QPainter | None) -> None:  # noqa: N802 - Qt API
        if painter is None:  # pragma: no cover - defensive
            return
        super().drawContents(painter)
