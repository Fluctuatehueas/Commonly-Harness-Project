# C# 插件 SDK 设计文档

Time: 2026/11/28（二次修订）  
Target: .NET 10（`net10.0` 单目标）  
Scope: 宏观架构、解决方案与模块分布、宿主对 C# 开放的全部 API 契约、契约治理、发布与安全策略。  
配套文档：[CSharp插件SDK开发文档.md](CSharp插件SDK开发文档.md)（逐模块 C# 代码与实现讲解）。

> 修订说明：初版把「关联模式」写成 HTTP JSON-RPC 远程插件，与事实不符。
> 真实的关联模式是 `src/protocals/.protoes/*.proto` 定义的 **gRPC 双向契约**
> （C# 承载 `ToolService`，Python 承载 `ChatService` 等）。本版据此重写，
> 并以「SDK 携带 proto、统一 `[HarnessTool]`」为基线。

---

## 1. 定位与三种接入形态

`Harness.Plugin.Sdk` 是 C# / .NET 开发者接入 Harness 的官方类库。它同时覆盖两条
互不替代的集成面：

| 形态 | 契约 | 传输/拓扑 | 谁调用谁 | 适用场景 |
| :-- | :-- | :-- | :-- | :-- |
| **A. 插件模式** | manifest + `plugin.describe`（JSON） | stdio，宿主拉起 `dotnet Plugin.dll` | 宿主 → 插件，单向 | 工具包、市场分发、无界面能力 |
| **B. 契约关联模式** | `.proto` 生成的 gRPC 桩 | gRPC（h2c），双方各自监听 | 双向：C#→Python Chat；Python→C# Tools | WPF/桌面宿主整体接入 Agent 后端 |
| **C. 轻量远程插件** | manifest + describe（JSON） | HTTP JSON-RPC，单端点 | 宿主 → C# 服务，单向、无反向调用 | 已有 HTTP 服务想暴露少量工具 |

A 与 B 的区别不是传输细节，而是**应用级还是工具级**：

| 维度 | A 插件模式 | B gRPC 契约关联 |
| :-- | :-- | :-- |
| 契约权威源 | manifest + describe | `.proto` |
| 工具发现 | 握手时 describe 一次 | `ListTools` 随时拉取 |
| 工具分组 | 扁平 | `SkillDefinition` + `prompt_fragment` |
| 审批 | 无；靠 manifest permissions | `NEEDS_APPROVAL` → `ResolveApproval`（300s、单次、EDIT） |
| 幂等 | 无 | `call_id` 缓存 600s |
| 轨迹 | 宿主中间件 | C# 在 `ToolCallResult.trajectory` 上报 |
| 会话/文件/能力发现 | 由宿主 REST 提供 | `SessionService` / `FileService` / `HarnessService` |
| 隔离 | 子进程 + 静态扫描 + 沙箱试运行 | 本机回环，无进程隔离，C# 是受信宿主 |

SDK **不负责**：

- 不安装插件、不下载依赖、不改宿主环境。
- 不自动重试 `tool.call`，也不重试 `CallTool`（业务可能非幂等）。
- 不读取宿主环境变量；插件凭据只来自 `host.secret.get`。
- 不实现 Agent 循环；C# 桥接层只做工具与 UI，不做规划。
- 不保证进程被强杀时的清理；持久状态写 `host.storage` 或业务侧存储。

---

## 2. 与宿主模块的映射

### 2.1 形态 A / C：插件总线

| SDK 部件 | 宿主对应模块 | 契约来源 |
| :-- | :-- | :-- |
| `Protocol/RpcPeer` | [transport/stdio.py](../../../src/plugin_bus/transport/stdio.py)、[transport/http.py](../../../src/plugin_bus/transport/http.py) | 一行一条 JSON-RPC 2.0 |
| `PluginHost` | [lifecycle.py](../../../src/plugin_bus/lifecycle.py) | initialize → describe → health |
| `HarnessToolRegistry`（元数据层） | [registry.py](../../../src/plugin_bus/registry.py) | describe.capabilities |
| `HostClient` | [permissions.py](../../../src/plugin_bus/permissions.py) | `host.*` 白名单 |
| `PluginPack`（打包工具） | [installer.py](../../../src/plugin_bus/installer.py)、[scanner.py](../../../src/plugin_bus/scanner.py) | 包结构与体积 |

### 2.2 形态 B：gRPC 契约关联

C# 侧（受信宿主，例如 WPF 编辑器）与 Python 侧（Agent 后端）的既定分工：

