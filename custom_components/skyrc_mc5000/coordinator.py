"""Polling coordinator for the SkyRC MC5000."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import replace
from datetime import timedelta

from bleak.exc import BleakError
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from skyrc_ble import (
    Mc5000,
    Mc5000DeviceInfo,
    Mc5000Program,
    Mc5000Settings,
    Mc5000State,
    Mc5000UsbStatus,
)

from .const import CONF_ADDRESS, DOMAIN, SLOTS, UPDATE_INTERVAL_SECONDS
from .program import SlotProgram

_LOGGER = logging.getLogger(__name__)

# Settings only change from the charger's menu or this integration, so a
# slow re-read is enough to pick up changes made on the charger.
SETTINGS_REFRESH_S = 300


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
        # System settings, USB-C port and firmware, read alongside the slots.
        self.settings: Mc5000Settings | None = None
        self.usb: Mc5000UsbStatus | None = None
        self.device_info: Mc5000DeviceInfo | None = None
        self._settings_read = 0.0
        # How each slot's last run ended; restored by the sensors after a restart.
        self.last_results: dict[int, dict] = {}
        self._runs: dict[int, dict] = {}

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
                await self._async_read_extras(charger)
                self._track_runs(charger.state)
                if not charger.is_connected:
                    # The library swallows errors once the link has dropped;
                    # don't report the previous readings as current.
                    raise UpdateFailed(f"Lost the connection to MC5000 {self.address}")
            except HomeAssistantError as err:
                raise UpdateFailed(str(err)) from err
            except (BleakError, TimeoutError) as err:
                raise UpdateFailed(f"Error talking to MC5000: {err}") from err
            return charger.state

    async def _async_read_extras(self, charger: Mc5000) -> None:
        """USB-C every poll; settings every few minutes; firmware once."""
        self.usb = await charger.get_usb_status()
        if self.settings is None or time.monotonic() - self._settings_read > SETTINGS_REFRESH_S:
            settings = await charger.get_settings()
            if settings is not None:
                self.settings = settings
                self._settings_read = time.monotonic()
        if self.device_info is None:
            info = await charger.get_device_info()
            if info is not None:
                self.device_info = info
                registry = dr.async_get(self.hass)
                for device in dr.async_entries_for_config_entry(
                    registry, self.config_entry.entry_id
                ):
                    if (DOMAIN, self.address) in device.identifiers:
                        registry.async_update_device(
                            device.id, sw_version=info.firmware, hw_version=info.hardware
                        )

    def _track_runs(self, state: Mc5000State) -> None:
        """Keep the final numbers of each run.

        The charger zeroes the mAh counter when a run ends in an error, so the
        last non-zero reading seen during the run is what gets kept.
        """
        from homeassistant.util import dt as dt_util
        from skyrc_ble.models import Mc5000ChannelStatus, Mc5000Error

        active = {Mc5000ChannelStatus.CHARGE, Mc5000ChannelStatus.DISCHARGE,
                  Mc5000ChannelStatus.REST}
        done = {Mc5000ChannelStatus.DONE, Mc5000ChannelStatus.DONE_ALT}
        for slot in SLOTS:
            ch = state.channels[slot - 1]
            if ch is None:
                continue
            run = self._runs.get(slot)
            if ch.status in active:
                if run is None:
                    run = self._runs[slot] = {"started": dt_util.utcnow().isoformat()}
                run["battery_type"] = ch.type.name.lower()
                if ch.operation is not None:
                    run["operation"] = ch.operation.name.lower()
                if ch.capacity:
                    run["capacity"] = ch.capacity
                if ch.time:
                    run["elapsed"] = ch.time
                continue
            if run is None:
                continue
            del self._runs[slot]
            if ch.error not in (Mc5000Error.NONE,):
                outcome = ch.error.name.lower().removesuffix("_alt")
            elif ch.status in done:
                outcome = "done"
            else:
                outcome = "stopped"
            self.last_results[slot] = {
                "outcome": outcome,
                "capacity": ch.capacity or run.get("capacity"),
                "elapsed": ch.time or run.get("elapsed"),
                "battery_type": run.get("battery_type"),
                "operation": run.get("operation"),
                "started": run["started"],
                "ended": dt_util.utcnow().isoformat(),
            }

    async def async_set_settings(self, **changes) -> None:
        """Change system settings (read-modify-write) and confirm by reading back."""
        result: list[Mc5000Settings | None] = []

        async def go(charger: Mc5000) -> None:
            current = await charger.get_settings() or self.settings
            if current is None:
                raise HomeAssistantError("Couldn't read the MC5000's settings")
            if not await charger.set_settings(replace(current, **changes)):
                raise HomeAssistantError("The MC5000 didn't accept the settings")
            result.append(await charger.get_settings())

        await self._async_command(go)
        if result and result[0] is not None:
            self.settings = result[0]
            self._settings_read = time.monotonic()
        self.async_update_listeners()

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

    async def async_get_history(self, slot: int):
        """The voltage curve the charger keeps for a slot's current or last finished run.

        None when the charger doesn't answer: empty slot, never run, or stopped by hand.
        """
        result: list = []

        async def go(charger: Mc5000) -> None:
            result.append(await charger.get_history(slot - 1))

        await self._async_command(go)
        return result[0] if result else None

    async def async_send_raw(self, command: int, payload: bytes) -> bytes | None:
        """Send one raw frame and return the charger's reply (None on timeout).

        For testing commands that have no entity yet; see the send_command service.
        """
        result: list[bytes | None] = []

        async def go(charger) -> None:
            result.append(await charger._request(command, payload))  # noqa: SLF001

        await self._async_command(go)
        return result[0] if result else None

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
