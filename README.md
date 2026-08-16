# CASBOT → Yandex Realtime

## Project

CASBOT Yandex Realtime Migration

## Goal

将 CASBOT 当前的一体化 Realtime 云端对话能力替换为 Yandex Realtime，同时保持厂家开放的 ROS2 外部接口、speaker、嘴型、Web、运动控制等外围能力尽可能不变。

## Current status

```text
Phase 0 — COMPLETE
Phase 1 — COMPLETE
Gate 1 — CONDITIONAL PASS
Phase 2 — COMPLETE
Gate 2 — PASS
Phase 3 — COMPLETE
Gate 3 — CONDITIONAL PASS
Phase 4 — COMPLETE
Gate 4 — PASS
Phase 5 — COMPLETE
Gate 5 — CONDITIONAL PASS
Phase 6 — COMPLETE
Gate 6 — PASS
Phase 7 — COMPLETE
Gate 7 — CONDITIONAL PASS
Phase 8 — IN PROGRESS
Phase 8C — ROBOT BUILD COMPLETE; CONTROL-PLANE REVALIDATION PENDING
Robot Yandex runtime — NOT STARTED
Formal switch — NOT STARTED
Formal rollback — NOT STARTED
```

本地 Phase 2 真人 PoC 已使用 `speech-realtime-260528` 跑通当前 WebSocket、24 kHz PCM 麦克风输入、俄语多轮回答、增量回答音频和本地扬声器输出；未使用 fallback。播放中本地停止与 truncate 曾实际成功，用户随后明确取消了“生成中 response.cancel 必须 live 验证”的本轮要求。详见 `docs/YANDEX_REALTIME_LOCAL_POC.md`。

Phase 3 已建立 ROS2 Humble / Python 3.10 `ament_python` 包 `realtime_dialog`；其历史 Gate 3 仍为 `CONDITIONAL PASS`，原因是本机无 ROS2 Humble，未实际启动 wrapper。

Phase 4 的只读运行时证据已冻结实际边界。**VERIFIED：**节点为 `/lzdl10823/dialog_node`，namespace 为 `/lzdl10823`；厂家节点直接通过 ALSA 以 S16_LE、mono、16 kHz 采集麦克风；`PcmAudioFrame` 字段、dialog play/flush/status QoS、`audio_speaker_node` 播放与嘴型链路，以及 `lingze_robot.service → start_robot.sh → ros2 launch bringup` 启动链均有直接证据。Phase 4 收口时，`PcmAudioFrame.format` 实际值和 speaker 输入转换行为仍为 **NOT OBSERVED / DEFERRED**；后续 Phase 8 实机事实单独记录在 `docs/RUNTIME_SNAPSHOT.md`，不改写 Phase 4 历史结论。Phase 4 未将机器人接入 Yandex。

Phase 5 已完成本地适配：`ArecordMicAdapter` 以可配置 ALSA device 捕获 16 kHz PCM16 mono，经过无第三方依赖的状态化 16→24 kHz 重采样和 20 ms 分片后，由有界队列中的单一异步 sender 发送；Yandex 24 kHz mono 输出进入带 generation/playback epoch 的有界队列，再由 ROS executor 构造真实 `lingze_msgs/msg/PcmAudioFrame`。ROS 名称已相对化，CASBOT launch profile 可解析到 `/lzdl10823/dialog_node`，并加入已验证 QoS 和 `dialog/session_active` 项目兼容语义。

Phase 5 Gate 修复补齐了生命周期边界：stop 在 arecord shutdown 前先 enqueue 本地 flush；capture 异常经线程安全、带 generation/capture token 的回调进入 Controller；cancel 失败仍强制 close transport；ROS spin 结束后会在销毁节点前显式 enqueue 并 drain 最终 flush。68 项 core/mock tests 和 10 项 Phase 2 PoC regression 已通过复审，Gate 5 结论为 `CONDITIONAL PASS`。

Gate 5 收口时的条件是后续真实环境验证：ROS2 Humble + vendor overlay 的真实 build/launch、真实 `lingze_msgs.msg.PcmAudioFrame` import、`PcmAudioFrame.format` runtime 值、speaker 接受的 sample-rate/channels 及是否执行 resample/mono-stereo conversion、真实 arecord device string/executable、实机 speaker/嘴型/shutdown flush 行为，以及厂家 `session_active` 精确时序。Phase 8 已覆盖其中一部分，见下文和 `docs/RUNTIME_SNAPSHOT.md`；其余项继续通过配置、Adapter 或 fail-fast 隔离，不作猜测硬编码。

