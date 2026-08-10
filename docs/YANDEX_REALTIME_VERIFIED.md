# Yandex Realtime Verified Facts

## Status

```text
Phase 1 verification: COMPLETE
Gate 1: CONDITIONAL PASS
Phase 2 live PoC: COMPLETE
Gate 2: PASS
```

Phase 2 的本地运行时证据与用户调整后的 Gate 结论记录在
`docs/YANDEX_REALTIME_LOCAL_POC.md`。本文其余正文保留 Phase 1 官方资料核验时的
证据口径；其中“本轮未连接 API”等描述是 Phase 1 历史说明，不用于否认后续 PoC。

## Verification date

2026-08-10 (Asia/Shanghai)

## Scope and evidence rules

- 本文只核验 Yandex Cloud / Yandex AI Studio 的当前官方文档、官方 API Reference、官方 Release Notes、官方示例仓库和官方 SDK 源码。
- `VERIFIED` 表示有当前官方资料直接支持；`CONFLICT` 表示当前官方资料互相不一致；`UNKNOWN` 表示官方资料未公开；`INFERENCE` 表示项目根据多项官方证据做出的推断或工程决策，不冒充官方声明。
- 所有来源均在 2026-08-10 重新查询；未显示页面发布日期的动态页面标为 “current page, page date not shown”。
- 本轮没有连接 API、没有使用凭据、没有创建资源，也没有执行 Realtime PoC。因此所有线上可操作性结论都受本文 Gate 条件约束。
- 本文不包含机器人麦克风、`PcmAudioFrame`、QoS、namespace 或 systemd 的推测；这些仍属于 Phase 4 机器人运行时审计。

## Executive conclusion

- **Route A — VERIFIED at product/protocol-documentation level.** Yandex AI Studio 将 Realtime API 定义为基于 WebSocket 的 Voice Agent 接口，接收音频或文本，并以流式文本和合成音频返回模型结果。当前官方 Voice Agent 教程和官方示例展示了连续麦克风 PCM 输入、模型处理、`response.output_audio.delta` 增量语音输出和文本事件，因而不需要先改成独立 `STT → LLM → TTS` 架构。[S1][S5][S6]
- **Primary model — project decision, conditional:** `speech-realtime-260528`。官方 Model Gallery 将其列为 Realtime API 模型、上下文 65,536；2026-05-28 Release Notes 明确称它已供 Voice Agent 使用。[S2][S3]
- **Fallback model — VERIFIED:** `speech-realtime-250923`。它仍在当前 Model Gallery 中，上下文 32,768，并且当前 Voice Agent 概念页、教程和 API Reference 直接使用它。[S1][S2][S5]
- **Protocol baseline — VERIFIED:** 只采用 2026 当前格式和 `wss://ai.api.cloud.yandex.net/v1/realtime`。旧 endpoint 和旧交互格式已于 2026-05-12 停止支持。[S4]
- **Gate 1 — CONDITIONAL PASS.** 核心双向实时语音链路有充分官方证据，但必须在 Phase 2 用真实、非仓库化凭据验证 260528 握手、实际音频 delta、PCM 字节契约、打断一致性、俄语质量、长连接行为和实际计费。若 260528 失败，先在同一 Route A 上回退到 250923；若 250923 的实时音频也失败，停止并重新评审 Gate，不自动切换 Route B。

## 1. Current product and model

| Fact | Status | Official evidence | Source date/version | Project impact |
|---|---|---|---|---|
| 当前产品名称是 Yandex Cloud AI Studio Voice Agents；Voice Agent 通过 Realtime API 工作。 | VERIFIED | [S1] | Current page; page date not shown | Route A 使用 Realtime API，不使用普通 Responses API 代替实时语音。 |
| Realtime API 是事件驱动、双向、异步的 WebSocket 接口，面向音频和文本混合交互，并返回部分结果和最终事件。 | VERIFIED | [S1][S20] | Current pages | 网络层必须按事件流和 delta 设计。 |
| `speech-realtime-260528` 当前列于 Model Gallery，URI 为 `gpt://<folder_ID>/speech-realtime-260528`，上下文 65,536，接口为 Realtime API。 | VERIFIED | [S2] | Current page | 具备作为首选模型的官方可用性证据。 |
| `speech-realtime-260528` 于 2026-05-28 被 Release Notes 宣布可用于 Voice Agent。 | VERIFIED | [S3] | Release 2026-05-28 | 它是比 250923 更新的候选主模型。 |
| `speech-realtime-250923` 当前仍列于 Model Gallery，URI 为 `gpt://<folder_ID>/speech-realtime-250923`，上下文 32,768，接口为 Realtime API。 | VERIFIED | [S2] | Current page | 可作为当前文档最完整的兼容 fallback。 |
| 260528 应优先、250923 应 fallback。 | INFERENCE / PROJECT DECISION | [S1][S2][S3][S5] | Decision on 2026-08-10 | 260528 较新且上下文更大；250923 的教程和 Reference 更完整。Phase 2 必须先验证该排序。 |
| 当前 Realtime API 的最后一个明确生命周期声明是 Preview。 | VERIFIED WITH SEARCH LIMIT | [S3] | Preview announcement 2025-09-24; release notes checked through 2026-06-18 | 不能把当前生命周期写成 GA。未找到后续 GA announcement 不等于证明 Yandex 内部状态从未变化。 |

