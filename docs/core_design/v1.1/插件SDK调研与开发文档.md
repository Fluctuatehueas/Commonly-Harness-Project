# 插件 SDK 调研与开发文档（P2）

Time: 2026/11/27  
Author: GuiQIHueas  
Scope: 本轮只做 SDK 调研、目录规划与开发设计；不交付 SDK 代码。  
前置：`plugin-development` SKILL 与内置插件 `text-toolkit` 已完成。

---

## 1. 背景与结论

P2 原始范围包含 SDK、样例与 Agent 自迭代。按当前节奏拆成两步：

1. **已完成**：`plugin-development` SKILL（协议文档、stdio/inprocess 模板、冒烟脚本、打包脚本）+ 内置插件 `text-toolkit`。
2. **本文档**：C# 与 TypeScript 官方 SDK 的调研、目录规划、接口草案、测试与发布方案；**先不写 SDK 代码**，等样例与技能验证稳定后再启动实现。

Java SDK 预留，不在首批实现。

---

## 2. SDK 目录规划

用户建议按语言分目录。实际采用：

```text
src/sdk/
  csharp/          # C# / .NET SDK（首批）
  typescript/      # TypeScript / JavaScript SDK（首批）
  java/            # 预留，暂不实现
docs/plugin_samples/
  csharp-stdio/
  typescript-stdio/
```

说明：

- **不使用字面量 `C#` 作为目录名**：`#` 在 URL、部分 CLI 参数、构建缓存路径和脚本转义里容易出问题；目录用 `csharp`，对外包名与文档展示仍写「C# SDK」。
- `java/` 先建目录与 README 占位，不写实现。
- Python 不单列 SDK：宿主本身就是 Python，内置插件模板已覆盖；外部 Python 插件使用 `plugin-development` 的 `stdio-plugin` 模板即可。

---

## 3. 协议契约摘要

完整协议见 [插件系统开发文档](插件系统开发文档.md) 第 4、5 节。SDK 只需封装以下内容：

| 项 | 约定 |
| :-- | :-- |
| 传输 | 外部插件默认 stdio：一行一条 JSON-RPC 2.0；stdout 只放协议 |
| 握手 | `plugin.initialize` → `plugin.describe` → `plugin.health` |
| 能力 | `tool` / `resource` / `prompt` / `hook` / `ui` / `integration` |
| 调用 | `tool.call` 参数 `{name, arguments}`；结果建议 `{content}` |
| 反向调用 | 仅 `host.storage.*`、`host.secret.get`、`host.log`、`host.progress` |
| 版本 | `protocolVersion=1.0`；主版本不匹配直接拒绝 |
| 错误 | JSON-RPC 标准码 + `-32001 ~ -32010` 业务码 |
| 退出 | `plugin.shutdown` 后停止读取并退出 |

SDK 不替插件做业务，只负责：握手、能力注册、心跳、请求分发、错误映射、重试边界、日志落 stderr、优雅退出。

---

## 4. 通用 SDK 设计

### 4.1 职责边界

SDK 负责：

1. 解析 manifest，校验 `plugin_id` / `protocolVersion` / `runtime` 基本字段。
2. 读取 stdin 行、分发到插件的 `initialize` / `describe` / `health` / `tool.call` 等处理函数。
3. 把插件返回结构序列化成 JSON-RPC 响应；把异常转换成错误码。
4. 提供 `host.*` 反向调用客户端（storage / secret / log / progress）。
5. 心跳与退出：收到 `plugin.shutdown` 后停止读取，关闭资源。
6. 日志只写 stderr；stdout 保持协议纯净。

SDK 不负责：

- 不安装依赖、不改宿主环境。
- 不自动重试 `tool.call`（业务可能非幂等）。
- 不读取宿主环境变量；`host.secret.get` 之外没有凭据来源。
- 不实现 UI 渲染，只提供 UI 静态文件约定。

### 4.2 重试边界

| 调用 | 是否自动重试 | 说明 |
| :-- | :-- | :-- |
| `plugin.initialize` | 否 | 由总线决定重启 |
| `plugin.health` | 否 | 失败由总线计健康次数 |
| `tool.call` | 否 | 避免重复副作用 |
| `host.storage.get/list` | 可重试 1 次 | 只读、幂等 |
| `host.storage.set/delete` | 否 | 由插件自行决定 |
| `host.log` / `host.progress` | 不重试 | 通知语义，失败忽略 |

