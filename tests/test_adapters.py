from collections import deque
import logging
import subprocess
import threading
import unittest

from realtime_dialog.adapters import (
    AdapterNotConfiguredError,
    ArecordMicAdapter,
    QueuedRobotAudioOutputAdapter,
    RobotAudioPacket,
    RobotFlushEvent,
    build_pcm_audio_frame,
    publish_pcm_audio_packet,
)


class FakeStdout:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = deque(chunks)
        self.closed = False

    def read(self, _size: int) -> bytes:
        if self.closed or not self.chunks:
            return b""
        return self.chunks.popleft()

    def close(self) -> None:
        self.closed = True


class BlockingStdout:
    def __init__(self) -> None:
        self.closed = False
        self._closed = threading.Event()

    def read(self, _size: int) -> bytes:
        self._closed.wait(timeout=1)
        return b""

    def close(self) -> None:
        self.closed = True
        self._closed.set()


class FakeProcess:
    def __init__(self, chunks: list[bytes], *, timeout_until_kill: bool = False) -> None:
        self.stdout = FakeStdout(chunks)
        self.timeout_until_kill = timeout_until_kill
        self.terminated = 0
        self.killed = 0
        self.wait_calls = 0
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.terminated += 1

    def kill(self) -> None:
        self.killed += 1
        self.returncode = -9
        self.stdout.close()

    def wait(self, timeout=None) -> int:
        self.wait_calls += 1
        if self.timeout_until_kill and not self.killed:
            raise subprocess.TimeoutExpired("arecord", timeout)
        self.returncode = 0
        self.stdout.close()
        return self.returncode


class RecordingProcessFactory:
    def __init__(self, process: FakeProcess) -> None:
        self.process = process
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return self.process


