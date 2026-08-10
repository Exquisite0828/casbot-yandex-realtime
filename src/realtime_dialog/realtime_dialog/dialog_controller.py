"""ROS-independent dialog lifecycle and stale-event suppression."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable, Protocol

from .adapters import AudioOutputAdapter, MicAdapter
from .ros_contract import (
    STATUS_CONNECTING,
    STATUS_ERROR,
    STATUS_IDLE,
    STATUS_LISTENING,
    STATUS_SPEAKING_TEXT,
)
from .yandex_realtime_client import RealtimeEvent, RealtimeEventKind


class RealtimeClient(Protocol):
    def set_event_handler(self, handler: Callable[[RealtimeEvent], object]) -> None: ...

    def set_generation(self, generation_id: int) -> None: ...

    async def connect(self, generation_id: int) -> None: ...

    async def close(self) -> None: ...

    async def send_audio(self, pcm: bytes) -> None: ...

    async def send_text(self, text: str) -> None: ...

    async def cancel_current_response(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ControllerResult:
    success: bool
    message: str


class DialogController:
    """Serialize commands while accepting asynchronous transport events."""

    def __init__(
        self,
        *,
        client: RealtimeClient,
        mic_adapter: MicAdapter,
        audio_output: AudioOutputAdapter,
        status_sink: Callable[[str], object],
        text_result_sink: Callable[[str], object],
    ) -> None:
        self._client = client
        self._mic = mic_adapter
        self._audio_output = audio_output
        self._status_sink = status_sink
        self._text_result_sink = text_result_sink
        self._state = STATUS_IDLE
        self._generation_id = 0
        self._command_lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._microphone_started = False
        self._accept_events = False
        self._client.set_event_handler(self.handle_event)

    @property
    def state(self) -> str:
        return self._state

    @property
    def generation_id(self) -> int:
        return self._generation_id

    def _set_state(self, state: str) -> None:
        if state == self._state:
            return
        self._state = state
        self._status_sink(state)

    def _advance_generation(self) -> int:
        self._generation_id += 1
        self._client.set_generation(self._generation_id)
        return self._generation_id

    async def start_session(self) -> ControllerResult:
        async with self._command_lock:
            return await self._start_session(start_microphone=True)

    async def _start_session(self, *, start_microphone: bool) -> ControllerResult:
        if self._state not in {STATUS_IDLE, STATUS_ERROR}:
            if start_microphone and not self._microphone_started:
                self._start_microphone()
            return ControllerResult(True, "session already active")

        self._loop = asyncio.get_running_loop()
        generation = self._advance_generation()
        self._accept_events = True
        self._set_state(STATUS_CONNECTING)
        try:
            await self._client.connect(generation)
            if start_microphone:
                self._start_microphone()
        except Exception as error:
            self._accept_events = False
            self._set_state(STATUS_ERROR)
            return ControllerResult(False, f"session start failed: {error}")
        return ControllerResult(True, "session started")

    def _start_microphone(self) -> None:
        if self._microphone_started:
            return
        self._mic.start(self._on_microphone_audio)
        self._microphone_started = True

    def _on_microphone_audio(self, pcm: bytes) -> None:
        """Bridge a potentially non-async adapter callback to the worker loop."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        generation = self._generation_id
        loop.call_soon_threadsafe(
            lambda: asyncio.create_task(
                self._send_microphone_audio(bytes(pcm), generation)
            )
        )

    async def _send_microphone_audio(self, pcm: bytes, generation: int) -> None:
        if generation != self._generation_id or self._state == STATUS_IDLE:
            return
        try:
            await self._client.send_audio(pcm)
        except Exception:
            if generation == self._generation_id:
                self._set_state(STATUS_ERROR)

    async def stop_session(self) -> ControllerResult:
        async with self._command_lock:
            self._accept_events = False
            self._advance_generation()
            if self._microphone_started:
                self._mic.stop()
                self._microphone_started = False
            try:
                await self._client.cancel_current_response()
                self._audio_output.flush()
                await self._client.close()
            except Exception as error:
                self._set_state(STATUS_ERROR)
                return ControllerResult(False, f"session stop failed: {error}")
            self._set_state(STATUS_IDLE)
            return ControllerResult(True, "session stopped")

    async def handle_text_input(self, text: str) -> ControllerResult:
        value = text.strip()
        if not value:
            return ControllerResult(False, "text input is empty")
        async with self._command_lock:
            if self._state in {STATUS_IDLE, STATUS_ERROR}:
                started = await self._start_session(start_microphone=False)
                if not started.success:
                    return started
            else:
                self._advance_generation()
                self._audio_output.flush()
                await self._client.cancel_current_response()
            try:
                await self._client.send_text(value)
            except Exception as error:
                self._set_state(STATUS_ERROR)
                return ControllerResult(False, f"text input failed: {error}")
            return ControllerResult(True, "text input accepted")

    async def handle_event(self, event: RealtimeEvent) -> None:
        """Drop every event that belongs to a superseded lifecycle generation."""
        if not self._accept_events or event.generation_id != self._generation_id:
            return

        if event.kind is RealtimeEventKind.SESSION_READY:
            self._set_state(STATUS_LISTENING)
        elif event.kind is RealtimeEventKind.RESPONSE_STARTED:
            self._set_state(STATUS_SPEAKING_TEXT)
        elif event.kind is RealtimeEventKind.ASSISTANT_TEXT:
            text = str(event.data.get("text", ""))
            if text:
                self._text_result_sink(text)
        elif event.kind is RealtimeEventKind.ASSISTANT_AUDIO:
            try:
                self._audio_output.write(
                    event.data["pcm"],
                    int(event.data["sample_rate"]),
                    event.generation_id,
                )
            except Exception:
                self._set_state(STATUS_ERROR)
        elif event.kind is RealtimeEventKind.RESPONSE_DONE:
            self._set_state(STATUS_LISTENING)
        elif event.kind is RealtimeEventKind.SPEECH_STARTED:
            if self._state == STATUS_SPEAKING_TEXT:
                self._advance_generation()
                self._audio_output.flush()
                await self._client.cancel_current_response()
                self._set_state(STATUS_LISTENING)
        elif event.kind is RealtimeEventKind.ERROR:
            self._set_state(STATUS_ERROR)
