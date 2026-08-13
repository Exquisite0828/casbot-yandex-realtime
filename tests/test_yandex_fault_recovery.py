import asyncio
import base64
import json
import logging
from types import SimpleNamespace
import time
import traceback
import unittest

from fake_yandex_server import FakeYandexRealtimeServer
from realtime_dialog.adapters import (
    QueuedRobotAudioOutputAdapter,
    RobotAudioPacket,
    RobotFlushEvent,
)
from realtime_dialog.dialog_controller import DialogController
from realtime_dialog.ros_contract import STATUS_ERROR, STATUS_LISTENING, STATUS_SPEAKING_TEXT
from realtime_dialog.yandex_realtime_client import (
    CURRENT_ENDPOINT,
    RealtimeEvent,
    RealtimeEventKind,
    RuntimeConfig,
    YandexRealtimeClient,
)
from test_yandex_websocket_integration import (
    ControllerHarness,
    FakeMicAdapter,
    PCM_CHUNK,
    TEST_KEY,
    wait_until,
)


REDACTION_SECRET = "phase6-fake-secret-never-real"


class ControlledWebSocket:
    """Constructor-injected transport used only for local fault tests."""

    close_code = 1000

    def __init__(
        self,
        *,
        send_failure_type: str | None = None,
        receive_error: Exception | None = None,
        close_error: Exception | None = None,
    ) -> None:
        self.closed = False
        self.sent: list[dict[str, object]] = []
        self.send_failure_type = send_failure_type
        self.receive_error = receive_error
        self.close_error = close_error
        self.close_called = False
        self._setup_sent = asyncio.Event()
        self._receive_gate = asyncio.Event()
        self._ready_delivered = False

    async def send_json(self, event: dict[str, object]) -> None:
        if event.get("type") == self.send_failure_type:
            raise RuntimeError(f"Authorization: Api-Key {REDACTION_SECRET}")
        self.sent.append(event)
        if event.get("type") == "session.update":
            self._setup_sent.set()

    def __aiter__(self):
        return self

    async def __anext__(self):
        import aiohttp

        if not self._ready_delivered:
            await self._setup_sent.wait()
            self._ready_delivered = True
            return SimpleNamespace(
                type=aiohttp.WSMsgType.TEXT,
                data=json.dumps({"type": "session.updated", "session": {}}),
            )
        await self._receive_gate.wait()
        if self.receive_error is not None:
            error = self.receive_error
            self.receive_error = None
            raise error
        raise StopAsyncIteration

    def trigger_receive_failure(self) -> None:
        self._receive_gate.set()

    async def close(self) -> None:
        self.close_called = True
        self._receive_gate.set()
        if self.close_error is not None:
            raise self.close_error
        self.closed = True


