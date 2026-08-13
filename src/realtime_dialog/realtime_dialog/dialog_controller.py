"""ROS-independent dialog lifecycle and stale-event suppression."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol

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
        microphone_queue_chunks: int = 50,
    ) -> None:
        if microphone_queue_chunks <= 0:
            raise ValueError("microphone_queue_chunks must be greater than zero")
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
        self._microphone_capture_id = 0
        self._microphone_queue: asyncio.Queue[tuple[int, bytes]] = asyncio.Queue(
            maxsize=microphone_queue_chunks
        )
        self._microphone_sender_task: asyncio.Task[None] | None = None
        self._microphone_dropped_chunks = 0
        self._accept_events = False
        self._lifecycle_lock = asyncio.Lock()
        self._failure_task: asyncio.Task[None] | None = None
        self._last_error: str | None = None
        self._client.set_event_handler(self.handle_event)
        self._audio_output.set_generation(self._generation_id)

    @property
    def state(self) -> str:
        return self._state

    @property
    def generation_id(self) -> int:
        return self._generation_id

    @property
    def microphone_sender_task(self) -> asyncio.Task[None] | None:
        return self._microphone_sender_task

    @property
    def microphone_queue_size(self) -> int:
        return self._microphone_queue.qsize()

    @property
    def microphone_dropped_chunks(self) -> int:
        return self._microphone_dropped_chunks

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def _set_state(self, state: str) -> None:
        if state == self._state:
            return
        self._state = state
        self._status_sink(state)

    def _clear_microphone_queue(self) -> None:
        while True:
            try:
                self._microphone_queue.get_nowait()
            except asyncio.QueueEmpty:
                return

    def _advance_generation(self) -> int:
        self._generation_id += 1
        self._clear_microphone_queue()
        self._client.set_generation(self._generation_id)
        self._audio_output.set_generation(self._generation_id)
        return self._generation_id

    async def start_session(self) -> ControllerResult:
        return await self._run_command(
            lambda: self._start_session(start_microphone=True)
        )

    async def _run_command(
        self,
        transition: Callable[[], Awaitable[ControllerResult]],
    ) -> ControllerResult:
        async with self._command_lock:
            while True:
                await self._await_failure_cleanup()
                pending_failure: asyncio.Task[None] | None = None
                async with self._lifecycle_lock:
                    failure_task = self._failure_task
                    if (
                        failure_task is None
                        or failure_task.done()
                        or failure_task is asyncio.current_task()
                    ):
                        return await transition()
                    pending_failure = failure_task
                await asyncio.gather(pending_failure, return_exceptions=True)

    async def _await_failure_cleanup(self) -> None:
        failure_task = self._failure_task
        if failure_task is not None and failure_task is not asyncio.current_task():
            await asyncio.gather(failure_task, return_exceptions=True)

    async def _start_session(self, *, start_microphone: bool) -> ControllerResult:
        if self._state not in {STATUS_IDLE, STATUS_ERROR}:
            if start_microphone:
                try:
                    await self._start_microphone_uplink()
                except Exception as error:
                    self._set_state(STATUS_ERROR)
                    return ControllerResult(
                        False, f"microphone start failed: {error}"
                    )
            return ControllerResult(True, "session already active")

        self._loop = asyncio.get_running_loop()
        generation = self._advance_generation()
        self._accept_events = True
        self._last_error = None
        self._set_state(STATUS_CONNECTING)
        try:
            await self._client.connect(generation)
            if start_microphone:
                await self._start_microphone_uplink()
        except Exception as error:
            message = f"session start failed: {error}"
            await self._fail_active_session_owned(message, generation)
            return ControllerResult(False, message)
        return ControllerResult(True, "session started")

    async def _start_microphone_uplink(self) -> None:
        sender = self._microphone_sender_task
        if sender is None or sender.done():
            self._microphone_sender_task = asyncio.create_task(
                self._microphone_sender(), name="yandex-microphone-sender"
            )
        if self._microphone_started:
            return
        self._microphone_capture_id += 1
        capture_id = self._microphone_capture_id
        self._microphone_started = True
        try:
            self._mic.start(
                self._on_microphone_audio,
                lambda error: self._on_microphone_runtime_error(
                    error,
                    capture_id,
                ),
            )
        except BaseException:
            self._microphone_started = False
            sender = self._microphone_sender_task
            self._microphone_sender_task = None
            if sender is not None:
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)
            raise

    def _on_microphone_audio(self, pcm: bytes) -> None:
        """Bridge the arecord reader thread to one bounded async sender."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        generation = self._generation_id
        payload = bytes(pcm)
        loop.call_soon_threadsafe(
            self._offer_microphone_audio, payload, generation
        )

    def _on_microphone_runtime_error(
        self,
        error: Exception,
        capture_id: int,
    ) -> None:
        """Bridge a capture-thread failure onto the controller event loop."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        generation = self._generation_id
        loop.call_soon_threadsafe(
            self._handle_microphone_runtime_error,
            error,
            generation,
            capture_id,
        )

    def _handle_microphone_runtime_error(
        self,
        _error: Exception,
        generation: int,
        capture_id: int,
    ) -> None:
        if (
            generation != self._generation_id
            or capture_id != self._microphone_capture_id
            or not self._microphone_started
            or not self._accept_events
        ):
            return
        self._schedule_failure(
            f"microphone capture failed: {type(_error).__name__}",
            generation,
        )

    def _offer_microphone_audio(self, pcm: bytes, generation: int) -> None:
        if (
            not self._microphone_started
            or generation != self._generation_id
            or not self._accept_events
            or self._state in {STATUS_IDLE, STATUS_ERROR}
        ):
            return
        try:
            self._microphone_queue.put_nowait((generation, pcm))
        except asyncio.QueueFull:
            try:
                self._microphone_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self._microphone_dropped_chunks += 1
            self._microphone_queue.put_nowait((generation, pcm))

    async def _microphone_sender(self) -> None:
        while True:
            generation, pcm = await self._microphone_queue.get()
            if (
                generation != self._generation_id
                or not self._microphone_started
                or not self._accept_events
            ):
                continue
            try:
                await self._client.send_audio(pcm)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if generation == self._generation_id and self._accept_events:
                    self._schedule_failure(
                        f"microphone send failed: {error}",
                        generation,
                    )
                    return

    async def _stop_microphone_uplink(self) -> None:
        stop_error: Exception | None = None
        if self._microphone_started:
            self._microphone_started = False
            try:
                self._mic.stop()
            except Exception as error:
                stop_error = error
        sender = self._microphone_sender_task
        self._microphone_sender_task = None
        current = asyncio.current_task()
        if sender is not None and sender is not current:
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
        self._clear_microphone_queue()
        if stop_error is not None:
            raise stop_error

    async def _restart_microphone_sender(self) -> None:
        """Cancel any old-generation in-flight send, then resume capture uplink."""
        sender = self._microphone_sender_task
        self._microphone_sender_task = None
        if sender is not None:
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
        self._clear_microphone_queue()
        if self._microphone_started and self._accept_events:
            self._microphone_sender_task = asyncio.create_task(
                self._microphone_sender(), name="yandex-microphone-sender"
            )

    async def stop_session(self) -> ControllerResult:
        return await self._run_command(self._stop_session_owned)

    async def _stop_session_owned(self) -> ControllerResult:
        self._accept_events = False
        self._advance_generation()
        self._audio_output.flush()
        errors: list[str] = []
        try:
            await self._stop_microphone_uplink()
        except Exception as error:
            errors.append(f"microphone stop failed: {error}")
        try:
            await self._client.cancel_current_response()
        except Exception as error:
            errors.append(f"cancel failed: {error}")
        try:
            await self._client.close()
        except Exception as error:
            errors.append(f"close failed: {error}")
        if errors:
            self._set_state(STATUS_ERROR)
            return ControllerResult(False, f"session stop failed: {'; '.join(errors)}")
        self._set_state(STATUS_IDLE)
        return ControllerResult(True, "session stopped")

    async def handle_text_input(self, text: str) -> ControllerResult:
        value = text.strip()
        if not value:
            return ControllerResult(False, "text input is empty")
        return await self._run_command(
            lambda: self._handle_text_input_owned(value)
        )

    async def _handle_text_input_owned(self, value: str) -> ControllerResult:
        if self._state in {STATUS_IDLE, STATUS_ERROR}:
            started = await self._start_session(start_microphone=False)
            if not started.success:
                return started
        else:
            self._advance_generation()
            self._audio_output.flush()
            await self._restart_microphone_sender()
            try:
                await self._client.cancel_current_response()
            except Exception as error:
                message = f"text replacement cancel failed: {error}"
                await self._fail_active_session_owned(
                    message,
                    self._generation_id,
                )
                return ControllerResult(False, message)
        try:
            await self._client.send_text(value)
        except Exception as error:
            message = f"text input failed: {error}"
            await self._fail_active_session_owned(message, self._generation_id)
            return ControllerResult(False, message)
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
                self._schedule_failure(
                    "audio output rejected response PCM",
                    event.generation_id,
                )
        elif event.kind is RealtimeEventKind.RESPONSE_DONE:
            self._set_state(STATUS_LISTENING)
        elif event.kind is RealtimeEventKind.SPEECH_STARTED:
            await self._handle_speech_started(event)
        elif event.kind in {
            RealtimeEventKind.ERROR,
            RealtimeEventKind.TRANSPORT_CLOSED,
        }:
            self._schedule_failure(
                str(event.data.get("message", "active session failed")),
                event.generation_id,
            )

    async def _handle_speech_started(self, event: RealtimeEvent) -> None:
        async with self._lifecycle_lock:
            if (
                not self._accept_events
                or event.generation_id != self._generation_id
                or self._state != STATUS_SPEAKING_TEXT
            ):
                return
            self._advance_generation()
            self._audio_output.flush()
            await self._restart_microphone_sender()
            try:
                await self._client.cancel_current_response()
            except Exception as error:
                self._schedule_failure(
                    f"interruption cancel failed: {error}",
                    self._generation_id,
                )
                return
            self._set_state(STATUS_LISTENING)

    def _schedule_failure(self, message: str, generation: int) -> None:
        if generation != self._generation_id or not self._accept_events:
            return
        failure_task = self._failure_task
        if failure_task is not None and not failure_task.done():
            return
        task = asyncio.create_task(
            self._run_failure_cleanup(
                message,
                generation,
            ),
            name="dialog-session-failure-cleanup",
        )
        self._failure_task = task

        def clear(done: asyncio.Task[None]) -> None:
            if self._failure_task is done:
                self._failure_task = None

        task.add_done_callback(clear)

    async def _run_failure_cleanup(
        self,
        message: str,
        generation: int,
    ) -> None:
        async with self._lifecycle_lock:
            await self._fail_active_session_owned(
                message,
                generation,
            )

    async def _fail_active_session_owned(
        self,
        message: str,
        generation: int,
    ) -> None:
        """Invalidate and clean the current failed lifecycle exactly once."""
        if generation != self._generation_id:
            return
        if not self._accept_events:
            return
        self._last_error = message
        self._accept_events = False
        self._advance_generation()
        self._audio_output.flush()
        cleanup_errors: list[str] = []
        try:
            await self._stop_microphone_uplink()
        except Exception as error:
            cleanup_errors.append(f"microphone stop failed: {error}")
        try:
            await self._client.close()
        except Exception as error:
            cleanup_errors.append(f"close failed: {error}")
        if cleanup_errors:
            self._last_error = f"{message}; {'; '.join(cleanup_errors)}"
        self._set_state(STATUS_ERROR)