| 层 | 归属 | 职责 |
| :-- | :-- | :-- |
| UI | C# | 输入、会话列表、审批卡片、轨迹与计划渲染 |
| 业务能力 | C# | 工程文件、场景、Undo/Redo、业务工具实现 |
| 桥接层 | C#（本 SDK） | `[HarnessTool]` 注册、`ToolService` 服务端、Chat/Session 等客户端 |
| Agent 后端 | Python | LLM、规划、工具选择、上下文、记忆 |
| 持久化 | Python | 会话、轨迹、技能元数据 |

固定角色（不可反转）：

| RPC | Python | C# | 形态 |
| :-- | :-- | :-- | :-- |
| `ChatService.Chat` / `ResumeChat` | Server | Client | server-streaming |
| `ToolService.ListTools` / `CallTool` / `ResolveApproval` | Client | **Server** | unary |
| `SessionService.*` | Server | Client | unary |
| `TrajectoryService.GetTrajectory` | Server | Client | unary |
| `HarnessService.*` | Server | Client | unary |
| `FileService.*` | Server | Client | unary |

> 现状提示：CommonProject 目前没有 gRPC 层，完整链路只存在于
> `Live2DEditorHarness/Commonly-Harness-Project/src/protocals`。Python 对端的
> 通用性评估与分层设计见 §9。

---

## 3. 解决方案与项目结构

```text
src/sdk/csharp/
  Harness.Plugin.Sdk.sln
  Directory.Build.props
  proto/                                # 唯一权威契约（SDK 携带、NuGet 分发）
    Agent.proto                         # Chat + Tool + 消息/工具/轨迹/计划/HITL
    Session.proto
    Trajectory.proto
    Harness.proto
    File.proto
    contract.version                    # 契约版本单一来源，如 1.3.0
  src/
    Harness.Plugin.Sdk/                 # 核心：元数据 + 插件模式，零第三方依赖
      Metadata/                         # [HarnessTool]、注册表、Schema、参数绑定、结果模型
      Protocol/                         # JSON-RPC 信封、RpcPeer、错误码
      Hosting/                          # PluginHost、HostClient、Dispatcher
      Contracts/                        # manifest、PluginInitRequest、契约文件校验
      Bridge/                           # 包装已有 exe/控制台程序
    Harness.Plugin.Sdk.Grpc/            # 契约关联模式
      buildTransitive/Harness.Plugin.Sdk.Grpc.props   # 自动注入 <Protobuf>
      Server/                           # ToolGrpcService、HarnessToolServer、ToolApprovalBroker
      Client/                           # Chat/Session/Trajectory/Harness/File 客户端
      Models/                           # ChatEvent、审批快照、DTO 映射
    Harness.Plugin.Sdk.Tools/           # dotnet tool：pack / validate / proto-sync
  samples/
    EchoPlugin/                         # 形态 A 最小插件
    StoragePlugin/                      # 形态 A + host.storage
    ConsoleBridgePlugin/                # 形态 A + 外部进程包装
    GrpcToolHost/                       # 形态 B 最小 ToolService（控制台）
    WpfHostSample/                      # 形态 B 接入示例（可选）
  tests/
    Harness.Plugin.Sdk.Tests/
    Harness.Plugin.Sdk.Grpc.Tests/
```

拆分原则：

- **元数据层只有一份**：`[HarnessTool]`、Schema 生成、参数绑定、`ToolResult`
  同时服务 A/C 的 `describe` 与 B 的 `ToolDescriptor`，避免两端字段漂移。
- 核心包零第三方依赖；gRPC 相关依赖全部收敛在 `Harness.Plugin.Sdk.Grpc`。
- `.proto` 不复制到业务工程长期维护：由 NuGet 包内的 `buildTransitive` props 自动挂载。
- `Sdk.Tools` 只在开发机执行，不进运行时依赖。

包 ID（暂定）：`Harness.Plugin.Sdk`、`Harness.Plugin.Sdk.Grpc`、`Harness.Plugin.Sdk.Tools`。

---

## 4. 模块分布与依赖方向

```text
                 ┌──────────────────────────────┐
                 │  Harness.Plugin.Sdk.Tools    │ pack / validate / proto-sync
                 └──────────────┬───────────────┘
                                │
                 ┌──────────────▼───────────────┐
                 │  Harness.Plugin.Sdk.Grpc     │ 形态 B：ToolService + 客户端
                 └──────────────┬───────────────┘
                                │
┌────────────────────────────────▼─────────────────────────────────┐
│                       Harness.Plugin.Sdk                          │
│  Metadata（特性/Schema/绑定/结果） ─► Hosting ─► Protocol          │
│                                        └─► Bridge                │
│  Contracts（manifest / 契约文件）                                  │
└───────────────────────────────────────────────────────────────────┘
```

| 模块 | 关键类型 | 职责 |
| :-- | :-- | :-- |
| `Metadata/HarnessToolAttribute` | 特性 + `SandboxLevel`/`HighlightTarget` | 两种形态共用的工具声明 |
| `Metadata/HarnessToolRegistry` | `HarnessToolRegistry`、`ToolRegistration` | 扫描、命名校验、技能分组、Schema 生成 |
| `Metadata/ToolContext` | `ToolContext`、`IHostClient` | 工具注入上下文；`Host` 为接口，避免元数据层依赖传输 |
| `Metadata/JsonSchemaBuilder` | `JsonSchemaBuilder` | CLR → JSON Schema（对齐宿主 pydantic 与 gRPC `args_schema_json`） |
| `Metadata/ToolArgumentBinder` | `ToolArgumentBinder` | 单 DTO 整包绑定 + 按参数名绑定 |
| `Metadata/ToolResult` | `ToolResult`、`ToolError`、`TrajectoryEvent` | 统一结果模型；插件模式转 `{content}` |
| `Protocol/RpcPeer` | `RpcPeer` | 形态 A 的行协议、id 关联、并发写锁 |
| `Hosting/PluginHost` | `PluginHost`、`PluginDispatcher`、`HostClient` | 形态 A 的握手、分发、`host.*` |
| `Contracts/ManifestInfo` | `ManifestInfo` | 读取并复核 manifest |
| `Contracts/ContractDocument` | `ContractDocument`、`ContractValidator` | 离线契约导出与漂移校验 |
| `Grpc/Server/ToolGrpcService` | `ToolGrpcService` | ListTools / CallTool / ResolveApproval |
| `Grpc/Server/ToolApprovalBroker` | `ToolApprovalBroker` | 审批事件与决策注入 |
| `Grpc/Server/HarnessToolServer` | `HarnessToolServer` | Kestrel h2c 宿主（默认 50052） |
| `Grpc/Client/HarnessChatClient` | `HarnessChatClient` | Chat/ResumeChat 流式客户端（默认 50051） |
| `Grpc/Client/*` | Session/Trajectory/Harness/File 客户端 | 其余只读与管理面 |

依赖规则：

- `Metadata` 只依赖 BCL、`System.Text.Json`、`Contracts` 与自身声明的 `IHostClient`
  接口；不感知 stdio / gRPC 传输。
- `Hosting` 依赖 `Metadata`，实现 `IHostClient`；`Grpc` 依赖 `Metadata` 与生成桩。
- `Protocol` / `Hosting` 只服务形态 A/C。
- `Grpc` 依赖核心包与 `Google.Protobuf`、`Grpc.*`；核心包反向不依赖 gRPC。
- `Bridge` 是唯一允许创建子进程的模块。

---

## 5. 运行时时序

### 5.1 形态 A：插件握手与工具调用

```text
宿主 LifecycleManager                    C# 插件进程
      │ Popen(dotnet Plugin.dll)              │
      │──────────────────────────────────────►│ Main：抢占 stdout，Console.Out→stderr
      │ {"plugin.initialize", params}         │
      │──────────────────────────────────────►│ OnInitializeAsync
      │◄──────────────── result {} ───────────│
      │ {"plugin.describe"}                   │
      │──────────────────────────────────────►│ 注册表 → capabilities
      │◄──── result {capabilities} ───────────│ 宿主 build_snapshot
      │ {"plugin.health"} 每 30s               │
      │◄──────────────── {"status":"ok"} ─────│
      │ {"tool.call",{name,arguments}}        │
      │──────────────────────────────────────►│ 绑定 → 业务
      │                                       │──► host.storage.get ─► HostBridge
      │◄──── result {"content":"..."} ────────│
      │ close：terminate()（不发 shutdown）     │ stdin EOF → 退出
```

### 5.2 形态 B：工具发现与调用

```text
Python ToolService 客户端                 C# ToolService（50052）
      │ ListTools{contract_version}           │
      │──────────────────────────────────────►│ 版本主号校验 → 技能分组
      │◄──── skills[{name,tools[descriptor]}] │
      │ CallTool{tool_name,args,call_id}      │
      │──────────────────────────────────────►│ call_id 幂等查缓存
      │                                       │ 需要审批 → NEEDS_APPROVAL
      │◄──── status=NEEDS_APPROVAL ───────────│   + approval_id + hitl 轨迹
      │ ResolveApproval{approval_id,decision} │
      │──────────────────────────────────────►│ DecisionProvider / 默认决策
      │                                       │ 执行（超时 + 取消）
      │◄──── OK/DENIED/ERROR + trajectory ────│
```

关键语义（与现有实现一致，SDK 内建）：

- `contract_version` 主版本必须一致；`ListTools` 用 `FailedPrecondition`，其余用错误结果。
- `call_id` 幂等缓存 600s；同 id 不同参数返回 `CALL_ID_CONFLICT`。
- 审批记录单次使用、300s 过期；`EDIT` 仅在 `supports_edit_args` 为真时允许。
- 被拒绝必须回 `DENIED`，模型不得自动重试或换工具绕过。
- 工具异常不穿透 gRPC，统一转 `ERROR + ToolError{code,message,hint,retryable}`。
- `DurationMs` 与 `trajectory` 由服务端填充。

### 5.3 形态 B：对话流

```text
C# HarnessChatClient                    Python ChatService（50051）
      │ Chat{humanmsg,thread_id,mode}        │
      │──────────────────────────────────────►│
      │◄──── ChatChunk.chunk（文本增量） ──────│
      │◄──── ChatChunk.tool_call ─────────────│ 流式参数片段（is_partial）
      │◄──── ChatChunk.tool_result ───────────│
      │◄──── ChatChunk.trajectory / plan ─────│
      │◄──── ChatChunk.hitl ──────────────────│ 中断，等待 ResumeChat
      │◄──── ChatChunk.done ──────────────────│ 必须显式结束
```

---

## 6. 宿主对 C# 开放的全部 API

### 6.1 形态 A/C：JSON-RPC 协议（摘要）

| 方法 | params | result | 备注 |
| :-- | :-- | :-- | :-- |
| `plugin.initialize` | `protocolVersion`、`plugin_id`、`version`、`user_id`、`permissions`、`config` | 任意 | 主版本不符 `-32006` |
| `plugin.describe` | `{}` | `{plugin_id,version,protocolVersion,capabilities}` | 能力类型必须是 manifest 子集 |
| `plugin.health` | `{}` | 任意 | 5s 超时 |
| `tool.call` | `{name,arguments}` | `string` 或 `{content}` | 60s 超时 |
| `resource.list` / `resource.read` | `{}` / `{uri}` | 列表 / 正文 | |
| `prompt.get` | `{id}` | `{content}` | 总注入 ≤2000 字符 |
| `hook.invoke` | `{hook,payload}` | `{allow,reason}` / `{content}` | 3s，失败降级 |
| `event.notify` | 任意 | 无需响应 | 当前仅冒烟使用 |

反向白名单：`host.storage.get/set/list/delete`（`storage`）、`host.secret.get`（`secret`）、
`host.log`、`host.progress`。错误码 `-32001 ~ -32010` 见 [protocol.py](../../../src/plugin_bus/protocol.py)。

### 6.2 形态 B：gRPC 契约（全量）

**服务与 RPC**

| 服务 | RPC | 请求 → 响应 |
| :-- | :-- | :-- |
| `ChatService` | `Chat` | `ChatRequest` → `stream ChatChunk` |
| | `ResumeChat` | `ResumeChatRequest` → `stream ChatChunk` |
| `ToolService` | `ListTools` | `ListToolsRequest` → `ListToolsResponse` |
| | `CallTool` | `ToolCallRequest` → `ToolCallResult` |
| | `ResolveApproval` | `ResolveApprovalRequest` → `ToolCallResult` |
| `SessionService` | `ListSessions` / `GetSessionHistory` / `CreateSession` / `RenameSession` / `SetSessionPinned` / `DeleteSession` | 见 `Session.proto` |
| `TrajectoryService` | `GetTrajectory` | `GetTrajectoryRequest` → `GetTrajectoryResponse` |
| `HarnessService` | `GetCapabilities` / `GetModelSettings` / `UpdateModelSettings` / `ListSkills` / `GetSkill` / `ListSubagents` / `ListMcpServers` / `ListContextFormats` | 见 `Harness.proto` |
| `FileService` | `ListThreadFiles` / `ReadThreadFile` / `WriteThreadFile` | 见 `File.proto` |

**枚举**

| 枚举 | 取值 | 语义 |
| :-- | :-- | :-- |
| `ChatMode` | `UNSPECIFIED/CHAT/TASK` | 只有 TASK 允许调用工具 |
| `MessageRole` | `SYSTEM/HUMAN/AI/TOOL` | 与 LangChain 对齐 |
| `ChatStatus` | `OK/INTERRUPTED/ERROR/CANCELLED` | `done` 携带 |
| `ToolStatus` | `OK/ERROR/DENIED/NEEDS_APPROVAL/CANCELLED` | `NEEDS_APPROVAL` 不是失败 |
| `SandboxLevel` | `AUTO/CONFIRM/ALWAYS_CONFIRM` | 是否需要审批的唯一依据 |
| `ApprovalDecision` | `UNSPECIFIED/APPROVE/REJECT/EDIT` | `UNSPECIFIED` 交给 C# 决策方 |

