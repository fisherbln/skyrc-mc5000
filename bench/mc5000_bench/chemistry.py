"""What the MC5000 offers per battery chemistry.

Mirrors the skyrc_mc5000 integration (and the skyrc-ble library) so presets
can be checked here before they are sent to Home Assistant.
"""
from __future__ import annotations

# Chemistry key (as the integration's start_program service names it) -> label.
CHEMISTRIES: dict[str, str] = {
    "liion": "Li-ion",
    "liion_hv": "Li-ion HV (4.35 V)",
    "lifepo4": "LiFePO4",
    "nimh": "NiMH",
    "nicd": "NiCd",
    "eneloop": "Eneloop",
    "nizn": "NiZn",
    "ram": "RAM",
    "lto": "LTO",
    "naion": "Na-ion",
}

OPERATION_LABELS: dict[str, str] = {
    "charge": "Charge",
    "storage": "Storage",
    "discharge": "Discharge",
    "cycle": "Cycle",
    "refresh": "Refresh",
    "breakin": "Break-in",
}

_LI_OPS = ["charge", "storage", "discharge", "cycle"]
_NI_OPS = ["charge", "discharge", "cycle", "refresh", "breakin"]
_BASIC_OPS = ["charge", "discharge", "cycle"]

OPERATIONS: dict[str, list[str]] = {
    "liion": _LI_OPS,
    "liion_hv": _LI_OPS,
    "lifepo4": _LI_OPS,
    "lto": _LI_OPS,
    "naion": _LI_OPS,
    "nimh": _NI_OPS,
    "nicd": _NI_OPS,
    "eneloop": _NI_OPS,
    "nizn": _BASIC_OPS,
    "ram": _BASIC_OPS,
}

# Full-charge voltage per chemistry (mV); the integration refuses higher.
MAX_CHARGE_MV: dict[str, int] = {
    "liion": 4200,
    "liion_hv": 4350,
    "lifepo4": 3650,
    "nimh": 1650,
    "nicd": 1650,
    "eneloop": 1650,
    "nizn": 1900,
    "ram": 1650,
    "lto": 2850,
    "naion": 4000,
}

# Charger default discharge cutoff (mV).
DISCHARGE_MV: dict[str, int] = {
    "liion": 3200,
    "liion_hv": 3400,
    "lifepo4": 2900,
    "nimh": 900,
    "nicd": 900,
    "eneloop": 900,
    "nizn": 1100,
    "ram": 900,
    "lto": 1800,
    "naion": 2000,
}

# Charger default storage voltage (mV), used by the storage operation.
STORAGE_MV: dict[str, int] = {
    "liion": 3800,
    "liion_hv": 3900,
    "lifepo4": 3300,
    "lto": 2400,
    "naion": 3500,
}

# Limits the integration's start_program service accepts.
CHARGE_A = (0.05, 3.0)
DISCHARGE_A = (0.05, 2.0)
CAPACITY_MAH = (100, 20000)

ACTIVE_STATUSES = {"charging", "discharging", "resting"}

STATUS_LABELS: dict[str, str] = {
    "standby": "Idle",
    "ready": "Ready",
    "charging": "Charging",
    "discharging": "Discharging",
    "resting": "Resting",
    "done": "Done",
}


def uses_charge(operation: str) -> bool:
    return operation != "discharge"


def uses_discharge(operation: str) -> bool:
    return operation != "charge"


def label(chemistry: str | None) -> str:
    return CHEMISTRIES.get(chemistry or "", chemistry or "?")


def operation_label(operation: str | None) -> str:
    return OPERATION_LABELS.get(operation or "", operation or "?")
