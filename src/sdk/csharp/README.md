# C# SDK（规划中）

目标框架：.NET 8 LTS；包名暂定 `Harness.Plugin.Sdk`。

计划封装：stdio JSON-RPC 读取与分发、`plugin.initialize/describe/health/shutdown`、
`tool.call`、`host.storage.*`、`host.log`、`host.progress`、错误码与优雅退出。

接口草案与测试方案见 `docs/core_design/v1.1/插件SDK调研与开发文档.md` 第 5 节。

当前状态：未开始实现。
