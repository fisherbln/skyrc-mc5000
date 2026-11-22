"""Home Assistant WebSocket client, run on its own asyncio thread.

The UI only talks to it through Qt signals and the thread-safe call_* methods,
so nothing here ever blocks the window.
"""
from __future__ import annotations

import asyncio
import json
import logging
import ssl
import threading
from typing import Any

import websockets
from PySide6.QtCore import QObject, Signal

from .charger import ChargerEntities, find_charger

_LOG = logging.getLogger(__name__)


def websocket_url(base_url: str) -> str:
    url = base_url.strip().rstrip("/")
    if url.startswith("https://"):
        url = "wss://" + url[len("https://"):]
    elif url.startswith("http://"):
        url = "ws://" + url[len("http://"):]
    elif not url.startswith(("ws://", "wss://")):
        url = "ws://" + url
    if not url.endswith("/api/websocket"):
        url += "/api/websocket"
    return url


def apply_entity_diff(states: dict[str, dict[str, Any]], event: dict[str, Any]) -> set[str]:
    """Apply a subscribe_entities event (compressed format) to a state cache.

    Returns the entity IDs that changed.
    """
    changed: set[str] = set()
    for eid, full in (event.get("a") or {}).items():
        states[eid] = {"state": full.get("s"), "attributes": dict(full.get("a") or {})}
        changed.add(eid)
    for eid, diff in (event.get("c") or {}).items():
        cur = states.setdefault(eid, {"state": None, "attributes": {}})
        plus = diff.get("+") or {}
        if "s" in plus:
            cur["state"] = plus["s"]
        if "a" in plus:
            cur["attributes"].update(plus["a"])
        for key in (diff.get("-") or {}).get("a", []):
            cur["attributes"].pop(key, None)
        changed.add(eid)
    for eid in event.get("r") or []:
        states.pop(eid, None)
        changed.add(eid)
    return changed


