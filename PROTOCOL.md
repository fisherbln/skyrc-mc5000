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
| `0x96` | query system settings | `[0x04]` | settings frame |
| `0x95` | write system settings | `MachineBean.getMc5000SystemSettingData()` | byte 4 == 1 = success |
| `0x9B` | USB-C PD port status | `[0x00]` | PD status frame (≥18 bytes) |
| `0x93` | query slot program (unverified) | `[mask]` | program frame |

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
