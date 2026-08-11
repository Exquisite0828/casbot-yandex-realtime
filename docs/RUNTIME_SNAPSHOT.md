# CASBOT Runtime Snapshot

## Status

```text
Collection date: 2026-08-11
Phase 4 — COMPLETE
Gate 4 — PASS
Phase 5 — NOT STARTED
```

This snapshot freezes the Phase 4 read-only runtime evidence. `VERIFIED`
means directly supported by the collected robot output or by the
user-confirmed collection scope. `NOT OBSERVED`, `NOT COLLECTED`, `DEFERRED`,
and `INFERENCE` are used explicitly; absence of evidence is not promoted into
a runtime fact.

## Collection scope and safety

**VERIFIED (user-confirmed collection scope):** Phase 4 evidence was collected
through read-only SSH before this closeout task. The collection did not restart,
stop, or kill services; write to or upload files to the robot; install packages;
modify systemd; copy closed-source code; or collect a real API key/token.

**VERIFIED:** This closeout task used only the four local evidence files. It did
not reconnect to the robot or execute any robot command.

## Evidence files

The byte-preserved raw files are local-only:

| File | SHA-256 |
|---|---|
| `runtime_snapshot/raw/phase4_round1.txt` | `af478f3f30ab030bba304586977e0e90fe2818c333c836baa7e634867d3429a2` |
| `runtime_snapshot/raw/phase4_round1_ros.txt` | `5de2df6ab01f2943ce53aa3de4363298e7abaff239af0238e5865b967b56dc1a` |
| `runtime_snapshot/raw/phase4_round2.txt` | `c0b05929bdd32eded64290d20264d9aa81722fc78bc77dcc27ca3202fd7b9e1d` |
| `runtime_snapshot/raw/phase4_round3.txt` | `e7d0722bf19a48e98b725940dc6547a74ffdf5ed79ebff8bd13b752fb039cfc9` |

**VERIFIED:** `runtime_snapshot/raw/` is ignored by Git. Raw SSH output is
intentionally excluded from version control and is not a project deliverable.

## Robot environment

**VERIFIED:**

```text
hostname: xiaoling0040
architecture: aarch64
kernel: Linux 4.14.87
Python: 3.10.12
ROS_DISTRO: humble
ros2 CLI: /opt/ros/humble/bin/ros2
```

**VERIFIED:** The first non-interactive shell did not load the ROS environment,
so `ROS_DISTRO` was empty and `ros2` was not on `PATH`. After sourcing
`/opt/ros/humble/setup.bash` and `/lingze/install/setup.bash`, the live ROS graph
was accessible. Sourcing those environment scripts did not change the robot.

## ROS2 runtime and actual dialog node

**VERIFIED:**

```text
actual node: /lzdl10823/dialog_node
actual namespace: /lzdl10823
runtime executable:
  /lingze/install/lingze_omni_s2s/lib/lingze_omni_s2s/dialog_node
observed dialog PID: 5789
```

The package/executable label `lingze_omni_s2s/dialog_node` is a runtime fact.
Treating that label as proof of any exact cloud model ID would be an
**INFERENCE**, not a verified fact.

## Actual ROS2 contract

The following application interfaces came from `ros2 node info
/lzdl10823/dialog_node`. Generic `/parameter_events`, `/rosout`, and parameter
services also existed but are not replacement-specific application contracts.

### Subscribers

**VERIFIED:**

```text
/lzdl10823/dialog/text_input       std_msgs/msg/String
/lzdl10823/system/config_update    std_msgs/msg/String
```

### Publishers

**VERIFIED:**

```text
/lzdl10823/audio/dialog_flush      std_msgs/msg/Bool
/lzdl10823/audio/dialog_play       lingze_msgs/msg/PcmAudioFrame
/lzdl10823/dialog/input_waveform   std_msgs/msg/Float32MultiArray
/lzdl10823/dialog/session_active   std_msgs/msg/Bool
/lzdl10823/dialog/status           std_msgs/msg/String
/lzdl10823/dialog/text_result      std_msgs/msg/String
```

### Services

**VERIFIED:**

```text
/lzdl10823/dialog/start_session    std_srvs/srv/Trigger
/lzdl10823/dialog/stop_session     std_srvs/srv/Trigger
```

**NOT COLLECTED:** Dialog parameter names. The ROS parameter service call
returned an exception during the audit; no parameter names or values are
inferred from that failure.

