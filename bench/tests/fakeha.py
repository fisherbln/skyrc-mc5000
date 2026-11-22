"""A small stand-in for Home Assistant's WebSocket API, for tests and previews."""
from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

import websockets

TOKEN = "test-token"
ADDR = "AA:BB:CC:DD:40:36"
ENTRY = "entry123"

SENSOR_KEYS = ["status", "voltage", "current", "capacity", "elapsed", "resistance",
               "operation", "chemistry", "error"]


SETTING_ENTITIES = {
    "backlight": "select.skyrc_mc5000_4036_lcd_backlight",
    "screen_timeout": "select.skyrc_mc5000_4036_screen_timeout",
    "keypress_beep": "select.skyrc_mc5000_4036_keypress_beep",
    "notify_beep": "select.skyrc_mc5000_4036_notify_beep",
    "completion_tone": "select.skyrc_mc5000_4036_completion_tone",
}
BEEPS = ["off", "low", "medium", "high"]


def registry() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for slot in (1, 2, 3, 4):
        for key in SENSOR_KEYS:
            out.append({"entity_id": f"sensor.skyrc_mc5000_4036_slot_{slot}_{key}",
                        "platform": "skyrc_mc5000", "unique_id": f"{ADDR}_slot{slot}_{key}",
                        "config_entry_id": ENTRY})
        out.append({"entity_id": f"button.skyrc_mc5000_4036_stop_slot_{slot}",
                    "platform": "skyrc_mc5000", "unique_id": f"{ADDR}_slot{slot}_stop",
                    "config_entry_id": ENTRY})
        out.append({"entity_id": f"button.skyrc_mc5000_4036_start_slot_{slot}",
                    "platform": "skyrc_mc5000", "unique_id": f"{ADDR}_slot{slot}_start",
                    "config_entry_id": ENTRY})
    out.append({"entity_id": "button.skyrc_mc5000_4036_stop_all_slots",
                "platform": "skyrc_mc5000", "unique_id": f"{ADDR}_stop_all",
                "config_entry_id": ENTRY})
    out.append({"entity_id": "switch.skyrc_mc5000_4036_connection",
                "platform": "skyrc_mc5000", "unique_id": f"{ADDR}_connection",
                "config_entry_id": ENTRY})
    for key, eid in SETTING_ENTITIES.items():
        out.append({"entity_id": eid, "platform": "skyrc_mc5000",
                    "unique_id": f"{ADDR}_setting_{key}", "config_entry_id": ENTRY})
    for key in ("voltage", "current", "power", "energy", "runtime"):
        out.append({"entity_id": f"sensor.skyrc_mc5000_4036_usb_c_{key}",
                    "platform": "skyrc_mc5000", "unique_id": f"{ADDR}_usb_{key}",
                    "config_entry_id": ENTRY})
    out.append({"entity_id": "light.kitchen", "platform": "hue", "unique_id": "x",
                "config_entry_id": "other"})
    return out


def slot_states(slot: int, **values: Any) -> dict[str, dict[str, Any]]:
    units = {"voltage": "V", "current": "A", "capacity": "mAh", "elapsed": "min",
             "resistance": "mΩ"}
    out = {}
    for key in SENSOR_KEYS:
        value = values.get(key, "unknown")
        attrs = {"unit_of_measurement": units[key]} if key in units else {}
        out[f"sensor.skyrc_mc5000_4036_slot_{slot}_{key}"] = {"s": str(value), "a": attrs}
    return out


