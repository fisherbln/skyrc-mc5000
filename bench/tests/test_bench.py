from __future__ import annotations

import json
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from mc5000_bench.charger import RunTracker, SlotReading, find_charger, read_slot
from mc5000_bench.ha import HaClient, apply_entity_diff, websocket_url
from mc5000_bench.presets import DEFAULT_PRESETS, Preset, PresetStore

from fakeha import ADDR, ENTRY, TOKEN, FakeHA, registry


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def wait_for(qapp, cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_default_presets_are_valid():
    for preset in DEFAULT_PRESETS:
        assert preset.problems() == [], preset.name
    assert len({p.name for p in DEFAULT_PRESETS}) == len(DEFAULT_PRESETS)


def test_preset_rules():
    p = Preset("x", "nimh", "storage", 2000)
    assert any("storage" in s for s in p.problems())
    p = Preset("x", "liion", "charge", 2000, charge_current=3.5)
    assert any("Charge current" in s for s in p.problems())
    # Discharge current doesn't matter for a charge-only preset.
    assert Preset("x", "liion", "charge", 2000, discharge_current=9).problems() == []
    assert Preset("x", "liion", "storage", 2000, storage_voltage=4300).problems()


def test_service_data():
    storage = next(p for p in DEFAULT_PRESETS if p.operation == "storage")
    data = storage.service_data(3, ENTRY)
    assert data == {
        "slot": "3", "battery_type": "liion", "operation": "storage", "capacity": 5000,
        "charge_current": 1.0, "discharge_current": 1.0, "cycle_count": 1,
        "storage_voltage": 3800, "config_entry_id": ENTRY,
    }
    ebl = next(p for p in DEFAULT_PRESETS if p.name.startswith("Discharge test"))
    assert ebl.service_data(2)["discharge_voltage"] == 1000


def test_preset_store_roundtrip(tmp_path):
    path = tmp_path / "presets.json"
    store = PresetStore.load(path)
    assert store.names() == [p.name for p in DEFAULT_PRESETS]
    store.presets.append(Preset("Mine", "lifepo4", "charge", 600, charge_current=0.3))
    store.save()
    again = PresetStore.load(path)
    assert again.get("Mine").battery_type == "lifepo4"
    # Unknown keys from a newer version are ignored.
    raw = json.loads(path.read_text())
    raw["presets"][0]["future_field"] = 1
    path.write_text(json.dumps(raw))
    assert PresetStore.load(path).presets[0].name == "Eneloop charge"


def test_find_charger():
    charger = find_charger(registry())
    assert charger.config_entry_id == ENTRY
    assert charger.connection == "switch.skyrc_mc5000_4036_connection"
    assert charger.stop_all == "button.skyrc_mc5000_4036_stop_all_slots"
    assert charger.slots[3]["stop"] == "button.skyrc_mc5000_4036_stop_slot_3"
    assert charger.slots[1]["status"] == "sensor.skyrc_mc5000_4036_slot_1_status"
    assert "light.kitchen" not in charger.all_ids()
    assert not find_charger([{"platform": "hue", "unique_id": f"{ADDR}_slot1_status"}]).found


def test_apply_entity_diff_and_read_slot():
    states: dict = {}
    apply_entity_diff(states, {"a": {"sensor.skyrc_mc5000_4036_slot_1_elapsed":
                                     {"s": "2.5", "a": {"unit_of_measurement": "min"}},
                                     "sensor.skyrc_mc5000_4036_slot_1_status": {"s": "charging", "a": {}}}})
    apply_entity_diff(states, {"c": {"sensor.skyrc_mc5000_4036_slot_1_elapsed": {"+": {"s": "3"}}}})
    charger = find_charger(registry())
    reading = read_slot(1, charger, states)
    assert reading.status == "charging" and reading.active
    assert reading.elapsed_s == 180
    assert reading.voltage is None


def test_run_tracker_keeps_capacity_after_error():
    tracker = RunTracker()

    def r(**kw):
        return SlotReading(slot=2, available=True, **kw)

    assert tracker.update(r(status="discharging", capacity=1100, elapsed_s=8000,
                            chemistry="nimh", operation="discharge"), "t1") is None
    tracker.update(r(status="discharging", capacity=1160, elapsed_s=8355,
                     chemistry="nimh", operation="discharge"), "t2")
    # Charger drops to standby, zeroes the counter and reports the error.
    result = tracker.update(r(status="standby", capacity=0, elapsed_s=0,
                              error="connection_break"), "t3")
    assert result.outcome == "Connection break"
    assert result.capacity == 1160 and result.elapsed_s == 8355
    # Going offline mid-run is not the end of the run.
    tracker.update(r(status="charging", capacity=5), "t4")
    assert tracker.update(SlotReading(slot=2), "t5") is None
    done = tracker.update(r(status="done", capacity=900), "t6")
    assert done.outcome == "Done" and done.capacity == 900


def test_websocket_url():
    assert websocket_url("http://ha.local:8123/") == "ws://ha.local:8123/api/websocket"
    assert websocket_url("https://smarthome.example.org") == "wss://smarthome.example.org/api/websocket"
    assert websocket_url("ha.local:8123") == "ws://ha.local:8123/api/websocket"


def test_client_against_fake_ha(qapp):
    ha = FakeHA()
    client = HaClient()
    seen: dict = {}
    client.status.connect(lambda level, msg: seen.__setitem__("status", (level, msg)))
    client.states_changed.connect(lambda s: seen.__setitem__("states", s))
    client.service_failed.connect(lambda m: seen.__setitem__("failed", m))
    client.connect_to(ha.url, TOKEN)
    assert wait_for(qapp, lambda: seen.get("status", ("",))[0] == "ok")
    assert wait_for(qapp, lambda: "states" in seen)
    assert seen["states"]["sensor.skyrc_mc5000_4036_slot_1_status"]["state"] == "charging"

    ha.push({"sensor.skyrc_mc5000_4036_slot_1_status": "done"})
    assert wait_for(qapp, lambda: seen["states"]["sensor.skyrc_mc5000_4036_slot_1_status"]["state"] == "done")

    preset = DEFAULT_PRESETS[0]
    client.call_service("skyrc_mc5000", "start_program", preset.service_data(4, ENTRY))
    client.press("button.skyrc_mc5000_4036_stop_slot_1")
    assert wait_for(qapp, lambda: len(ha.calls) == 2)
    assert ha.calls[0]["service_data"]["slot"] == "4"
    assert ha.calls[1]["target"] == {"entity_id": "button.skyrc_mc5000_4036_stop_slot_1"}

    ha.fail_services = True
    client.press("button.skyrc_mc5000_4036_stop_slot_1")
    assert wait_for(qapp, lambda: "failed" in seen)
    assert "nope" in seen["failed"]
    client.shutdown()


def test_bad_token(qapp):
    ha = FakeHA()
    client = HaClient()
    seen: dict = {}
    client.status.connect(lambda level, msg: seen.__setitem__("status", (level, msg)))
    client.connect_to(ha.url, "wrong")
    assert wait_for(qapp, lambda: seen.get("status", ("",))[0] == "error")
    assert "token" in seen["status"][1]
    client.shutdown()


def test_main_window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from mc5000_bench import app as appmod
    from mc5000_bench.config import Settings, save_settings

    ha = FakeHA()
    save_settings(Settings(ha.url, TOKEN))
    assert oct(os.stat(tmp_path / "mc5000-bench/settings.json").st_mode & 0o777) == "0o600"
    window = appmod.MainWindow()
    assert wait_for(qapp, lambda: window.cards[1].reading.status == "charging")
    card1, card4 = window.cards[1], window.cards[4]
    assert card1.stop.isEnabled() and not card1.start.isEnabled()
    assert card4.start.isEnabled() and not card4.stop.isEnabled()
    assert card1.values["voltage"].text() == "1.412 V"
    assert card1.values["elapsed"].text() == "0:47:30"
    assert window.cards[2].error.text() == "Connection break"

    # Run ends with an error: the last result keeps the mAh.
    ha.push({"sensor.skyrc_mc5000_4036_slot_1_status": "standby",
             "sensor.skyrc_mc5000_4036_slot_1_capacity": "0",
             "sensor.skyrc_mc5000_4036_slot_1_error": "battery_full"})
    assert wait_for(qapp, lambda: "812 mAh" in card1.last.text())
    assert "Battery full" in card1.last.text()
    assert (tmp_path / "mc5000-bench/results.json").exists()

    card4.preset.setCurrentText("Li-ion storage")
    window.start_preset(4, window.presets.get("Li-ion storage"))
    assert wait_for(qapp, lambda: len(ha.calls) == 1)
    assert ha.calls[0]["service_data"]["operation"] == "storage"
    assert json.loads((tmp_path / "mc5000-bench/selection.json").read_text())["4"] == "Li-ion storage"

    # Pausing HA's charger link shows the banner.
    ha.push({"switch.skyrc_mc5000_4036_connection": "off"})
    assert wait_for(qapp, lambda: not window.banner_box.isHidden())
    window.close()


def test_preset_dialog(qapp, tmp_path):
    from mc5000_bench.app import PresetDialog

    store = PresetStore.load(tmp_path / "p.json")
    dialog = PresetDialog(store)
    dialog.list.setCurrentRow(3)  # Li-ion storage
    assert dialog.operation.currentData() == "storage"
    assert abs(dialog.storage.value() - 3.80) < 1e-6
    # Switching to NiMH drops the storage mode.
    dialog.battery.setCurrentIndex(dialog.battery.findData("nimh"))
    assert dialog.items[3].battery_type == "nimh"
    assert dialog.items[3].operation in ("charge", "discharge", "cycle", "refresh", "breakin")
    assert dialog.items[3].storage_voltage is None
    dialog._add()
    assert dialog.items[-1].name == "New preset"
    dialog.name.setText("AA test")
    assert dialog.items[-1].name == "AA test"


def test_buttons_enabled_when_states_arrive_before_ok(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from mc5000_bench import app as appmod
    from mc5000_bench.charger import find_charger
    from mc5000_bench.ha import apply_entity_diff
    from fakeha import sample_states

    class Quiet(HaClient):
        def connect_to(self, *a, **k):
            pass

    window = appmod.MainWindow(Quiet())
    window._charger_found(find_charger(registry()))
    states: dict = {}
    apply_entity_diff(states, {"a": sample_states()})
    window._states_changed(states)  # still "connecting"
    assert not window.cards[1].stop.isEnabled()
    window._set_status("ok", "Connected")
    assert window.cards[1].stop.isEnabled()
    assert window.cards[4].start.isEnabled()


def test_charger_offline_message(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from mc5000_bench import app as appmod
    from mc5000_bench.charger import find_charger

    class Quiet(HaClient):
        def connect_to(self, *a, **k):
            pass

    window = appmod.MainWindow(Quiet())
    window._charger_found(find_charger(registry()))
    window._set_status("ok", "Connected to Home Assistant")
    charger = find_charger(registry())
    window._states_changed({eid: {"state": "unavailable", "attributes": {}}
                            for eid in charger.all_ids()})
    assert "not reachable" in window.status.text()
    assert window.cards[1].chip.text() == "Offline"
    assert not window.cards[1].start.isEnabled()
