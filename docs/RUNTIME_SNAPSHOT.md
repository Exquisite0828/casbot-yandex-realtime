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

## Phase 7 supplemental read-only evidence (2026-08-14)

本节补充 Phase 7 由用户提供的只读 SSH 输出。它不改写或替代上面的 Phase 4
冻结结论。证据等级为 **VERIFIED BY USER-PROVIDED READ-ONLY SSH TRANSCRIPT**；
本轮 Codex 没有重新 SSH，也没有执行机器人命令。

**VERIFIED BY USER-PROVIDED READ-ONLY SSH TRANSCRIPT：**

```text
hostname: xiaoling0040
robot_current_mode: jijia
current_llm: lingze_omni_s2s
namespace: lzdl10823

ROS2 node: /lzdl10823/dialog_node
runtime executable:
  /lingze/install/lingze_omni_s2s/lib/lingze_omni_s2s/dialog_node
parent launch process:
  /usr/bin/python3 /opt/ros/humble/bin/ros2 launch bringup bringup.launch.py
installed mode launch:
  /lingze/install/bringup/share/bringup/launch/launch/jijia.launch.py
```

`jijia.launch.py` 中 `_dialog_backend_node()` 读取
`/lingze/config/user_config.json` 的 `current_llm`，只把
`lingze_omni_s2s`/`lingze_s2s` 映射到相应 package，再启动 executable
`dialog_node`。dialog、speaker、Web、系统命令、触摸、摄像头及其他节点是并列
启动项。厂家技术支持表示可在 bringup launch 屏蔽其中一个节点。

在已搜索的 bringup launch 目录中没有发现 dialog 专用 `respawn`、
`OnProcessExit` 或 restart 定义。因此只确认“该 launch 定义没有 dialog 独立自动
拉起”；机器人其他位置是否有 watchdog 保持 **UNKNOWN**。

厂家主 service 内容补充为：

```ini
Type=simple
User=root
WorkingDirectory=/lingze
ExecStart=/bin/bash /lingze/bin/start_robot.sh
KillSignal=SIGINT
KillMode=control-group
TimeoutStopSec=5s
Restart=always
RestartSec=5s
```

`start_robot.sh` 会 source `/opt/tros/humble/setup.bash` 或
`/opt/ros/humble/setup.bash`、设置 RMW/FastDDS、source
`/lingze/install/setup.bash`、等待设备并 exec bringup launch。停止该 service 会
停止整个 ROS2 control group，因此不能作为长期只禁用 dialog 的部署方式。

构建/运行环境补充事实：

```text
ROS2 Humble
Python 3.10.12
colcon: /usr/local/bin/colcon
rclpy: import OK
lingze_msgs.msg.PcmAudioFrame: import OK
arecord: /usr/bin/arecord
capture enumeration: card 0 / device 0, Yundea 1076 USB Audio
aiohttp in current system Python: NOT INSTALLED
```

`hw:0,0` 只是由设备枚举支持的集成候选；尚未在厂家 dialog 退出后实际打开，
不是 capture PASS。曾只读监听 `/lzdl10823/audio/dialog_play` metadata 30 秒，因
办公室无人触发机器人回答而未收到消息。该 timeout 不提供 `format`、rate 或
channels 事实。

本补充证据采集没有机器人修改；本轮也没有 SSH、systemd 操作、marker 创建、
上传、安装、进程停止或真实 Yandex 调用。

## Phase 8C field evidence and gate repair boundary（2026-08-16）

本节是本轮任务提供的 Phase 8 实机记录，不改写 Phase 4 冻结证据或 Phase 7
只读记录。本轮 Codex 没有 SSH、访问机器人或执行远程操作。

**VERIFIED BY USER-PROVIDED PHASE 8 FIELD RECORD：**

```text
host: xiaoling0040
architecture: aarch64
Python: 3.10.12
ROS2: Humble
robot_current_mode: jijia
current_llm: lingze_omni_s2s
namespace: lzdl10823

deployed source commit: 54962929981bad8a5aefeb5c9da13d0bbc830666
independent venv: CREATED
aiohttp isolated to venv purelib: VERIFIED
ROS2 build and installed executable: VERIFIED
Robot Yandex runtime: NOT STARTED
```

机器人没有 ensurepip，apt 也没有可用 Candidate。已验证的受控 fallback 是：

```text
python3 -m venv --without-pip --system-site-packages <venv>
system pip used only as installer
pip --target <venv purelib> aiohttp
```

验证结果为 venv Python 可导入 `rclpy`、`lingze_msgs` 和 venv purelib 中的
`aiohttp`，系统 Python 仍无法导入 aiohttp。该事实不构成修改 apt 源或污染系统
Python 的许可。

厂家首个 `/lzdl10823/audio/dialog_play` frame 的实际 metadata 为：

```text
sample_rate: 24000
channels: 1
format: pcm_s16le
```

此 frame 已由 speaker 实际播放出声，因此仅能确认 speaker 接受该实际 tuple 并播放；
嘴型未确认，speaker 是否内部重采样或进行 mono/stereo 转换也未确认。

build preflight 的实际阻塞调用链为：

```text
strict deployment wrapper (set -u)
→ source /opt/tros/humble/setup.bash
→ external ament setup reads unset AMENT_TRACE_SETUP_FILES
→ unbound variable
```

