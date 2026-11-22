"""MC5000 Bench: a touch-friendly controller for a SkyRC MC5000 in Home Assistant."""
from __future__ import annotations

import argparse
import sys
import weakref
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from . import chemistry as chem
from .charger import (
    SLOTS,
    ChargerEntities,
    RunResult,
    RunTracker,
    SlotReading,
    error_label,
    format_elapsed,
    read_slot,
)
from .config import config_dir, load_json, load_settings, save_json, save_settings, Settings
from .ha import HaClient
from .presets import Preset, PresetStore

STATUS_COLORS = {
    "charging": "#1e88e5",
    "discharging": "#fb8c00",
    "resting": "#8e8e93",
    "done": "#43a047",
    "ready": "#8e8e93",
    "standby": "#8e8e93",
    "error": "#e53935",
    "offline": "#8e8e93",
}

STYLE = """
QWidget { font-size: 15pt; }
QFrame#card { border: 1px solid palette(mid); border-radius: 16px; background: palette(base); }
QLabel#slotTitle { font-size: 22pt; font-weight: 600; }
QLabel#chip { border-radius: 12px; padding: 4px 14px; color: white; font-weight: 600; font-size: 13pt; }
QLabel#value { font-size: 26pt; font-weight: 600; }
QLabel#caption { color: palette(placeholder-text); font-size: 11pt; }
QLabel#muted { color: palette(placeholder-text); font-size: 12pt; }
QLabel#error { color: #e53935; font-weight: 600; }
QLabel#banner { background: #fb8c00; color: white; border-radius: 10px; padding: 8px 14px; }
QPushButton { min-height: 52px; padding: 0 22px; border-radius: 12px;
              background: palette(button); border: 1px solid palette(mid); }
QPushButton:pressed { background: palette(midlight); }
QPushButton:disabled { color: palette(placeholder-text); border-color: palette(midlight); }
QPushButton#start { background: #1e88e5; border-color: #1e88e5; color: white; font-weight: 600; }
QPushButton#start:pressed { background: #1565c0; }
QPushButton#start:disabled { background: palette(midlight); border-color: palette(midlight);
                             color: palette(placeholder-text); }
QPushButton#stop { font-weight: 600; color: #e53935; border-color: #e53935; }
QPushButton#stop:disabled { color: palette(placeholder-text); border-color: palette(midlight); }
QComboBox { min-height: 52px; padding: 0 12px; }
QComboBox QAbstractItemView::item { min-height: 48px; }
QListWidget::item { min-height: 48px; padding: 0 8px; }
QSpinBox, QDoubleSpinBox, QLineEdit { min-height: 48px; }
"""


def _fmt(value: float | None, unit: str, digits: int) -> str:
    if value is None:
        return "–"
    return f"{value:.{digits}f} {unit}"


