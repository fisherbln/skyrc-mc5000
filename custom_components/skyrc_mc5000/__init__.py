"""SkyRC MC5000 charger/analyzer over Bluetooth LE."""
from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
from skyrc_ble import Mc5000BatteryType, Mc5000Operation, Mc5000Program
from skyrc_ble.models import MC5000_MAX_CHARGE_VOLTAGE

from .const import DOMAIN
from .coordinator import Mc5000Coordinator
from .program import DEFAULT_DISCHARGE_VOLTAGE

PLATFORMS = [
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type Mc5000ConfigEntry = ConfigEntry[Mc5000Coordinator]

SERVICE_START_PROGRAM = "start_program"


_current = vol.All(vol.Coerce(float), vol.Range(min=0, max=3.0))
_mv = vol.All(vol.Coerce(int), vol.Range(min=500, max=4400))
_ma = vol.All(vol.Coerce(int), vol.Range(min=0, max=3000))

START_PROGRAM_SCHEMA = vol.Schema(
    {
        vol.Optional("config_entry_id"): cv.string,
        vol.Required("slot"): vol.In(["1", "2", "3", "4", "all"]),
        vol.Required("battery_type"): vol.In([b.name.lower() for b in Mc5000BatteryType]),
        vol.Required("operation"): vol.In([o.name.lower() for o in Mc5000Operation]),
        vol.Required("capacity"): vol.All(vol.Coerce(int), vol.Range(min=100, max=65535)),
        vol.Required("charge_current"): _current,
        vol.Optional("discharge_current", default=0.5): _current,
        vol.Optional("charge_voltage"): _mv,
        vol.Optional("discharge_voltage"): _mv,
        vol.Optional("storage_voltage"): _mv,
        vol.Optional("charge_end_current", default=100): _ma,
        vol.Optional("discharge_end_current", default=100): _ma,
        vol.Optional("rest_time", default=10): vol.All(vol.Coerce(int), vol.Range(min=0, max=999)),
        vol.Optional("cycle_count", default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=99)),
        vol.Optional("delta_v", default=6): vol.All(vol.Coerce(int), vol.Range(min=0, max=20)),
        vol.Optional("trickle_current", default=0): vol.All(vol.Coerce(int), vol.Range(min=0, max=300)),
        vol.Optional("protection_temperature", default=60): vol.All(vol.Coerce(int), vol.Range(min=20, max=71)),
        vol.Optional("protection_time", default=0): vol.All(vol.Coerce(int), vol.Range(min=0, max=65535)),
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's services."""

    async def async_start_program(call: ServiceCall) -> None:
        coordinator = _coordinator_for_call(hass, call)
        battery_type = Mc5000BatteryType[call.data["battery_type"].upper()]
        operation = Mc5000Operation[call.data["operation"].upper()]
        try:
            program = Mc5000Program(
                battery_type=battery_type,
                operation=operation,
                capacity=call.data["capacity"],
                charge_current=call.data["charge_current"],
                discharge_current=call.data["discharge_current"],
                charge_voltage=call.data.get(
                    "charge_voltage", MC5000_MAX_CHARGE_VOLTAGE[battery_type]
                ),
                discharge_voltage=call.data.get(
                    "discharge_voltage", DEFAULT_DISCHARGE_VOLTAGE[battery_type]
                ),
                storage_voltage=call.data.get("storage_voltage", 0),
                charge_end_current=call.data["charge_end_current"],
                discharge_end_current=call.data["discharge_end_current"],
                charge_rest_time=call.data["rest_time"],
                discharge_rest_time=call.data["rest_time"],
                cycle_count=call.data["cycle_count"],
                delta_v=call.data["delta_v"],
                trickle_current=call.data["trickle_current"],
                protection_temperature=call.data["protection_temperature"],
                protection_time=call.data["protection_time"],
            )
            # Validate before touching the charger (raises ValueError).
            from skyrc_ble.mc5000 import build_program_payload

            build_program_payload(0, program)
        except (KeyError, ValueError) as err:
            raise ServiceValidationError(f"Invalid MC5000 program: {err}") from err

        slot = call.data["slot"]
        channels = 0 if slot == "all" else 1 << (int(slot) - 1)
        await coordinator.async_start_program(channels, program)

    hass.services.async_register(
        DOMAIN, SERVICE_START_PROGRAM, async_start_program, schema=START_PROGRAM_SCHEMA
    )
    return True


def _coordinator_for_call(hass: HomeAssistant, call: ServiceCall) -> Mc5000Coordinator:
    entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]
    if entry_id := call.data.get("config_entry_id"):
        entries = [entry for entry in entries if entry.entry_id == entry_id]
    if not entries:
        raise HomeAssistantError("No loaded SkyRC MC5000 found")
    if len(entries) > 1:
        raise ServiceValidationError(
            "More than one MC5000 is set up; pass config_entry_id"
        )
    return entries[0].runtime_data


async def async_setup_entry(hass: HomeAssistant, entry: Mc5000ConfigEntry) -> bool:
    coordinator = Mc5000Coordinator(hass, entry)
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Connect in the background so a charger that is off doesn't block startup.
    entry.async_create_background_task(
        hass, coordinator.async_refresh(), f"{DOMAIN} first refresh"
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: Mc5000ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded
