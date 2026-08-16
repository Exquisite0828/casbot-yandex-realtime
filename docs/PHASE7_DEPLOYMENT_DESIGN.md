# Phase 7 Deployment Design

> Phase 7 设计收口日期：2026-08-14
> Phase 7：COMPLETE
> Gate 7：CONDITIONAL PASS
> Phase 7 收口时 Phase 8：NOT STARTED（历史状态）
> 当前：Phase 8 IN PROGRESS；Phase 8C ROBOT BUILD COMPLETE；CONTROL-PLANE REVALIDATION PENDING

## 1. 目标与非目标

本 Phase 在本地仓库中建立可供 Phase 8 使用的部署控制面：独立目录、独立
Python 环境、独立 systemd service、厂家 launch marker gate、preflight、三态
验证、有限切换、有限回滚和只读 metadata probe。

本 Phase 不部署、不 SSH、不操作机器人、不执行真实 `systemctl`/ROS graph/
麦克风命令、不调用真实 Yandex，也不写真实凭据。生成工具不等于机器人已部署。

首版明确不做：

- 删除或覆盖厂家 `dialog_node` executable；
- 把本项目安装到 `/lingze/install`；
- 把 Yandex 加入厂家 `current_llm` 或 Web 模型列表；
- 用未知 `current_llm` 值长期禁用厂家节点；
- 长期停止整个 `lingze_robot.service`；
- 使用 `kill`、`pkill`、`rm -rf` 或 `rsync --delete`；
- 启用无限自动重启；
- 自动进入 Phase 8。

## 2. Phase 7 补充只读事实

以下为 **VERIFIED BY USER-PROVIDED READ-ONLY SSH TRANSCRIPT**，不是本轮重新
SSH 取得：

```text
hostname: xiaoling0040
robot_current_mode: jijia
current_llm: lingze_omni_s2s
namespace: lzdl10823

dialog node: /lzdl10823/dialog_node
dialog executable:
  /lingze/install/lingze_omni_s2s/lib/lingze_omni_s2s/dialog_node
installed launch:
  /lingze/install/bringup/share/bringup/launch/launch/jijia.launch.py
```

`jijia.launch.py` 直接读取 `/lingze/config/user_config.json` 的 `current_llm`，
通过 `backend_map` 选择 `lingze_omni_s2s` 或 `lingze_s2s`，并把 dialog、speaker、
Web、运动、摄像头、传感器等作为并列启动项。已搜索的 bringup launch 目录没有
发现 dialog 专用 `respawn`、`OnProcessExit` 或 restart；这只证明已检查的 launch
定义没有独立拉起配置，不证明机器人其他位置绝对没有 watchdog。

环境事实：ROS2 Humble、Python 3.10.12、`/usr/local/bin/colcon`、`rclpy`、
`lingze_msgs.msg.PcmAudioFrame` 和 `/usr/bin/arecord` 可用；当前系统 Python 没有
`aiohttp`。`hw:0,0` 有 card 0/device 0 枚举依据，但尚未在厂家 dialog 退出后实际
打开，保持 **CONDITIONAL**。

## 3. 当前厂家启动链

```text
lingze_robot.service
  Type=simple / User=root / Restart=always
  WorkingDirectory=/lingze
  ExecStart=/bin/bash /lingze/bin/start_robot.sh
  KillMode=control-group
  └── start_robot.sh
      ├── source /opt/tros/humble/setup.bash 或 /opt/ros/humble/setup.bash
      ├── 设置 RMW / FastDDS
      ├── source /lingze/install/setup.bash
      ├── 等待设备
      └── exec ros2 launch bringup bringup.launch.py
          └── jijia.launch.py
              ├── dialog_node
              ├── speaker_node
              ├── Web
              ├── 运动/摄像头/传感器
              └── 其他厂家节点
```

停止 `lingze_robot.service` 会停止整个 control group，因此它不能作为长期禁用
厂家 dialog 的方法。部署必须保留该服务继续管理 speaker、嘴型、Web、运动和
其他节点，只让 `jijia.launch.py` 有条件跳过 dialog 启动项。

