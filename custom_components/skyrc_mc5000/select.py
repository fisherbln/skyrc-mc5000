"""Program chemistry and operation pickers for each slot."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from skyrc_ble import Mc5000BatteryType, Mc5000Operation

from . import Mc5000ConfigEntry
from .const import SLOTS
from .entity import Mc5000Entity
from .program import operations_for


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Mc5000ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[SelectEntity] = []
    for slot in SLOTS:
        entities.append(Mc5000ChemistrySelect(coordinator, slot))
        entities.append(Mc5000OperationSelect(coordinator, slot))
    async_add_entities(entities)


class _ProgramSelect(Mc5000Entity, SelectEntity, RestoreEntity):
    """Settings live in Home Assistant, so they stay editable while offline."""

    def __init__(self, coordinator, slot: int, key: str) -> None:
        super().__init__(coordinator, f"slot{slot}_program_{key}")
        self._slot = slot
        self._attr_translation_key = f"program_{key}"
        self._attr_translation_placeholders = {"slot": str(slot)}

    @property
    def available(self) -> bool:
        return True

    @property
    def program(self):
        return self.coordinator.programs[self._slot]


class Mc5000ChemistrySelect(_ProgramSelect):
    _attr_options = [b.name.lower() for b in Mc5000BatteryType]

    def __init__(self, coordinator, slot: int) -> None:
        super().__init__(coordinator, slot, "chemistry")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in self._attr_options:
            self.program.set_battery_type(Mc5000BatteryType[last.state.upper()])

    @property
    def current_option(self) -> str:
        return self.program.battery_type.name.lower()

    async def async_select_option(self, option: str) -> None:
        self.program.set_battery_type(Mc5000BatteryType[option.upper()])
        # The operation list depends on the chemistry, so refresh every entity.
        self.coordinator.async_update_listeners()


class Mc5000OperationSelect(_ProgramSelect):
    def __init__(self, coordinator, slot: int) -> None:
        super().__init__(coordinator, slot, "operation")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in self.options:
            self.program.operation = Mc5000Operation[last.state.upper()]

    @property
    def options(self) -> list[str]:
        return [op.name.lower() for op in operations_for(self.program.battery_type)]

    @property
    def current_option(self) -> str:
        return self.program.operation.name.lower()

    async def async_select_option(self, option: str) -> None:
        self.program.operation = Mc5000Operation[option.upper()]
        self.async_write_ha_state()
