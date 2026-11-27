# 宿主能力（host_execute / host_read / host_write）与强制 HITL 审批设计

> 状态：已按推荐默认项实现（2026-11-01）  
> 目标：沙箱缺少运行环境时，允许 Agent 在**本机**执行命令 / 读写项目外文件，但每一次宿主操作都必须经过人工审批。  
> 原则：**默认关闭、能力最小化、审批强制、路径与命令双重校验**。

---

## 一、当前现状（已核查）

| 能力 | 当前落点 | 结论 |
|---|---|---|
| 文件读写 | `ProjectFilesBackend`（`virtual_mode=True`） | 只能在当前项目目录内；`..`、`~`、盘符绝对路径、`.commonharness` 均被拒绝 |
| 项目外文件 | 无通用文件工具 | 不能读、不能写 |
| 命令执行 | `ProjectWorkspaceBackend.execute` | 只走沙箱；没有沙箱时直接 `NotImplementedError`，不会回退本机 |
| `run_in_sandbox` | 沙箱专用 | 只能上传项目内文件到沙箱 |
| `publish_to_project` | 项目内写入 | 目标只能是项目相对路径 |
| `read_word_document` | 宿主读取 | 仅限 `CONTEXT_ROOT`（用户上传附件） |
| `get_system_info` | 宿主 `subprocess` | 命令硬编码，模型不能拼接；**当前无审批** |
| MCP 工具 | 宿主 MCP Server | 能力由配置决定；**当前无审批** |
| HITL | `request_additional_info` / `request_user_choice` | 只有模型主动调用才中断；没有工具级强制审批 |

**结论：现在不存在“超出沙箱后自动回退本机”的链路**。因此用户问的“是否必须审批”目前没有实际分支——执行层直接拒绝。要开放这条链路，必须新增工具 + 强制审批。

框架侧具备的条件：

- `deepagents.create_deep_agent` 支持 `interrupt_on: dict[str, bool | InterruptOnConfig]`，会自动挂载 `HumanInTheLoopMiddleware`。
- `InterruptOnConfig` 支持 `allowed_decisions`（`approve / edit / reject / respond`）、`description`、`when` 谓词。
- 顶层 `interrupt_on` 会被声明式子 Agent 继承；**异步子 Agent（Agent Protocol）不继承**。
- 前端已能渲染 `hitl_approval`，但目前只有“全部批准 / 全部拒绝”，没有逐条 edit。

---

## 二、目标与非目标

### 目标
1. 沙箱环境缺失（例如沙箱里没有 Node/Python/Java/ffmpeg）时，Agent 可以请求在本机执行等价命令。
2. 需要读取项目外文件（例如用户指定 `D:\需求\xxx.docx`）时，Agent 可以请求本机读取。
3. 需要把结果写到项目外路径时，Agent 可以请求本机写入。
4. 上述三类操作**每一次都强制暂停**，由用户批准/拒绝/仅当前会话允许/后才执行。
5. 未开启时，工具对模型不可见，现有行为完全不变。
6. 如果是进当前会话允许的，就意味着，当前会话的每一轮，针对该命令，就不需要用户审批，进入了白名单，但是换了会话依旧需要提问。

### 非目标
1. 不做“静默回退”：沙箱失败后不会自动在本机执行，必须由模型显式发起并经审批。
2. 不开放任意 shell 的“免审批白名单”；审批是硬边界。
3. 不解决 MCP 工具的审批问题（单独在决策点中讨论）。

---

## 三、总体设计

```
模型
  ↓ 调用 host_execute / host_read_file / host_write_file
HumanInTheLoopMiddleware（interrupt_on）
  ↓ 生成 action_requests，LangGraph interrupt（checkpoint 落库）
前端 InterruptCard 展示命令/路径/参数
  ↓ 用户 approve / reject / only_current_thread
恢复执行
  ↓
host_tools.py 内部二次校验（路径、大小、超时、进程树）
  ↓
宿主执行 / 读取 / 写入
```