class ArecordMicAdapterTest(unittest.TestCase):
    def make_adapter(self, process: FakeProcess, **overrides) -> tuple[ArecordMicAdapter, RecordingProcessFactory]:
        factory = RecordingProcessFactory(process)
        options = dict(
            device="hw:0,0",
            process_factory=factory,
            executable_resolver=lambda value: f"/usr/bin/{value}",
            read_size_bytes=257,
            stop_timeout=0.01,
        )
        options.update(overrides)
        return ArecordMicAdapter(**options), factory

    def test_command_uses_file_type_raw_and_never_uses_shell(self) -> None:
        adapter, factory = self.make_adapter(FakeProcess([]))
        adapter.start(lambda _pcm: None)
        adapter.stop()
        command, kwargs = factory.calls[0]
        self.assertEqual(
            command,
            [
                "/usr/bin/arecord",
                "--device",
                "hw:0,0",
                "--format",
                "S16_LE",
                "--channels",
                "1",
                "--rate",
                "16000",
                "--file-type",
                "raw",
            ],
        )
        self.assertNotIn("--type", command)
        self.assertIs(kwargs["shell"], False)
        self.assertIs(kwargs["stderr"], subprocess.DEVNULL)

    def test_missing_device_and_executable_fail_fast(self) -> None:
        with self.assertRaisesRegex(AdapterNotConfiguredError, "mic_device"):
            ArecordMicAdapter(device="")
        adapter = ArecordMicAdapter(
            device="hw:0,0", executable_resolver=lambda _value: None
        )
        with self.assertRaisesRegex(AdapterNotConfiguredError, "executable"):
            adapter.start(lambda _pcm: None)

    def test_arbitrary_stdout_boundaries_produce_only_complete_target_chunks(self) -> None:
        source = b"".join(
            int(index - 600).to_bytes(2, "little", signed=True)
            for index in range(1200)
        )
        boundaries = [1, 18, 207, 512, 333, 729, 600]
        chunks = []
        offset = 0
        for size in boundaries:
            chunks.append(source[offset : offset + size])
            offset += size
        chunks.append(source[offset:])
        process = FakeProcess(chunks)
        adapter, _factory = self.make_adapter(process)
        received: list[bytes] = []
        adapter.start(received.append)
        adapter.reader_finished.wait(timeout=1)
        adapter.stop()
        self.assertGreaterEqual(len(received), 3)
        self.assertTrue(all(len(chunk) == 960 for chunk in received))
        self.assertIsInstance(adapter.last_error, RuntimeError)

    def test_start_and_stop_are_idempotent(self) -> None:
        adapter, factory = self.make_adapter(FakeProcess([]))
        adapter.start(lambda _pcm: None)
        adapter.start(lambda _pcm: None)
        adapter.stop()
        adapter.stop()
        self.assertEqual(len(factory.calls), 1)

    def test_stop_terminates_then_kills_after_bounded_timeout(self) -> None:
        process = FakeProcess([], timeout_until_kill=True)
        adapter, _factory = self.make_adapter(process)
        adapter.start(lambda _pcm: None)
        adapter.stop()
        self.assertEqual(process.terminated, 1)
        self.assertEqual(process.killed, 1)
        self.assertGreaterEqual(process.wait_calls, 2)

    def test_process_exit_does_not_leave_reader_thread(self) -> None:
        process = FakeProcess([])
        process.returncode = 1
        adapter, _factory = self.make_adapter(process)
        runtime_errors: list[Exception] = []
        adapter.start(lambda _pcm: None, runtime_errors.append)
        self.assertTrue(adapter.reader_finished.wait(timeout=1))
        adapter.stop()
        self.assertFalse(adapter.running)
        self.assertEqual(runtime_errors, [adapter.last_error])

    def test_stdout_eof_before_poll_update_reports_runtime_error(self) -> None:
        adapter, _factory = self.make_adapter(FakeProcess([]))
        runtime_errors: list[Exception] = []
        adapter.start(lambda _pcm: None, runtime_errors.append)
        self.assertTrue(adapter.reader_finished.wait(timeout=1))
        adapter.stop()
        self.assertEqual(runtime_errors, [adapter.last_error])
        self.assertIn("ended unexpectedly", str(adapter.last_error))

    def test_active_stop_does_not_report_runtime_error(self) -> None:
        process = FakeProcess([])
        process.stdout = BlockingStdout()
        adapter, _factory = self.make_adapter(process)
        runtime_errors: list[Exception] = []
        adapter.start(lambda _pcm: None, runtime_errors.append)
        adapter.stop()
        self.assertEqual(runtime_errors, [])

    def test_unpaired_final_stdout_byte_is_reported_not_truncated(self) -> None:
        adapter, _factory = self.make_adapter(FakeProcess([b"\x01"]))
        adapter.start(lambda _pcm: None)
        self.assertTrue(adapter.reader_finished.wait(timeout=1))
        adapter.stop()
        self.assertIsInstance(adapter.last_error, ValueError)

    def test_callback_receives_bytes_not_logged_pcm(self) -> None:
        source = b"\x00\x00" * 700
        adapter, _factory = self.make_adapter(FakeProcess([source]))
        received: list[bytes] = []
        records: list[logging.LogRecord] = []

        class Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record)

        logger = logging.getLogger("realtime_dialog.adapters")
        handler = Capture()
        logger.addHandler(handler)
        try:
            adapter.start(received.append)
            adapter.reader_finished.wait(timeout=1)
            adapter.stop()
        finally:
            logger.removeHandler(handler)
        self.assertTrue(all(isinstance(chunk, bytes) for chunk in received))
        rendered = "\n".join(record.getMessage() for record in records)
        self.assertNotIn(source.hex(), rendered)


class FakePcmAudioFrame:
    def __init__(self) -> None:
        self.stamp = None
        self.sample_rate = 0
        self.channels = 0
        self.format = ""
        self.data = []


class FakeTime:
    def __init__(self, message) -> None:
        self.message = message

    def to_msg(self):
        return self.message


class FakeClock:
    def __init__(self, message) -> None:
        self.message = message

    def now(self):
        return FakeTime(self.message)


class FakePublisher:
    def __init__(self) -> None:
        self.messages = []

    def publish(self, message) -> None:
        self.messages.append(message)