### Model-document conflict

当前 Model Gallery 和 2026-05-28 Release Notes 支持 260528，但 Voice Agent 概念页、教程、`session.created` 和 `session.updated` Reference 仍只列 250923，后两者甚至写 “Currently, only ... 250923 is supported”。这是明确的官方文档冲突。[S1][S2][S3][S5][S8]

项目判断：较新的 Release Notes 与当前 Model Gallery 足以证明 260528 是 Realtime/Voice Agent 模型，但不足以证明它已通过当前 endpoint 的真实握手。因此 260528 保持首选，同时成为 Gate 1 条件，不将 “Reference 滞后” 写成事实。

## 2. Endpoint and authentication

| Fact | Status | Official evidence | Project impact |
|---|---|---|---|
| WebSocket endpoint 是 `wss://ai.api.cloud.yandex.net/v1/realtime`。 | VERIFIED | [S4][S5] | 使用 TLS/WSS 当前 endpoint。 |
| 模型通过 URL query 指定：`?model=gpt://<folder_ID>/<model_ID>`。 | VERIFIED for 250923; CONDITIONAL for 260528 | [S2][S5] | 250923 的完整 URL 有官方教程；260528 URI 与 Realtime 归属已验证，但组合后的实际握手留给 Phase 2。 |
| 当前官方示例没有单独的 `agent` ID；行为、instructions、tools、voice 和 modalities 通过 session 配置。 | VERIFIED | [S1][S5][S9] | 不引入未经文档证明的 agent 参数。 |
| Service Account 可用 API Key：`Authorization: Api-Key <API_key>`。 | VERIFIED | [S5][S7] | 生产凭据外部配置，不写入 Git。 |
| Service Account 或用户可用 IAM token：`Authorization: Bearer <IAM_token>`。 | VERIFIED | [S7] | Phase 2 可选择 IAM token 或 API key。 |
| Realtime API 最低角色为 `ai.models.user`。 | VERIFIED | [S5][S7] | 资源准备时使用最小权限。 |
| 官方 Voice Agent 教程要求 API key scope `yc.ai.foundationModels.execute`。 | VERIFIED | [S5] | 创建测试 key 时限制 scope；本轮未创建。 |
| Realtime 专用区域或网络限制。 | NOT DOCUMENTED | [S1][S18] | 俄罗斯网络可达性、延迟和账号区域须在 Phase 2/部署环境验证；不得从缺少限制说明推断全球可用。 |

## 3. Session lifecycle

| Fact | Status | Official evidence | Project impact |
|---|---|---|---|
| 建立 WebSocket 时自动创建一个 session；`session.created` 是首个 server event。 | VERIFIED | [S1][S8] | 连接状态机等待并校验 `session.created`。 |
| 一个 session 保存对话历史和配置，并持续到 WebSocket 关闭。关闭后要继续工作需建立新 session。 | VERIFIED | [S1] | 多轮历史由当前连接内 session 保存；重连后的恢复策略留给 Phase 2。 |
| `session.update` 是 patch，只更新所提供字段；成功后返回完整有效配置的 `session.updated`。 | VERIFIED | [S9] | 可在运行中更新 instructions、voice、modalities 和 audio/VAD 配置，并校验回执。 |
| `instructions` 是默认 system instructions；`output_modalities` 控制文本或音频输出，voice 和 audio 配置位于 `session.audio`。 | VERIFIED | [S1][S5][S8] | 可承载 CASBOT persona 和音频输出选择。 |
| Maximum Realtime session wall-clock duration。 | NOT DOCUMENTED | Official Realtime concept, limits, reference and guide search | Phase 2 做长连接和重连测试；不得套用 SpeechKit Streaming STT 的限制。 |
| Idle timeout。 | NOT DOCUMENTED | Same search scope | Phase 2 测试空闲连接。 |
| 应用层 heartbeat/ping 是协议强制要求。 | NOT DOCUMENTED | [S5][S6] | 官方示例仅以 `aiohttp` 的 `heartbeat=20.0` 作为客户端实现选择，不能升级为协议要求；Phase 2 记录服务端实际行为。 |

## 4. Input audio contract

