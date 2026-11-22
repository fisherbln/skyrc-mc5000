from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_USER
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.service_info.bluetooth import BluetoothServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry
from skyrc_ble import Mc5000BatteryType, Mc5000Operation

from custom_components.skyrc_mc5000.const import DOMAIN

from .conftest import ADDRESS

SERVICE_INFO = BluetoothServiceInfo(
    name="#Charger-4036",
    address=ADDRESS,
    rssi=-47,
    manufacturer_data={},
    service_data={},
    service_uuids=["0000ffe0-0000-1000-8000-00805f9b34fb"],
    source="local",
)


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=ADDRESS, data={"address": ADDRESS},
        title="SkyRC MC5000 4036",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_user_flow(hass: HomeAssistant) -> None:
    with patch(
        "custom_components.skyrc_mc5000.config_flow.async_discovered_service_info",
        return_value=[SERVICE_INFO],
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
        assert result["type"] is FlowResultType.FORM
        with patch("custom_components.skyrc_mc5000.async_setup_entry", return_value=True):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {"address": ADDRESS}
            )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "SkyRC MC5000 4036"
    assert result["data"] == {"address": ADDRESS}


async def test_bluetooth_discovery_flow(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=SERVICE_INFO
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"
    with patch("custom_components.skyrc_mc5000.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_no_devices(hass: HomeAssistant) -> None:
    with patch(
        "custom_components.skyrc_mc5000.config_flow.async_discovered_service_info",
        return_value=[],
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_sensors(hass: HomeAssistant, mock_charger) -> None:
    await _setup(hass)
    assert hass.states.get("sensor.skyrc_mc5000_4036_slot_1_voltage").state == "1.295"
    assert hass.states.get("sensor.skyrc_mc5000_4036_slot_1_status").state == "standby"
    assert hass.states.get("sensor.skyrc_mc5000_4036_slot_1_chemistry").state == "nimh"
    # Charge is operation code 0; it must not read as unknown.
    assert hass.states.get("sensor.skyrc_mc5000_4036_slot_1_operation").state == "charge"
    assert hass.states.get("sensor.skyrc_mc5000_4036_slot_3_voltage").state == "3.518"
    assert hass.states.get("sensor.skyrc_mc5000_4036_slot_3_chemistry").state == "liion"
    assert hass.states.get("sensor.skyrc_mc5000_4036_slot_1_internal_resistance").state == "unknown"


async def test_stop_buttons(hass: HomeAssistant, mock_charger) -> None:
    await _setup(hass)
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.skyrc_mc5000_4036_stop_slot_2"}, blocking=True
    )
    mock_charger.stop_charge_multi.assert_awaited_with(0x02)
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.skyrc_mc5000_4036_stop_all_slots"}, blocking=True
    )
    mock_charger.stop_charge_multi.assert_awaited_with(0)


async def test_start_program_defaults(hass: HomeAssistant, mock_charger) -> None:
    await _setup(hass)
    await hass.services.async_call(
        DOMAIN, "start_program",
        {"slot": "3", "battery_type": "liion", "operation": "charge",
         "capacity": 5000, "charge_current": 1.0},
        blocking=True,
    )
    channels, program = mock_charger.start_program_multi.await_args.args
    assert channels == 0x04
    assert program.battery_type is Mc5000BatteryType.LIION
    assert program.operation is Mc5000Operation.CHARGE
    assert program.charge_voltage == 4200
    assert program.discharge_voltage == 3200
    assert program.charge_current == 1.0
    assert program.cycle_type == 0
    assert program.maintenance_voltage == 0


async def test_start_program_cycle_order(hass: HomeAssistant, mock_charger) -> None:
    await _setup(hass)
    await hass.services.async_call(
        DOMAIN, "start_program",
        {"slot": "2", "battery_type": "nimh", "operation": "cycle", "capacity": 2000,
         "charge_current": 0.5, "discharge_current": 0.5, "cycle_count": 2,
         "cycle_mode": "discharge_charge", "charge_end_current": 50, "rest_time": 30,
         "delta_v": 4, "trickle_current": 50, "protection_temperature": 45},
        blocking=True,
    )
    _channels, program = mock_charger.start_program_multi.await_args.args
    assert program.cycle_type == 1 and program.cycle_count == 2
    assert program.charge_end_current == 50 and program.charge_rest_time == 30
    assert program.delta_v == 4 and program.trickle_current == 50
    assert program.protection_temperature == 45
    from skyrc_ble.mc5000 import build_program_payload

    payload = build_program_payload(0x02, program)
    assert payload[21] == 1  # cycle mode byte (PROTOCOL.md)


@pytest.mark.parametrize(
    "extra",
    [
        {"battery_type": "liion", "operation": "charge", "charge_voltage": 4300},
        {"battery_type": "nimh", "operation": "storage"},
    ],
)
async def test_start_program_rejects_unsafe(hass: HomeAssistant, mock_charger, extra) -> None:
    await _setup(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "start_program",
            {"slot": "1", "capacity": 2000, "charge_current": 0.5, **extra},
            blocking=True,
        )
    mock_charger.start_program_multi.assert_not_awaited()


async def test_connection_switch_releases_charger(hass: HomeAssistant, mock_charger) -> None:
    await _setup(hass)
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.skyrc_mc5000_4036_connection"}, blocking=True
    )
    mock_charger.disconnect.assert_awaited()
    assert hass.states.get("sensor.skyrc_mc5000_4036_slot_1_voltage").state == STATE_UNAVAILABLE
    assert hass.states.get("switch.skyrc_mc5000_4036_connection").state == "off"
    # Buttons are unavailable while released, so presses never reach the charger.
    assert hass.states.get("button.skyrc_mc5000_4036_stop_slot_1").state == STATE_UNAVAILABLE
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.skyrc_mc5000_4036_stop_slot_1"}, blocking=True
    )
    mock_charger.stop_charge_multi.assert_not_awaited()
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.skyrc_mc5000_4036_connection"}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get("sensor.skyrc_mc5000_4036_slot_1_voltage").state == "1.295"


