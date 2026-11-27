# 插件样例（规划中）

首批样例与 SDK 同步实现：

```text
docs/plugin_samples/
  csharp-stdio/       # C# echo + host.storage
  typescript-stdio/   # TS echo + host.progress
```

验收方式：用 `src/agent/skills/main/plugin-development/scripts/smoke_plugin.py`
跑 `initialize → describe → health → shutdown`，再通过 `/plugins/install/upload`
安装并调用工具。

当前状态：目录规划完成，尚未加入样例代码。
