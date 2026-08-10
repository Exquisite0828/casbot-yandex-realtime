"""Robot-specific audio boundaries kept deliberately abstract in Phase 3."""

from __future__ import annotations

from typing import Callable, Protocol


AudioInputCallback = Callable[[bytes], None]


class MicAdapter(Protocol):
    def start(self, on_audio: AudioInputCallback) -> None:
        """Start supplying raw PCM bytes to ``on_audio``."""

    def stop(self) -> None:
        """Stop the configured microphone source."""


class AudioOutputAdapter(Protocol):
    def write(self, pcm: bytes, sample_rate: int, generation_id: int) -> None:
        """Accept Yandex PCM for the current controller generation."""

    def flush(self) -> None:
        """Discard queued dialog audio."""


class AdapterNotConfiguredError(RuntimeError):
    """Raised when a Phase 4/5 robot-specific mapping is still unavailable."""


class PendingRobotMicAdapter:
    """Phase 3 placeholder that intentionally provides no guessed audio source.

    Phase 4 must determine whether the robot microphone is a ROS2 topic or ALSA,
    plus its rate, bit depth, channel count, and frame size.
    """

    def __init__(self) -> None:
        self._on_audio: AudioInputCallback | None = None

    def start(self, on_audio: AudioInputCallback) -> None:
        self._on_audio = on_audio

    def stop(self) -> None:
        self._on_audio = None


class PendingRobotAudioOutputAdapter:
    """Flush bridge with no fabricated PcmAudioFrame mapping.

    Phase 4 must inspect ``lingze_msgs/msg/PcmAudioFrame``. Phase 5 can then
    implement ``write`` for ``/audio/dialog_play``.
    """

    def __init__(self, on_flush: Callable[[], None]) -> None:
        self._on_flush = on_flush

    def write(self, pcm: bytes, sample_rate: int, generation_id: int) -> None:
        del pcm, sample_rate, generation_id
        raise AdapterNotConfiguredError(
            "audio output mapping requires Phase 4 PcmAudioFrame inspection"
        )

    def flush(self) -> None:
        self._on_flush()
