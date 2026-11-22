"""Slot presets: named programs saved on this computer."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from . import chemistry as chem


@dataclass
class Preset:
    name: str
    battery_type: str
    operation: str
    capacity: int
    charge_current: float = 0.5
    discharge_current: float = 0.5
    # Optional overrides; None means the charger's default for the chemistry.
    storage_voltage: int | None = None
    discharge_voltage: int | None = None
    cycle_count: int = 1
    note: str = ""
    # Advanced settings (charger defaults unless changed).
    charge_voltage: int | None = None  # mV; None = the chemistry's full charge
    cycle_mode: str = "charge_discharge"
    charge_end_current: int = 100  # mA
    discharge_end_current: int = 100  # mA
    rest_time: int = 10  # minutes between phases
    delta_v: int = 6  # mV, nickel full-charge detection
    trickle_current: int = 0  # mA, nickel only
    protection_temperature: int = 60  # °C
    protection_time: int = 0  # minutes, 0 = charger default
    restart_voltage: int = 0  # mV, lithium recharge threshold, 0 = off

    def problems(self) -> list[str]:
        """Reasons the preset can't be sent; empty when it is fine."""
        out: list[str] = []
        if not self.name.strip():
            out.append("Give the preset a name.")
        if self.battery_type not in chem.CHEMISTRIES:
            out.append(f"Unknown battery type {self.battery_type!r}.")
            return out
        if self.operation not in chem.OPERATIONS[self.battery_type]:
            out.append(
                f"{chem.label(self.battery_type)} has no "
                f"{chem.operation_label(self.operation).lower()} mode."
            )
        lo, hi = chem.CAPACITY_MAH
        if not lo <= self.capacity <= hi:
            out.append(f"Capacity must be {lo}–{hi} mAh.")
        if chem.uses_charge(self.operation):
            lo, hi = chem.CHARGE_A
            if not lo <= self.charge_current <= hi:
                out.append(f"Charge current must be {lo}–{hi} A.")
        if chem.uses_discharge(self.operation):
            lo, hi = chem.DISCHARGE_A
            if not lo <= self.discharge_current <= hi:
                out.append(f"Discharge current must be {lo}–{hi} A.")
        if self.storage_voltage is not None:
            if self.operation != "storage":
                out.append("Storage voltage only applies to the storage mode.")
            elif not 2000 <= self.storage_voltage <= chem.MAX_CHARGE_MV[self.battery_type]:
                out.append(
                    "Storage voltage must be between 2.00 V and "
                    f"{chem.MAX_CHARGE_MV[self.battery_type] / 1000:.2f} V."
                )
        if self.discharge_voltage is not None and not (
            500 <= self.discharge_voltage < chem.MAX_CHARGE_MV[self.battery_type]
        ):
            out.append("Discharge cutoff is out of range for this battery type.")
        if not 1 <= self.cycle_count <= 99:
            out.append("Cycles must be 1–99.")
        top = chem.MAX_CHARGE_MV[self.battery_type]
        if self.charge_voltage is not None and not 1000 <= self.charge_voltage <= top:
            out.append(f"Charge voltage can't be above {top / 1000:.2f} V for this battery.")
        if self.cycle_mode not in chem.CYCLE_MODES:
            out.append("Unknown cycle order.")
        for label, value in (("Charge end current", self.charge_end_current),
                             ("Discharge end current", self.discharge_end_current)):
            if not 10 <= value <= 1000:
                out.append(f"{label} must be 10–1000 mA.")
        if not 0 <= self.rest_time <= 240:
            out.append("Rest time must be 0–240 min.")
        if not 0 <= self.delta_v <= 20:
            out.append("ΔV must be 0–20 mV.")
        if not 0 <= self.trickle_current <= 300 or self.trickle_current % 10:
            out.append("Trickle current must be 0–300 mA in 10 mA steps.")
        if not 20 <= self.protection_temperature <= 71:
            out.append("Temperature limit must be 20–71 °C.")
        if not 0 <= self.protection_time <= 1440:
            out.append("Time limit must be 0–1440 min.")
        if self.restart_voltage and not 1000 <= self.restart_voltage < top:
            out.append("Restart voltage must be below the full-charge voltage.")
        return out

    def summary(self) -> str:
        parts = [chem.label(self.battery_type), chem.operation_label(self.operation)]
        if chem.uses_charge(self.operation):
            parts.append(f"chg {self.charge_current:g} A")
        if chem.uses_discharge(self.operation):
            parts.append(f"dchg {self.discharge_current:g} A")
        if self.operation == "storage":
            mv = self.storage_voltage or chem.STORAGE_MV.get(self.battery_type)
            if mv:
                parts.append(f"to {mv / 1000:.2f} V")
        if self.charge_voltage and self.charge_voltage != chem.MAX_CHARGE_MV[self.battery_type]:
            parts.append(f"to {self.charge_voltage / 1000:.2f} V")
        if self.operation == "cycle":
            parts.append(f"{chem.CYCLE_SHORT.get(self.cycle_mode, '?')} ×{self.cycle_count}")
        parts.append(f"{self.capacity} mAh")
        return " · ".join(parts)

    def service_data(self, slot: int, config_entry_id: str | None = None) -> dict[str, Any]:
        """Data for the skyrc_mc5000.start_program service."""
        data: dict[str, Any] = {
            "slot": str(slot),
            "battery_type": self.battery_type,
            "operation": self.operation,
            "capacity": int(self.capacity),
            "charge_current": round(float(self.charge_current), 2),
            "discharge_current": round(float(self.discharge_current), 2),
            "cycle_count": int(self.cycle_count),
        }
        if self.operation == "storage":
            data["storage_voltage"] = int(
                self.storage_voltage or chem.STORAGE_MV[self.battery_type]
            )
        if self.discharge_voltage is not None:
            data["discharge_voltage"] = int(self.discharge_voltage)
        if self.charge_voltage is not None:
            data["charge_voltage"] = int(self.charge_voltage)
        data.update({
            "charge_end_current": int(self.charge_end_current),
            "discharge_end_current": int(self.discharge_end_current),
            "rest_time": int(self.rest_time),
            "delta_v": int(self.delta_v),
            "trickle_current": int(self.trickle_current),
            "protection_temperature": int(self.protection_temperature),
            "protection_time": int(self.protection_time),
        })
        # Only sent when changed, so integration versions before 0.2.4 still work.
        if self.operation == "cycle" and self.cycle_mode != "charge_discharge":
            data["cycle_mode"] = self.cycle_mode
        if self.restart_voltage:
            data["restart_voltage"] = int(self.restart_voltage)
        if config_entry_id:
            data["config_entry_id"] = config_entry_id
        return data

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Preset":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})