## 4. 推荐架构

```text
lingze_robot.service
└── 厂家 bringup（保留）
    ├── speaker / 嘴型
    ├── Web / 运动 / 摄像头 / 传感器
    └── vendor dialog
        └── external-dialog marker 存在时跳过

casbot-yandex-dialog.service（独立，首次 Restart=no）
└── /opt/casbot-yandex-realtime
    ├── venv --system-site-packages
    ├── 独立 build/install/log
    └── launch wrapper 直接 exec 安装后的 realtime_dialog_node
        └── /lzdl10823/dialog_node（namespace 运行时解析）
```

Yandex service 使用 `Requires=lingze_robot.service`，因为 speaker 和其他厂家模块
必须继续存在；禁止 `Conflicts=lingze_robot.service`。`PartOf` 使厂家主服务的
stop/restart 向 Yandex service 传播，避免主依赖消失后留下孤儿 dialog。首次使用
`Restart=no`，防止未验收节点无限自动拉起。

## 5. 三态和互斥模型

| 状态 | 厂家 dialog | Yandex dialog | marker |
|---|---:|---:|---:|
| `VENDOR_MODE` | ON | OFF | absent |
| `TRANSITION` | OFF | OFF | present |
| `YANDEX_MODE` | OFF | ON | present |

禁止状态是厂家 dialog 和 Yandex dialog 同时 ON。互斥不依赖操作员记忆，而由
四类证据共同检查：

1. marker 状态；
2. `lingze_robot.service` / `casbot-yandex-dialog.service` 状态；
3. 厂家和 Yandex executable 的进程检查，包括 orphan process；
4. rclpy graph 中 `/namespace/dialog_node` 的精确计数和 Yandex MainPID 命令证据。

switch 与 rollback apply 还共享 `/var/lib/.../operation-state/operation.lock` 的非阻塞
advisory lock；同一时刻只能有一个事务。厂家 PID 检查覆盖 `lingze_omni_s2s` 和
`lingze_s2s` 两种已知 backend。Yandex wrapper 直接 `exec` 安装后的节点，因此健康
模式必须只有一个匹配 PID，且它精确等于 systemd MainPID；额外 orphan 一律失败。
任何命令失败或 graph 无法可靠统计时为 `FAIL`，不得降格成安全的“不存在”。

## 6. Marker 语义

marker 固定为：

```text
/etc/casbot-yandex-realtime/external-dialog.enabled
```

- marker 不存在：保留原 `_read_current_llm()` 与 `backend_map` 行为；
- marker 存在：`jijia.launch.py` 返回 `LogInfo` 并跳过厂家 dialog；
- marker 不修改 `current_llm`，不修改 `backend_map`，不删除 executable；
- 常规回滚只删除 marker，保留 gate patch；
- 完整卸载才严格 restore 厂家 launch 原始 bytes。

## 7. 目录设计

```text
/opt/casbot-yandex-realtime/
├── src or release
├── venv
├── build
├── install
├── log
└── deploy/bin

/etc/casbot-yandex-realtime/
├── casbot-yandex.yaml
├── yandex.env                 # 0600
└── external-dialog.enabled

/var/lib/casbot-yandex-realtime/
├── vendor-backups
├── vendor-gate-manifest.json
└── operation-state/operation.lock
```

真实凭据只进入 `/etc/.../yandex.env`。`/var/lib` 只保存 hash、byte-preserved backup
和操作状态，不保存用户音频或完整对话。所有路径由 `DeploymentPaths` 集中定义；
`--root <temporary-root>` 将绝对路径映射到测试目录，只有真实 root `/` 才要求 root
权限。

## 8. Python / ROS2 依赖策略

Phase 8 构建顺序冻结为：

```bash
source /opt/tros/humble/setup.bash  # 不存在时使用 /opt/ros/humble/setup.bash
source /lingze/install/setup.bash
python3 -m venv --system-site-packages /opt/casbot-yandex-realtime/venv
source /opt/casbot-yandex-realtime/venv/bin/activate
python -m pip install -r deploy/config/requirements.txt
colcon build --base-paths src
source /opt/casbot-yandex-realtime/install/setup.bash
```

