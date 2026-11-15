"""Program currents and capacity for each slot."""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntityDescription,
    NumberMode,
    RestoreNumber,
)
from homeassistant.const import UnitOfElectricCurrent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import Mc5000ConfigEntry
from .const import SLOTS
from .entity import Mc5000Entity


@dataclass(frozen=True, kw_only=True)
class Mc5000NumberDescription(NumberEntityDescription):
    attr: str


NUMBERS: tuple[Mc5000NumberDescription, ...] = (
    Mc5000NumberDescription(
        key="charge_current",
        translation_key="program_charge_current",
        attr="charge_current",
        device_class=NumberDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        native_min_value=0.05,
        native_max_value=3.0,
        native_step=0.05,
        mode=NumberMode.BOX,
    ),
    Mc5000NumberDescription(
        key="discharge_current",
        translation_key="program_discharge_current",
        attr="discharge_current",
        device_class=NumberDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        native_min_value=0.05,
        native_max_value=2.0,
        native_step=0.05,
        mode=NumberMode.BOX,
    ),
    Mc5000NumberDescription(
        key="capacity",
        translation_key="program_capacity",
        attr="capacity",
        native_unit_of_measurement="mAh",
        native_min_value=100,
        native_max_value=10000,
        native_step=50,
        mode=NumberMode.BOX,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Mc5000ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        Mc5000ProgramNumber(coordinator, slot, description)
        for slot in SLOTS
        for description in NUMBERS
    )


class Mc5000ProgramNumber(Mc5000Entity, RestoreNumber):
    entity_description: Mc5000NumberDescription

    def __init__(self, coordinator, slot: int, description: Mc5000NumberDescription):
        super().__init__(coordinator, f"slot{slot}_program_{description.key}")
        self.entity_description = description
        self._slot = slot
        self._attr_translation_placeholders = {"slot": str(slot)}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            await self.async_set_native_value(last.native_value)

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> float:
        return getattr(self.coordinator.programs[self._slot], self.entity_description.attr)

    async def async_set_native_value(self, value: float) -> None:
        if self.entity_description.attr == "capacity":
            value = int(value)
        else:
            value = round(value, 3)
        setattr(self.coordinator.programs[self._slot], self.entity_description.attr, value)
        if self.hass is not None and self.entity_id:
            self.async_write_ha_state()
