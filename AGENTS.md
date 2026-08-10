# AGENTS.md

## Project mission

Replace the CASBOT robot's current integrated realtime cloud conversation model with Yandex realtime voice capabilities while preserving the robot's external ROS2 behavior and unrelated subsystems.

Read `docs/YANDEX_REALTIME_MIGRATION_PLAN.md` before making changes.

## Evidence hierarchy

Use this order:

1. Actual robot runtime observations collected by read-only SSH.
2. Manufacturer CASBOT / Lingze secondary-development documentation.
3. Current official Yandex Cloud / Yandex AI Studio documentation.
4. Explicit user-provided facts.
5. Inference.

Never promote inference into fact.

Treat `qwen3.5-omni-flash-realtime` as a working hypothesis until runtime-verified.

## Scope

Preserve unless runtime evidence proves adaptation is required:

- `speaker_node`
- speaker routing
- mouth/lip synchronization
- web server
- robot motion/control
- unrelated ROS2 packages
- external dialog Topic/Service contracts

Do not reverse engineer or decompile closed-source vendor binaries.

## ROS2 compatibility target

Preserve documented behavior of:

- node `realtime_dialog_node`
- `/dialog/start_session`
- `/dialog/stop_session`
- `/dialog/text_input`
- `/dialog/status`
- `/dialog/text_result`
- `/audio/dialog_play`
- `/audio/dialog_flush`

Do not invent `lingze_msgs/msg/PcmAudioFrame` fields. Keep the boundary abstract until runtime inspection.

Target: Linux, ROS2 Humble, Python 3.10.

## Yandex research rule

Realtime APIs are time-sensitive.

Before protocol implementation, verify current official Yandex sources and record facts in `docs/YANDEX_REALTIME_VERIFIED.md` with:

- fact
- official source
- query/update date
- project impact

Do not implement from memory, old blogs, or stale examples.

If official sources conflict or do not prove required realtime audio behavior, stop at the project gate and report uncertainty.

## Remote robot safety

Until explicitly authorized:

- do not SSH to robot
- do not execute remote commands
- do not restart services
- do not write systemd
- do not kill processes
- do not upload files
- do not change `/lingze`
- do not install dependencies on robot

Phase 4 is read-only unless the user explicitly changes that rule.

Never collect or commit real API keys/tokens.

## Development rules

- Keep robot-specific input/output behind adapters.
- Keep Yandex network logic outside ROS2 callbacks.
- Do not block ROS2 executor with network I/O.
- Make interruption/cancellation explicit.
- Reject stale events from previous sessions/generations.
- Prefer minimal dependencies compatible with Python 3.10 and ARM/Linux.
- Configure endpoints, credentials, IDs, timeouts and audio settings externally.
- Never hardcode production secrets.
- Avoid unrelated refactors.

## Git

Before work:

```bash
git status --short
```

Do not modify unrelated user changes.

Do not commit automatically unless the user asks.

Keep secrets, `.env`, logs, caches, build products and sensitive runtime snapshots out of Git.

## Work loop

1. Read this file.
2. Read `docs/YANDEX_REALTIME_MIGRATION_PLAN.md`.
3. Identify active phase/gate.
4. Inspect before editing.
5. Make the smallest coherent change.
6. Run relevant tests.
7. Update plan checklist/status only with evidence.
8. Report changed files, tests, verified facts, unresolved facts, current gate, next task.

Do not silently cross a gate.

## Stop conditions

Stop and ask for a user decision if:

- current Yandex official API cannot support required realtime voice;
- official Yandex sources materially conflict;
- robot audio/message fields would have to be guessed;
- remote write access would be needed;
- unrelated robot subsystems would need modification;
- a secret is required but unavailable;
- fallback `STT → LLM → TTS` architecture would be required.
