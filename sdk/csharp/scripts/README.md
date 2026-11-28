# Python Agent的Protobuf定义

## 负责给C#端与Python端提供一致的语言接口契约。保证Python端可以调用c#的函数业务逻辑已实现AI调用工具的操控WPF的功能

> 现行契约是 `Agent.proto`、`Session.proto`、`Trajectory.proto`、`Harness.proto`、`File.proto`；
> `BaseRule.proto` 为早期草稿，不再编译。
> 完整范式、产物管理与运行命令见 `.trae/skills/proto-contract/SKILL.md`。

***

一、编写方法\
在.protoes/目录下，编写.proto文件，可以多文件编写，并进行文档间的互相引用.\
在scripts/目录下，编写脚本文件，负责python后端的逻辑和.proto这个桥梁实现对接。

现行契约分工：

| 文件                 | 服务                                                                                     |
| :----------------- | :------------------------------------------------------------------------------------- |
| `Agent.proto`      | `ChatService.Chat` / `ResumeChat`、`ToolService.ListTools / CallTool / ResolveApproval` |
| `Session.proto`    | `SessionService` 会话列表、历史、创建、重命名、置顶、删除                                                  |
| `Trajectory.proto` | `TrajectoryService.GetTrajectory`                                                      |
| `Harness.proto`    | `HarnessService` 能力发现、模型配置、技能/子 Agent/MCP/上下文格式                                        |
| `File.proto`       | `FileService` 会话工作区文件                                                                  |

二、编译proto文件，产生中间文件

```bash
cd src/protocals/.protoes

python -m grpc_tools.protoc -I. --python_out=../scripts/model --pyi_out=../scripts/type_stub --grpc_python_out=../scripts/grpc_service Agent.proto Session.proto Trajectory.proto Harness.proto File.proto
```

命令详解：

- 以模块方式运行（python -m 模块名），等价于 import 模块; 模块.main()
- grpc\_tools：grpcio-tools 包提供的模块
- .protoc：grpc\_tools 包内置了一份编译好的 protoc 二进制，并附带 gRPC 插件
- -I. 表示当前目录是proto文件的搜索路径
- \--python\_out=. 表示将生成的python文件输出到当前目录
- \--grpc\_python\_out=. 表示将生成的grpc python文件输出到当前目录
