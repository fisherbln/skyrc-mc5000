# Changelog

Releases of the `skyrc_mc5000` integration. A release is published automatically when
the version in `custom_components/skyrc_mc5000/manifest.json` changes on main.

## v0.5.0

First tagged release. From now on Home Assistant's Marketplace (HACS) only offers an update when a new release is published, not on every commit.

**Integration (`skyrc_mc5000`)**
- Per-slot status, voltage, current, capacity, time, resistance, chemistry, operation, error and temperature sensors; stop/start buttons; program pickers.
- `start_program` action with every program field, including cycle order and Li-ion restart voltage.
- Slot N last result sensors: how each run ended, with final mAh and time (kept even when the charger zeroes its counter on an error).
- Charger settings as selects: LCD backlight, screen timeout, keypress beep, notify beep, completion tone.
- USB-C PD port sensors (voltage, current, power, energy, time on) and firmware version, with an update entity that checks SkyRC's update server.
- `get_history`: the 1-second voltage curve the charger keeps for a running slot.
- `send_command`: raw frames for protocol testing (firmware, factory reset and password commands refused).

**MC5000 Bench** (`bench/`): touch-friendly Linux desktop app that works through Home Assistant: presets, charge curves, run history, charger settings.

Requires skyrc-ble with MC5000 support (installed automatically from fisherbln/skyrc-ble until jperquin/skyrc-ble#1 is released).
