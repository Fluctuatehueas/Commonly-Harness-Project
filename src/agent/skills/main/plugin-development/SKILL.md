---
name: plugin-development
description: 开发、冒烟测试和打包 Harness 插件（stdio 外部插件或进程内内置插件），产出可安装 ZIP 与自检结果，安装前必须由用户确认。
---

# 插件开发

使用本技能时，你负责产出一个**可验证、可安装**的插件包；不要直接把插件写进宿主目录，也不要在未经用户确认时安装。

## 先选运行形态

| 场景 | runtime.kind | transport.kind | 说明 |
| :-- | :-- | :-- | :-- |
| 用户自制 / 社区插件 | `subprocess` | `stdio` | 由 Plugin Bus 拉起子进程，最通用 |
| 随系统发布的内置插件 | `inprocess` | `inprocess` | 仅 `source=builtin`，在宿主进程内运行 |
| 已有远程服务 | `remote` | `http` | 只注册 endpoint，不落包 |
| 项目级插件 | `subprocess` | 总线强制 `sandbox_stdio` | 只在沙箱内一次性运行 |

默认选 `subprocess + stdio`。只有明确要随系统发布时才用 `inprocess`。

## 目录结构

```text
{plugin_id}/
  manifest.json          # 必须放在包根，ZIP 解压后第一层可见
  plugin.py              # stdio 入口；inprocess 时由 runtime.entry 指向
  ui/                    # 可选：声明 ui 能力时提供
```

## 开发流程

1. 从模板复制一份到沙箱工作目录：
   - 外部插件：`templates/stdio-plugin/`
   - 内置插件：`templates/inprocess-plugin/`
2. 修改 `manifest.json`：
   - `plugin_id` 只用字母、数字、`-`、`_`，长度 ≤ 64。
   - `protocolVersion` 固定 `1.0`；`harnessVersion` 至少覆盖 `1.1.0 ~ 1.x`。
   - `capabilities` 只声明实际提供的能力：`tool` / `resource` / `prompt` / `hook` / `ui` / `integration`。
   - `permissions` 最小化；未声明的权限总线侧直接拒绝。
   - `author.name` 必填。
3. 实现协议方法。最小集合：
   - `plugin.initialize`、`plugin.describe`、`plugin.health`、`plugin.shutdown`
   - 有工具时实现 `tool.call`
4. 在沙箱运行冒烟：
   ```bash
   python scripts/smoke_plugin.py {plugin_id}
   ```
   脚本会完成 `initialize → describe → health → shutdown`，并打印工具清单。
5. 打包并记录哈希：
   ```bash
   python scripts/pack_plugin.py {plugin_id} -o dist/{plugin_id}-{version}.zip
   ```
6. 把包路径、manifest、工具清单、权限清单、冒烟结果交给用户；**由用户确认后再走安装通道**。

## 协议要点

- 一行一条 JSON-RPC 2.0；stdout 只放协议消息，日志写 stderr。
- 请求必须回显 `id`；没有 `id` 的是通知，不需要响应。
- 插件可以反向调用总线，但只限已声明权限：
  - `host.storage.get/set/list/delete`（需要 `storage`）
  - `host.secret.get`（需要 `secret`）
  - `host.log` / `host.progress`（无需权限）
- 不要读取宿主环境变量、不要假设工作目录以外可写；插件工作目录固定在版本目录下。
- 工具结果建议返回 `{"content": "文本"}`；总线会把它转成工具文本。
- 错误用 `{"code": -320xx, "message": "..."}`；具体错误码见 `references/protocol.md`。

## 安全与归属

- 包内禁止符号链接、绝对路径、`..` 路径穿越；单包默认 ≤ 32 MB。
- 依赖必须随包携带，或声明为沙箱依赖；不要假设宿主会自动安装。
- 外部插件默认按当前用户安装；不要伪装成系统内置插件。
- 安装前必须展示：能力、权限、运行形态、来源、外部系统访问范围。
- 高风险插件（`secret` / `net` / `process`）需要用户逐条确认。

## 交付格式

回答用户时至少给出：

```text
插件：{name} ({plugin_id}) v{version}
运行形态：subprocess/stdio 或 inprocess
能力：tool: xxx, yyy
权限：storage, net
冒烟：initialize/describe/health 通过
包：dist/{plugin_id}-{version}.zip（sha256: ...）
待确认：是否安装到当前用户？
```