### 4.3 版本兼容

- SDK 版本与 `protocolVersion` 分开：SDK 1.x 支持协议 1.0。
- `harnessVersion` 由 manifest 声明，宿主在安装与握手阶段检查。
- SDK 不猜测旧协议，主版本不一致直接报 `-32006`。

---

## 5. C# SDK 调研

### 5.1 技术栈

| 项 | 选择 | 理由 |
| :-- | :-- | :-- |
| 运行时 | .NET 10（2026/11 决策，原计划 .NET 8 LTS） | 单目标；Windows/Linux/macOS 一致 |
| JSON | `System.Text.Json` | 内置、Source Generator 可裁剪 |
| 进程 | `System.Diagnostics.Process` + `StandardInput/Output` | 不引入第三方依赖 |
| 异步 | `async` / `CancellationToken` / `IAsyncDisposable` | 与 stdio 长连接匹配 |
| 测试 | xUnit + 自建假总线 | 协议帧可快照断言 |
| 包 | NuGet `Harness.Plugin.Sdk` | 先本地源，稳定后发布 |

### 5.2 接口草案（文档级，不实现）

```csharp
public sealed record ToolDefinition(
    string Name,
    string Description,
    JsonNode InputSchema);

public interface IHarnessPlugin
{
    Task<JsonNode?> InitializeAsync(PluginInitContext context, CancellationToken ct);
    IReadOnlyList<ToolDefinition> Describe();
    Task<ToolResult> CallToolAsync(string name, JsonNode arguments, CancellationToken ct);
    Task ShutdownAsync(CancellationToken ct);
}

public sealed class PluginHost : IAsyncDisposable
{
    public PluginHost(IHarnessPlugin plugin, PluginHostOptions options);
    public Task RunAsync(CancellationToken ct);
}

public sealed class PluginInitContext
{
    public string UserId { get; }
    public string PluginId { get; }
    public IReadOnlyList<string> Permissions { get; }
    public PluginStorage Storage { get; }
    public PluginSecrets Secrets { get; }
    public IPluginLogger Log { get; }
    public IProgressSink Progress { get; }
}
```

插件作者入口：

```csharp
await using var host = new PluginHost(new MyPlugin());
await host.RunAsync(CancellationToken.None);
```

约束：

- 读取 stdin 使用 `Console.In.ReadLineAsync(ct)` 或带缓冲的 StreamReader；必须按行、UTF-8。
- 写 stdout 用 `Console.Out.WriteLineAsync`，并 `Flush`；不要用 `Console.Write` 混入日志。
- 所有日志走 `Console.Error`。
- 捕获 `OperationCanceledException`，按正常退出处理。
- 进程被强杀时不做清理承诺；需要持久化的状态写 `host.storage`。

### 5.3 C# 插件样例规划

`docs/plugin_samples/csharp-stdio/`：

```text
MyPlugin/
  MyPlugin.csproj
  Program.cs
  Plugin.cs
manifest.json
README.md
```

样例只依赖 `Harness.Plugin.Sdk`，实现一个 `echo` 与一个 `host.storage` 计数工具，用于验证反向调用。

### 5.4 风险

- Windows 控制台编码：发布时固定 UTF-8，避免中文乱码。
- `Process` 子进程树：宿主侧已负责终止；SDK 不主动创建子进程。
- AOT 裁剪：首批不做 NativeAOT，避免 JSON 反射问题。

---

## 6. TypeScript / JavaScript SDK 调研

### 6.1 技术栈

| 项 | 选择 | 理由 |
| :-- | :-- | :-- |
| 运行时 | Node.js ≥ 20 | 与前端工具链一致，原生 ESM |
| 语言 | TypeScript | 类型即协议文档 |
| 进程/流 | `node:readline` + `process.stdin/stdout` | 零依赖 |
| 取消 | `AbortSignal` | 与 fetch / timers 统一 |
| 测试 | Vitest + 假总线 harness | 快速、可断言帧 |
| 包 | npm `@harness/plugin-sdk` | ESM 主入口 + CJS 兼容 |