Phase 6 已完成并通过 Gate 6 本地软件测试复审。测试通过构造器专用 connector 将严格校验后的官方 Yandex URL 映射到 `127.0.0.1` 临时端口，实际经过 aiohttp TCP/WebSocket handshake 和 JSON 收发；生产 endpoint 校验、生产 connector 与 session schema 均未放宽。正常语音/文本链路、统一 lifecycle 串行、stop/interruption/stale suppression、response-generation map 释放、setup/runtime 故障、异常清理、五条凭据脱敏路径、显式恢复及 5 次资源生命周期均有覆盖。103 项 core/integration tests 与 10 项 Phase 2 PoC regression 通过；没有真实 Yandex 调用、ROS2/机器人运行或自动重连。Gate 6 的 `PASS` 仅证明该本地测试范围，不表示机器人已接入 Yandex。Gate 5 保留的 ROS2/vendor overlay、真实消息 import、speaker/arecord 参数和实机行为仍为 **UNKNOWN / DEFERRED / CONDITIONAL**；它们不是 Gate 6 的本地软件阻塞项，留待后续集成环境验证。详见 `docs/PHASE6_SYSTEMATIC_TESTING.md`。

Phase 7 已取得用户提供的只读启动链补充证据：当前 `jijia.launch.py` 直接启动厂家 dialog，且 dialog 与 speaker/Web/运动等是并列项；停止整个 `lingze_robot.service` 会停止全部厂家 ROS2 进程组，因此部署路线冻结为“厂家 launch marker gate + 独立 Yandex workspace/venv/systemd service”。本地已实现默认 dry-run 的 gate、preflight、verify、switch、rollback 和 metadata probe。Phase 7 已 **COMPLETE**，Gate 7 为 **CONDITIONAL PASS**；其收口时尚未部署、未禁用厂家节点。

Phase 8 已进入 **IN PROGRESS**。根据本轮任务提供的 Phase 8 实机记录，Phase 8C 已完成固定源码上传、独立 venv、aiohttp 隔离、ROS2 build 和安装产物验证；厂家首个 `/audio/dialog_play` frame 为 `24000 Hz / 1 channel / pcm_s16le`，并已确认 speaker 实际出声，嘴型尚未确认。机器人缺少 ensurepip，现场采用 `venv --without-pip --system-site-packages` 配合 `pip --target` 写入 venv purelib，验证 venv 可导入 `rclpy`、`lingze_msgs`、`aiohttp` 且系统 Python 仍无 aiohttp。

build preflight 随后发现严格 shell 在 source ROS/ament setup 时触发 `AMENT_TRACE_SETUP_FILES: unbound variable`。仓库已在共享 setup 加载层完成 nounset 兼容修复并通过本地回归；新版本尚未重新同步到机器人，机器人 build preflight 尚未复验。厂家 launch、marker、service 和 dialog 未被切换或修改，Yandex runtime、真实凭据/连接、正式 switch/rollback 均未开始。`hw:0,0` 实际打开、嘴型/flush、厂家 `session_active` 精确时序和 speaker 转换行为仍为 **UNKNOWN / DEFERRED / CONDITIONAL**。详见 `docs/PHASE7_DEPLOYMENT_DESIGN.md`、`docs/RUNTIME_SNAPSHOT.md` 和 `deploy/README.md`。

## Key documents

```text
AGENTS.md
docs/YANDEX_REALTIME_MIGRATION_PLAN.md
docs/YANDEX_REALTIME_VERIFIED.md
docs/YANDEX_REALTIME_LOCAL_POC.md
docs/PHASE6_SYSTEMATIC_TESTING.md
docs/PHASE7_DEPLOYMENT_DESIGN.md
docs/ROS2_COMPATIBILITY_SKELETON.md
docs/RUNTIME_SNAPSHOT.md
docs/vendor/二开文档.md
```

## Important constraint

厂家原始 `realtime_dialog` 源码不开放，因此本项目采用兼容 ROS2 契约的新 `realtime_dialog_node` 实现，而不是修改厂家闭源实现。
