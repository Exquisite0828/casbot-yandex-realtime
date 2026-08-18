# CASBOT → Yandex Realtime 迁移实施计划

> 版本：v1.0  
> 状态：Phase 7 COMPLETE；Gate 7 CONDITIONAL PASS；Phase 8 IN PROGRESS；Phase 8H half-duplex mitigation FIELD PASS；Phase 8I 本地 COMPLETE / CONDITIONAL PASS，机器人同步、systemd enable 与 cold-boot acceptance 待执行
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

## 4. Phase 4 后的运行时事实与未知项

| 项目 | 状态 | 结论 / 后续用途 |
|---|---|---|
| 麦克风入口 | VERIFIED | `dialog_node` 直接持有 ALSA `/dev/snd/pcmC0D0c`；未观察到 ROS2 mic 输入订阅 |
| 麦克风 PCM | VERIFIED | MMAP_INTERLEAVED、S16_LE、mono、16 kHz、period 1024、buffer 16384 |
| `PcmAudioFrame` 字段 | VERIFIED | `stamp`、`sample_rate`、`channels`、`format`、`data` |
| `PcmAudioFrame.format` 实际值 | DEFERRED / CONDITIONAL | 留待真实 vendor runtime 验证，禁止猜字符串 |
| dialog play/flush/status/text QoS | VERIFIED | Reliability/Durability 已冻结；CLI 的 history depth 为 UNKNOWN，精确值 NOT COLLECTED |
| 实际节点 / namespace | VERIFIED | `/lzdl10823/dialog_node`，namespace `/lzdl10823` |
| systemd / launch 入口 | VERIFIED | `lingze_robot.service → /lingze/bin/start_robot.sh`；进程树观察到 `ros2 launch bringup bringup.launch.py` |
| ROS2 视觉输入 | NOT OBSERVED | `dialog_node` 无 camera/image subscriber；不证明闭源进程未通过非 ROS2 路径访问摄像头 |
| speaker 输入转换 / 重采样 | NOT OBSERVED / DEFERRED / CONDITIONAL | 播放硬件为 48 kHz stereo 不证明发布端格式；留待后续实机验证 |
| 当前云端模型准确 ID | NOT COLLECTED | `lingze_omni_s2s` 仅是运行时包名；Qwen 具体型号仍是工作假设 |
| 当前 system prompt / persona | NOT COLLECTED / DEFERRED | 可获得则采集，否则重新定义并在验收中确认 |
| 原系统真人黑盒体验基线 | NOT COLLECTED / DEFERRED | 用户决定推迟到集成/验收，不阻塞 Gate 4 |

原则：已验证事实用于 Phase 5 Adapter；未观察或推迟项继续通过 Adapter / 配置边界隔离，禁止猜测硬编码。

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

- [x] 初始化本地 Git 仓库。
- [x] 保存本计划。
- [x] 创建 `AGENTS.md`。
- [x] 创建 `.gitignore`。
- [x] 创建 `config/example.env`。
- [x] 确认任何真实 API Key 都不会进入 Git。
- [x] 创建 `docs/YANDEX_REALTIME_VERIFIED.md`。
- [x] 创建 `docs/RUNTIME_SNAPSHOT.md` 空模板。

**Gate 0：** 仅完成项目骨架与安全规则，不连接机器人。

### Phase 1 — 核验 Yandex 当前 Realtime 能力

只使用当前 Yandex 官方资料确认：

- [x] Realtime WebSocket endpoint — 当前为 `wss://ai.api.cloud.yandex.net/v1/realtime`；旧 endpoint 已停止支持。
- [x] 鉴权方式 — API Key / IAM token、`ai.models.user` 与 API key scope 已核验。
- [x] model / agent / session 指定方式 — model URI 通过 URL query；当前无独立 agent 参数；行为通过 session 配置。
- [x] 输入 PCM 格式、采样率、声道 — `audio/pcm` + Base64 raw bytes；官方 helper 使用 mono int16；完整 rate 范围和 Realtime endian 未直接公布，留给 Phase 2。
- [x] 输出音频实时能力 — 当前官方教程/示例使用 `response.output_audio.delta`；Reference 的 unsupported 标记冲突已记录并留给 Phase 2 实测。
- [x] transcription / response 事件 — final input transcription、response lifecycle、text/audio delta 及 Reference 限制已核验。
- [x] session update — `session.update` patch 与 `session.updated` 回执已核验。
- [x] VAD — `server_vad`、`threshold`、`silence_duration_ms`、speech started/stopped 和 manual turn 已核验。
- [x] 用户打断 — `speech_started` 与官方示例本地清播放队列行为已核验。
- [x] assistant truncate/cancel — `response.cancel`、`conversation.item.truncate` 字段和完成事件已核验。
- [x] 心跳、超时、最大会话时长 — 官方未公开 maximum/idle 数值或强制 heartbeat；结论为 `NOT DOCUMENTED`，运行时验证推迟到 Phase 2。
- [x] 错误事件与限流 — error schema、通用 HTTP 状态、默认 10 并发 session / 10 次每秒建连 quota、`rate_limits.updated` 不支持已核验。
- [x] 俄语支持、voice/role — 250923 明确面向 Russian/Kazakh；Realtime 兼容 SpeechKit voices/roles；260528 俄语效果作为 Phase 2 条件。
- [x] Preview / GA 状态与限制 — 最新明确状态仍为 Preview，未找到后续 GA announcement；2026 协议迁移和文档冲突已记录。

交付：`docs/YANDEX_REALTIME_VERIFIED.md`

