"""The charger as Home Assistant sees it: which entity is which, and slot readings."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

PLATFORM = "skyrc_mc5000"
SLOTS = (1, 2, 3, 4)

# The integration builds unique IDs as "<bluetooth address>_<key>".
_KEY = re.compile(
    r"_(slot([1-4])_([a-z_]+)|stop_all|connection|setting_([a-z_]+)|usb_([a-z_]+))$"
)

UNAVAILABLE = {"unavailable", "unknown", None, ""}


@dataclass
class ChargerEntities:
    """Entity IDs for one MC5000, found from the entity registry."""

    config_entry_id: str | None = None
    connection: str | None = None
    stop_all: str | None = None
    # slot -> key ("status", "voltage", "stop", ...) -> entity_id
    slots: dict[int, dict[str, str]] = field(default_factory=dict)
    # Charger system settings (integration 0.3.0+): key -> select entity_id
    settings: dict[str, str] = field(default_factory=dict)
    # USB-C port sensors: "voltage", "current", "power", "energy", "runtime"
    usb: dict[str, str] = field(default_factory=dict)

    def all_ids(self) -> list[str]:
        ids = [e for e in (self.connection, self.stop_all) if e]
        for keys in self.slots.values():
            ids.extend(keys.values())
        ids.extend(self.settings.values())
        ids.extend(self.usb.values())
        return sorted(ids)

    @property
    def found(self) -> bool:
        return bool(self.slots)


def find_charger(registry: list[dict[str, Any]]) -> ChargerEntities:
    """Pick the first MC5000 in the entity registry and map its entities."""
    mine = [e for e in registry if e.get("platform") == PLATFORM and e.get("unique_id")]
    if not mine:
        return ChargerEntities()
    entry_ids = sorted({e.get("config_entry_id") for e in mine if e.get("config_entry_id")})
    entry_id = entry_ids[0] if entry_ids else None
    charger = ChargerEntities(config_entry_id=entry_id)
    for ent in mine:
        if entry_id and ent.get("config_entry_id") != entry_id:
            continue
        m = _KEY.search(ent["unique_id"])
        if not m:
            continue
        if m.group(1) == "stop_all":
            charger.stop_all = ent["entity_id"]
        elif m.group(1) == "connection":
            charger.connection = ent["entity_id"]
        elif m.group(4):
            charger.settings[m.group(4)] = ent["entity_id"]
        elif m.group(5):
            charger.usb[m.group(5)] = ent["entity_id"]
        else:
            slot, key = int(m.group(2)), m.group(3)
            charger.slots.setdefault(slot, {})[key] = ent["entity_id"]
    return charger


def _num(state: dict[str, Any] | None) -> float | None:
    if not state or state.get("state") in UNAVAILABLE:
        return None
    try:
        return float(state["state"])
    except (TypeError, ValueError):
        return None


def _str(state: dict[str, Any] | None) -> str | None:
    if not state or state.get("state") in UNAVAILABLE:
        return None
    return str(state["state"])


def _seconds(state: dict[str, Any] | None) -> float | None:
    value = _num(state)
    if value is None:
        return None
    unit = (state or {}).get("attributes", {}).get("unit_of_measurement", "s")
    return value * {"min": 60, "h": 3600, "d": 86400}.get(unit, 1)


@dataclass
class SlotReading:
    slot: int
    available: bool = False
    status: str | None = None
    voltage: float | None = None
    current: float | None = None
    capacity: float | None = None
    elapsed_s: float | None = None
    resistance: float | None = None
    chemistry: str | None = None
    operation: str | None = None
    error: str | None = None

    @property
    def active(self) -> bool:
        return self.status in {"charging", "discharging", "resting"}


def read_slot(
    slot: int, charger: ChargerEntities, states: dict[str, dict[str, Any]]
) -> SlotReading:
    keys = charger.slots.get(slot, {})

    def st(key: str) -> dict[str, Any] | None:
        eid = keys.get(key)
        return states.get(eid) if eid else None

    reading = SlotReading(
        slot=slot,
        status=_str(st("status")),
        voltage=_num(st("voltage")),
        current=_num(st("current")),
        capacity=_num(st("capacity")),
        elapsed_s=_seconds(st("elapsed")),
        resistance=_num(st("resistance")),
        chemistry=_str(st("chemistry")),
        operation=_str(st("operation")),
        error=_str(st("error")),
    )
    reading.available = reading.status is not None
    if reading.error == "none":
        reading.error = None
    return reading


def format_elapsed(seconds: float | None) -> str:
    if seconds is None:
        return "–"
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def error_label(error: str | None) -> str:
    if not error:
        return ""
    return error.replace("_", " ").capitalize()


@dataclass
class RunResult:
    """How a slot's last run ended (kept by this app)."""

    ended: str  # ISO time
    outcome: str  # "Done", "Stopped" or an error label
    capacity: float | None
    elapsed_s: float | None
    chemistry: str | None
    operation: str | None


class RunTracker:
    """Watches slot readings and records each run's final numbers.

    The charger zeroes the mAh counter when a run ends in an error, so the
    last non-zero reading seen during the run is what gets kept.
    """

    def __init__(self) -> None:
        self._running: dict[int, SlotReading] = {}
        self.results: dict[int, RunResult] = {}

    def update(self, reading: SlotReading, now_iso: str) -> RunResult | None:
        slot = reading.slot
        if not reading.available:
            return None  # charger offline: don't treat as the run ending
        if reading.active:
            prev = self._running.get(slot)
            keep = SlotReading(**reading.__dict__)
            if prev:
                if not keep.capacity and prev.capacity:
                    keep.capacity = prev.capacity
                if not keep.elapsed_s and prev.elapsed_s:
                    keep.elapsed_s = prev.elapsed_s
            self._running[slot] = keep
            return None
        last = self._running.pop(slot, None)
        if last is None:
            return None
        if reading.error:
            outcome = error_label(reading.error)
        elif reading.status == "done":
            outcome = "Done"
        else:
            outcome = "Stopped"
        capacity = reading.capacity if reading.capacity else last.capacity
        elapsed = reading.elapsed_s if reading.elapsed_s else last.elapsed_s
        result = RunResult(now_iso, outcome, capacity, elapsed, last.chemistry, last.operation)
        self.results[slot] = result
        return result


@dataclass
class UsbReading:
    voltage: float | None = None
    current: float | None = None
    power: float | None = None
    energy: float | None = None

    @property
    def active(self) -> bool:
        return bool(self.power)


def read_usb(charger: ChargerEntities, states: dict[str, dict[str, Any]]) -> UsbReading | None:
    if not charger.usb:
        return None

    def st(key: str) -> dict[str, Any] | None:
        eid = charger.usb.get(key)
        return states.get(eid) if eid else None

    return UsbReading(_num(st("voltage")), _num(st("current")), _num(st("power")),
                      _num(st("energy")))
