"""Fixtures for the skyrc_mc5000 Home Assistant integration tests."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from skyrc_ble import Mc5000DeviceInfo, Mc5000Settings, Mc5000UsbStatus
from bleak.backends.device import BLEDevice
from skyrc_ble.mc5000 import parse_channel_data
from skyrc_ble.models import Mc5000State

pytest_plugins = "pytest_homeassistant_custom_component"

ADDRESS = "AA:BB:CC:DD:40:36"

# Frames captured from a real MC5000.
FRAMES = [
    "0f 16 91 01 00 00 05 0f 00 00 00 00 00 00 00 00 00 00 00 00 00 03 00 a9",
    "0f 16 91 02 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 03 00 96",
    "0f 16 91 04 00 00 0d be 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 60",
    "0f 16 91 08 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 99",
]


@pytest.fixture(autouse=True)
def auto_enable(enable_custom_integrations, mock_bluetooth):
    yield


@pytest.fixture
def mock_charger():
    state = Mc5000State()
    for raw in FRAMES:
        channel, data = parse_channel_data(bytes.fromhex(raw))
        state.channels[channel] = data

    charger = MagicMock()
    charger.state = state
    charger.is_connected = True
    charger.connect = AsyncMock(return_value=True)
    charger.update = AsyncMock()
    charger.disconnect = AsyncMock()
    charger.start_program_multi = AsyncMock()
    charger.stop_charge_multi = AsyncMock()
    # Readings from the real charger (firmware 2.02) on 2026-11-22.
    charger.get_settings = AsyncMock(return_value=Mc5000Settings(
        keypress_beep=0, notify_beep=1, completion_repeat=False,
        backlight=1, warning=1, screen_timeout=0,
    ))
    charger.set_settings = AsyncMock(return_value=True)
    charger.get_usb_status = AsyncMock(return_value=Mc5000UsbStatus(
        status=2, current=2.17, voltage=15.264, energy=0.23, runtime=47,
    ))
    charger.get_device_info = AsyncMock(return_value=Mc5000DeviceInfo(
        model_code="100214", firmware="2.02", hardware="2",
        serial_code="313030323134040000000002020200ce",
    ))

    ble_device = BLEDevice(ADDRESS, "#Charger-4036", None)
    with patch(
        "custom_components.skyrc_mc5000.coordinator.Mc5000", return_value=charger
    ), patch(
        "custom_components.skyrc_mc5000.coordinator.bluetooth.async_ble_device_from_address",
        return_value=ble_device,
    ):
        yield charger
