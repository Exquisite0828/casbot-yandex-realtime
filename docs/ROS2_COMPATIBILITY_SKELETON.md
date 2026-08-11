# Phase 3 — ROS2 Compatibility Skeleton

> Date: 2026-08-10
> Status: Phase 3 COMPLETE
> Gate 3: CONDITIONAL PASS
> ROS2 runtime launch: NOT RUN (local environment unavailable)

## Package

```text
package:    realtime_dialog
executable: realtime_dialog_node
build:      ament_python
target:     Linux / ROS2 Humble / Python 3.10
```

The package lives in `src/realtime_dialog`. The implementation has three
layers:

```text
ROS2 wrapper callbacks
        ↓ submit only
background asyncio worker → DialogController → YandexRealtimeClient
                                ↓
                       MicAdapter / AudioOutputAdapter
        ↑
thread-safe outbound queue → ROS2 publishers
```

`YandexRealtimeClient` is the reusable, audio-device-independent transport. It
contains the Phase 2 verified current endpoint, `speech-realtime-260528` model
URI handling, 2026 `session.update`, audio/text input, response cancel/truncate,
wire-event normalization, response-to-generation binding, and credential
redaction. The Phase 2 local PoC imports these shared protocol helpers rather
than keeping a second copy.

## ROS2 contract

| Direction | Name | Type | Phase 3 status |
|---|---|---|---|
| Service | `/dialog/start_session` | `std_srvs/srv/Trigger` | Wrapper implemented; schedules background start and returns immediately |
| Service | `/dialog/stop_session` | `std_srvs/srv/Trigger` | Wrapper implemented; schedules background stop and returns immediately |
| Subscribe | `/dialog/text_input` | `std_msgs/msg/String` | Wrapper implemented; auto-ensures a session in the controller |
| Publish | `/dialog/status` | `std_msgs/msg/String` | Implemented; Reliable + Transient Local, marked vendor-documented provisional |
| Publish | `/dialog/text_result` | `std_msgs/msg/String` | Implemented for assistant text deltas |
| Publish | `/audio/dialog_flush` | `std_msgs/msg/Bool` | Implemented through `AudioOutputAdapter.flush()` with `data=true` |
| Future publish | `/audio/dialog_play` | `lingze_msgs/msg/PcmAudioFrame` | Adapter target recorded; publisher intentionally deferred to Phase 5 |

No `lingze_msgs` import and no fabricated `PcmAudioFrame` definition exists in
Phase 3.

## State and lifecycle

The public states are:

```text
STATUS_IDLE
STATUS_CONNECTING
STATUS_LISTENING
STATUS_SPEAKING_TEXT
STATUS_ERROR
```

Main transitions:

```text
start: IDLE → CONNECTING → session.updated → LISTENING
answer: LISTENING → response.created → SPEAKING_TEXT → response.done → LISTENING
stop: invalidate generation → cancel → flush → close → IDLE
error: active current-generation transport/session error → ERROR
```

`/dialog/text_input` trims and rejects empty text. If no session is active it
starts one without inventing a robot microphone source, then sends the text. A
duplicate start does not open another WebSocket.

Every lifecycle/replacement uses a monotonically increasing `generation_id`.
Response IDs are bound to the generation in which `response.created` arrived.
Text, audio, completion, and error events from an old generation are dropped.
Stop also disables event acceptance before closing, so a late session-level
error cannot move an idle controller back to `STATUS_ERROR`.

## Non-blocking ROS boundary

ROS service and subscription callbacks only create and submit controller
coroutines to `AsyncioWorker`; they never wait for WebSocket connection, setup,
send, cancel, or close. Controller status/text/flush events are placed in a
thread-safe queue and published by a short ROS timer callback on the executor
side.

The real API key is not a ROS parameter. The node obtains
`YANDEX_API_KEY` through `RuntimeConfig.from_environment`; it is excluded from
configuration representations and redacted from remote error text. Endpoint,
model/model URI, folder ID, Yandex-side sample rate, voice, VAD threshold,
silence duration, instructions, and connect/setup timeouts are non-secret ROS
parameters.

