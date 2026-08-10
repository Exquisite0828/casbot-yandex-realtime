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
Phase 3 — NOT STARTED
```

本地 Phase 2 真人 PoC 已使用 `speech-realtime-260528` 跑通当前 WebSocket、24 kHz PCM 麦克风输入、俄语多轮回答、增量回答音频和本地扬声器输出；未使用 fallback。播放中本地停止与 truncate 曾实际成功，用户随后明确取消了“生成中 response.cancel 必须 live 验证”的本轮要求。详见 `docs/YANDEX_REALTIME_LOCAL_POC.md`。Phase 3 尚未开始。

## Key documents

```text
AGENTS.md
docs/YANDEX_REALTIME_MIGRATION_PLAN.md
docs/YANDEX_REALTIME_VERIFIED.md
docs/YANDEX_REALTIME_LOCAL_POC.md
docs/RUNTIME_SNAPSHOT.md
docs/vendor/二开文档.md
```

## Important constraint

厂家原始 `realtime_dialog` 源码不开放，因此本项目采用兼容 ROS2 契约的新 `realtime_dialog_node` 实现，而不是修改厂家闭源实现。
