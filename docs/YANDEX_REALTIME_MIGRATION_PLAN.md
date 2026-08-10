# CASBOT → Yandex Realtime 迁移实施计划

> 版本：v1.0  
> 状态：准备开工  
> 目标平台：Linux / ROS2 Humble / Python 3.10  
> 核心原则：本地开发优先、最少 SSH、保持机器人外部 ROS2 契约不变、可快速回滚。

## 0. 项目目标

将机器人当前的一体化 Realtime 云端对话模型替换为 Yandex 的实时语音方案，从而：

- 停止使用当前千问 Realtime 模型及其 Token/计费；
- 保持机器人“直接语音对话”的使用方式；
- 保持 `speaker_node`、扬声器、嘴型、Web、动作、运动控制等外围功能不变；
- 尽量只替换 `realtime_dialog` 这一层的云端对话实现；
- 优先在本地完成开发与测试，只在必要阶段短时间 SSH 机器人；
- 部署失败时可快速恢复厂家原有对话节点。

### 已确认事实

1. 厂家不开放 `/lingze/src/` 与原 `realtime_dialog` 源码。
2. 厂家要求按照二开文档第 5 章，通过兼容 ROS2 接口替换 `realtime_dialog` 节点。
3. 厂家已确认当前架构不是独立的 `ASR → LLM → TTS` 三模型链路，而是一体化 Realtime 对话方案。
4. 当前模型按现有信息高度怀疑为 `qwen3.5-omni-flash-realtime`，但在运行环境中尚未独立验证，因此在代码和文档中必须标记为“待运行时确认”，不能当作绝对事实。
5. 机器人是直接语音对话场景。

### 项目非目标

- 不修改舵机、运动控制、视觉、Web 前端等无关模块。
- 不破解、不反编译厂家闭源代码。
- 不覆盖厂家整个 ROS2 工作空间。
- 第一阶段不增加 RAG、工具调用、长期记忆等新业务功能。
- 第一阶段不追求声音与现有模型 100% 完全一致；声音、节奏、VAD 等属于验收项。

## 1. 厂家二开契约

新实现必须尽量保持厂家二开文档第 5 章规定的外部行为。

### 节点

```text
realtime_dialog_node
```

### Service

```text
/dialog/start_session
/dialog/stop_session
```

类型：

```text
std_srvs/srv/Trigger
```

### Topic

```text
/dialog/text_input      std_msgs/msg/String
/dialog/status          std_msgs/msg/String
/dialog/text_result     std_msgs/msg/String
/audio/dialog_play      lingze_msgs/msg/PcmAudioFrame
/audio/dialog_flush     std_msgs/msg/Bool
```

### 状态兼容目标

```text
STATUS_IDLE
STATUS_CONNECTING
STATUS_LISTENING
STATUS_SPEAKING_TEXT
STATUS_ERROR
```

### 行为约束

- `/dialog/text_input` 在无会话时也应能够触发一轮问答或播报。
- 新文本或 `/dialog/stop_session` 应中断旧回复。
- 中断时应触发 `/audio/dialog_flush=true`。
- 回答音频继续交给 `/audio/dialog_play`。
- 音频继续由原 `speaker_node` 统一播放并驱动嘴型。
- 新节点不得直接取代 `speaker_node` 或独占最终播放声卡。
- 外部 ROS2 Topic / Service / 状态语义尽量保持不变。
- Web 和上层业务应尽量无感。

## 2. 当前架构与目标架构

### 当前工作假设

```text
机器人麦克风
      ↓
厂家 realtime_dialog_node
      ↓ WebSocket / Realtime Protocol
Qwen Realtime（一体化实时语音模型，具体型号待运行时确认）
      ↓
实时文本 / 实时回答音频
      ↓
/audio/dialog_play
      ↓
speaker_node
      ↓
扬声器 + 嘴型
```

### 目标架构

```text
机器人麦克风
      ↓
我们实现的 realtime_dialog_node
      ↓
Yandex Realtime / Yandex Voice Agent API
      ↓
实时文本 / 实时回答音频
      ↓
/audio/dialog_play
      ↓
原 speaker_node
      ↓
原扬声器 + 原嘴型
```

### 架构原则

机器人外部只应该感知到：

```text
原 realtime_dialog_node
        ↓
替换成
        ↓
兼容 realtime_dialog_node
```

而不需要修改外围消费者。

## 3. 关键技术决策

### 主路线 A：Yandex Realtime 一体化替换

优先验证：

```text
实时麦克风 PCM
      ↓
Yandex Realtime
      ↓
实时回答音频 + 文本
      ↓
ROS2 兼容输出
```

原因：它与当前“一体化 Realtime”架构最接近，最符合“只换云端对话供应商”的目标。

### 备用路线 B：分段式 Yandex 语音链路

只有当主路线无法满足生产要求时再评估：

```text
STT
 ↓
Yandex 文本大模型
 ↓
TTS
```

此路线不是第一选择，因为会扩大状态机、延迟、打断、音频格式和故障处理的工程范围。

### 决策 Gate

在任何机器人部署代码完成前，必须先基于 **当前 Yandex 官方文档** 验证：

- 当前可用的 Realtime endpoint；
- 鉴权方式；
- 输入音频格式；
- 输出音频能力；
- VAD / turn detection；
- 打断 / truncate / cancel 机制；
- 支持语言；
- 会话限制；
- 当前正式支持与 Preview 能力边界。

Yandex Realtime API 在 2026 年发生过协议格式与 endpoint 调整，因此禁止根据旧博客、旧示例或记忆直接实现。

## 4. 当前未知项

以下未知项 **不阻塞 Phase 1 本地开发**，但会阻塞最终机器人接入：

| 未知项 | 用途 | 解决时间 |
|---|---|---|
| 麦克风通过 ROS2 Topic 还是直接 ALSA 采集 | 选择 Mic Adapter | 第一次 SSH |
| 麦克风采样率 / 位深 / 声道 / 帧大小 | 输入 Yandex | 第一次 SSH |
| `PcmAudioFrame.msg` 的真实字段 | 输出给 speaker | 第一次 SSH |
| `/dialog/status` 的实际 QoS | 完整兼容 | 第一次 SSH |
| 原节点真正的节点名 / namespace | 兼容部署 | 第一次 SSH |
| 原节点由哪个 systemd / launch 启动 | 停旧启新 | 第一次 SSH |
| 当前模型准确 ID | 基线与成本对比 | 第一次 SSH / 厂家 |
| 当前是否输入摄像头/视觉给 Omni | 防止功能回退 | 第一次 SSH + 黑盒测试 |
| 当前 system prompt / persona 来源 | 保持回答风格 | 可获得则采集；否则重新定义 |
| 当前音色 / VAD / 打断行为 | 体验对齐 | 黑盒基线测试 |

原则：未知项必须通过 Adapter / 配置边界隔离，禁止在本地代码中猜死。

## 5. 推荐本地项目结构

```text
casbot-yandex-realtime/
├── AGENTS.md
├── docs/
│   ├── YANDEX_REALTIME_MIGRATION_PLAN.md
│   ├── YANDEX_REALTIME_VERIFIED.md
│   └── RUNTIME_SNAPSHOT.md
├── src/
│   └── <ROS2 package>
├── tests/
├── tools/
│   └── local_poc/
├── config/
│   └── example.env
└── README.md
```

## 6. 分阶段执行计划

### Phase 0 — 初始化与证据冻结

- [ ] 初始化本地 Git 仓库。
- [ ] 保存本计划。
- [ ] 创建 `AGENTS.md`。
- [ ] 创建 `.gitignore`。
- [ ] 创建 `config/example.env`。
- [ ] 确认任何真实 API Key 都不会进入 Git。
- [ ] 创建 `docs/YANDEX_REALTIME_VERIFIED.md`。
- [ ] 创建 `docs/RUNTIME_SNAPSHOT.md` 空模板。

**Gate 0：** 仅完成项目骨架与安全规则，不连接机器人。

### Phase 1 — 核验 Yandex 当前 Realtime 能力

只使用当前 Yandex 官方资料确认：

- [ ] Realtime WebSocket endpoint。
- [ ] 鉴权方式。
- [ ] model / agent / session 指定方式。
- [ ] 输入 PCM 格式、采样率、声道。
- [ ] 输出音频实时能力。
- [ ] transcription / response 事件。
- [ ] session update。
- [ ] VAD。
- [ ] 用户打断。
- [ ] assistant truncate/cancel。
- [ ] 心跳、超时、最大会话时长。
- [ ] 错误事件与限流。
- [ ] 俄语支持、voice/role。
- [ ] Preview / GA 状态与限制。

交付：`docs/YANDEX_REALTIME_VERIFIED.md`

**Gate 1：** 官方资料必须证明能形成满足项目需求的“实时语音输入 → 实时回答语音输出”链路。否则停止并报告，不能自动切换架构。

### Phase 2 — 本地 Yandex Realtime PoC

最小链路：

```text
本地麦克风 / WAV
      ↓
AudioInputAdapter
      ↓
YandexRealtimeClient
      ↓
AudioOutputAdapter
      ↓
本地扬声器 / WAV
```

建议模块：

```text
YandexRealtimeClient
AudioInputAdapter
AudioOutputAdapter
SessionController
EventParser
Metrics
```

必测：

- [ ] 连接和鉴权。
- [ ] 连续输入语音。
- [ ] 俄语回答文本。
- [ ] 回答语音。
- [ ] 多轮会话。
- [ ] 用户插话。
- [ ] 取消旧回答。
- [ ] 网络断开 / 重连。
- [ ] API Key 错误。
- [ ] 超时。
- [ ] 连接、首响应、首音、总延迟。

**Gate 2：** 本地 Realtime PoC 稳定通过后再接 ROS2。

### Phase 3 — ROS2 兼容节点骨架

设计：

```text
ROS2 Node
  ├── MicAdapter
  ├── YandexRealtimeClient
  ├── OutputAudioAdapter
  ├── DialogStateMachine
  └── RuntimeConfig
```

任务：

- [ ] 创建 ROS2 Python package。
- [ ] 实现 `/dialog/start_session`。
- [ ] 实现 `/dialog/stop_session`。
- [ ] 实现 `/dialog/text_input`。
- [ ] 实现 `/dialog/status`。
- [ ] 实现 `/dialog/text_result`。
- [ ] 实现 `/audio/dialog_flush`。
- [ ] 抽象 `/audio/dialog_play`，不猜 `PcmAudioFrame` 字段。
- [ ] 状态机。
- [ ] generation/session ID，抑制迟到事件。
- [ ] 网络 I/O 不阻塞 ROS2 executor。
- [ ] 参数全部配置化。
- [ ] 日志脱敏。
- [ ] 为机器人未知接口保留 Adapter。

**Gate 3：** ROS2 contract 与状态机可用 mock 测试验证；机器人专有字段允许保留明确 TODO。

### Phase 4 — 第一次集中只读 SSH 审计

原则：

- 只读；
- 不复制厂家源码；
- 不保存真实 Token；
- 不 restart；
- 不 kill；
- 不修改 systemd；
- 输出集中保存到 `docs/RUNTIME_SNAPSHOT.md` 或 `runtime_snapshot/`。

采集：

```bash
ros2 node list
ros2 topic list -t
ros2 service list -t
ros2 node info <actual_node>
ros2 param list <actual_node>
ros2 param dump <actual_node>

ros2 interface show lingze_msgs/msg/PcmAudioFrame
ros2 topic info -v /audio/dialog_play
ros2 topic info -v /dialog/status

arecord -l
arecord -L

ps -ef | grep -E "realtime_dialog|ros2|launch" | grep -v grep
systemctl list-units --type=service --all | grep -Ei "lingze|dialog|bringup|ros"
```

定位实际 service 后：

```bash
systemctl cat <actual_service>
systemctl show <actual_service>   -p FragmentPath   -p DropInPaths   -p ExecStart   -p WorkingDirectory   -p EnvironmentFiles
```

同时记录原系统黑盒基线：首音延迟、打断、俄语回答、状态序列、speaker/嘴型、是否有视觉依赖。

**Gate 4：** 必须拿到麦克风入口、音频格式、`PcmAudioFrame`、QoS、服务停启方式和真实外部契约。

### Phase 5 — 机器人适配

- [ ] MicAdapter 接实际麦克风。
- [ ] 需要时 resample。
- [ ] Yandex 输出转实际 `PcmAudioFrame`。
- [ ] 对齐 QoS。
- [ ] 对齐状态序列。
- [ ] 对齐 stop / flush。
- [ ] 对齐 namespace。
- [ ] 确认 package / executable / launch 最终名称。
- [ ] systemd 部署模板。
- [ ] 回滚脚本。

**Gate 5：** 所有机器人差异均已参数化，无猜测硬编码。

### Phase 6 — 测试

单元测试：

- [ ] event parser
- [ ] session 状态机
- [ ] cancel / generation ID
- [ ] 音频分片
- [ ] PCM 转换
- [ ] 配置校验
- [ ] 错误映射
- [ ] 日志脱敏

集成测试：

- [ ] fake Yandex server
- [ ] fake MicAdapter
- [ ] fake audio output
- [ ] text input
- [ ] start/stop
- [ ] interruption
- [ ] timeout
- [ ] disconnect/reconnect
- [ ] stale event suppression

### Phase 7 — 部署设计

第一版不得删除或覆盖厂家原节点。

推荐：

```text
厂家原服务（保留，可回滚）
+
独立 Yandex workspace / package
+
独立 Yandex systemd service
```

切换：

```text
停止厂家对话节点
↓
确认退出
↓
启动 Yandex 节点
↓
验收
```

上传：

```text
本地 Git
↓
本地测试
↓
rsync --dry-run
↓
上传独立目录
↓
机器人构建
↓
启动新 service
```

首次禁止：

```text
rsync --delete
rm -rf
覆盖 /lingze/src
覆盖 /lingze/install
```

### Phase 8 — 正式部署与回滚

部署：

- [ ] 维护窗口。
- [ ] 远程恢复路径。
- [ ] 上传代码。
- [ ] 最小依赖。
- [ ] 构建。
- [ ] 停原对话服务。
- [ ] 启 Yandex 服务。
- [ ] ROS graph 检查。
- [ ] 真人语音测试。
- [ ] 日志检查。
- [ ] 确认千问不再新增请求。
- [ ] 检查 Yandex usage。

回滚：

```text
停止 Yandex
↓
启动厂家原对话服务
↓
确认 ROS2 接口恢复
↓
确认直接语音对话恢复
```

## 7. 验收标准

### 功能

- [ ] 人可直接对机器人讲话。
- [ ] 机器人通过 Yandex 回答。
- [ ] `speaker_node` 正常播放。
- [ ] 嘴型正常。
- [ ] Web / 上层业务无明显回归。
- [ ] stop / interruption 有效。
- [ ] `/dialog/text_result`、`/dialog/status` 正常。

### 成本

- [ ] 原 Qwen Realtime 不再产生机器人新用量。
- [ ] Yandex usage 可追踪。
- [ ] 可以按相同会话样本比较成本。

### 性能

最终阈值必须基于原机器人基线后冻结。

至少记录：

```text
connect latency
time to first server response
time to first transcript/token
time to first playable audio
end-to-end response latency
interruption stop latency
```

### 稳定性

至少：

- 100 轮连续对话；
- 2 小时连续运行；
- 断网/恢复；
- API 超时；
- 连续打断；
- 服务重启。

## 8. 安全要求

- API Key 不进入 Git。
- 不硬编码密钥。
- 不打印 Authorization。
- `.env` 必须忽略。
- 正式部署优先 systemd EnvironmentFile 或现有秘密配置机制。
- 默认不记录完整真实用户语音/文本。
- runtime snapshot 如含密钥必须脱敏。
- 不使用未验证的第三方 Yandex API 示例作为协议依据。

## 9. Codex 工作规则

Codex 每次任务必须：

1. 读取 `AGENTS.md`。
2. 读取本计划。
3. 查看 Git 状态。
4. 找到当前 Phase。
5. 只完成当前 Phase / 用户指定任务。
6. 不跨 Gate 自动继续。
7. 不猜机器人专有参数。
8. 运行相关测试。
9. 仅在有证据时更新 checklist / 状态。
10. 最终报告：改动、测试、已验证事实、未知项、当前 Gate、下一步。

## 10. 当前状态

```text
Current Phase: Phase 0 → Phase 1
Remote robot changes: NONE
Remote SSH required now: NO
Vendor source available: NO
Yandex production protocol verified: NOT YET
Local Yandex PoC: NOT STARTED
ROS2 compatibility node: NOT STARTED
Robot runtime snapshot: NOT COLLECTED
Deployment: NOT STARTED
```

### 当前唯一允许执行的下一步

```text
Phase 0 初始化项目
+
Phase 1 核验 Yandex 当前官方 Realtime API
```

在 Gate 1 通过之前，不写机器人正式 ROS2 集成代码。

## 11. Decision Log

- **D-001**：厂家源码不开放，因此采用“兼容节点替换”而不是“修改厂家 Provider”。
- **D-002**：当前架构不是独立 ASR / LLM / TTS，优先按一体化 Realtime → Yandex Realtime 迁移。
- **D-003**：机器人外围模块保持不变；`speaker_node` 仍为最终音频播放和嘴型联动入口。
- **D-004**：SSH 集中到 Phase 4 和 Phase 8，前期尽量全部本地完成。
- **D-005**：机器人专有未知信息通过 Adapter / 配置隔离，不在代码中推测。
- **D-006**：Yandex Realtime 是快速变化接口；必须先核验当前官方协议，禁止沿用旧事件或旧 endpoint。
