"""A small, touch-friendly curve chart: one pane per series, shared time axis."""
from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from .charger import format_elapsed


@dataclass
class Series:
    key: str
    title: str
    unit: str
    digits: int
    color: str


SERIES = [
    Series("voltage", "Voltage", "V", 3, "#1e88e5"),
    Series("current", "Current", "A", 2, "#fb8c00"),
    Series("capacity", "Capacity", "mAh", 0, "#43a047"),
    Series("temperature", "Temperature", "°C", 1, "#e53935"),
]


def nice_ticks(lo: float, hi: float, count: int = 4) -> list[float]:
    if hi <= lo:
        return [lo]
    raw = (hi - lo) / count
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    first = math.ceil(lo / step) * step
    ticks = []
    v = first
    while v <= hi + step * 1e-6:
        ticks.append(round(v, 10))
        v += step
    return ticks


def value_at(points: list[tuple[float, float]], t: float) -> float | None:
    """Sample-and-hold value at time t."""
    value = None
    for pt, pv in points:
        if pt > t:
            break
        value = pv
    return value


class CurveChart(QWidget):
    LEFT, RIGHT, TOP, GAP, BOTTOM = 92, 24, 38, 24, 34

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(360)
        self.data: dict[str, list[tuple[float, float]]] = {}
        self.start = 0.0
        self.end = 1.0
        self.cursor: float | None = None
        self.empty_text = "No data"

    # ----- data -----

    def set_data(self, data: dict[str, list[tuple[float, float]]], start: float,
                 end: float | None) -> None:
        self.data = {
            k: [(t, abs(v) if k == "current" else v) for t, v in pts]
            for k, pts in data.items()
        }
        last = max((pts[-1][0] for pts in self.data.values() if pts), default=start)
        self.start = start
        self.end = max(end or last, start + 60)
        if self.cursor is not None and not self.start <= self.cursor <= self.end:
            self.cursor = None
        self.update()

    def append(self, key: str, t: float, value: float) -> None:
        if key == "current":
            value = abs(value)
        pts = self.data.setdefault(key, [])
        if pts and pts[-1][0] >= t:
            return
        pts.append((t, value))
        self.end = max(self.end, t)
        self.update()

    def visible_series(self) -> list[Series]:
        return [s for s in SERIES if len(self.data.get(s.key, [])) >= 1]

    # ----- geometry -----

    def _x(self, t: float, plot_w: float) -> float:
        return self.LEFT + (t - self.start) / (self.end - self.start) * plot_w

    def _t(self, x: float) -> float:
        plot_w = max(1.0, self.width() - self.LEFT - self.RIGHT)
        frac = min(1.0, max(0.0, (x - self.LEFT) / plot_w))
        return self.start + frac * (self.end - self.start)

    # ----- input (mouse events also come from touch) -----

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.cursor = self._t(event.position().x())
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        self.cursor = self._t(event.position().x())
        self.update()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.cursor = None
        self.update()

    # ----- drawing -----

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.palette()
        text = pal.color(pal.ColorRole.WindowText)
        muted = pal.color(pal.ColorRole.PlaceholderText)
        grid = QColor(muted)
        grid.setAlpha(60)
        series = self.visible_series()
        if not series:
            p.setPen(muted)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.empty_text)
            return
        small = QFont(self.font())
        small.setPointSizeF(max(8.0, self.font().pointSizeF() * 0.72))
        bold = QFont(self.font())
        bold.setPointSizeF(max(9.0, self.font().pointSizeF() * 0.85))
        bold.setBold(True)

        plot_w = max(1.0, self.width() - self.LEFT - self.RIGHT)
        n = len(series)
        pane_h = (self.height() - self.BOTTOM - n * (self.TOP + self.GAP)) / n
        y0 = 0.0
        for s in series:
            pts = self.data[s.key]
            top = y0 + self.TOP
            rect = QRectF(self.LEFT, top, plot_w, max(10.0, pane_h))
            vals = [v for _, v in pts]
            lo, hi = min(vals), max(vals)
            pad = (hi - lo) * 0.08 or max(abs(hi) * 0.05, 10 ** -s.digits * 5, 0.01)
            lo, hi = lo - pad, hi + pad
            if s.key in ("capacity", "current") and min(vals) >= 0:
                lo = max(lo, 0.0)

            def ymap(v: float) -> float:
                return rect.bottom() - (v - lo) / (hi - lo) * rect.height()

            # Title and the value under the cursor (or the latest value).
            shown_t = self.cursor if self.cursor is not None else pts[-1][0]
            shown = value_at(pts, shown_t)
            p.setFont(bold)
            p.setPen(QColor(s.color))
            p.drawText(QPointF(self.LEFT, y0 + self.TOP - 10), s.title)
            if shown is not None:
                p.setPen(text)
                p.drawText(QRectF(self.LEFT, y0, plot_w, self.TOP - 4),
                           Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom,
                           f"{shown:.{s.digits}f} {s.unit}")
            # Grid and y labels.
            p.setFont(small)
            for tick in nice_ticks(lo, hi):
                y = ymap(tick)
                p.setPen(QPen(grid, 1))
                p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
                p.setPen(muted)
                p.drawText(QRectF(0, y - 10, self.LEFT - 10, 20),
                           Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                           f"{tick:.{min(s.digits, 2)}f}")
            # Line (sample-and-hold steps).
            path = QPainterPath()
            for i, (t, v) in enumerate(pts):
                x, y = self._x(t, plot_w), ymap(v)
                if i == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, path.currentPosition().y())
                    path.lineTo(x, y)
            last_x = self._x(self.end, plot_w)
            path.lineTo(last_x, path.currentPosition().y())
            p.setPen(QPen(QColor(s.color), 2.2))
            p.drawPath(path)
            y0 = rect.bottom() + self.GAP

        # Time axis.
        p.setFont(small)
        p.setPen(muted)
        span = self.end - self.start
        step = next((m for m in (60, 300, 600, 900, 1800, 3600, 7200, 10800, 21600)
                     if span / m <= 8), 43200)
        t = 0.0
        while t <= span:
            x = self.LEFT + t / span * plot_w
            p.setPen(QPen(grid, 1))
            p.drawLine(QPointF(x, self.TOP), QPointF(x, y0 - self.GAP))
            p.setPen(muted)
            p.drawText(QRectF(x - 50, y0 - self.GAP + 6, 100, 22),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                       format_elapsed(t)[:-3] if span >= 600 else format_elapsed(t))
            t += step
        # Cursor.
        if self.cursor is not None:
            x = self._x(self.cursor, plot_w)
            p.setPen(QPen(text, 1.2, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(x, self.TOP - 4), QPointF(x, y0 - self.GAP))
            p.setPen(text)
            p.setFont(bold)
            label = format_elapsed(self.cursor - self.start)
            p.drawText(QRectF(min(max(x - 60, 0), self.width() - 120), y0 - self.GAP + 4, 120, 24),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, label)
        p.end()
