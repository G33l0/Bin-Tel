"""Qt conveniences shared across the interface.

Kept deliberately small: layout scaffolding, clipboard access and the one
genuinely platform-dependent operation the application needs — revealing a
folder in the desktop's file manager.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import QObject, Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QGuiApplication, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QFrame,
    QLabel,
    QGridLayout,
    QHBoxLayout,
    QLayout,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)

from app.core.logging_config import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Layout scaffolding
# ---------------------------------------------------------------------------


def vbox(
    parent: QWidget | None = None,
    *,
    margins: tuple[int, int, int, int] = (0, 0, 0, 0),
    spacing: int = 0,
) -> QVBoxLayout:
    layout = QVBoxLayout(parent) if parent is not None else QVBoxLayout()
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    return layout


def hbox(
    parent: QWidget | None = None,
    *,
    margins: tuple[int, int, int, int] = (0, 0, 0, 0),
    spacing: int = 0,
) -> QHBoxLayout:
    layout = QHBoxLayout(parent) if parent is not None else QHBoxLayout()
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    return layout


def grid(
    parent: QWidget | None = None,
    *,
    margins: tuple[int, int, int, int] = (0, 0, 0, 0),
    spacing: int = 12,
) -> QGridLayout:
    layout = QGridLayout(parent) if parent is not None else QGridLayout()
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    return layout


def expanding_spacer(horizontal: bool = True) -> QSpacerItem:
    if horizontal:
        return QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
    return QSpacerItem(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)


def horizontal_rule(parent: QWidget | None = None) -> QFrame:
    line = QFrame(parent)
    line.setObjectName("Divider")
    line.setFrameShape(QFrame.Shape.NoFrame)
    line.setFixedHeight(1)
    return line


def centered_paragraph(
    text: str,
    parent: QWidget | None = None,
    *,
    max_width: int = 480,
    role: str = "pageSubtitle",
) -> QWidget:
    """A word-wrapped, centred paragraph that reserves its real height.

    A word-wrapped QLabel added to a layout with an alignment flag is measured
    at its unwrapped width and gets clipped; boxing it between two stretches
    lets the layout give it the height it actually needs.
    """
    container = QWidget(parent)
    layout = hbox(container)
    layout.addStretch(1)
    label = QLabel(text, container)
    label.setObjectName("Paragraph")
    label.setProperty("role", role)
    label.setWordWrap(True)
    label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
    label.setMaximumWidth(max_width)
    label.setMinimumWidth(min(max_width, 240))
    # Weighting the label above the two spacers lets it grow to ``max_width``
    # on a wide window while still sitting centred on a narrow one.
    layout.addWidget(label, 6)
    layout.addStretch(1)
    container.label = label  # type: ignore[attr-defined]
    return container


def clear_layout(layout: QLayout) -> None:
    """Remove and delete every item in *layout*."""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
        child_layout = item.layout()
        if child_layout is not None:
            clear_layout(child_layout)


def set_property(widget: QWidget, name: str, value: object) -> None:
    """Set a QSS-visible property and repolish so the change takes effect."""
    widget.setProperty(name, value)
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)
    widget.update()


# ---------------------------------------------------------------------------
# Interaction
# ---------------------------------------------------------------------------


def copy_to_clipboard(text: str) -> bool:
    clipboard = QGuiApplication.clipboard()
    if clipboard is None:  # pragma: no cover - headless without a clipboard
        return False
    clipboard.setText(text)
    return True


def shortcut(
    parent: QWidget, sequence: str, handler: Callable[[], None], *, global_context: bool = True
) -> QShortcut:
    """Register a keyboard shortcut on *parent*."""
    action = QShortcut(QKeySequence(sequence), parent)
    if global_context:
        action.setContext(Qt.ShortcutContext.WindowShortcut)
    action.activated.connect(handler)
    return action


def debounce(owner: QObject, interval_ms: int, handler: Callable[[], None]) -> QTimer:
    """A single-shot timer that restarts on every call — search-as-you-type."""
    timer = QTimer(owner)
    timer.setSingleShot(True)
    timer.setInterval(max(0, interval_ms))
    timer.timeout.connect(handler)
    return timer


def open_path(path: Path) -> bool:
    """Open a file or folder with the desktop's default handler."""
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def open_url(url: str) -> bool:
    return QDesktopServices.openUrl(QUrl(url))


def reveal_in_file_manager(path: Path) -> bool:
    """Show *path* in the platform file manager, selecting it where supported.

    Falls back to simply opening the containing folder, which every desktop
    supports, so this never fails outright.
    """
    path = Path(path)
    folder = path if path.is_dir() else path.parent
    try:
        if sys.platform.startswith("win") and path.exists():
            subprocess.run(["explorer", "/select,", str(path)], check=False)
            return True
        if sys.platform == "darwin" and path.exists():
            subprocess.run(["open", "-R", str(path)], check=False)
            return True
    except OSError:  # pragma: no cover - unusual desktop configuration
        logger.debug("Native reveal failed; falling back to opening the folder")
    return open_path(folder)


def elide(widget: QWidget, text: str, width: int | None = None) -> str:
    """Elide *text* to fit *widget*'s width using its own font metrics."""
    metrics = widget.fontMetrics()
    return metrics.elidedText(
        text, Qt.TextElideMode.ElideRight, width or max(40, widget.width())
    )
