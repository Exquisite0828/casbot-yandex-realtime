"""Robot-specific audio adapters with no ROS dependency in their core."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import logging
import shutil
import subprocess
import threading
from typing import Any, Callable, Protocol

from .audio_pipeline import (
    Pcm16MonoRechunker,
    Pcm16MonoResampler,
    validate_pcm16_mono,
)


LOG = logging.getLogger(__name__)
AudioInputCallback = Callable[[bytes], None]
MicRuntimeErrorCallback = Callable[[Exception], None]


class MicAdapter(Protocol):
    def start(
        self,
        on_audio: AudioInputCallback,
        on_error: MicRuntimeErrorCallback,
    ) -> None: ...

    def stop(self) -> None: ...


class AudioOutputAdapter(Protocol):
    def set_generation(self, generation_id: int) -> None: ...

    def write(self, pcm: bytes, sample_rate: int, generation_id: int) -> bool: ...

    def flush(self) -> int: ...


class AdapterNotConfiguredError(RuntimeError):
    """Raised when an integration-required robot value is unavailable."""


class ArecordMicAdapter:
    """Capture verified PCM16 mono input through a configurable ``arecord``."""

    def __init__(
        self,
        *,
        device: str,
        executable: str = "arecord",
        source_sample_rate: int = 16_000,
        target_sample_rate: int = 24_000,
        channels: int = 1,
        pcm_format: str = "S16_LE",
        chunk_ms: int = 20,
        read_size_bytes: int = 4096,
        stop_timeout: float = 1.0,
        process_factory: Callable[..., Any] = subprocess.Popen,
        executable_resolver: Callable[[str], str | None] = shutil.which,
    ) -> None:
        if not device.strip():
            raise AdapterNotConfiguredError(
                "mic_device is required for the robot arecord backend"
            )
        if channels != 1:
            raise ValueError("mic_channels must be 1")
        if pcm_format != "S16_LE":
            raise ValueError("mic_format must be S16_LE for the verified capture path")
        if read_size_bytes <= 0:
            raise ValueError("read_size_bytes must be greater than zero")
        if stop_timeout <= 0:
            raise ValueError("stop_timeout must be greater than zero")
        self.device = device
        self.executable = executable
        self.source_sample_rate = source_sample_rate
        self.target_sample_rate = target_sample_rate
        self.channels = channels
        self.pcm_format = pcm_format
        self.chunk_ms = chunk_ms
        self.read_size_bytes = read_size_bytes
        self.stop_timeout = stop_timeout
        self._process_factory = process_factory
        self._executable_resolver = executable_resolver
        self._resampler = Pcm16MonoResampler(
            source_sample_rate, target_sample_rate
        )
        self._rechunker = Pcm16MonoRechunker(
            sample_rate=target_sample_rate, chunk_ms=chunk_ms
        )
        self._process: Any = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._started = False
        self.reader_finished = threading.Event()
        self.last_error: Exception | None = None

    @property
    def running(self) -> bool:
        thread = self._thread
        return self._started and thread is not None and thread.is_alive()

    def start(
        self,
        on_audio: AudioInputCallback,
        on_error: MicRuntimeErrorCallback | None = None,
    ) -> None:
        with self._lifecycle_lock:
            if self._started:
                return
            resolved = self._executable_resolver(self.executable)
            if not resolved:
                raise AdapterNotConfiguredError(
                    f"mic_executable '{self.executable}' is not available"
                )
            self._resampler.reset()
            self._rechunker.reset()
            self._stop_event.clear()
            self.reader_finished.clear()
            self.last_error = None
            command = [
                resolved,
                "--device",
                self.device,
                "--format",
                self.pcm_format,
                "--channels",
                str(self.channels),
                "--rate",
                str(self.source_sample_rate),
                "--type",
                "raw",
            ]
            try:
                self._process = self._process_factory(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    bufsize=0,
                    shell=False,
                )
            except Exception as error:
                raise RuntimeError(
                    f"failed to start configured microphone executable: {error}"
                ) from error
            if self._process.stdout is None:
                self._process = None
                raise RuntimeError("configured microphone process has no stdout")
            self._started = True
            self._thread = threading.Thread(
                target=self._read_loop,
                args=(on_audio, on_error),
                name="arecord-pcm-reader",
                daemon=True,
            )
            self._thread.start()

    def _read_loop(
        self,
        on_audio: AudioInputCallback,
        on_error: MicRuntimeErrorCallback | None,
    ) -> None:
        byte_remainder = b""
        try:
            stdout = self._process.stdout
            while not self._stop_event.is_set():
                block = stdout.read(self.read_size_bytes)
                if not block:
                    break
                aligned = byte_remainder + bytes(block)
                if len(aligned) % 2:
                    byte_remainder = aligned[-1:]
                    aligned = aligned[:-1]
                else:
                    byte_remainder = b""
                if not aligned:
                    continue
                converted = self._resampler.process(aligned)
                for chunk in self._rechunker.feed(converted):
                    if self._stop_event.is_set():
                        break
                    on_audio(chunk)
            if byte_remainder and not self._stop_event.is_set():
                raise ValueError(
                    "configured microphone returned non-frame-aligned PCM"
                )
            return_code = self._process.poll()
            if not self._stop_event.is_set():
                if return_code is None:
                    raise RuntimeError(
                        "configured microphone stream ended unexpectedly"
                    )
                raise RuntimeError(
                    "configured microphone process exited unexpectedly "
                    f"with status {return_code}"
                )
        except Exception as error:
            if not self._stop_event.is_set():
                self.last_error = error
                LOG.warning(
                    "configured microphone reader stopped after %s",
                    type(error).__name__,
                )
                if on_error is not None:
                    try:
                        on_error(error)
                    except Exception:
                        LOG.warning("microphone runtime error callback failed")
        finally:
            self.reader_finished.set()

    def stop(self) -> None:
        with self._lifecycle_lock:
            if not self._started and self._process is None:
                self._reset_pipeline()
                return
            self._started = False
            self._stop_event.set()
            process = self._process
            thread = self._thread
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=self.stop_timeout)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=self.stop_timeout)
            stdout = getattr(process, "stdout", None)
            if stdout is not None:
                try:
                    stdout.close()
                except Exception:
                    pass
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=self.stop_timeout)
            self._process = None
            self._thread = None
            self._reset_pipeline()

    def _reset_pipeline(self) -> None:
        self._resampler.reset()
        self._rechunker.reset()


@dataclass(frozen=True, slots=True)
class RobotAudioPacket:
    pcm: bytes
    sample_rate: int
    channels: int
    generation_id: int
    playback_epoch: int


@dataclass(frozen=True, slots=True)
class RobotFlushEvent:
    generation_id: int
    playback_epoch: int


RobotAudioEvent = RobotAudioPacket | RobotFlushEvent


class QueuedRobotAudioOutputAdapter:
    """Thread-safe bounded queue for publishing audio only on the ROS thread."""

    def __init__(self, *, channels: int = 1, max_audio_packets: int = 100) -> None:
        if channels != 1:
            raise ValueError("speaker_channels must be 1 without conversion evidence")
        if max_audio_packets <= 0:
            raise ValueError("max_audio_packets must be greater than zero")
        self.channels = channels
        self.max_audio_packets = max_audio_packets
        self._events: deque[RobotAudioEvent] = deque()
        self._lock = threading.Lock()
        self._generation_id = 0
        self._playback_epoch = 0
        self.dropped_audio_packets = 0

    @property
    def playback_epoch(self) -> int:
        with self._lock:
            return self._playback_epoch

    def set_generation(self, generation_id: int) -> None:
        with self._lock:
            self._generation_id = generation_id
            self._events = deque(
                event
                for event in self._events
                if not isinstance(event, RobotAudioPacket)
            )

    def write(self, pcm: bytes, sample_rate: int, generation_id: int) -> bool:
        payload = bytes(pcm)
        validate_pcm16_mono(
            payload, sample_rate=sample_rate, channels=self.channels
        )
        if not payload:
            return False
        with self._lock:
            if generation_id != self._generation_id:
                return False
            audio_count = sum(
                isinstance(event, RobotAudioPacket) for event in self._events
            )
            if audio_count >= self.max_audio_packets:
                for event in self._events:
                    if isinstance(event, RobotAudioPacket):
                        self._events.remove(event)
                        self.dropped_audio_packets += 1
                        break
            self._events.append(
                RobotAudioPacket(
                    pcm=payload,
                    sample_rate=sample_rate,
                    channels=self.channels,
                    generation_id=generation_id,
                    playback_epoch=self._playback_epoch,
                )
            )
            return True

    def flush(self) -> int:
        with self._lock:
            self._playback_epoch += 1
            self._events.clear()
            self._events.append(
                RobotFlushEvent(
                    generation_id=self._generation_id,
                    playback_epoch=self._playback_epoch,
                )
            )
            return self._playback_epoch

    def drain_events(self) -> list[RobotAudioEvent]:
        with self._lock:
            events = list(self._events)
            self._events.clear()
            return events

    def accepts_packet(self, packet: RobotAudioPacket) -> bool:
        with self._lock:
            return (
                packet.generation_id == self._generation_id
                and packet.playback_epoch == self._playback_epoch
            )

    def publish_if_current(
        self,
        packet: RobotAudioPacket,
        publish: Callable[[RobotAudioPacket], None],
    ) -> bool:
        """Atomically guard a ROS publish against a concurrent local flush."""
        with self._lock:
            if (
                packet.generation_id != self._generation_id
                or packet.playback_epoch != self._playback_epoch
            ):
                return False
            publish(packet)
            return True


def build_pcm_audio_frame(
    packet: RobotAudioPacket,
    *,
    speaker_pcm_format: str,
    stamp: Any,
    message_type: Callable[[], Any],
) -> Any:
    """Map a pure packet onto the Phase 4-verified vendor message fields."""
    if not speaker_pcm_format.strip():
        raise AdapterNotConfiguredError(
            "PcmAudioFrame.format is not configured; vendor runtime value is unknown"
        )
    validate_pcm16_mono(
        packet.pcm, sample_rate=packet.sample_rate, channels=packet.channels
    )
    message = message_type()
    message.stamp = stamp
    message.sample_rate = packet.sample_rate
    message.channels = packet.channels
    message.format = speaker_pcm_format
    message.data = list(packet.pcm)
    return message


def publish_pcm_audio_packet(
    packet: RobotAudioPacket,
    *,
    speaker_pcm_format: str,
    clock: Any,
    publisher: Any,
    message_type: Callable[[], Any],
) -> Any:
    """Publish through duck-typed ROS objects while remaining mock-testable."""
    message = build_pcm_audio_frame(
        packet,
        speaker_pcm_format=speaker_pcm_format,
        stamp=clock.now().to_msg(),
        message_type=message_type,
    )
    publisher.publish(message)
    return message
