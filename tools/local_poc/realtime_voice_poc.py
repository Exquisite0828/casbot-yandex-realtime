#!/usr/bin/env python3
"""Minimal local Yandex Realtime Voice proof of concept.

The API key is read only from ``YANDEX_API_KEY`` and is never logged or written.
Microphone and speaker audio stay in memory as raw PCM16 mono samples.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
from collections import Counter
from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any


SHARED_PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src" / "realtime_dialog"
if str(SHARED_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_PACKAGE_ROOT))

from realtime_dialog.yandex_realtime_client import (  # noqa: E402
    CURRENT_ENDPOINT,
    PRIMARY_MODEL,
    build_audio_append,
    build_barge_in_events,
    build_session_update,
    build_websocket_url,
    model_label,
    redact_text,
    resolve_model_uri,
)


BYTES_PER_FRAME = 2  # signed PCM16 mono
LOG = logging.getLogger("yandex_realtime_poc")


@dataclass(frozen=True, slots=True)
class ResponseSnapshot:
    response_id: str | None
    item_id: str | None
    content_index: int


class ResponseState:
    """Accept current response audio and reject deltas after interruption."""

    def __init__(self) -> None:
        self.response_id: str | None = None
        self.item_id: str | None = None
        self.content_index = 0
        self.generating = False
        self._rejected: set[str] = set()

    def start(self, response_id: str) -> None:
        self.response_id = response_id
        self.item_id = None
        self.content_index = 0
        self.generating = True

    def accept_audio(
        self, response_id: str | None, item_id: str | None, content_index: int
    ) -> bool:
        if response_id and response_id in self._rejected:
            return False
        if self.response_id and response_id and response_id != self.response_id:
            return False
        if response_id and not self.response_id:
            self.response_id = response_id
            self.generating = True
        if item_id:
            self.item_id = item_id
        self.content_index = content_index
        return True

    def finish(self, response_id: str | None) -> None:
        if not response_id or response_id == self.response_id:
            self.generating = False

    def interrupt(self) -> ResponseSnapshot:
        response_to_cancel = self.response_id if self.generating else None
        snapshot = ResponseSnapshot(
            response_id=response_to_cancel,
            item_id=self.item_id,
            content_index=self.content_index,
        )
        if self.response_id:
            self._rejected.add(self.response_id)
        self.response_id = None
        self.item_id = None
        self.content_index = 0
        self.generating = False
        return snapshot


@dataclass(frozen=True, slots=True)
class PlaybackSnapshot:
    was_playing: bool
    played_ms: int


def plan_barge_in_events(
    *,
    response_snapshot: ResponseSnapshot,
    playback_snapshot: PlaybackSnapshot,
) -> list[dict[str, object]]:
    """Cancel active generation; truncate only audio that was being played."""
    return build_barge_in_events(
        response_id=response_snapshot.response_id,
        item_id=response_snapshot.item_id if playback_snapshot.was_playing else None,
        content_index=response_snapshot.content_index,
        played_ms=playback_snapshot.played_ms,
    )


class PCMPlayback:
    """Small in-memory PCM playback queue with an immediate abort operation."""

    def __init__(
        self,
        *,
        sample_rate: int,
        block_frames: int,
        device: int | str | None,
    ) -> None:
        import sounddevice as sd

        self._sd = sd
        self.sample_rate = sample_rate
        self._lock = threading.Lock()
        self._buffer = bytearray()
        self._played_frames = 0
        self._last_audio_callback = 0.0
        self._status_reported = False
        self._stream = sd.RawOutputStream(
            samplerate=sample_rate,
            blocksize=block_frames,
            device=device,
            channels=1,
            dtype="int16",
            callback=self._callback,
        )

    def _callback(
        self, outdata: Any, frames: int, _time_info: Any, status: Any
    ) -> None:
        if status and not self._status_reported:
            self._status_reported = True
            LOG.warning("speaker callback status: %s", status)
        byte_count = frames * BYTES_PER_FRAME
        outdata[:byte_count] = b"\x00" * byte_count
        with self._lock:
            copied = min(byte_count, len(self._buffer))
            copied -= copied % BYTES_PER_FRAME
            if copied:
                outdata[:copied] = self._buffer[:copied]
                del self._buffer[:copied]
                self._played_frames += copied // BYTES_PER_FRAME
                self._last_audio_callback = time.monotonic()

    def start(self) -> None:
        self._stream.start()

    def enqueue(self, pcm: bytes) -> None:
        with self._lock:
            self._buffer.extend(pcm)

    def interrupt(self) -> PlaybackSnapshot:
        self._stream.abort(ignore_errors=True)
        with self._lock:
            recently_playing = time.monotonic() - self._last_audio_callback < 0.25
            was_playing = bool(self._buffer) or recently_playing
            played_ms = round(self._played_frames * 1000 / self.sample_rate)
            self._buffer.clear()
            self._played_frames = 0
            self._last_audio_callback = 0.0
        self._stream.start()
        return PlaybackSnapshot(was_playing=was_playing, played_ms=played_ms)

    def close(self) -> None:
        self._stream.abort(ignore_errors=True)
        self._stream.close(ignore_errors=True)


class Microphone:
    """Feed raw microphone blocks from PortAudio into an asyncio queue."""

    def __init__(
        self,
        *,
        sample_rate: int,
        block_frames: int,
        device: int | str | None,
    ) -> None:
        import sounddevice as sd

        self._loop = asyncio.get_running_loop()
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=100)
        self.dropped_chunks = 0
        self._status_reported = False
        self._stream = sd.RawInputStream(
            samplerate=sample_rate,
            blocksize=block_frames,
            device=device,
            channels=1,
            dtype="int16",
            callback=self._callback,
        )

    def _callback(
        self, indata: Any, _frames: int, _time_info: Any, status: Any
    ) -> None:
        if status and not self._status_reported:
            self._status_reported = True
            LOG.warning("microphone callback status: %s", status)
        self._loop.call_soon_threadsafe(self._offer, bytes(indata))

    def _offer(self, pcm: bytes) -> None:
        try:
            self._queue.put_nowait(pcm)
        except asyncio.QueueFull:
            self.dropped_chunks += 1

    def start(self) -> None:
        self._stream.start()

    async def read(self) -> bytes:
        return await self._queue.get()

    def close(self) -> None:
        self._stream.abort(ignore_errors=True)
        self._stream.close(ignore_errors=True)


@dataclass(slots=True)
class RuntimeStats:
    websocket_connected: bool = False
    session_created: bool = False
    session_updated: bool = False
    microphone_streaming: bool = False
    audio_returned: bool = False
    speaker_playback: bool = False
    transcripts: int = 0
    responses_done: int = 0
    barge_ins: int = 0
    cancel_sent: int = 0
    truncate_sent: int = 0
    errors: int = 0
    microphone_chunks: int = 0
    output_audio_bytes: int = 0
    last_speech_stopped_at: float | None = None
    first_audio_latencies_ms: list[int] = field(default_factory=list)
    event_types: Counter[str] = field(default_factory=Counter)


@dataclass(slots=True)
class RunOptions:
    endpoint: str
    model_uri: str
    sample_rate: int
    chunk_ms: int
    voice: str
    vad_threshold: float
    silence_ms: int
    input_device: int | str | None
    output_device: int | str | None
    duration_seconds: float
    setup_timeout: float
    connect_timeout: float


class LiveSession:
    def __init__(self, *, api_key: str, options: RunOptions) -> None:
        self._api_key = api_key
        self.options = options
        self.stats = RuntimeStats()
        self.ready = asyncio.Event()
        self.stop = asyncio.Event()
        self.response = ResponseState()
        self.playback: PCMPlayback | None = None
        self._send_lock = asyncio.Lock()
        self._printed_assistant_prefix = False

    async def send(self, ws: Any, event: dict[str, object]) -> None:
        async with self._send_lock:
            await ws.send_json(event)

    async def uplink(self, ws: Any, microphone: Microphone) -> None:
        while not self.stop.is_set():
            pcm = await microphone.read()
            await self.send(ws, build_audio_append(pcm))
            self.stats.microphone_chunks += 1
            if not self.stats.microphone_streaming:
                self.stats.microphone_streaming = True
                LOG.info("microphone streaming started")

    async def _handle_barge_in(self, ws: Any) -> None:
        response_snapshot = self.response.interrupt()
        if not self.playback:
            return
        playback_snapshot = self.playback.interrupt()
        events = plan_barge_in_events(
            response_snapshot=response_snapshot,
            playback_snapshot=playback_snapshot,
        )
        if not events:
            return
        self.stats.barge_ins += 1
        if playback_snapshot.was_playing:
            LOG.info(
                "barge-in: local playback stopped at approximately %d ms",
                playback_snapshot.played_ms,
            )
        for event in events:
            await self.send(ws, event)
            if event["type"] == "response.cancel":
                self.stats.cancel_sent += 1
                LOG.info("barge-in: response.cancel sent")
            else:
                self.stats.truncate_sent += 1
                LOG.info("barge-in: conversation.item.truncate sent")

    async def handle_event(self, ws: Any, message: dict[str, Any]) -> None:
        event_type = str(message.get("type", "<missing-type>"))
        self.stats.event_types[event_type] += 1

        if event_type == "session.created":
            self.stats.session_created = True
            LOG.info("session.created")
        elif event_type == "session.updated":
            self.stats.session_updated = True
            self.ready.set()
            LOG.info("session.updated")
        elif event_type == "input_audio_buffer.speech_started":
            LOG.info("speech_started")
            await self._handle_barge_in(ws)
        elif event_type == "input_audio_buffer.speech_stopped":
            self.stats.last_speech_stopped_at = time.monotonic()
            LOG.info("speech_stopped")
        elif event_type == "conversation.item.input_audio_transcription.completed":
            transcript = str(message.get("transcript", "")).strip()
            if transcript:
                self.stats.transcripts += 1
                print(f"\nUser transcript: {transcript}", flush=True)
        elif event_type == "response.created":
            response_id = str((message.get("response") or {}).get("id") or "")
            if response_id:
                self.response.start(response_id)
            self._printed_assistant_prefix = False
            LOG.info("response.created")
        elif event_type in {
            "response.output_text.delta",
            "response.output_audio_transcript.delta",
        }:
            delta = str(message.get("delta", ""))
            if delta:
                if not self._printed_assistant_prefix:
                    print("\nAssistant text: ", end="", flush=True)
                    self._printed_assistant_prefix = True
                print(delta, end="", flush=True)
        elif event_type == "response.output_audio.delta":
            response_id = message.get("response_id")
            item_id = message.get("item_id")
            content_index = int(message.get("content_index", 0))
            if not self.response.accept_audio(response_id, item_id, content_index):
                return
            try:
                pcm = base64.b64decode(message["delta"], validate=True)
            except (KeyError, ValueError) as error:
                self.stats.errors += 1
                LOG.error("invalid response audio delta: %s", error)
                return
            if self.playback:
                self.playback.enqueue(pcm)
            self.stats.output_audio_bytes += len(pcm)
            self.stats.audio_returned = True
            self.stats.speaker_playback = True
            if self.stats.output_audio_bytes == len(pcm):
                LOG.info("first response audio received")
            if self.stats.last_speech_stopped_at is not None:
                latency = round(
                    (time.monotonic() - self.stats.last_speech_stopped_at) * 1000
                )
                self.stats.first_audio_latencies_ms.append(latency)
                self.stats.last_speech_stopped_at = None
                LOG.info("speech stopped -> first response audio: approximately %d ms", latency)
        elif event_type == "conversation.item.truncated":
            LOG.info("conversation.item.truncated")
        elif event_type == "response.done":
            response = message.get("response") or {}
            response_id = response.get("id")
            self.response.finish(response_id)
            self.stats.responses_done += 1
            status = response.get("status", "unknown")
            print(flush=True)
            LOG.info("response.done status=%s", status)
        elif event_type == "error":
            self.stats.errors += 1
            error = message.get("error") or {}
            summary = "type={type} code={code} param={param} message={message}".format(
                type=error.get("type", "unknown"),
                code=error.get("code", "unknown"),
                param=error.get("param", "unknown"),
                message=error.get("message", "unknown"),
            )
            LOG.error("server error: %s", redact_text(summary, secrets=(self._api_key,)))
        elif event_type in {
            "response.output_item.added",
            "response.output_item.done",
            "response.output_audio.done",
            "response.output_text.done",
            "conversation.item.created",
            "input_audio_buffer.committed",
        }:
            LOG.info("%s", event_type)

    async def downlink(self, ws: Any) -> None:
        import aiohttp

        try:
            async for message in ws:
                if message.type == aiohttp.WSMsgType.TEXT:
                    try:
                        event = json.loads(message.data)
                    except json.JSONDecodeError:
                        self.stats.errors += 1
                        LOG.error("received a non-JSON text event")
                        continue
                    await self.handle_event(ws, event)
                elif message.type in {
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.ERROR,
                }:
                    LOG.warning(
                        "WebSocket terminal frame type=%s code=%s reason=%s",
                        message.type.name,
                        message.data,
                        message.extra or "<none>",
                    )
                    break
        finally:
            if not self.stop.is_set():
                LOG.warning(
                    "WebSocket closed code=%s exception=%s",
                    ws.close_code,
                    ws.exception() or "<none>",
                )
            self.stop.set()

    async def run(self) -> RuntimeStats:
        import aiohttp

        block_frames = round(self.options.sample_rate * self.options.chunk_ms / 1000)
        websocket_url = build_websocket_url(
            self.options.endpoint, self.options.model_uri
        )
        headers = {"Authorization": f"Api-Key {self._api_key}"}
        timeout = aiohttp.ClientTimeout(
            total=None, sock_connect=self.options.connect_timeout
        )
        receiver: asyncio.Task[None] | None = None
        sender: asyncio.Task[None] | None = None
        microphone: Microphone | None = None
        try:
            async with aiohttp.ClientSession(timeout=timeout) as client:
                async with client.ws_connect(
                    websocket_url,
                    headers=headers,
                    autoclose=True,
                ) as ws:
                    self.stats.websocket_connected = True
                    LOG.info(
                        "connect success; model=%s", model_label(self.options.model_uri)
                    )
                    receiver = asyncio.create_task(self.downlink(ws))
                    await self.send(
                        ws,
                        build_session_update(
                            input_sample_rate=self.options.sample_rate,
                            output_sample_rate=self.options.sample_rate,
                            voice=self.options.voice,
                            vad_threshold=self.options.vad_threshold,
                            silence_ms=self.options.silence_ms,
                        ),
                    )
                    await asyncio.wait_for(
                        self.ready.wait(), timeout=self.options.setup_timeout
                    )
                    self.playback = PCMPlayback(
                        sample_rate=self.options.sample_rate,
                        block_frames=block_frames,
                        device=self.options.output_device,
                    )
                    try:
                        microphone = Microphone(
                            sample_rate=self.options.sample_rate,
                            block_frames=block_frames,
                            device=self.options.input_device,
                        )
                        self.playback.start()
                        microphone.start()
                        sender = asyncio.create_task(self.uplink(ws, microphone))
                        LOG.info("local microphone and speaker streams started")
                        if self.options.duration_seconds > 0:
                            try:
                                await asyncio.wait_for(
                                    self.stop.wait(),
                                    timeout=self.options.duration_seconds,
                                )
                            except asyncio.TimeoutError:
                                LOG.info("requested live-test duration elapsed")
                                self.stop.set()
                        else:
                            await self.stop.wait()
                    finally:
                        if sender and not sender.done():
                            sender.cancel()
                            await asyncio.gather(sender, return_exceptions=True)
                        if microphone:
                            close_started = time.monotonic()
                            microphone.close()
                            LOG.info(
                                "microphone stream closed in approximately %d ms",
                                round((time.monotonic() - close_started) * 1000),
                            )
                            if microphone.dropped_chunks:
                                LOG.warning(
                                    "microphone queue dropped %d chunks",
                                    microphone.dropped_chunks,
                                )
                            microphone = None
                        if self.playback:
                            self.playback.close()
                            self.playback = None
        finally:
            self.stop.set()
            for task in (sender, receiver):
                if task and not task.done():
                    task.cancel()
            for task in (sender, receiver):
                if task:
                    await asyncio.gather(task, return_exceptions=True)
        return self.stats


def parse_device(value: str | None) -> int | str | None:
    if value is None:
        return None
    return int(value) if value.isdigit() else value


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Local microphone/speaker Yandex Realtime Voice PoC"
    )
    parser.add_argument("--list-devices", action="store_true")
    parser.add_argument("--model", help="model ID or complete gpt:// URI")
    parser.add_argument("--endpoint", help="current Yandex Realtime WSS endpoint")
    parser.add_argument("--sample-rate", type=int, default=24_000)
    parser.add_argument("--chunk-ms", type=int, default=20)
    parser.add_argument("--voice", default="dasha")
    parser.add_argument("--vad-threshold", type=float, default=0.5)
    parser.add_argument("--silence-ms", type=int, default=500)
    parser.add_argument("--input-device")
    parser.add_argument("--output-device")
    parser.add_argument(
        "--duration-seconds",
        type=float,
        default=0,
        help="stop automatically; 0 means run until Ctrl+C",
    )
    parser.add_argument("--setup-timeout", type=float, default=20)
    parser.add_argument("--connect-timeout", type=float, default=20)
    return parser


def print_devices() -> None:
    import sounddevice as sd

    print(sd.query_devices())


def print_summary(stats: RuntimeStats) -> None:
    latency = (
        f"{stats.first_audio_latencies_ms} ms"
        if stats.first_audio_latencies_ms
        else "not observed"
    )
    print("\nRuntime summary (human acceptance is recorded separately):")
    print(f"  WebSocket connected: {'YES' if stats.websocket_connected else 'NO'}")
    print(f"  Session created: {'YES' if stats.session_created else 'NO'}")
    print(f"  Session updated: {'YES' if stats.session_updated else 'NO'}")
    print(f"  Microphone streaming: {'YES' if stats.microphone_streaming else 'NO'}")
    print(f"  Realtime audio returned: {'YES' if stats.audio_returned else 'NO'}")
    print(f"  Speaker stream received audio: {'YES' if stats.speaker_playback else 'NO'}")
    print(f"  User transcripts observed: {stats.transcripts}")
    print(f"  Responses done: {stats.responses_done}")
    print(f"  Barge-ins observed: {stats.barge_ins}")
    print(f"  Cancel / truncate sent: {stats.cancel_sent} / {stats.truncate_sent}")
    print(f"  First-audio latency observations: {latency}")
    print(f"  Server/protocol errors: {stats.errors}")
    if stats.event_types:
        print("  Event types:")
        for event_type, count in sorted(stats.event_types.items()):
            print(f"    {event_type}: {count}")


def configure_logging() -> None:
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("aiohttp").setLevel(logging.WARNING)


def main() -> int:
    if sys.version_info < (3, 10):
        print("Python 3.10 or newer is required", file=sys.stderr)
        return 2
    args = make_parser().parse_args()
    configure_logging()
    if args.list_devices:
        print_devices()
        return 0

    api_key = os.environ.get("YANDEX_API_KEY")
    if not api_key:
        print("YANDEX_API_KEY is required", file=sys.stderr)
        return 2
    configured_model = (
        args.model
        or os.environ.get("YANDEX_MODEL_OR_AGENT")
        or PRIMARY_MODEL
    )
    try:
        model_uri = resolve_model_uri(
            configured_model, os.environ.get("YANDEX_FOLDER_ID")
        )
        endpoint = (
            args.endpoint
            or os.environ.get("YANDEX_REALTIME_ENDPOINT")
            or CURRENT_ENDPOINT
        )
        build_websocket_url(endpoint, model_uri)
    except ValueError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2

    options = RunOptions(
        endpoint=endpoint,
        model_uri=model_uri,
        sample_rate=args.sample_rate,
        chunk_ms=args.chunk_ms,
        voice=args.voice,
        vad_threshold=args.vad_threshold,
        silence_ms=args.silence_ms,
        input_device=parse_device(args.input_device),
        output_device=parse_device(args.output_device),
        duration_seconds=args.duration_seconds,
        setup_timeout=args.setup_timeout,
        connect_timeout=args.connect_timeout,
    )
    print(
        "Speak Russian naturally. Use headphones for a reliable barge-in test. "
        "Press Ctrl+C to stop."
    )
    session = LiveSession(api_key=api_key, options=options)
    try:
        stats = asyncio.run(session.run())
    except KeyboardInterrupt:
        stats = session.stats
        LOG.info("stopped by user")
    except Exception as error:  # Runtime boundary: keep credentials out of output.
        stats = session.stats
        LOG.error("live PoC failed: %s", redact_text(str(error), secrets=(api_key,)))
        print_summary(stats)
        return 1
    print_summary(stats)
    return 0 if stats.session_updated else 1


if __name__ == "__main__":
    raise SystemExit(main())