class HaClient(QObject):
    """Connects, finds the MC5000's entities and streams their states."""

    status = Signal(str, str)  # level ("ok"/"connecting"/"error"), message
    charger_found = Signal(object)  # ChargerEntities
    states_changed = Signal(object)  # dict entity_id -> {"state", "attributes"}
    service_failed = Signal(str)  # message for the user
    response = Signal(str, object, str)  # key, result (None on failure), error message

    def __init__(self) -> None:
        super().__init__()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._task: asyncio.Future | None = None
        self._ws: Any = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future] = {}
        self.states: dict[str, dict[str, Any]] = {}
        self.charger = ChargerEntities()

    # ----- called from the UI thread -----

    def connect_to(self, base_url: str, token: str, verify_tls: bool = True) -> None:
        def start() -> None:
            if self._task:
                self._task.cancel()
            self._task = asyncio.ensure_future(self._run(base_url, token, verify_tls))

        self._loop.call_soon_threadsafe(start)

    def call_service(self, domain: str, service: str, data: dict[str, Any] | None = None,
                     entity_id: str | None = None) -> None:
        msg: dict[str, Any] = {
            "type": "call_service",
            "domain": domain,
            "service": service,
            "service_data": dict(data or {}),
        }
        if entity_id:
            msg["target"] = {"entity_id": entity_id}
        asyncio.run_coroutine_threadsafe(self._call(msg), self._loop)

    def request(self, key: str, msg: dict[str, Any], timeout: float = 60) -> None:
        """Send any WebSocket command; the answer arrives on the response signal."""
        async def go() -> None:
            try:
                result = await self._request(msg, timeout)
            except Exception as err:  # noqa: BLE001
                self.response.emit(key, None, str(err) or err.__class__.__name__)
                return
            if result.get("success"):
                self.response.emit(key, result.get("result"), "")
            else:
                self.response.emit(key, None, (result.get("error") or {}).get("message", "failed"))

        asyncio.run_coroutine_threadsafe(go(), self._loop)

    def history(self, key: str, entity_ids: list[str], start: float, end: float) -> None:
        from .history import iso

        self.request(key, {
            "type": "history/history_during_period",
            "start_time": iso(start),
            "end_time": iso(end),
            "entity_ids": entity_ids,
            "minimal_response": True,
            "no_attributes": True,
            "significant_changes_only": False,
        })

    def press(self, entity_id: str) -> None:
        self.call_service("button", "press", entity_id=entity_id)

    def shutdown(self) -> None:
        """Close the connection and wait for the network thread to finish."""
        if not self._thread.is_alive():
            return

        def stop() -> None:
            if self._task:
                self._task.cancel()
            self._loop.call_later(0.2, self._loop.stop)

        self._loop.call_soon_threadsafe(stop)
        self._thread.join(timeout=3)

    # ----- asyncio side -----

    async def _call(self, msg: dict[str, Any]) -> None:
        try:
            result = await self._request(msg, timeout=30)
        except Exception as err:  # noqa: BLE001 - shown to the user
            self.service_failed.emit(str(err) or err.__class__.__name__)
            return
        if not result.get("success", False):
            error = result.get("error") or {}
            self.service_failed.emit(error.get("message") or "Home Assistant refused the request")

    async def _request(self, msg: dict[str, Any], timeout: float = 15) -> dict[str, Any]:
        if self._ws is None:
            raise ConnectionError("Not connected to Home Assistant")
        msg_id = self._next_id
        self._next_id += 1
        fut = self._loop.create_future()
        self._pending[msg_id] = fut
        await self._ws.send(json.dumps({"id": msg_id, **msg}))
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(msg_id, None)

    async def _run(self, base_url: str, token: str, verify_tls: bool) -> None:
        url = websocket_url(base_url)
        backoff = 2.0
        while True:
            self.status.emit("connecting", f"Connecting to {base_url}…")
            try:
                await self._session(url, token, verify_tls)
                backoff = 2.0
            except asyncio.CancelledError:
                raise
            except PermissionError as err:
                self.status.emit("error", str(err))
                return  # a bad token won't fix itself
            except Exception as err:  # noqa: BLE001
                _LOG.debug("Connection failed", exc_info=True)
                self.status.emit("error", f"Can't reach Home Assistant: {err or err.__class__.__name__}")
            finally:
                self._ws = None
                for fut in self._pending.values():
                    if not fut.done():
                        fut.set_exception(ConnectionError("Connection to Home Assistant lost"))
                self._pending.clear()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    async def _session(self, url: str, token: str, verify_tls: bool) -> None:
        ssl_ctx: ssl.SSLContext | None = None
        if url.startswith("wss://"):
            ssl_ctx = ssl.create_default_context()
            if not verify_tls:
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE
        async with websockets.connect(
            url, ssl=ssl_ctx, max_size=32 * 1024 * 1024, open_timeout=10,
            ping_interval=20, ping_timeout=20,
        ) as ws:
            hello = json.loads(await ws.recv())
            if hello.get("type") != "auth_required":
                raise ConnectionError("That address isn't a Home Assistant server")
            await ws.send(json.dumps({"type": "auth", "access_token": token}))
            auth = json.loads(await ws.recv())
            if auth.get("type") != "auth_ok":
                raise PermissionError(
                    "Home Assistant rejected the access token. Create a new one "
                    "under your profile → Security → Long-lived access tokens."
                )
            self._ws = ws
            reader = asyncio.ensure_future(self._reader(ws))
            try:
                registry = await self._request({"type": "config/entity_registry/list"}, 30)
                if not registry.get("success"):
                    raise ConnectionError(
                        (registry.get("error") or {}).get("message", "Couldn't read the entity list")
                    )
                self.charger = find_charger(registry["result"])
                self.charger_found.emit(self.charger)
                if not self.charger.found:
                    self.status.emit(
                        "error", "Connected, but no SkyRC MC5000 is set up in Home Assistant."
                    )
                    await reader
                    return
                self.states = {}
                sub = await self._request(
                    {"type": "subscribe_entities", "entity_ids": self.charger.all_ids()}
                )
                if not sub.get("success"):
                    raise ConnectionError("Couldn't subscribe to the charger's entities")
                self.status.emit("ok", "Connected to Home Assistant")
                await reader
            finally:
                reader.cancel()

    async def _reader(self, ws: Any) -> None:
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") == "result":
                fut = self._pending.get(msg.get("id"))
                if fut and not fut.done():
                    fut.set_result(msg)
            elif msg.get("type") == "event" and "event" in msg:
                event = msg["event"]
                if isinstance(event, dict) and ("a" in event or "c" in event or "r" in event):
                    apply_entity_diff(self.states, event)
                    self.states_changed.emit({k: dict(v) for k, v in self.states.items()})
        raise ConnectionError("Home Assistant closed the connection")