三道防线：

1. **可见性**：开关关闭时不注册工具，模型看不到。
2. **审批**：`interrupt_on` 强制暂停，未批准不执行。
3. **工具内校验**：即使审批通过，仍做路径越界、系统目录、大小、超时校验；校验失败直接返回错误。

---

## 四、工具契约

### 4.1 `host_execute`

```python
host_execute(
    command: str,              # 要执行的命令，原样展示给审批人
    reason: str,               # 为什么沙箱无法满足（必填）
    timeout: int = 120,        # 秒，最大 600
    shell: str | None = None,  # powershell / cmd / bash；默认按平台
) -> dict
```

- cwd：**固定为当前项目根目录**；自由会话（无项目）不允许调用。
- Windows 默认 `powershell.exe -NoProfile -NonInteractive -Command <command>`；Linux/macOS 默认 `bash -lc`。
- 输出：`{ok, exit_code, stdout, stderr, truncated, duration_ms, cwd, shell}`。
- stdout/stderr 合计截断到 `HARNESS_HOST_EXEC_MAX_OUTPUT`（默认 30000 字符）。
- 超时：杀进程树（Windows `taskkill /f /t`），返回 `timeout` 错误。
- 不接受交互式命令；不注入额外环境变量，但会剥离常见敏感变量（可选）。

### 4.2 `host_read_file`

```python
host_read_file(
    path: str,                 # 绝对路径（也允许项目相对路径）
    reason: str,
    offset: int = 0,           # 行偏移
    limit: int = 2000,         # 行数
) -> dict
```

- 默认只允许文本；二进制返回 `binary_not_supported`。
- 单文件读取上限 `HARNESS_HOST_READ_MAX_MB`（默认 10MB）。
- 校验：
  - `Path.resolve()` 后必须是普通文件；
  - 拒绝符号链接 / 目录联接；
  - 拒绝 `.commonharness`；
  - 拒绝系统目录与凭据目录（见第六节）。
- 返回：`{ok, path, size, content, truncated}`。

### 4.3 `host_write_file`

```python
host_write_file(
    path: str,                 # 绝对路径
    content: str | None,       # 直接内容
    source_path: str | None,   # 或从项目/沙箱文件复制
    reason: str,
    overwrite: bool = False,   # 默认不覆盖
) -> dict
```

- `content` 与 `source_path` 二选一。
- 目标已存在且 `overwrite=False` → 返回 `already_exists`，不写。
- 拒绝系统目录、盘符根、`.commonharness`、可执行文件扩展名（可选配置）。
- 父目录不存在时不自动创建；需要用户批准时明确展示。
- 写入前可选做备份（P1）：`<file>.bak-<timestamp>`。
- 单文件上限 `HARNESS_HOST_WRITE_MAX_MB`（默认 10MB）。

> `host_list_dir` / `host_glob` 暂不实现。读取目录清单可用 `host_read_file` 之外的后续迭代补充；P0 保持三个工具。

---

## 五、审批接线

在 `src/agent/main_agent.py` 构图时按开关注入：

```python
host_tools = build_host_tools(...)          # 开关关闭时返回 []
host_interrupt_on = {
    "host_execute": InterruptOnConfig(
        allowed_decisions=["approve", "reject"],   # P0；P1 再加 "edit"
        description="本机执行命令需要审批",
    ),
    "host_read_file": InterruptOnConfig(
        allowed_decisions=["approve", "reject"],
        description="读取项目外文件需要审批",
    ),
    "host_write_file": InterruptOnConfig(
        allowed_decisions=["approve", "reject"],
        description="写入项目外文件需要审批",
    ),
}
create_deep_agent(
    tools=[*main_tools, *host_tools],
    interrupt_on=host_interrupt_on if host_tools else None,
    ...
)
```

要点：