| Fact | Status | Official evidence | Project impact |
|---|---|---|---|
| 连续音频事件为 `input_audio_buffer.append`，`audio` 字段是 Base64 编码的音频 bytes；该事件无逐块确认。 | VERIFIED | [S5][S10] | 客户端可持续 append，但需自行实现流控与统计。 |
| 2026 当前 session schema 使用 `audio.input.format.type = "audio/pcm"` 和 `rate`。 | VERIFIED | [S4][S5] | 禁止继续使用旧顶层 `input_audio_format: "pcm16"`。 |
| Realtime 使用 LPCM；官方示例/SDK 将裸 PCM bytes 直接 Base64，不封装 WAV header。 | VERIFIED | [S1][S5][S19] | Phase 2 发送 raw PCM，不发送 WAV 容器。 |
| 官方 Realtime helper 采集单声道并将 float samples 转为 `numpy.int16` bytes；输出 helper 也按单声道 `int16` 读取。 | VERIFIED IMPLEMENTATION FACT | [S19] | 当前最强实现证据是 signed 16-bit mono；仍需 Phase 2 与服务端互操作验证。 |
| Realtime `audio/pcm` 的 byte order 是 little-endian。 | INFERENCE / CONDITIONAL | [S4][S19][S21] | 迁移文档把旧 `pcm16` 映射为新 `audio/pcm`；官方 SDK 使用 `int16` bytes，SpeechKit 官方 PCM16 定义为 signed little-endian，但 Realtime Reference 未直接重述 endian。Phase 2 必须用已知样本确认。 |
| sample rate 由 session 的 `rate` 指定；官方迁移例使用 24,000 Hz，当前 Voice Agent 教程使用 44,100 Hz，SDK microphone 默认 24,000 Hz。 | VERIFIED EXAMPLES | [S4][S5][S19] | 不把机器人采样率写死；Phase 4 取得真实输入后再决定直传或 resample。 |
| Realtime 支持的完整 sample-rate 枚举/范围。 | NOT DOCUMENTED | Current Realtime Reference and examples | 只把 24 kHz、44.1 kHz 视为官方示例值，不推断任意 rate 均可用。 |
| 最大 audio chunk/frame 大小。 | NOT DOCUMENTED | [S10] | Phase 2 测试 20 ms 等保守分片并记录服务端限制。 |
| Client-driven/manual turn。 | VERIFIED | [S10][S11] | 可 `append` 后发送 `input_audio_buffer.commit` 创建 user item，再用 `response.create` 请求回答。 |

## 5. Output audio contract

| Fact | Status | Official evidence | Project impact |
|---|---|---|---|
| session 可配置 `audio.output.format = {"type":"audio/pcm","rate":...}`、`voice` 和可选 `role`。 | VERIFIED | [S1][S4][S5] | 输出 PCM rate、音色和 role 外部配置。 |
| 当前官方 Voice Agent 教程和官方示例消费 `response.output_audio.delta`，其 `delta` 是 Base64 音频增量。 | VERIFIED BY CURRENT GUIDE/EXAMPLE | [S5][S6] | Route A 可将增量 PCM 后续映射到 `/audio/dialog_play`。 |
| Server Events Reference 将 `response.output_audio.delta` 和 `response.output_audio.done` 标记为 `[CURRENTLY NOT SUPPORTED]`。 | CONFLICT | [S12] | Phase 2 必须实际观察 delta；不能只按 Reference 标签或只按教程假定。 |
| 回答过程中可同时观察 `response.output_text.delta`，官方 Voice Agent 示例在音频模式下用它记录即将合成的文本。 | VERIFIED BY CURRENT GUIDE/EXAMPLE, REFERENCE CONFLICT | [S5][S6][S12] | 可作为未来 `/dialog/text_result` 的候选来源，但真实序列留给 Phase 2。 |
| `response.done` 总会在 response 流结束时发出，包含 `completed/cancelled/failed/incomplete` 等状态；`response.output_item.done` 标记 item 完成。 | VERIFIED | [S13] | 不依赖被标记不支持的 `response.output_audio.done` 作为唯一完成信号。 |
| 输出 PCM signed/bit depth/channel/endian。 | CONDITIONAL | [S19][S21] | 官方 helper 按 mono int16 播放，little-endian 仅有间接官方证据；Phase 2 保存测试音频并验证。 |

## 6. Text events and transcription

| Fact | Status | Official evidence | Project impact |
|---|---|---|---|
| `conversation.item.input_audio_transcription.completed` 返回 user audio 的最终 transcript；client commit 或 server VAD commit 后开始转写。 | VERIFIED | [S14] | 可记录最终用户转写；不依赖增量输入转写。 |
| `conversation.item.input_audio_transcription.delta` 当前在事件索引中标为不支持。 | VERIFIED LIMIT | [S12] | UI/状态机不得要求输入 partial transcript 才能工作。 |
| `response.created`、output deltas、`response.output_item.done` 和 `response.done` 构成回答生命周期。 | VERIFIED | [S5][S11][S13] | Phase 2 按 ID 关联 response/item，拒绝迟到事件。 |
| 事件索引把 `response.output_text.delta/done` 也标为不支持，但当前官方音频/文本教程实际使用这些事件。 | CONFLICT | [S5][S12][S22] | 文本结果事件同样必须在 Phase 2 实测，不将 Reference 的 unsupported 标记静默忽略。 |