def sample_states() -> dict[str, dict[str, Any]]:
    states: dict[str, dict[str, Any]] = {}
    states.update(slot_states(1, status="charging", voltage=1.412, current=1.0, capacity=812,
                              elapsed=47.5, resistance=38, operation="charge",
                              chemistry="eneloop", error="none"))
    states.update(slot_states(2, status="standby", voltage=0.0, current=0.0, capacity=0,
                              elapsed=0, resistance="unknown", operation="discharge",
                              chemistry="nimh", error="connection_break"))
    states.update(slot_states(3, status="done", voltage=3.802, current=0.0, capacity=342,
                              elapsed=18.2, resistance=24, operation="storage",
                              chemistry="liion", error="none"))
    states.update(slot_states(4, status="standby", voltage=0.0, current=0.0, capacity=0,
                              elapsed=0, operation="charge", chemistry="nimh", error="none"))
    states["switch.skyrc_mc5000_4036_connection"] = {"s": "on", "a": {}}
    for key, (value, options) in {
        "backlight": ("high", ["low", "medium", "high"]),
        "screen_timeout": ("off", ["off", "1_min", "3_min", "5_min", "10_min", "15_min",
                                   "30_min", "60_min", "120_min"]),
        "keypress_beep": ("off", BEEPS),
        "notify_beep": ("low", BEEPS),
        "completion_tone": ("once", ["once", "repeat"]),
    }.items():
        states[SETTING_ENTITIES[key]] = {"s": value, "a": {"options": options}}
    for key, value in {"voltage": 15.264, "current": 2.17, "power": 33.12,
                       "energy": 0.23, "runtime": 47}.items():
        states[f"sensor.skyrc_mc5000_4036_usb_c_{key}"] = {"s": str(value), "a": {}}
    states["button.skyrc_mc5000_4036_stop_all_slots"] = {"s": "unknown", "a": {}}
    return states


def _sensor(slot: int, key: str) -> str:
    return f"sensor.skyrc_mc5000_4036_slot_{slot}_{key}"


def sample_history(now: float) -> dict[str, list[dict[str, Any]]]:
    """Three runs: an EBL discharge that ends in a connection break (slot 2,
    two days ago), a Li-ion storage top-up (slot 3, yesterday) and the Eneloop
    charge still running on slot 1."""
    h: dict[str, list[dict[str, Any]]] = {}

    def add(slot: int, key: str, t: float, value: Any) -> None:
        h.setdefault(_sensor(slot, key), []).append({"s": str(value), "lu": t})

    # Slot 2: 0.5 A discharge for 8355 s, 1160 mAh, then the cell cuts out.
    t0 = now - 2 * 86400
    add(2, "status", t0 - 600, "standby")
    add(2, "error", t0 - 600, "none")
    add(2, "chemistry", t0 - 600, "nimh")
    add(2, "operation", t0, "discharge")
    add(2, "status", t0, "discharging")
    for i in [*range(0, 8355, 60), 8355]:
        frac = i / 8355
        add(2, "voltage", t0 + i, round(1.49 - 0.03 * frac - (0.02 if frac > 0.97 else 0), 3))
        add(2, "current", t0 + i, 0.5)
        add(2, "capacity", t0 + i, round(1160 * frac))
        add(2, "elapsed", t0 + i, round(i / 60, 2))
    # An HA restart in the middle must not split the run.
    add(2, "status", t0 + 4000, "unavailable")
    add(2, "status", t0 + 4010, "discharging")
    end = t0 + 8360
    add(2, "status", end, "standby")
    add(2, "error", end, "connection_break")
    add(2, "capacity", end, 0)
    add(2, "voltage", end, 0.0)
    add(2, "current", end, 0.0)
    # Slot 3: storage top-up, charging 3.66 -> 3.80 V, done after 18 min.
    t1 = now - 86400
    add(3, "chemistry", t1 - 60, "liion")
    add(3, "operation", t1, "storage")
    add(3, "status", t1, "charging")
    for i in range(0, 1093, 30):
        add(3, "voltage", t1 + i, round(3.70 + 0.10 * i / 1092, 3))
        add(3, "current", t1 + i, 1.0 if i < 900 else 0.4)
        add(3, "capacity", t1 + i, round(342 * i / 1092))
        add(3, "elapsed", t1 + i, round(i / 60, 2))
    add(3, "status", t1 + 1095, "done")
    # Slot 1: Eneloop charge running for 47.5 min.
    t2 = now - 2850
    add(1, "chemistry", t2, "eneloop")
    add(1, "operation", t2, "charge")
    add(1, "status", t2, "charging")
    for i in range(0, 2851, 30):
        add(1, "voltage", t2 + i, round(1.30 + 0.112 * i / 2850, 3))
        add(1, "current", t2 + i, 1.0)
        add(1, "capacity", t2 + i, round(812 * i / 2850))
        add(1, "elapsed", t2 + i, round(i / 60, 2))
    return h


