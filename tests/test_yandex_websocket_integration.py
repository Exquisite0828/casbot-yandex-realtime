import asyncio
import base64
from dataclasses import fields
import unittest

from fake_yandex_server import FakeYandexRealtimeServer
from realtime_dialog.adapters import (
    QueuedRobotAudioOutputAdapter,
    RobotAudioPacket,
    RobotFlushEvent,
)
from realtime_dialog.dialog_controller import DialogController
from realtime_dialog.ros_contract import (
    STATUS_ERROR,
    STATUS_IDLE,
    STATUS_LISTENING,
    STATUS_SPEAKING_TEXT,
)
from realtime_dialog.yandex_realtime_client import (
    CURRENT_ENDPOINT,
    RuntimeConfig,
    YandexRealtimeClient,
    build_websocket_url,
)


TEST_KEY = "phase6-obviously-fake-api-key"
MODEL_URI = "gpt://phase6-fake-folder/speech-realtime-260528"
PCM_CHUNK = b"\x00\x01" * 480


class LocalWebSocketConnector:
    def __init__(self, local_url: str) -> None:
        self.local_url = local_url
        self.validated_urls: list[str] = []

    async def __call__(self, session, validated_url, headers):
        self.validated_urls.append(validated_url)
        return await session.ws_connect(
            self.local_url,
            headers=headers,
            autoclose=True,
        )


class FakeMicAdapter:
    def __init__(self) -> None:
        self.started = False
        self.start_count = 0
        self.stop_count = 0
        self.on_audio = None
        self.on_error = None

    def start(self, on_audio, on_error) -> None:
        self.started = True
        self.start_count += 1
        self.on_audio = on_audio
        self.on_error = on_error

    def stop(self) -> None:
        if self.started:
            self.stop_count += 1
        self.started = False

    def emit(self, pcm: bytes) -> None:
        if self.on_audio is None:
            raise RuntimeError("microphone is not started")
        self.on_audio(pcm)


class ControllerHarness:
    def __init__(
        self,
        server: FakeYandexRealtimeServer,
        *,
        setup_timeout: float = 0.2,
        microphone_queue_chunks: int = 4,
    ) -> None:
        self.connector = LocalWebSocketConnector(server.url)
        self.config = RuntimeConfig(
            api_key=TEST_KEY,
            endpoint=CURRENT_ENDPOINT,
            model_uri=MODEL_URI,
            input_sample_rate=24_000,
            yandex_output_sample_rate=24_000,
            setup_timeout=setup_timeout,
            connect_timeout=0.5,
        )
        self.client = YandexRealtimeClient(
            self.config,
            websocket_connector=self.connector,
        )
        self.mic = FakeMicAdapter()
        self.audio = QueuedRobotAudioOutputAdapter(max_audio_packets=4)
        self.statuses: list[str] = []
        self.text: list[str] = []
        self.controller = DialogController(
            client=self.client,
            mic_adapter=self.mic,
            audio_output=self.audio,
            status_sink=self.statuses.append,
            text_result_sink=self.text.append,
            microphone_queue_chunks=microphone_queue_chunks,
        )

    async def close(self) -> None:
        await self.controller.stop_session()


async def wait_until(predicate, timeout: float = 1.0) -> None:
    async def poll() -> None:
        while not predicate():
            await asyncio.sleep(0)

    await asyncio.wait_for(poll(), timeout=timeout)


class YandexWebSocketIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_localhost_requires_explicit_connector_and_config_stays_strict(self) -> None:
        self.assertNotIn("websocket_connector", {field.name for field in fields(RuntimeConfig)})
        with self.assertRaisesRegex(ValueError, "wss"):
            build_websocket_url("ws://127.0.0.1:1/v1/realtime", MODEL_URI)
        with self.assertRaisesRegex(ValueError, "legacy"):
            build_websocket_url(
                "wss://rest-assistant.api.cloud.yandex.net/v1/realtime", MODEL_URI
            )

        async with FakeYandexRealtimeServer() as server:
            harness = ControllerHarness(server)
            result = await harness.controller.start_session()
            self.assertTrue(result.success)
            self.assertEqual(
                harness.connector.validated_urls,
                [build_websocket_url(CURRENT_ENDPOINT, MODEL_URI)],
            )
            self.assertEqual(server.authorization_headers, [f"Api-Key {TEST_KEY}"])
            self.assertNotIn(TEST_KEY, repr(harness.config))
            await harness.close()

            blocked_connector = LocalWebSocketConnector(server.url)
            blocked = YandexRealtimeClient(
                RuntimeConfig(
                    api_key=TEST_KEY,
                    endpoint=server.url.replace("http://", "ws://"),
                    model_uri=MODEL_URI,
                ),
                websocket_connector=blocked_connector,
            )
            with self.assertRaisesRegex(ValueError, "wss"):
                await blocked.connect(1)
            self.assertEqual(blocked_connector.validated_urls, [])

    async def test_voice_session_uses_real_wire_for_setup_uplink_and_downlink(self) -> None:
        async with FakeYandexRealtimeServer(duplicate_ready=True) as server:
            harness = ControllerHarness(server)
            result = await harness.controller.start_session()
            self.assertTrue(result.success)
            self.assertEqual(harness.controller.state, STATUS_LISTENING)
            self.assertEqual(harness.mic.start_count, 1)
            setup = await server.wait_for_client_event("session.update")
            session = setup["session"]
            self.assertEqual(session["audio"]["input"]["format"]["rate"], 24_000)
            self.assertEqual(session["audio"]["output"]["format"]["rate"], 24_000)
            self.assertEqual(session["audio"]["input"]["languages"], ["ru-RU"])
            self.assertEqual(
                session["audio"]["input"]["turn_detection"]["type"], "server_vad"
            )

            harness.mic.emit(PCM_CHUNK)
            append = await server.wait_for_client_event("input_audio_buffer.append")
            self.assertEqual(base64.b64decode(append["audio"]), PCM_CHUNK)

            await server.send_json(
                {"type": "response.created", "response": {"id": "response-a"}}
            )
            await server.send_json(
                {
                    "type": "response.output_text.delta",
                    "response_id": "response-a",
                    "delta": "Привет",
                }
            )
            await server.send_json(
                {
                    "type": "response.output_audio.delta",
                    "response_id": "response-a",
                    "item_id": "item-a",
                    "content_index": 0,
                    "delta": base64.b64encode(PCM_CHUNK).decode("ascii"),
                }
            )
            await wait_until(lambda: harness.text == ["Привет"])
            self.assertEqual(harness.controller.state, STATUS_SPEAKING_TEXT)
            events = harness.audio.drain_events()
            packet = next(event for event in events if isinstance(event, RobotAudioPacket))
            self.assertEqual(packet.pcm, PCM_CHUNK)
            self.assertEqual(packet.sample_rate, 24_000)
            self.assertEqual(packet.generation_id, harness.controller.generation_id)

            await server.send_json(
                {
                    "type": "response.done",
                    "response": {"id": "response-a", "status": "completed"},
                }
            )
            await wait_until(lambda: harness.controller.state == STATUS_LISTENING)
            self.assertEqual(server.connection_count, 1)
            self.assertEqual(harness.mic.start_count, 1)
            await harness.close()

    async def test_text_only_input_uses_real_wire_without_starting_microphone(self) -> None:
        async with FakeYandexRealtimeServer() as server:
            harness = ControllerHarness(server)
            result = await harness.controller.handle_text_input("  Как дела?  ")
            self.assertTrue(result.success)
            item = await server.wait_for_client_event("conversation.item.create")
            response = await server.wait_for_client_event("response.create")
            self.assertEqual(
                item["item"]["content"],
                [{"type": "input_text", "text": "Как дела?"}],
            )
            self.assertEqual(response, {"type": "response.create"})
            self.assertFalse(harness.mic.started)
            self.assertEqual(harness.mic.start_count, 0)
            await harness.close()

    async def test_stop_cancels_active_response_and_releases_real_transport(self) -> None:
        async with FakeYandexRealtimeServer() as server:
            harness = ControllerHarness(server)
            await harness.controller.start_session()
            await server.send_json(
                {"type": "response.created", "response": {"id": "active-response"}}
            )
            await wait_until(lambda: harness.controller.state == STATUS_SPEAKING_TEXT)
            result = await harness.controller.stop_session()
            self.assertTrue(result.success)
            cancel = await server.wait_for_client_event("response.cancel")
            self.assertEqual(cancel["response_id"], "active-response")
            self.assertEqual(harness.controller.state, STATUS_IDLE)
            self.assertFalse(harness.mic.started)
            self.assertIsNone(harness.controller.microphone_sender_task)
            self.assertEqual(harness.controller.microphone_queue_size, 0)
            self.assertIsNone(harness.client._ws)
            self.assertIsNone(harness.client._client_session)
            self.assertIsNone(harness.client._receive_task)
            self.assertEqual(harness.client._response_generations, {})
            self.assertNotIn(STATUS_ERROR, harness.statuses)
            events = harness.audio.drain_events()
            self.assertTrue(any(isinstance(event, RobotFlushEvent) for event in events))

    async def test_interruption_maps_old_response_to_stale_generation_on_real_wire(self) -> None:
        async with FakeYandexRealtimeServer() as server:
            harness = ControllerHarness(server)
            await harness.controller.start_session()
            generation_a = harness.controller.generation_id
            await server.send_json(
                {"type": "response.created", "response": {"id": "response-a"}}
            )
            await wait_until(lambda: harness.controller.state == STATUS_SPEAKING_TEXT)
            await server.send_json(
                {
                    "type": "response.output_text.delta",
                    "response_id": "response-a",
                    "delta": "heard-a",
                }
            )
            await server.send_json(
                {
                    "type": "response.output_audio.delta",
                    "response_id": "response-a",
                    "delta": base64.b64encode(PCM_CHUNK).decode("ascii"),
                }
            )
            await wait_until(lambda: harness.text == ["heard-a"])
            await server.send_json({"type": "input_audio_buffer.speech_started"})
            cancel = await server.wait_for_client_event("response.cancel")
            self.assertEqual(cancel["response_id"], "response-a")
            await wait_until(lambda: harness.controller.generation_id > generation_a)

            await server.send_json(
                {
                    "type": "response.output_text.delta",
                    "response_id": "response-a",
                    "delta": "late-a",
                }
            )
            await server.send_json(
                {
                    "type": "response.output_audio.delta",
                    "response_id": "response-a",
                    "delta": base64.b64encode(PCM_CHUNK).decode("ascii"),
                }
            )
            await server.send_json(
                {
                    "type": "response.done",
                    "response": {"id": "response-a", "status": "cancelled"},
                }
            )
            await wait_until(
                lambda: "response-a" not in harness.client._response_generations
            )
            await server.send_json(
                {"type": "response.created", "response": {"id": "response-b"}}
            )
            await server.send_json(
                {
                    "type": "response.output_text.delta",
                    "response_id": "response-b",
                    "delta": "new-b",
                }
            )
            await server.send_json(
                {
                    "type": "response.output_audio.delta",
                    "response_id": "response-b",
                    "delta": base64.b64encode(PCM_CHUNK).decode("ascii"),
                }
            )
            await wait_until(lambda: harness.text == ["heard-a", "new-b"])
            self.assertNotIn("late-a", harness.text)
            for _ in range(6):
                await server.send_json(
                    {
                        "type": "response.output_audio.delta",
                        "response_id": "response-b",
                        "delta": base64.b64encode(PCM_CHUNK).decode("ascii"),
                    }
                )
            await wait_until(lambda: harness.audio.dropped_audio_packets >= 3)
            events = harness.audio.drain_events()
            self.assertIsInstance(events[0], RobotFlushEvent)
            packets = [event for event in events if isinstance(event, RobotAudioPacket)]
            self.assertEqual(len(packets), harness.audio.max_audio_packets)
            self.assertTrue(
                all(
                    packet.generation_id == harness.controller.generation_id
                    for packet in packets
                )
            )
            self.assertEqual(harness.mic.start_count, 1)
            sender = harness.controller.microphone_sender_task

            await server.send_json({"type": "input_audio_buffer.speech_started"})
            await server.wait_for_client_event("response.cancel", occurrence=2)
            self.assertIsNot(harness.controller.microphone_sender_task, sender)
            self.assertEqual(harness.mic.start_count, 1)
            await harness.close()


if __name__ == "__main__":
    unittest.main()
