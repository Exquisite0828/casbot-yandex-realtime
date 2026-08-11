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
Phase 5 — NOT STARTED
```

本地 Phase 2 真人 PoC 已使用 `speech-realtime-260528` 跑通当前 WebSocket、24 kHz PCM 麦克风输入、俄语多轮回答、增量回答音频和本地扬声器输出；未使用 fallback。播放中本地停止与 truncate 曾实际成功，用户随后明确取消了“生成中 response.cancel 必须 live 验证”的本轮要求。详见 `docs/YANDEX_REALTIME_LOCAL_POC.md`。

Phase 3 已建立 ROS2 Humble / Python 3.10 `ament_python` 包 `realtime_dialog`：厂家公开 `/dialog/*` 与 `/audio/dialog_flush` 契约由薄 ROS wrapper 提供，网络请求交给独立 asyncio worker，纯 Python controller 负责状态机和 generation/stale-event suppression。机器人麦克风与 `/audio/dialog_play` 仍由 Adapter 隔离，未猜测 `PcmAudioFrame`。本机无 ROS2 Humble，core/mock 测试通过但未实际启动 ROS 节点，因此 Gate 3 为 `CONDITIONAL PASS`。详见 `docs/ROS2_COMPATIBILITY_SKELETON.md`。

Phase 4 的只读运行时证据已冻结实际边界。**VERIFIED：**节点为 `/lzdl10823/dialog_node`，namespace 为 `/lzdl10823`；厂家节点直接通过 ALSA 以 S16_LE、mono、16 kHz 采集麦克风；`PcmAudioFrame` 字段、dialog play/flush/status QoS、`audio_speaker_node` 播放与嘴型链路，以及 `lingze_robot.service → start_robot.sh → ros2 launch bringup` 启动链均有直接证据。**NOT OBSERVED / DEFERRED：**`PcmAudioFrame.format` 实际值和 speaker 输入转换行为留给 Phase 5。机器人适配尚未开始，也未将机器人接入 Yandex。详见 `docs/RUNTIME_SNAPSHOT.md`。

## Key documents

```text
AGENTS.md
docs/YANDEX_REALTIME_MIGRATION_PLAN.md
docs/YANDEX_REALTIME_VERIFIED.md
docs/YANDEX_REALTIME_LOCAL_POC.md
docs/ROS2_COMPATIBILITY_SKELETON.md
docs/RUNTIME_SNAPSHOT.md
docs/vendor/二开文档.md
```

## Important constraint

厂家原始 `realtime_dialog` 源码不开放，因此本项目采用兼容 ROS2 契约的新 `realtime_dialog_node` 实现，而不是修改厂家闭源实现。