`requirements.txt` 只有 `aiohttp>=3.8,<4`，与 `setup.py` 一致。不得把 `aiohttp`
安装到系统 Python。`--system-site-packages` 用于复用系统/vendor 的 rclpy 和
`lingze_msgs`；ROS、vendor、venv、项目 install 必须按该顺序加载。

## 9. systemd 关系

模板包含：

```ini
After=network-online.target lingze_robot.service
Requires=lingze_robot.service
PartOf=lingze_robot.service
ConditionPathExists=/etc/casbot-yandex-realtime/external-dialog.enabled
EnvironmentFile=/etc/casbot-yandex-realtime/yandex.env
ExecStartPre=...casbot-yandex-preflight --mode service
ExecStart=...casbot-yandex-launch
KillSignal=SIGINT
KillMode=control-group
Restart=no
```

Phase 8 首次验证前不得 `enable`；只允许维护窗口内手工 `start`。人工验收完成后
再单独决定是否 enable 或调整重启策略。

## 10. 凭据管理

- `yandex.env.example` 只含 `<replace_me>`；
- 生产文件要求 root-owned `0600` regular non-symlink，且父目录不可 group/other 写；
- config 和 marker 同样要求可信 owner/parent、regular non-symlink 和不可非 owner 写；
- env 只接受四个白名单赋值，拒绝 `export`、重复名、未知变量和非支持转义；
- 必需变量缺失或仍为 placeholder 时失败；
- 日志和 JSON 只输出变量名/检查结果，不输出值；
- launch、switch 和 rollback 不读取或打印凭据内容；
- 不记录 Authorization，不保存真实语音。

## 11. Vendor gate 安全模型

`casbot-yandex-vendor-gate` 支持 `status`、`plan`、`apply`、`restore`：

- `status` 返回 `UNPATCHED/PATCHED/DIVERGED/MISSING`；
- 以 Python AST 定位唯一 `_dialog_backend_node()` 中的唯一
  `current_llm = _read_current_llm()`，不依赖固定行号；
- 同时验证 `LogInfo` binding、两种 `backend_map` 和唯一
  `_optional_node(..., "dialog_node")` 形状；任何缺失/重复均拒绝；
- patch 有唯一 BEGIN/END marker；
- apply 前 byte-preserved backup，记录原始/patched SHA-256、mode、uid、gid；
- 临时文件同 filesystem 写入、flush、fsync、`py_compile` 后原子 replace；
- replace 前重新校验目标仍为 original SHA，拒绝 TOCTOU 漂移；
- replace 后 durability 失败会尝试恢复 original bytes；无法证明恢复时保留 backup 和
  manifest 并明确失败；
- restore 同时校验 backup SHA 和当前 patched SHA；任一漂移拒绝覆盖；
- 即使目标语义上已 unpatched，也必须与 manifest original SHA byte-identical 才算幂等；
- restore 保留历史备份和 manifest，不重启服务。

`apply` 和 `restore` 默认 dry-run，只有显式 `--apply` 才写入。

## 12. Preflight

结果只有 `PASS/FAIL/DEFERRED`。`DEFERRED` 不冒充 PASS；任何 hard `FAIL` 返回
非零。

- `build`：Python 3.10、colcon、ROS/vendor setup、venv、aiohttp/rclpy/
  `PcmAudioFrame` import、项目 package/install/executable/launch/config；
- `service`：marker、gate、厂家主服务、配置/env/权限/变量、非空
  `speaker_pcm_format`、namespace、厂家与 orphan Yandex 进程、graph 0 dialog、
  speaker、麦克风释放和 runtime imports；
- `switch`：要求已验证 vendor mode、Yandex service/进程停止、gate/config/
  credentials 完整；麦克风释放只能在 marker restart 后验证，因此此时明确
  `DEFERRED`；
- `rollback`：任何 service、marker 或进程操作前重新验证机器人模式和厂家 dialog
  backend，失败时零命令、零 marker 写入。

配置的 `speaker_pcm_format` 必须唯一位于严格扁平的 `/** → ros__parameters`
层级并使用引号；namespace 与 launch wrapper 共用 ROS token 规则。

`service`、`switch`、`rollback` preflight 和全部 verify 模式都会重新读取
`/lingze/config/user_config.json`。该文件缺失、不可读、不是合法 UTF-8 JSON、根节点
不是 object，或 `namespace`、`robot_current_mode`、`current_llm` 任一不是非空字符串
时统一 fail closed。正式 switch、systemd `ExecStartPre --mode service` 和 launch
wrapper 都要求 `robot_current_mode == "jijia"`；`current_llm` 只允许厂家已验证映射
`lingze_omni_s2s` 或 `lingze_s2s`。即使设置 `CASBOT_ROS_NAMESPACE`，也不能绕过该
配置校验。错误输出只说明字段/类别，不输出配置值。

service/switch/verify 在进程、ROS graph、import 等检查开始前保存配置原始 bytes 的
SHA-256，并在报告返回前重新解析和比较；结束值无效或 bytes 发生变化均为
`robot_config_stable=FAIL`，防止长时检查使用过期模式快照。

## 13. 首次切换

Phase 8 预期顺序：

```text
switch preflight
→ stop Yandex service
→ atomic create marker
→ restart lingze_robot.service
→ wait bounded
→ verify TRANSITION（vendor/Yandex 都 OFF，speaker ON，mic free）
→ service preflight（再次读取 user_config，作为启动前最后一道控制面 guard）
→ start Yandex service
→ wait bounded
→ wrapper 直接 exec 安装后的 realtime_dialog_node
→ verify YANDEX_MODE（唯一 PID = service MainPID；唯一 graph dialog）
→ 人工验收
```

人工验收必须包括 `start_session` 后等待 `STATUS_LISTENING`，不能把 Service 返回
`scheduled` 当作 ready。还要检查俄语真人对话、speaker、嘴型、stop、interruption、
flush、Web、其他机器人模块、Yandex usage 和原云端 usage。

## 14. 正常回滚

```text
rollback preflight（jijia + supported current_llm）
→
stop Yandex service
→ verify TRANSITION
→ atomic remove marker
→ restart lingze_robot.service
→ wait bounded
→ verify VENDOR_MODE
→ 厂家真人语音验收
```

已在 vendor mode 时幂等成功且不重复 restart。Yandex 已停止或 marker 已不存在时
按可验证状态安全处理。正常回滚不 restore launch patch。

## 15. 完整卸载恢复

完整卸载必须拆开执行，禁止危险的一键删除：

1. rollback 并验证 `VENDOR_MODE`；
2. stop/disable Yandex service；
3. `vendor-gate restore --apply`；
4. 验证恢复后的原始 SHA-256；
5. 人工移除独立 `/opt`、`/etc`、`/var/lib` 项目目录。

本 Phase 不实现 `rm -rf` 卸载命令。

## 16. 自动回滚边界

marker 原子 replace 已发生后（包括随后目录 fsync 失败）、`YANDEX_MODE` 建立前的
hard failure 只尝试一次。配置仍有效时：

```text
stop Yandex → remove marker → restart vendor main → verify vendor-mode
```

每一步有 timeout；不无限重试。自动回滚成功仍返回切换失败的非零结果，并保留
原始错误。只有 `systemctl stop` 成功且进程检查明确证明 Yandex executable 已退出，
工具才会删除 marker 并重启厂家；如果无法证明退出，则保留 marker、禁止厂家重启，
避免双 dialog。自动回滚失败输出 `CRITICAL` 和人工步骤。它绝不 restore vendor
launch。人工步骤也必须先证明所有匹配 Yandex PID 为空；在此之前必须保留 marker，
不得重启厂家。停止 Yandex 后还会执行 rollback preflight；如果切换过程中模式变成
非 `jijia`、配置损坏或 `current_llm` 不再受支持，则停止恢复、保留 marker 且不重启
厂家 dialog。解除 marker 使用共享安全原语：删除紧邻前、删除后、厂家 restart 紧邻
前分别重新解析并比较配置 SHA；任何变化都会原子恢复 marker 并阻止 restart。只有
配置重新证明 `jijia + supported current_llm` 后才允许解除 gate。