## 7. VAD and turn detection

| Fact | Status | Official evidence | Project impact |
|---|---|---|---|
| 当前支持 `server_vad`，参数包括 `threshold` 和 `silence_duration_ms`。 | VERIFIED | [S1][S4][S5] | VAD 参数必须配置化。 |
| server VAD 检测到开始/结束时发送 `input_audio_buffer.speech_started` / `input_audio_buffer.speech_stopped`。 | VERIFIED | [S15] | `speech_started` 是未来本地 flush/barge-in 触发点。 |
| 当前官方 Voice Agent 示例只持续 append，不手动 commit 或 create response，仍描述完整语音问答。 | VERIFIED EXAMPLE BEHAVIOR | [S5][S6] | 证明示例中的 server VAD 路线负责 turn commit/触发回答；Phase 2 仍应记录精确事件序列。 |
| Manual turn 可使用 `input_audio_buffer.commit` 与 `response.create`。 | VERIFIED | [S10][S11] | 作为 server VAD 之外的客户端控制候选。 |

## 8. Barge-in / cancel / truncate

| Fact | Status | Official evidence | Project impact |
|---|---|---|---|
| `input_audio_buffer.speech_started` 提供 `audio_start_ms` 和 `item_id`。 | VERIFIED | [S15] | 可立即停止旧回答的本地播放。 |
| 当前官方 Voice Agent 示例在 `speech_started` 时清空本地 `AsyncAudioOut` 队列，避免继续播放旧回答。 | VERIFIED EXAMPLE BEHAVIOR | [S5][S6] | 与未来 `/audio/dialog_flush=true` 的行为目标一致。 |
| `response.cancel` 可取消进行中的 response；`response_id` 可选，省略时取消最近的进行中 response；服务端随后发 `response.done`。 | VERIFIED | [S16] | 用显式取消抑制旧 response 的后续流。 |
| `conversation.item.truncate` 用来把服务端历史同步到用户实际听到的 assistant 音频，必填 `item_id`、`content_index`、`audio_end_ms`；成功返回 `conversation.item.truncated`。 | VERIFIED | [S17] | 本地播放器必须跟踪真实已播放时长和 assistant item ID。 |
| 官方 SDK `AsyncAudioOut.clear()` 返回实际已交给播放设备的毫秒数，并在源码注释中明确用于 Realtime `conversation.item.truncate`。 | VERIFIED IMPLEMENTATION FACT | [S19] | 支持 CASBOT 打断链路的工程可实现性。 |
| 当前 Voice Agent 教程会主动发送 cancel/truncate。 | NOT SHOWN / GAP | [S5][S6] | 教程只清本地队列；完整 server-context 一致性必须在 Phase 2 验证。 |

### Project barge-in strategy for Phase 2

以下是项目工程策略，不是一个由 Yandex 官方页面原样给出的原子流程：

```text
input_audio_buffer.speech_started
    → stop and measure local playback
    → future /audio/dialog_flush=true
    → response.cancel
    → conversation.item.truncate(item_id, content_index, played_ms)
    → accept/process the new user turn
```

Phase 2 必须验证 cancel 与 truncate 的顺序、重复调用、迟到 delta、`response.done.status` 以及 VAD 是否已经自动取消 server generation。

## 9. Multi-turn, instructions, and text input

| Fact | Status | Official evidence | Project impact |
|---|---|---|---|
| session 保存当前连接内的 conversation history。 | VERIFIED | [S1] | 支持多轮对话。 |
| `instructions` 设置 system prompt，并可通过 `session.update` 在运行时更新。 | VERIFIED | [S1][S5][S9] | 可配置 CASBOT 的俄语前台 persona。 |
| 当前文本 user item 格式为 `conversation.item.create`，`item.type="message"`、`role="user"`、`content=[{"type":"input_text","text":"..."}]`。 | VERIFIED | [S4][S23] | 可映射未来 `/dialog/text_input`。 |
| `response.create` 根据当前 conversation context 和 session defaults 创建回答，并流式返回 events。 | VERIFIED | [S11] | 文本 item 后显式发送 `response.create`。 |
| 音频输入与文本输入可共存于同一 Realtime Voice Agent/session。 | VERIFIED AT PRODUCT/CONTEXT LEVEL | [S1][S20][S23] | 满足直接语音和 `/dialog/text_input` 共存需求；交错时序留给 Phase 2。 |

## 10. Russian, voice, and role

