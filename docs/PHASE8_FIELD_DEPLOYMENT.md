# Phase 8 Field Deployment Record

> Updated: 2026-08-18
> Phase 8 — IN PROGRESS
> Phase 8C — COMPLETE
> Phase 8D — CONFIG/CREDENTIAL PREPARATION COMPLETE
> Phase 8E — SYSTEMD UNIT INSTALLED; DISABLED/INACTIVE
> Phase 8F — FIRST SWITCH ATTEMPT FAILED; VENDOR MODE RESTORED
> Phase 8F repository repair — COMPLETE LOCALLY; LATER YANDEX MODE OBSERVED
> Phase 8H half-duplex mitigation — IMPLEMENTED LOCALLY; FIELD VALIDATION PENDING

This document records the Phase 8 facts supplied from the maintenance-window
field work separately from the historical Phase 4 and Phase 7 conclusions. No
secret values, environment-file contents, user audio, or conversation content
are recorded here.

## Verified field facts supplied for this repair

The robot is `xiaoling0040`, aarch64, Python 3.10.12, ROS2 Humble. At the
recorded checks, `/lingze/config/user_config.json` identified namespace
`lzdl10823`, `robot_current_mode=jijia`, and
`current_llm=lingze_omni_s2s`.

Phase 8C completed source deployment, an independent venv, venv-local aiohttp
3.14.3, ROS/vendor imports, colcon build, installed-executable interpreter
validation, and build preflight. System Python remained without aiohttp.

Phase 8D/E created the production YAML and a real root-owned mode-0600
environment file, installed the systemd unit disabled/inactive, safely applied
the vendor gate, and passed switch preflight. The repository contains neither
the real credentials nor their values.

A real robot-to-Yandex WebSocket/session probe passed. This proves the recorded
network/session probe only; it does not prove that the replacement ROS2 dialog
node took over the robot.

The vendor `/lzdl10823/audio/dialog_play` frame metadata was observed as
`24000 Hz`, one channel, `pcm_s16le`, and the speaker audibly played that vendor
frame. Mouth movement, internal speaker conversion, flush behavior, and exact
`session_active` timing were not verified.

## First Phase 8F switch attempt

The first formal switch failed during `transition` verification, before the
Yandex service start step. The later Yandex journal had no entries. Therefore
there is no evidence that the Yandex service started in that attempt and no
evidence that a vendor watchdog detected or terminated a Yandex node.

The old control plane then reported that its immediate automatic-rollback
`vendor-mode` verification failed. A later read-only state check found:

```text
marker=absent
vendor_service=active
yandex_service=inactive
```

A later complete `vendor-mode` report passed all recorded configuration,
service, process, mutual-exclusion, graph, speaker, and configuration-stability
checks. The robot was therefore safely back in verified vendor mode at that
later observation.

The old control plane discarded the first failed `transition` CheckReport, so
the exact historical failing check remains **UNKNOWN**. A readiness/settling
race between systemd state and process/ROS graph/audio readiness is the current
strongest engineering inference, not a verified root cause. The evidence does
not prove that a vendor watchdog or anti-third-party mechanism exists or does
not exist.

## Phase 8F repository repair

Switch and rollback now use one bounded readiness mechanism for transition,
service preflight, Yandex mode, automatic rollback vendor mode, and normal
rollback transition/vendor mode.

Time controls are distinct:

- `--timeout` is the overall deadline for each readiness stage (default 30 s);
- `--probe-timeout` bounds one command/ROS snapshot (default is the smaller of
  5 s or half the stage timeout, and must remain below the stage timeout);
- `--poll-interval` is the interval between snapshots (default 0.5 s);
- `--stable-passes` is the number of consecutive complete PASS reports required
  for transition, Yandex mode, and vendor mode (default 2). Service preflight
  uses one complete PASS while retaining independent fail-closed ExecStartPre
  behavior.

Only explicitly classified settling conditions are retried. Robot configuration
or mode drift, marker/gate/config/credential contradictions, dual dialogs,
unknown safety state, a vendor dialog in Yandex mode, and command failures stop
immediately. Timeout and hard-failure errors retain the complete last
`CheckReport`.

If automatic or normal rollback is not proven, the controller samples the
actual final marker, both service states, and both process families. Recovery
guidance is derived from that snapshot: it no longer tells an operator to
retain a marker that is actually absent, and it never claims vendor mode was
restored without a passing verifier report.

