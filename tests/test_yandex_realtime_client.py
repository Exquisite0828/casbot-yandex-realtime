import asyncio
import base64
import json
from types import SimpleNamespace
import unittest

from realtime_dialog.yandex_realtime_client import (
    CURRENT_ENDPOINT,
    PRIMARY_MODEL,
    RealtimeEventKind,
    RuntimeConfig,
    YandexRealtimeClient,
    build_audio_append,
    build_session_update,
    build_text_input_events,
    build_websocket_url,
    normalize_server_event,
    resolve_model_uri,
)


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []
        self.closed = False

    async def send_json(self, event: dict[str, object]) -> None:
        self.sent.append(event)


class YandexProtocolTest(unittest.IsolatedAsyncioTestCase):
    def test_missing_credentials_folder_and_bad_model_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "YANDEX_API_KEY"):
            RuntimeConfig.from_environment(
                {"YANDEX_FOLDER_ID": "folder-1"}
            )
        with self.assertRaisesRegex(ValueError, "YANDEX_FOLDER_ID"):
            RuntimeConfig.from_environment(
                {
                    "YANDEX_API_KEY": "test-only",
                    "YANDEX_MODEL_OR_AGENT": PRIMARY_MODEL,
                }
            )
        with self.assertRaisesRegex(ValueError, "model"):
            resolve_model_uri("https://bad-model.example", "folder-1")

    def test_runtime_config_rejects_missing_key_model_and_invalid_rates(self) -> None:
        valid = {
            "api_key": "test-only",
            "model_uri": "gpt://folder-1/speech-realtime-260528",
        }
        for overrides, message in (
            ({"api_key": ""}, "api_key"),
            ({"model_uri": ""}, "model_uri"),
            ({"input_sample_rate": 0}, "input_sample_rate"),
            ({"yandex_output_sample_rate": -1}, "yandex_output_sample_rate"),
            ({"connect_timeout": 0}, "connect_timeout"),
            ({"setup_timeout": 0}, "setup_timeout"),
        ):
            values = {**valid, **overrides}
            with self.subTest(overrides=overrides):
                with self.assertRaisesRegex(ValueError, message):
                    RuntimeConfig(**values)

    def test_runtime_config_reads_secret_from_supplied_environment_only(self) -> None:
        config = RuntimeConfig.from_environment(
            {
                "YANDEX_API_KEY": "test-api-key-value",
                "YANDEX_FOLDER_ID": "folder-1",
                "YANDEX_REALTIME_ENDPOINT": CURRENT_ENDPOINT,
                "YANDEX_MODEL_OR_AGENT": PRIMARY_MODEL,
            }
        )
        self.assertEqual(config.model_uri, "gpt://folder-1/speech-realtime-260528")
        self.assertNotIn("test-api-key-value", repr(config))

    def test_current_session_and_text_schemas_are_reused(self) -> None:
        session = build_session_update(
            input_sample_rate=24_000,
            output_sample_rate=16_000,
            voice="dasha",
            vad_threshold=0.5,
            silence_ms=500,
            instructions="Отвечай по-русски.",
        )
        self.assertEqual(
            session["session"]["audio"]["input"]["format"],
            {"type": "audio/pcm", "rate": 24_000},
        )
        self.assertEqual(
            session["session"]["audio"]["output"]["format"],
            {"type": "audio/pcm", "rate": 16_000},
        )
        self.assertEqual(session["session"]["audio"]["input"]["languages"], ["ru-RU"])
        text_events = build_text_input_events("Привет")
        self.assertEqual(text_events[0]["type"], "conversation.item.create")
        self.assertEqual(text_events[0]["item"]["content"][0], {"type": "input_text", "text": "Привет"})
        self.assertEqual(text_events[1], {"type": "response.create"})

    def test_endpoint_and_audio_append_match_phase_2(self) -> None:
        uri = resolve_model_uri(PRIMARY_MODEL, "folder-1")
        self.assertEqual(uri, "gpt://folder-1/speech-realtime-260528")
        self.assertIn("model=gpt%3A%2F%2Ffolder-1", build_websocket_url(CURRENT_ENDPOINT, uri))
        pcm = b"\x00\x01\x02\x03"
        self.assertEqual(base64.b64decode(build_audio_append(pcm)["audio"]), pcm)

    def test_response_generation_mapping_marks_late_events_stale(self) -> None:
        generations: dict[str, int] = {}
        started = normalize_server_event(
            {"type": "response.created", "response": {"id": "old"}},
            current_generation=3,
            response_generations=generations,
            output_sample_rate=24_000,
            secrets=(),
        )
        late = normalize_server_event(
            {
                "type": "response.output_text.delta",
                "response_id": "old",
                "delta": "late",
            },
            current_generation=4,
            response_generations=generations,
            output_sample_rate=24_000,
            secrets=(),
        )
        self.assertEqual(started.generation_id, 3)
        self.assertEqual(late.generation_id, 3)
        self.assertEqual(late.kind, RealtimeEventKind.ASSISTANT_TEXT)

    def test_completed_responses_do_not_grow_generation_map(self) -> None:
        generations: dict[str, int] = {}
        for index in range(1_000):
            response_id = f"response-{index}"
            normalize_server_event(
                {"type": "response.created", "response": {"id": response_id}},
                current_generation=3,
                response_generations=generations,
                output_sample_rate=24_000,
                secrets=(),
            )
            done = normalize_server_event(
                {
                    "type": "response.done",
                    "response": {"id": response_id, "status": "completed"},
                },
                current_generation=4,
                response_generations=generations,
                output_sample_rate=24_000,
                secrets=(),
            )
            self.assertEqual(done.generation_id, 3)
        self.assertEqual(generations, {})

    async def test_close_clears_response_generation_map(self) -> None:
        client = YandexRealtimeClient(
            RuntimeConfig(
                api_key="test-only",
                model_uri="gpt://folder-1/speech-realtime-260528",
            )
        )
        client._response_generations["completed"] = 7
        await client.close()
        self.assertEqual(client._response_generations, {})

    async def test_stale_connection_frame_is_rejected_before_normalization(self) -> None:
        import aiohttp

        class StaleWebSocket:
            close_code = 1000

            def __init__(self) -> None:
                self._messages = iter(
                    [
                        SimpleNamespace(
                            type=aiohttp.WSMsgType.TEXT,
                            data=json.dumps(
                                {
                                    "type": "response.created",
                                    "response": {"id": "stale-response"},
                                }
                            ),
                        )
                    ]
                )

            def __aiter__(self):
                return self

            async def __anext__(self):
                try:
                    return next(self._messages)
                except StopIteration:
                    raise StopAsyncIteration

        client = YandexRealtimeClient(
            RuntimeConfig(
                api_key="test-only",
                model_uri="gpt://folder-1/speech-realtime-260528",
            )
        )
        client._connection_token = 2
        await client._receive_loop(StaleWebSocket(), connection_token=1)
        self.assertEqual(client._response_generations, {})

    def test_audio_event_is_decoded_without_audio_device_dependency(self) -> None:
        pcm = b"\x01\x02\x03\x04"
        event = normalize_server_event(
            {
                "type": "response.output_audio.delta",
                "response_id": "response-1",
                "delta": base64.b64encode(pcm).decode("ascii"),
            },
            current_generation=8,
            response_generations={"response-1": 8},
            output_sample_rate=24_000,
            secrets=(),
        )
        self.assertEqual(event.kind, RealtimeEventKind.ASSISTANT_AUDIO)
        self.assertEqual(event.data["pcm"], pcm)
        self.assertEqual(event.data["sample_rate"], 24_000)

    def test_error_event_redacts_authorization_and_secret(self) -> None:
        secret = "actual-secret-value"
        event = normalize_server_event(
            {
                "type": "error",
                "error": {
                    "type": "bad_request",
                    "code": "bad",
                    "message": f"Authorization: Api-Key {secret}",
                },
            },
            current_generation=2,
            response_generations={},
            output_sample_rate=24_000,
            secrets=(secret,),
        )
        self.assertEqual(event.kind, RealtimeEventKind.ERROR)
        self.assertNotIn(secret, event.data["message"])

    async def test_client_send_methods_use_current_2026_events(self) -> None:
        config = RuntimeConfig(
            api_key="test-only",
            endpoint=CURRENT_ENDPOINT,
            model_uri="gpt://folder-1/speech-realtime-260528",
        )
        client = YandexRealtimeClient(config)
        websocket = FakeWebSocket()
        client._ws = websocket
        client._send_lock = asyncio.Lock()
        client.set_generation(9)
        await client.send_audio(b"\x00\x01")
        await client.send_text("Привет")
        client._current_response_id = "response-9"
        await client.cancel_current_response()
        await client.truncate_response("item-9", content_index=0, audio_end_ms=250)
        self.assertEqual(websocket.sent[0]["type"], "input_audio_buffer.append")
        self.assertEqual(websocket.sent[1:3], build_text_input_events("Привет"))
        self.assertEqual(
            websocket.sent[3],
            {"type": "response.cancel", "response_id": "response-9"},
        )
        self.assertEqual(websocket.sent[4]["type"], "conversation.item.truncate")

    def test_runtime_config_has_independent_input_and_output_rates(self) -> None:
        config = RuntimeConfig(
            api_key="test-only",
            model_uri="gpt://folder-1/speech-realtime-260528",
            input_sample_rate=24_000,
            yandex_output_sample_rate=16_000,
        )
        self.assertEqual(config.input_sample_rate, 24_000)
        self.assertEqual(config.yandex_output_sample_rate, 16_000)
        self.assertFalse(hasattr(config, "sample_rate"))

    async def test_close_cleans_session_when_websocket_close_raises(self) -> None:
        class FailingWebSocket:
            closed = False

            async def close(self) -> None:
                raise RuntimeError("websocket close failed")

        class Session:
            def __init__(self) -> None:
                self.closed = False

            async def close(self) -> None:
                self.closed = True

        config = RuntimeConfig(
            api_key="test-only",
            model_uri="gpt://folder-1/speech-realtime-260528",
        )
        client = YandexRealtimeClient(config)
        session = Session()
        client._ws = FailingWebSocket()
        client._client_session = session
        with self.assertRaisesRegex(RuntimeError, "websocket close failed"):
            await client.close()
        self.assertTrue(session.closed)
        self.assertIsNone(client._ws)
        self.assertIsNone(client._client_session)


if __name__ == "__main__":
    unittest.main()
