import asyncio
import unittest

from realtime_dialog.dialog_controller import DialogController
from realtime_dialog.ros_contract import (
    STATUS_CONNECTING,
    STATUS_ERROR,
    STATUS_IDLE,
    STATUS_LISTENING,
    STATUS_SPEAKING_TEXT,
)
from realtime_dialog.yandex_realtime_client import RealtimeEvent, RealtimeEventKind


class FakeClient:
    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.handler = None
        self.generation = 0
        self.connect_calls = 0
        self.sent_audio: list[bytes] = []
        self.sent_text: list[str] = []
        self.cancel_calls = 0
        self.close_calls = 0
        self.send_gate: asyncio.Event | None = None
        self.send_started = asyncio.Event()
        self.send_error: Exception | None = None
        self.cancel_error: Exception | None = None
        self.close_error: Exception | None = None

    def set_event_handler(self, handler) -> None:
        self.handler = handler

    def set_generation(self, generation_id: int) -> None:
        self.generation = generation_id

    async def connect(self, generation_id: int) -> None:
        self.connect_calls += 1
        self.generation = generation_id
        await self.handler(
            RealtimeEvent(RealtimeEventKind.SESSION_READY, generation_id, {})
        )

    async def close(self) -> None:
        self.close_calls += 1
        self.order.append("close")
        if self.close_error is not None:
            error = self.close_error
            self.close_error = None
            raise error

    async def send_audio(self, pcm: bytes) -> None:
        self.send_started.set()
        if self.send_gate is not None:
            await self.send_gate.wait()
        if self.send_error is not None:
            error = self.send_error
            self.send_error = None
            raise error
        self.sent_audio.append(pcm)

    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)

    async def cancel_current_response(self) -> None:
        self.cancel_calls += 1
        self.order.append("cancel")
        if self.cancel_error is not None:
            error = self.cancel_error
            self.cancel_error = None
            raise error


class FakeMicAdapter:
    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.started = False
        self.on_audio = None
        self.on_error = None
        self.error_callbacks = []
        self.emit_error_on_stop = False
        self.stop_error: Exception | None = None

    def start(self, on_audio, on_error) -> None:
        self.started = True
        self.on_audio = on_audio
        self.on_error = on_error
        self.error_callbacks.append(on_error)
        self.order.append("mic_start")

    def stop(self) -> None:
        self.order.append("mic_stop_begin")
        if self.emit_error_on_stop:
            assert self.on_error is not None
            self.on_error(RuntimeError("expected active stop"))
        self.started = False
        if self.stop_error is not None:
            error = self.stop_error
            self.stop_error = None
            raise error
        self.order.append("mic_stop_end")

    def emit(self, pcm: bytes) -> None:
        assert self.on_audio is not None
        self.on_audio(pcm)

    def fail(self, error: Exception) -> None:
        assert self.on_error is not None
        self.on_error(error)


class FakeAudioOutputAdapter:
    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.generation = 0
        self.writes: list[tuple[bytes, int, int]] = []
        self.flush_count = 0

    def set_generation(self, generation_id: int) -> None:
        self.generation = generation_id

    def write(self, pcm: bytes, sample_rate: int, generation_id: int) -> bool:
        if generation_id != self.generation:
            return False
        self.writes.append((pcm, sample_rate, generation_id))
        return True

    def flush(self) -> int:
        self.flush_count += 1
        self.order.append("flush")
        return self.flush_count


class DialogControllerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.order: list[str] = []
        self.client = FakeClient(self.order)
        self.mic = FakeMicAdapter(self.order)
        self.audio = FakeAudioOutputAdapter(self.order)
        self.statuses: list[str] = []
        self.text_results: list[str] = []
        self.controller = DialogController(
            client=self.client,
            mic_adapter=self.mic,
            audio_output=self.audio,
            status_sink=lambda value: (
                self.statuses.append(value),
                self.order.append(f"status:{value}"),
            ),
            text_result_sink=self.text_results.append,
            microphone_queue_chunks=2,
        )

    async def asyncTearDown(self) -> None:
        self.client.send_gate = None
        if self.controller.state != STATUS_IDLE:
            await self.controller.stop_session()

    async def test_start_transitions_and_has_exactly_one_sender_task(self) -> None:
        first = await self.controller.start_session()
        sender = self.controller.microphone_sender_task
        second = await self.controller.start_session()
        self.assertTrue(first.success and second.success)
        self.assertEqual(self.statuses, [STATUS_CONNECTING, STATUS_LISTENING])
        self.assertEqual(self.client.connect_calls, 1)
        self.assertTrue(self.mic.started)
        self.assertIs(self.controller.microphone_sender_task, sender)
        self.assertIsNotNone(sender)
        self.assertFalse(sender.done())

    async def test_bounded_queue_drops_oldest_and_single_sender_preserves_order(self) -> None:
        self.client.send_gate = asyncio.Event()
        await self.controller.start_session()
        self.mic.emit(b"first")
        await asyncio.wait_for(self.client.send_started.wait(), timeout=1)
        self.mic.emit(b"drop-me")
        self.mic.emit(b"second")
        self.mic.emit(b"third")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertEqual(self.controller.microphone_queue_size, 2)
        self.assertEqual(self.controller.microphone_dropped_chunks, 1)
        self.client.send_gate.set()
        for _ in range(10):
            await asyncio.sleep(0)
            if len(self.client.sent_audio) == 3:
                break
        self.assertEqual(self.client.sent_audio, [b"first", b"second", b"third"])

    async def test_stop_flushes_before_mic_shutdown_and_network_cleanup(self) -> None:
        await self.controller.start_session()
        self.order.clear()
        result = await self.controller.stop_session()
        self.assertTrue(result.success)
        self.assertLess(self.order.index("flush"), self.order.index("mic_stop_begin"))
        self.assertLess(self.order.index("mic_stop_end"), self.order.index("cancel"))
        self.assertLess(self.order.index("cancel"), self.order.index("close"))
        self.assertLess(self.order.index("close"), self.order.index(f"status:{STATUS_IDLE}"))
        self.assertEqual(self.controller.microphone_queue_size, 0)
        self.assertIsNone(self.controller.microphone_sender_task)

    async def test_active_microphone_stop_callback_does_not_enter_error(self) -> None:
        await self.controller.start_session()
        self.mic.emit_error_on_stop = True
        result = await self.controller.stop_session()
        await asyncio.sleep(0)
        self.assertTrue(result.success)
        self.assertEqual(self.controller.state, STATUS_IDLE)
        self.assertNotIn(STATUS_ERROR, self.statuses)

    async def test_current_microphone_runtime_error_enters_error(self) -> None:
        await self.controller.start_session()
        self.mic.fail(RuntimeError("arecord exited"))
        for _ in range(10):
            await asyncio.sleep(0)
            if not self.mic.started:
                break
        self.assertEqual(self.controller.state, STATUS_ERROR)
        self.assertFalse(self.mic.started)
        self.assertIsNone(self.controller.microphone_sender_task)

    async def test_microphone_runtime_error_allows_capture_restart(self) -> None:
        await self.controller.start_session()
        starts_before_failure = len(self.mic.error_callbacks)
        self.mic.fail(RuntimeError("arecord exited"))
        await asyncio.sleep(0)
        restarted = await self.controller.start_session()
        self.assertTrue(restarted.success)
        self.assertTrue(self.mic.started)
        self.assertEqual(len(self.mic.error_callbacks), starts_before_failure + 1)

    async def test_stale_microphone_runtime_error_does_not_pollute_new_generation(self) -> None:
        await self.controller.start_session()
        old_error_callback = self.mic.error_callbacks[-1]
        await self.controller.stop_session()
        restarted = await self.controller.start_session()
        old_error_callback(RuntimeError("old arecord failure"))
        await asyncio.sleep(0)
        self.assertTrue(restarted.success)
        self.assertNotEqual(self.controller.state, STATUS_ERROR)

    async def test_cancel_failure_still_closes_transport_and_reports_error(self) -> None:
        await self.controller.start_session()
        self.client.cancel_error = RuntimeError("cancel failed")
        self.order.clear()
        result = await self.controller.stop_session()
        self.assertFalse(result.success)
        self.assertIn("cancel failed", result.message)
        self.assertEqual(self.client.close_calls, 1)
        self.assertLess(self.order.index("flush"), self.order.index("cancel"))
        self.assertLess(self.order.index("cancel"), self.order.index("close"))
        self.assertEqual(self.controller.state, STATUS_ERROR)

    async def test_close_failure_reports_error(self) -> None:
        await self.controller.start_session()
        self.client.close_error = RuntimeError("close failed")
        result = await self.controller.stop_session()
        self.assertFalse(result.success)
        self.assertIn("close failed", result.message)
        self.assertEqual(self.client.cancel_calls, 1)
        self.assertEqual(self.client.close_calls, 1)
        self.assertEqual(self.controller.state, STATUS_ERROR)

    async def test_microphone_stop_failure_still_closes_transport(self) -> None:
        await self.controller.start_session()
        self.mic.stop_error = RuntimeError("microphone stop failed")
        self.order.clear()
        result = await self.controller.stop_session()
        self.assertFalse(result.success)
        self.assertIn("microphone stop failed", result.message)
        self.assertEqual(self.client.cancel_calls, 1)
        self.assertEqual(self.client.close_calls, 1)
        self.assertLess(self.order.index("flush"), self.order.index("mic_stop_begin"))
        self.assertLess(self.order.index("mic_stop_begin"), self.order.index("cancel"))
        self.assertLess(self.order.index("cancel"), self.order.index("close"))
        self.assertIsNone(self.controller.microphone_sender_task)
        self.assertEqual(self.controller.microphone_queue_size, 0)
        self.assertEqual(self.controller.state, STATUS_ERROR)

    async def test_generation_change_drops_queued_old_microphone_audio(self) -> None:
        self.client.send_gate = asyncio.Event()
        await self.controller.start_session()
        old_generation = self.controller.generation_id
        self.mic.emit(b"in-flight")
        await asyncio.wait_for(self.client.send_started.wait(), timeout=1)
        self.controller._offer_microphone_audio(b"old-queued", old_generation)
        replacement = asyncio.create_task(self.controller.handle_text_input("Новый"))
        await asyncio.sleep(0)
        self.assertEqual(self.controller.microphone_queue_size, 0)
        self.client.send_gate.set()
        await replacement
        self.assertNotIn(b"old-queued", self.client.sent_audio)

    async def test_text_replacement_flushes_before_cancel(self) -> None:
        await self.controller.start_session()
        self.order.clear()
        result = await self.controller.handle_text_input("Новый вопрос")
        self.assertTrue(result.success)
        self.assertLess(self.order.index("flush"), self.order.index("cancel"))

    async def test_speech_started_barge_in_flushes_before_cancel(self) -> None:
        await self.controller.start_session()
        generation = self.controller.generation_id
        await self.controller.handle_event(
            RealtimeEvent(RealtimeEventKind.RESPONSE_STARTED, generation, {})
        )
        self.order.clear()
        await self.controller.handle_event(
            RealtimeEvent(RealtimeEventKind.SPEECH_STARTED, generation, {})
        )
        self.assertLess(self.order.index("flush"), self.order.index("cancel"))
        self.assertEqual(self.controller.generation_id, generation + 1)
        self.assertEqual(self.controller.state, STATUS_LISTENING)

    async def test_text_input_ensures_text_only_session(self) -> None:
        result = await self.controller.handle_text_input("  Привет  ")
        self.assertTrue(result.success)
        self.assertEqual(self.client.sent_text, ["Привет"])
        self.assertFalse(self.mic.started)
        self.assertIsNone(self.controller.microphone_sender_task)

    async def test_assistant_text_and_audio_reach_sinks(self) -> None:
        await self.controller.start_session()
        generation = self.controller.generation_id
        await self.controller.handle_event(
            RealtimeEvent(RealtimeEventKind.RESPONSE_STARTED, generation, {})
        )
        await self.controller.handle_event(
            RealtimeEvent(
                RealtimeEventKind.ASSISTANT_TEXT, generation, {"text": "Ответ"}
            )
        )
        await self.controller.handle_event(
            RealtimeEvent(
                RealtimeEventKind.ASSISTANT_AUDIO,
                generation,
                {"pcm": b"\x00\x01", "sample_rate": 24_000},
            )
        )
        self.assertEqual(self.text_results, ["Ответ"])
        self.assertEqual(self.audio.writes, [(b"\x00\x01", 24_000, generation)])

    async def test_stale_text_audio_done_and_error_are_rejected(self) -> None:
        await self.controller.start_session()
        old_generation = self.controller.generation_id
        await self.controller.stop_session()
        self.statuses.clear()
        for event in (
            RealtimeEvent(RealtimeEventKind.ASSISTANT_TEXT, old_generation, {"text": "late"}),
            RealtimeEvent(
                RealtimeEventKind.ASSISTANT_AUDIO,
                old_generation,
                {"pcm": b"late", "sample_rate": 24_000},
            ),
            RealtimeEvent(RealtimeEventKind.RESPONSE_DONE, old_generation, {}),
            RealtimeEvent(RealtimeEventKind.ERROR, old_generation, {"message": "late"}),
        ):
            await self.controller.handle_event(event)
        self.assertEqual(self.text_results, [])
        self.assertEqual(self.audio.writes, [])
        self.assertEqual(self.statuses, [])
        self.assertEqual(self.controller.state, STATUS_IDLE)

    async def test_sender_error_only_changes_current_generation(self) -> None:
        self.client.send_error = RuntimeError("send failed")
        await self.controller.start_session()
        self.mic.emit(b"current")
        for _ in range(10):
            await asyncio.sleep(0)
            if self.controller.state == STATUS_ERROR:
                break
        self.assertEqual(self.controller.state, STATUS_ERROR)

    async def test_stale_inflight_sender_error_does_not_change_new_generation(self) -> None:
        self.client.send_gate = asyncio.Event()
        self.client.send_error = RuntimeError("old send failed")
        await self.controller.start_session()
        self.mic.emit(b"old")
        await asyncio.wait_for(self.client.send_started.wait(), timeout=1)
        replacement = asyncio.create_task(self.controller.handle_text_input("Новый"))
        await asyncio.sleep(0)
        self.client.send_gate.set()
        await replacement
        await asyncio.sleep(0)
        self.assertNotEqual(self.controller.state, STATUS_ERROR)
        self.assertNotIn(b"old", self.client.sent_audio)


if __name__ == "__main__":
    unittest.main()
