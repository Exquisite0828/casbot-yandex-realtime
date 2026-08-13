import struct
import unittest

from realtime_dialog.audio_pipeline import (
    Pcm16MonoRechunker,
    Pcm16MonoResampler,
    validate_pcm16_mono,
)


def pcm16(*samples: int) -> bytes:
    return struct.pack(f"<{len(samples)}h", *samples)


def samples(pcm: bytes) -> tuple[int, ...]:
    return tuple(value[0] for value in struct.iter_unpack("<h", pcm))


class AudioPipelineTest(unittest.TestCase):
    def test_pcm_validation_rejects_odd_length_and_invalid_parameters(self) -> None:
        with self.assertRaisesRegex(ValueError, "frame-aligned"):
            validate_pcm16_mono(b"\x00", sample_rate=16_000)
        with self.assertRaisesRegex(ValueError, "sample_rate"):
            validate_pcm16_mono(b"", sample_rate=0)
        with self.assertRaisesRegex(ValueError, "channels"):
            validate_pcm16_mono(b"", sample_rate=16_000, channels=2)
        with self.assertRaisesRegex(ValueError, "sample_width"):
            validate_pcm16_mono(
                b"", sample_rate=16_000, sample_width_bytes=1
            )
        with self.assertRaisesRegex(ValueError, "source_sample_rate"):
            Pcm16MonoResampler(0, 24_000)
        with self.assertRaisesRegex(ValueError, "target_sample_rate"):
            Pcm16MonoResampler(16_000, 0)
        with self.assertRaisesRegex(ValueError, "chunk_ms"):
            Pcm16MonoRechunker(sample_rate=24_000, chunk_ms=0)

    def test_resampler_16k_to_24k_has_expected_final_frame_count(self) -> None:
        source = pcm16(*(index - 160 for index in range(320)))
        resampler = Pcm16MonoResampler(16_000, 24_000)
        output = resampler.process(source) + resampler.finish()
        self.assertEqual(len(output) // 2, 480)
        self.assertEqual(len(output) % 2, 0)

    def test_chunked_and_contiguous_resampling_are_identical(self) -> None:
        source = pcm16(*(index * 31 % 20_000 - 10_000 for index in range(997)))
        whole = Pcm16MonoResampler(16_000, 24_000)
        expected = whole.process(source) + whole.finish()

        chunked = Pcm16MonoResampler(16_000, 24_000)
        parts = [source[:202], source[202:646], source[646:1210], source[1210:]]
        actual = b"".join(chunked.process(part) for part in parts)
        actual += chunked.finish()
        self.assertEqual(actual, expected)

    def test_chunk_boundary_preserves_linear_ramp_without_repeat_or_gap(self) -> None:
        source_values = tuple(range(-200, 200))
        source = pcm16(*source_values)
        resampler = Pcm16MonoResampler(16_000, 24_000)
        output = (
            resampler.process(source[:300])
            + resampler.process(source[300:])
            + resampler.finish()
        )
        output_values = samples(output)
        self.assertEqual(len(output_values), 600)
        self.assertTrue(
            all(left <= right for left, right in zip(output_values, output_values[1:]))
        )
        self.assertLessEqual(
            max(right - left for left, right in zip(output_values, output_values[1:])),
            1,
        )

    def test_int16_extremes_remain_clipped_and_signed_little_endian(self) -> None:
        resampler = Pcm16MonoResampler(16_000, 24_000)
        output = resampler.process(pcm16(-32_768, 32_767)) + resampler.finish()
        values = samples(output)
        self.assertIn(-32_768, values)
        self.assertIn(32_767, values)
        self.assertTrue(all(-32_768 <= value <= 32_767 for value in values))

    def test_empty_input_is_empty_and_finish_is_idempotent(self) -> None:
        resampler = Pcm16MonoResampler(16_000, 24_000)
        self.assertEqual(resampler.process(b""), b"")
        self.assertEqual(resampler.finish(), b"")
        self.assertEqual(resampler.finish(), b"")

    def test_rechunker_emits_only_20ms_24k_chunks(self) -> None:
        rechunker = Pcm16MonoRechunker(sample_rate=24_000, chunk_ms=20)
        self.assertEqual(rechunker.chunk_bytes, 960)
        self.assertEqual(rechunker.feed(b"\x00\x00" * 100), [])
        chunks = rechunker.feed(b"\x01\x00" * 900)
        self.assertEqual([len(chunk) for chunk in chunks], [960, 960])
        self.assertEqual(rechunker.remainder_bytes, 80)

    def test_rechunker_reset_drops_previous_generation_remainder(self) -> None:
        rechunker = Pcm16MonoRechunker(sample_rate=24_000, chunk_ms=20)
        old = b"\x11\x11" * 200
        rechunker.feed(old)
        rechunker.reset()
        new = b"\x22\x22" * 480
        self.assertEqual(rechunker.feed(new), [new])


if __name__ == "__main__":
    unittest.main()
