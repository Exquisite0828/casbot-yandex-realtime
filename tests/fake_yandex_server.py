"""Minimal local aiohttp WebSocket peer for Phase 6 integration tests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
from typing import Any

from aiohttp import WSMsgType, web


class FakeYandexRealtimeServer:
    """Capture client events and let tests script Yandex server events."""

    def __init__(
        self,
        *,
        auto_ready: bool | Callable[[int], bool] = True,
        duplicate_ready: bool = False,
        close_on_client_event: str | None = None,
    ) -> None:
        self._auto_ready = auto_ready
        self._duplicate_ready = duplicate_ready
        self._close_on_client_event = close_on_client_event
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._changed = asyncio.Event()
        self.url = ""
        self.connection_count = 0
        self.authorization_headers: list[str] = []
        self.client_events: list[tuple[int, dict[str, Any]]] = []
        self.websockets: list[web.WebSocketResponse] = []

    async def __aenter__(self) -> "FakeYandexRealtimeServer":
        app = web.Application()
        app.router.add_get("/v1/realtime", self._handle_websocket)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host="127.0.0.1", port=0)
        await self._site.start()
        server = self._site._server
        if server is None or not server.sockets:
            raise RuntimeError("fake WebSocket server did not bind a socket")
        port = int(server.sockets[0].getsockname()[1])
        self.url = f"http://127.0.0.1:{port}/v1/realtime"
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        errors: list[BaseException] = []
        try:
            for websocket in tuple(self.websockets):
                if websocket.closed:
                    continue
                try:
                    await websocket.close()
                except BaseException as error:
                    errors.append(error)
            if self._runner is not None:
                try:
                    await self._runner.cleanup()
                except BaseException as error:
                    errors.append(error)
        finally:
            self.websockets.clear()
            self._runner = None
            self._site = None

        if errors:
            raise errors[0]

    def _ready_for(self, connection_index: int) -> bool:
        if callable(self._auto_ready):
            return bool(self._auto_ready(connection_index))
        return self._auto_ready

    async def _handle_websocket(self, request: web.Request) -> web.WebSocketResponse:
        websocket = web.WebSocketResponse(autoclose=True)
        await websocket.prepare(request)
        connection_index = self.connection_count
        self.connection_count += 1
        self.authorization_headers.append(request.headers.get("Authorization", ""))
        self.websockets.append(websocket)
        self._notify()
        await websocket.send_json(
            {"type": "session.created", "session": {"id": f"session-{connection_index}"}}
        )
        async for message in websocket:
            if message.type is WSMsgType.TEXT:
                try:
                    payload = json.loads(message.data)
                except json.JSONDecodeError:
                    continue
                if not isinstance(payload, dict):
                    continue
                self.client_events.append((connection_index, payload))
                self._notify()
                event_type = payload.get("type")
                if event_type == "session.update" and self._ready_for(connection_index):
                    await websocket.send_json(
                        {
                            "type": "session.updated",
                            "session": payload.get("session", {}),
                        }
                    )
                    if self._duplicate_ready:
                        await websocket.send_json(
                            {
                                "type": "session.updated",
                                "session": payload.get("session", {}),
                            }
                        )
                if event_type == self._close_on_client_event:
                    await websocket.close(code=1011, message=b"scripted fault")
                    break
            elif message.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR}:
                break
        self._notify()
        return websocket

    def _notify(self) -> None:
        self._changed.set()
        self._changed = asyncio.Event()

    async def wait_for_connection(self, count: int = 1, timeout: float = 1.0) -> None:
        await self._wait_until(lambda: self.connection_count >= count, timeout)

    async def wait_for_client_event(
        self,
        event_type: str,
        *,
        connection_index: int | None = None,
        occurrence: int = 1,
        timeout: float = 1.0,
    ) -> dict[str, Any]:
        def find() -> dict[str, Any] | None:
            matches = [
                event
                for index, event in self.client_events
                if event.get("type") == event_type
                and (connection_index is None or index == connection_index)
            ]
            return matches[occurrence - 1] if len(matches) >= occurrence else None

        return await self._wait_until(find, timeout)

    async def send_json(
        self, payload: dict[str, Any], *, connection_index: int = -1
    ) -> None:
        await self.websockets[connection_index].send_json(payload)

    async def send_malformed(self, *, connection_index: int = -1) -> None:
        await self.websockets[connection_index].send_str("{not-json")

    async def disconnect(
        self, *, connection_index: int = -1, code: int = 1011
    ) -> None:
        await self.websockets[connection_index].close(
            code=code, message=b"scripted disconnect"
        )

    async def _wait_until(self, predicate: Callable[[], Any], timeout: float) -> Any:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            result = predicate()
            if result:
                return result
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError("fake server condition was not met")
            changed = self._changed
            await asyncio.wait_for(changed.wait(), timeout=remaining)
