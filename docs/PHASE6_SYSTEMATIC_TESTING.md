# Phase 6 Systematic Testing and Fault Recovery

> Test date: 2026-08-14
> Baseline commit: `82f5e107e56d4280f49b3742fa18cac96318bb4a`
> Phase 6: COMPLETE
> Gate 6: PASS
> Phase 7: NOT STARTED

## Scope and architecture

Phase 6 validates the local software path without ROS2, robot hardware or a
real Yandex connection:

```text
FakeMicAdapter
  → DialogController
  → real YandexRealtimeClient
  → real aiohttp ClientSession / WebSocket handshake / JSON frames
  → FakeYandexRealtimeServer on 127.0.0.1 with an OS-assigned port
  → real event normalization and response-to-generation mapping
  → QueuedRobotAudioOutputAdapter
```

The pre-change baseline at the listed commit was 68 core/mock tests plus 10
Phase 2 PoC regression tests, all passing. Phase 6 and its Gate repair bring the
core/integration discovery total to 103.

## Fake server design

`tests/fake_yandex_server.py` uses `aiohttp.web.Application`, `AppRunner`,
`TCPSite(host="127.0.0.1", port=0)` and `WebSocketResponse`. Each async context
owns and closes its WebSockets and runner. It records connection counts, client
JSON events and the test Authorization header in memory, but never prints the
header or writes PCM/audio to disk.

Exceptional teardown attempts every WebSocket close, then guarantees
`AppRunner.cleanup()` and reference reset before re-raising the first observed
exception. A controlled close-failure test proves later sockets and the runner
are not skipped.

The test API can wait for a connection or a selected client event, send a JSON
server event, send malformed text, close the socket, omit `session.updated`,
duplicate it, or close after a selected client event. Waits use events and
bounded timeouts; there is no external network dependency.

## Production transport seam

`YandexRealtimeClient` accepts an optional constructor-only
`websocket_connector`. It is not a `RuntimeConfig` field, ROS parameter or
environment option. `connect()` always calls the unchanged strict
`build_websocket_url()` first. Consequently non-WSS, localhost/non-Yandex and
legacy endpoints are rejected before the connector can run.

The default `_connect_production_websocket` uses the validated official URL and
the real aiohttp session. Tests explicitly inject a connector which receives
that official validated URL, records it for assertion, and maps the connection
to the fake loopback URL. This preserves the production endpoint boundary while
exercising a real TCP/WebSocket client/server exchange.

## Normal-path matrix

| Scenario | Observed assertion | Result |
|---|---|---|
| Session setup | `session.update` carries 24 kHz input/output, `ru-RU`, `server_vad`; `session.updated` reaches LISTENING | PASS |
| Duplicate ready | No second WebSocket, microphone start or sender | PASS |
| Microphone uplink | `input_audio_buffer.append` Base64 decodes byte-for-byte to the emitted 20 ms PCM | PASS |
| Text input | `conversation.item.create` then `response.create`; no microphone capture | PASS |
| Response downlink | created/text delta/audio delta/done drives status, text sink and correct PCM/rate/generation | PASS |
| Stop | Current response cancel is sent; flush, mic/sender/queue and transport cleanup complete; final IDLE | PASS |
| Interruption | generation advances, flush precedes new audio, old response events are stale, new response is accepted | PASS |
| Repeated interruption | Exactly one current sender and one microphone capture remain | PASS |
| Bounded output | Oldest queued response audio is dropped at the configured packet bound | PASS |

## Fault-injection matrix

| Fault | Expected/observed behavior | Result |
|---|---|---|
| Setup timeout | Fails at short setup timeout; no mic start; WebSocket/session/receiver closed | PASS |
| Server error before ready | Wakes setup immediately, redacts test secret, cleans transport, enters ERROR | PASS |
| Malformed JSON before ready | Fatal setup failure without waiting for the full timeout | PASS |
| Server error after ready | Generation invalidated, local flush, mic/sender stop, queues and transport cleaned, ERROR | PASS |
| Invalid Base64 response audio | Parser emits fatal error; no invalid packet reaches output | PASS |
| Malformed JSON after ready | Receiver failure is observable and centrally cleaned | PASS |
| Disconnect while LISTENING | Flush and complete active-session cleanup, then ERROR | PASS |
| Disconnect while SPEAKING | Queued old audio is invalidated before ERROR cleanup completes | PASS |
| Disconnect during microphone uplink | Current sender terminates and current generation is cleaned | PASS |
| Stop after dead transport | Bounded, idempotent, and leaves no transport resource | PASS |

## Recovery and lifecycle semantics

A current unexpected transport or fatal protocol/session failure follows this
frozen sequence:

```text
reject current lifecycle events
→ advance generation and invalidate stale audio
→ enqueue local flush
→ stop microphone capture and the single sender
→ clear bounded queues
→ close WebSocket, receive task and ClientSession
→ STATUS_ERROR
```

Recovery is explicit only. A later `start_session()` creates a new WebSocket,
generation, receiver and microphone sender. A later text input creates a new
text-only session without starting the microphone. Connection tokens prevent a
late callback from an old receiver from changing the recovered session. No
background retry, reconnect timer or infinite reconnect loop exists.

