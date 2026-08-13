# ROS2 Compatibility Skeleton and Phase 5 Adapter Handoff

> Updated: 2026-08-13
> Phase 3: COMPLETE; historical Gate 3: CONDITIONAL PASS
> Phase 4: COMPLETE; Gate 4: PASS
> Phase 5: COMPLETE; Gate 5: CONDITIONAL PASS
> Phase 6: NOT STARTED
> Robot deployment/runtime launch: NOT RUN

## Package and boundaries

```text
package:    realtime_dialog
executable: realtime_dialog_node
build:      ament_python
target:     Linux / ROS2 Humble / Python 3.10
```

The ROS wrapper remains thin. ROS callbacks submit commands to a background
asyncio worker and never wait for Yandex network I/O. Robot-specific audio is
kept behind adapters:

```text
arecord (configured device, 16 kHz PCM16 mono)
  → stateful linear 16 → 24 kHz resampler
  → stateful 20 ms / 960-byte rechunker
  → callback → bounded asyncio queue (default 50; drop oldest)
  → exactly one Yandex sender task
  → YandexRealtimeClient
  → 24 kHz PCM16 mono response audio
  → bounded QueuedRobotAudioOutputAdapter
  → ROS executor thread
  → lingze_msgs/msg/PcmAudioFrame
```

No third-party DSP package or `audioop` is used. The `arecord` subprocess uses
an argument list with `shell=False`, redirects stderr so it cannot block, reads
stdout on a dedicated thread, and has bounded terminate/kill shutdown. A
missing device or executable fails clearly. The example `hw:0,0` is an
integration candidate only; Phase 4 verified `/dev/snd/pcmC0D0c` as the device
held by the vendor process, not an equivalent `arecord --device` string.
Unexpected capture termination is reported from the reader thread through a
thread-safe Controller callback. Both dialog generation and a per-capture
lifecycle token reject late failures from superseded readers; an intentional
stop disables reporting before terminating arecord.

## Yandex transport and lifecycle

`RuntimeConfig` now separates `input_sample_rate` and
`yandex_output_sample_rate`; both default to 24 kHz. Phase 2 endpoint, model,
event schema, Russian language selection and credential redaction are
unchanged. The local PoC keeps its single `--sample-rate` CLI option and maps
that value explicitly to both session directions.

Microphone callbacks no longer create an unbounded task per chunk. A single
sender drains a bounded queue; full queues drop the oldest waiting chunk and
increment a counter. Generation changes clear pending chunks and cancel any
old in-flight sender before starting the new-generation sender.

Stop ordering is:

```text
disable events / advance generation
→ advance playback epoch and enqueue local flush
→ stop arecord, cancel sender, and clear microphone queue
→ best-effort cancel current Yandex response
→ mandatory close transport
→ STATUS_IDLE
```

If cancel or close fails, close is still attempted, the result reports the
cleanup error, and state becomes `STATUS_ERROR`. When ROS spin exits, shutdown
explicitly enqueues and drains one final flush before `destroy_node()`; the
drain only publishes already queued output and retains the normal
generation/playback-epoch guard for audio packets.

Text replacement and `speech_started` interruption likewise advance the
generation and enqueue local flush before awaiting remote cancel. Exact
`conversation.item.truncate` playback progress remains a later integration
item; Phase 5 does not invent speaker progress feedback.

## Relative ROS2 contract and CASBOT profile

All application names are relative so namespace launch settings are honored:

| Direction | Relative name | Type |
|---|---|---|
| Service | `dialog/start_session` | `std_srvs/srv/Trigger` |
| Service | `dialog/stop_session` | `std_srvs/srv/Trigger` |
| Subscribe | `dialog/text_input` | `std_msgs/msg/String` |
| Publish | `dialog/status` | `std_msgs/msg/String` |
| Publish | `dialog/text_result` | `std_msgs/msg/String` |
| Publish | `dialog/session_active` | `std_msgs/msg/Bool` |
| Publish | `audio/dialog_play` | `lingze_msgs/msg/PcmAudioFrame` |
| Publish | `audio/dialog_flush` | `std_msgs/msg/Bool` |

Generic defaults remain an empty namespace and node name
`realtime_dialog_node`. `launch/casbot_realtime_dialog.launch.py` supplies
overridable example values `namespace=lzdl10823` and `node_name=dialog_node`,
resolving to the Phase 4-observed surface such as
`/lzdl10823/audio/dialog_play` and `/lzdl10823/dialog/status`.

## QoS and `session_active`

Phase 4 verified Reliability and Durability; history depth was not collected.
The code therefore records depth only as an implementation buffer policy:

