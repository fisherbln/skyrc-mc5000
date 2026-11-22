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
        if self.operation == "cycle" and self.cycle_count > 1:
            parts.append(f"×{self.cycle_count}")
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
           discharge_current=0.5, note="Charges, then discharges: ends empty"),
    Preset("Li-ion capacity test", "liion", "cycle", 5000, charge_current=1.0,
           discharge_current=1.0, note="Charges, then discharges: ends empty"),
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
