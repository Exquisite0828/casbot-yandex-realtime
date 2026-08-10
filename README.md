# CASBOT → Yandex Realtime

## Project

CASBOT Yandex Realtime Migration

## Goal

将 CASBOT 当前的一体化 Realtime 云端对话能力替换为 Yandex Realtime，同时保持厂家开放的 ROS2 外部接口、speaker、嘴型、Web、运动控制等外围能力尽可能不变。

## Current status

```text
Phase 0 — COMPLETE
Phase 1 — NOT STARTED
```

## Key documents

```text
AGENTS.md
docs/YANDEX_REALTIME_MIGRATION_PLAN.md
docs/YANDEX_REALTIME_VERIFIED.md
docs/RUNTIME_SNAPSHOT.md
docs/vendor/二开文档.md
```

## Important constraint

厂家原始 `realtime_dialog` 源码不开放，因此本项目采用兼容 ROS2 契约的新 `realtime_dialog_node` 实现，而不是修改厂家闭源实现。