**关键消息**

| 消息 | 关键字段 | 约束 |
| :-- | :-- | :-- |
| `ToolDescriptor` | `name, display_name, description, domain, skill, args_schema_json, result_schema_json, sandbox_level, is_write, is_destructive, supports_edit_args, timeout_ms, version` | `name` 为 snake_case；Schema 必须是 JSON 对象 |
| `SkillDefinition` | `name, display_name, description, prompt_fragment, tools, metadata_json` | 工具分组；提示词片段可空 |
| `ListToolsRequest/Response` | `contract_version, thread_id` / `contract_version, skills` | 主版本校验 |
| `ToolCallRequest` | `tool_name, arguments_json, thread_id, call_id, run_id, mode, trace_id, contract_version` | `call_id` 幂等键 |
| `ToolCallResult` | `success, result_json, error, status, approval_id, call_id, trajectory, duration_ms, detail` | `success` 必须由 `status==OK` 推导 |
| `ToolError` | `code, message, hint, retryable` | 不抛裸异常 |
| `ChatRequest` | `humanmsg, thread_id, mode, task_json, client_context_json, history, contract_version, user_id, username` | `thread_id` 由 C# 生成 |
| `ResumeChatRequest` | `thread_id, resume_json, user_id, username, contract_version, mode` | HITL 恢复 |
| `ChatChunk` | oneof：`chunk/tool_call/tool_result/done/message/trajectory/error/reasoning/hitl/plan` | 新增分支只能追加编号 |
| `ToolCallEvent` | `thread_id, tool_name, arguments_json, call_id, run_id, is_partial` | `is_partial=true` 时客户端按 `call_id` 累积 |
| `HitlEvent` | `hitl_id, kind, title, description, fields[], options[], allow_multiple, allow_free_text, submit_label, cancel_label, resume_schema_json, metadata_json` | UI 直接渲染 |
| `PlanEvent` / `PlanStepInfo` | `plan_id, version, steps[], replan_reason, status` | 全量快照非增量 |
| `TrajectoryEvent` | `event_id, run_id, thread_id, parent_id, type, name, status, args_json, result_json, started_at, ended_at, metadata_json` | C# 只补业务侧事件 |

**错误码（gRPC 层 `ToolError.code`）**

| code | 触发 | 处理建议 |
| :-- | :-- | :-- |
| `CONTRACT_VERSION_MISMATCH` | 主版本不同 | 重新生成两端桩 |
| `TOOL_NOT_FOUND` | 未注册 | 先 `ListTools`，禁止猜名 |
| `INVALID_ARGUMENT` | JSON 非法/缺参 | 按 Schema 修正 |
| `TIMEOUT` / `CANCELLED` | 超时或取消 | 检查是否响应 CancellationToken |
| `TOOL_EXCEPTION` | 未捕获异常 | 修实现；不自动重试 |
| `APPROVAL_REQUIRED` | 需要审批 | 调 `ResolveApproval` |
| `APPROVAL_DENIED` / `APPROVAL_EXPIRED` / `APPROVAL_NOT_FOUND` | 审批失败 | 如实告知，不绕过 |
| `EDIT_NOT_SUPPORTED` | 未声明可编辑 | 改用 APPROVE/REJECT |
| `CALL_ID_CONFLICT` | 同 id 不同参数 | 生成新 `call_id` |

### 6.3 manifest 契约（形态 A/C）

必填：`plugin_id`（`^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`）、`name`、`version`（semver）、
`protocolVersion`（主版本 1）、`author.name`、`capabilities`（非空）、`runtime`、`transport`、
`permissions`、`harnessVersion`（覆盖当前宿主）。

C# 框架依赖发布的标准写法：

```json
{
  "runtime": { "kind": "subprocess", "language": "dotnet",
               "command": "dotnet", "args": ["YourPlugin.dll"] },
  "transport": { "kind": "stdio" },
  "env": { "dotnet": ">=10.0" }
}
```

### 6.4 管理面 REST（可选）

`/plugins` 下提供安装、上传、启停、重启、升级、卸载、配置、健康、任务进度、审计、
市场目录、UI 静态资源；完整列表见 [api/plugins.py](../../../src/api/plugins.py)。
形态 B 不经过插件总线，无对应 REST，由 Python 侧自行登记服务地址。

### 6.5 进程与网络约束