| Fact | Status | Official evidence | Project impact |
|---|---|---|---|
| 当前 Voice Agent 概念页称所列 Speech Realtime 模型专为 Russian 和 Kazakh 设计。 | VERIFIED for documented 250923 model | [S1] | 250923 明确满足俄语能力要求。 |
| Realtime `audio.input.languages` 可指定识别语言；SpeechKit 当前语言列表包含 `ru-RU`。 | VERIFIED | [S1][S24] | Phase 2 显式设置并测试俄语。 |
| Realtime 兼容 SpeechKit 标准 voices/roles，以及 Brand Voice Lite/Premium。 | VERIFIED | [S1] | 可选俄语 voice/role；第一阶段不要求复刻现有音色。 |
| 260528 的独立页面明确逐字声明 Russian/Kazakh 支持。 | NOT FOUND | [S2][S3] | 260528 虽被列为 Voice Agent/Realtime 新模型，但俄语必须成为 Phase 2 验收条件；失败则回退 250923。 |
| 俄罗斯 Yandex Cloud 区域有可用性区域。 | VERIFIED GENERAL CLOUD FACT | [S25] | 说明部署地域存在，但不等于 Realtime 在具体账号/网络的可达性保证；后者仍需 Phase 2 验证。 |

## 11. Errors, quotas, and limits

| Fact | Status | Official evidence | Project impact |
|---|---|---|---|
| Realtime `error` event 包含 server `event_id`，以及 `error.type`、`code`、`message`、`param` 和关联 client `event_id`。 | VERIFIED | [S26] | 解析结构化字段并脱敏记录，不依赖可变 message 文案。 |
| AI Studio 通用错误包括 400 `INVALID_ARGUMENT`、401 `UNAUTHENTICATED`、403 `PERMISSION_DENIED`、429 `RESOURCE_EXHAUSTED`、500 `INTERNAL`、503 `UNAVAILABLE`、504 `DEADLINE_EXCEEDED`。 | VERIFIED | [S27] | Phase 2 工程策略：401/403 视为凭据/配置失败；429 退避并检查 quota；5xx/504 作为重连/退避候选。该策略不是官方协议事实。 |
| 默认 Realtime 并发 session quota 为 10；session creation quota 为每秒 10。quota 可请求支持调整。 | VERIFIED | [S18] | Phase 2 压测不得误把 quota 当协议错误。 |
| `rate_limits.updated` 当前在 Server Events Reference 标记为不支持。 | VERIFIED LIMIT | [S12] | 客户端不能依赖动态 rate-limit event，需基于 429 和静态 quota 处理。 |
| Realtime 最大 session 时长、idle timeout、最大音频 chunk。 | NOT DOCUMENTED | [S10][S18] | 均列入 Phase 2 运行时测量。 |

## 12. Pricing

以下为 2026-08-10 查询到的 Yandex.Cloud LLC 俄罗斯卢布价格；页面说明 RUB/KZT 价格含 VAT，不同签约实体适用不同币种。[S28]

### Model tokens, synchronous mode, per 1,000 tokens

| Model | Input | Cached | Tool tokens | Output | Status |
|---|---:|---:|---:|---:|---|
| Speech Realtime 260528 | ₽0.1 | ₽0.025 | ₽0.025 | ₽0.2 | VERIFIED |
| Speech Realtime 250923 | ₽0.8 | ₽0.2 | ₽0.2 | ₽0.8 | VERIFIED |

### Voice Agent audio

| Usage | Price | Status |
|---|---:|---|
| Incoming audio | ₽0.0264 per second | VERIFIED as general Voice Agent pricing |
| Outgoing audio | ₽0.0203 per second | VERIFIED as general Voice Agent pricing |

- 官方 pricing 页面明确说 Voice Agent 成本由 incoming audio、outgoing audio、Speech Realtime 文本生成和 tool invocation 组成；内置 tools 另行计费。[S28]
- 同一页面的 Voice Agent 说明和计算示例仍点名 `speech-realtime-250923`，没有把每秒音频价格明确绑定到 260528。因此“260528 同样采用上述每秒音频价格”是 **INFERENCE**，必须在 Phase 2 Billing/Usage 中核对。
- 当前 Voice Agent 概念页说明 Billing UI 会分别显示 input audio duration、cached tokens、tool tokens、output text tokens、output audio duration 和 total consumption。[S1]
- Phase 2 成本测试必须记录实际 Billing/Usage，不得只用静态表估算。

## 13. API lifecycle and 2026 migration risks

