"""Async Yandex Realtime transport shared by the PoC and ROS2 skeleton.

This module contains no ROS2 or audio-device code.  The API key is accepted
only through :meth:`RuntimeConfig.from_environment` (or direct test
construction), is excluded from ``repr``, and is used only for the WebSocket
authorization header.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
from enum import Enum
import inspect
import json
import re
from typing import Any, Awaitable, Callable, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


CURRENT_ENDPOINT = "wss://ai.api.cloud.yandex.net/v1/realtime"
PRIMARY_MODEL = "speech-realtime-260528"
DEFAULT_INSTRUCTIONS = (
    "Ты дружелюбный голосовой ассистент. Всегда отвечай на русском языке, "
    "кратко и естественно. Помни контекст текущего разговора."
)


def resolve_model_uri(model_or_uri: str, folder_id: str | None) -> str:
    """Resolve a model ID to the current ``gpt://`` URI form."""
    value = model_or_uri.strip()
    if value.startswith("gpt://"):
        if value.removeprefix("gpt://").count("/") != 1:
            raise ValueError("model URI must be gpt://<folder_id>/<model_id>")
        return value
    if "://" in value:
        raise ValueError("model must be a model ID or gpt:// model URI")
    if not value:
        raise ValueError("model must not be empty")
    if not folder_id:
        raise ValueError(
            "YANDEX_FOLDER_ID is required when the model is not a complete gpt:// URI"
        )
    return f"gpt://{folder_id}/{value}"


def build_websocket_url(endpoint: str, model_uri: str) -> str:
    """Attach the model query to the verified current endpoint."""
    parsed = urlsplit(endpoint.strip())
    if "rest-assistant.api.cloud.yandex.net" in parsed.netloc:
        raise ValueError("legacy Yandex Realtime endpoint is forbidden")
    if parsed.scheme != "wss":
        raise ValueError("YANDEX_REALTIME_ENDPOINT must use wss://")
    if (
        parsed.netloc != "ai.api.cloud.yandex.net"
        or parsed.path.rstrip("/") != "/v1/realtime"
    ):
        raise ValueError("endpoint must be the current Yandex /v1/realtime endpoint")
    query = [(key, value) for key, value in parse_qsl(parsed.query) if key != "model"]
    query.append(("model", model_uri))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))


def build_session_update(
    *,
    sample_rate: int,
    voice: str,
    vad_threshold: float,
    silence_ms: int,
    instructions: str = DEFAULT_INSTRUCTIONS,
) -> dict[str, object]:
    """Build the current schema verified during Phase 2."""
    return {
        "type": "session.update",
        "session": {
            "instructions": instructions,
            "output_modalities": ["audio"],
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": sample_rate},
                    "languages": ["ru-RU"],
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": vad_threshold,
                        "silence_duration_ms": silence_ms,
                    },
                },
                "output": {
                    "format": {"type": "audio/pcm", "rate": sample_rate},
                    "voice": voice,
                },
            },
        },
    }


def build_audio_append(pcm: bytes) -> dict[str, str]:
    return {
        "type": "input_audio_buffer.append",
        "audio": base64.b64encode(pcm).decode("ascii"),
    }


def build_text_input_events(text: str) -> list[dict[str, object]]:
    return [
        {
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": text}],
            },
        },
        {"type": "response.create"},
    ]


def build_barge_in_events(
    *,
    response_id: str | None,
    item_id: str | None,
    content_index: int,
    played_ms: int,
) -> list[dict[str, object]]:
    """Build the cancel/truncate sequence verified by the Phase 2 PoC."""
    events: list[dict[str, object]] = []
    if response_id:
        events.append({"type": "response.cancel", "response_id": response_id})
    if item_id:
        events.append(
            {
                "type": "conversation.item.truncate",
                "item_id": item_id,
                "content_index": content_index,
                "audio_end_ms": max(0, played_ms),
            }
        )
    return events


def model_label(model_uri: str) -> str:
    """Return only the non-sensitive model ID for status output."""
    return model_uri.rstrip("/").rsplit("/", 1)[-1]


def redact_text(text: str, *, secrets: tuple[str, ...] = ()) -> str:
    """Remove credential material before propagating an error."""
    result = text
    for secret in secrets:
        if secret:
            result = result.replace(secret, "<redacted>")
    result = re.sub(
        r"(?i)(authorization\s*[:=]\s*(?:api-key|bearer)\s+)[^\s;,]+",
        r"\1<redacted>",
        result,
    )
    return re.sub(
        r"(?i)(api[_-]?key\s*[:=]\s*)[^\s;,]+",
        r"\1<redacted>",
        result,
    )


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    api_key: str = field(repr=False)
    endpoint: str = CURRENT_ENDPOINT
    model_uri: str = ""
    sample_rate: int = 24_000
    voice: str = "dasha"
    vad_threshold: float = 0.5
    silence_ms: int = 500
    instructions: str = DEFAULT_INSTRUCTIONS
    connect_timeout: float = 15.0
    setup_timeout: float = 10.0

    @classmethod
    def from_environment(
        cls, environ: Mapping[str, str] | None = None, **overrides: Any
    ) -> "RuntimeConfig":
        if environ is None:
            import os

            environ = os.environ
        api_key = environ.get("YANDEX_API_KEY", "")
        if not api_key:
            raise ValueError("YANDEX_API_KEY is required in the process environment")
        endpoint = overrides.pop(
            "endpoint", environ.get("YANDEX_REALTIME_ENDPOINT", CURRENT_ENDPOINT)
        )
        model_or_uri = overrides.pop(
            "model_or_uri", environ.get("YANDEX_MODEL_OR_AGENT", PRIMARY_MODEL)
        )
        folder_id = overrides.pop("folder_id", environ.get("YANDEX_FOLDER_ID"))
        model_uri = overrides.pop(
            "model_uri", resolve_model_uri(model_or_uri, folder_id)
        )
        return cls(
            api_key=api_key,
            endpoint=endpoint,
            model_uri=model_uri,
            **overrides,
        )


