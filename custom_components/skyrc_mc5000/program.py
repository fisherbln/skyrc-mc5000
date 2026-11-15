"""Per-slot program settings held by the coordinator."""
from __future__ import annotations

from dataclasses import dataclass

from skyrc_ble import Mc5000BatteryType, Mc5000Operation, Mc5000Program
from skyrc_ble.models import MC5000_MAX_CHARGE_VOLTAGE, mc5000_operation_code

# App default discharge cutoff per chemistry, in mV.
DEFAULT_DISCHARGE_VOLTAGE = {
    Mc5000BatteryType.LIION: 3200,
    Mc5000BatteryType.LIION_HV: 3400,
    Mc5000BatteryType.LIFEPO4: 2900,
    Mc5000BatteryType.NIMH: 900,
    Mc5000BatteryType.NICD: 900,
    Mc5000BatteryType.ENELOOP: 900,
    Mc5000BatteryType.NIZN: 1100,
    Mc5000BatteryType.RAM: 900,
    Mc5000BatteryType.LTO: 1800,
    Mc5000BatteryType.NAION: 2000,
}


def operations_for(battery_type: Mc5000BatteryType) -> list[Mc5000Operation]:
    """Operations the charger offers for a chemistry, in menu order."""
    result = []
    for operation in Mc5000Operation:
        try:
            mc5000_operation_code(battery_type, operation)
        except ValueError:
            continue
        result.append(operation)
    return result


@dataclass
class SlotProgram:
    """Program settings for one slot (what Start will send)."""

    battery_type: Mc5000BatteryType = Mc5000BatteryType.NIMH
    operation: Mc5000Operation = Mc5000Operation.CHARGE
    charge_current: float = 0.5
    discharge_current: float = 0.5
    capacity: int = 2000

    def set_battery_type(self, battery_type: Mc5000BatteryType) -> None:
        self.battery_type = battery_type
        if self.operation not in operations_for(battery_type):
            self.operation = Mc5000Operation.CHARGE

    def build(self) -> Mc5000Program:
        return Mc5000Program(
            battery_type=self.battery_type,
            operation=self.operation,
            capacity=self.capacity,
            charge_current=round(self.charge_current, 3),
            discharge_current=round(self.discharge_current, 3),
            charge_voltage=MC5000_MAX_CHARGE_VOLTAGE[self.battery_type],
            discharge_voltage=DEFAULT_DISCHARGE_VOLTAGE[self.battery_type],
        )