## 17. 故障矩阵

| 故障 | 行为 |
|---|---|
| gate target missing/diverged/anchor 非唯一 | 停止，不写 target |
| gate replace 前 original SHA 漂移 | 保留外部修改，拒绝覆盖 |
| backup/current SHA 漂移 | 拒绝 restore |
| env/config/marker symlink、owner/mode/parent 不安全 | preflight FAIL |
| env 缺失、重复/未知变量、`export`、placeholder | preflight FAIL |
| user_config 缺失、JSON 损坏、字段为空 | service/switch/rollback/verify/launch FAIL |
| `robot_current_mode != jijia` | 禁止 switch/Yandex start；fail closed |
| `current_llm` 不属于两个厂家 backend | 禁止 rollback/解除 marker |
| marker 后机器人模式变化 | 停止 Yandex；保留 marker；不重启厂家；CRITICAL |
| `speaker_pcm_format` 空 | preflight FAIL；节点本身也 fail-fast |
| ROS graph 不可统计 | FAIL，不声明唯一 |
| 厂家和 Yandex 进程并存 | mutual-exclusion FAIL |
| orphan Yandex process | service/switch preflight FAIL |
| rollback 无法证明 Yandex process 已退出 | 保留 marker；不重启厂家；CRITICAL |
| marker replace 后目录 fsync 失败 | 视为 post-marker failure；一次自动回滚 |
| marker 后厂家 restart 失败 | 一次自动回滚 |
| transition 验证失败 | 不启动 Yandex；一次自动回滚 |
| Yandex start/yandex-mode 验证失败 | 一次自动回滚 |
| 自动回滚失败 | CRITICAL + 人工恢复步骤 |
| service active 但节点 `STATUS_ERROR` | process/graph 仍不足；人工检查 `/dialog/status` |
| metadata 30 秒无帧 | 明确非零 timeout；不保存 payload |

## 18. 运行状态与 ready 语义

`systemctl is-active` 只证明进程存活。当前节点在 Yandex transport runtime error 后
可能保持进程但发布 `STATUS_ERROR`。Phase 8 必须订阅 `/dialog/status`：

```text
start_session 返回 scheduled ≠ ready
STATUS_LISTENING = ready evidence
STATUS_ERROR = failure evidence
```

## 19. Phase 8 前置条件

Gate 7 的以下条件必须在单独授权的 Phase 8 中实机验证，不得视为已验证事实：

1. `PcmAudioFrame.format`；
2. 厂家实际发布 rate/channels；
3. speaker 输入兼容性；
4. `hw:0,0` 实际采集；
5. speaker / 嘴型 / flush；
6. `session_active` 精确时序；
7. 真实 Yandex 网络和凭据；
8. 正式 switch / rollback。

Phase 8 还必须安排维护窗口和独立远程恢复路径，完成 ROS2/vendor overlay 的真实
build/import/launch，使用最小权限凭据且不写入 Git，先执行 dry-run 与真实
preflight，并准备人工 rollback 步骤。上述要求不表示 Phase 8 已开始。

## 20. 未知和保留项

以下仍为 **UNKNOWN / DEFERRED / CONDITIONAL**：

- `PcmAudioFrame.format`；
- 厂家实际发布 rate/channels；
- speaker 输入兼容性，包括接受的 rate/channels、resample 与 mono→stereo；
- `hw:0,0` 实际采集；
- speaker / 嘴型 / flush；
- `session_active` 精确时序；
- 真实 Yandex 网络和凭据；
- 正式 switch / rollback。

## 21. 本地验证证据

