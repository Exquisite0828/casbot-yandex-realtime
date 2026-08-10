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

## Adapter boundary and Phase 4 unknowns

`PendingRobotMicAdapter` deliberately supplies no audio. Phase 4 must determine:

- whether the microphone source is a ROS2 topic or ALSA;
- real sample rate, bit depth, channel count, and frame size.

`PendingRobotAudioOutputAdapter` can publish flush through the wrapper but
rejects PCM writes until the real message schema is known. Phase 4 must inspect:

- the exact `lingze_msgs/msg/PcmAudioFrame` fields;
- `/audio/dialog_play` and `/dialog/status` QoS;
- actual node name/namespace and topic remapping behavior;
- original status ordering and stop/flush behavior.

These are explicit integration unknowns, not Phase 3 implementation facts.

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
Phase 4: NOT STARTED
```

The next step requires a separate user authorization for Phase 4 read-only
robot inspection. No SSH, deployment, robot change, commit, or push was
performed in Phase 3.
