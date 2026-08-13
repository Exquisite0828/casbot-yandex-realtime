"""Dependency-free PCM16 mono processing for the robot microphone path."""

from __future__ import annotations

import struct


PCM16_WIDTH_BYTES = 2


def validate_pcm16_mono(
    pcm: bytes,
    *,
    sample_rate: int,
    channels: int = 1,
    sample_width_bytes: int = PCM16_WIDTH_BYTES,
) -> None:
    """Validate the PCM contract used by the Phase 5 audio adapters."""
    if sample_rate <= 0:
        raise ValueError("sample_rate must be greater than zero")
    if channels != 1:
        raise ValueError("channels must be 1 for the PCM16 mono pipeline")
    if sample_width_bytes != PCM16_WIDTH_BYTES:
        raise ValueError("sample_width_bytes must be 2 for signed PCM16")
    frame_bytes = channels * sample_width_bytes
    if len(pcm) % frame_bytes:
        raise ValueError("PCM byte length must be int16 frame-aligned")


def _clip_int16(value: int) -> int:
    return max(-32_768, min(32_767, value))


def _divide_round_nearest(numerator: int, denominator: int) -> int:
    if numerator >= 0:
        return (numerator + denominator // 2) // denominator
    return -((-numerator + denominator // 2) // denominator)


class Pcm16MonoResampler:
    """Stateful deterministic linear PCM16 resampler.

    Output positions are tracked with integer rational arithmetic, so chunking
    the same byte stream differently cannot change phase or interpolation.
    ``finish()`` holds the final input sample only for the sub-sample tail that
    represents the finite input duration; a live stream normally calls
    ``reset()`` on stop instead.
    """

    def __init__(self, source_sample_rate: int, target_sample_rate: int) -> None:
        if source_sample_rate <= 0:
            raise ValueError("source_sample_rate must be greater than zero")
        if target_sample_rate <= 0:
            raise ValueError("target_sample_rate must be greater than zero")
        self.source_sample_rate = source_sample_rate
        self.target_sample_rate = target_sample_rate
        self.reset()

    def reset(self) -> None:
        self._samples: list[int] = []
        self._buffer_start = 0
        self._total_input_frames = 0
        self._next_output_index = 0
        self._finished = False

    def process(self, pcm: bytes) -> bytes:
        validate_pcm16_mono(pcm, sample_rate=self.source_sample_rate)
        if self._finished:
            raise RuntimeError("resampler must be reset before processing after finish")
        if not pcm:
            return b""
        self._samples.extend(value[0] for value in struct.iter_unpack("<h", pcm))
        self._total_input_frames += len(pcm) // PCM16_WIDTH_BYTES
        return self._drain(allow_tail=False)

    def finish(self) -> bytes:
        if self._finished:
            return b""
        self._finished = True
        return self._drain(allow_tail=True)

    def _drain(self, *, allow_tail: bool) -> bytes:
        if not self._samples:
            return b""
        output: list[int] = []
        last_index = self._total_input_frames - 1
        target_count = (
            self._total_input_frames * self.target_sample_rate
            + self.source_sample_rate // 2
        ) // self.source_sample_rate

        while True:
            if allow_tail and self._next_output_index >= target_count:
                break
            position_numerator = (
                self._next_output_index * self.source_sample_rate
            )
            left_index, fraction = divmod(
                position_numerator, self.target_sample_rate
            )
            if left_index > last_index:
                break
            needs_right = fraction != 0
            if needs_right and left_index + 1 > last_index and not allow_tail:
                break

            left = self._sample_at(left_index)
            if needs_right and left_index + 1 <= last_index:
                right = self._sample_at(left_index + 1)
            else:
                right = left
            numerator = (
                left * (self.target_sample_rate - fraction) + right * fraction
            )
            output.append(
                _clip_int16(
                    _divide_round_nearest(numerator, self.target_sample_rate)
                )
            )
            self._next_output_index += 1

        self._discard_consumed_prefix()
        if not output:
            return b""
        return struct.pack(f"<{len(output)}h", *output)

    def _sample_at(self, absolute_index: int) -> int:
        return self._samples[absolute_index - self._buffer_start]

    def _discard_consumed_prefix(self) -> None:
        next_position = self._next_output_index * self.source_sample_rate
        next_left_index = next_position // self.target_sample_rate
        discard = max(0, min(len(self._samples), next_left_index - self._buffer_start))
        if discard:
            del self._samples[:discard]
            self._buffer_start += discard


class Pcm16MonoRechunker:
    """Collect arbitrary aligned PCM blocks into fixed-duration chunks."""

    def __init__(self, *, sample_rate: int, chunk_ms: int = 20) -> None:
        if sample_rate <= 0:
            raise ValueError("sample_rate must be greater than zero")
        if chunk_ms <= 0:
            raise ValueError("chunk_ms must be greater than zero")
        frame_numerator = sample_rate * chunk_ms
        if frame_numerator % 1000:
            raise ValueError("sample_rate * chunk_ms must produce whole frames")
        self.sample_rate = sample_rate
        self.chunk_ms = chunk_ms
        self.chunk_frames = frame_numerator // 1000
        self.chunk_bytes = self.chunk_frames * PCM16_WIDTH_BYTES
        self._remainder = bytearray()

    @property
    def remainder_bytes(self) -> int:
        return len(self._remainder)

    def feed(self, pcm: bytes) -> list[bytes]:
        validate_pcm16_mono(pcm, sample_rate=self.sample_rate)
        if pcm:
            self._remainder.extend(pcm)
        chunks: list[bytes] = []
        while len(self._remainder) >= self.chunk_bytes:
            chunks.append(bytes(self._remainder[: self.chunk_bytes]))
            del self._remainder[: self.chunk_bytes]
        return chunks

    def reset(self) -> None:
        self._remainder.clear()
