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
        self.order.append("close")

    async def send_audio(self, pcm: bytes) -> None:
        self.sent_audio.append(pcm)

    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)

    async def cancel_current_response(self) -> None:
        self.cancel_calls += 1
        self.order.append("cancel")


class FakeMicAdapter:
    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.started = False
        self.on_audio = None

    def start(self, on_audio) -> None:
        self.started = True
        self.on_audio = on_audio
        self.order.append("mic_start")

    def stop(self) -> None:
        self.started = False
        self.order.append("mic_stop")


class FakeAudioOutputAdapter:
    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.writes: list[tuple[bytes, int, int]] = []
        self.flush_count = 0

    def write(self, pcm: bytes, sample_rate: int, generation_id: int) -> None:
        self.writes.append((pcm, sample_rate, generation_id))

    def flush(self) -> None:
        self.flush_count += 1
        self.order.append("flush")


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
            status_sink=lambda value: (self.statuses.append(value), self.order.append(f"status:{value}")),
            text_result_sink=self.text_results.append,
        )

    async def test_start_transitions_idle_connecting_listening_once(self) -> None:
        self.assertEqual(self.controller.state, STATUS_IDLE)
        first = await self.controller.start_session()
        second = await self.controller.start_session()
        self.assertTrue(first.success)
        self.assertTrue(second.success)
        self.assertEqual(self.statuses, [STATUS_CONNECTING, STATUS_LISTENING])
        self.assertEqual(self.controller.state, STATUS_LISTENING)
        self.assertEqual(self.client.connect_calls, 1)
        self.assertTrue(self.mic.started)

    async def test_stop_flushes_then_closes_then_enters_idle(self) -> None:
        await self.controller.start_session()
        self.order.clear()
        result = await self.controller.stop_session()
        self.assertTrue(result.success)
        self.assertLess(self.order.index("cancel"), self.order.index("flush"))
        self.assertLess(self.order.index("flush"), self.order.index("close"))
        self.assertLess(self.order.index("close"), self.order.index(f"status:{STATUS_IDLE}"))
        self.assertFalse(self.mic.started)

    async def test_text_input_ensures_session_without_explicit_start(self) -> None:
        result = await self.controller.handle_text_input("  Привет  ")
        self.assertTrue(result.success)
        self.assertEqual(self.client.connect_calls, 1)
        self.assertEqual(self.client.sent_text, ["Привет"])
        self.assertFalse(self.mic.started)

    async def test_assistant_text_and_audio_reach_sinks(self) -> None:
        await self.controller.start_session()
        generation = self.controller.generation_id
        await self.controller.handle_event(
            RealtimeEvent(RealtimeEventKind.RESPONSE_STARTED, generation, {})
        )
        await self.controller.handle_event(
            RealtimeEvent(
                RealtimeEventKind.ASSISTANT_TEXT,
                generation,
                {"text": "Ответ"},
            )
        )
        await self.controller.handle_event(
            RealtimeEvent(
                RealtimeEventKind.ASSISTANT_AUDIO,
                generation,
                {"pcm": b"\x00\x01", "sample_rate": 24_000},
            )
        )
        self.assertEqual(self.controller.state, STATUS_SPEAKING_TEXT)
        self.assertEqual(self.text_results, ["Ответ"])
        self.assertEqual(self.audio.writes, [(b"\x00\x01", 24_000, generation)])
        await self.controller.handle_event(
            RealtimeEvent(RealtimeEventKind.RESPONSE_DONE, generation, {})
        )
        self.assertEqual(self.controller.state, STATUS_LISTENING)

    async def test_interruption_advances_generation_flushes_and_cancels(self) -> None:
        await self.controller.start_session()
        generation = self.controller.generation_id
        await self.controller.handle_event(
            RealtimeEvent(RealtimeEventKind.RESPONSE_STARTED, generation, {})
        )
        await self.controller.handle_event(
            RealtimeEvent(RealtimeEventKind.SPEECH_STARTED, generation, {})
        )
        self.assertEqual(self.controller.generation_id, generation + 1)
        self.assertEqual(self.audio.flush_count, 1)
        self.assertEqual(self.client.cancel_calls, 1)
        self.assertEqual(self.controller.state, STATUS_LISTENING)

    async def test_old_generation_text_audio_done_and_error_are_dropped(self) -> None:
        await self.controller.start_session()
        old_generation = self.controller.generation_id
        await self.controller.stop_session()
        self.statuses.clear()
        await self.controller.handle_event(
            RealtimeEvent(RealtimeEventKind.ASSISTANT_TEXT, old_generation, {"text": "late"})
        )
        await self.controller.handle_event(
            RealtimeEvent(
                RealtimeEventKind.ASSISTANT_AUDIO,
                old_generation,
                {"pcm": b"late", "sample_rate": 24_000},
            )
        )
        await self.controller.handle_event(
            RealtimeEvent(RealtimeEventKind.RESPONSE_DONE, old_generation, {})
        )
        await self.controller.handle_event(
            RealtimeEvent(RealtimeEventKind.ERROR, old_generation, {"message": "late"})
        )
        self.assertEqual(self.text_results, [])
        self.assertEqual(self.audio.writes, [])
        self.assertEqual(self.statuses, [])
        self.assertEqual(self.controller.state, STATUS_IDLE)

    async def test_session_events_are_ignored_after_stop_even_with_current_id(self) -> None:
        await self.controller.start_session()
        await self.controller.stop_session()
        stopped_generation = self.controller.generation_id
        self.statuses.clear()
        await self.controller.handle_event(
            RealtimeEvent(
                RealtimeEventKind.ERROR,
                stopped_generation,
                {"message": "late socket error"},
            )
        )
        self.assertEqual(self.controller.state, STATUS_IDLE)
        self.assertEqual(self.statuses, [])

    async def test_current_error_enters_error_state(self) -> None:
        await self.controller.start_session()
        await self.controller.handle_event(
            RealtimeEvent(
                RealtimeEventKind.ERROR,
                self.controller.generation_id,
                {"message": "connection failed"},
            )
        )
        self.assertEqual(self.controller.state, STATUS_ERROR)


if __name__ == "__main__":
    unittest.main()
