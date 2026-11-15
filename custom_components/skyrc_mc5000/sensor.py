"""Per-slot sensors for the SkyRC MC5000."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from skyrc_ble import Mc5000ChannelData
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