async def test_program_entities_and_start(hass: HomeAssistant, mock_charger) -> None:
    await _setup(hass)
    chem = "select.skyrc_mc5000_4036_slot_3_program_chemistry"
    op = "select.skyrc_mc5000_4036_slot_3_program_operation"
    current = "number.skyrc_mc5000_4036_slot_3_program_charge_current"
    capacity = "number.skyrc_mc5000_4036_slot_3_program_capacity"

    assert hass.states.get(chem).state == "nimh"
    assert "refresh" in hass.states.get(op).attributes["options"]

    await hass.services.async_call(
        "select", "select_option", {"entity_id": op, "option": "breakin"}, blocking=True
    )
    # Switching to Li-ion drops break-in (not a lithium operation) back to charge.
    await hass.services.async_call(
        "select", "select_option", {"entity_id": chem, "option": "liion"}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(op).state == "charge"
    assert hass.states.get(op).attributes["options"] == [
        "charge", "storage", "discharge", "cycle"
    ]

    await hass.services.async_call(
        "number", "set_value", {"entity_id": current, "value": 1.0}, blocking=True
    )
    await hass.services.async_call(
        "number", "set_value", {"entity_id": capacity, "value": 5000}, blocking=True
    )
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.skyrc_mc5000_4036_start_slot_3"}, blocking=True
    )
    channels, program = mock_charger.start_program_multi.await_args.args
    assert channels == 0x04
    assert program.battery_type is Mc5000BatteryType.LIION
    assert program.operation is Mc5000Operation.CHARGE
    assert program.charge_current == 1.0
    assert program.capacity == 5000
    assert program.charge_voltage == 4200


async def test_program_settings_editable_while_released(hass: HomeAssistant, mock_charger) -> None:
    await _setup(hass)
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.skyrc_mc5000_4036_connection"}, blocking=True
    )
    chem = "select.skyrc_mc5000_4036_slot_1_program_chemistry"
    assert hass.states.get(chem).state == "nimh"
    await hass.services.async_call(
        "select", "select_option", {"entity_id": chem, "option": "eneloop"}, blocking=True
    )
    assert hass.states.get(chem).state == "eneloop"
    assert hass.states.get("button.skyrc_mc5000_4036_start_slot_1").state == STATE_UNAVAILABLE