A/B 结果为 nounset 开启时失败、在 source 的动态范围临时关闭 nounset 时 setup
成功。因此根因是部署 shell 与外部 ROS/ament setup 的 option 兼容性，不是 Yandex、
ROS package 或 colcon build 失败。仓库已在共享 setup 加载层完成修复并通过本地
回归；机器人尚未同步新版本，build preflight 尚未复验。

现场安全状态保持：厂家 launch 未修改，gate 未 apply，marker 不存在，service 未
重启，厂家 dialog 未停止，Yandex dialog 未启动，没有真实 Yandex 凭据或连接。
`hw:0,0` 尚未在厂家 dialog 退出后实际打开；嘴型、flush、`session_active` 精确时序、
真实 Yandex 和正式 switch/rollback 仍未验证。

## Phase 8D–8F field evidence and readiness repair boundary（2026-08-17）

本节继续使用任务提供的维护窗口记录；本轮 Codex 没有重新 SSH、读取真实 env、
运行远程命令或操作机器人。

**VERIFIED BY USER-PROVIDED PHASE 8 FIELD RECORD：**

```text
Phase 8C: COMPLETE; build preflight PASS
Phase 8D: CONFIG/CREDENTIAL PREPARATION COMPLETE
Phase 8E: systemd unit installed; disabled/inactive
vendor gate: PATCHED
marker after recovery: ABSENT
real robot-to-Yandex WebSocket/session probe: PASS
```

生产 YAML 和真实 `yandex.env` 已在机器人准备，后者为 root-owned mode 0600；本文及
仓库不保存其值。真实 session probe 只证明机器人网络/session 建立，不证明 Yandex
ROS2 dialog service 或 node 已接管机器人。

第一次正式 switch 在 `transition` verify 失败，早于 Yandex service start。稍后的
Yandex journal 为 `-- No entries --`。automatic rollback 的立即 `vendor-mode` verify
返回失败；再晚些只读状态为 marker absent、vendor service active、Yandex service
inactive，完整 `vendor-mode` report 全部 PASS。由此只确认稍后观察时机器人安全恢复
厂家模式。

旧控制面没有保存第一次 transition 的完整失败报告，具体历史失败项保持
**UNKNOWN**。systemd active 与 ROS graph/process/audio readiness 之间的 settling race
是当前最强工程推断，不是 VERIFIED root cause。现场证据既不证明厂家 watchdog/
反第三方机制存在，也不证明其不存在。

本地仓库 Phase 8F repair 引入有界 readiness polling、独立阶段/单 probe timeout、
连续稳定 PASS、显式 transient/hard 分类、完整 last `CheckReport`，以及基于最终
marker/service/process snapshot 的恢复指引。该修复尚未同步机器人，第二次正式
switch 和真实正常 rollback 均未执行。详细时间线见
`docs/PHASE8_FIELD_DEPLOYMENT.md`。

继续未决：`hw:0,0` capture PASS、替换节点的 speaker/嘴型/flush、厂家
`session_active` 精确时序、正常 rollback，以及完整机器人功能验收。

## Phase 8H follow-up field evidence（2026-08-19）

本节记录用户提供的后续现场事实；本轮 Codex 没有 SSH、机器人命令或真实 Yandex
调用。

**VERIFIED BY USER-PROVIDED FIELD RESULT：**第二次受控 Vendor → Yandex 切换
成功；Yandex Realtime、机器人麦克风、兼容 ROS2 node 与 speaker 回复链路曾真实
运行。真人俄语理解与回复正常，现场主观响应速度正常或很快。

机器人实际加载的 Phase 8H 参数为：

```text
barge_in_enabled=false
microphone_resume_guard_ms=500
```

加载该版本后的现场复测未再次出现机器人自问自答，响应速度正常，机器人回答结束
后再次提问仍可继续响应。因此 Phase 8H mitigation 为 **FIELD PASS**。

**INFERENCE / UNKNOWN：**该结果只证明 mitigation 在这次现场复测中有效，不能证明
speaker acoustic feedback 是唯一物理根因，也不构成 AEC。旧版本曾见的
`STATUS_ERROR` 是否由此前连续自反馈造成仍未验证；正常使用下是否会独立随机发生
session failure 仍未知。

## Phase 8I local implementation boundary（2026-08-19）

本地仓库增加 generic-default-false 的 `auto_start_session`，两个 CASBOT 配置模板显式
设为 true；节点完整初始化并先排入 `STATUS_IDLE/false` 后，仅通过后台 asyncio bridge
非阻塞提交一次 start。人工 stop 后不会自动再次启动，也没有 runtime auto-reconnect。

Controller fatal cleanup 完成后输出一次最终组合 failure diagnostic。节点仅将经 API
key、Authorization 和 API-key 形式脱敏后的 reason 放入 outbound queue，再由 ROS
logger 写入 systemd journal；不主动记录 PCM、完整环境、`RuntimeConfig` repr、env 文件
内容或 WebSocket Authorization header。

systemd service 模板的 ExecStartPre 改为复用 Phase 8F shared readiness waiter：60 秒
overall deadline、5 秒 probe bound、0.5 秒 poll interval、service 一次完整 PASS；hard
failure 立即停止，transient graph/speaker/microphone settling 可重试，timeout 保留最后
完整报告。一次性的 `preflight --mode service` 仍保持兼容，`Restart=no` 不变。

以上仅为本地实现边界，不是机器人开机默认使用 Yandex 的运行时事实：

```text
Phase 8I local implementation — COMPLETE / CONDITIONAL PASS
Robot synchronization — PENDING
systemd enable — NOT RUN
cold-boot acceptance — NOT RUN
Gate 8 — NOT FINAL
```
