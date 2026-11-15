"""Connection switch: release the BLE link so the SkyRC app can connect."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import Mc5000ConfigEntry
from .entity import Mc5000Entity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Mc5000ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([Mc5000ConnectionSwitch(entry.runtime_data)])


class Mc5000ConnectionSwitch(Mc5000Entity, SwitchEntity):
    """On: Home Assistant holds the connection. Off: released for the SkyRC app."""

    _attr_translation_key = "connection"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "connection")

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return not self.coordinator.paused

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_paused(False)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_paused(True)
        self.async_write_ha_state()
