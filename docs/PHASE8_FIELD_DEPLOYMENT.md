# Phase 8 Field Deployment Record

> Updated: 2026-08-17
> Phase 8 — IN PROGRESS
> Phase 8C — COMPLETE
> Phase 8D — CONFIG/CREDENTIAL PREPARATION COMPLETE
> Phase 8E — SYSTEMD UNIT INSTALLED; DISABLED/INACTIVE
> Phase 8F — FIRST SWITCH ATTEMPT FAILED; VENDOR MODE RESTORED
> Phase 8F repository repair — COMPLETE LOCALLY; ROBOT RESYNC AND SECOND SWITCH PENDING

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

This repair is local repository evidence only. The new fixed source has not
been synchronized to the robot, and a second switch has not been run.

## Remaining Phase 8 conditions

- synchronize one reviewed fixed commit and rerun preflight on the robot;
- run the separately authorized second switch while preserving every readiness
  report and final-state output;
- prove the replacement ROS2 node start and complete human acceptance;
- prove `hw:0,0` capture, speaker/mouth/flush behavior, and exact
  `session_active` timing;
- execute and verify a real normal rollback;
- retain the first transition's exact historical failure as UNKNOWN;
- retain vendor watchdog/anti-third-party behavior as NOT PROVEN.

Phase 8 and Gate 8 are not complete or decided by this repair.