class RobotAudioOutputTest(unittest.TestCase):
    def test_pcm_audio_frame_maps_verified_fields_and_uint8_data(self) -> None:
        packet = RobotAudioPacket(b"\x00\xff\x80\x7f", 24_000, 1, 4, 2)
        stamp = object()
        message = build_pcm_audio_frame(
            packet,
            speaker_pcm_format="vendor-provided-test-value",
            stamp=stamp,
            message_type=FakePcmAudioFrame,
        )
        self.assertIs(message.stamp, stamp)
        self.assertEqual(message.sample_rate, 24_000)
        self.assertEqual(message.channels, 1)
        self.assertEqual(message.format, "vendor-provided-test-value")
        self.assertEqual(message.data, [0, 255, 128, 127])

    def test_fake_clock_and_publisher_receive_mapped_message(self) -> None:
        packet = RobotAudioPacket(b"\x01\x02", 24_000, 1, 4, 2)
        stamp = object()
        publisher = FakePublisher()
        message = publish_pcm_audio_packet(
            packet,
            speaker_pcm_format="vendor-provided-test-value",
            clock=FakeClock(stamp),
            publisher=publisher,
            message_type=FakePcmAudioFrame,
        )
        self.assertEqual(publisher.messages, [message])
        self.assertIs(message.stamp, stamp)

    def test_pcm_audio_frame_requires_format_and_rejects_odd_pcm(self) -> None:
        packet = RobotAudioPacket(b"\x00\x00", 24_000, 1, 1, 0)
        with self.assertRaisesRegex(
            AdapterNotConfiguredError,
            "PcmAudioFrame.format is not configured; vendor runtime value is unknown",
        ):
            build_pcm_audio_frame(
                packet,
                speaker_pcm_format="",
                stamp=object(),
                message_type=FakePcmAudioFrame,
            )
        with self.assertRaisesRegex(ValueError, "frame-aligned"):
            build_pcm_audio_frame(
                RobotAudioPacket(b"\x00", 24_000, 1, 1, 0),
                speaker_pcm_format="configured-by-integrator",
                stamp=object(),
                message_type=FakePcmAudioFrame,
            )

    def test_flush_invalidates_old_audio_and_orders_flush_before_new_audio(self) -> None:
        adapter = QueuedRobotAudioOutputAdapter(max_audio_packets=2)
        adapter.set_generation(3)
        self.assertTrue(adapter.write(b"\x01\x00", 24_000, 3))
        old_epoch = adapter.playback_epoch
        new_epoch = adapter.flush()
        self.assertEqual(new_epoch, old_epoch + 1)
        self.assertTrue(adapter.write(b"\x02\x00", 24_000, 3))
        events = adapter.drain_events()
        self.assertEqual(len(events), 2)
        self.assertIsInstance(events[0], RobotFlushEvent)
        self.assertIsInstance(events[1], RobotAudioPacket)
        self.assertEqual(events[1].playback_epoch, new_epoch)
        self.assertEqual(events[1].pcm, b"\x02\x00")

    def test_generation_mismatch_is_dropped_and_audio_queue_is_bounded(self) -> None:
        adapter = QueuedRobotAudioOutputAdapter(max_audio_packets=2)
        adapter.set_generation(7)
        self.assertFalse(adapter.write(b"\x00\x00", 24_000, 6))
        adapter.write(b"\x01\x00", 24_000, 7)
        adapter.write(b"\x02\x00", 24_000, 7)
        adapter.write(b"\x03\x00", 24_000, 7)
        events = adapter.drain_events()
        self.assertEqual([event.pcm for event in events], [b"\x02\x00", b"\x03\x00"])
        self.assertEqual(adapter.dropped_audio_packets, 1)

    def test_generation_change_discards_queued_audio(self) -> None:
        adapter = QueuedRobotAudioOutputAdapter()
        adapter.set_generation(1)
        adapter.write(b"\x01\x00", 24_000, 1)
        adapter.set_generation(2)
        self.assertEqual(adapter.drain_events(), [])

    def test_flush_is_a_barrier_before_new_generation_audio(self) -> None:
        adapter = QueuedRobotAudioOutputAdapter()
        adapter.set_generation(1)
        adapter.flush()
        adapter.set_generation(2)
        adapter.write(b"\x02\x00", 24_000, 2)
        events = adapter.drain_events()
        self.assertEqual(len(events), 2)
        self.assertIsInstance(events[0], RobotFlushEvent)
        self.assertIsInstance(events[1], RobotAudioPacket)
        self.assertEqual(events[0].generation_id, 1)
        self.assertEqual(events[1].generation_id, 2)

    def test_publish_guard_rejects_packet_invalidated_after_drain(self) -> None:
        adapter = QueuedRobotAudioOutputAdapter()
        adapter.set_generation(1)
        old = RobotAudioPacket(b"\x01\x00", 24_000, 1, 1, 0)
        adapter.flush()
        published: list[RobotAudioPacket] = []
        self.assertFalse(adapter.publish_if_current(old, published.append))
        current = RobotAudioPacket(
            b"\x02\x00", 24_000, 1, 1, adapter.playback_epoch
        )
        self.assertTrue(adapter.publish_if_current(current, published.append))
        self.assertEqual(published, [current])


if __name__ == "__main__":
    unittest.main()