| Fact | Status | Official evidence | Project impact |
|---|---|---|---|
| Voice Agents 与 Realtime API 于 2025-09-24 以 Preview 发布。 | VERIFIED | [S3] | 当前不能宣称 GA。 |
| 截至所查当前 Release Notes 的最新条目（2026-06-18），未找到后续 Realtime GA announcement。 | VERIFIED SEARCH RESULT, NOT ABSOLUTE PRODUCT STATE | [S3] | Gate 保持 conditional；实施前继续关注 Release Notes。 |
| 2026-04-20 发布新交互格式，旧格式只支持到 2026-05-12；2026-05-12 起已停止支持。 | VERIFIED | [S3][S4] | 禁止复制 2025 旧示例。 |
| endpoint 从 `wss://rest-assistant.api.cloud.yandex.net/v1/realtime/` 迁移为 `wss://ai.api.cloud.yandex.net/v1/realtime`。 | VERIFIED | [S4] | 只配置新 endpoint。 |
| session schema 把 modalities、VAD、audio format/voice、instructions/tools 移入 `session`；VAD 字段改为 `threshold`；文本 item 改为 `content/input_text`；`response.create` 可简化。 | VERIFIED | [S4] | Phase 2 parser/client 只实现当前 schema，并对未知事件宽容。 |

## 14. Official-document conflicts

### C-001 — 260528 vs Realtime session/reference pages

- Model Gallery：260528 和 250923 都属于 Realtime API；260528 上下文 65,536。[S2]
- 2026-05-28 Release Notes：260528 已供 Voice Agent 使用。[S3]
- Voice Agent 概念页、当前教程、`session.created/session.updated` Reference：仍只列或声称只支持 250923。[S1][S5][S8]
- 结论：`CONFLICT`。将 Reference 滞后视为可能解释，但仅为 inference。Phase 2 先握手 260528，失败则尝试 250923。

### C-002 — `response.output_audio.delta`

- 当前官方 Voice Agent 教程和其链接的官方 GitHub 示例实际解析、Base64 解码并播放 `response.output_audio.delta`。[S5][S6]
- Server Events 索引和事件页把它标为 `[CURRENTLY NOT SUPPORTED]`。[S12]
- 结论：`CONFLICT`。产品说明与可运行教程足以证明 Route A 的官方设计和示例链路，但 Phase 2 必须实际收到该事件，Gate 因此为 conditional。

### C-003 — output text events

- 当前音频教程使用 `response.output_text.delta`；文本回答教程还使用 `response.output_text.done`。[S5][S22]
- Server Events 索引把两者标为不支持。[S12]
- 结论：`CONFLICT`。未来 `/dialog/text_result` 的事件选择必须以 Phase 2 实际序列为准。

### C-004 — 260528 Voice Agent audio pricing

- token pricing 表单列 260528 与 250923。[S28]
- Voice Agent 音频计费说明仍用 250923 解释文本生成费用，没有明确说明 per-second audio price 与 260528 的绑定关系。[S28]
- 结论：`DOCUMENTATION GAP`。260528 实际账单必须在 Phase 2 核对。

### C-005 — PCM byte-level contract

- Realtime 文档写 `audio/pcm`/LPCM，迁移页从 `pcm16` 映射到 `audio/pcm`；官方 SDK helper 实际使用 mono `int16` raw bytes。[S1][S4][S19]
- Realtime Reference 没有直接写 signed、16-bit、little-endian、mono 的完整一句话契约。
- 结论：`DOCUMENTATION GAP`，不是互相矛盾。本文只把 SDK 可观察实现写成 VERIFIED，把 Realtime little-endian 写成 INFERENCE/CONDITIONAL。

## 15. Unknowns deferred to later phases

### Phase 1 documentation unknowns

- Maximum Realtime session duration: **NOT DOCUMENTED**.
- Idle timeout: **NOT DOCUMENTED**.
- Mandatory application heartbeat/ping: **NOT DOCUMENTED**.
- Maximum audio append chunk/frame: **NOT DOCUMENTED**.
- Complete accepted sample-rate range: **NOT DOCUMENTED**.
- Realtime-specific byte-order statement: **NOT DOCUMENTED**.
- 260528-specific Russian/Kazakh statement: **NOT FOUND**.
- Later explicit Realtime GA announcement: **NOT FOUND in current Release Notes**.
- Realtime-specific region/network restriction: **NOT DOCUMENTED**.

### Phase 2 runtime validation

1. 以 260528 建立真实 WebSocket，确认 `session.created/session.updated`；失败时改用 250923，仍保持 Route A。
2. 发送已知 PCM16 mono 样本，验证 24 kHz 与 44.1 kHz、endianness、raw framing 和安全 chunk size。
3. 实际收到并保存事件序列：transcription、text delta、audio delta、item done、response done。
4. 验证 `speech_started` 时本地清队列、`response.cancel`、`conversation.item.truncate`、迟到 delta 和 server conversation 一致性。
5. 验证俄语识别、生成、voice/role 和自然打断；260528 不达标则回退 250923。
6. 测量连接空闲、长会话、heartbeat、服务端关闭、重连、多轮上下文和超时。
7. 核对 Billing/Usage：260528 token、输入/输出音频秒数、tools 和实际币种。
8. 验证俄罗斯部署网络的 TLS、DNS、延迟和账号区域可用性。