- 形态 A：env 白名单仅 `PATH/LANG/LC_ALL/TMPDIR/TEMP/TMP/SYSTEMROOT`；`cwd` = 版本目录；
  单行 ≤8 MB；包 ≤32 MB / 5000 文件；安装时在 Linux 沙箱试运行。
- 形态 B：`127.0.0.1:50052`（C# ToolService）与 `127.0.0.1:50051`（Python Chat）均为
  **h2c（HTTP/2 无 TLS）**；Kestrel 必须显式 `HttpProtocols.Http2`，客户端用 `http://`。
- 形态 B 需要目标机安装 ASP.NET Core Runtime（`FrameworkReference`）与 .NET 10 运行时。

---

## 7. 契约治理

### 7.1 唯一权威源

- `.proto` 只存在于 `src/sdk/csharp/proto/`；Python 侧通过同步脚本获取（`Sdk.Tools proto-sync`），
  不手工维护第二份。
- 禁止把 `.proto` 复制进业务工程长期维护；业务工程只引用 NuGet 包，由
  `buildTransitive/Harness.Plugin.Sdk.Grpc.props` 自动注入 `<Protobuf>`。

### 7.2 版本

| 版本 | 载体 | 规则 |
| :-- | :-- | :-- |
| 契约版本 `contract_version` | `proto/contract.version` + C# 常量 + Python 常量 | 主版本必须一致；minor/patch 仅提示 |
| SDK 包版本 | NuGet | 与契约版本解耦：SDK 1.x 可支持契约 1.3 |
| 插件协议版本 | manifest `protocolVersion` | 固定 `1.0`，主版本 1 |

现状风险：`ToolGrpcService.ContractVersion="1.0.0"` 与 `tool_client.py` 的 `"1.3.0"`
仅靠主版本相等才互通。SDK 必须把版本收敛到 `contract.version` 单一来源，并加单测比对。

### 7.3 字段演进

- 消息/字段 PascalCase / snake_case 规范与编号规则沿用现有 proto 注释：字段号永不复用，
  删除时写 `reserved`，流式载荷只用 `oneof` 扩展。
- 新增 RPC 必须同时更新：proto、C# 服务端（默认 `Unimplemented` 可接受）、Python 适配器、
  变更清单。
- 契约哈希：`Sdk.Tools validate --proto-dir` 计算所有 proto 的 sha256，与
  `contract.sha256` 比对，CI 门禁用。

---

## 8. 关键约束与对策

| 约束（事实来源） | 影响 | 对策 |
| :-- | :-- | :-- |
| 包 ≤32 MB、≤5000 文件 | 自包含发布被拒 | 框架依赖 + `dotnet Plugin.dll` |
| 安装时 Linux 沙箱试运行 | 纯 Windows 产物验证失败 | 框架依赖产物跨平台 |
| `.dll/.exe` 记 `COMPILED_BINARY` | 不拦截但记哈希 | manifest `dist.sha256` 说明来源 |
| stdout 污染即协议错误 | 握手失败 | `PluginHost` 抢占原始流，`Console.Out→stderr` |
| 宿主强杀/空闲回收 | 崩溃写 stdout 连锁失败 | EOF/SIGTERM 视为正常退出 |
| 工具名 64 字符截断 | 静默重名 | 打包校验 `plugin_id + name` 长度 |
| gRPC 明文 h2c | TLS 配置错就连不上 | 服务端 `Http2`，客户端 `http://` |
| WPF 工具碰可视树 | 跨线程异常 | 工具内封回 `Application.Current.Dispatcher` |
| 契约双份维护 | 字段漂移 | proto 单源 + 哈希门禁 + 版本文件 |
| 审批默认放行 | 破坏性操作被自动执行 | 声明 `SandboxLevel`；宿主可改 `DefaultDecision` |

---

## 9. Python 对端：通用性评估与分层

结论：**可以做成通用层，但必须把「协议壳」与「宿主适配」分开**。
现有 `protocals/scripts` 里的实现把两者混在一起（直接读 `agent.config`、`api.*`），
所以看起来特化；拆开后通用性没有问题。

### 9.1 三层结构

