---
name: csharp
description: Use this skill whenever the user asks to write, create, review, explain, refactor, or debug C# code, .NET projects, or C#-related questions. Covers project scaffolding, coding conventions, async/await pitfalls, LINQ, null handling, and how to compile/run when dotnet or mono is unavailable in the sandbox.
---

# C# 编码技能

本技能用于在沙箱环境中编写、审查、解释和调试 C# / .NET 代码。

## 何时使用

- 用户要求编写 C# 代码（控制台应用、类库、单元测试等）
- 用户要求解释、审查或重构已有 C# 代码
- 用户询问 C# 语法、.NET API、线程/异步等语言特性
- 用户希望按项目规范生成带注释的 C# 代码

## 环境适配（先检查，再动手）

沙箱默认可能没有 .NET SDK，动手前先探测：

```bash
which dotnet csc mcs mono 2>/dev/null
dotnet --version 2>/dev/null
```

按探测结果选择执行路径：

1. **有 `dotnet`**：用 `dotnet run` 直接编译运行。新建项目用
   `dotnet new console -o AppName`，写代码后 `cd AppName && dotnet run`。
2. **有 `mono` / `mcs`**：用 `mcs program.cs && mono program.exe` 编译运行单文件。
   - 注意：`mcs`（Mono 旧版）**不支持** `async Task Main`（C# 7.1 入口语法），
     报 `CS0028`/`CS5001`。控制台程序用 `void Main` + `Task.GetAwaiter().GetResult()`
     替代；非控制台场景用同步 `Main` 并把异步逻辑包在同步方法里。
3. **都没有（常见情况）**：
   - 不要假装"已运行"，不要编造输出。
   - 先提出安装建议（如 `apt-get install -y mono-mcs` 或安装 .NET SDK），
     用户同意且沙箱允许网络时再装。
   - 如果无法安装，退化为**静态审查**：产出代码 + 逐段解释 +
     指出可能的编译错误和运行行为，明确告知用户"未实际编译，需在本地验证"。

## 编码规范

### 命名
- 类型名、方法名、公开成员：`PascalCase`
- 局部变量、参数：`camelCase`
- 私有字段：`_camelCase`
- 常量：`PascalCase`
- 接口：`I` 前缀；异步方法：`Async` 后缀

### 注释要求（覆盖三层信息）
参考本机用户偏好，注释必须覆盖：
1. **语义**：这段代码做什么、为什么存在
2. **调用方信息**：谁调用它、入参含义、返回值/异常约定
3. **线程关系**：是否线程安全、是否在异步上下文、锁或并发约束

示例：

```csharp
/// <summary>
/// 计算订单总额。由 OrderService 在支付前调用（调用方）。
/// </summary>
/// <param name="items">订单项集合，非空。</param>
/// <returns>含税总金额。</returns>
/// <exception cref="ArgumentException">items 为空时抛出。</exception>
/// <remarks>仅用于单线程场景；不在异步上下文中使用（线程关系）。</remarks>
public decimal CalculateTotal(IEnumerable<OrderItem> items) { ... }
```

### 线程与异步
- 涉及共享状态时说明锁（`lock` / `SemaphoreSlim`）及可见性（`volatile`、`Interlocked`）
- `async void` 只允许用于事件处理器，否则用 `async Task`
- 异步代码避免 `.Result` / `.Wait()` 死锁；用 `await` 一路向上传播

## 常见陷阱速查

| 主题 | 陷阱 | 正确做法 |
|---|---|---|
| 字符串比较 | `==` 对中文/大小写语义不总是想要 | 明确用 `StringComparison.Ordinal` / `OrdinalIgnoreCase` |
| 浮点 | `float`/`double` 比较可能不精确 | 用 `decimal`（金额场景）或用 epsilon 比较 |
| LINQ | `IEnumerable` 是惰性求值 | 需复用集合时先 `ToList()`/`ToArray()` |
| Null | 空引用 NRE | 用可空类型、`?.`、`??`，必要时 `ArgumentNullException.ThrowIfNull` |
| 泛型 | 直接用 `object`/`ArrayList` | 用泛型 `List<T>`、`Dictionary<K,V>` |
| 释放 | `IDisposable` 未释放 | 用 `using` 语句 |
| 异常 | 捕获后吞掉 | 至少记录日志或重抛包装异常 |

## 交付形式

- 单文件小程序：直接给 `Program.cs` 代码或写入文件。
- 多文件/工程：给出目录结构 + 每个文件作用的一行说明。
- 默认给完整可用代码，不要省略关键部分。
- 未实际编译时，明确标注"需本地验证"。