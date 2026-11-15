"""Fixtures for the skyrc_mc5000 Home Assistant integration tests."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
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

    ble_device = BLEDevice(ADDRESS, "#Charger-4036", None)
    with patch(
        "custom_components.skyrc_mc5000.coordinator.Mc5000", return_value=charger
    ), patch(
        "custom_components.skyrc_mc5000.coordinator.bluetooth.async_ble_device_from_address",
        return_value=ble_device,
    ):
        yield charger