### 6.2 接口草案（文档级，不实现）

```ts
export interface ToolDefinition {
  name: string
  description: string
  inputSchema: Record<string, unknown>
}

export interface PluginContext {
  pluginId: string
  userId: string
  permissions: string[]
  storage: PluginStorage
  secrets: PluginSecrets
  log: PluginLogger
  progress: PluginProgress
}

export interface HarnessPlugin {
  initialize?(context: PluginContext): Promise<void> | void
  describe(): { capabilities: PluginCapabilities }
  callTool(name: string, args: Record<string, unknown>): Promise<ToolResult>
  health?(): Promise<Record<string, unknown>>
  shutdown?(): Promise<void>
}

export function definePlugin<T extends HarnessPlugin>(plugin: T): T
export function runStdioPlugin(plugin: HarnessPlugin, options?: RunOptions): Promise<void>
```

要求：

- `runStdioPlugin` 内部用 readline 逐行读取，禁止直接 `process.stdin.on('data')` 自行拼包。
- 所有日志 `console.error`；禁止 `console.log`，避免污染协议。
- 默认导出 ESM；同时提供 CJS 构建，`package.json` 用 `exports` 分流。
- 工具结果统一返回 `{ content: string }`；复杂对象由插件自行 JSON.stringify。
- Node 进程收到 `SIGTERM` 时先尝试 `plugin.shutdown`，再退出。

### 6.3 TS 插件样例规划

`docs/plugin_samples/typescript-stdio/`：

```text
src/index.ts
package.json
tsconfig.json
manifest.json
README.md
```

样例实现 `echo` 与 `host.progress`，用于验证长任务进度。

### 6.4 风险

- ESM/CJS 双构建增加发布复杂度；首批以 ESM 为主，CJS 只做薄封装。
- Windows 下 `readline` 与管道编码需固定 UTF-8。
- 插件依赖必须随包 `node_modules` 或声明为沙箱依赖，SDK 不自动安装。

---

## 7. Java（预留，不实现）

- JDK 21 + Jackson + `ProcessBuilder` 可行，但宿主侧没有 Java 运行环境保障。
- 首批只保留 `src/sdk/java/README.md`，说明按 `docs/plugin_protocol.html` 自行接入。
- 等 C# / TS SDK 与样例稳定后再评估。

---

## 8. Python 插件与 SDK 的关系

- 宿主是 Python，内置插件不需要 SDK：直接实现 `handle(method, params)`。
- 外部 Python 插件使用 `plugin-development` SKILL 的 `stdio-plugin` 模板。
- 暂不单独维护 `src/sdk/python`；等外部 Python 插件需求明确再补。

---

## 9. 对外协议文档

对外协议文档已落地为 `docs/plugin_protocol.html`（单文件自包含，取代原计划的 `docs/plugin_protocol.md`），内容从 [插件系统开发文档](插件系统开发文档.md) 第 4、5 节抽取并冻结：

- 信封、方法表、错误码、超时与重试约定。
- manifest 必填字段与版本规则。
- host.* 白名单与权限映射。
- stdio 帧格式与日志约定。
- C# / Java / TypeScript / JavaScript / Python 五语言最小实现。
- 「官方 SDK」章节占位，SDK 发布后直接在该 HTML 内补充。

C# / Java 不依赖 SDK 也能按该文档接入。SDK 只是把协议封装成语言习惯的 API。

---

## 10. 测试与验收

### 10.1 SDK 单测

- 协议帧：请求/响应/通知/错误四类序列化快照。
- 分发：未知方法返回 `-32601`；参数非法返回 `-32602`。
- 反向调用：mock 总线验证 `host.storage` / `host.log` / `host.progress`。
- 退出：收到 `plugin.shutdown` 后停止读取，stdin 关闭不崩溃。
- 超时与取消：`CancellationToken` / `AbortSignal` 能中断等待。

### 10.2 端到端

1. 用 SDK 跑 `docs/plugin_samples/csharp-stdio` 与 `typescript-stdio`。
2. 用宿主的 `smoke_plugin.py` 对两个样例做 `initialize → describe → health → shutdown`。
3. 通过 `/plugins/install/upload` 安装样例，验证工具调用与启停。
4. 与 `plugin-development` 的模板产物对比，保证协议一致。