**Gate 1（2026-08-10）：CONDITIONAL PASS。** 官方资料已证明能形成满足项目需求的“实时语音输入 → 模型理解/生成 → 增量回答语音与文本输出”单条 Route A，不需要自动切换架构。条件是 Phase 2 必须验证 260528 握手、实际 audio delta、PCM 字节契约、cancel/truncate 打断一致性、俄语、长连接和 Billing；260528 失败先回退 250923，两者核心实时音频均失败则停止并重审 Gate。详见 `docs/YANDEX_REALTIME_VERIFIED.md`。

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

- [x] 连接和鉴权。
- [x] 连续输入语音。
- [x] 俄语回答文本。
- [x] 回答语音。
- [x] 多轮会话。
- [x] 用户插话（实际观察到本地停止、truncate 和后续新回答；用户随后取消其 Gate 要求）。
- [ ] 取消生成中的旧回答（决策逻辑已有单元测试；用户明确取消本轮 live 验证要求）。
- [ ] 网络断开 / 重连。
- [ ] API Key 错误。
- [x] 超时（发现并移除会导致静默期客户端 1006 的可选 aiohttp heartbeat；75 秒复验通过原断点）。
- [x] 连接、首响应、首音、总延迟（仅记录观察值，不作为性能基准）。

**Gate 2（2026-08-10）：PASS。** `speech-realtime-260528` 已通过当前 endpoint 建立真实 session，并以本地 24 kHz PCM16 mono 麦克风完成俄语转写、多轮回答、增量音频返回与本地扬声器输出；未使用 fallback。一次播放中插话实际完成本地停止、truncate 与新回答。用户随后明确取消“生成中 `response.cancel` 必须 live 验证”的本轮要求，因此该项不阻塞 Gate。网络重连、错误凭据和系统性稳定性测试仍留在后续阶段。证据见 `docs/YANDEX_REALTIME_LOCAL_POC.md`。

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

- [x] 创建 ROS2 Python package。
- [x] 实现 `/dialog/start_session`。
- [x] 实现 `/dialog/stop_session`。
- [x] 实现 `/dialog/text_input`。
- [x] 实现 `/dialog/status`。
- [x] 实现 `/dialog/text_result`。
- [x] 实现 `/audio/dialog_flush`。
- [x] 抽象 `/audio/dialog_play`，不猜 `PcmAudioFrame` 字段。
- [x] 状态机。
- [x] generation/session ID，抑制迟到事件。
- [x] 网络 I/O 不阻塞 ROS2 executor。
- [x] 参数全部配置化。
- [x] 日志脱敏。
- [x] 为机器人未知接口保留 Adapter。

**Gate 3（2026-08-10）：CONDITIONAL PASS。** `realtime_dialog` ament_python package、厂家公开 Service/Topic wrapper、后台 asyncio Yandex client、状态机、generation/stale-event suppression 和机器人音频 Adapter 均已完成，Phase 2 与 Phase 3 共 31 项 pure Python/mock 单测通过。当前 macOS 环境没有 ROS2 Humble/rclpy，因此未实际启动节点；wrapper 已做语法/静态导入检查。`/audio/dialog_play` publisher、麦克风来源、真实音频格式、实机 QoS 与 namespace 继续等待 Phase 4 只读审计，未作猜测。详见 `docs/ROS2_COMPATIBILITY_SKELETON.md`。

### Phase 4 — 第一次集中只读 SSH 审计

只读审计结果：

- [x] 保持只读；未 restart、stop、kill、上传、安装或修改 systemd。
- [x] 取得实际 node、namespace、Topic、Service 与 consumer contract。
- [x] 取得 dialog play、dialog flush、status、text result、input waveform、session active QoS。
- [x] 取得麦克风入口：`dialog_node` 直接 ALSA capture。
- [x] 取得输入 PCM：S16_LE、mono、16 kHz、period/buffer 参数。
- [x] 取得 `PcmAudioFrame` schema；实际 `format` 值保持 DEFERRED。
- [x] 取得 speaker 节点、播放设备、speaker-active 与嘴型链路。
- [x] 取得 `lingze_robot.service` 与观测到的 ROS launch 入口。
- [x] 检查 ROS2 视觉订阅：`dialog_node` 未观察到 camera/image subscription。
- [x] 将永久结论和证据 caveat 写入 `docs/RUNTIME_SNAPSHOT.md`。
- [ ] 原系统真人黑盒 baseline：用户决定推迟到集成/验收；不阻塞 Gate 4。

**Gate 4（2026-08-11）：PASS。** 麦克风入口、实际输入 PCM、`PcmAudioFrame` schema、实际 QoS、node/namespace、service/startup 方法和 ROS2 外部契约均已由只读运行时证据覆盖。`PcmAudioFrame.format`、speaker 转换行为及 16 → 24 kHz 策略属于 Phase 5 适配决策；真人黑盒 baseline 经用户明确推迟，不作为本 Gate 阻塞项。Phase 4 原始证据仅保存在 Git ignored 的 `runtime_snapshot/raw/`，永久结论见 `docs/RUNTIME_SNAPSHOT.md`。

### Phase 5 — 机器人适配

- [x] 实现可配置 `ArecordMicAdapter` 生产路径；实际机器人 device string / executable 仍待集成验证。
- [x] 实现无第三方依赖的状态化 16 kHz → 24 kHz PCM16 mono 重采样与 20 ms 分片。
- [x] microphone uplink 改为有界 drop-oldest queue + 单一异步 sender。
- [x] Yandex input/output sample rate 拆分，默认均为 24 kHz。
- [x] 实现带 generation/playback epoch 的有界机器人输出队列和真实 `PcmAudioFrame` factory。
- [x] `speaker_pcm_format` 保持 required 空默认；未猜测真实字符串。
- [x] 对齐已验证 Reliability/Durability；history depth 明确为项目 buffer policy。
- [x] 实现 `session_active` 项目兼容语义；未冒充厂家精确时序。
- [x] stop / text replacement / speech_started 均先本地 flush，再等待网络 cancel/close。
- [x] stop 的 flush enqueue 位于 arecord shutdown 前；cancel 失败仍 mandatory close，错误结果保持可观察。
- [x] arecord runtime error 通过线程安全、generation/capture-token guard 进入 Controller；主动 stop 与 stale callback 不误报。
- [x] ROS shutdown 在 destroy 前显式 enqueue/drain 最终 flush；flush 作为跨 generation barrier 保留。
- [x] ROS 名称相对化；新增可覆盖 namespace/node name 的 CASBOT launch profile。
- [x] 声明 `lingze_msgs` 正式依赖并安装 launch/config 示例。
- [x] Phase 8C 已在 ROS2 Humble + vendor overlay 实机完成 build 和安装产物验证；Yandex launch/runtime 未开始。
- [x] Phase 8 实机确认厂家输出 `PcmAudioFrame` 为 `24000 / 1 / pcm_s16le` 且 speaker 实际出声。
- [ ] 实机确认 speaker 内部转换、`hw:0,0` 实际打开、嘴型、flush 与 `session_active` 精确时序。

**Gate 5（2026-08-13）：CONDITIONAL PASS。** Phase 5 本地机器人接口适配与生命周期修复已完成，68 项 core/mock tests 和 10 项 Phase 2 PoC regression 通过。该 Gate 当时保留的真实环境条件现由 Phase 8 分项验证；build/install、真实消息 import、厂家 output metadata 和 speaker 实际出声已有后续证据，Yandex launch/runtime、speaker 内部转换、真实 arecord 打开、嘴型/shutdown flush 和厂家 `session_active` 精确时序仍为 **UNKNOWN / DEFERRED / CONDITIONAL**。此结论不表示机器人已经接入 Yandex。

### Phase 6 — 测试（COMPLETE）

单元测试：

- [x] event parser
- [x] session 状态机
- [x] cancel / generation ID
- [x] 音频分片
- [x] PCM 转换
- [x] 配置校验
- [x] 错误映射
- [x] 日志脱敏

集成测试：

- [x] fake Yandex server（真实 localhost aiohttp WebSocket）
- [x] fake MicAdapter
- [x] fake audio output
- [x] text input
- [x] start/stop
- [x] interruption
- [x] timeout
- [x] disconnect / explicit reconnect
- [x] stale event suppression

**Gate 6（2026-08-14）：PASS。** 本地系统测试实际经过 aiohttp loopback TCP/WebSocket handshake 和 JSON 收发；103 项 core/integration tests 与 10 项 Phase 2 PoC regression 通过。unexpected transport/session failure 会登记单一 fatal cleanup，并与 stop/start/text/interruption lifecycle 串行；cleanup 失效 generation、flush 本地输出、停止 microphone/sender、清空有界队列、关闭 WebSocket/receive task/ClientSession 并进入 `STATUS_ERROR`。response-generation map 在 terminal done/close 时释放，stale connection 在 normalization 前由 token 拒绝；connector/receive/send/ws.close/session.close 的假凭据错误路径均有端到端脱敏证明。只允许由新的 start 或 text-input 命令显式恢复，不存在自动重连。生产 endpoint 校验未放宽，localhost 只能通过非配置化的构造器 test connector 使用。此 PASS 仅覆盖本地软件测试，不新增 ROS2、Yandex live 或机器人运行事实；Gate 5 保留的真实环境未知项不是 Gate 6 阻塞项，继续留待后续集成验证。

### Phase 7 — 部署设计

**Phase 7 收口历史状态：COMPLETE。Gate 7 为 CONDITIONAL PASS；当时 Phase 8 未开始。**

用户提供的只读 SSH 记录补充确认：当前模式为 `jijia`，厂家
`/lzdl10823/dialog_node` 由 `jijia.launch.py` 直接作为并列启动项拉起；
`lingze_robot.service` 管理包含 speaker、Web、运动等模块的整个 ROS2 control
group，不能作为长期只停 dialog 的开关。当前系统 Python 没有 `aiohttp`，但
Python 3.10.12、colcon、rclpy、`lingze_msgs.msg.PcmAudioFrame` 和 arecord 可用。
这些是用户提供的只读证据，本 Phase 没有重新 SSH 或修改机器人。

部署主路线冻结为：

```text
lingze_robot.service（保留 speaker/Web/运动等厂家模块）
└── jijia.launch.py
    └── external-dialog marker 存在时跳过厂家 dialog

casbot-yandex-dialog.service（独立；首轮 Restart=no）
└── /opt/casbot-yandex-realtime 独立 workspace/install/venv
```

部署状态严格区分：

```text
VENDOR_MODE: vendor ON, Yandex OFF, marker absent
TRANSITION:  vendor OFF, Yandex OFF, marker present
YANDEX_MODE: vendor OFF, Yandex ON, marker present
禁止状态:    vendor ON, Yandex ON
```

本轮本地交付包括：