## Phase 4 runtime evidence and Adapter handoff

Phase 4 read-only runtime evidence, frozen on 2026-08-11, resolved the hardware
and ROS boundary facts that Phase 3 intentionally left open.

**VERIFIED:**

```text
actual dialog node: /lzdl10823/dialog_node
actual namespace: /lzdl10823
runtime executable:
  /lingze/install/lingze_omni_s2s/lib/lingze_omni_s2s/dialog_node

microphone source: direct ALSA capture, not an observed ROS2 input subscription
capture device: /dev/snd/pcmC0D0c (Yundea 1076 USB Audio)
capture PCM: MMAP_INTERLEAVED, S16_LE, mono, 16000 Hz
period_size: 1024 frames
buffer_size: 16384 frames
```

The installed `lingze_msgs/msg/PcmAudioFrame` schema is:

```text
builtin_interfaces/Time stamp
uint32 sample_rate
uint8 channels
string format
uint8[] data
```

Observed QoS:

| Topic | Reliability | Durability | History depth |
|---|---|---|---|
| `/lzdl10823/audio/dialog_play` | RELIABLE | VOLATILE | UNKNOWN |
| `/lzdl10823/audio/dialog_flush` | RELIABLE | VOLATILE | UNKNOWN |
| `/lzdl10823/dialog/status` | RELIABLE | TRANSIENT_LOCAL | UNKNOWN |
| `/lzdl10823/dialog/text_result` | RELIABLE | VOLATILE | UNKNOWN |

`/lzdl10823/dialog/session_active` is also published with RELIABLE +
TRANSIENT_LOCAL QoS and has a live `face_play_example` subscriber. It was not in
the early public compatibility list, so Phase 5 must evaluate and likely retain
it. `/lzdl10823/dialog/input_waveform` is a BEST_EFFORT + VOLATILE output of
`dialog_node`, not a microphone input.

The Phase 3 code remains a skeleton: `PendingRobotMicAdapter` still supplies no
audio, and `PendingRobotAudioOutputAdapter` still has no
`PcmAudioFrame` publisher. Phase 5 must implement those adapters and namespace
alignment using the verified facts above.

Still unresolved without guesswork:

- **DEFERRED:** actual string expected in `PcmAudioFrame.format`;
- **NOT OBSERVED / DEFERRED:** whether `audio_speaker_node` resamples input or
  converts mono to stereo, and its accepted frame combinations;
- **DEFERRED:** use Yandex at 16 kHz or resample 16 kHz → 24 kHz;
- **NOT COLLECTED:** exact original status sequence and black-box experience
  baseline.

See `docs/RUNTIME_SNAPSHOT.md` for the complete evidence and caveats.

## Local verification

The local host is macOS with Python 3.14 and has neither `ros2` nor `rclpy`.
Installing ROS2 Humble was intentionally skipped. Verification used the
ROS-independent core, fakes, source/package metadata checks, and Python compile
checks:

```text
Phase 2 protocol helper tests: 10 passed
Phase 3 core/mock tests:       21 passed
Combined:                      31 passed
```

Coverage includes exact contract names and types, non-blocking command
submission, current Yandex schemas, error redaction, state transitions,
text-session auto-start, adapter routing, cancel/flush/close ordering,
interruption, and stale text/audio/done/error rejection.

Because an actual ROS2 Humble runtime was unavailable, the package was not
built with `colcon` and the node was not launched. This is the sole condition on
Gate 3.

## Gate 3

```text
Gate 3: CONDITIONAL PASS
Phase 3: COMPLETE
Phase 4: COMPLETE
Gate 4: PASS
Phase 5: NOT STARTED
```

Gate 3 remains the historical Phase 3 result: the local Mac could not launch a
ROS2 Humble wrapper. Phase 4 later froze the robot interface facts without
changing that historical conclusion. No Phase 5 adapter implementation or
deployment has started.
