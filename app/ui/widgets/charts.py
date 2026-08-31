"""Charts drawn natively with QPainter.

No plotting dependency: each chart is a small widget that reads its colours
from the active theme, so all five themes look intentional rather than having
a light-mode chart pasted into a dark interface. They are deliberately plain —
a bar, a value, a label — because these charts exist to be read, not admired.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFontMetrics,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
)
from PyQt6.QtWidgets import QSizePolicy, QWidget

from app.services.analytics_service import Distribution, GrowthPoint, Slice
from app.ui.themes.icons import IconProvider
from app.utils.formatting import format_number

#: A categorical sequence that stays legible on every Bin-Tel theme. Hues are
#: spread far enough apart to be told apart, and lightness is held in a band
#: that reads on both the dark themes and Professional Light.
SERIES_COLORS: tuple[str, ...] = (
    "#3E8FB8",
    "#22B39A",
    "#C58A4A",
    "#7C8AC9",
    "#4FA86F",
    "#B96C7E",
    "#5FA3C4",
    "#96A24E",
    "#A87BB5",
    "#5C93A8",
    "#C4894F",
    "#6F9E8A",
)


def series_color(index: int) -> QColor:
    return QColor(SERIES_COLORS[index % len(SERIES_COLORS)])


@dataclass(frozen=True, slots=True)
class ChartMetrics:
    """Colours pulled from the active theme once per repaint."""

    text: QColor
    muted: QColor
    grid: QColor
    track: QColor
    accent: QColor
    surface: QColor

    @classmethod
    def current(cls) -> ChartMetrics:
        theme = IconProvider.instance().theme
        return cls(
            text=QColor(theme.text_primary),
            muted=QColor(theme.text_secondary),
            grid=QColor(theme.border),
            track=QColor(theme.progress_bg),
            accent=QColor(theme.primary),
            surface=QColor(theme.card_bg),
        )


class BarChart(QWidget):
    """Horizontal bars with a label, a value and a share.

    Horizontal because the categories here are names — countries, networks,
    institutions — and a name reads better beside its bar than rotated beneath
    a vertical one.
    """

    slice_clicked = pyqtSignal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        max_bars: int = 8,
        show_share: bool = True,
        row_height: int = 30,
    ) -> None:
        super().__init__(parent)
        self._distribution: Distribution | None = None
        self._max_bars = max_bars
        self._show_share = show_share
        self._row_height = row_height
        self._hover_index = -1
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(row_height * 3)

    def set_distribution(self, distribution: Distribution | None) -> None:
        self._distribution = distribution
        rows = len(self._rows())
        self.setFixedHeight(max(self._row_height, rows * self._row_height + 6))
        self.setAccessibleName(
            distribution.title if distribution else "Chart with no data"
        )
        self.update()

    def _rows(self) -> list[Slice]:
        if self._distribution is None or self._distribution.is_empty:
            return []
        return self._distribution.top(self._max_bars)

    def paintEvent(self, event: QPaintEvent | None) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        metrics = ChartMetrics.current()
        rows = self._rows()

        if not rows:
            painter.setPen(metrics.muted)
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "No data to chart yet."
            )
            painter.end()
            return

        total = self._distribution.total if self._distribution else 0
        largest = max(item.value for item in rows) or 1
        font_metrics = QFontMetrics(self.font())

        # Reserve room for the widest label and the widest value.
        label_width = min(
            int(self.width() * 0.36),
            max(font_metrics.horizontalAdvance(item.label) for item in rows) + 12,
        )
        value_texts = [format_number(item.value) for item in rows]
        value_width = max(font_metrics.horizontalAdvance(text) for text in value_texts) + 10
        share_width = 52 if self._show_share and total else 0
        bar_left = label_width + 8
        bar_right = self.width() - value_width - share_width - 4
        bar_width = max(20, bar_right - bar_left)

        for index, item in enumerate(rows):
            top = index * self._row_height
            centre = top + self._row_height / 2
            bar_height = min(14, self._row_height - 12)
            bar_top = centre - bar_height / 2

            painter.setPen(metrics.text if index == self._hover_index else metrics.muted)
            painter.drawText(
                QRectF(0, top, label_width, self._row_height),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                font_metrics.elidedText(item.label, Qt.TextElideMode.ElideRight, label_width),
            )

            track = QRectF(bar_left, bar_top, bar_width, bar_height)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(metrics.track)
            painter.drawRoundedRect(track, bar_height / 2, bar_height / 2)

            filled = max(3.0, bar_width * (item.value / largest))
            colour = series_color(index)
            if index == self._hover_index:
                colour = colour.lighter(118)
            painter.setBrush(colour)
            painter.drawRoundedRect(
                QRectF(bar_left, bar_top, filled, bar_height), bar_height / 2, bar_height / 2
            )

            painter.setPen(metrics.text)
            painter.drawText(
                QRectF(bar_right + 4, top, value_width, self._row_height),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                value_texts[index],
            )
            if share_width:
                painter.setPen(metrics.muted)
                painter.drawText(
                    QRectF(bar_right + 4 + value_width, top, share_width, self._row_height),
                    int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                    f"{item.value / total:.1%}" if total else "",
                )
        painter.end()

    def mouseMoveEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802 - Qt API
        if event is None:
            return
        index = int(event.position().y() // self._row_height)
        rows = self._rows()
        index = index if 0 <= index < len(rows) else -1
        if index != self._hover_index:
            self._hover_index = index
            if index >= 0:
                item = rows[index]
                self.setToolTip(f"{item.label}: {format_number(item.value)}")
            self.update()

    def leaveEvent(self, event: object) -> None:  # noqa: N802 - Qt API
        self._hover_index = -1
        self.update()
        super().leaveEvent(event)  # type: ignore[arg-type]

    def mouseReleaseEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802 - Qt API
        rows = self._rows()
        if event is not None and 0 <= self._hover_index < len(rows):
            key = rows[self._hover_index].key
            if key != "__other__":
                self.slice_clicked.emit(key)
        super().mouseReleaseEvent(event)


class DonutChart(QWidget):
    """A proportional ring with a centred total and an inline legend."""

    def __init__(self, parent: QWidget | None = None, *, max_slices: int = 6) -> None:
        super().__init__(parent)
        self._distribution: Distribution | None = None
        self._max_slices = max_slices
        self.setMinimumHeight(180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(190)

    def set_distribution(self, distribution: Distribution | None) -> None:
        self._distribution = distribution
        self.setAccessibleName(distribution.title if distribution else "Chart with no data")
        self.update()

    def paintEvent(self, event: QPaintEvent | None) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        metrics = ChartMetrics.current()

        if self._distribution is None or self._distribution.is_empty:
            painter.setPen(metrics.muted)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No data to chart yet.")
            painter.end()
            return

        slices = self._distribution.top(self._max_slices)
        total = sum(item.value for item in slices) or 1

        diameter = min(self.height() - 20, 150)
        ring = QRectF(14, (self.height() - diameter) / 2, diameter, diameter)
        thickness = diameter * 0.22

        start = 90 * 16
        for index, item in enumerate(slices):
            span = int(-360 * 16 * item.value / total)
            painter.setPen(QPen(series_color(index), thickness, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
            inner = ring.adjusted(thickness / 2, thickness / 2, -thickness / 2, -thickness / 2)
            painter.drawArc(inner, start, span)
            start += span

        painter.setPen(metrics.text)
        font = painter.font()
        font.setPointSizeF(max(11.0, font.pointSizeF() * 1.4))
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(ring, Qt.AlignmentFlag.AlignCenter, format_number(self._distribution.total))

        # Legend
        font = painter.font()
        font.setBold(False)
        font.setPointSizeF(max(8.0, font.pointSizeF() / 1.4))
        painter.setFont(font)
        legend_left = ring.right() + 24
        row_height = 20
        top = (self.height() - len(slices) * row_height) / 2
        font_metrics = QFontMetrics(font)
        available = self.width() - legend_left - 60
        for index, item in enumerate(slices):
            y = top + index * row_height
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(series_color(index))
            painter.drawEllipse(QRectF(legend_left, y + row_height / 2 - 4, 8, 8))
            painter.setPen(metrics.text)
            painter.drawText(
                QRectF(legend_left + 16, y, max(40, available), row_height),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                font_metrics.elidedText(item.label, Qt.TextElideMode.ElideRight, max(40, int(available))),
            )
            painter.setPen(metrics.muted)
            painter.drawText(
                QRectF(self.width() - 58, y, 52, row_height),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                f"{item.value / total:.0%}",
            )
        painter.end()


class SparkArea(QWidget):
    """A filled line showing database growth over time."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._points: list[GrowthPoint] = []
        self._cumulative = True
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(170)

    def set_points(self, points: list[GrowthPoint], *, cumulative: bool = True) -> None:
        self._points = list(points)
        self._cumulative = cumulative
        self.setAccessibleName(f"Database growth across {len(points)} period(s)")
        self.update()

    def paintEvent(self, event: QPaintEvent | None) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        metrics = ChartMetrics.current()

        if len(self._points) < 2:
            painter.setPen(metrics.muted)
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "Growth needs at least two database releases to plot."
                if self._points
                else "No growth data in this database yet.",
            )
            painter.end()
            return

        values = [
            point.cumulative if self._cumulative else point.added for point in self._points
        ]
        highest = max(values) or 1
        lowest = min(min(values), 0)
        span = max(1, highest - lowest)

        left, right = 8.0, self.width() - 8.0
        top, bottom = 12.0, self.height() - 28.0
        step = (right - left) / max(1, len(values) - 1)

        # Baseline grid
        painter.setPen(QPen(metrics.grid, 1, Qt.PenStyle.DashLine))
        for fraction in (0.0, 0.5, 1.0):
            y = bottom - (bottom - top) * fraction
            painter.drawLine(QPointF(left, y), QPointF(right, y))

        path = QPainterPath()
        area = QPainterPath()
        area.moveTo(QPointF(left, bottom))
        for index, value in enumerate(values):
            x = left + index * step
            y = bottom - (bottom - top) * ((value - lowest) / span)
            point = QPointF(x, y)
            if index == 0:
                path.moveTo(point)
            else:
                path.lineTo(point)
            area.lineTo(point)
        area.lineTo(QPointF(right, bottom))
        area.closeSubpath()

        fill = QColor(metrics.accent)
        fill.setAlpha(46)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawPath(area)

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(metrics.accent, 2))
        painter.drawPath(path)

        painter.setPen(metrics.muted)
        font = painter.font()
        font.setPointSizeF(max(7.5, font.pointSizeF() * 0.85))
        painter.setFont(font)
        painter.drawText(
            QRectF(left, bottom + 4, 120, 20),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            self._points[0].period,
        )
        painter.drawText(
            QRectF(right - 120, bottom + 4, 120, 20),
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            self._points[-1].period,
        )
        painter.setPen(metrics.text)
        painter.drawText(
            QRectF(left, top - 8, right - left, 18),
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop),
            format_number(values[-1]),
        )
        painter.end()


