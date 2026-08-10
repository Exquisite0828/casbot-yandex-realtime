# Yandex Realtime Local Voice PoC

## Result

```text
Test date: 2026-08-10 (Asia/Shanghai)
Phase 2: COMPLETE
Gate 2: PASS
Phase 3: NOT STARTED
```

Gate 2 uses the user-adjusted acceptance scope from 2026-08-10: a separate
live proof of generation-time `response.cancel` is not required. One real
playback-time interruption was nevertheless observed and succeeded through
local flush plus `conversation.item.truncate`.

## Local environment

- OS: macOS 26.5.2 (25F84), Apple Silicon.
- Python: 3.14.5 locally; the PoC source uses Python 3.10-compatible syntax and
  APIs for the project target.
- Dependencies: `aiohttp 3.14.3`, `sounddevice 0.5.5` and their transitive
  packages in the ignored `.venv`.
- Input devices observed: built-in MacBook Air microphone and an additional
  microphone named `“exquisite”的麦克风`.
- Output device observed: built-in MacBook Air speakers.
- Live runs used the built-in microphone and speakers with raw PCM16 mono at
  24 kHz in 20 ms chunks. No audio was saved.

## Model and connection

- First model: `speech-realtime-260528`.
- Fallback attempted: no.
- Successful model: `speech-realtime-260528`.
- Endpoint: current `wss://ai.api.cloud.yandex.net/v1/realtime` endpoint from
  the Phase 1 verified facts.
- Authentication: API key read only from `YANDEX_API_KEY`; its value was never
  printed or written.

## Live acceptance

```text
WebSocket connected: YES
Session created: YES
Microphone streaming: YES
Russian understood: YES
Realtime audio returned: YES
Speaker playback pipeline: YES
3-turn conversation: YES
Barge-in: YES (local stop + truncate; no longer required by user)
```

Observed Russian input included `привет`, incremental forms of `какая сегодня
погода`, and other short live utterances. The server returned Russian user
transcripts, context-aware Russian responses such as recognizing a repeated
greeting, streamed answer text, and playable PCM audio within the same session.

The three-turn run observed three user transcripts and three assistant
responses. Two responses reached `response.done`; the third had already
returned text and audio when the bounded run ended. During one interruption,
old local playback stopped at approximately 2.25 seconds,
`conversation.item.truncate` was sent, and a new user turn and answer followed
without a server error.

## Actual event evidence

The successful runs observed:

```text
session.created
session.updated
input_audio_buffer.speech_started
input_audio_buffer.speech_stopped
conversation.item.input_audio_transcription.completed
conversation.item.created
response.created
response.output_item.added
response.content_part.added
response.output_text.delta
response.output_audio.delta
response.output_audio.done
response.output_text.done
response.output_item.done
response.done (completed)
conversation.item.truncated
```

This resolves the two most important Phase 1 runtime conditions for 260528:
the model establishes a session through the current endpoint, and it actually
returns `response.output_audio.delta` despite the documented Reference conflict.
The raw PCM16 mono 24 kHz input/output interpretation also interoperated with
the service.

Observed speech-stop to first-audio values varied widely across short runs:
approximately 1.8 s, 8.0 s, 6.3 s, 3.9 s, 21.5 s, and 12.9 s. These are
observations only, not benchmark results or a Gate threshold. Some runs used
fragmentary speech and were stopped on a fixed timer.

## Runtime differences and fixes

1. The 260528 session emitted `session.updated` twice in several runs. The PoC
   treats the event idempotently. No error followed.
2. Using `aiohttp`'s optional `heartbeat=20` caused an idle client-side close:
   `No PONG received after 10.0 seconds`, close code 1006. This was not a
   Yandex protocol error event. The optional heartbeat was removed; a later
   75-second session crossed that earlier failure point and ended only at the
   requested timer.
3. Local audio streams are closed before the WebSocket close handshake. The
   measured microphone close was about 0.11 s after this change.
4. A generation-time `response.cancel` decision is implemented and unit-tested,
   but no live PASS is claimed for it. The user explicitly removed this check
   from the current acceptance scope. Playback-time flush/truncate was observed
   live.

## Tests

```text
python -m py_compile tools/local_poc/realtime_voice_poc.py: PASS
python -m unittest test_realtime_voice_poc.py -v: 10 PASS
```

The unit tests cover current endpoint/model URI construction, the 2026 session
audio schema, Russian language selection, raw PCM Base64 framing, interruption
event construction, stale response rejection, and credential redaction.

## Gate 2 decision

```text
PASS
```

The local Route A chain worked with the preferred 260528 model: real current
WebSocket session, continuous microphone input, Russian transcription and
multi-turn generation, realtime answer audio, local speaker output, and no
architecture-level blocker for a later ROS2 adapter. The explicit user decision
removed generation-time cancel from this Gate. Phase 3 has not started.