- `interrupt_on` 只对已注册的工具生效；工具不存在时不要传空配置。
- 顶层 `interrupt_on` 会被同步声明式子 Agent 继承；**异步子 Agent 不继承**，因此不要把 host 工具注入异步子 Agent。
- 前端 `chat.resume` 已支持 `{decisions: [{type:"approve"}|{type:"reject",message}]}`，与 `HumanInTheLoopMiddleware` 协议一致。
- 审批卡片当前展示 `action.name + args JSON`；P0 需要增强为：
  - `host_execute`：高亮 `command`、`cwd`、`timeout`、`reason`；
  - `host_read_file` / `host_write_file`：高亮解析后的绝对路径、文件大小、是否覆盖；
  - 危险操作加红色标记（删除类命令、覆盖已有文件、系统目录）。

---

## 六、路径与命令安全策略

### 6.1 读取拒绝目录（默认）

- `%WINDIR%`、`%ProgramFiles%`、`%ProgramFiles(x86)%`、`%ProgramData%`
- `C:\Users\<user>\.ssh`、`.aws`、`.gnupg`、`.config` 中的凭据目录
- 浏览器/密码库目录（可选）
- 项目内 `.commonharness`
- 盘符根

### 6.2 写入策略

- 默认**禁止项目外写入**；
- 需要写入项目外时，必须配置 `HARNESS_HOST_WRITE_ROOTS`（分号分隔的允许根目录，如 `D:\输出;E:\交付`）；
- 即使审批通过，也不允许写入 6.1 的拒绝目录。

### 6.3 命令策略

- 审批是硬边界，不额外做“命令白名单”以避免误杀；
- 但工具内拒绝明显的高危组合（如 `format`、`shutdown`、`diskpart`、`Remove-Item -Recurse` 等）可作为可配置黑名单；
- 命令字符串原样写入轨迹与审批卡片，不做二次改写。

---

## 七、配置项（建议）

```dotenv
# 总开关；默认 false，关闭时不注册任何 host 工具
HARNESS_HOST_ACCESS_ENABLED=false

# 分项开关（总开关打开后才生效）
HARNESS_HOST_EXEC_ENABLED=true
HARNESS_HOST_READ_ENABLED=true
HARNESS_HOST_WRITE_ENABLED=false

# 执行限制
HARNESS_HOST_EXEC_TIMEOUT=120
HARNESS_HOST_EXEC_MAX_OUTPUT=30000
HARNESS_HOST_EXEC_DENY_COMMANDS=

# 读写限制
HARNESS_HOST_READ_MAX_MB=10
HARNESS_HOST_WRITE_MAX_MB=10
# 允许写入项目外的根目录；为空表示禁止
HARNESS_HOST_WRITE_ROOTS=
# 额外拒绝路径（分号分隔）
HARNESS_HOST_DENY_PATHS=
```

开关在构图时读取；修改后需要触发 `AgentLoader.reset_agents()`（可复用 `/settings/model` 的热更新思路，或新增设置接口）。

---

## 八、需要改动的文件

| 文件 | 改动 |
|---|---|
| `src/agent/tools/host_tools.py`（新增） | 三个工具、路径/大小/超时/进程树校验 |
| `src/agent/config.py` | 新增 host 开关与限制配置 |
| `src/agent/main_agent.py` | 按开关注册工具、传 `interrupt_on`、注入宿主能力提示词 |
| `src/agent/memory/prompts.py` / 执行策略段 | 增加“host 工具是最后手段、必须说明原因、等待审批”规则 |
| `frontend/HarnessControlPlatform/src/components/chat/InterruptCard.vue` | 审批卡片按工具类型高亮参数；P1 增加逐条 edit |
| `src/api/settings.py`（可选） | 暴露 host 开关状态，供设置页展示 |
| `.env.example` | 补充上述配置项 |
| `test/test_host_access.py`（新增） | 路径越界、拒绝目录、超时、拒绝/批准恢复、开关关闭回归 |

---

## 九、实施顺序