| Relative topic | Reliability | Durability | Project depth |
|---|---|---|---:|
| `audio/dialog_play` | RELIABLE | VOLATILE | 10 |
| `audio/dialog_flush` | RELIABLE | VOLATILE | 10 |
| `dialog/status` | RELIABLE | TRANSIENT_LOCAL | 1 |
| `dialog/text_result` | RELIABLE | VOLATILE | 10 |
| `dialog/session_active` | RELIABLE | TRANSIENT_LOCAL | 1 |

`dialog/session_active` uses this **PROJECT COMPATIBILITY SEMANTIC**, not a
claim about exact vendor timing:

```text
STATUS_IDLE          → false
STATUS_CONNECTING    → true
STATUS_LISTENING     → true
STATUS_SPEAKING_TEXT → true
STATUS_ERROR         → false
```

Initial status/session-active output is `STATUS_IDLE`/`false`; each status
transition queues its corresponding Bool value.

## `PcmAudioFrame` and output stale suppression

The ROS path imports the real `lingze_msgs.msg.PcmAudioFrame`; no local fake
message package exists. If it cannot be imported, startup explains that the
current environment lacks it and points integrators to the vendor overlay
normally sourced from `/lingze/install/setup.bash`.

The factory maps only Phase 4-verified fields:

```text
stamp       = node clock now
sample_rate = actual Yandex payload rate (default 24000)
channels    = actual payload channels (1)
format      = required speaker_pcm_format parameter
data        = raw PCM bytes represented as uint8 values
```

`speaker_pcm_format` has no guessed default. Empty configuration fails with:

```text
PcmAudioFrame.format is not configured; vendor runtime value is unknown
```

No 24→48 kHz resampling or mono→stereo conversion is performed. Physical
playback hardware observed at 48 kHz stereo is not treated as the ROS message
contract.

Every queued audio packet carries generation and playback epoch. `flush()`
atomically increments the epoch, removes old queued audio, and puts a flush
event before later audio. A second guarded check at ROS publish time rejects a
packet invalidated by a concurrent flush after drain.
Flush events deliberately survive generation changes as barriers: interruption
flush is published before any subsequently queued new-generation audio.

## Configuration and dependencies

`config/casbot.example.yaml` contains non-secret adapter/Yandex parameters.
`mic_device` and `speaker_pcm_format` must be confirmed by an integrator;
credentials remain process-environment-only through `YANDEX_API_KEY`.
`package.xml` declares `lingze_msgs`, and setup installs launch/config files.

## Local verification status

On 2026-08-13, 68 core/mock tests covered PCM validation, continuous
resampling, rechunk/reset, fake arecord lifecycle, bounded microphone sender,
generation and capture-error suppression, flush ordering/epoch suppression,
cancel/close cleanup, final shutdown drain, message mapping, relative names,
QoS, session-active semantics, metadata and Yandex rate split.
All 10 Phase 2 PoC regression tests also passed. Compileall passed.

The current macOS environment has no `ros2`, `colcon`, `rclpy` or
`lingze_msgs`, so a real ROS2 Humble build and wrapper launch were **NOT RUN —
environment unavailable**. This statement is not a ROS runtime PASS.

## Gate 5 conditional runtime items

Gate 5 is `CONDITIONAL PASS`. Its conditions are limited to these real
environment checks:

- ROS2 Humble + vendor overlay real build/launch: UNKNOWN / DEFERRED / CONDITIONAL.
- Real `lingze_msgs.msg.PcmAudioFrame` import: UNKNOWN / DEFERRED / CONDITIONAL.
- Real `PcmAudioFrame.format` runtime value: UNKNOWN / DEFERRED / CONDITIONAL; required config.
- Speaker accepted sample-rate/channel combinations: UNKNOWN / DEFERRED / CONDITIONAL.
- Speaker resample and mono-stereo conversion behavior: UNKNOWN / DEFERRED / CONDITIONAL.
- Actual robot `arecord` device string and executable: UNKNOWN / DEFERRED / CONDITIONAL.
- Real speaker, mouth and shutdown-flush behavior: UNKNOWN / DEFERRED / CONDITIONAL.
- Exact vendor `session_active` timing: UNKNOWN / DEFERRED / CONDITIONAL; exact timing was not collected.

These unknowns are isolated by configuration, adapters or fail-fast behavior;
none is guessed or hardcoded. `dialog/input_waveform`, `system/config_update`
and the final bringup/deployment switch remain separate deferred future scope,
not additional Gate 5 conditions. The robot has not been connected to Yandex,
and Phase 6 has not started.
