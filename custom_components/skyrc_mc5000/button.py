"""Stop buttons for the SkyRC MC5000."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import Mc5000ConfigEntry
from .const import SLOTS
from .entity import Mc5000Entity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Mc5000ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[ButtonEntity] = [Mc5000StopButton(coordinator, slot) for slot in SLOTS]
    entities.append(Mc5000StopButton(coordinator, None))
    async_add_entities(entities)


class Mc5000StopButton(Mc5000Entity, ButtonEntity):
    def __init__(self, coordinator, slot: int | None) -> None:
        super().__init__(coordinator, f"slot{slot}_stop" if slot else "stop_all")
        self._mask = 1 << (slot - 1) if slot else 0
        if slot:
            self._attr_translation_key = "stop_slot"
            self._attr_translation_placeholders = {"slot": str(slot)}
        else:
            self._attr_translation_key = "stop_all"

    async def async_press(self) -> None:
        await self.coordinator.async_stop(self._mask)
