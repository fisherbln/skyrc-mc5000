"""History screen, curve view and the service that feeds them from Home Assistant."""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Protocol

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import chemistry as chem
from .charger import SLOTS, ChargerEntities, format_elapsed
from .chart import CurveChart
from .ha import HaClient
from .history import (
    CURVE_KEYS,
    RUN_KEYS,
    Archive,
    Run,
    curve_for,
    find_runs,
    merge_runs,
    parse_history,
)

HISTORY_DAYS = 30


class _Source(Protocol):
    charger: ChargerEntities
    states: dict[str, Any]


class HistoryService(QObject):
    """Finds runs and curves in HA's history and keeps the local archive filled."""

    runs_ready = Signal(object, str)  # list[Run], note for the user ("" if fine)
    curve_ready = Signal(object)  # Run with .curve filled
    charger_curve_ready = Signal(int)  # slot whose stored curve just arrived

    def __init__(self, client: HaClient, source: _Source, archive: Archive | None = None) -> None:
        super().__init__()
        self.client = client
        self.source = source  # anything with .charger and .states (the main window)
        self.archive = archive or Archive()
        self._queue: list[Run] = []
        self._archiving: str | None = None
        self._next = 0
        # slot -> (fetched_at, interval_s, volts): the curve the charger keeps for
        # a slot's current run (1 s early on, coarser as the run grows), fetched now
        # and then while the run lasts and once more after it finishes.
        self.charger_curves: dict[int, tuple[float, int, list[float]]] = {}
        client.response.connect(self._response)

    def _key(self, kind: str) -> str:
        self._next += 1
        return f"hist:{kind}:{self._next}"

    def _elapsed_unit_s(self) -> float:
        charger = self.source.charger
        eid = charger.slots.get(1, {}).get("elapsed", "")
        unit = (self.source.states.get(eid) or {}).get("attributes", {}).get("unit_of_measurement", "min")
        return {"s": 1, "min": 60, "h": 3600}.get(unit, 60)

    def load_runs(self, days: int = HISTORY_DAYS) -> None:
        charger = self.source.charger
        ids = [charger.slots[s][k] for s in SLOTS for k in RUN_KEYS
               if k in charger.slots.get(s, {})]
        if not ids:
            self.runs_ready.emit(merge_runs([], self.archive.all()),
                                 "Not connected: showing saved runs only.")
            return
        now = time.time()
        self._pending_runs = self._key("runs")
        self._runs_now = now
        self.client.history(self._pending_runs, ids, now - days * 86400, now)

    def load_curve(self, run: Run) -> None:
        if run.end is not None and self.archive.has(run.run_id):
            saved = self.archive.load(run.run_id)
            if saved and any(saved.curve.values()):
                self.curve_ready.emit(saved)
                return
        self._request_curve(run, "curve")

    def fetch_charger_curve(self, slot: int) -> None:
        """Ask HA for the charger's stored curve of a slot's current or last run (integration 0.5+)."""
        self.client.request(self._key(f"charger:{slot}"), {
            "type": "call_service", "domain": "skyrc_mc5000", "service": "get_history",
            "service_data": {"slot": str(slot)}, "return_response": True,
        }, timeout=30)

    def apply_charger_curve(self, run: Run) -> None:
        """Use the charger's voltage samples for the part of the run they cover."""
        got = self.charger_curves.get(run.slot)
        if not got:
            return
        fetched, interval, volts = got
        if not volts or not interval:
            return
        # The curve stops growing when a run finishes, so a copy fetched after the
        # end has its last sample at the end, not at the moment it was fetched.
        last = min(fetched, run.end) if run.end is not None else fetched
        first = last - (len(volts) - 1) * interval
        end = run.end if run.end is not None else fetched
        if first < run.start - 120 - interval or last < run.start:
            return  # those samples belong to another run
        points = [(first + i * interval, v) for i, v in enumerate(volts)]
        points = [p for p in points if run.start - 120 <= p[0] <= end + 1]
        older = [p for p in run.curve.get("voltage", []) if p[0] < first]
        newer = [p for p in run.curve.get("voltage", []) if p[0] > last]
        run.curve["voltage"] = older + points + newer

    def _request_curve(self, run: Run, kind: str) -> None:
        charger = self.source.charger
        ids = [charger.slots[run.slot][k] for k in CURVE_KEYS if k in charger.slots.get(run.slot, {})]
        if not ids:
            return
        key = self._key(f"{kind}:{run.run_id}")
        self._curve_runs = getattr(self, "_curve_runs", {})
        self._curve_runs[key] = run
        end = run.end if run.end is not None else time.time()
        self.client.history(key, ids, run.start - 5, end + 5)

    def _response(self, key: str, result: Any, error: str) -> None:
        if not key.startswith("hist:"):
            return
        if key.startswith("hist:charger:"):
            slot = int(key.split(":")[2])
            data = (result or {}).get("response") if isinstance(result, dict) else None
            # "available" from integration 0.5.1 on, "running" in 0.5.0.
            if data and data.get("available", data.get("running")) and data.get("voltages"):
                self.charger_curves[slot] = (time.time(), int(data["interval"] or 1),
                                             [float(v) for v in data["voltages"]])
            # No curve (slot emptied, run stopped by hand): keep the last copy.
            self.charger_curve_ready.emit(slot)
            return
        if key == getattr(self, "_pending_runs", None):
            archived = self.archive.all()
            if result is None:
                self.runs_ready.emit(merge_runs([], archived),
                                     f"Couldn't read Home Assistant's history ({error}); "
                                     "showing saved runs only.")
                return
            history = parse_history(result)
            charger = self.source.charger
            found: list[Run] = []
            for slot in SLOTS:
                found.extend(find_runs(slot, charger, history, self._runs_now,
                                       self._elapsed_unit_s()))
            self.runs_ready.emit(merge_runs(found, archived), "")
            self._queue = [r for r in found if r.end is not None and not self.archive.has(r.run_id)]
            self._archive_next()
            return
        run = getattr(self, "_curve_runs", {}).pop(key, None)
        if run is None:
            return
        if result is not None:
            run.curve = curve_for(run, self.source.charger, parse_history(result))
            self.apply_charger_curve(run)
            if run.end is not None and any(run.curve.values()):
                self.archive.save(run)
        if ":curve:" in key:
            self.curve_ready.emit(run)
        else:
            self._archiving = None
            self._archive_next()

    def _archive_next(self) -> None:
        if self._archiving or not self._queue:
            return
        run = self._queue.pop(0)
        self._archiving = run.run_id
        self._request_curve(run, "archive")


