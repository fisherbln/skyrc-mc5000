#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["bleak>=0.22"]
# ///
"""
SkyRC MC5000 BLE control (status, stop, start/program).

Protocol recovered from the official SkyRC Android app (com.skyrc.q200,
package com.storm.skyrccharge), see PROTOCOL.md next to this file.

Usage (uv recommended, it installs bleak on the fly):
    uv run mc5000.py scan
    uv run mc5000.py status  -a <address>            # all 4 slots once
    uv run mc5000.py watch   -a <address>            # live table, Ctrl-C to quit
    uv run mc5000.py stop    -a <address> --slot 1   # or --slot all
    uv run mc5000.py start   -a <address> --slot 1 --chem nimh --mode charge --current 500
                                                     # prints the frame (dry run)
    uv run mc5000.py start   ... --send              # actually sends it
    uv run mc5000.py sysinfo -a <address>
    uv run mc5000.py raw     -a <address> 91 01      # send cmd+payload, framing added

On macOS the address is a CoreBluetooth UUID (from `scan`), not a MAC.
Close the SkyRC app first: the charger accepts one BLE connection at a time.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time

CHAR_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"
SERVICE_UUID = "0000ffe0-0000-1000-8000-00805f9b34fb"

HEADER = 0x0F
CMD_STATUS = 0x91         # query one slot's realtime data, payload [slot_mask]
CMD_START = 0x94          # program + start, payload 40 bytes (Nc3000SetBean.getDatas)
CMD_SYS_SET = 0x95        # write system settings
CMD_SYS_QUERY = 0x96      # query system settings, payload [0x04]
CMD_PD_STATUS = 0x9B      # USB-C PD port status, payload [0x00]
CMD_STOP = 0xFE           # stop, payload [slot_mask] (0 = all slots)

SLOT_MASK = {1: 0x01, 2: 0x02, 3: 0x04, 4: 0x08}
MASK_SLOT = {v: k for k, v in SLOT_MASK.items()}

# batteryType214 index -> (name, default/max charge V mV, default discharge cutoff mV, storage mV)
CHEMS = {
    "liion":   (0, "Li-ion",    4200, 3200, 3800),
    "liionhv": (1, "Li-ion HV", 4350, 3400, 3900),
    "lifepo4": (2, "LiFePO4",   3650, 2900, 3300),
    "nimh":    (3, "NiMH",      1650,  900, 3300),
    "nicd":    (4, "NiCd",      1650,  900, 3300),
    "eneloop": (5, "Eneloop",   1650,  900, 3300),
    "nizn":    (6, "NiZn",      1900, 1100, 3300),
    "ram":     (7, "RAM",       1650,  900, 3300),
    "lto":     (8, "LTO",       2850, 1800, 2400),
    "naion":   (9, "Na-ion",    4000, 2000, 3500),
}
CHEM_BY_INDEX = {v[0]: v[1] for v in CHEMS.values()}
GROUP = {0: 0, 1: 0, 2: 0, 8: 0, 9: 0, 3: 1, 4: 1, 5: 1, 6: 2, 7: 2}
MODES = {
    0: {"charge": 0, "storage": 1, "discharge": 2, "cycle": 3},
    1: {"charge": 0, "refresh": 1, "breakin": 2, "discharge": 3, "cycle": 4},
    2: {"charge": 0, "discharge": 1, "cycle": 2},
}
MODE_NAMES = {g: {v: k for k, v in m.items()} for g, m in MODES.items()}
STATUSES = {0: "standby", 1: "ready", 2: "charging", 3: "discharging",
            4: "resting", 5: "complete", 6: "complete"}
ERRORS = {0: "", 1: "input V low", 2: "input V high", 3: "connection break",
          4: "capacity limit", 5: "time limit", 6: "internal temp high",
          7: "calibration failed", 8: "high IR", 9: "connection break",
          10: "battery type error", 11: "overload", 12: "reversed polarity",
          13: "fully charged"}

# Conservative ceilings for this tool (the charger enforces its own too).
MAX_CHARGE_MA = 3000
MAX_DISCHARGE_MA = 2000


def frame(cmd: int, payload: bytes | list[int]) -> bytes:
    payload = bytes(payload)
    body = bytes([cmd]) + payload
    return bytes([HEADER, len(payload) + 2]) + body + bytes([sum(body) & 0xFF])


def check(data: bytes) -> bool:
    return len(data) >= 4 and data[0] == HEADER and (sum(data[2:-1]) & 0xFF) == data[-1]


def u16(v: int) -> list[int]:
    return [(v >> 8) & 0xFF, v & 0xFF]


def program_payload(slot_mask: int, chem_idx: int, mode: int, *, charge_ma: int,
                    discharge_ma: int, capacity_mah: int, charge_mv: int,
                    discharge_mv: int, term_ma: int, dis_term_ma: int,
                    rest_charge_min: int, rest_discharge_min: int, cycles: int,
                    cycle_mode: int, delta_v_mv: int, trickle_ma: int,
                    restart_mv: int, temp_c: int, time_min: int,
                    maxboost_mah: int, turbo: int, storage_mv: int,
                    eneloop: int) -> bytes:
    """Mirror of Nc3000SetBean.getDatas() with is214Type=true (MC5000)."""
    b = [0] * 40
    b[0] = slot_mask
    b[1] = mode
    b[2:4] = u16(charge_ma)
    b[4:6] = u16(discharge_ma)
    b[6:8] = u16(capacity_mah)
    b[8:10] = u16(charge_mv)
    b[10:12] = u16(discharge_mv)
    b[12:14] = u16(term_ma)
    b[14:16] = u16(dis_term_ma or discharge_ma)
    b[16:18] = u16(rest_charge_min)
    b[18:20] = u16(rest_discharge_min)
    b[20] = cycles & 0xFF
    b[21] = cycle_mode & 0xFF
    b[22] = delta_v_mv & 0xFF
    b[23] = (trickle_ma // 10) & 0xFF
    b[24:26] = u16(restart_mv)
    b[26] = (temp_c or 71) & 0xFF
    b[27:29] = u16(time_min)
    b[29:31] = u16(maxboost_mah or 300)
    b[31] = turbo & 0xFF
    b[32] = chem_idx
    b[33:35] = u16(storage_mv)
    b[35] = eneloop & 0xFF
    return bytes(b)


def parse_status(data: bytes) -> dict:
    d = data
    out = {
        "slot": MASK_SLOT.get(d[3], d[3]),
        "current_ma": (d[4] << 8) | d[5],
        "voltage_mv": (d[6] << 8) | d[7],
        "temp": (d[8] << 8) | d[9],
        "capacity_mah": (d[10] << 8) | d[11],
    }
    if len(d) >= 22:
        out["seconds"] = int.from_bytes(d[12:16], "big")
        out["ir_mohm"] = (d[16] << 8) | d[17]
        out["status"] = STATUSES.get(d[18], f"?{d[18]}")
        chem = d[21]
        out["chem"] = CHEM_BY_INDEX.get(chem, f"?{chem}")
        out["mode"] = MODE_NAMES.get(GROUP.get(chem, -1), {}).get(d[19], f"?{d[19]}")
        out["error"] = ERRORS.get(d[20], f"err{d[20]}")
        if len(d) > 23:
            out["pd"] = d[22]
    return out


def fmt_status(s: dict) -> str:
    t = s.get("seconds", 0)
    return (f"slot {s['slot']}: {s.get('status','?'):<11} {s.get('chem','?'):<9} "
            f"{s.get('mode','?'):<9} {s['voltage_mv']/1000:6.3f} V  "
            f"{s['current_ma']:5d} mA  {s['capacity_mah']:5d} mAh  "
            f"T={s['temp']:<4} IR={s.get('ir_mohm','-'):<5} "
            f"{t//3600:d}:{t%3600//60:02d}:{t%60:02d}  {s.get('error','')}")


class Charger:
    def __init__(self, address: str, verbose: bool = False):
        self.address = address
        self.verbose = verbose
        self.client = None
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._buf = b""
        self._last_write = 0.0

    async def __aenter__(self):
        from bleak import BleakClient
        self.client = BleakClient(self.address)
        await self.client.connect()
        await self.client.start_notify(CHAR_UUID, self._on_notify)
        if self.verbose:
            print(f"connected, MTU {self.client.mtu_size}", file=sys.stderr)
        # The first frame after connecting gets no reply (seen on macOS), so let
        # the link settle and send a throwaway status query before real commands.
        await asyncio.sleep(0.5)
        if self.verbose:
            print("  (warm-up)", file=sys.stderr)
        await self.request(CMD_STATUS, [SLOT_MASK[1]], timeout=1.5, retries=0)
        return self

    async def __aexit__(self, *exc):
        try:
            await self.client.stop_notify(CHAR_UUID)
        finally:
            await self.client.disconnect()

    def _on_notify(self, _sender, data: bytearray):
        # Reassemble frames that arrive split across notifications.
        self._buf += bytes(data)
        while len(self._buf) >= 2:
            if self._buf[0] != HEADER:
                self._buf = self._buf[1:]
                continue
            need = self._buf[1] + 2
            if len(self._buf) < need:
                return
            f, self._buf = self._buf[:need], self._buf[need:]
            if self.verbose:
                print(f"  <- {f.hex(' ')}{'' if check(f) else '  (BAD CHECKSUM)'}", file=sys.stderr)
            self.queue.put_nowait(f)

    async def send(self, data: bytes):
        # The app spaces writes >=150 ms apart and sends each frame in one write.
        gap = time.monotonic() - self._last_write
        if gap < 0.15:
            await asyncio.sleep(0.15 - gap)
        if self.verbose:
            print(f"  -> {data.hex(' ')}", file=sys.stderr)
        await self.client.write_gatt_char(CHAR_UUID, data, response=False)
        self._last_write = time.monotonic()

    async def request(self, cmd: int, payload, timeout: float = 3.0, retries: int = 1) -> bytes | None:
        for attempt in range(retries + 1):
            while not self.queue.empty():
                self.queue.get_nowait()
            if attempt and self.verbose:
                print("  (retry)", file=sys.stderr)
            await self.send(frame(cmd, payload))
            end = time.monotonic() + timeout
            while (left := end - time.monotonic()) > 0:
                try:
                    f = await asyncio.wait_for(self.queue.get(), left)
                except asyncio.TimeoutError:
                    break
                if len(f) > 2 and f[2] == cmd:
                    return f
        return None

    async def status(self, slot: int) -> dict | None:
        f = await self.request(CMD_STATUS, [SLOT_MASK[slot]])
        return parse_status(f) if f and check(f) else None


def slot_mask(arg: str) -> int:
    if arg == "all":
        return 0
    return SLOT_MASK[int(arg)]


async def cmd_scan(_a):
    from bleak import BleakScanner
    found = await BleakScanner.discover(timeout=8.0, return_adv=True)
    for dev, adv in sorted(found.values(), key=lambda x: -x[1].rssi):
        uuids = [u.lower() for u in adv.service_uuids or []]
        hit = SERVICE_UUID in uuids or any(k in (dev.name or "").lower() for k in ("mc5000", "charger", "skyrc"))
        if hit or _a.all:
            print(f"{dev.address}  rssi={adv.rssi:4d}  name={dev.name!r}  {'<-- likely' if hit else ''}")


async def cmd_status(a):
    async with Charger(a.address, a.verbose) as c:
        for s in (range(1, 5) if a.slot == "all" else [int(a.slot)]):
            st = await c.status(s)
            print(fmt_status(st) if st else f"slot {s}: no response")


async def cmd_watch(a):
    async with Charger(a.address, a.verbose) as c:
        while True:
            lines = []
            for s in range(1, 5):
                st = await c.status(s)
                lines.append(fmt_status(st) if st else f"slot {s}: no response")
            print("\033[2J\033[H" + time.strftime("%H:%M:%S") + "\n" + "\n".join(lines), flush=True)
            await asyncio.sleep(a.interval)


async def cmd_stop(a):
    async with Charger(a.address, a.verbose) as c:
        f = await c.request(CMD_STOP, [slot_mask(a.slot)])
        print(f"ack: {f.hex(' ')}  -> {'OK' if f[4] == 1 else 'FAILED'}" if f and len(f) > 4 else "no ack")


def build_start(a) -> bytes:
    chem_idx, chem_name, vmax, vdis, vstore = CHEMS[a.chem]
    group = GROUP[chem_idx]
    if a.mode not in MODES[group]:
        sys.exit(f"mode {a.mode!r} not valid for {chem_name}; choose from {list(MODES[group])}")
    mode = MODES[group][a.mode]
    charge_mv = a.charge_mv or vmax
    if charge_mv > vmax:
        sys.exit(f"refusing: charge voltage {charge_mv} mV is above the {chem_name} max of {vmax} mV")
    if a.current > MAX_CHARGE_MA or a.discharge > MAX_DISCHARGE_MA:
        sys.exit(f"refusing: currents capped at {MAX_CHARGE_MA} mA charge / {MAX_DISCHARGE_MA} mA discharge")
    if group == 1 and a.delta_v > 20:
        sys.exit("refusing: NiMH -dV above 20 mV makes no sense")
    payload = program_payload(
        slot_mask(a.slot), chem_idx, mode,
        charge_ma=a.current, discharge_ma=a.discharge, capacity_mah=a.capacity,
        charge_mv=charge_mv, discharge_mv=a.discharge_mv or vdis,
        term_ma=a.term, dis_term_ma=a.discharge_term,
        rest_charge_min=a.rest, rest_discharge_min=a.rest, cycles=a.cycles,
        cycle_mode=a.cycle_mode, delta_v_mv=a.delta_v, trickle_ma=a.trickle,
        restart_mv=a.restart_mv, temp_c=a.temp_cutoff, time_min=a.time_limit,
        maxboost_mah=300, turbo=0, storage_mv=a.storage_mv or vstore, eneloop=0)
    return frame(CMD_START, payload)


async def cmd_start(a):
    f = build_start(a)
    print(f"{a.chem} {a.mode} slot={a.slot} charge={a.current}mA discharge={a.discharge}mA")
    print(f"frame ({len(f)} bytes): {f.hex(' ')}")
    if not a.send:
        print("dry run - add --send to transmit")
        return
    if not a.address:
        sys.exit("--send needs -a/--address")
    async with Charger(a.address, a.verbose) as c:
        await c.send(f)
        end = time.monotonic() + 3
        while (left := end - time.monotonic()) > 0:
            try:
                r = await asyncio.wait_for(c.queue.get(), left)
            except asyncio.TimeoutError:
                break
            if r[2] == CMD_START:
                print(f"ack: {r.hex(' ')}  -> {'OK' if len(r) > 4 and r[4] == 1 else 'FAILED'}")
                break
        else:
            print("no ack (check the charger screen)")
        # The slot stays in standby for a few seconds (NiMH) to ~30 s (Li-ion)
        # while the charger detects the cell and measures IR, so poll until it starts.
        slots = [int(a.slot)] if a.slot != "all" else list(range(1, 5))
        deadline = time.monotonic() + 45
        while True:
            await asyncio.sleep(3)
            states = {s: await c.status(s) for s in slots}
            started = all(st and st.get("status") not in ("standby", "ready") for st in states.values())
            if started or time.monotonic() > deadline:
                break
            print("  waiting for the charger to start ...", file=sys.stderr)
        for s, st in states.items():
            print(fmt_status(st) if st else f"slot {s}: no response")
        if not started:
            print("not running yet - check the charger screen")


async def cmd_sysinfo(a):
    async with Charger(a.address, a.verbose) as c:
        for name, cmd, p in (("system settings", CMD_SYS_QUERY, [4]), ("PD status", CMD_PD_STATUS, [0])):
            f = await c.request(cmd, p)
            print(f"{name}: {f.hex(' ') if f else 'no response'}")


async def cmd_raw(a):
    data = bytes(int(x, 16) for x in a.hex)
    async with Charger(a.address, True) as c:
        await c.send(frame(data[0], data[1:]))
        await asyncio.sleep(a.wait)


def main():
    p = argparse.ArgumentParser(description="SkyRC MC5000 BLE control")
    p.add_argument("-v", "--verbose", action="store_true", help="print raw frames")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan"); s.add_argument("--all", action="store_true")
    for name in ("status", "watch", "stop", "start", "sysinfo", "raw"):
        sp = sub.add_parser(name)
        sp.add_argument("-a", "--address", required=name != "start")
        if name in ("status",):
            sp.add_argument("--slot", default="all")
        if name == "watch":
            sp.add_argument("--interval", type=float, default=2.0)
        if name == "stop":
            sp.add_argument("--slot", required=True, help="1-4 or all")
        if name == "raw":
            sp.add_argument("hex", nargs="+", help="cmd byte then payload bytes, hex")
            sp.add_argument("--wait", type=float, default=3.0)
        if name == "start":
            sp.add_argument("--slot", required=True, help="1-4 or all")
            sp.add_argument("--chem", required=True, choices=list(CHEMS))
            sp.add_argument("--mode", required=True,
                            help="charge|storage|discharge|cycle (Li), charge|refresh|breakin|discharge|cycle (Ni)")
            sp.add_argument("--current", type=int, required=True, help="charge current mA")
            sp.add_argument("--discharge", type=int, default=500, help="discharge current mA (default 500)")
            sp.add_argument("--capacity", type=int, default=3000, help="battery capacity mAh (default 3000)")
            sp.add_argument("--charge-mv", type=int, help="charge target mV (default = chemistry max)")
            sp.add_argument("--discharge-mv", type=int, help="discharge cutoff mV (default per chemistry)")
            sp.add_argument("--storage-mv", type=int, help="storage voltage mV (Li storage mode)")
            sp.add_argument("--term", type=int, default=100, help="charge termination current mA (default 100)")
            sp.add_argument("--discharge-term", type=int, default=100, help="discharge termination current mA")
            sp.add_argument("--rest", type=int, default=10, help="rest between cycle steps, minutes")
            sp.add_argument("--cycles", type=int, default=1)
            sp.add_argument("--cycle-mode", type=int, default=0, help="raw cycle mode index (default 0)")
            sp.add_argument("--delta-v", type=int, default=6, help="NiMH -dV mV (default 6)")
            sp.add_argument("--trickle", type=int, default=0, help="trickle mA (multiple of 10, default 0 = off)")
            sp.add_argument("--restart-mv", type=int, default=0, help="restart voltage mV (0 = off)")
            sp.add_argument("--temp-cutoff", type=int, default=60, help="cell temp cutoff C (default 60)")
            sp.add_argument("--time-limit", type=int, default=0, help="safety timer minutes (0 = charger default)")
            sp.add_argument("--send", action="store_true", help="actually transmit (default is dry run)")
    a = p.parse_args()
    fn = globals()[f"cmd_{a.cmd}"]
    try:
        asyncio.run(fn(a))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
