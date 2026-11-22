"""Firmware update check against SkyRC's update server.

This only tells you a newer firmware exists. Installing it stays in the SkyRC
app: a failed update over Bluetooth can leave the charger unusable.
"""
from __future__ import annotations

import logging
from datetime import timedelta

import aiohttp
from homeassistant.components.update import UpdateEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import Mc5000ConfigEntry
from .entity import Mc5000Entity

_LOGGER = logging.getLogger(__name__)

# The address the SkyRC app asks; it answers a JSON list with the newest
# firmware for the serial code ([] when there is nothing newer).
UPDATE_URL = "http://upgrade.skyrc.com/"
SCAN_INTERVAL = timedelta(hours=12)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Mc5000ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([Mc5000FirmwareUpdate(entry.runtime_data)], update_before_add=False)


def _version_key(version: str) -> float:
    try:
        return float(version)
    except (TypeError, ValueError):
        return 0.0


class Mc5000FirmwareUpdate(Mc5000Entity, UpdateEntity):
    _attr_translation_key = "firmware"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_release_url = "https://www.skyrc.com/"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "firmware")
        self._latest: str | None = None
        self._summary: str | None = None
        self._first_check = False

    @property
    def should_poll(self) -> bool:
        return True  # the server check runs on SCAN_INTERVAL, not the BLE poll

    @property
    def available(self) -> bool:
        return self.coordinator.device_info is not None

    @property
    def installed_version(self) -> str | None:
        info = self.coordinator.device_info
        return info.firmware if info else None

    @property
    def latest_version(self) -> str | None:
        installed = self.installed_version
        if self._latest and installed and _version_key(self._latest) > _version_key(installed):
            return self._latest
        return installed

    @property
    def release_summary(self) -> str | None:
        return self._summary if self.latest_version != self.installed_version else None

    def version_is_newer(self, latest_version: str, installed_version: str) -> bool:
        return _version_key(latest_version) > _version_key(installed_version)

    def _handle_coordinator_update(self) -> None:
        # Check once as soon as the serial code is known; after that every
        # SCAN_INTERVAL (a failed check just waits for the next one).
        info = self.coordinator.device_info
        if not self._first_check and info and info.serial_code:
            self._first_check = True
            self.hass.async_create_task(self.async_update_ha_state(force_refresh=True))
        super()._handle_coordinator_update()

    async def async_update(self) -> None:
        info = self.coordinator.device_info
        if info is None or not info.serial_code:
            return
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                UPDATE_URL,
                params={"SN": info.serial_code},
                headers={"SN": info.serial_code},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            _LOGGER.debug("SkyRC update check failed: %s", err)
            return
        if not isinstance(data, list) or not data:
            self._latest, self._summary = None, None
            return
        newest = data[0] if isinstance(data[0], dict) else {}
        self._latest = str(newest.get("version") or "") or None
        memo = newest.get("memo") or {}
        summary = memo.get("en") if isinstance(memo, dict) else None
        self._summary = summary[:255] if summary else None