## QoS

All values below are live endpoint observations.

| Topic | Endpoints observed | Reliability | Durability | History depth | Classification |
|---|---|---|---|---|---|
| `/lzdl10823/audio/dialog_play` | `dialog_node` publisher → `audio_speaker_node` subscriber | RELIABLE | VOLATILE | UNKNOWN | VERIFIED; exact depth NOT COLLECTED |
| `/lzdl10823/audio/dialog_flush` | `dialog_node` publisher → `audio_speaker_node` subscriber | RELIABLE | VOLATILE | UNKNOWN | VERIFIED; exact depth NOT COLLECTED |
| `/lzdl10823/dialog/status` | `dialog_node` publisher → `face_play_example`, `rosbridge_websocket` subscribers | RELIABLE | TRANSIENT_LOCAL | UNKNOWN | VERIFIED; exact depth NOT COLLECTED |
| `/lzdl10823/dialog/text_result` | `dialog_node` publisher; subscriber count 0 | RELIABLE | VOLATILE | UNKNOWN | VERIFIED; exact depth NOT COLLECTED |
| `/lzdl10823/dialog/input_waveform` | `dialog_node` publisher | BEST_EFFORT | VOLATILE | UNKNOWN | VERIFIED; exact depth NOT COLLECTED |
| `/lzdl10823/dialog/session_active` | `dialog_node` publisher → `face_play_example` subscriber | RELIABLE | TRANSIENT_LOCAL | UNKNOWN | VERIFIED; exact depth NOT COLLECTED |

**VERIFIED:** `input_waveform` is an output of `dialog_node`; it is not a
microphone input topic. `session_active` was not in the early public compatibility
list, but it has a live consumer and is therefore a Phase 5 compatibility
candidate.

## Microphone path

**VERIFIED:** The current `dialog_node` captures microphone audio directly
through ALSA. No ROS2 microphone input subscription was observed.

Direct evidence:

```text
dialog_node PID: 5789
open capture FD: /dev/snd/pcmC0D0c
fuser owner: PID 5789 dialog_node
capture card: card 0, Yundea 1076
capture device: device 0, USB Audio
```

## Input audio format

**VERIFIED:** These are the live ALSA capture parameters of the current vendor
`dialog_node`:

```text
access: MMAP_INTERLEAVED
format: S16_LE
subformat: STD
channels: 1
rate: 16000 Hz
period_size: 1024 frames
buffer_size: 16384 frames
```

The Phase 2 Yandex PoC used 24 kHz mono audio. The Phase 5 choice between using
Yandex at 16 kHz and resampling 16 kHz → 24 kHz is **DEFERRED**; this snapshot
does not choose an adaptation strategy.

## PcmAudioFrame

**VERIFIED:** The installed interface file
`/lingze/install/lingze_msgs/share/lingze_msgs/msg/PcmAudioFrame.msg` contains:

```text
builtin_interfaces/Time stamp
uint32 sample_rate
uint8 channels
string format
uint8[] data
```

**VERIFIED:** The `.msg` and `.idl` files exist under the installed share
directory. In the collected shell, `ros2 interface show` and `ros2 pkg prefix`
could not resolve `lingze_msgs` through the package index. This package-index
condition does not mean the message type or its installed interface files are
absent.

**DEFERRED:** The actual runtime string expected in `PcmAudioFrame.format` was
not collected and must not be guessed.

## Speaker path and playback hardware

**VERIFIED:**

```text
/lzdl10823/audio/dialog_play
  → /lzdl10823/audio_speaker_node
  → /dev/snd/pcmC0D0p
```

`audio_speaker_node` subscribes to the Bluetooth, dialog, and system play/flush
topics plus `audio/volume_cmd`. It publishes:

```text
/lzdl10823/audio/speaker_active    std_msgs/msg/Bool
/lzdl10823/head/mouth_cmd          std_msgs/msg/Float32
```

**VERIFIED:** The retained vendor speaker path therefore continues to own final
playback, speaker-active feedback, and mouth/lip synchronization.

**VERIFIED:** Live playback hardware parameters were:

```text
device: /dev/snd/pcmC0D0p
access: MMAP_INTERLEAVED
format: S16_LE
channels: 2
rate: 48000 Hz
period_size: 960
buffer_size: 7682
```

The following were **NOT OBSERVED** and are **DEFERRED** to Phase 5 compatibility
work:

- the `PcmAudioFrame.format` value expected by `audio_speaker_node`;
- whether `audio_speaker_node` resamples input;
- whether it converts mono to stereo;
- its accepted input sample-rate/channel combinations.

The playback hardware parameters do not prove that publishers must send 48 kHz
stereo frames.

## systemd and launch chain

**VERIFIED:**

```text
service: lingze_robot.service
observed service state: active / running
FragmentPath: /etc/systemd/system/lingze_robot.service
WorkingDirectory: /lingze
ExecStart: /bin/bash /lingze/bin/start_robot.sh
observed launch process:
  /usr/bin/python3 /opt/ros/humble/bin/ros2 launch bringup bringup.launch.py
```

The evidence proves that systemd starts `start_robot.sh` and that the observed
process tree includes `ros2 launch bringup bringup.launch.py`. Full script
contents and the complete internal launch chain were **NOT COLLECTED**.

## Visual-input observation

**NOT OBSERVED:** No ROS2 camera/image subscription by `dialog_node` was present
in its subscriber list. The observed camera topic
`/lzdl10823/camera/image_omni/compressed` was published by `usb_camera_node` and
had subscriber count 0 at the sample time.

This does not prove that the closed-source process never accesses camera data
outside ROS2. Any such claim would be an **INFERENCE** and remains unverified.

## Current realtime model

```text
Runtime package/executable label: lingze_omni_s2s/dialog_node — VERIFIED
Exact current cloud model ID: NOT COLLECTED / NOT RUNTIME-VERIFIED
```

`qwen3.5-omni-flash-realtime` remains a working hypothesis only. It is not a
runtime-verified fact.

## Evidence caveats

1. **VERIFIED:** The first non-interactive shell lacked the sourced ROS
   environment; its `ros2: command not found` output is not evidence that the
   robot lacks ROS2. The corrected ROS collection is in `phase4_round1_ros.txt`.
2. **VERIFIED:** ROS node lists differed between sample times. The ROS graph is
   dynamic, so a single node list is not a permanent exhaustive inventory.
3. **VERIFIED conflict:** `/proc/asound/card0/pcm0c/sub0/status` reported
   `owner_pid: 8249`, while `/proc/5789/fd` and `fuser /dev/snd/pcmC0D0c`
   directly tied the capture FD to `dialog_node` PID 5789. Microphone ownership
   is based on the open FD and `fuser`; the status discrepancy is preserved and
   not explained by guesswork.
4. **VERIFIED:** `lingze_msgs` was not resolved by the shell's ROS package
   index, while its installed `.msg` and `.idl` files were directly located.
5. **NOT COLLECTED:** QoS history depth. The CLI reported `UNKNOWN` for every
   inspected endpoint; no numeric depth is inferred.

## Deferred items

- **DEFERRED to Phase 5:** actual `PcmAudioFrame.format` value.
- **DEFERRED to Phase 5:** `audio_speaker_node` input conversion/resampling and
  accepted sample-rate/channel combinations.
- **DEFERRED to Phase 5:** 16 kHz direct Yandex input versus 16 → 24 kHz
  resampling decision.
- **NOT COLLECTED / DEFERRED:** original system prompt/persona.
- **NOT COLLECTED:** exact current cloud model ID.
- **NOT COLLECTED / DEFERRED to integration:** full original dialog status
  sequence.
- **NOT COLLECTED / DEFERRED to integration:** original first-audio latency and
  live interruption experience.
- **NOT COLLECTED / DEFERRED to integration/acceptance:** original black-box
  conversational baseline. The user explicitly deferred it; it is not a Gate 4
  blocker.

## Gate 4 decision

**Gate 4 — PASS.** Direct runtime evidence now covers all Gate 4 inputs:

```text
actual microphone entry: VERIFIED
actual input PCM parameters: VERIFIED
PcmAudioFrame schema: VERIFIED
actual QoS: VERIFIED (history depth remains NOT COLLECTED)
actual node and namespace: VERIFIED
actual service/startup method: VERIFIED
actual ROS2 external contract: VERIFIED
```

The remaining format/conversion choices are Phase 5 adapter decisions, not
missing Gate 4 evidence. The user-deferred black-box experience baseline is an
integration/acceptance item and does not block the gate.

```text
Phase 4 — COMPLETE
Gate 4 — PASS
Phase 5 — NOT STARTED
```
