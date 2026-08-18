# Phase 8 Field Deployment Record

> Updated: 2026-08-19
> Phase 8 — IN PROGRESS
> Phase 8C — COMPLETE
> Phase 8D — CONFIG/CREDENTIAL PREPARATION COMPLETE
> Phase 8E — SYSTEMD UNIT INSTALLED; DISABLED/INACTIVE
> Phase 8F — FIRST SWITCH ATTEMPT FAILED; VENDOR MODE RESTORED
> Phase 8F repository repair — COMPLETE LOCALLY; LATER YANDEX MODE OBSERVED
> Phase 8H half-duplex mitigation — FIELD PASS
> Phase 8I local implementation — COMPLETE / CONDITIONAL PASS
> Robot synchronization — PENDING
> systemd enable — NOT RUN
> cold-boot acceptance — NOT RUN
> Gate 8 — NOT FINAL

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

User-provided follow-up field evidence confirms that the Phase 8H robot runtime
actually loaded:

```text
barge_in_enabled=false
microphone_resume_guard_ms=500
```

With that half-duplex version loaded, the field retest did not reproduce the
robot speaking to itself. Response speed remained normal, and a new human
question after the robot finished answering still received a response. These
observations establish a **FIELD PASS for the mitigation**.

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

The field result validates the mitigation's observed behavior; it does not prove
that acoustic feedback was the unique physical root cause and does not constitute
AEC. The older relationship between the continuous-speech incident and a later
`STATUS_ERROR` remains unproven.

## Phase 8I default boot session and failure observability

Phase 8I is implemented and tested in the local repository only. Generic node
behavior keeps `auto_start_session=false`; the two CASBOT robot templates set it
to `true`. After publishers, services, timer, worker and the initial
`STATUS_IDLE/false` output are ready, one non-blocking start command is submitted
through the existing asyncio bridge. It is not a watchdog: a later manual
`stop_session` remains stopped, and no runtime auto-reconnect was added.

Fatal cleanup now emits one final combined diagnostic through a ROS-independent
sink. The node redacts the configured API key plus Authorization/API-key forms,
queues only the sanitized text, and writes it through the ROS logger so it can
reach the service journal. Unexpected command exceptions and otherwise
unreported critical start/stop failures are also observable; benign text-input
rejections are not promoted to error logs. No PCM, complete environment,
`RuntimeConfig` representation, env-file contents or Authorization header is
intentionally logged.

The systemd template now runs service preflight through the shared Phase 8F
readiness waiter with a 60 s overall deadline, 5 s per-probe bound, 0.5 s poll
interval and one complete service PASS. Only the explicit `--wait` entry polls;
the existing `casbot-yandex-preflight --mode service` remains a one-shot check.
Hard configuration/credential/marker/mode failures stop immediately, transient
ROS graph/speaker/microphone settling may retry, and timeout retains the final
complete `CheckReport`. `Restart=no` and the existing switch/rollback semantics
are unchanged.

This is **not** evidence that the robot now boots into Yandex mode. The fixed
commit has not been synchronized for Phase 8I, the real YAML has not been updated
for `auto_start_session`, the revised unit has not been installed or enabled,
and no unattended cold boot has been accepted. Therefore:

```text
Phase 8I local implementation — COMPLETE / CONDITIONAL PASS
Robot synchronization — PENDING
systemd enable — NOT RUN
cold-boot acceptance — NOT RUN
Gate 8 — NOT FINAL
```

### Separately authorized cold-boot acceptance sequence

Do not execute these steps as part of the local Phase 8I work:

1. Synchronize one reviewed fixed commit.
2. Set `auto_start_session=true` in the real robot YAML.
3. Rebuild the `realtime_dialog` package with colcon.
4. Install the reviewed systemd unit.
5. Run daemon-reload.
6. Run build and bounded service preflight.
7. Enable `casbot-yandex-dialog.service` under explicit authorization.
8. Reboot the whole robot.
9. Do not use SSH to call `start_session`.
10. Confirm Yandex mode PASS, `STATUS_LISTENING`, `session_active=true`, and an
    active arecord process.
11. Have an on-site person speak Russian and receive a normal response.
12. Verify multiple turns without self-speech and confirm a new question works
    after each answer.
13. Perform and verify the formal rollback to vendor mode.

## Remaining Phase 8 conditions

- synchronize one reviewed Phase 8I commit and rerun build/service preflight;
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
- update the real YAML with `auto_start_session=true`, rebuild
  `realtime_dialog`, install the reviewed systemd unit, and run daemon-reload;
- enable the service only under separate authorization, then perform one full
  unattended cold boot without calling `start_session` over SSH;
- verify Yandex mode, `STATUS_LISTENING`, `session_active=true`, arecord,
  direct Russian conversation, continued half-duplex behavior and formal
  rollback after that cold boot;
- retain speaker acoustic feedback as an inference despite the Phase 8H
  mitigation FIELD PASS.

Phase 8 and Gate 8 are not complete or decided by this repair.
