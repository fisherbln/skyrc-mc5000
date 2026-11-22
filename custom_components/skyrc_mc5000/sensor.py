"""Per-slot sensors for the SkyRC MC5000."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from skyrc_ble import Mc5000ChannelData, Mc5000UsbStatus
from skyrc_ble.models import (
    Mc5000BatteryType,
    Mc5000ChannelStatus,
    Mc5000Error,
    Mc5000Operation,
)

from . import Mc5000ConfigEntry
from .const import SLOTS
from .entity import Mc5000Entity

STATUS_OPTIONS = ["standby", "ready", "charging", "discharging", "resting", "done"]
_STATUS = {
    Mc5000ChannelStatus.STANDBY: "standby",
    Mc5000ChannelStatus.READY: "ready",
    Mc5000ChannelStatus.CHARGE: "charging",
    Mc5000ChannelStatus.DISCHARGE: "discharging",
    Mc5000ChannelStatus.REST: "resting",
    Mc5000ChannelStatus.DONE: "done",
    Mc5000ChannelStatus.DONE_ALT: "done",
}
OPERATION_OPTIONS = [op.name.lower() for op in Mc5000Operation]
CHEMISTRY_OPTIONS = [bt.name.lower() for bt in Mc5000BatteryType]
ERROR_OPTIONS = sorted({e.name.lower().removesuffix("_alt") for e in Mc5000Error})


@dataclass(frozen=True, kw_only=True)
class Mc5000SensorDescription(SensorEntityDescription):
    value_fn: Callable[[Mc5000ChannelData], Any]


SENSORS: tuple[Mc5000SensorDescription, ...] = (
    Mc5000SensorDescription(
        key="status",
        translation_key="status",
        device_class=SensorDeviceClass.ENUM,
        options=STATUS_OPTIONS,
        value_fn=lambda d: _STATUS.get(d.status),
    ),
    Mc5000SensorDescription(
        key="voltage",
        translation_key="voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=3,
        value_fn=lambda d: d.voltage,
    ),
    Mc5000SensorDescription(
        key="current",
        translation_key="current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=3,
        value_fn=lambda d: d.current,
    ),
    Mc5000SensorDescription(
        key="capacity",
        translation_key="capacity",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="mAh",
        value_fn=lambda d: d.capacity,
    ),
    Mc5000SensorDescription(
        key="elapsed",
        translation_key="elapsed",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=0,
        value_fn=lambda d: d.time,
    ),
    Mc5000SensorDescription(
        key="resistance",
        translation_key="resistance",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="mΩ",
        # 0, 1 and 65535 mean "not measured" (no cell, or a regulated 1.5 V cell)
        value_fn=lambda d: None if d.resistance in (0, 1, 65535) else d.resistance,
    ),
    Mc5000SensorDescription(
        key="operation",
        translation_key="operation",
        device_class=SensorDeviceClass.ENUM,
        options=OPERATION_OPTIONS,
        value_fn=lambda d: d.operation.name.lower() if d.operation is not None else None,
    ),
    Mc5000SensorDescription(
        key="chemistry",
        translation_key="chemistry",
        device_class=SensorDeviceClass.ENUM,
        options=CHEMISTRY_OPTIONS,
        value_fn=lambda d: d.type.name.lower(),
    ),
    Mc5000SensorDescription(
        key="error",
        translation_key="error",
        device_class=SensorDeviceClass.ENUM,
        options=ERROR_OPTIONS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.error.name.lower().removesuffix("_alt"),
    ),
    Mc5000SensorDescription(
        key="temperature",
        translation_key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.temperature,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Mc5000ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        Mc5000SlotSensor(coordinator, slot, description)
        for slot in SLOTS
        for description in SENSORS
    )
    async_add_entities(Mc5000UsbSensor(coordinator, d) for d in USB_SENSORS)
    async_add_entities(Mc5000LastResultSensor(coordinator, slot) for slot in SLOTS)


@dataclass(frozen=True, kw_only=True)
class Mc5000UsbDescription(SensorEntityDescription):
    value_fn: Callable[[Mc5000UsbStatus], Any]


USB_SENSORS: tuple[Mc5000UsbDescription, ...] = (
    Mc5000UsbDescription(
        key="usb_voltage", translation_key="usb_voltage",
        device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=2, value_fn=lambda u: u.voltage,
    ),
    Mc5000UsbDescription(
        key="usb_current", translation_key="usb_current",
        device_class=SensorDeviceClass.CURRENT, state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=2, value_fn=lambda u: u.current,
    ),
    Mc5000UsbDescription(
        key="usb_power", translation_key="usb_power",
        device_class=SensorDeviceClass.POWER, state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=1, value_fn=lambda u: u.power,
    ),
    Mc5000UsbDescription(
        # The charger restarts this counter for each device plugged in.
        key="usb_energy", translation_key="usb_energy",
        device_class=SensorDeviceClass.ENERGY, state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        suggested_display_precision=2, value_fn=lambda u: u.energy,
    ),
    Mc5000UsbDescription(
        key="usb_runtime", translation_key="usb_runtime",
        device_class=SensorDeviceClass.DURATION, state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.SECONDS, suggested_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=lambda u: u.runtime,
    ),
)


class Mc5000UsbSensor(Mc5000Entity, SensorEntity):
    """The charger's USB-C PD port."""

    entity_description: Mc5000UsbDescription

    def __init__(self, coordinator, description: Mc5000UsbDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.usb is not None

    @property
    def native_value(self):
        usb = self.coordinator.usb
        return None if usb is None else self.entity_description.value_fn(usb)


class Mc5000SlotSensor(Mc5000Entity, SensorEntity):
    entity_description: Mc5000SensorDescription

    def __init__(self, coordinator, slot: int, description: Mc5000SensorDescription):
        super().__init__(coordinator, f"slot{slot}_{description.key}")
        self.entity_description = description
        self._slot = slot
        self._attr_translation_placeholders = {"slot": str(slot)}

    def _channel(self) -> Mc5000ChannelData | None:
        data = self.coordinator.data
        if data is None:
            return None
        return data.channels[self._slot - 1]

    @property
    def available(self) -> bool:
        return super().available and self._channel() is not None

    @property
    def native_value(self):
        channel = self._channel()
        return None if channel is None else self.entity_description.value_fn(channel)


RESULT_OPTIONS = ["done", "stopped", *[e for e in ERROR_OPTIONS if e != "none"]]


class Mc5000LastResultSensor(Mc5000Entity, RestoreSensor):
    """How the slot's last run ended, with its final mAh and time as attributes."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = RESULT_OPTIONS
    _attr_translation_key = "last_result"

    def __init__(self, coordinator, slot: int) -> None:
        super().__init__(coordinator, f"slot{slot}_last_result")
        self._slot = slot
        self._attr_translation_placeholders = {"slot": str(slot)}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._slot in self.coordinator.last_results:
            return
        last = await self.async_get_last_state()
        if last is not None and last.state in RESULT_OPTIONS:
            self.coordinator.last_results[self._slot] = {
                "outcome": last.state,
                **{k: last.attributes.get(k) for k in
                   ("capacity", "elapsed", "battery_type", "operation", "started", "ended")},
            }

    @property
    def available(self) -> bool:
        return True  # a past result stays true while the charger is offline

    @property
    def native_value(self) -> str | None:
        result = self.coordinator.last_results.get(self._slot)
        return result["outcome"] if result else None

    @property
    def extra_state_attributes(self) -> dict | None:
        result = self.coordinator.last_results.get(self._slot)
        if not result:
            return None
        return {k: v for k, v in result.items() if k != "outcome"}
