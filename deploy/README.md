# CASBOT Yandex Deployment Control Plane

> Phase 7 — COMPLETE
> Gate 7 — CONDITIONAL PASS
> Phase 8 — IN PROGRESS
> Phase 8C — ROBOT BUILD COMPLETE; CONTROL-PLANE REVALIDATION PENDING
> Robot Yandex runtime / formal switch / formal rollback — NOT STARTED
> All write commands are dry-run unless `--apply` is supplied.
> This repository repair did not access or modify the robot.

## Layout

```text
deploy/
├── bin/       # launch, preflight, verify, switch, rollback, vendor gate, probe
├── config/    # production parameter/env examples and aiohttp requirement
├── lib/       # shared Python control plane and runtime environment loader
└── systemd/   # independent Yandex dialog service template
```

Every tool accepts `--root <temporary-root>` directly or through its command
syntax. Paths under `/lingze`, `/etc`, `/opt` and `/var/lib` are then mapped
inside that temporary directory. Real `/` apply operations require root.

## Phase 8 target layout

```text
/opt/casbot-yandex-realtime/{src,venv,build,install,log,deploy}
/etc/casbot-yandex-realtime/{casbot-yandex.yaml,yandex.env,external-dialog.enabled}
/var/lib/casbot-yandex-realtime/{vendor-backups,vendor-gate-manifest.json,operation-state}
```

Do not copy source into `/lingze/src` or install into `/lingze/install`.

## Python environment

Use Python 3.10 and an independent venv:

```bash
source /opt/tros/humble/setup.bash  # or /opt/ros/humble/setup.bash
source /lingze/install/setup.bash
python3 -m venv --system-site-packages /opt/casbot-yandex-realtime/venv
source /opt/casbot-yandex-realtime/venv/bin/activate
python -m pip install -r /opt/casbot-yandex-realtime/deploy/config/requirements.txt
colcon build --base-paths src
source /opt/casbot-yandex-realtime/install/setup.bash
```

Do not install `aiohttp` into system Python.

The Phase 8C robot did not have ensurepip, and apt exposed no installable
Candidate. The verified fallback was:

```bash
python3 -m venv --without-pip --system-site-packages /opt/casbot-yandex-realtime/venv
python3 -m pip install --target <venv-purelib> \
  -r /opt/casbot-yandex-realtime/deploy/config/requirements.txt
```

Here system pip is only the installer; the target must be the independent
venv's actual purelib. Before proceeding, verify that the venv Python resolves
`rclpy` and `lingze_msgs` through the intended ROS/vendor paths, resolves
`aiohttp` from venv purelib, and that system Python still cannot import
`aiohttp`. Do not change apt sources, install aiohttp into system Python, or
assume a generic purelib path.

## External setup and strict shell mode

Deployment Bash entry points keep `set -euo pipefail`. ROS/ament and vendor
setup files are external shell code and can legitimately inspect variables that
are unset. Every production setup source point therefore uses
`casbot_source_setup_file` from `lib/casbot-runtime-env`: it sources the file in
the current shell, temporarily relaxes only nounset, preserves environment side
effects, propagates the setup return code and stderr, then restores the caller's
original nounset state. Do not replace this with a subshell, a hardcoded
`AMENT_TRACE_SETUP_FILES` value, or a wrapper-wide `set +u`.

The repository repair has passed local regression only. The complete deployment
assets still need to be synchronized from one fixed commit and build preflight
must be rerun on the robot before any transition or Yandex service start.

## Vendor launch gate

Status and plans are read-only:

```bash
deploy/bin/casbot-yandex-vendor-gate status
deploy/bin/casbot-yandex-vendor-gate plan
deploy/bin/casbot-yandex-vendor-gate apply
deploy/bin/casbot-yandex-vendor-gate restore
```

The last two commands above remain dry-run. Explicit writes are:

```bash
deploy/bin/casbot-yandex-vendor-gate apply --apply
deploy/bin/casbot-yandex-vendor-gate restore --apply
```

`apply` requires one semantic anchor, creates a byte-preserved backup and
manifest, checks Python syntax and atomically replaces only the installed
`jijia.launch.py`. It rechecks the original SHA immediately before replace and
recovers original bytes after post-replace durability failures where possible.
`restore` refuses any backup/current drift, including a semantically unpatched
file that is not byte-identical to the manifest original.

Normal vendor rollback does not call `restore`; it only removes the marker.

## Configuration and credentials

Copy the examples in Phase 8:

```text
deploy/config/casbot-yandex.yaml.example
  → /etc/casbot-yandex-realtime/casbot-yandex.yaml
deploy/config/yandex.env.example
  → /etc/casbot-yandex-realtime/yandex.env (0600)
```

The example keeps `speaker_pcm_format` empty to force an explicit deployment
choice. Phase 8 field evidence now supports the production output tuple
`sample_rate=24000`, `channels=1`, `speaker_pcm_format=pcm_s16le`: the vendor
frame was observed and the speaker audibly played it. This does not prove mouth
movement, internal speaker conversion, flush behavior, or `hw:0,0` capture;
`hw:0,0` remains only a device-enumeration candidate.

