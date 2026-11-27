# Plugin Protocol 速查（1.0）

完整设计见 `docs/core_design/v1.1/插件系统开发文档.md` 第 4、5 节。本文件只列开发插件时必须知道的最小契约。

## 信封

```json
{"jsonrpc":"2.0","id":"req-1","method":"tool.call","params":{}}
{"jsonrpc":"2.0","id":"req-1","result":{}}
{"jsonrpc":"2.0","id":"req-1","error":{"code":-32010,"message":"..."}}
```

- stdio：一行一条 JSON，stdout 只写协议，日志走 stderr。
- 响应必须回显 `id`；通知无 `id`，不需要响应。
- 超时由总线控制，插件不要自行重试长时间阻塞。

## 总线 → 插件

| 方法 | 说明 |
| :-- | :-- |
| `plugin.initialize` | 握手；params 含 `protocolVersion`、`plugin_id`、`version`、`user_id`、`permissions`、`config` |
| `plugin.describe` | 上报能力；必须与 manifest 声明一致 |
| `plugin.health` | 健康探针 |
| `plugin.shutdown` | 优雅退出 |
| `tool.call` | `params = {"name": "...", "arguments": {...}}` |
| `resource.list` / `resource.read` | 资源列举与读取 |
| `prompt.get` | 取领域提示词 |
| `hook.invoke` | 拦截点；`params = {"hook": "before_tool", "payload": {...}}` |
| `event.notify` | 配置/启停事件，不等待响应 |

## 插件 → 总线（白名单）

| 方法 | 所需权限 |
| :-- | :-- |
| `host.storage.get` / `set` / `list` / `delete` | `storage` |
| `host.secret.get` | `secret` |
| `host.log` | 无 |
| `host.progress` | 无 |

`host.storage.*` 只能访问本插件 `data/` 下的键值；不要传绝对路径。

## describe 结构

```json
{
  "plugin_id": "demo",
  "protocolVersion": "1.0",
  "capabilities": {
    "tool": [
      {
        "name": "echo",
        "description": "回显文本",
        "inputSchema": {
          "type": "object",
          "properties": { "text": { "type": "string" } },
          "required": ["text"]
        }
      }
    ],
    "resource": [],
    "prompt": [],
    "hook": [],
    "ui": []
  }
}
```

## 错误码

| 码 | 含义 |
| :-- | :-- |
| -32700 | JSON 解析失败 |
| -32600 | 请求非法 |
| -32601 | 方法不存在 |
| -32602 | 参数非法 |
| -32603 | 内部错误 |
| -32001 | 插件不存在 |
| -32002 | 插件未就绪 |
| -32003 | 权限拒绝 |
| -32004 | 超时 |
| -32005 | 能力与 manifest 不一致 |
| -32006 | 协议版本不兼容 |
| -32007 | 插件崩溃 |
| -32008 | 不健康 |
| -32009 | 包非法 |
| -32010 | 工具调用失败 |

## manifest 必填字段

`plugin_id`、`name`、`version`、`protocolVersion`、`author.name`、`capabilities`、`runtime`、`transport`、`permissions`、`harnessVersion`。

- `runtime.kind`：`subprocess` / `remote` / `inprocess`（仅内置）。
- `subprocess` 必须有 `runtime.command`；`inprocess` 必须有 `runtime.entry`（如 `plugin:handle`）。
- `ui` 能力必须同时声明 `ui.slots` 与 `ui` 权限。
