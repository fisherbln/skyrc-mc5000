# Changelog

Releases of the `skyrc_mc5000` integration. A release is published automatically when
the version in `custom_components/skyrc_mc5000/manifest.json` changes on main.

## v0.5.1

**Integration**
- `get_history` now also returns the curve of a slot's last run after it finishes on its own (the charger keeps it until the next run starts). The response field `running` is renamed `available`, since a finished run has a curve too.
- Clearer description of the curve's resolution: about 300 samples per run, so the interval grows from 1 s to 16 s over an hour-long charge.

**MC5000 Bench 0.5.1**
- Fetches a slot's curve once more after a run ends, so the saved run keeps the charger's detail to the very end, lined up with the run's end time.
- The curve hint says how often the charger sampled ("every 16 s") instead of claiming one sample a second.
- Accepts both `available` (integration 0.5.1) and `running` (0.5.0).

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