async def test_program_settings_restored(hass: HomeAssistant, mock_charger) -> None:
    from homeassistant.core import State
    from pytest_homeassistant_custom_component.common import (
        mock_restore_cache_with_extra_data,
    )

    mock_restore_cache_with_extra_data(
        hass,
        [
            (State("select.skyrc_mc5000_4036_slot_2_program_chemistry", "lifepo4"), {}),
            (State("select.skyrc_mc5000_4036_slot_2_program_operation", "storage"), {}),
            (
                State("number.skyrc_mc5000_4036_slot_2_program_charge_current", "1.5"),
                {"native_value": 1.5, "native_unit_of_measurement": "A", "native_min_value": 0.05, "native_max_value": 3.0, "native_step": 0.05},
            ),
        ],
    )
    await _setup(hass)
    assert hass.states.get("select.skyrc_mc5000_4036_slot_2_program_chemistry").state == "lifepo4"
    assert hass.states.get("select.skyrc_mc5000_4036_slot_2_program_operation").state == "storage"
    assert hass.states.get("number.skyrc_mc5000_4036_slot_2_program_charge_current").state == "1.5"


async def test_dropped_connection_marks_unavailable(hass: HomeAssistant, mock_charger) -> None:
    await _setup(hass)
    assert hass.states.get("sensor.skyrc_mc5000_4036_slot_1_voltage").state == "1.295"
    mock_charger.is_connected = False
    mock_charger.connect.return_value = False
    coordinator = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.skyrc_mc5000_4036_slot_1_voltage").state == STATE_UNAVAILABLE


async def test_disconnects_when_home_assistant_stops(hass: HomeAssistant, mock_charger) -> None:
    from homeassistant.const import EVENT_HOMEASSISTANT_STOP

    await _setup(hass)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    mock_charger.disconnect.assert_awaited()


async def test_keeps_polling_while_connected_and_not_advertising(
    hass: HomeAssistant, mock_charger
) -> None:
    """Connected chargers stop advertising; that must not fail the poll."""
    await _setup(hass)
    coordinator = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    with patch(
        "custom_components.skyrc_mc5000.coordinator.bluetooth.async_ble_device_from_address",
        return_value=None,
    ):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    assert coordinator.last_update_success
    assert hass.states.get("sensor.skyrc_mc5000_4036_slot_1_voltage").state == "1.295"


async def test_send_command(hass: HomeAssistant, mock_charger) -> None:
    await _setup(hass)
    mock_charger._request = AsyncMock(return_value=bytes.fromhex("0f0d96000102010103000000000000a2"))
    reply = await hass.services.async_call(
        DOMAIN, "send_command", {"command": 150, "payload": "04"},
        blocking=True, return_response=True,
    )
    mock_charger._request.assert_awaited_with(150, b"\x04")
    assert reply["sent"] == "96 04"
    assert reply["reply"].startswith("0f 0d 96")
    # Firmware, factory reset and password commands never reach the charger.
    mock_charger._request.reset_mock()
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "send_command", {"command": 0x97, "payload": "04"},
            blocking=True, return_response=True,
        )
    mock_charger._request.assert_not_awaited()


async def test_system_settings(hass: HomeAssistant, mock_charger) -> None:
    await _setup(hass)
    backlight = "select.skyrc_mc5000_4036_lcd_backlight"
    assert hass.states.get(backlight).state == "low"
    assert hass.states.get("select.skyrc_mc5000_4036_screen_timeout").state == "off"
    assert hass.states.get("select.skyrc_mc5000_4036_keypress_beep").state == "off"
    assert hass.states.get("select.skyrc_mc5000_4036_notify_beep").state == "low"
    assert hass.states.get("select.skyrc_mc5000_4036_completion_tone").state == "once"

    from dataclasses import replace

    current = mock_charger.get_settings.return_value
    mock_charger.get_settings.side_effect = [current, replace(current, backlight=3)]
    await hass.services.async_call(
        "select", "select_option", {"entity_id": backlight, "option": "high"}, blocking=True
    )
    written = mock_charger.set_settings.await_args.args[0]
    assert written == replace(current, backlight=3)  # nothing else changed
    assert hass.states.get(backlight).state == "high"


