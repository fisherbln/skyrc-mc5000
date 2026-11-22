# SkyRC MC5000 BLE protocol

Recovered from the official SkyRC Android app (`com.skyrc.q200`, internal package
`com.storm.skyrccharge`, downloaded from soft.skyrc.com on 2026-11-15). The MC5000 is
device model **SK-100214**; the app calls it `DeviceType.mc5000` / "214 type" and
reuses the NC3000 command set. Key classes: `app/Cmd.java` (frame builders),
`bean/Nc3000SetBean.getDatas()` (program payload), `bean/NcBean.setMc5000Data()`
(status decode), `data/ble/BleDataSourceImpl.handleIncomingBleData()` (responses).

Status: frame format, status decode and stop/start encoding are from the app.
Start payload is verified byte-identical to the app's own code (300 random programs).
Status, stop and start (NiMH charge/discharge, Li-ion charge) verified on hardware (see Transport).

## Transport

- GATT service `0000ffe0-…-00805f9b34fb`, characteristic `0000ffe1-…` (write without
  response + notify; same characteristic both ways).
- The app requests MTU 253 and sends each frame in **one** write (no 20-byte split),
  at least 150 ms apart. Frames longer than 20 bytes (start = 44 bytes) need MTU > 23.
- No pairing, password or handshake is needed for these commands (the app sends a
  `0x57` device-info frame with the phone's view of the MAC on connect, but status
  polling works without it).
- One BLE central at a time: close the SkyRC app first.
- **Hardware-verified (2026-11-15, macOS/bleak, MTU 223):** the first frame after
  connecting is answered with an unsolicited `0f 04 06 01 01 08` (cmd `0x06`, looks like a
  connect hello) instead of its own reply, so send a throwaway status query first.
  Status (`0x91`) and stop (`0xFE`, ack `0f 04 fe <mask> 01 <sum>`) confirmed working.
  Start (`0x94`) confirmed: ack `0f 04 94 <mask> 01 <sum>`, then the slot stays in standby
  for a few seconds (detection + IR test) before reporting charging. NiMH 300 mA charge
  on slot 1 ran at exactly 300 mA, IR reported at 61 mΩ. Temperature read 0 with no probe.
  Li-ion charge at 1 A (21700, slot 3) ran at 995–1001 mA, IR 40 mΩ; it took longer than
  5 s to leave standby. NiMH discharge at 500 mA held 501 mA.

## Framing

```
0F | LEN | CMD | PAYLOAD… | SUM
LEN = len(PAYLOAD) + 2
SUM = (CMD + sum(PAYLOAD)) & 0xFF
```
Responses use the same framing and may arrive split across notifications; reassemble
by LEN. Multi-byte values are big-endian.

## Slot addressing