class HealthGauge(QWidget):
    """An arc showing the database health score."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._score = 0.0
        self._grade = ""
        self._state = "info"
        self.setFixedSize(150, 110)

    def set_score(self, score: float, grade: str, state: str = "info") -> None:
        self._score = max(0.0, min(1.0, score))
        self._grade = grade
        self._state = state
        self.setAccessibleName(f"Database health {int(self._score * 100)} percent, {grade}")
        self.update()

    def paintEvent(self, event: QPaintEvent | None) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        metrics = ChartMetrics.current()
        theme = IconProvider.instance().theme
        colour = QColor(
            {
                "success": theme.success,
                "warning": theme.warning,
                "danger": theme.danger,
            }.get(self._state, theme.info)
        )

        thickness = 12.0
        rect = QRectF(thickness, thickness + 6, self.width() - thickness * 2, self.width() - thickness * 2)
        painter.setPen(QPen(metrics.track, thickness, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(rect, 200 * 16, -220 * 16)
        painter.setPen(QPen(colour, thickness, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(rect, 200 * 16, int(-220 * 16 * self._score))

        painter.setPen(metrics.text)
        font = painter.font()
        font.setPointSizeF(max(15.0, font.pointSizeF() * 1.9))
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(
            rect.adjusted(0, -6, 0, -6), Qt.AlignmentFlag.AlignCenter, f"{int(self._score * 100)}%"
        )

        font.setPointSizeF(max(7.5, font.pointSizeF() / 2.4))
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(metrics.muted)
        painter.drawText(
            QRectF(0, self.height() - 22, self.width(), 18),
            Qt.AlignmentFlag.AlignCenter,
            self._grade,
        )
        painter.end()