Never print or commit populated `yandex.env`.

Preflight requires env/config/marker files to be regular non-symlinks with trusted
ownership, safe parents and modes. The env file accepts only the four documented
assignments—no `export`, duplicates, extra variables or unsupported escaping.

Every service/switch/rollback preflight and every verification mode re-reads
`/lingze/config/user_config.json`. Missing/unreadable files, malformed JSON,
non-object roots, or empty `namespace`, `robot_current_mode` or `current_llm`
fail closed. `robot_current_mode` must be exactly `jijia`; `current_llm` must be
`lingze_omni_s2s` or `lingze_s2s`. The launch wrapper repeats this guard, so an
environment namespace override cannot bypass robot-mode validation.
Long-running service/switch preflight and all verifier modes compare validated
start/end SHA-256 snapshots; a changed file fails instead of returning a result
based on stale robot mode.

## Preflight

```bash
deploy/bin/casbot-yandex-preflight --mode build
deploy/bin/casbot-yandex-preflight --mode service
deploy/bin/casbot-yandex-preflight --mode switch
deploy/bin/casbot-yandex-preflight --mode rollback
deploy/bin/casbot-yandex-preflight --mode switch --json
```

Checks return explicit `PASS`, `FAIL` or `DEFERRED`. Command/graph uncertainty
is a failure, not proof that a process or node is absent.

## Mode verification

```bash
deploy/bin/casbot-yandex-verify vendor-mode
deploy/bin/casbot-yandex-verify transition
deploy/bin/casbot-yandex-verify yandex-mode
```

The verifier checks marker, both services, both vendor backend process patterns,
the exact ROS dialog-node count, speaker presence and Yandex MainPID ownership.
The launch wrapper directly `exec`s the installed node, so Yandex mode requires
one matching PID exactly equal to MainPID; extra matching PIDs fail.
`transition` also requires microphone release.

Service active and one ROS node are not the final readiness criteria. In Phase
8, call `start_session` and wait for `/dialog/status=STATUS_LISTENING`.

## Metadata probe

Run only in vendor mode and have someone trigger one robot reply:

```bash
deploy/bin/casbot-yandex-probe-dialog-metadata --timeout 30
deploy/bin/casbot-yandex-probe-dialog-metadata --timeout 30 --json
```

It prints only `sample_rate`, `channels` and `format` from the first
`/namespace/audio/dialog_play` frame. It never accesses or saves `data`.

## Switch

Plan only:

```bash
deploy/bin/casbot-yandex-switch
```

Phase 8 apply requires both flags:

```bash
deploy/bin/casbot-yandex-switch --apply --maintenance-window
```

Order: preflight → stop Yandex → create marker → restart vendor main → verify
transition → service preflight → start Yandex → verify yandex-mode. Any hard failure after marker
creation triggers one bounded automatic rollback. The marker is removed and the
vendor is restarted only after the Yandex executable is proven absent; otherwise
the marker is retained to prevent two dialogs. A restored vendor mode still returns
switch failure so the original failure remains visible.

If `robot_current_mode` changes after marker creation, switching stops. Automatic
recovery stops Yandex if necessary, then retains the marker and does not restart
the vendor dialog until `user_config.json` again proves `jijia` and a supported
`current_llm`.

Switch and rollback apply share a nonblocking operation lock. A second concurrent
transaction fails before any service or marker action. A marker replace followed by
directory-fsync failure is still treated as post-marker and triggers the one rollback.

## Rollback

Plan only:

```bash
deploy/bin/casbot-yandex-rollback
```

Phase 8 apply:

```bash
deploy/bin/casbot-yandex-rollback --apply --maintenance-window
```

Order: rollback preflight → stop Yandex → verify transition → remove marker → restart vendor main →
verify vendor-mode. An already verified vendor mode is idempotent and does not
restart again.

On any CRITICAL recovery path, prove every matching Yandex dialog PID is absent
before removing the marker or restarting the vendor. Otherwise retain the marker.
The same recovery must also prove `robot_current_mode=jijia` and a supported
`current_llm`; invalid or changing robot configuration is never treated as safe
permission to ungate the vendor dialog.
Marker removal is guarded by validated configuration snapshots immediately before
and after deletion and again immediately before vendor restart. Drift restores the
marker atomically and blocks the restart.

## systemd

The template is `systemd/casbot-yandex-dialog.service`. It requires and follows
the retained vendor main service, but does not conflict with it. It has
`Restart=no` and a marker condition.

For the first Phase 8 validation, do not enable the service. Install the unit,
reload systemd, and use a manual start only after preflight and transition
verification. Enabling is a separate post-acceptance decision.

## Complete uninstall

There is deliberately no one-command directory deletion:

1. rollback and verify vendor-mode;
2. stop/disable the Yandex service;
3. `vendor-gate restore --apply`;
4. verify the original SHA-256;
5. manually remove the independent deployment directories.

Never use `rm -rf`, `rsync --delete`, or overwrite the vendor workspace.