Bitmask: slot 1 = `0x01`, slot 2 = `0x02`, slot 3 = `0x04`, slot 4 = `0x08`.
`0x00` = all slots (used by stop-all and by the app's "apply to all slots" start).

## Commands

| CMD | Name | Payload | Response |
|---|---|---|---|
| `0x91` | slot status | `[mask]` | status frame (below) |
| `0x94` | program + start | 40 bytes (below) | `[.., ok]`, byte 4 == 1 = success |
| `0xFE` | stop | `[mask]` (0 = all) | byte 4 == 1 = success |
| `0x96` | query system settings | `[0x04]` | settings frame (below) |
| `0x95` | write system settings | 17 bytes (below) | byte 4 == 1 = success |
| `0x9B` | USB-C PD port status | `[0x00]` | USB-C frame (below) |
| `0x57` | device info | 17 bytes: `00` + MAC byte-reversed + zeros | info frame (below) |
| `0x93` | query slot program: **not supported** (no reply, idle or running) | `[mask]` | – |
| `0x98` | stored voltage curve of a slot's current or last finished run | `[mask]` | 1+ pages (below) |

Examples: status slot 1 `0f 03 91 01 92`; stop all `0f 03 fe 00 fe`;
stop slot 3 `0f 03 fe 04 02`.

## Status frame (`0x91`) – byte offsets in the full frame

| Bytes | Field |
|---|---|
| 3 | slot mask |
| 4–5 | current, mA |
| 6–7 | voltage, mV |
| 8–9 | temperature (app shows as °C) |
| 10–11 | capacity, mAh |
| 12–15 | elapsed time, s |
| 16–17 | internal resistance, mΩ |
| 18 | status: 0 standby, 1 ready, 2 charging, 3 discharging, 4 resting, 5/6 complete |
| 19 | mode (per chemistry group, below) |
| 20 | error code (kolinger/skyrc-mc3000 list) |
| 21 | chemistry |
| 22 | PD working status (if present) |

## Chemistry and modes

| Idx | Chemistry | Group | Default charge V | Discharge cutoff | Storage |
|---|---|---|---|---|---|
| 0 | Li-ion | Li | 4.20 | 3.20 | 3.80 |
| 1 | Li-ion HV | Li | 4.35 | 3.40 | 3.90 |
| 2 | LiFePO4 | Li | 3.65 | 2.90 | 3.30 |
| 3 | NiMH | Ni | 1.65 | 0.90 | – |
| 4 | NiCd | Ni | 1.65 | 0.90 | – |
| 5 | Eneloop | Ni | 1.65 | 0.90 | – |
| 6 | NiZn | Zn | 1.90 | 1.10 | – |
| 7 | RAM | Zn | 1.65 | 0.90 | – |
| 8 | LTO | Li | 2.85 | 1.80 | 2.40 |
| 9 | Na-ion | Li | 4.00 | 2.00 | 3.50 |

Modes: Li group 0 charge, 1 storage, 2 discharge, 3 cycle · Ni group 0 charge,
1 refresh, 2 break-in, 3 discharge, 4 cycle · Zn group 0 charge, 1 discharge, 2 cycle.
The app refuses charge voltages above the default column.

## Program payload (`0x94`, 40 bytes)

| Bytes | Field | App default |
|---|---|---|
| 0 | slot mask (0 = all) | |
| 1 | mode | |
| 2–3 | charge current, mA | 1000 (Ni), 3000 (Li) |
| 4–5 | discharge current, mA | |
| 6–7 | capacity, mAh | 3000 / 5000 |
| 8–9 | charge target voltage, mV | chemistry default |
| 10–11 | discharge cutoff voltage, mV | chemistry default |
| 12–13 | charge termination current, mA | 100 |
| 14–15 | discharge termination current, mA (0 → discharge current) | 100 |
| 16–17 | rest after charge, min | 10 |
| 18–19 | rest after discharge, min | 10 |
| 20 | cycle count | 1 |
| 21 | cycle mode | 0 |
| 22 | NiMH −ΔV, mV | 6 |
| 23 | trickle current ÷ 10 | 0 |
| 24–25 | restart voltage, mV | 0 |
| 26 | temperature cutoff, °C (0 → 71) | 60 |
| 27–28 | safety timer, min | 0 |
| 29–30 | MaxBoost capacity, mAh (0 → 300) | 300 |
| 31 | turbo | 0 |
| 32 | chemistry index | |
| 33–34 | storage voltage, mV | chemistry default |
| 35 | Eneloop sub-type | 0 |
| 36–39 | zero | |


## System settings, USB-C and device info (hardware-verified 2026-11-22, firmware 2.02)

**Settings reply (`0x96`)**, e.g. `0f 09 96 08 00 01 00 01 01 00 a1`: byte 4 keypress
beep (0 off, 1 low, 2 medium, 3 high), 5 notify beep (same scale), 6 completion tone
(0 once, 1 repeat every 30 min), 7 LCD backlight (1 low, 2 medium, 3 high), 8 "warning"
(no item in the charger's menu; keep as read), 9 screen timeout in minutes (0 off; 1, 3,
5, 10, 15, 30, 60, 120). Values matched the charger's System Settings menu.

**Settings write (`0x95`)**: 17-byte payload `00 key notify completion backlight warning
timeout 00 …00`. Writing `00 00 01 00 03 01 00 …` changed the backlight from low to high
(ack `0f 04 95 10 01 a6`) and the read-back showed it. Reads are read-modify-write: send
every field back as read.

**USB-C (`0x9B`)**: byte 4 status (0 idle; 2 while powering a phone), 5–6 current mA,
7–8 voltage mV, 9–12 energy mWh, 13–16 seconds on (all big-endian). Example with a phone
charging: `0f 10 9b 00 02 08 7a 3b a0 00 00 00 e6 00 00 00 2f 0f` = 2.17 A, 15.264 V,
230 mWh, 47 s. The port is bidirectional (PD 3.0 input up to 100 W, output up to 65 W);
only output has been observed.

**Device info (`0x57`)**: reply `0f 13 57 00 31 30 30 32 31 34 04 00 00 00 00 02 02 02 00 ce 57`:
bytes 4–9 ASCII model code `100214`, bytes 15–16 firmware `2.02`, hardware revision from
the hex of bytes 4–19 (character 27) = `2`.


## Stored voltage curve (`0x98`, hardware-verified 2026-11-22)

`0f LEN 98 <mask> <pages> <page> <interval> <v0 lo> <v0 hi> <v1 lo> <v1 hi> … SUM`

One request streams every page back to back (page 1..pages), each up to 100 samples.
Samples are millivolts, little-endian; `interval` is seconds per sample. The charger
keeps the whole run in about 300 samples: as the run grows it doubles the interval
(observed 1 s at 4 min, 4 s at 17 min, 8 s at 32 min, 16 s at 66 min, each covering
the full run from its start), so long runs come back at coarser resolution rather than
truncated. The
voltage itself is re-measured roughly every 20 s. Example, 228 s into
an Eneloop charge at 0.5 A: pages 3 (100 + 100 + 26 samples), starting at 1.409 V and
dipping to 1.343 V before rising.

The curve stays readable after a run finishes on its own, frozen at the run's
length: 17 minutes after a 66-minute Eneloop charge ended (`done`, 481 mAh), the reply
was still pages 3 (100 + 100 + 47 samples) at 16 s, i.e. 247 samples covering the run
from start to finish, last sample 1.533 V. There is no reply for an empty slot, a slot
that hasn't run, or once a run was stopped by hand. `0x93` (the NC-series "read program")
got no reply on the MC5000 at all.