1. **P0-1 配置与骨架**：新增开关与 `host_tools.py`，开关关闭时返回空列表。
2. **P0-2 工具实现**：路径校验 → 读取 → 执行 → 写入；全部走 `asyncio.to_thread`，超时杀进程树。
3. **P0-3 审批接线**：`interrupt_on` + 提示词 + 工具可见性；前端审批卡片参数高亮。
4. **P0-4 测试与验收**：单元测试 + 手工审批流；关闭开关回归原有行为。
5. **P1（可选）**：审批卡片逐条 edit、写入前备份、`host_list_dir`。

---

## 十、验收清单

- [ ] 开关关闭：模型工具列表中没有 host 工具；原有沙箱/项目行为不变。
- [ ] 开关打开：`host_execute` 调用 → 会话暂停 → 前端出现审批卡片 → 批准后执行，拒绝后不执行且有拒绝理由。
- [ ] 沙箱缺环境场景：模型先收到沙箱失败，再显式发起 host 工具；审批卡片显示完整命令与 cwd。
- [ ] 路径攻击：`..`、`~`、符号链接、盘符根、`C:\Windows`、`.commonharness` 全部被拒。
- [ ] 写入项目外且未配置 `HARNESS_HOST_WRITE_ROOTS`：直接拒绝。
- [ ] 超时命令：进程树被杀，返回 `timeout`，会话继续可用。
- [ ] 拒绝后：模型收到明确的 tool error，不会自动重试同一命令。
- [ ] 轨迹页：能看到 host 工具调用和审批结果。

---

## 十一、风险与权衡

1. **审批疲劳**：每次宿主操作都停；可通过“同一会话同一命令短时记忆”优化，但会削弱审批边界，建议 P0 不做。
2. **提示词注入**：网页/文档内容可能诱导模型调用 host 工具；审批卡片必须展示**最终参数**，用户需看命令本身，而不是只看模型解释。
3. **Windows 完整性**：host 工具运行在后端进程内；若后端仍是 Low IL，会继续 `WinError 5`。启动自检与顶部告警已具备。
4. **MCP 旁路**：本机 MCP Server 的工具不受本方案约束；如果要求统一治理，需要把 MCP 工具也纳入审批或白名单。
5. **异步子 Agent 不继承审批**：不要把 host 工具注入异步子 Agent，否则审批会被绕过。

---

## 十二、已采用的默认决策

1. **触发方式**：沙箱失败后由模型自行判断并发起 host 工具。
2. **项目外读取范围**：除系统/凭据目录外都可读，但每次审批。
3. **项目外写入范围**：默认禁止；必须配置 `HARNESS_HOST_WRITE_ROOTS`。
4. **审批动作**：`approve`（批准一次）、`approve_thread`（仅本会话允许）、`reject`（拒绝）。逐条 `edit` 留到 P1。
5. **已有宿主入口**：`get_system_info` 与 MCP 工具本轮不改，仍按受信入口处理。

---

## 十三、实现说明（与初稿的差异）

1. **审批没有用 `interrupt_on`，而是在每个工具内部调用 `interrupt()`。**
   原因：`interrupt_on` 只能表达 `approve / edit / reject / respond`，无法表达“仅本会话允许”；工具内中断可以在恢复时把命令/路径 key 写入会话白名单。
2. **会话白名单是进程内结构**：`thread_id -> 命令/路径 key`，上限 200 条；后端重启后清空，换会话不会继承。
3. **“仅本会话允许”的匹配粒度**：
   - `host_execute`：shell + 完整命令 + cwd；
   - `host_read_file`：解析后的绝对路径；
   - `host_write_file`：目标路径 + 是否覆盖 + 内容 sha256（内容变化会重新审批）。
4. **中断类型**：后端新增 `host_approval`，前端 `InterruptCard` 显示完整参数，并提供“批准一次 / 仅本会话允许 / 拒绝”三个按钮。
5. **测试**：`test/test_host_access.py` 覆盖开关、路径拒绝、写根限制、命令黑名单、超时杀进程，以及“中断 → 批准并记住 → 同会话不再中断 → 换会话重新中断”的完整流程。
