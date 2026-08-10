import base64
import unittest

from realtime_voice_poc import (
    ResponseState,
    build_audio_append,
    build_barge_in_events,
    build_session_update,
    build_websocket_url,
    model_label,
    plan_barge_in_events,
    redact_text,
    resolve_model_uri,
)


class ProtocolHelpersTest(unittest.TestCase):
    def test_resolve_model_uri_builds_primary_model_uri(self) -> None:
        self.assertEqual(
            resolve_model_uri("speech-realtime-260528", "folder-123"),
            "gpt://folder-123/speech-realtime-260528",
        )

    def test_resolve_model_uri_keeps_complete_uri(self) -> None:
        uri = "gpt://folder-123/speech-realtime-260528"
        self.assertEqual(resolve_model_uri(uri, None), uri)

    def test_build_websocket_url_uses_current_endpoint_and_model_query(self) -> None:
        url = build_websocket_url(
            "wss://ai.api.cloud.yandex.net/v1/realtime?trace=1",
            "gpt://folder-123/speech-realtime-260528",
        )
        self.assertEqual(
            url,
            "wss://ai.api.cloud.yandex.net/v1/realtime?trace=1&model="
            "gpt%3A%2F%2Ffolder-123%2Fspeech-realtime-260528",
        )

    def test_build_websocket_url_rejects_legacy_endpoint(self) -> None:
        with self.assertRaisesRegex(ValueError, "legacy"):
            build_websocket_url(
                "wss://rest-assistant.api.cloud.yandex.net/v1/realtime/",
                "gpt://folder-123/speech-realtime-260528",
            )

    def test_session_update_uses_2026_audio_schema_and_russian(self) -> None:
        message = build_session_update(
            sample_rate=24_000,
            voice="dasha",
            vad_threshold=0.5,
            silence_ms=500,
        )
        session = message["session"]
        self.assertEqual(message["type"], "session.update")
        self.assertEqual(session["output_modalities"], ["audio"])
        self.assertEqual(
            session["audio"]["input"]["format"],
            {"type": "audio/pcm", "rate": 24_000},
        )
        self.assertEqual(session["audio"]["input"]["languages"], ["ru-RU"])
        self.assertEqual(
            session["audio"]["input"]["turn_detection"],
            {
                "type": "server_vad",
                "threshold": 0.5,
                "silence_duration_ms": 500,
            },
        )
        self.assertEqual(
            session["audio"]["output"],
            {
                "format": {"type": "audio/pcm", "rate": 24_000},
                "voice": "dasha",
            },
        )
        self.assertIn("русском", session["instructions"])

    def test_audio_append_base64_encodes_raw_pcm(self) -> None:
        pcm = b"\x00\x01\xff\x7f"
        message = build_audio_append(pcm)
        self.assertEqual(message["type"], "input_audio_buffer.append")
        self.assertEqual(base64.b64decode(message["audio"]), pcm)

    def test_barge_in_sends_cancel_then_truncate(self) -> None:
        messages = build_barge_in_events(
            response_id="resp-1",
            item_id="item-1",
            content_index=0,
            played_ms=321,
        )
        self.assertEqual(
            messages,
            [
                {"type": "response.cancel", "response_id": "resp-1"},
                {
                    "type": "conversation.item.truncate",
                    "item_id": "item-1",
                    "content_index": 0,
                    "audio_end_ms": 321,
                },
            ],
        )

    def test_generation_is_cancelled_before_any_audio_is_playing(self) -> None:
        messages = plan_barge_in_events(
            response_snapshot=type(
                "Snapshot",
                (),
                {"response_id": "resp-1", "item_id": None, "content_index": 0},
            )(),
            playback_snapshot=type(
                "Playback", (), {"was_playing": False, "played_ms": 0}
            )(),
        )
        self.assertEqual(
            messages,
            [{"type": "response.cancel", "response_id": "resp-1"}],
        )

    def test_response_state_rejects_late_audio_after_barge_in(self) -> None:
        state = ResponseState()
        state.start("resp-1")
        self.assertTrue(state.accept_audio("resp-1", "item-1", 0))
        snapshot = state.interrupt()
        self.assertEqual(snapshot.response_id, "resp-1")
        self.assertEqual(snapshot.item_id, "item-1")
        self.assertFalse(state.accept_audio("resp-1", "item-1", 0))
        state.start("resp-2")
        self.assertTrue(state.accept_audio("resp-2", "item-2", 0))

    def test_labels_and_errors_do_not_expose_credentials(self) -> None:
        key = "secret-api-key"
        uri = "gpt://folder-123/speech-realtime-260528"
        self.assertEqual(model_label(uri), "speech-realtime-260528")
        text = redact_text(
            f"Authorization: Api-Key {key}; api_key={key}; {key}",
            secrets=(key,),
        )
        self.assertNotIn(key, text)
        self.assertNotIn("folder-123", model_label(uri))


if __name__ == "__main__":
    unittest.main()