- 默认 dry-run、可按 semantic anchor 校验且 byte-preserved restore 的 vendor gate；
- 独立 systemd、配置、凭据和 `venv --system-site-packages` 模板；
- build/service/switch/rollback preflight 与 service/MainPID/PID-set/graph 三态 verify；
- `user_config.json` 在 service/switch/rollback/verify/launch 中 fail closed；正式切换
  和 Yandex 启动强制 `robot_current_mode=jijia`，厂家恢复只接受两个已知 backend；
- 共享非阻塞事务锁、有限 switch、一次自动 rollback、幂等正常 rollback；
- wrapper 直接 exec 安装后的节点，使 Yandex PID 与 systemd MainPID 可精确校验；
- 只输出 `sample_rate/channels/format` 的 metadata probe；
- fake-root / fake runner 部署测试和永久设计文档。

常规回滚只删除 marker 并恢复 `VENDOR_MODE`；完整卸载才校验 manifest/SHA 后
restore 厂家 launch 原始 bytes。第一版不接入厂家 `current_llm` 或 Web 模型切换，
不删除厂家 executable，不覆盖 `/lingze/install`，也不启用无限自动重启。

**Gate 7（2026-08-14）：CONDITIONAL PASS。** Phase 7 本地部署设计、控制工具、
fail-closed guard、fake-root/fake-runner 测试和安全边界已完成复审。此结论不证明
任何机器人或真实云端行为；以下条件明确保留为单独授权的 Phase 8 实机验证项，
不得写成已验证事实：

- `PcmAudioFrame.format`；
- 厂家实际发布 rate/channels；
- speaker 输入兼容性；
- `hw:0,0` 实际采集；
- speaker / 嘴型 / flush；
- `session_active` 精确时序；
- 真实 Yandex 网络和凭据；
- 正式 switch / rollback。

其中任何必需值仍需猜测时不得切换。完整设计见
`docs/PHASE7_DEPLOYMENT_DESIGN.md`，操作边界见 `deploy/README.md`。

### Phase 8 — 正式部署与回滚

**当前状态：IN PROGRESS。Phase 8C COMPLETE；Phase 8D CONFIG/CREDENTIAL
PREPARATION COMPLETE；Phase 8E SYSTEMD UNIT INSTALLED、DISABLED/INACTIVE；
Phase 8F FIRST SWITCH ATTEMPT FAILED、VENDOR MODE RESTORED，后续第二次受控切换成功；
Phase 8H half-duplex mitigation FIELD PASS；Phase 8I 本地实现 COMPLETE /
CONDITIONAL PASS，机器人同步、systemd enable 与 cold-boot acceptance 均未执行。**

本轮任务提供的实机记录证明 Phase 8C 已完成固定源码上传、独立 venv、aiohttp
隔离、ROS2 build 和安装产物验证。机器人缺少 ensurepip 且 apt 无可用 Candidate，
因此采用 `venv --without-pip --system-site-packages` 加
`pip --target <venv purelib>`；venv 的 `rclpy`、`lingze_msgs`、`aiohttp` 路径和系统
Python 未被 aiohttp 污染均已核对，build preflight 已 PASS。

厂家首个 `/audio/dialog_play` frame 为 `24000 / 1 / pcm_s16le`，speaker 实际出声；
嘴型和 speaker 内部转换未确认。Phase 8D/E 已准备生产 YAML、root-owned mode-0600
真实 env（值不进入仓库），安装 disabled/inactive systemd unit，并安全 PATCH vendor
gate；marker 当前 absent。switch preflight 与机器人到真实 Yandex WebSocket/session
探测 PASS，但后者不证明 Yandex ROS2 节点已经接管机器人。

第一次正式 switch 在 Yandex service start 之前的 transition verify 失败，Yandex
journal 无记录。旧 automatic rollback 的立即 vendor-mode verify 失败；稍后 marker
absent、vendor service active、Yandex service inactive，完整 vendor-mode verify
PASS，机器人安全恢复厂家模式。旧版本未保留 transition 失败报告，具体失败项仍为
**UNKNOWN**。readiness/settling race 是最强推断，不是已证实根因；厂家 watchdog/
反第三方机制没有被证明存在或不存在。

Phase 8F 本地修复已统一 bounded readiness polling、deadline/probe timeout、连续稳定
PASS、transient/hard 分类、last report 和 final-state-aware 恢复指引。该修复完成
时尚未同步机器人；用户提供的后续现场事实确认第二次受控 switch 成功，Yandex
Realtime、机器人 microphone、兼容 ROS2 node 与 speaker 回复链路曾真实运行。

后续用户提供的现场事实确认机器人曾成功建立 Yandex 模式，真人俄语理解和回复
正常且主观响应很快；随后观察到机器人连续自己说话。speaker 输出被持续运行的
本机 microphone 回采并重新上传是当前主要推断，尚未实机严格证明。Phase 8H 在
controller 入队端和 sender 发送前增加 half-duplex microphone suppression，并在
`RESPONSE_DONE` 后采用 500 ms monotonic guard；500 ms 是项目调优策略，不是厂家或
Yandex 事实。用户提供的现场证据确认机器人实际加载
`barge_in_enabled=false`/`microphone_resume_guard_ms=500`，复测未再出现自问自答，
响应正常且回答后可继续提问，因此 mitigation 为 `FIELD PASS`；这仍不证明物理 AEC
根因或唯一 acoustic-feedback 根因。