def _disconnect(service: HistoryService, runs_slot: Any, curve_slot: Any) -> None:
    for signal, slot in ((service.runs_ready, runs_slot), (service.curve_ready, curve_slot)):
        try:
            signal.disconnect(slot)
        except (RuntimeError, TypeError):
            pass


def capacity_text(run: Run) -> str:
    if run.operation == "cycle" and run.discharged:
        return f"{run.discharged:.0f} mAh out"
    return f"{run.capacity:.0f} mAh" if run.capacity else "–"


def run_title(run: Run) -> str:
    when = datetime.fromtimestamp(run.start).strftime("%a %b %-d, %-I:%M %p")
    what = f"{chem.label(run.chemistry)} {chem.operation_label(run.operation).lower()}"
    bits = [f"Slot {run.slot}", what, run.outcome]
    if run.capacity:
        bits.append(capacity_text(run))
    if run.duration_s:
        bits.append(format_elapsed(run.duration_s))
    return f"{when} · " + " · ".join(bits)


class RunView(QWidget):
    """Title line plus the curve chart."""

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel("Pick a run to see its curve.")
        self.title.setObjectName("muted")
        self.title.setWordWrap(True)
        self.hint = QLabel("Touch or drag on the chart to read values; double-tap to clear.")
        self.hint.setObjectName("caption")
        self.chart = CurveChart()
        layout.addWidget(self.title)
        layout.addWidget(self.chart, 1)
        layout.addWidget(self.hint)
        self.run: Run | None = None

    def show_run(self, run: Run | None, message: str = "") -> None:
        self.run = run
        if run is None:
            self.title.setText(message or "No runs yet.")
            self.chart.empty_text = message or "No data"
            self.chart.set_data({}, 0, 60)
            return
        self.title.setText(run_title(run))
        self.chart.empty_text = "Loading…" if not run.curve else "No readings recorded for this run"
        self.chart.set_data(run.curve or {}, run.start, run.end)

    def live(self, slot: int, charger: ChargerEntities, states: dict[str, Any]) -> None:
        """Append the latest readings while the shown run is still going."""
        run = self.run
        if run is None or run.end is not None or run.slot != slot:
            return
        now = time.time()
        for key in CURVE_KEYS:
            eid = charger.slots.get(slot, {}).get(key)
            try:
                value = float((states.get(eid) or {}).get("state"))
            except (TypeError, ValueError):
                continue
            self.chart.append(key, now, value)


