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
    states["button.skyrc_mc5000_4036_stop_all_slots"] = {"s": "unknown", "a": {}}
    return states


class FakeHA:
    """Runs on its own thread; records service calls and can push state changes."""

    def __init__(self) -> None:
        self.states = sample_states()
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
            elif kind == "call_service":
                self.calls.append(msg)
                if self.fail_services:
                    await ws.send(json.dumps({"id": mid, "type": "result", "success": False,
                                              "error": {"code": "service_validation_error",
                                                        "message": "Invalid MC5000 program: nope"}}))
                else:
                    await ws.send(json.dumps({"id": mid, "type": "result", "success": True,
                                              "result": {}}))

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