class FakeHA:
    """Runs on its own thread; records service calls and can push state changes."""

    def __init__(self) -> None:
        import time as _time

        self.states = sample_states()
        self.history = sample_history(_time.time())
        self.calls: list[dict[str, Any]] = []
        self.fail_services = False
        self._loop = asyncio.new_event_loop()
        self._clients: list[tuple[Any, int]] = []
        self.port = 0
        ready = threading.Event()
        threading.Thread(target=self._serve, args=(ready,), daemon=True).start()
        ready.wait(5)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _serve(self, ready: threading.Event) -> None:
        asyncio.set_event_loop(self._loop)

        async def start() -> None:
            server = await websockets.serve(self._handler, "127.0.0.1", 0)
            self.port = server.sockets[0].getsockname()[1]
            ready.set()

        self._loop.run_until_complete(start())
        self._loop.run_forever()

    async def _handler(self, ws: Any) -> None:
        await ws.send(json.dumps({"type": "auth_required"}))
        auth = json.loads(await ws.recv())
        if auth.get("access_token") != TOKEN:
            await ws.send(json.dumps({"type": "auth_invalid", "message": "bad"}))
            return
        await ws.send(json.dumps({"type": "auth_ok"}))
        async for raw in ws:
            msg = json.loads(raw)
            mid, kind = msg.get("id"), msg.get("type")
            if kind == "config/entity_registry/list":
                await ws.send(json.dumps({"id": mid, "type": "result", "success": True,
                                          "result": registry()}))
            elif kind == "subscribe_entities":
                await ws.send(json.dumps({"id": mid, "type": "result", "success": True,
                                          "result": None}))
                wanted = set(msg.get("entity_ids") or self.states)
                await ws.send(json.dumps({"id": mid, "type": "event", "event": {
                    "a": {k: v for k, v in self.states.items() if k in wanted}}}))
                self._clients.append((ws, mid))
            elif kind == "history/history_during_period":
                await ws.send(json.dumps({"id": mid, "type": "result", "success": True,
                                          "result": self._history(msg)}))
            elif kind == "call_service":
                self.calls.append(msg)
                if self.fail_services:
                    await ws.send(json.dumps({"id": mid, "type": "result", "success": False,
                                              "error": {"code": "service_validation_error",
                                                        "message": "Invalid MC5000 program: nope"}}))
                else:
                    await ws.send(json.dumps({"id": mid, "type": "result", "success": True,
                                              "result": {}}))

    def _history(self, msg: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        from datetime import datetime

        start = datetime.fromisoformat(msg["start_time"]).timestamp()
        end = datetime.fromisoformat(msg["end_time"]).timestamp()
        out = {}
        for eid in msg["entity_ids"]:
            items = sorted(self.history.get(eid, []), key=lambda i: i["lu"])
            before = [i for i in items if i["lu"] < start]
            inside = [i for i in items if start <= i["lu"] <= end]
            if before:
                inside.insert(0, {"s": before[-1]["s"], "lu": start})
            if inside:
                out[eid] = inside
        return out

    def push(self, changes: dict[str, str]) -> None:
        """Change entity states and tell subscribers (compressed format)."""
        async def send() -> None:
            diff = {}
            for eid, state in changes.items():
                self.states.setdefault(eid, {"s": None, "a": {}})["s"] = state
                diff[eid] = {"+": {"s": state}}
            for ws, sub in self._clients:
                await ws.send(json.dumps({"id": sub, "type": "event", "event": {"c": diff}}))

        asyncio.run_coroutine_threadsafe(send(), self._loop).result(5)