```text
src/grpc_contract/            # ① 协议壳（完全通用，零业务）
  proto/                      #    与 SDK 的 proto 同步
  generated/                  #    *_pb2 / *_pb2_grpc
  registry.py                 #    服务注册 + h2c 启动 + 契约版本

src/grpc_runtime/             # ② 适配接口 + 默认实现（通用）
  chat_adapter.py             #    ChatAdapter: stream/resume
  session_adapter.py          #    SessionAdapter
  trajectory_adapter.py       #    TrajectoryAdapter
  capability_adapter.py       #    CapabilityAdapter
  file_adapter.py             #    FileAdapter
  tool_bridge.py              #    ToolService 客户端 → LangChain StructuredTool

src/grpc_common_harness/      # ③ CommonProject 的适配实现（宿主特化）
  chat.py                     #    复用 api/chat.py 的 SSE 事件抽取
  session.py                  #    复用会话存储
  trajectory.py               #    复用 agent/middlewares/trajectory.py
  capabilities.py             #    模型/技能/子 Agent/MCP
  files.py                    #    thread state files
```

### 9.2 通用性判定

| 组件 | 是否通用 | 依据 |
| :-- | :-- | :-- |
| `ChatService` / `ChatChunk` 协议 | 通用 | 计划/HITL/轨迹均为可选分支 |
| `ToolService` 客户端桥 | 通用 | 只依赖 `ListTools` 的 Schema |
| `SessionService` | 通用 | 只需「列会话/取历史/增删改」的存储接口 |
| `TrajectoryService` | 通用 | 事件模型统一，来源由适配器决定 |
| `HarnessService` | 通用 | 能力发现是只读聚合 |
| `PlanStepInfo.domain` | 通用 | 只是字符串，具体域由宿主注入 |
| Agent 图、中间件、HITL 触发点 | **不通用** | 由适配器实现；没有就返回空/不支持 |

### 9.3 在 CommonProject 落地的可行性

- `api/chat.py` 已有完整的流式事件抽取（文本增量、工具调用、工具结果、中断、推理、
  自定义事件），与 `ChatChunk` 分支几乎一一对应。
- `agent/middlewares/trajectory.py` 已产出 `run_id/parent_id`，可直接映射 `TrajectoryEvent`。
- 插件总线已有工具注册表，但 gRPC 的 `ToolService` 是反方向（C# 提供工具），
  两者可并存：`plugin__*` 是宿主侧插件，gRPC 工具是 C# 侧工具，命名不冲突。
- 缺失项：`ChatService` 的 `plan` 分支与 `SessionService` 的 `usage_json`
  取决于宿主持久化；SDK 文档按「可选能力」处理。

**建议**：先交付 C# SDK（形态 A + B），Python 侧分两步：
先做 ①+②+`ToolService` 桥（最小闭环：C# 工具可被 Agent 调用），
再补 Chat/Session/Trajectory 的完整 servicer。

---

## 10. 发布与打包

### 10.1 NuGet 携带 proto

`Harness.Plugin.Sdk.Grpc.csproj` 关键片段：

```xml
<ItemGroup>
  <!-- 把 proto 与 props 打进包 -->
  <None Include="..\..\proto\*.proto" Pack="true" PackagePath="proto\" />
  <None Include="..\..\proto\contract.version" Pack="true" PackagePath="proto\" />
  <None Include="buildTransitive\Harness.Plugin.Sdk.Grpc.props" Pack="true"
        PackagePath="buildTransitive\" />
</ItemGroup>
```

`buildTransitive/Harness.Plugin.Sdk.Grpc.props`：

```xml
<Project>
  <ItemGroup>
    <Protobuf Include="$(MSBuildThisFileDirectory)..\proto\Agent.proto" GrpcServices="Both" />
    <Protobuf Include="$(MSBuildThisFileDirectory)..\proto\Session.proto" GrpcServices="Both" />
    <Protobuf Include="$(MSBuildThisFileDirectory)..\proto\Trajectory.proto" GrpcServices="Both" />
    <Protobuf Include="$(MSBuildThisFileDirectory)..\proto\Harness.proto" GrpcServices="Both" />
    <Protobuf Include="$(MSBuildThisFileDirectory)..\proto\File.proto" GrpcServices="Both" />
  </ItemGroup>
</Project>
```

消费者只需：

```xml
<PackageReference Include="Harness.Plugin.Sdk.Grpc" Version="1.0.0" />
<PackageReference Include="Grpc.Tools" Version="2.62.0" PrivateAssets="All" />
```

### 10.2 形态 A 插件包

```bash
dotnet publish samples/EchoPlugin/CSharpEchoPlugin.csproj -c Release -o dist/csharp-echo \
  --no-self-contained /p:PublishSingleFile=false
```

包结构：`manifest.json` + dll + deps.json + runtimeconfig.json（可选 `ui/`）。
`manifest.json` 必须在 ZIP 第一层；`runtime.args` 与 dll 名严格一致。

### 10.3 版本与兼容

