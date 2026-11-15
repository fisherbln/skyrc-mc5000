"""Regression tests built from frames captured on a real MC5000 (2026-11-15).

Run: uv run --with pytest --with bleak pytest
"""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("mc5000", Path(__file__).parent.parent / "mc5000.py")
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)

h = bytes.fromhex


def test_status_request_frames():
    assert m.frame(m.CMD_STATUS, [0x01]) == h("0f 03 91 01 92")
    assert m.frame(m.CMD_STATUS, [0x08]) == h("0f 03 91 08 99")


def test_stop_frames():
    assert m.frame(m.CMD_STOP, [0x01]) == h("0f03fe01ff")
    assert m.frame(m.CMD_STOP, [0x00]) == h("0f03fe00fe")


def test_acks_from_hardware_have_valid_checksums():
    for ack in ("0f04fe010100", "0f0494010196", "0f0494020197", "0f0406010108"):
        assert m.check(h(ack))


def test_status_decode_idle_cell():
    s = m.parse_status(h("0f 16 91 01 00 00 05 0f 00 00 00 00 00 00 00 00 00 00 00 00 00 03 00 a9"))
    assert s["slot"] == 1
    assert s["voltage_mv"] == 1295
    assert s["status"] == "standby"
    assert s["chem"] == "NiMH"
    assert s["mode"] == "charge"


def test_start_nimh_charge_matches_captured_frame():
    # uv run mc5000.py start --slot 1 --chem nimh --mode charge --current 300 --capacity 2000
    payload = m.program_payload(
        0x01, 3, 0, charge_ma=300, discharge_ma=500, capacity_mah=2000, charge_mv=1650,
        discharge_mv=900, term_ma=100, dis_term_ma=100, rest_charge_min=10,
        rest_discharge_min=10, cycles=1, cycle_mode=0, delta_v_mv=6, trickle_ma=0,
        restart_mv=0, temp_c=60, time_min=0, maxboost_mah=300, turbo=0, storage_mv=3300,
        eneloop=0)
    assert m.frame(m.CMD_START, payload) == h(
        "0f 2a 94 01 00 01 2c 01 f4 07 d0 06 72 03 84 00 64 00 64 00 0a 00 0a 01 00 06 00 00 00 3c "
        "00 00 01 2c 00 03 0c e4 00 00 00 00 00 cc")


def test_start_nimh_discharge_matches_captured_frame():
    payload = m.program_payload(
        0x02, 3, 3, charge_ma=300, discharge_ma=500, capacity_mah=3000, charge_mv=1650,
        discharge_mv=1000, term_ma=100, dis_term_ma=100, rest_charge_min=10,
        rest_discharge_min=10, cycles=1, cycle_mode=0, delta_v_mv=6, trickle_ma=0,
        restart_mv=0, temp_c=60, time_min=0, maxboost_mah=300, turbo=0, storage_mv=3300,
        eneloop=0)
    assert m.frame(m.CMD_START, payload) == h(
        "0f 2a 94 02 03 01 2c 01 f4 0b b8 06 72 03 e8 00 64 00 64 00 0a 00 0a 01 00 06 00 00 00 3c "
        "00 00 01 2c 00 03 0c e4 00 00 00 00 00 20")