Phase 8I 本地增加 generic-default-false、机器人 profile 为 true 的一次性非阻塞
`auto_start_session`；fatal cleanup 的最终组合错误经统一脱敏后进入 ROS logger /
systemd journal；service ExecStartPre 复用 Phase 8F readiness policy，以 60 秒 overall、
5 秒 probe 和 0.5 秒 poll 有界等待 transient ROS/speaker/microphone settling。一次性
service preflight、`Restart=no`、half-duplex、外部 ROS2 契约和 switch/rollback 语义
保持不变，没有 runtime auto-reconnect。该实现尚未同步机器人，unit 未 enable，冷启动
无人干预验收未执行，不得宣称机器人已默认开机使用 Yandex。

部署：

- [x] 第一次 switch 维护窗口（尝试失败并恢复厂家模式）。
- [ ] 远程恢复路径。
- [x] 上传固定源码并完成 Phase 8C build/preflight。
- [x] 独立 venv 与最小 aiohttp 隔离。
- [x] ROS2 build 和安装产物验证。
- [x] 生产配置/凭据文件和 disabled/inactive unit 准备。
- [x] vendor gate PATCHED；恢复后 marker absent。
- [x] 真实机器人到 Yandex WebSocket/session 探测。
- [x] 同步 Phase 8F readiness repair 后完成后续受控 switch（用户提供现场事实）。
- [x] 第二次受控 switch 成功建立 Yandex 模式。
- [x] 后续实机曾成功建立 Yandex 模式并运行 Yandex 服务（用户提供现场事实）。
- [x] 兼容 ROS2 node、机器人 microphone 和 speaker 回复链路曾真实运行。
- [x] 真人俄语理解与回复曾成功；随后连续自言自语，完整验收仍未完成。
- [ ] 日志检查。
- [ ] 确认千问不再新增请求。
- [ ] 检查 Yandex usage。
- [ ] 同步 Phase 8I 固定 commit，更新真实 YAML、重新 colcon build 并安装新版 unit。
- [ ] 单独授权 daemon-reload / enable 后执行整机冷启动无人干预验收。

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
Current Phase: Phase 8 — IN PROGRESS
Gate 1: CONDITIONAL PASS
Gate 2: PASS
Gate 3: CONDITIONAL PASS
Gate 4: PASS
Gate 5: CONDITIONAL PASS
Phase 6: COMPLETE
Gate 6: PASS
Phase 7: COMPLETE
Gate 7: CONDITIONAL PASS
Phase 8: IN PROGRESS
Phase 8C: COMPLETE
Phase 8D: CONFIG/CREDENTIAL PREPARATION COMPLETE
Phase 8E: SYSTEMD UNIT INSTALLED; DISABLED/INACTIVE
Phase 8F: FIRST SWITCH ATTEMPT FAILED; VENDOR MODE RESTORED
Phase 8F repository repair: COMPLETE LOCALLY; LATER YANDEX MODE OBSERVED
Phase 8H half-duplex mitigation: FIELD PASS
Phase 8I local implementation: COMPLETE / CONDITIONAL PASS
Phase 8I robot synchronization: PENDING
systemd enable: NOT RUN
cold-boot acceptance: NOT RUN
Robot Yandex voice conversation: PASS; HALF-DUPLEX RETEST PASSED
Formal switch: YANDEX MODE ESTABLISHED ONCE; ACCEPTANCE NOT COMPLETE
Formal rollback: NOT STARTED
Remote robot changes in this repair: NONE
Remote SSH required now: NO
Vendor source available: NO
Yandex production protocol verified: LOCAL ROUTE A CORE PATH VERIFIED WITH speech-realtime-260528
Local Yandex PoC: COMPLETE
ROS2 compatibility node: ROBOT BUILD/INSTALL VERIFIED; YANDEX VOICE PATH OBSERVED
Robot runtime snapshot: PHASE 4 EVIDENCE FROZEN; PHASE 8 FIELD EVIDENCE RECORDED
Robot adaptation: PHASE 5 COMPLETE; LOCAL ADAPTER IMPLEMENTATION COMPLETE
Systematic testing: PHASE 6 COMPLETE; GATE 6 PASS
Deployment design: PHASE 7 COMPLETE; LOCAL CONTROL PLANE IMPLEMENTED
Robot deployment: YANDEX MODE ESTABLISHED; PHASE 8I SYNC/ENABLE/COLD BOOT PENDING
```

### 当前阶段边界

```text
Phase 6 — COMPLETE
Gate 6 — PASS
Phase 7 — COMPLETE
Gate 7 — CONDITIONAL PASS
Phase 8 — IN PROGRESS
Phase 8C — COMPLETE
Phase 8D — CONFIG/CREDENTIAL PREPARATION COMPLETE
Phase 8E — SYSTEMD UNIT INSTALLED; DISABLED/INACTIVE
Phase 8F — FIRST SWITCH ATTEMPT FAILED; VENDOR MODE RESTORED
Phase 8F repository repair — COMPLETE LOCALLY; LATER YANDEX MODE OBSERVED
Phase 8H half-duplex mitigation — FIELD PASS
Phase 8I local implementation — COMPLETE / CONDITIONAL PASS
Robot synchronization — PENDING
systemd enable — NOT RUN
cold-boot acceptance — NOT RUN
Robot Yandex voice conversation — PASS; HALF-DUPLEX RETEST PASSED
Formal switch — YANDEX MODE ESTABLISHED ONCE; ACCEPTANCE NOT COMPLETE
Formal rollback — NOT STARTED
```

Phase 6 本地系统测试与 Gate 修复已完成并通过复审，Gate 6 正式为
`PASS`。Phase 7 本地部署设计和控制工具已经完成复审，Gate 7 为
`CONDITIONAL PASS`。Phase 8C–8E 已完成 build/preflight、配置/凭据准备和
disabled/inactive unit 安装。Phase 8F 第一次 switch 在 transition 阶段失败并最终恢复
厂家模式；后续第二次受控切换成功，Yandex 模式和真人俄语对话曾真实运行。连续
自言自语观察后，Phase 8H half-duplex 参数实际加载并通过现场复测。Phase 8I 只在
本地完成，尚未同步、enable 或执行冷启动无人干预验收。正式正常 rollback 和 Gate 8
均未完成或判定，不得自动继续实机操作。

## 11. Decision Log

- **D-001**：厂家源码不开放，因此采用“兼容节点替换”而不是“修改厂家 Provider”。
- **D-002**：当前架构不是独立 ASR / LLM / TTS，优先按一体化 Realtime → Yandex Realtime 迁移。
- **D-003**：机器人外围模块保持不变；`speaker_node` 仍为最终音频播放和嘴型联动入口。
- **D-004**：SSH 集中到 Phase 4 和 Phase 8，前期尽量全部本地完成。
- **D-005**：机器人专有未知信息通过 Adapter / 配置隔离，不在代码中推测。
- **D-006**：Yandex Realtime 是快速变化接口；必须先核验当前官方协议，禁止沿用旧事件或旧 endpoint。
- **D-007**：以 2026 当前 Realtime API 和 `wss://ai.api.cloud.yandex.net/v1/realtime` 为唯一实现基线；2025 旧 endpoint / interaction format 禁止作为实现依据。
- **D-008**：`speech-realtime-260528` 作为条件性 primary，`speech-realtime-250923` 作为 Route A fallback；260528 必须先通过 Phase 2 握手、俄语和事件流验证。
- **D-009**：Gate 1 为 `CONDITIONAL PASS`。官方教程与 Reference 对 260528 和 output delta 存在同步冲突，PCM 字节级契约及会话时限也未完全公开；所有条件均推迟到 Phase 2 实测，失败时不自动切换 Route B。
- **D-010**：Phase 2 使用 `speech-realtime-260528` 实际跑通当前 endpoint、24 kHz PCM16 mono 麦克风、俄语多轮、增量回答音频和本地播放。用户明确取消“生成中 `response.cancel` 必须 live 验证”的本轮要求；播放中本地停止和 truncate 已实际观察。Gate 2 为 `PASS`，Phase 3 未开始。
- **D-011**：Phase 3 使用纯 Python core + 薄 ROS2 wrapper：所有 Yandex 网络 I/O 在独立 asyncio worker 执行，ROS 回程经线程安全队列；机器人麦克风和 `PcmAudioFrame` 输出保持 Adapter/TODO。因本机无 ROS2 Humble，mock/core 测试通过后 Gate 3 为 `CONDITIONAL PASS`，Phase 4 未开始。
- **D-012**：实机实际 dialog node 为 `/lzdl10823/dialog_node`，namespace 为 `/lzdl10823`，runtime package/executable 为 `lingze_omni_s2s/dialog_node`；包名不证明准确云端模型 ID。
- **D-013**：当前 `dialog_node` 直接通过 ALSA `/dev/snd/pcmC0D0c` 采集 S16_LE、mono、16 kHz 音频；未观察到 ROS2 microphone input subscription。
- **D-014**：`PcmAudioFrame` schema 与 dialog play/dialog flush/status/text result QoS 已通过运行时证据冻结；`format` 实际值和 speaker conversion behavior 仍 DEFERRED，禁止猜测。
- **D-015**：`/lzdl10823/dialog/session_active` 在实机由 `dialog_node` 发布且有 `face_play_example` consumer，Phase 5 纳入兼容评估。
- **D-016**：原系统真人黑盒 baseline 经用户决定推迟到集成/验收，不作为 Gate 4 阻塞项；其余 Gate 4 核心证据齐备，因此 Gate 4 为 `PASS`，Phase 5 未开始。
- **D-017**：机器人已验证的 16 kHz S16_LE mono 输入在 Adapter 内使用无第三方依赖、跨 chunk 连续的线性重采样转换为 Yandex 24 kHz PCM16 mono，并统一重分片为 20 ms。
- **D-018**：Yandex 回答保持实际 24 kHz mono payload 发布，不根据 48 kHz stereo 物理硬件推断 speaker 输入契约，也不做未经验证的输出转换。
- **D-019**：`speaker_pcm_format` 为 required runtime configuration，空值 fail-fast；没有任何猜测默认值。
- **D-020**：核心 ROS 接口使用相对名称，CASBOT namespace 和 node name 由 launch profile 配置；`lzdl10823` 仅为可覆盖实例示例。
- **D-021**：`dialog/session_active` 采用 IDLE/ERROR=false、CONNECTING/LISTENING/SPEAKING=true 的 PROJECT COMPATIBILITY SEMANTIC，不宣称等同厂家精确时序。
- **D-022**：`dialog/input_waveform` 与 `system/config_update` 继续 DEFERRED；不创建收到后无行为的伪兼容接口。
- **D-023**：systemd 部署模板与正式回滚脚本归入 Phase 7，不属于 Phase 5 本地接口适配。
- **D-024**：Phase 5 本地机器人接口适配完成：ALSA microphone → stateful 16→24 kHz → Yandex 路径，以及 Yandex output → bounded audio queue → `PcmAudioFrame` 路径均已实现；stop/interruption/shutdown 生命周期问题已修复，68 项 core/mock tests 与 10 项 Phase 2 PoC regression 通过。Gate 5 为 `CONDITIONAL PASS`；ROS2 Humble/vendor overlay 的真实 build/launch、真实 `lingze_msgs` import、speaker/arecord 参数与实机播放、嘴型、shutdown flush、厂家 `session_active` 精确时序仍为 UNKNOWN / DEFERRED / CONDITIONAL，且已通过配置、Adapter 或 fail-fast 隔离而未猜测硬编码。此结论不表示机器人已经接入 Yandex，Phase 6 尚未开始。
- **D-025**：Phase 6 使用构造器专用 connector 将严格校验后的官方 URL 映射到本地动态端口，以真实 aiohttp WebSocket 测试 Controller、production client、事件 normalization 和有界 output。setup timeout/error/malformed、runtime error/invalid audio/disconnect/send fault、stop-after-dead、interruption/stale event、显式 start/text recovery 与 5 次生命周期均已覆盖；由此集中实现当前 generation 的 fatal cleanup，并以 connection token 拒绝旧连接回调。没有自动重连、真实 Yandex/ROS2/机器人操作；该轮形成 Gate 6 初始复审证据，最终结论见 D-027。
- **D-026**：Phase 6 Gate repair 统一以 lifecycle lock 串行 fatal cleanup、stop/start/text 与 interruption；fatal task 只登记一次，后续命令等待其完成，旧 generation callback 不能污染新 session。response-generation mapping 在 terminal done 后释放并在 close 时清空，stale connection token 在 normalization 前拒绝。Fake Yandex server 即使单个 WebSocket close 失败也继续关闭其余连接并 guaranteed runner cleanup。connector/receive/send/ws.close/session.close 五条假凭据故障路径连同异常链均已证明脱敏。103 项 core/integration tests 与 10 项 Phase 2 PoC regression 通过；该轮形成 Gate repair 复审证据，正式 Gate 状态见 D-027。
- **D-027**：Phase 6 本地系统测试与 Gate 修复经复审完成，Gate 6 正式为 `PASS`，Phase 7 为 `NOT STARTED`。真实 localhost aiohttp TCP/WebSocket 覆盖 production client、Controller、JSON 收发与事件 normalization；fake microphone、有界 audio output、正常语音/文本、故障与显式恢复、lifecycle races、1,000-response map 释放、五次资源生命周期和五条异常链凭据脱敏路径均已验证。最终证据为 103/103 普通 core/integration tests、103/103 `ResourceWarning` strict tests、10/10 Phase 2 PoC regression、6 个确定性 race tests 连续 20 轮、1,000-response 场景、5-cycle 资源场景、compileall 与 `git diff --check` 全部通过。生产 endpoint/schema 未放宽，不存在自动重连；未执行真实 Yandex、ROS2、SSH、机器人或部署操作。Gate 5 保留的 ROS2/vendor overlay、真实 `lingze_msgs` import、`PcmAudioFrame.format`、speaker/arecord 参数和实机行为继续为 **UNKNOWN / DEFERRED / CONDITIONAL**，均由配置、Adapter 或 fail-fast 隔离而未猜测硬编码；这些后续集成项不是 Gate 6 的本地软件阻塞项。
- **D-028**：Phase 7 使用用户提供的只读 SSH 记录确认当前 `jijia` 启动链：`lingze_robot.service → start_robot.sh → bringup.launch.py → jijia.launch.py`，厂家 dialog 与 speaker/Web/运动等为并列项；停止厂家主 service 会停止整个 ROS2 control group。本 Phase 没有重新 SSH，也没有机器人修改。
- **D-029**：首版采用 `/etc/casbot-yandex-realtime/external-dialog.enabled` marker gate 加独立 `casbot-yandex-dialog.service`。厂家主 service 保留并继续提供外围模块；三态 verify 必须阻止 vendor/Yandex dialog 同时运行，任何 service/process/graph 不确定性按失败处理。
- **D-030**：首轮不把 Yandex 接入厂家 `current_llm`、`backend_map` 或 Web 模型切换。gate 只在 marker 存在时跳过厂家 dialog；marker 不存在时原选择行为保持不变。
- **D-031**：Yandex 使用 `/opt/casbot-yandex-realtime` 独立 workspace 和 `venv --system-site-packages`；aiohttp 只进入独立 venv，复用系统/vendor rclpy 与 `lingze_msgs`，不覆盖 `/lingze/src` 或 `/lingze/install`。
- **D-032**：常规 rollback 停止 Yandex、删除 marker、重启厂家主 service 并验证 `VENDOR_MODE`，保留 gate patch；只有完整卸载才在 backup/current SHA 均匹配时 byte-identical restore 厂家 launch。Phase 7 只生成并本地测试这些工具，不执行机器人 apply。
- **D-033**：`/lingze/config/user_config.json` 是部署控制面的强制 fail-closed 输入。service/switch/rollback preflight、全部 verify 和 launch wrapper 每次重新读取；缺失、损坏、字段为空或 `robot_current_mode != jijia` 时禁止正式切换/Yandex 启动。`current_llm` 只允许 `lingze_omni_s2s`/`lingze_s2s`。长时检查比较起止 bytes SHA；解除 marker 在删除前、删除后和厂家 restart 紧邻前重复验证，漂移时原子恢复 marker。自动回滚遇到模式漂移时停止 Yandex、保留 marker 且不重启厂家 dialog，直到配置重新满足 guard。
- **D-034**：Phase 7 最终决策为独立 Yandex service 配合 vendor marker gate；正式切换和 Yandex 启动必须通过 `jijia` + 已知厂家 backend 的 fail-closed guard。若切换过程中配置漂移，则停止 Yandex、保留 marker，且不自动恢复未知模式。Phase 7 为 `COMPLETE`，Gate 7 为 `CONDITIONAL PASS`；`PcmAudioFrame.format`、厂家实际发布 rate/channels、speaker 输入兼容性、`hw:0,0` 实际采集、speaker/嘴型/flush、`session_active` 精确时序、真实 Yandex 网络和凭据、正式 switch/rollback 均保留为 Phase 8 实机验证项，不得视为已验证。Phase 7 收口时 Phase 8 尚未开始。
- **D-035**：Phase 8 已进入 `IN PROGRESS`。用户提供的 Phase 8C 实机记录证明固定基线源码、独立 venv、aiohttp purelib 隔离、ROS2 build/install 已完成；厂家 output metadata 为 `24000 / 1 / pcm_s16le` 且 speaker 实际出声，嘴型未确认。ensurepip 缺失时采用 `--without-pip --system-site-packages` 加 `pip --target <venv purelib>`，并验证系统 Python 无 aiohttp。build preflight 暴露 strict wrapper 与外部 ROS/ament setup 的 nounset 兼容问题；决策是在 `deploy/lib/casbot-runtime-env` 统一 helper 中仅围绕当前-shell source 临时关闭 nounset、保留环境副作用、传播错误并恢复原 option，所有共享控制入口和独立 launch 均复用该抽象，不硬编码 AMENT 变量。仓库 repair 已通过本地测试；机器人新版本同步和 build preflight 复验仍 pending。厂家 launch、marker、service、dialog 未切换或修改，Robot Yandex runtime、真实凭据/连接、正式 switch/rollback、Gate 8 均未开始；不进入 Phase 8D。
- **D-036**：Phase 8C build preflight、Phase 8D 配置/凭据准备和 Phase 8E disabled/inactive unit 安装已完成；vendor gate 为 `PATCHED`，恢复后 marker absent，真实机器人到 Yandex WebSocket/session 探测 PASS，但不构成替换 ROS2 节点接管证据。第一次 Phase 8F switch 在 Yandex service start 前的 transition verify 失败且 journal 无 Yandex entries；旧 automatic rollback 的立即 vendor-mode verify 失败，稍后完整 vendor-mode verify PASS，确认当时已安全恢复厂家模式。旧版本未保存具体 transition failure，因此该项保持 UNKNOWN；readiness/settling race 只是最强推断，厂家 watchdog/反第三方机制未被证明存在或不存在。控制面决策为所有 transition/service/Yandex/vendor readiness 采用统一 monotonic deadline、独立小 probe timeout、显式 transient/hard policy 和连续稳定 PASS；失败保留 last `CheckReport`，rollback 未证明时按最终 marker/service/process snapshot 提示。该 repair 仅在本地完成，尚未同步机器人，第二次 switch、替换节点验收、真实正常 rollback 与 Gate 8 均未完成。
- **D-037**：后续用户提供的实机事实确认机器人曾建立 Yandex 模式并完成真人俄语对话，理解和回复正常且主观响应很快；随后现场观察到机器人连续自己说话。speaker 声音被持续采集的本机 microphone 回采并触发新 Yandex turn 是当前最强推断，不是 VERIFIED 根因，仓库也没有 AEC。Phase 8H 采用可配置 half-duplex：generic controller 默认保留 speech barge-in，ROS robot/profile 明确关闭；assistant 输出期间入队端与 sender 发送前均抑制 microphone uplink 但不停止 arecord，忽略 speech-based interruption，保留显式 text replacement/cancel，并在 response done 后使用 500 ms monotonic resume guard。500 ms 是 project tuning policy。该 mitigation 仅本地实现和测试，真实 `/etc` 配置未修改、机器人未同步或复验，不得记为 FIELD PASS；后续通过也只证明 mitigation 有效，不证明物理 AEC 根因。`STATUS_IDLE` 自动结束问题明确保持 OUT OF SCOPE。
- **D-038**：用户提供的后续实机事实确认第二次受控 Vendor → Yandex 切换成功，真实 Yandex Realtime、机器人 microphone、兼容 ROS2 node 和 speaker 回复链路运行；Phase 8H 参数 `barge_in_enabled=false` 与 `microphone_resume_guard_ms=500` 实际加载。现场复测未再出现自问自答，响应速度正常，回答结束后继续提问仍有响应，因此 half-duplex mitigation 记为 `FIELD PASS`。该结论只证明 mitigation 在现场有效，不证明 acoustic feedback 是唯一物理根因，也不构成 AEC；旧 `STATUS_ERROR` 与此前自反馈事件的关系及正常使用下是否存在独立随机 session failure 仍为 UNKNOWN。
- **D-039**：Phase 8I 采用 generic-default-false、CASBOT profile 为 true 的 `auto_start_session`，只在节点初始化完成并先输出 `STATUS_IDLE/false` 后经现有 background bridge 提交一次，不形成 watchdog，人工 stop 后不自动拉起。Controller fatal cleanup 在最终组合 `last_error` 确定后只发一个 diagnostic；节点以配置 secret 和 Authorization/API-key 规则统一脱敏，经 outbound queue 在 ROS executor 侧写 error journal。systemd `ExecStartPre` 使用 Phase 8F shared readiness waiter（60 s overall、5 s probe、0.5 s poll、service 1 complete PASS），one-shot preflight 与 `Restart=no` 保持不变。runtime auto-reconnect 明确不实现。Phase 8I 仅本地 `COMPLETE / CONDITIONAL PASS`；固定 commit 未同步机器人、真实 YAML 未更新、unit 未 enable、cold-boot acceptance 未运行，Gate 8 非最终结论。