### 10.3 CI

- C#：`windows-latest` + `ubuntu-latest`，`dotnet test`。
- TS：Node 20/22，`npm test` + `tsc --noEmit`。
- 样例：构建后跑宿主冒烟脚本，作为集成门禁。

---

## 11. 实施顺序

1. 冻结 `docs/plugin_protocol.html`。
2. C# SDK 骨架：stdio 读取、分发、describe、tool.call、host.storage/log。
3. TypeScript SDK 骨架：同上 + ESM 构建。
4. 两个样例 + 宿主冒烟联调。
5. 发布脚本：NuGet 本地包 / npm 本地 tarball。
6. Java README 与协议接入说明。
7. 再评估 Python SDK 与 `docs/plugin_samples` 扩展。

---

## 12. 待决问题

| 问题 | 现状 | 后续 |
| :-- | :-- | :-- |
| SDK 目录是否用 `C#` 字面量 | 采用 `csharp/` | 如需展示名，在 README 写「C#」 |
| NuGet 包 ID | 暂定 `Harness.Plugin.Sdk` | 发布前确认命名空间 |
| npm scope | 暂定 `@harness/plugin-sdk` | 与前端包名统一 |
| ESM/CJS | ESM 主、CJS 薄封装 | 观察样例与用户反馈 |
| 浏览器端远程插件客户端 | 不纳入首批 | 等远程插件实际使用后评估 |
| 远程插件反向 `host.*` | 协议暂不支持 | 需要回调通道时再设计 |

---

## 13. 本轮完成记录（2026-11-27）

- 新增 `plugin-development` SKILL：`src/agent/skills/main/plugin-development/`，含协议速查、stdio/inprocess 模板、`pack_plugin.py`、`smoke_plugin.py`；两个模板与打包脚本已实跑通过。
- 新增可用内置插件 `text-toolkit`：`src/plugin_bus/sys_plugins/text-toolkit/`，提供 `text_stats`、`text_transform`、`find_replace` 三个工具，进程内运行、无额外权限。
- 本文档完成 C# 与 TypeScript SDK 的目录规划、接口草案、测试与发布方案；SDK 代码留到下一步实施，对外协议文档已以 `docs/plugin_protocol.html` 交付。
- 插件测试累计 69 项通过。

---

## 14. 完成记录（2026-11-28）C# SDK 设计定稿（含二次修订）

决策：

| 项 | 决策 | 理由 |
| :-- | :-- | :-- |
| 目标框架 | `net10.0` 单目标 | 用户指定；不引入多目标条件编译 |
| 部署形态 | 框架依赖 + `dotnet Plugin.dll` | 单包跨平台；规避 32 MB 包上限与 Linux 沙箱验证 |
| 接入形态 | A `stdio` 插件 + **B gRPC 契约关联** + C HTTP 轻量远程 | 二次修订：关联模式以 `src/protocals/.protoes` 为准，不是 HTTP JSON-RPC |
| 契约归属 | **proto 由 SDK 携带**，NuGet `buildTransitive` 自动注入 | 业务工程不再配置 `HarnessProtoDir` 绝对路径 |
| 能力范围 | 统一 `[HarnessTool]` + `ExternalProcessTool` 包装已有 exe | 一份元数据同时产出 describe 与 ToolDescriptor |
| 包拆分 | `Harness.Plugin.Sdk` / `Harness.Plugin.Sdk.Grpc` / `Harness.Plugin.Sdk.Tools` | 核心零第三方依赖，gRPC 依赖隔离 |

交付文档：

- [CSharp插件SDK设计文档.md](CSharp插件SDK设计文档.md)：三种接入形态、模块分布、
  插件协议与 gRPC 契约全量清单、契约治理、Python 对端通用性评估、发布与安全策略。
- [CSharp插件SDK开发文档.md](CSharp插件SDK开发文档.md)：统一元数据层、形态 A 的
  RpcPeer/Dispatcher/Host、形态 B 的 ToolService/审批/幂等/Kestrel/Chat 客户端、
  形态 C、Python 适配层附录、测试与坑位清单。

对应原文档 §5（C# SDK 调研）的接口草案已被上述两份文档取代；`src/sdk/csharp/README.md`
已同步更新。代码实施尚未开始。