### Phase 4 robot runtime unknowns

- 麦克风是 ROS2 Topic 还是 ALSA。
- 机器人真实采样率、位深、声道和帧大小。
- `lingze_msgs/msg/PcmAudioFrame` 真实字段。
- `/dialog/status` 实际 QoS。
- 真实 node name / namespace。
- systemd / launch 启动方式。
- 当前 Qwen 模型准确 ID。
- 当前是否向 Omni 输入摄像头。
- 当前 persona/system prompt 来源。
- 当前音色、VAD 和 interruption baseline。

这些项目未被 Phase 1 “补全”，也未作任何推测。

## 16. Gate 1 decision

```text
CONDITIONAL PASS
```

理由：官方产品说明、当前教程和官方示例共同证明 Yandex Realtime API 能形成“连续实时音频输入 → 专用多模态模型理解/生成 → 增量合成语音与文本输出”的单条 Route A；俄语、session history、instructions、VAD、text input、cancel 和 truncate 均有官方协议或产品证据。没有证据要求改成独立 `STT → LLM → TTS`。

条件：

1. 260528 必须在 Phase 2 通过当前 endpoint 的真实握手；失败时回退 250923。
2. 必须实际收到 `response.output_audio.delta`；若 260528 未收到则测试 250923，两者均失败即停止并重审 Gate。
3. 必须验证官方 SDK 暗示的 PCM16 mono raw byte contract、endianness、sample rate 与分片；不提前绑定机器人参数。
4. 必须验证 `speech_started → local flush → cancel → truncate` 的服务端和本地播放一致性；若无法可靠中断且同步上下文，则停止并重审 Gate。
5. 必须验证 260528 的俄语识别/回答/音色；失败时回退官方明确面向 Russian/Kazakh 的 250923。
6. 必须测出可接受的 idle/maximum session 行为与重连策略，因为官方未公开数值。
7. 必须通过 Billing/Usage 核验 260528 的实际音频和 token 计费。
8. Realtime 最新明确状态仍是 Preview，且 Reference 与教程存在同步冲突；进入生产前需再次查 Release Notes/Reference。

条件失败后的退路只限于同一 Route A 的 250923 fallback 或停止评审；本 Gate 不授权 Route B。

## Official sources