async def test_settings_write_refused(hass: HomeAssistant, mock_charger) -> None:
    await _setup(hass)
    mock_charger.set_settings.return_value = False
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "select", "select_option",
            {"entity_id": "select.skyrc_mc5000_4036_notify_beep", "option": "high"},
            blocking=True,
        )


async def test_usb_sensors_and_firmware(hass: HomeAssistant, mock_charger) -> None:
    await _setup(hass)
    assert float(hass.states.get("sensor.skyrc_mc5000_4036_usb_c_voltage").state) == 15.264
    assert float(hass.states.get("sensor.skyrc_mc5000_4036_usb_c_power").state) == 33.12
    assert float(hass.states.get("sensor.skyrc_mc5000_4036_usb_c_energy").state) == 0.23
    from homeassistant.helpers import device_registry as dr

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, ADDRESS)})
    assert device.sw_version == "2.02" and device.hw_version == "2"


UPDATE_URL = "http://upgrade.skyrc.com/"
SERIAL = "313030323134040000000002020200ce"


async def test_firmware_update_available(hass: HomeAssistant, mock_charger, aioclient_mock) -> None:
    aioclient_mock.get(UPDATE_URL, params={"SN": SERIAL},
                       json=[{"version": "2.03", "memo": {"en": "Fixes NiZn"}, "force": 0}])
    await _setup(hass)
    await hass.async_block_till_done()
    state = hass.states.get("update.skyrc_mc5000_4036_firmware")
    assert state.attributes["installed_version"] == "2.02"
    assert state.attributes["latest_version"] == "2.03"
    assert state.state == "on"
    assert state.attributes["release_summary"] == "Fixes NiZn"
    assert aioclient_mock.mock_calls[0][1].query["SN"] == SERIAL


async def test_firmware_up_to_date(hass: HomeAssistant, mock_charger, aioclient_mock) -> None:
    aioclient_mock.get(UPDATE_URL, params={"SN": SERIAL}, json=[])
    await _setup(hass)
    await hass.async_block_till_done()
    state = hass.states.get("update.skyrc_mc5000_4036_firmware")
    assert state.state == "off" and state.attributes["latest_version"] == "2.02"


async def test_last_result_keeps_capacity_after_error(hass: HomeAssistant, mock_charger, aioclient_mock) -> None:
    from dataclasses import replace

    from skyrc_ble.models import Mc5000ChannelStatus, Mc5000Error, Mc5000Operation

    aioclient_mock.get(UPDATE_URL, params={"SN": SERIAL}, json=[])
    await _setup(hass)
    coordinator = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    channels = mock_charger.state.channels
    base = channels[1]
    channels[1] = replace(base, status=Mc5000ChannelStatus.DISCHARGE,
                          operation=Mc5000Operation.DISCHARGE, capacity=1160, time=8355,
                          error=Mc5000Error.NONE)
    await coordinator.async_refresh()
    assert hass.states.get("sensor.skyrc_mc5000_4036_slot_2_last_result").state == "unknown"
    # The cell's protection cuts out: standby, counter zeroed, error reported.
    channels[1] = replace(channels[1], status=Mc5000ChannelStatus.STANDBY, capacity=0,
                          time=0, error=Mc5000Error.CONNECTION_BREAK)
    await coordinator.async_refresh()
    state = hass.states.get("sensor.skyrc_mc5000_4036_slot_2_last_result")
    assert state.state == "connection_break"
    assert state.attributes["capacity"] == 1160 and state.attributes["elapsed"] == 8355
    assert state.attributes["operation"] == "discharge"