class RealtimeEventKind(str, Enum):
    SESSION_READY = "session_ready"
    SPEECH_STARTED = "speech_started"
    SPEECH_STOPPED = "speech_stopped"
    INPUT_TRANSCRIPT = "input_transcript"
    RESPONSE_STARTED = "response_started"
    ASSISTANT_TEXT = "assistant_text"
    ASSISTANT_AUDIO = "assistant_audio"
    RESPONSE_DONE = "response_done"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class RealtimeEvent:
    kind: RealtimeEventKind
    generation_id: int
    data: dict[str, Any]


def _response_id(message: Mapping[str, Any]) -> str | None:
    response = message.get("response")
    nested = response.get("id") if isinstance(response, Mapping) else None
    value = message.get("response_id") or nested
    return str(value) if value else None


def _generation_for(
    message: Mapping[str, Any],
    current_generation: int,
    response_generations: dict[str, int],
) -> tuple[int, str | None]:
    response_id = _response_id(message)
    event_type = message.get("type")
    if event_type == "response.created" and response_id:
        response_generations[response_id] = current_generation
    return response_generations.get(response_id, current_generation), response_id


def normalize_server_event(
    message: Mapping[str, Any],
    *,
    current_generation: int,
    response_generations: dict[str, int],
    output_sample_rate: int,
    secrets: tuple[str, ...],
) -> RealtimeEvent | None:
    """Normalize supported wire events and bind responses to their generation."""
    generation, response_id = _generation_for(
        message, current_generation, response_generations
    )
    event_type = str(message.get("type", ""))

    if event_type == "session.updated":
        return RealtimeEvent(RealtimeEventKind.SESSION_READY, generation, {})
    if event_type == "input_audio_buffer.speech_started":
        return RealtimeEvent(RealtimeEventKind.SPEECH_STARTED, generation, {})
    if event_type == "input_audio_buffer.speech_stopped":
        return RealtimeEvent(RealtimeEventKind.SPEECH_STOPPED, generation, {})
    if event_type == "conversation.item.input_audio_transcription.completed":
        return RealtimeEvent(
            RealtimeEventKind.INPUT_TRANSCRIPT,
            generation,
            {"text": str(message.get("transcript", ""))},
        )
    if event_type == "response.created":
        return RealtimeEvent(
            RealtimeEventKind.RESPONSE_STARTED,
            generation,
            {"response_id": response_id},
        )
    if event_type in {
        "response.output_text.delta",
        "response.output_audio_transcript.delta",
    }:
        return RealtimeEvent(
            RealtimeEventKind.ASSISTANT_TEXT,
            generation,
            {"text": str(message.get("delta", "")), "response_id": response_id},
        )
    if event_type == "response.output_audio.delta":
        try:
            pcm = base64.b64decode(str(message["delta"]), validate=True)
        except (KeyError, ValueError, TypeError) as error:
            return RealtimeEvent(
                RealtimeEventKind.ERROR,
                generation,
                {"message": f"invalid response audio delta: {error}"},
            )
        return RealtimeEvent(
            RealtimeEventKind.ASSISTANT_AUDIO,
            generation,
            {
                "pcm": pcm,
                "sample_rate": output_sample_rate,
                "response_id": response_id,
                "item_id": message.get("item_id"),
                "content_index": int(message.get("content_index", 0)),
            },
        )
    if event_type == "response.done":
        response = message.get("response")
        status = response.get("status") if isinstance(response, Mapping) else None
        return RealtimeEvent(
            RealtimeEventKind.RESPONSE_DONE,
            generation,
            {"response_id": response_id, "status": status},
        )
    if event_type == "error":
        error = message.get("error")
        if not isinstance(error, Mapping):
            error = {}
        summary = "type={type} code={code} param={param} message={message}".format(
            type=error.get("type", "unknown"),
            code=error.get("code", "unknown"),
            param=error.get("param", "unknown"),
            message=error.get("message", "unknown"),
        )
        return RealtimeEvent(
            RealtimeEventKind.ERROR,
            generation,
            {"message": redact_text(summary, secrets=secrets)},
        )
    return None