2026-08-14 当前工作树的 82 项 `test_deployment_*.py` 全部通过，覆盖 dry-run、
semantic patch/restore、SHA/TOCTOU/fsync 故障、rooted file 安全、PID/MainPID、共享
事务锁、marker post-replace 回滚、user_config fail-closed/起止快照/安全 ungate/
中途模式漂移、strict config/env、metadata cleanup 和 secret 输出边界。完整回归矩阵
以本轮最终报告中的 fresh verification 为准。

## 22. Gate 7 最终结论

```text
Phase 7 — COMPLETE
Gate 7 — CONDITIONAL PASS
Phase 8 — NOT STARTED
```

理由：本地部署架构、原子 vendor gate、三态互斥检查、独立 service/venv、有限
switch/rollback、metadata 最小采集和 fake-root 测试已经完成复审。Gate 7 的
`CONDITIONAL PASS` 只覆盖 Phase 7 本地设计与测试；第 19、20 节列出的全部真实
ROS2、metadata、speaker/mic、凭据、Yandex 和机器人切换/回滚事实仍留在 Phase 8。
切换前 `speaker_pcm_format` 必须取得证据，未知值不得猜测。Phase 8 尚未开始。

## 23. 安全声明

本 Phase 仅在本地仓库和临时目录工作。没有 SSH、机器人写操作、真实 systemd
操作、真实 ROS graph/mic 操作、真实 Yandex 调用、真实密钥、Authorization 输出或
真实音频保存。Phase 8 未开始。

## 24. Phase 8 field-discovered repair note（2026-08-16）

本节只追加用户提供的 Phase 8 实机事实和本地仓库修复，不改写 Phase 7 当时的
设计、测试数量或 Gate 结论。

**Phase 8 实机事实（由本轮任务提供）：**

- Phase 8C 已完成固定源码上传、独立 venv、aiohttp 隔离、ROS2 build 和安装产物
  验证；Yandex runtime 尚未启动。
- 厂家首个 `/audio/dialog_play` frame metadata 为 `sample_rate=24000`、
  `channels=1`、`format=pcm_s16le`；speaker 实际出声已确认，嘴型未确认。
- 机器人标准 `venv` 因 ensurepip 缺失不可用；现场采用
  `python3 -m venv --without-pip --system-site-packages`，再以系统 pip 仅作为安装器，
  通过 `pip --target <venv purelib>` 安装 aiohttp。venv 中的 `rclpy`、
  `lingze_msgs`、`aiohttp` 导入路径已核对，系统 Python 仍无法导入 aiohttp。
- build preflight 在严格 shell 中 source `/opt/tros/humble/setup.bash` 时，因外部
  ament setup 读取未定义的 `AMENT_TRACE_SETUP_FILES` 而触发 `unbound variable`。
  A/B 复核证明临时关闭 nounset 后 setup 可正常加载；这不是 Yandex、ROS package
  或 colcon build 失败。

仓库的 Phase 8C repair 在共享 runtime loader 中集中定义安全 source helper：只在
source 外部 setup 的动态范围内放宽 nounset，在当前 shell 保留环境副作用，传播
setup 的返回码和 stderr，并恢复调用者原 nounset 状态。preflight、switch、rollback、
verify、metadata probe 通过共享 loader 获得修复；独立 launch path 也复用同一 helper。
仓库修复已通过本地测试，但新部署资产尚未重新同步，机器人 build preflight 尚未
复验。

当前边界为：

```text
Phase 7 — COMPLETE
Gate 7 — CONDITIONAL PASS
Phase 8 — IN PROGRESS
Phase 8C — ROBOT BUILD COMPLETE; CONTROL-PLANE REVALIDATION PENDING
Robot Yandex runtime — NOT STARTED
Formal switch — NOT STARTED
Formal rollback — NOT STARTED
```

厂家 launch、marker、service 和 dialog 未被切换或修改。`hw:0,0` 实际打开、嘴型、
flush、`session_active` 精确时序、真实 Yandex 凭据/连接及正式 switch/rollback 仍待
后续单独授权验证；本轮不进入 Phase 8D。
