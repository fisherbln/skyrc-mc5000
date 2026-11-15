"""Polling coordinator for the SkyRC MC5000."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from bleak.exc import BleakError
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from skyrc_ble import Mc5000, Mc5000Program, Mc5000State

from .const import CONF_ADDRESS, DOMAIN, SLOTS, UPDATE_INTERVAL_SECONDS
from .program import SlotProgram

_LOGGER = logging.getLogger(__name__)


class Mc5000Coordinator(DataUpdateCoordinator[Mc5000State]):
    """Keeps a BLE connection to the charger and polls all four slots."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.data[CONF_ADDRESS]}",
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )
        self.address: str = entry.data[CONF_ADDRESS]
        self.charger: Mc5000 | None = None
        self.paused = False
        self._lock = asyncio.Lock()
        self.programs: dict[int, SlotProgram] = {slot: SlotProgram() for slot in SLOTS}

    async def _async_connected_charger(self) -> Mc5000:
        """Return a connected charger, connecting through HA Bluetooth if needed."""
        if self.charger is not None and self.charger.is_connected:
            # A connected charger stops advertising, so HA's Bluetooth manager
            # forgets it after a few minutes; only look it up to reconnect.
            return self.charger
        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if ble_device is None:
            raise HomeAssistantError(
                f"MC5000 {self.address} is not in range of a connectable Bluetooth "
                "adapter or proxy"
            )
        if self.charger is None:
            self.charger = Mc5000(ble_device)
        else:
            self.charger.set_ble_device(ble_device)
        if not self.charger.is_connected and not await self.charger.connect():
            raise HomeAssistantError(f"Could not connect to MC5000 {self.address}")
        return self.charger

    async def _async_update_data(self) -> Mc5000State:
        if self.paused:
            return self.data if self.data is not None else Mc5000State()
        async with self._lock:
            try:
                charger = await self._async_connected_charger()
                await charger.update()
                if not charger.is_connected:
                    # The library swallows errors once the link has dropped;
                    # don't report the previous readings as current.
                    raise UpdateFailed(f"Lost the connection to MC5000 {self.address}")
            except HomeAssistantError as err:
                raise UpdateFailed(str(err)) from err
            except (BleakError, TimeoutError) as err:
                raise UpdateFailed(f"Error talking to MC5000: {err}") from err
            return charger.state

    async def async_start_program(self, channels: int, program: Mc5000Program) -> None:
        """Send a program to the channels in the bitmask and start it."""
        await self._async_command(lambda c: c.start_program_multi(channels, program))

    async def async_start_slot(self, slot: int) -> None:
        """Start the program set up for one slot with the select/number entities."""
        program = self.programs[slot].build()
        try:
            from skyrc_ble.mc5000 import build_program_payload

            build_program_payload(0, program)
        except ValueError as err:
            raise HomeAssistantError(f"Invalid program for slot {slot}: {err}") from err
        await self.async_start_program(1 << (slot - 1), program)

    async def async_stop(self, channels: int) -> None:
        """Stop the channels in the bitmask (0 = all)."""
        await self._async_command(lambda c: c.stop_charge_multi(channels))

    async def _async_command(self, command) -> None:
        if self.paused:
            raise HomeAssistantError(
                "The MC5000 connection is released for the SkyRC app; turn the "
                "Connection switch back on first"
            )
        async with self._lock:
            try:
                charger = await self._async_connected_charger()
                await command(charger)
            except (BleakError, TimeoutError) as err:
                raise HomeAssistantError(f"MC5000 command failed: {err}") from err
        await self.async_request_refresh()

    async def async_set_paused(self, paused: bool) -> None:
        """Release (or retake) the BLE connection so the SkyRC app can connect."""
        self.paused = paused
        if paused:
            async with self._lock:
                if self.charger is not None and self.charger.is_connected:
                    await self.charger.disconnect()
            self.async_update_listeners()
        else:
            await self.async_request_refresh()

    async def async_shutdown(self) -> None:
        await super().async_shutdown()
        if self.charger is not None and self.charger.is_connected:
            await self.charger.disconnect()
