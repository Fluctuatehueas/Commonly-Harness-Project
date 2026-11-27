# TypeScript SDK（规划中）

运行时：Node.js ≥ 20；包名暂定 `@harness/plugin-sdk`，ESM 主入口、CJS 薄封装。

计划封装：`definePlugin`、`runStdioPlugin`、工具定义、反向调用客户端、
AbortSignal 取消、错误码映射与优雅退出。

接口草案与测试方案见 `docs/core_design/v1.1/插件SDK调研与开发文档.md` 第 6 节。

当前状态：未开始实现。
