"""Base entity for the SkyRC MC5000."""
from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import Mc5000Coordinator


class Mc5000Entity(CoordinatorEntity[Mc5000Coordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: Mc5000Coordinator, key: str) -> None:
        super().__init__(coordinator)
        address = coordinator.address
        self._attr_unique_id = f"{address}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            connections={(CONNECTION_BLUETOOTH, address)},
            manufacturer="SkyRC",
            model="MC5000",
            name=coordinator.config_entry.title,
        )

    @property
    def available(self) -> bool:
        return super().available and not self.coordinator.paused