- **[S1]** [Voice agents in Yandex Cloud AI Studio](https://aistudio.yandex.ru/docs/en/ai-studio/concepts/agents/realtime.html) — current page, checked 2026-08-10.
- **[S2]** [Available generative models](https://aistudio.yandex.ru/docs/en/ai-studio/concepts/generation/models.html) — current Model Gallery documentation, checked 2026-08-10.
- **[S3]** [Yandex Cloud AI Studio release notes](https://aistudio.yandex.ru/docs/en/ai-studio/release-notes/) — entries through 2026-06-18, checked 2026-08-10.
- **[S4]** [Realtime API format update](https://aistudio.yandex.ru/docs/en/ai-studio/concepts/agents/realtime-changes.html) — 2026 format migration, checked 2026-08-10.
- **[S5]** [Creating a voice agent via Realtime API](https://aistudio.yandex.ru/docs/en/ai-studio/operations/agents/create-voice-agent.html) — current official tutorial, checked 2026-08-10.
- **[S6]** [Official Realtime Voice Agent example](https://github.com/yandex-ai-studio/yandex-ai-studio-api-examples/blob/main/realtime/voice_agent.py) — official Yandex AI Studio GitHub, `main`, checked 2026-08-10.
- **[S7]** [Authentication with the Yandex Cloud AI Studio API](https://aistudio.yandex.ru/docs/en/ai-studio/api-ref/authentication.html) — current page, checked 2026-08-10.
- **[S8]** [Server event: session.created](https://aistudio.yandex.ru/docs/en/ai-studio/serverEvents/realtimeServerSessionCreated.html) and [session.updated](https://aistudio.yandex.ru/docs/en/ai-studio/serverEvents/realtimeServerSessionUpdated.html) — current Reference, checked 2026-08-10.
- **[S9]** [Client event: session.update](https://aistudio.yandex.ru/docs/en/ai-studio/clientEvents/realtimeSessionUpdate.html) — current Reference, checked 2026-08-10.
- **[S10]** [Client event: input_audio_buffer.append](https://aistudio.yandex.ru/docs/en/ai-studio/clientEvents/realtimeInputAudioBufferAppend.html) and [input_audio_buffer.commit](https://aistudio.yandex.ru/docs/en/ai-studio/clientEvents/realtimeInputAudioBufferCommit.html) — current Reference, checked 2026-08-10.
- **[S11]** [Client event: response.create](https://aistudio.yandex.ru/docs/en/ai-studio/clientEvents/realtimeResponseCreate.html) — current Reference, checked 2026-08-10.
- **[S12]** [Realtime server events index](https://aistudio.yandex.ru/docs/en/ai-studio/serverEvents/) and [response.output_audio.delta](https://aistudio.yandex.ru/docs/en/ai-studio/serverEvents/realtimeServerResponseOutputAudioDelta.html) — current Reference, checked 2026-08-10.
- **[S13]** [Server event: response.done](https://aistudio.yandex.ru/docs/en/ai-studio/serverEvents/realtimeServerResponseDone.html) and [response.output_item.done](https://aistudio.yandex.ru/docs/en/ai-studio/serverEvents/realtimeServerResponseOutputItemDone.html) — current Reference, checked 2026-08-10.
- **[S14]** [Server event: input audio transcription completed](https://aistudio.yandex.ru/docs/en/ai-studio/serverEvents/realtimeServerConversationItemInputAudioTranscriptionCompleted.html) — current Reference, checked 2026-08-10.
- **[S15]** [Server event: input_audio_buffer.speech_started](https://aistudio.yandex.ru/docs/en/ai-studio/serverEvents/realtimeServerInputAudioBufferSpeechStarted.html) and [speech_stopped](https://aistudio.yandex.ru/docs/en/ai-studio/serverEvents/realtimeServerInputAudioBufferSpeechStopped.html) — current Reference, checked 2026-08-10.
- **[S16]** [Client event: response.cancel](https://aistudio.yandex.ru/docs/en/ai-studio/clientEvents/realtimeResponseCancel.html) — current Reference, checked 2026-08-10.
- **[S17]** [Client event: conversation.item.truncate](https://aistudio.yandex.ru/docs/en/ai-studio/clientEvents/realtimeConversationItemTruncate.html) and [server acknowledgement](https://aistudio.yandex.ru/docs/en/ai-studio/serverEvents/realtimeServerConversationItemTruncated.html) — current Reference, checked 2026-08-10.
- **[S18]** [Yandex Cloud AI Studio quotas and limits](https://aistudio.yandex.ru/docs/en/ai-studio/concepts/limits.html) — current page, checked 2026-08-10.
- **[S19]** Official Yandex AI Studio SDK audio helpers: [microphone.py](https://github.com/yandex-cloud/yandex-ai-studio-sdk/blob/master/src/yandex_ai_studio_sdk/_experimental/audio/microphone.py), [out.py](https://github.com/yandex-cloud/yandex-ai-studio-sdk/blob/master/src/yandex_ai_studio_sdk/_experimental/audio/out.py), [utils.py](https://github.com/yandex-cloud/yandex-ai-studio-sdk/blob/master/src/yandex_ai_studio_sdk/_experimental/audio/utils.py) — `master`, checked 2026-08-10.
- **[S20]** [Specifics of API implementation in AI Studio](https://aistudio.yandex.ru/docs/en/ai-studio/concepts/api.html) and [AI agents](https://aistudio.yandex.ru/docs/en/ai-studio/concepts/agents/index.html) — current pages, checked 2026-08-10.
- **[S21]** [Official SDK SpeechKit audio format reference](https://aistudio.yandex.ru/docs/en/ai-studio/sdk-ref/types/speechkit.html) — supporting PCM16 definition, not a substitute for Realtime runtime verification; checked 2026-08-10.
- **[S22]** [Creating a Voice Agent with text responses](https://aistudio.yandex.ru/docs/en/ai-studio/operations/agents/voice-text-agent.html) — current official tutorial, checked 2026-08-10.
- **[S23]** [Client event: conversation.item.create](https://aistudio.yandex.ru/docs/en/ai-studio/clientEvents/realtimeConversationItemCreate.html) — current Reference, checked 2026-08-10.
- **[S24]** [Supported SpeechKit recognition languages](https://aistudio.yandex.ru/docs/en/speechkit/stt/models.html) — linked by Realtime Voice Agent documentation, checked 2026-08-10.
- **[S25]** [Yandex Cloud regions](https://yandex.cloud/en/docs/overview/concepts/region) — updated 2026-07-23, checked 2026-08-10.
- **[S26]** [Realtime server error event](https://aistudio.yandex.ru/docs/en/ai-studio/serverEvents/realtimeServerError.html) — current Reference, checked 2026-08-10.
- **[S27]** [AI Studio error codes](https://aistudio.yandex.ru/docs/en/ai-studio/troubleshooting/error-codes.html) — current page, checked 2026-08-10.
- **[S28]** [Yandex Cloud AI Studio pricing](https://aistudio.yandex.ru/docs/en/ai-studio/pricing.html) and [Russian pricing view](https://aistudio.yandex.ru/docs/ru/ai-studio/pricing.html) — RUB/USD/KZT current page, checked 2026-08-10.
