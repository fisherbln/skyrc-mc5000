# skyrc-mc5000

Control and monitor a **SkyRC MC5000** charger/analyzer over Bluetooth LE, without the
SkyRC app. Read live slot data, **stop** a slot, and **program + start** any mode
(charge, discharge, storage, cycle, refresh, break-in) for every chemistry the
charger supports.

- [`PROTOCOL.md`](PROTOCOL.md) is the full protocol: framing, commands, status layout, and
  the 40-byte program payload.
- [`mc5000.py`](mc5000.py) is a single-file CLI (Python + [bleak](https://github.com/hbldh/bleak)).
- [`custom_components/skyrc_mc5000`](custom_components/skyrc_mc5000) is a Home Assistant
  integration (HACS custom repository). It works through Home Assistant's Bluetooth,
  including ESPHome Bluetooth proxies.

## Status

| Feature | Command | Verified on hardware |
|---|---|---|
| Slot status (V, I, mAh, time, IR, state, mode, chemistry, error) | `0x91` | ✅ |
| Stop slot / all slots | `0xFE` | ✅ |
| Program + start (NiMH charge) | `0x94` | ✅ |
| Program + start (NiMH discharge) | `0x94` | ✅ |
| Program + start (Li-ion charge, 1 A on a 21700) | `0x94` | ✅ |
| LiFePO4 / other chemistries | `0x94` | not yet |
| System settings read/write, USB-C PD status | `0x96` / `0x95` / `0x9B` | not yet |
| Home Assistant integration through an ESPHome Bluetooth proxy (status, stop, start) | | ✅ |

Tested on one MC5000 (Nov 2026) from macOS. Reports from other firmware versions and
platforms are welcome. Please open an issue with `-v` output.

## Quick start

Requires Python 3.10+. [uv](https://docs.astral.sh/uv/) installs `bleak` automatically.
**Close the SkyRC app first**, because the charger accepts only one BLE connection.

```sh
uv run mc5000.py scan                                  # shows e.g. "#Charger-4036"
uv run mc5000.py status -a <address>                   # all four slots
uv run mc5000.py watch  -a <address>                   # live view
uv run mc5000.py stop   -a <address> --slot 1          # or --slot all

# Dry run: prints the frame it would send
uv run mc5000.py start -a <address> --slot 1 --chem nimh --mode charge --current 500
# Add --send to actually start it
uv run mc5000.py start -a <address> --slot 1 --chem nimh --mode charge --current 500 --send
```

On macOS the address is a CoreBluetooth UUID from `scan`. On Linux it's the MAC. `-v`
prints every frame in both directions. See `uv run mc5000.py start -h` for all program
parameters (capacity, cutoffs, termination current, −ΔV, trickle, rest times, cycles,
temperature and time limits).

## Home Assistant

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/fisherbln/skyrc-mc5000`,
   type **Integration**, then download **SkyRC MC5000** and restart Home Assistant.
2. The charger is discovered automatically (it advertises as `#Charger-XXXX`) when a
   Bluetooth adapter or proxy can reach it. Otherwise add it under Settings → Devices &
   services → Add integration → SkyRC MC5000.

Per slot you get status, voltage, current, capacity, elapsed time, internal resistance,
operation, chemistry and error sensors, plus **Stop slot N** and **Stop all slots**
buttons. The **Connection** switch releases the Bluetooth link so the SkyRC app can
connect. Turn it back on to resume.

Start a program with the `skyrc_mc5000.start_program` action:

```yaml
action: skyrc_mc5000.start_program
data:
  slot: "3"
  battery_type: liion
  operation: charge
  capacity: 5000
  charge_current: 1.0     # A; charge voltage defaults to the chemistry max (4.20 V)
```

Programs are validated before anything is sent: the charge voltage can't exceed the
chemistry's maximum, currents are capped at 3 A charge / 2 A discharge, and the operation
must exist for the chemistry. The integration uses the MC5000 support in
[skyrc-ble](https://github.com/jperquin/skyrc-ble/pull/1). It can't be installed alongside
the MC3000 integration (`skyrc_mc3000`) until that change is released, because both use
the `skyrc-ble` package.

## Safety

You are sending charge programs to a lithium-capable charger. The CLI refuses charge
voltages above each chemistry's normal maximum and caps current at 3 A charge / 2 A
discharge, and the charger enforces its own limits as well. Still, double-check the
chemistry before you `--send`, and don't leave cells unattended.

## How this was made

The protocol was worked out by analyzing the official SkyRC Android app (an interoperability
analysis) and then confirmed against a real charger. The program-payload encoder in this repo
was checked byte-for-byte against the app's own encoder on 300 random programs. No SkyRC code,
APK or decompiled source is included in this repository.

The status layout matches what [kolinger/skyrc-mc3000](https://github.com/kolinger/skyrc-mc3000)
found independently (MC5000 BLE monitor). The MC3000 BLE work in
[kroimon/skyrc-ble](https://github.com/kroimon/skyrc-ble) was the starting point.

Not affiliated with or endorsed by SkyRC.

## License

MIT