class HistoryDialog(QDialog):
    COLUMNS = ["Started", "Slot", "Battery", "Mode", "Result", "Capacity", "Time"]

    def __init__(self, service: HistoryService, parent: QWidget | None = None,
                 slot: int | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.runs: list[Run] = []
        self.setWindowTitle("Run history")
        self.resize(1500, 950)

        root = QVBoxLayout(self)
        top = QHBoxLayout()
        self.filter = QComboBox()
        self.filter.addItem("All slots", None)
        for s in SLOTS:
            self.filter.addItem(f"Slot {s}", s)
        if slot:
            self.filter.setCurrentIndex(slot)
        self.filter.currentIndexChanged.connect(self._fill)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.reload)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        self.note = QLabel("Loading…")
        self.note.setObjectName("muted")
        top.addWidget(self.filter)
        top.addWidget(self.note, 1)
        top.addWidget(refresh)
        top.addWidget(close)
        root.addLayout(top)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(48)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.currentCellChanged.connect(self._cell_changed)
        self.view = RunView()
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.table)
        split.addWidget(self.view)
        split.setSizes([330, 600])
        root.addWidget(split, 1)

        service.runs_ready.connect(self._runs)
        service.curve_ready.connect(self._curve)
        self.reload()

    def done(self, result: int) -> None:
        _disconnect(self.service, self._runs, self._curve)
        if hasattr(self, "_timer"):
            self._timer.stop()
            try:
                self.service.charger_curve_ready.disconnect(self._charger_curve)
            except (RuntimeError, TypeError):
                pass
        super().done(result)

    def reload(self) -> None:
        self.note.setText("Loading…")
        self.service.load_runs()

    def _runs(self, runs: list[Run], note: str) -> None:
        self.runs = runs
        self.note.setText(note or f"{len(runs)} runs")
        self._fill()

    def _shown(self) -> list[Run]:
        slot = self.filter.currentData()
        return [r for r in self.runs if slot is None or r.slot == slot]

    def _fill(self) -> None:
        shown = self._shown()
        self.table.setRowCount(len(shown))
        for row, run in enumerate(shown):
            cells = [
                datetime.fromtimestamp(run.start).strftime("%b %-d, %-I:%M %p"),
                str(run.slot),
                chem.label(run.chemistry),
                chem.operation_label(run.operation),
                run.outcome,
                capacity_text(run),
                format_elapsed(run.duration_s) if run.duration_s else "–",
            ]
            for col, text in enumerate(cells):
                self.table.setItem(row, col, QTableWidgetItem(text))
        if shown:
            self.table.selectRow(0)
            self._select(0)
        else:
            self.view.show_run(None, "No runs recorded yet.")

    def _cell_changed(self, row: int, *_: int) -> None:
        self._select(row)

    def _select(self, row: int) -> None:
        shown = self._shown()
        if 0 <= row < len(shown):
            run = shown[row]
            self.view.show_run(run)
            if not run.curve:
                self.service.load_curve(run)

    def _curve(self, run: Run) -> None:
        if self.view.run and self.view.run.run_id == run.run_id:
            self.view.show_run(run)


class CurveDialog(QDialog):
    """The current (or most recent) run on one slot, updating live."""

    def __init__(self, slot: int, service: HistoryService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.slot = slot
        self.service = service
        self.setWindowTitle(f"Slot {slot} curve")
        self.resize(1400, 900)
        root = QVBoxLayout(self)
        self.view = RunView()
        self.view.show_run(None, "Loading…")
        root.addWidget(self.view, 1)
        row = QHBoxLayout()
        history = QPushButton("All runs on this slot")
        history.clicked.connect(self._history)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addStretch()
        row.addWidget(history)
        row.addWidget(close)
        root.addLayout(row)
        service.runs_ready.connect(self._runs)
        service.curve_ready.connect(self._curve)
        service.charger_curve_ready.connect(self._charger_curve)
        service.load_runs(days=3)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_charger_curve)
        self._timer.start(60_000)

    def _runs(self, runs: list[Run], note: str) -> None:
        mine = [r for r in runs if r.slot == self.slot]
        if not mine:
            self.view.show_run(None, note or "No runs on this slot in the last 3 days.")
            return
        run = mine[0]  # newest
        self.view.show_run(run)
        self.service.load_curve(run)

    def _curve(self, run: Run) -> None:
        if self.view.run and self.view.run.run_id == run.run_id:
            self.view.show_run(run)
            if run.end is None:
                self.service.fetch_charger_curve(self.slot)

    def _refresh_charger_curve(self) -> None:
        run = self.view.run
        if run is not None and run.end is None:
            self.service.fetch_charger_curve(self.slot)

    def _charger_curve(self, slot: int) -> None:
        run = self.view.run
        if slot == self.slot and run is not None and run.end is None and run.curve:
            self.service.apply_charger_curve(run)
            self.view.show_run(run)
            got = self.service.charger_curves.get(slot)
            every = f"every {got[1]} s" if got and got[1] > 1 else "every second"
            self.view.hint.setText(f"Voltage {every} from the charger's own memory. "
                                   "Touch or drag to read values; double-tap to clear.")

    def live(self, charger: ChargerEntities, states: dict[str, Any]) -> None:
        self.view.live(self.slot, charger, states)

    def done(self, result: int) -> None:
        _disconnect(self.service, self._runs, self._curve)
        if hasattr(self, "_timer"):
            self._timer.stop()
            try:
                self.service.charger_curve_ready.disconnect(self._charger_curve)
            except (RuntimeError, TypeError):
                pass
        super().done(result)

    def _history(self) -> None:
        HistoryDialog(self.service, self, slot=self.slot).exec()