DEFAULT_PRESETS: list[Preset] = [
    Preset("Eneloop charge", "eneloop", "charge", 2000, charge_current=1.0),
    Preset("NiMH charge", "nimh", "charge", 2000, charge_current=0.5),
    Preset("Li-ion charge", "liion", "charge", 5000, charge_current=1.0,
           note="18650 / 21700, to 4.20 V"),
    Preset("Li-ion storage", "liion", "storage", 5000, charge_current=1.0,
           discharge_current=1.0, storage_voltage=3800),
    Preset("NiMH capacity test", "nimh", "cycle", 2000, charge_current=0.5,
           discharge_current=0.5, cycle_count=2, cycle_mode="discharge_charge",
           note="Discharge, charge, measure, charge: ends charged"),
    Preset("Li-ion capacity test", "liion", "cycle", 5000, charge_current=1.0,
           discharge_current=1.0, note="Charges, then discharges: ends empty (run Li-ion storage after)"),
    Preset("Discharge test (EBL 1.5 V AA)", "nimh", "discharge", 3000,
           discharge_current=0.5, discharge_voltage=1000,
           note="Discharge only: these cells charge over USB"),
]


@dataclass
class PresetStore:
    path: Path
    presets: list[Preset] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "PresetStore":
        store = cls(path)
        if path.exists():
            raw = json.loads(path.read_text())
            store.presets = [Preset.from_dict(p) for p in raw.get("presets", [])]
        else:
            store.presets = [Preset(**asdict(p)) for p in DEFAULT_PRESETS]
            store.save()
        return store

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"presets": [asdict(p) for p in self.presets]}, indent=2) + "\n"
        )
        tmp.replace(self.path)

    def names(self) -> list[str]:
        return [p.name for p in self.presets]

    def get(self, name: str) -> Preset | None:
        return next((p for p in self.presets if p.name == name), None)