- 契约主版本变更 = 破坏性变更，必须同步 Python 与 C#，并升 NuGet 主版本。
- 插件协议 `1.0` 与契约版本无关，互不约束。
- `Harness.Plugin.Sdk.Grpc` 与 `Grpc.*` 版本需成组升级（生成代码与运行时匹配）。

### 10.4 已知风险

- 目标机缺 ASP.NET Core Runtime 时 `Kestrel` 起不来；README 必须写明。
- 沙箱镜像缺 .NET 运行时会导致形态 A 安装验证失败。
- 端口冲突：50051/50052 被占用时启动失败，SDK 应支持端口 0 探测并回报实际端口。

---

## 11. 安全模型

- 形态 A：权限未声明即拒绝；凭据只走 `host.secret.get`；静态扫描 + 沙箱试运行 + 用户确认。
- 形态 B：C# 是**受信宿主**，无进程隔离；因此：
  - 写操作必须声明 `SandboxLevel`，破坏性操作 `AlwaysConfirm`。
  - 默认决策建议在生产环境改为 `Reject`，由 UI 显式批准。
  - `EDIT` 仅对 `SupportsEditArgs=true` 的工具开放，且必须校验完整参数。
  - 工作目录、文件访问由业务工具自行约束；SDK 不提供宿主级沙箱。
- 传输：本机回环 + h2c；跨机部署必须自行加 TLS/网关，当前契约未内置鉴权。

---

## 12. 从 HarnessBridge 迁移

| 现有类型/文件 | SDK 对应 | 迁移动作 |
| :-- | :-- | :-- |
| `Attributes/ToolAttribute` | `Metadata/HarnessToolAttribute` | 特性名替换；属性名保持一致（Name/Description/Domain/Skill/...） |
| `Tools/ToolRegistry`（静态） | `Metadata/HarnessToolRegistry`（实例，DI） | 改为 `builder.AddTools(instance)` 或 `AddToolsFromAssembly` |
| `Tools/ToolSchemaBuilder` | `Metadata/JsonSchemaBuilder` | 直接替换；补单 DTO 与逐参数两种模式 |
| `Tools/ToolArgumentBinder` | `Metadata/ToolArgumentBinder` | 保持 JSON 对象/数组两种绑定 |
| `Models/ToolResult`、`ToolError`、`TrajectoryEvent` | `Metadata/ToolResult` 等 | 字段同名；`ToolResult.Text()` 处理字符串结果 |
| `Server/ToolGrpcService` | `Grpc/Server/ToolGrpcService` | 继承同一生成基类；审批/幂等/TTL 参数改为 `ToolServiceOptions` |
| `Server/ToolApprovalBroker` | `Grpc/Server/ToolApprovalBroker` | 事件与 `DecisionProvider` 保持不变 |
| `Server/HarnessToolServer` | `Grpc/Server/HarnessToolServer` | Kestrel 配置内建；支持端口 0 |
| `Client/HarnessChatClient` | `Grpc/Client/HarnessChatClient` | 事件投影保留，`ContractVersion` 改读常量 |
| `HarnessProtoDir` 绝对路径 | NuGet 自带 proto | 删除路径配置 |
| `[Tool]` | `[HarnessTool]` | 保留 `ToolAttribute` 作为 `[Obsolete]` 别名一个版本周期 |

---

## 13. 待决问题

| 问题 | 现状 | 后续 |
| :-- | :-- | :-- |
| 契约版本单一来源 | 计划 `proto/contract.version` | 需与 Python 同步脚本一起落地 |
| 审批默认决策 | 现有默认 `APPROVE` | 生产建议 `REJECT`，需产品确认 |
| Plan/HITL 在 CommonProject 的覆盖度 | 中间件已具备中断能力，Planner 配置待确认 | Python 适配阶段评估 |
| 跨机部署鉴权 | 契约无鉴权字段 | 需要时加 mTLS 或网关注入 |
| 多语言客户端 | 仅 C# | 契约天然跨语言，Java/TS 后续再生 |
| `[Tool]` 别名保留周期 | 计划一个大版本 | 视用户迁移情况 |

---

## 14. 结论

1. 关联模式 = **gRPC `.proto` 双向契约**，与插件总线并列而非替代。
2. proto 收进 SDK 作为唯一源，NuGet 通过 `buildTransitive` 自动注入，业务工程不再配绝对路径。
3. 特性统一为 `[HarnessTool]`，元数据层一份，同时产出 `describe` 与 `ToolDescriptor`。
4. Python 对端可以通用化，关键是把协议壳、适配接口、宿主实现分三层；
   CommonProject 已有 SSE 抽取与轨迹中间件，改造量可控。
5. 具体实现见 [CSharp插件SDK开发文档.md](CSharp插件SDK开发文档.md)。