class SlotCard(QFrame):
    def __init__(self, slot: int, window: "MainWindow") -> None:
        super().__init__()
        self.slot = slot
        # A weak link back to the window: a reference cycle between Qt widgets
        # can crash PySide when Python's garbage collector breaks it.
        self.main = weakref.proxy(window)
        self.reading = SlotReading(slot)
        self.setObjectName("card")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(10)

        top = QHBoxLayout()
        title = QLabel(f"Slot {slot}")
        title.setObjectName("slotTitle")
        self.chip = QLabel("Offline")
        self.chip.setObjectName("chip")
        top.addWidget(title)
        top.addStretch()
        top.addWidget(self.chip)
        root.addLayout(top)

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        self.values: dict[str, QLabel] = {}
        for col, (key, caption) in enumerate(
            [("voltage", "Voltage"), ("current", "Current"), ("capacity", "Capacity"),
             ("elapsed", "Time"), ("resistance", "Resistance")]
        ):
            cap = QLabel(caption)
            cap.setObjectName("caption")
            val = QLabel("–")
            val.setObjectName("value")
            grid.addWidget(cap, 0, col)
            grid.addWidget(val, 1, col)
            self.values[key] = val
        root.addLayout(grid)

        self.program = QLabel("")
        self.program.setObjectName("muted")
        self.error = QLabel("")
        self.error.setObjectName("error")
        self.error.hide()
        self.last = QLabel("")
        self.last.setObjectName("muted")
        root.addWidget(self.program)
        root.addWidget(self.error)
        root.addWidget(self.last)
        root.addStretch()

        controls = QHBoxLayout()
        self.preset = QComboBox()
        self.preset.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.preset.currentTextChanged.connect(self._preset_changed)
        self.start = QPushButton("Start")
        self.start.setObjectName("start")
        self.start.clicked.connect(self._start)
        self.stop = QPushButton("Stop")
        self.stop.setObjectName("stop")
        self.stop.clicked.connect(self._stop)
        controls.addWidget(self.preset, 1)
        controls.addWidget(self.start)
        controls.addWidget(self.stop)
        root.addLayout(controls)
        self.summary = QLabel("")
        self.summary.setObjectName("muted")
        self.summary.setWordWrap(True)
        self.summary.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.summary.setMinimumHeight(self.summary.fontMetrics().lineSpacing() * 2 + 4)
        root.addWidget(self.summary)

    # ----- presets -----

    def set_presets(self, names: list[str], selected: str | None) -> None:
        self.preset.blockSignals(True)
        self.preset.clear()
        self.preset.addItems(names)
        if selected in names:
            self.preset.setCurrentText(selected)
        self.preset.blockSignals(False)
        self._preset_changed(self.preset.currentText(), remember=False)

    def selected_preset(self) -> Preset | None:
        return self.main.presets.get(self.preset.currentText())

    def _preset_changed(self, _name: str, remember: bool = True) -> None:
        preset = self.selected_preset()
        if preset is None:
            self.summary.setText("No presets yet: add one under Presets.")
        else:
            text = preset.summary()
            if preset.note:
                text += f"\n{preset.note}"
            self.summary.setText(text)
        if remember:
            self.main.remember_selection(self.slot, self.preset.currentText())
        self._refresh_buttons()

    # ----- readings -----

    def show_reading(self, reading: SlotReading) -> None:
        self.reading = reading
        if not reading.available:
            self._chip("Offline", "offline")
            for label in self.values.values():
                label.setText("–")
            self.program.setText("")
            self.error.hide()
        else:
            status = reading.status or "standby"
            if reading.error:
                self._chip("Error", "error")
            else:
                self._chip(chem.STATUS_LABELS.get(status, status.title()), status)
            self.values["voltage"].setText(_fmt(reading.voltage, "V", 3))
            current = reading.current
            self.values["current"].setText(_fmt(abs(current) if current is not None else None, "A", 2))
            self.values["capacity"].setText(_fmt(reading.capacity, "mAh", 0))
            self.values["elapsed"].setText(format_elapsed(reading.elapsed_s) if reading.elapsed_s else "–")
            self.values["resistance"].setText(_fmt(reading.resistance, "mΩ", 0))
            if reading.chemistry or reading.operation:
                self.program.setText(
                    f"Charger set to {chem.label(reading.chemistry)} · "
                    f"{chem.operation_label(reading.operation)}"
                )
            if reading.error:
                self.error.setText(error_label(reading.error))
                self.error.show()
            else:
                self.error.hide()
        self._refresh_buttons()

    def show_result(self, result: RunResult | None) -> None:
        if result is None:
            self.last.setText("")
            return
        try:
            when = datetime.fromisoformat(result.ended).strftime("%-I:%M %p, %b %-d")
        except ValueError:
            when = result.ended
        bits = [result.outcome]
        if result.capacity:
            bits.append(f"{result.capacity:.0f} mAh")
        if result.elapsed_s:
            bits.append(format_elapsed(result.elapsed_s))
        if result.chemistry:
            bits.append(f"{chem.label(result.chemistry)} {chem.operation_label(result.operation).lower()}")
        self.last.setText(f"Last run: {' · '.join(bits)} · ended {when}")

    def _chip(self, text: str, key: str) -> None:
        self.chip.setText(text)
        self.chip.setStyleSheet(f"background: {STATUS_COLORS.get(key, '#8e8e93')};")

    def _refresh_buttons(self) -> None:
        online = self.main.online and self.reading.available
        preset = self.selected_preset()
        self.start.setEnabled(bool(online and not self.reading.active and preset and not preset.problems()))
        self.stop.setEnabled(bool(online and self.reading.active))
        if preset is not None and preset.problems():
            self.summary.setText("Fix this preset: " + " ".join(preset.problems()))

    # ----- actions -----

    def _start(self) -> None:
        preset = self.selected_preset()
        if preset is None:
            return
        answer = QMessageBox.question(
            self,
            f"Start slot {self.slot}",
            f"Start “{preset.name}” on slot {self.slot}?\n\n{preset.summary()}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.main.start_preset(self.slot, preset)

    def _stop(self) -> None:
        self.main.stop_slot(self.slot)


class PresetDialog(QDialog):
    """Add, change and delete presets."""

    def __init__(self, store: PresetStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Presets")
        self.resize(1100, 700)
        self.items = [Preset(**asdict(p)) for p in store.presets]
        self._loading = False

        layout = QHBoxLayout(self)
        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._select)
        left.addWidget(self.list, 1)
        row = QHBoxLayout()
        for text, fn in (("Add", self._add), ("Copy", self._copy), ("Delete", self._delete),
                         ("Up", lambda: self._move(-1)), ("Down", lambda: self._move(1))):
            btn = QPushButton(text)
            btn.clicked.connect(fn)
            row.addWidget(btn)
        left.addLayout(row)
        layout.addLayout(left, 1)

        right = QVBoxLayout()
        form = QFormLayout()
        self.name = QLineEdit()
        self.battery = QComboBox()
        for key, label in chem.CHEMISTRIES.items():
            self.battery.addItem(label, key)
        self.operation = QComboBox()
        self.charge = QDoubleSpinBox()
        self.charge.setRange(*chem.CHARGE_A)
        self.charge.setSingleStep(0.05)
        self.charge.setSuffix(" A")
        self.discharge = QDoubleSpinBox()
        self.discharge.setRange(*chem.DISCHARGE_A)
        self.discharge.setSingleStep(0.05)
        self.discharge.setSuffix(" A")
        self.capacity = QSpinBox()
        self.capacity.setRange(*chem.CAPACITY_MAH)
        self.capacity.setSingleStep(100)
        self.capacity.setSuffix(" mAh")
        self.storage = QDoubleSpinBox()
        self.storage.setRange(2.0, 4.35)
        self.storage.setDecimals(2)
        self.storage.setSingleStep(0.05)
        self.storage.setSuffix(" V")
        self.cutoff_default = QCheckBox("Charger default")
        self.cutoff = QDoubleSpinBox()
        self.cutoff.setRange(0.5, 4.3)
        self.cutoff.setDecimals(2)
        self.cutoff.setSingleStep(0.05)
        self.cutoff.setSuffix(" V")
        cutoff_row = QHBoxLayout()
        cutoff_row.addWidget(self.cutoff)
        cutoff_row.addWidget(self.cutoff_default)
        self.cycles = QSpinBox()
        self.cycles.setRange(1, 99)
        self.note = QLineEdit()
        form.addRow("Name", self.name)
        form.addRow("Battery type", self.battery)
        form.addRow("Mode", self.operation)
        form.addRow("Charge current", self.charge)
        form.addRow("Discharge current", self.discharge)
        form.addRow("Capacity", self.capacity)
        form.addRow("Storage voltage", self.storage)
        form.addRow("Discharge cutoff", cutoff_row)
        form.addRow("Cycles", self.cycles)
        form.addRow("Note", self.note)
        self.form = form
        right.addLayout(form)
        self.problems = QLabel("")
        self.problems.setObjectName("error")
        self.problems.setWordWrap(True)
        right.addWidget(self.problems)
        right.addStretch()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        right.addWidget(buttons)
        layout.addLayout(right, 2)

        for widget in (self.name, self.note):
            widget.textChanged.connect(self._edited)
        for widget in (self.charge, self.discharge, self.storage, self.cutoff):
            widget.valueChanged.connect(self._edited)
        self.capacity.valueChanged.connect(self._edited)
        self.cycles.valueChanged.connect(self._edited)
        self.cutoff_default.toggled.connect(self._edited)
        self.battery.currentIndexChanged.connect(self._battery_changed)
        self.operation.currentIndexChanged.connect(self._edited)

        self._fill_list()
        if self.items:
            self.list.setCurrentRow(0)
        else:
            self._enable_form(False)

    def _fill_list(self, keep: int | None = None) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        self.list.addItems([p.name for p in self.items])
        self.list.blockSignals(False)
        if keep is not None and self.items:
            self.list.setCurrentRow(max(0, min(keep, len(self.items) - 1)))

    def _enable_form(self, on: bool) -> None:
        for i in range(self.form.rowCount()):
            item = self.form.itemAt(i, QFormLayout.ItemRole.FieldRole)
            if item and item.widget():
                item.widget().setEnabled(on)
        self.cutoff.setEnabled(on)
        self.cutoff_default.setEnabled(on)

    def _current(self) -> Preset | None:
        row = self.list.currentRow()
        return self.items[row] if 0 <= row < len(self.items) else None

    def _select(self, row: int) -> None:
        preset = self._current()
        self._enable_form(preset is not None)
        if preset is None:
            return
        self._loading = True
        self.name.setText(preset.name)
        self.battery.setCurrentIndex(max(0, self.battery.findData(preset.battery_type)))
        self._fill_operations(preset.battery_type, preset.operation)
        self.charge.setValue(preset.charge_current)
        self.discharge.setValue(preset.discharge_current)
        self.capacity.setValue(preset.capacity)
        self.storage.setValue(
            (preset.storage_voltage or chem.STORAGE_MV.get(preset.battery_type, 3800)) / 1000
        )
        self.cutoff_default.setChecked(preset.discharge_voltage is None)
        self.cutoff.setValue(
            (preset.discharge_voltage or chem.DISCHARGE_MV[preset.battery_type]) / 1000
        )
        self.cycles.setValue(preset.cycle_count)
        self.note.setText(preset.note)
        self._loading = False
        self._update_visibility()
        self._show_problems()

    def _fill_operations(self, battery: str, selected: str | None) -> None:
        self.operation.blockSignals(True)
        self.operation.clear()
        for op in chem.OPERATIONS[battery]:
            self.operation.addItem(chem.operation_label(op), op)
        idx = self.operation.findData(selected)
        self.operation.setCurrentIndex(idx if idx >= 0 else 0)
        self.operation.blockSignals(False)

    def _battery_changed(self) -> None:
        if self._loading:
            return
        battery = self.battery.currentData()
        self._fill_operations(battery, self.operation.currentData())
        self.storage.setValue(chem.STORAGE_MV.get(battery, 3800) / 1000)
        self.cutoff.setValue(chem.DISCHARGE_MV[battery] / 1000)
        self._edited()

    def _update_visibility(self) -> None:
        op = self.operation.currentData() or "charge"
        rows = {
            self.charge: chem.uses_charge(op),
            self.discharge: chem.uses_discharge(op),
            self.storage: op == "storage",
            self.cycles: op == "cycle",
        }
        for widget, visible in rows.items():
            self.form.setRowVisible(widget, visible)
        self.cutoff.setEnabled(not self.cutoff_default.isChecked())

    def _edited(self, *_: Any) -> None:
        if self._loading:
            return
        preset = self._current()
        if preset is None:
            return
        preset.name = self.name.text()
        preset.battery_type = self.battery.currentData()
        preset.operation = self.operation.currentData()
        preset.charge_current = round(self.charge.value(), 2)
        preset.discharge_current = round(self.discharge.value(), 2)
        preset.capacity = self.capacity.value()
        preset.storage_voltage = (
            round(self.storage.value() * 1000) if preset.operation == "storage" else None
        )
        preset.discharge_voltage = (
            None if self.cutoff_default.isChecked() else round(self.cutoff.value() * 1000)
        )
        preset.cycle_count = self.cycles.value() if preset.operation == "cycle" else 1
        preset.note = self.note.text()
        item = self.list.currentItem()
        if item:
            item.setText(preset.name)
        self._update_visibility()
        self._show_problems()

    def _show_problems(self) -> None:
        preset = self._current()
        issues = preset.problems() if preset else []
        names = [p.name.strip() for p in self.items]
        if preset and names.count(preset.name.strip()) > 1:
            issues.append("Another preset already has this name.")
        self.problems.setText(" ".join(issues))

    def _add(self) -> None:
        self.items.append(Preset("New preset", "nimh", "charge", 2000))
        self._fill_list(len(self.items) - 1)

    def _copy(self) -> None:
        preset = self._current()
        if preset is None:
            return
        clone = Preset(**asdict(preset))
        clone.name = f"{preset.name} (copy)"
        self.items.insert(self.list.currentRow() + 1, clone)
        self._fill_list(self.list.currentRow() + 1)

    def _delete(self) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(self.items):
            del self.items[row]
            self._fill_list(row)
            if not self.items:
                self._enable_form(False)

    def _move(self, delta: int) -> None:
        row = self.list.currentRow()
        new = row + delta
        if 0 <= row < len(self.items) and 0 <= new < len(self.items):
            self.items[row], self.items[new] = self.items[new], self.items[row]
            self._fill_list(new)

    def _save(self) -> None:
        names = [p.name.strip() for p in self.items]
        bad = [p.name for p in self.items if p.problems()]
        if len(set(names)) != len(names):
            QMessageBox.warning(self, "Presets", "Two presets have the same name.")
            return
        if bad:
            QMessageBox.warning(self, "Presets", "Fix these presets first: " + ", ".join(bad))
            return
        for p in self.items:
            p.name = p.name.strip()
        self.accept()


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Home Assistant connection")
        self.resize(900, 360)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "The charger stays connected to Home Assistant; this app sends its "
            "commands through Home Assistant. Create a token in Home Assistant under "
            "your profile → Security → Long-lived access tokens."
        )
        intro.setWordWrap(True)
        intro.setObjectName("muted")
        layout.addWidget(intro)
        form = QFormLayout()
        self.url = QLineEdit(settings.url)
        self.url.setPlaceholderText("http://homeassistant.local:8123")
        self.token = QLineEdit(settings.token)
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.verify = QCheckBox("Check the HTTPS certificate")
        self.verify.setChecked(settings.verify_tls)
        form.addRow("Address", self.url)
        form.addRow("Access token", self.token)
        form.addRow("", self.verify)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def value(self) -> Settings:
        return Settings(self.url.text().strip(), self.token.text().strip(), self.verify.isChecked())