EventHandler = Callable[[RealtimeEvent], Awaitable[None] | None]


class YandexRealtimeClient:
    """One-session asynchronous transport; caller owns reconnection policy."""

    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config
        self._generation_id = 0
        self._response_generations: dict[str, int] = {}
        self._current_response_id: str | None = None
        self._event_handler: EventHandler | None = None
        self._client_session: Any = None
        self._ws: Any = None
        self._send_lock = asyncio.Lock()
        self._receive_task: asyncio.Task[None] | None = None
        self._session_ready = asyncio.Event()

    def set_event_handler(self, handler: EventHandler) -> None:
        self._event_handler = handler

    def set_generation(self, generation_id: int) -> None:
        self._generation_id = generation_id

    async def connect(self, generation_id: int) -> None:
        if self._ws is not None and not getattr(self._ws, "closed", False):
            self.set_generation(generation_id)
            return
        import aiohttp

        self.set_generation(generation_id)
        self._response_generations.clear()
        self._current_response_id = None
        self._session_ready.clear()
        timeout = aiohttp.ClientTimeout(
            total=None, sock_connect=self.config.connect_timeout
        )
        self._client_session = aiohttp.ClientSession(timeout=timeout)
        try:
            self._ws = await self._client_session.ws_connect(
                build_websocket_url(self.config.endpoint, self.config.model_uri),
                headers={"Authorization": f"Api-Key {self.config.api_key}"},
                autoclose=True,
            )
            self._receive_task = asyncio.create_task(self._receive_loop())
            await self._send(
                build_session_update(
                    sample_rate=self.config.sample_rate,
                    voice=self.config.voice,
                    vad_threshold=self.config.vad_threshold,
                    silence_ms=self.config.silence_ms,
                    instructions=self.config.instructions,
                )
            )
            await asyncio.wait_for(
                self._session_ready.wait(), timeout=self.config.setup_timeout
            )
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        receive_task = self._receive_task
        self._receive_task = None
        ws = self._ws
        self._ws = None
        session = self._client_session
        self._client_session = None
        if ws is not None and not getattr(ws, "closed", False):
            await ws.close()
        current = asyncio.current_task()
        if receive_task is not None and receive_task is not current:
            receive_task.cancel()
            await asyncio.gather(receive_task, return_exceptions=True)
        if session is not None and not getattr(session, "closed", False):
            await session.close()
        self._current_response_id = None

    async def send_audio(self, pcm: bytes) -> None:
        if pcm:
            await self._send(build_audio_append(pcm))

    async def send_text(self, text: str) -> None:
        for event in build_text_input_events(text):
            await self._send(event)

    async def cancel_current_response(self) -> None:
        if self._current_response_id:
            await self._send(
                {
                    "type": "response.cancel",
                    "response_id": self._current_response_id,
                }
            )

    async def truncate_response(
        self, item_id: str, *, content_index: int, audio_end_ms: int
    ) -> None:
        await self._send(
            {
                "type": "conversation.item.truncate",
                "item_id": item_id,
                "content_index": content_index,
                "audio_end_ms": max(0, audio_end_ms),
            }
        )

    async def _send(self, event: dict[str, object]) -> None:
        if self._ws is None or getattr(self._ws, "closed", False):
            raise RuntimeError("Yandex Realtime WebSocket is not connected")
        async with self._send_lock:
            await self._ws.send_json(event)

    async def _dispatch(self, event: RealtimeEvent) -> None:
        if event.kind is RealtimeEventKind.SESSION_READY:
            self._session_ready.set()
        if event.kind is RealtimeEventKind.RESPONSE_STARTED:
            self._current_response_id = event.data.get("response_id")
        elif event.kind is RealtimeEventKind.RESPONSE_DONE:
            if event.data.get("response_id") == self._current_response_id:
                self._current_response_id = None
        if self._event_handler is not None:
            result = self._event_handler(event)
            if inspect.isawaitable(result):
                await result

    async def _receive_loop(self) -> None:
        import aiohttp

        ws = self._ws
        try:
            async for message in ws:
                if message.type == aiohttp.WSMsgType.TEXT:
                    try:
                        payload = json.loads(message.data)
                    except json.JSONDecodeError:
                        await self._dispatch(
                            RealtimeEvent(
                                RealtimeEventKind.ERROR,
                                self._generation_id,
                                {"message": "received a non-JSON text event"},
                            )
                        )
                        continue
                    event = normalize_server_event(
                        payload,
                        current_generation=self._generation_id,
                        response_generations=self._response_generations,
                        output_sample_rate=self.config.sample_rate,
                        secrets=(self.config.api_key,),
                    )
                    if event is not None:
                        await self._dispatch(event)
                elif message.type in {
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.ERROR,
                }:
                    break
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await self._dispatch(
                RealtimeEvent(
                    RealtimeEventKind.ERROR,
                    self._generation_id,
                    {
                        "message": redact_text(
                            f"WebSocket receive failed: {error}",
                            secrets=(self.config.api_key,),
                        )
                    },
                )
            )