`_command_lock` serializes user commands, while `_lifecycle_lock` owns every
command/fatal/interruption lifecycle transition. A fatal source atomically
registers one cleanup task; later stop/start/text commands wait for it without
self-awaiting, and multiple same-generation fatal sources cannot duplicate the
generation advance, flush or close. Deterministic `asyncio.Event` gates cover
fatal-before-stop/start/text, stop-before-stale-fatal, multiple fatal sources,
and interruption-versus-stop ordering.

Each `response.created` records its generation until the matching terminal
`response.done` has been normalized, so post-interruption late text/audio remain
stale. The terminal event then removes the mapping; `close()` clears the whole
map even on cleanup errors. A stale connection token is checked before JSON
normalization can modify the map. A 1,000-response test finishes with an empty
map.

Five consecutive connect → ready → stop cycles verified after every round that
the captured WebSocket and ClientSession were closed; receiver and microphone
sender references were cleared/done; microphone and queues were empty; and no
live task named `yandex-*` remained. The suite was also run with
`ResourceWarning` promoted to an error.

## Issues found and fixed

1. Terminal WebSocket closure could end the receive loop silently. The client
   now emits one current-connection `TRANSPORT_CLOSED` event and distinguishes
   requested close through a closing flag and connection token.
2. Setup waited only for `session.updated`, so server/protocol/transport failure
   could wait until timeout. A per-connection setup future now completes with
   either ready or a safe failure and is reset/cleared on every lifecycle.
3. Runtime failure branches did not all perform the same cleanup and could leave
   microphone, sender or aiohttp resources alive. The controller now has one
   generation-guarded fatal cleanup path that flushes and releases all owners.
4. A late receiver callback could race a newly created session. Per-connection
   tokens reject stale callbacks independently of response generation mapping.
5. Direct construction accepted empty credentials/model and non-positive rates
   or timeouts. `RuntimeConfig` now fails fast on those invalid values without
   exposing the API key in its representation.
6. Fatal cleanup and user commands previously used separate ownership and could
   overlap. One lifecycle lock plus a registered single failure task now makes
   the final state and cleanup order deterministic.
7. Completed response IDs remained in the generation map for the life of a
   connection. Terminal done and close now release them without weakening late
   response suppression.
8. Fake-server teardown could skip runner cleanup after a socket-close error.
   Teardown now exhausts all cleanup owners and then rethrows the first error.
9. Transport errors were redacted at the wrapper message, but Python exception
   chaining could retain the original credential-bearing cause. Sanitized
   boundary exceptions now suppress that cause; controller-level tests cover
   connector, generic receive, send, WebSocket close and ClientSession close.

## Commands and results

The full discovery used the repository virtual environment because it contains
the existing aiohttp dependency. Final closeout verification produced:

```bash
PATH="$PWD/.venv/bin:$PATH" PYTHONPATH=src/realtime_dialog \
  python3 -m unittest discover -s tests -p 'test_*.py' -v
# 103 tests — PASS

PATH="$PWD/.venv/bin:$PATH" PYTHONWARNINGS=error::ResourceWarning \
  PYTHONPATH=src/realtime_dialog \
  python3 -m unittest discover -s tests -p 'test_*.py' -v
# 103 tests — PASS

cd tools/local_poc
python3 -m unittest test_realtime_voice_poc.py -v
# 10 tests — PASS
cd ../..

python3 -m compileall -q src tests tools/local_poc
# PASS

git diff --check
# PASS
```

The six deterministic lifecycle race tests passed in 20 consecutive runs. The
targeted 1,000 completed-response map test and the five-cycle
connect → ready → stop resource test also passed independently. At their
completion there was no open captured WebSocket or ClientSession, live receive
task, live microphone sender, `dialog-session-failure-cleanup` task or
`yandex-*` task, and no `ResourceWarning` was reported.

## Deferred and unverified items

- Local software: no automatic reconnect policy is designed; deliberate
  truncate/playback-progress precision remains outside this Phase 6 scope.
- ROS2/vendor: ROS2 Humble build/launch, real `lingze_msgs` import and exact
  vendor `session_active` timing remain UNKNOWN / DEFERRED / CONDITIONAL.
- Robot: `PcmAudioFrame.format`, accepted speaker rate/channels/conversion,
  actual arecord device string, speaker/mouth/shutdown-flush behavior and all
  end-to-end robot behavior remain unverified.
- Deployment: service integration, switch-over and rollback belong to Phase 7
  or later and were not designed or started here.

The ROS2/vendor and robot items above retain their **UNKNOWN / DEFERRED /
CONDITIONAL** evidence status. They are subsequent environment/integration
checks, not blockers for the local-software Gate 6 decision, and no value or
behavior has been guessed in order to close this gate.

## Safety statement and Gate decision

The tests use only obviously fake credentials and loopback networking. No real
Yandex API call, SSH, robot command, raw robot evidence, raw audio persistence,
systemd/bringup change, deployment action, commit or push occurred.

**Gate 6: PASS. Phase 6: COMPLETE. Phase 7: NOT STARTED.** This decision is
limited to the local software scope and evidence documented above. It is not a
ROS2 runtime, robot integration, real Yandex service or deployment PASS.