class MainWindow(QMainWindow):
    def __init__(self, client: HaClient | None = None) -> None:
        super().__init__()
        self.setWindowTitle("MC5000 Bench")
        self.resize(1600, 960)
        self.settings = load_settings()
        self.presets = PresetStore.load(config_dir() / "presets.json")
        self.selection: dict[str, str] = load_json("selection.json") or {}
        self.tracker = RunTracker()
        for slot, raw in (load_json("results.json") or {}).items():
            try:
                self.tracker.results[int(slot)] = RunResult(**raw)
            except (TypeError, ValueError):
                pass
        self.charger = ChargerEntities()
        self.states: dict[str, dict[str, Any]] = {}
        self.online = False

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(14)

        header = QHBoxLayout()
        title = QLabel("MC5000")
        title.setObjectName("slotTitle")
        self.dot = QLabel("●")
        self.status = QLabel("Not connected")
        self.status.setObjectName("muted")
        presets_btn = QPushButton("Presets")
        presets_btn.clicked.connect(self.edit_presets)
        settings_btn = QPushButton("Settings")
        settings_btn.clicked.connect(self.edit_settings)
        self.stop_all = QPushButton("Stop all")
        self.stop_all.setObjectName("stop")
        self.stop_all.clicked.connect(self._stop_all)
        header.addWidget(title)
        header.addSpacing(12)
        header.addWidget(self.dot)
        header.addWidget(self.status, 1)
        header.addWidget(presets_btn)
        header.addWidget(settings_btn)
        header.addWidget(self.stop_all)
        root.addLayout(header)

        banner_row = QHBoxLayout()
        self.banner = QLabel(
            "Home Assistant's connection to the charger is paused (for the SkyRC app)."
        )
        self.banner.setObjectName("banner")
        self.reconnect = QPushButton("Reconnect charger")
        self.reconnect.clicked.connect(self._reconnect_charger)
        banner_row.addWidget(self.banner, 1)
        banner_row.addWidget(self.reconnect)
        self.banner_box = QWidget()
        self.banner_box.setLayout(banner_row)
        self.banner_box.hide()
        root.addWidget(self.banner_box)

        grid = QGridLayout()
        grid.setSpacing(16)
        self.cards: dict[int, SlotCard] = {}
        for i, slot in enumerate(SLOTS):
            card = SlotCard(slot, self)
            self.cards[slot] = card
            grid.addWidget(card, i // 2, i % 2)
            card.show_result(self.tracker.results.get(slot))
        root.addLayout(grid, 1)
        self.setCentralWidget(central)
        self._fill_presets()
        self._set_status("connecting" if self.settings.complete else "error",
                         "Set up the Home Assistant connection under Settings."
                         if not self.settings.complete else "Connecting…")

        self.client = client or HaClient()
        self.client.status.connect(self._set_status)
        self.client.charger_found.connect(self._charger_found)
        self.client.states_changed.connect(self._states_changed)
        self.client.service_failed.connect(self._service_failed)
        if self.settings.complete:
            self.client.connect_to(self.settings.url, self.settings.token, self.settings.verify_tls)

    # ----- connection -----

    def _set_status(self, level: str, message: str) -> None:
        self.online = level == "ok"
        color = {"ok": "#43a047", "connecting": "#fb8c00"}.get(level, "#e53935")
        self.dot.setStyleSheet(f"color: {color};")
        self.status.setText(message)
        self.stop_all.setEnabled(self.online)
        for card in self.cards.values():
            if self.online:
                card._refresh_buttons()  # states can arrive just before "ok"
            else:
                card.show_reading(SlotReading(card.slot))

    def _charger_found(self, charger: ChargerEntities) -> None:
        self.charger = charger

    def _states_changed(self, states: dict[str, dict[str, Any]]) -> None:
        self.states = states
        now = datetime.now().isoformat(timespec="seconds")
        changed_results = False
        for slot, card in self.cards.items():
            reading = read_slot(slot, self.charger, states)
            card.show_reading(reading)
            if self.tracker.update(reading, now):
                card.show_result(self.tracker.results[slot])
                changed_results = True
        if changed_results:
            save_json("results.json", {str(k): asdict(v) for k, v in self.tracker.results.items()})
        conn = states.get(self.charger.connection or "", {})
        paused = conn.get("state") == "off"
        self.banner_box.setVisible(paused)
        if self.online:
            if paused or any(c.reading.available for c in self.cards.values()):
                self.status.setText("Connected to Home Assistant")
            else:
                self.status.setText(
                    "Connected to Home Assistant · charger not reachable (off or out of range?)"
                )

    def _service_failed(self, message: str) -> None:
        QMessageBox.warning(self, "Home Assistant", message)

    # ----- actions -----

    def start_preset(self, slot: int, preset: Preset) -> None:
        self.client.call_service(
            "skyrc_mc5000", "start_program",
            preset.service_data(slot, self.charger.config_entry_id),
        )

    def stop_slot(self, slot: int) -> None:
        eid = self.charger.slots.get(slot, {}).get("stop")
        if eid:
            self.client.press(eid)

    def _stop_all(self) -> None:
        if not self.charger.stop_all:
            return
        answer = QMessageBox.question(
            self, "Stop all", "Stop every slot?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.client.press(self.charger.stop_all)

    def _reconnect_charger(self) -> None:
        if self.charger.connection:
            self.client.call_service("switch", "turn_on", entity_id=self.charger.connection)

    # ----- presets & settings -----

    def _fill_presets(self) -> None:
        names = self.presets.names()
        for slot, card in self.cards.items():
            card.set_presets(names, self.selection.get(str(slot)))

    def remember_selection(self, slot: int, name: str) -> None:
        self.selection[str(slot)] = name
        save_json("selection.json", self.selection)

    def edit_presets(self) -> None:
        dialog = PresetDialog(self.presets, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.presets.presets = dialog.items
            self.presets.save()
            self._fill_presets()

    def edit_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.settings = dialog.value()
            save_settings(self.settings)
            if self.settings.complete:
                self.client.connect_to(self.settings.url, self.settings.token, self.settings.verify_tls)

    def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt name
        self.client.shutdown()
        super().closeEvent(event)


DESKTOP_ENTRY = """[Desktop Entry]
Type=Application
Name=MC5000 Bench
Comment=Control the SkyRC MC5000 charger through Home Assistant
Exec={exe}
Icon=battery
Terminal=false
Categories=Utility;Electronics;
"""


def install_desktop_entry() -> Path:
    target = Path.home() / ".local/share/applications/mc5000-bench.desktop"
    target.parent.mkdir(parents=True, exist_ok=True)
    exe = Path(sys.argv[0]).resolve()
    target.write_text(DESKTOP_ENTRY.format(exe=exe))
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mc5000-bench", description=__doc__)
    parser.add_argument("--fullscreen", action="store_true", help="fill the screen")
    parser.add_argument("--install-desktop", action="store_true",
                        help="add MC5000 Bench to the desktop's app menu and exit")
    parser.add_argument("--version", action="version", version=__version__)
    args, qt_args = parser.parse_known_args(argv)
    if args.install_desktop:
        print(f"Added {install_desktop_entry()}")
        return 0
    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName("mc5000-bench")
    app.setApplicationDisplayName("MC5000 Bench")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    app.setWindowIcon(QIcon.fromTheme("battery"))
    font = QFont(app.font())
    app.setFont(font)
    window = MainWindow()
    if args.fullscreen:
        window.showFullScreen()
    else:
        window.show()
    if not window.settings.complete:
        window.edit_settings()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