class ControlledConnector:
    def __init__(
        self,
        websocket: ControlledWebSocket | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.websocket = websocket
        self.error = error
        self.sessions: list[object] = []

    async def __call__(self, session, _validated_url, _headers):
        self.sessions.append(session)
        if self.error is not None:
            raise self.error
        assert self.websocket is not None
        return self.websocket


class ControlledSession:
    def __init__(self, *, close_error: Exception | None = None) -> None:
        self.closed = False
        self.close_called = False
        self.close_error = close_error

    async def close(self) -> None:
        self.close_called = True
        if self.close_error is not None:
            raise self.close_error
        self.closed = True


class CapturedLogHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(self.format(record))


class YandexFaultRecoveryTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.log_handler = CapturedLogHandler()
        logging.getLogger().addHandler(self.log_handler)

    def tearDown(self) -> None:
        logging.getLogger().removeHandler(self.log_handler)

    def _config(self) -> RuntimeConfig:
        return RuntimeConfig(
            api_key=REDACTION_SECRET,
            endpoint=CURRENT_ENDPOINT,
            model_uri="gpt://phase6-fake-folder/speech-realtime-260528",
            setup_timeout=0.2,
            connect_timeout=0.2,
        )

    def _controller_for(self, connector: ControlledConnector):
        client = YandexRealtimeClient(
            self._config(), websocket_connector=connector
        )
        events: list[RealtimeEvent] = []
        mic = FakeMicAdapter()
        audio = QueuedRobotAudioOutputAdapter(max_audio_packets=4)
        controller = DialogController(
            client=client,
            mic_adapter=mic,
            audio_output=audio,
            status_sink=lambda _status: None,
            text_result_sink=lambda _text: None,
        )

        async def capture(event: RealtimeEvent) -> None:
            events.append(event)
            await controller.handle_event(event)

        client.set_event_handler(capture)
        return SimpleNamespace(
            client=client,
            controller=controller,
            mic=mic,
            audio=audio,
            events=events,
        )

    def _assert_redacted(self, *values: object) -> None:
        collected: list[str] = []
        for value in values:
            if isinstance(value, BaseException):
                collected.extend(traceback.format_exception(value))
            elif isinstance(value, RealtimeEvent):
                collected.append(str(value.data.get("message", "")))
            elif value is not None:
                collected.append(str(value))
        collected.extend(self.log_handler.messages)
        self.assertNotIn(REDACTION_SECRET, "\n".join(collected))

    async def test_connector_exception_is_redacted_end_to_end(self) -> None:
        connector_error = RuntimeError(
            f"Authorization: Api-Key {REDACTION_SECRET}"
        )
        direct_client = YandexRealtimeClient(
            self._config(),
            websocket_connector=ControlledConnector(error=connector_error),
        )
        with self.assertRaises(RuntimeError) as raised:
            await direct_client.connect(1)
        self._assert_redacted(raised.exception)
        self.assertIsNone(direct_client._ws)
        self.assertIsNone(direct_client._client_session)

        harness = self._controller_for(
            ControlledConnector(
                error=RuntimeError(
                    f"Authorization: Api-Key {REDACTION_SECRET}"
                )
            )
        )
        result = await harness.controller.start_session()
        self.assertFalse(result.success)
        self.assertEqual(harness.controller.state, STATUS_ERROR)
        self._assert_redacted(
            result.message,
            harness.controller.last_error,
            *harness.events,
        )
        self.assertIsNone(harness.client._client_session)

    async def test_generic_receive_exception_is_redacted_end_to_end(self) -> None:
        websocket = ControlledWebSocket(
            receive_error=RuntimeError(
                f"api_key={REDACTION_SECRET}"
            )
        )
        harness = self._controller_for(ControlledConnector(websocket))
        result = await harness.controller.start_session()
        self.assertTrue(result.success)

        websocket.trigger_receive_failure()
        await wait_until(lambda: harness.controller.state == STATUS_ERROR)
        await wait_until(lambda: harness.client._client_session is None)

        error_events = [
            event
            for event in harness.events
            if event.kind is RealtimeEventKind.ERROR
        ]
        self.assertEqual(len(error_events), 1)
        self._assert_redacted(
            result.message,
            harness.controller.last_error,
            *error_events,
        )
        self.assertFalse(harness.mic.started)
        self.assertIsNone(harness.controller.microphone_sender_task)

    async def test_send_exception_is_redacted_after_fatal_cleanup(self) -> None:
        direct_client = YandexRealtimeClient(self._config())
        direct_client._ws = ControlledWebSocket(
            send_failure_type="conversation.item.create"
        )
        with self.assertRaises(RuntimeError) as raised:
            await direct_client.send_text("Проверка")
        self._assert_redacted(raised.exception)
        await direct_client.close()

        websocket = ControlledWebSocket(
            send_failure_type="conversation.item.create"
        )
        harness = self._controller_for(ControlledConnector(websocket))
        result = await harness.controller.handle_text_input("Проверка")

        self.assertFalse(result.success)
        self.assertEqual(harness.controller.state, STATUS_ERROR)
        self._assert_redacted(
            result.message,
            harness.controller.last_error,
            *harness.events,
        )
        self.assertTrue(websocket.close_called)
        self.assertIsNone(harness.client._ws)
        self.assertIsNone(harness.client._client_session)
        self.assertIsNone(harness.controller.microphone_sender_task)

    async def test_websocket_close_exception_is_redacted_and_session_closes(self) -> None:
        direct_client = YandexRealtimeClient(self._config())
        direct_session = ControlledSession()
        direct_client._ws = ControlledWebSocket(
            close_error=RuntimeError(
                f"Authorization: Api-Key {REDACTION_SECRET}"
            )
        )
        direct_client._client_session = direct_session
        with self.assertRaises(RuntimeError) as raised:
            await direct_client.close()
        self._assert_redacted(raised.exception)
        self.assertTrue(direct_session.closed)

        websocket = ControlledWebSocket(
            close_error=RuntimeError(
                f"Authorization: Api-Key {REDACTION_SECRET}"
            )
        )
        connector = ControlledConnector(websocket)
        harness = self._controller_for(connector)
        started = await harness.controller.start_session()
        self.assertTrue(started.success)
        session = connector.sessions[0]

        result = await harness.controller.stop_session()

        self.assertFalse(result.success)
        self.assertTrue(websocket.close_called)
        self.assertTrue(session.closed)
        self.assertIsNone(harness.client._ws)
        self.assertIsNone(harness.client._client_session)
        self._assert_redacted(
            result.message,
            harness.controller.last_error,
            *harness.events,
        )

    async def test_client_session_close_exception_is_redacted_and_refs_clear(self) -> None:
        direct_client = YandexRealtimeClient(self._config())
        direct_session = ControlledSession(
            close_error=RuntimeError(f"api_key={REDACTION_SECRET}")
        )
        direct_client._client_session = direct_session
        with self.assertRaises(RuntimeError) as raised:
            await direct_client.close()
        self._assert_redacted(raised.exception)
        self.assertTrue(direct_session.close_called)
        self.assertIsNone(direct_client._client_session)

        websocket = ControlledWebSocket()
        connector = ControlledConnector(websocket)
        harness = self._controller_for(connector)
        started = await harness.controller.start_session()
        self.assertTrue(started.success)
        real_session = connector.sessions[0]
        await real_session.close()
        failing_session = ControlledSession(
            close_error=RuntimeError(f"api_key={REDACTION_SECRET}")
        )
        harness.client._client_session = failing_session

        result = await harness.controller.stop_session()

        self.assertFalse(result.success)
        self.assertTrue(failing_session.close_called)
        self.assertIsNone(harness.client._ws)
        self.assertIsNone(harness.client._client_session)
        self._assert_redacted(
            result.message,
            harness.controller.last_error,
            *harness.events,
        )

    async def test_fake_server_cleanup_continues_after_websocket_close_failure(self) -> None:
        close_order: list[str] = []

        class WebSocket:
            closed = False

            def __init__(self, name: str, error: Exception | None = None) -> None:
                self.name = name
                self.error = error

            async def close(self) -> None:
                close_order.append(self.name)
                if self.error is not None:
                    raise self.error
                self.closed = True

        class Runner:
            def __init__(self) -> None:
                self.cleaned = False

            async def cleanup(self) -> None:
                self.cleaned = True

        server = FakeYandexRealtimeServer()
        failing = WebSocket("failing", RuntimeError("scripted close failure"))
        remaining = WebSocket("remaining")
        runner = Runner()
        server.websockets.extend([failing, remaining])
        server._runner = runner
        server._site = object()

        with self.assertRaisesRegex(RuntimeError, "scripted close failure"):
            await server.__aexit__(None, None, None)

        self.assertEqual(close_order, ["failing", "remaining"])
        self.assertTrue(remaining.closed)
        self.assertTrue(runner.cleaned)
        self.assertIsNone(server._runner)
        self.assertIsNone(server._site)
        self.assertEqual(server.websockets, [])

    async def _assert_failed_cleanly(self, harness: ControllerHarness) -> None:
        await wait_until(lambda: harness.controller.state == STATUS_ERROR)
        await wait_until(lambda: harness.client._client_session is None)
        self.assertFalse(harness.mic.started)
        self.assertIsNone(harness.controller.microphone_sender_task)
        self.assertEqual(harness.controller.microphone_queue_size, 0)
        self.assertIsNone(harness.client._ws)
        self.assertIsNone(harness.client._receive_task)
        self.assertEqual(harness.client._response_generations, {})
        events = harness.audio.drain_events()
        self.assertTrue(any(isinstance(event, RobotFlushEvent) for event in events))
        self.assertFalse(any(isinstance(event, RobotAudioPacket) for event in events))

    async def test_setup_timeout_closes_all_resources_without_starting_microphone(self) -> None:
        async with FakeYandexRealtimeServer(auto_ready=False) as server:
            harness = ControllerHarness(server, setup_timeout=0.05)
            result = await harness.controller.start_session()
            self.assertFalse(result.success)
            self.assertIn("session start failed", result.message)
            self.assertEqual(harness.controller.state, STATUS_ERROR)
            self.assertEqual(harness.mic.start_count, 0)
            self.assertIsNone(harness.client._ws)
            self.assertIsNone(harness.client._client_session)
            self.assertIsNone(harness.client._receive_task)
            self.assertEqual(harness.client._response_generations, {})

    async def test_server_error_before_ready_fails_fast_and_redacts_secret(self) -> None:
        async with FakeYandexRealtimeServer(auto_ready=False) as server:
            harness = ControllerHarness(server, setup_timeout=0.8)
            started_at = time.monotonic()
            start_task = asyncio.create_task(harness.controller.start_session())
            await server.wait_for_client_event("session.update")
            await server.send_json(
                {
                    "type": "error",
                    "error": {
                        "type": "bad_request",
                        "code": "bad_setup",
                        "param": "session",
                        "message": f"Authorization: Api-Key {TEST_KEY}",
                    },
                }
            )
            result = await asyncio.wait_for(start_task, timeout=0.3)
            self.assertFalse(result.success)
            self.assertLess(time.monotonic() - started_at, 0.5)
            self.assertNotIn(TEST_KEY, result.message)
            self.assertIn("bad_setup", result.message)
            self.assertEqual(harness.mic.start_count, 0)
            self.assertIsNone(harness.client._client_session)

    async def test_malformed_json_before_ready_is_fatal_without_waiting_timeout(self) -> None:
        async with FakeYandexRealtimeServer(auto_ready=False) as server:
            harness = ControllerHarness(server, setup_timeout=0.8)
            start_task = asyncio.create_task(harness.controller.start_session())
            await server.wait_for_client_event("session.update")
            await server.send_malformed()
            result = await asyncio.wait_for(start_task, timeout=0.3)
            self.assertFalse(result.success)
            self.assertIn("non-JSON", result.message)
            self.assertEqual(harness.controller.state, STATUS_ERROR)
            self.assertIsNone(harness.client._client_session)

    async def test_server_error_after_ready_runs_central_failure_cleanup(self) -> None:
        async with FakeYandexRealtimeServer() as server:
            harness = ControllerHarness(server)
            await harness.controller.start_session()
            await server.send_json(
                {
                    "type": "error",
                    "error": {
                        "type": "server_error",
                        "code": "fault",
                        "message": f"api_key={TEST_KEY}",
                    },
                }
            )
            await self._assert_failed_cleanly(harness)
            self.assertEqual(server.connection_count, 1)

    async def test_invalid_audio_is_fatal_and_never_reaches_output(self) -> None:
        async with FakeYandexRealtimeServer() as server:
            harness = ControllerHarness(server)
            await harness.controller.start_session()
            await server.send_json(
                {"type": "response.created", "response": {"id": "response-invalid"}}
            )
            await wait_until(lambda: harness.controller.state == STATUS_SPEAKING_TEXT)
            await server.send_json(
                {
                    "type": "response.output_audio.delta",
                    "response_id": "response-invalid",
                    "delta": "not-base64@@",
                }
            )
            await self._assert_failed_cleanly(harness)

    async def test_malformed_json_after_ready_is_fatal_and_cleans_transport(self) -> None:
        async with FakeYandexRealtimeServer() as server:
            harness = ControllerHarness(server)
            await harness.controller.start_session()
            await server.send_malformed()
            await self._assert_failed_cleanly(harness)

    async def test_unexpected_disconnect_while_listening_cleans_active_session(self) -> None:
        async with FakeYandexRealtimeServer() as server:
            harness = ControllerHarness(server)
            await harness.controller.start_session()
            self.assertEqual(harness.controller.state, STATUS_LISTENING)
            await server.disconnect()
            await self._assert_failed_cleanly(harness)

    async def test_unexpected_disconnect_while_speaking_invalidates_queued_audio(self) -> None:
        async with FakeYandexRealtimeServer() as server:
            harness = ControllerHarness(server)
            await harness.controller.start_session()
            await server.send_json(
                {"type": "response.created", "response": {"id": "response-speaking"}}
            )
            await server.send_json(
                {
                    "type": "response.output_audio.delta",
                    "response_id": "response-speaking",
                    "delta": base64.b64encode(PCM_CHUNK).decode("ascii"),
                }
            )
            await wait_until(lambda: harness.controller.state == STATUS_SPEAKING_TEXT)
            await server.disconnect()
            await self._assert_failed_cleanly(harness)

    async def test_disconnect_during_send_audio_stops_the_single_sender(self) -> None:
        async with FakeYandexRealtimeServer(
            close_on_client_event="input_audio_buffer.append"
        ) as server:
            harness = ControllerHarness(server)
            await harness.controller.start_session()
            harness.mic.emit(PCM_CHUNK)
            await server.wait_for_client_event("input_audio_buffer.append")
            await self._assert_failed_cleanly(harness)

    async def test_stop_after_dead_transport_is_bounded_and_idempotent(self) -> None:
        async with FakeYandexRealtimeServer() as server:
            harness = ControllerHarness(server)
            await harness.controller.start_session()
            await server.disconnect()
            await self._assert_failed_cleanly(harness)
            first = await asyncio.wait_for(harness.controller.stop_session(), timeout=0.5)
            second = await asyncio.wait_for(harness.controller.stop_session(), timeout=0.5)
            self.assertTrue(first.success)
            self.assertTrue(second.success)
            self.assertIsNone(harness.client._client_session)

    async def test_explicit_start_recovers_with_new_connection_and_generation(self) -> None:
        async with FakeYandexRealtimeServer() as server:
            harness = ControllerHarness(server)
            await harness.controller.start_session()
            old_generation = harness.controller.generation_id
            old_connection_token = harness.client._connection_token
            old_sender = harness.controller.microphone_sender_task
            old_receiver = harness.client._receive_task
            await server.send_json(
                {"type": "response.created", "response": {"id": "old-response"}}
            )
            await server.disconnect()
            await self._assert_failed_cleanly(harness)

            recovered = await harness.controller.start_session()
            self.assertTrue(recovered.success)
            await server.wait_for_connection(2)
            self.assertEqual(harness.controller.state, STATUS_LISTENING)
            self.assertGreater(harness.controller.generation_id, old_generation)
            self.assertIsNot(harness.controller.microphone_sender_task, old_sender)
            self.assertIsNot(harness.client._receive_task, old_receiver)
            self.assertTrue(old_receiver.done())
            self.assertTrue(harness.mic.started)
            self.assertEqual(harness.mic.start_count, 2)
            self.assertEqual(server.connection_count, 2)

            await harness.client._dispatch(
                RealtimeEvent(
                    RealtimeEventKind.TRANSPORT_CLOSED,
                    old_generation,
                    {"message": "late old connection close"},
                ),
                old_connection_token,
            )
            await asyncio.sleep(0)
            self.assertEqual(harness.controller.state, STATUS_LISTENING)

            await server.send_json(
                {"type": "response.created", "response": {"id": "new-response"}}
            )
            await server.send_json(
                {
                    "type": "response.output_text.delta",
                    "response_id": "new-response",
                    "delta": "recovered",
                }
            )
            await server.send_json(
                {
                    "type": "response.output_audio.delta",
                    "response_id": "new-response",
                    "delta": base64.b64encode(PCM_CHUNK).decode("ascii"),
                }
            )
            await wait_until(lambda: harness.text == ["recovered"])
            packets = [
                event
                for event in harness.audio.drain_events()
                if isinstance(event, RobotAudioPacket)
            ]
            self.assertEqual(len(packets), 1)
            await harness.close()

    async def test_text_input_recovers_from_error_without_starting_microphone(self) -> None:
        async with FakeYandexRealtimeServer() as server:
            harness = ControllerHarness(server)
            await harness.controller.start_session()
            await server.disconnect()
            await self._assert_failed_cleanly(harness)
            starts_before = harness.mic.start_count

            result = await harness.controller.handle_text_input("Восстановись")
            self.assertTrue(result.success)
            await server.wait_for_connection(2)
            item = await server.wait_for_client_event(
                "conversation.item.create", connection_index=1
            )
            self.assertEqual(item["item"]["content"][0]["text"], "Восстановись")
            self.assertFalse(harness.mic.started)
            self.assertEqual(harness.mic.start_count, starts_before)
            await harness.close()

    async def test_five_connect_stop_cycles_release_every_resource(self) -> None:
        async with FakeYandexRealtimeServer() as server:
            harness = ControllerHarness(server)
            for expected_connection_count in range(1, 6):
                started = await harness.controller.start_session()
                self.assertTrue(started.success)
                await server.wait_for_connection(expected_connection_count)
                sender = harness.controller.microphone_sender_task
                websocket = harness.client._ws
                client_session = harness.client._client_session
                self.assertIsNotNone(sender)
                stopped = await harness.controller.stop_session()
                self.assertTrue(stopped.success)
                self.assertTrue(sender.done())
                self.assertFalse(harness.mic.started)
                self.assertIsNone(harness.controller.microphone_sender_task)
                self.assertEqual(harness.controller.microphone_queue_size, 0)
                self.assertIsNone(harness.client._ws)
                self.assertIsNone(harness.client._client_session)
                self.assertIsNone(harness.client._receive_task)
                self.assertTrue(websocket.closed)
                self.assertTrue(client_session.closed)
                harness.audio.drain_events()
            self.assertEqual(server.connection_count, 5)
            active_transport_tasks = [
                task.get_name()
                for task in asyncio.all_tasks()
                if task is not asyncio.current_task()
                and not task.done()
                and task.get_name().startswith("yandex-")
            ]
            self.assertEqual(active_transport_tasks, [])


if __name__ == "__main__":
    unittest.main()