At the time of this repair, it was local repository evidence only: the fixed
source had not been synchronized and a second switch had not been run. The
later Yandex-mode field observation is recorded separately under Phase 8H.

## Phase 8F microphone CLI compatibility evidence

User-provided field results established the following command-level behavior:

```text
arecord --type raw
→ RC=1; unsupported option

arecord -D hw:0,0 -f S16_LE -c 1 -r 16000 -t raw
→ recording started successfully; timeout terminated it; RC=124
```

The timeout return code records the deliberate external timeout, not an
`arecord` startup failure. This verifies the replacement capture tuple
`hw:0,0 / S16_LE / mono / 16000 Hz` on the robot. The project adapter used the
unsupported long option `--type raw`; it now uses the supported equivalent
`--file-type raw` while preserving device, format, channel and rate arguments.

This is a project adapter CLI compatibility bug. It is not evidence of a
manufacturer watchdog, anti-third-party behavior, or another vendor protection
mechanism. This repository repair did not run the command or access the robot.

## Phase 8H half-duplex self-echo protection

### VERIFIED field observations

User-provided field evidence confirms that the robot established Yandex mode
and completed a real Russian voice conversation. It understood the human
speaker and returned appropriate Russian replies; the observed response was
subjectively fast, but no quantitative latency result is claimed.

The later field observation was that the robot began speaking continuously,
reported as `Она сама говорит без остановки`. This observation is verified;
its physical cause is not.

### INFERENCE

The leading hypothesis is that robot speaker output was acoustically captured
by the continuously running local microphone, uploaded to Yandex, and treated
as new speech. The repository has no acoustic echo cancellation. This
speaker-to-microphone feedback path is an inference, not a verified root cause;
the evidence does not prove a physical AEC diagnosis.

### IMPLEMENTED MITIGATION

The local repository now provides a configurable half-duplex controller mode.
The generic `DialogController` keeps speech barge-in enabled by default for
compatibility, while the ROS robot default and both robot configuration
templates explicitly set `barge_in_enabled: false`.

With speech barge-in disabled, microphone capture remains active for the whole
session, but `RESPONSE_STARTED` clears queued microphone PCM and suppresses new
uplink audio. The sender rechecks suppression after dequeue and before calling
the Yandex audio send boundary. `SPEECH_STARTED` during assistant output is
ignored without generation advance, speaker flush, response cancel, or state
change. Explicit `text_input` replacement/cancel behavior is unchanged.

After `RESPONSE_DONE`, uplink remains suppressed for the configured
`microphone_resume_guard_ms`; the robot policy is 500 ms. This value is a
project tuning policy, not a manufacturer or Yandex verified fact. The guard
uses event-loop monotonic time and does not stop or restart `arecord`.

This mitigation is implemented and locally tested only. It is not a
`FIELD PASS`: the repository was not synchronized to the robot in this work,
the real `/etc/casbot-yandex-realtime/casbot-yandex.yaml` was not changed, and
there has been no follow-up field test proving that continuous self-speech no
longer occurs. Even a later successful field test would validate the mitigation
without proving the acoustic-feedback hypothesis. The separate
`STATUS_IDLE`/automatic-session-ending issue remains out of scope.

## Remaining Phase 8 conditions

- synchronize one reviewed fixed commit and rerun preflight on the robot;
- preserve and review the readiness reports and final-state output from the
  later successful Yandex-mode establishment;
- preserve and review replacement-node/ROS-graph evidence from the established
  Yandex mode, and complete human acceptance including the self-speech retest;
- retain `hw:0,0 / S16_LE / mono / 16000 Hz` capture as VERIFIED field evidence;
- prove replacement-path speaker/mouth/flush behavior and exact `session_active`
  timing;
- execute and verify a real normal rollback;
- retain the first transition's exact historical failure as UNKNOWN;
- retain vendor watchdog/anti-third-party behavior as NOT PROVEN;
- deploy the reviewed Phase 8H source and robot configuration, then verify that
  continuous self-speech no longer occurs before recording a mitigation
  `FIELD PASS`;
- retain speaker acoustic feedback as an inference even if the mitigation
  passes in the field.

Phase 8 and Gate 8 are not complete or decided by this repair.
