# C# SDK

目标框架：.NET 10（`net10.0` 单目标）。  
提供三种接入形态：

| 形态 | 契约 | 运输 | 场景 |
| :-- | :-- | :-- | :-- |
| A 插件模式 | manifest + `describe` | stdio（`dotnet Plugin.dll`） | 工具包、市场分发 |
| B 契约关联 | `proto/*.proto`（gRPC） | h2c 双向：C# ToolService / Python ChatService | 桌面宿主整体接入 Agent |
| C 轻量远程插件 | manifest + `describe` | HTTP JSON-RPC 单端点 | 已有 HTTP 服务 |

完整文档：

- [CSharp插件SDK设计文档.md](../../../docs/core_design/v1.1/CSharp插件SDK设计文档.md)
  —— 三种形态、模块分布、插件协议与 gRPC 契约全量清单、契约治理、Python 对端评估、发布与安全。
- [CSharp插件SDK开发文档.md](../../../docs/core_design/v1.1/CSharp插件SDK开发文档.md)
  —— 统一元数据层、形态 A 的 RpcPeer/Dispatcher/Host、形态 B 的 ToolService/审批/幂等/
  Kestrel/Chat 客户端、形态 C、Python 适配层附录、测试与坑位清单。

规划的项目结构：

```text
src/sdk/csharp/
  Harness.Plugin.Sdk.sln
  Directory.Build.props
  proto/                          # 唯一权威 .proto + contract.version（随 NuGet 分发）
  src/
    Harness.Plugin.Sdk/           # 核心：元数据 + 形态 A/C，零第三方依赖
    Harness.Plugin.Sdk.Grpc/      # 形态 B：ToolService + 客户端 + buildTransitive props
    Harness.Plugin.Sdk.Tools/     # dotnet tool：pack / validate / proto-sync
  samples/
    EchoPlugin/  StoragePlugin/  ConsoleBridgePlugin/  GrpcToolHost/
  tests/
    Harness.Plugin.Sdk.Tests/  Harness.Plugin.Sdk.Grpc.Tests/
```

当前状态：设计与实现文档已完成，代码未开始。
