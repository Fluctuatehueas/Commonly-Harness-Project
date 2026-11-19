# 管理与配置 API

> 版本：v1.0 · 2026-11-11
> 读者：甲方后端 / 运维 / 管理员
> 范围：MCP 服务、RAG Provider、模型与沙箱设置、MyAgent 档案、技能、任务流、项目。
> **这些接口只应部署在内网，或经网关做管理员角色校验后开放。** 业务对话接口见 [业务主链路API.md](./业务主链路API.md)。

---

## 目录

- [1. 公共约定](#1-公共约定)
- [2. API 一览](#2-api-一览)
- [3. MCP 服务管理](#3-mcp-服务管理)
- [4. RAG Provider 管理](#4-rag-provider-管理)
- [5. 设置](#5-设置)
- [6. MyAgent 档案](#6-myagent-档案)
- [7. 技能管理](#7-技能管理)
- [8. 任务流管理](#8-任务流管理)
- [9. 项目管理](#9-项目管理)
- [10. 权限与安全建议](#10-权限与安全建议)

---

## 1. 公共约定

| 项 | 约定 |
| :-- | :-- |
| Base URL | `http://{harness-host}:8000` |
| 身份头 | `X-User-Id`（由网关注入，按用户隔离资源） |
| 内容类型 | `application/json; charset=utf-8` |
| 时间 | ISO 8601 UTC |
| 错误 | `{"detail": "文本"}` 或 `{"detail": {"code": "...", "message": "..."}}` |

这类接口会修改配置文件、`.env`、用户目录或数据库。**必须限制调用来源**，不要把 `/mcp/*`、`/settings/*`、`/skills/*` 直接暴露给公网。

---

## 2. API 一览

| 方法 | 路径 | 作用 |
| :-- | :-- | :-- |
| GET | `/mcp/runtime` | MCP 运行时概览 |
| GET | `/mcp/config` | MCP 配置文件位置 |
| POST | `/mcp/preflight` | 导入前预检 |
| POST | `/mcp/import` | 导入 MCP 服务 |
| GET | `/mcp/integrations` | 集成项目列表 |
| GET | `/mcp/servers` | 服务列表 |
| GET | `/mcp/servers/{server_id}` | 服务详情 |
| POST | `/mcp/servers` | 新增服务 |
| PUT | `/mcp/servers/{server_id}` | 更新服务 |
| DELETE | `/mcp/servers/{server_id}` | 删除服务 |
| POST | `/mcp/servers/{server_id}/enable` | 启用 / 停用 |
| POST | `/mcp/servers/{server_id}/probe` | 测试连接 |
| POST | `/mcp/servers/{server_id}/restart` | 重启连接 |
| GET | `/mcp/servers/{server_id}/tools` | 工具清单 |
| POST | `/mcp/servers/{server_id}/tools/{tool_name}/test` | 测试调用工具 |
| GET | `/mcp/servers/{server_id}/logs` | 最近日志 |
| GET | `/rag/config` | RAG 配置文件位置 |
| GET | `/rag/providers` | Provider 列表 |
| GET | `/rag/providers/{provider_id}` | Provider 详情 |
| POST | `/rag/providers` | 新增 Provider |
| PUT | `/rag/providers/{provider_id}` | 更新 Provider |
| DELETE | `/rag/providers/{provider_id}` | 删除 Provider |
| POST | `/rag/providers/{provider_id}/enable` | 启用 / 停用 |
| POST | `/rag/providers/{provider_id}/probe` | 健康检查 |
| POST | `/rag/retrieve` | 检索 |
| POST | `/rag/providers/{provider_id}/ingest` | 灌入文档 |
| GET | `/rag/providers/{provider_id}/knowledge/documents` | 文档列表 |
| DELETE | `/rag/providers/{provider_id}/knowledge/documents/{document_id}` | 删除文档 |
| GET | `/rag/providers/{provider_id}/knowledge/stats` | 知识统计 |
| POST | `/rag/discover` | 从 MCP 发现 RAG |
| GET | `/settings/model` | 模型配置 |
| PUT | `/settings/model` | 更新模型配置 |
| GET | `/settings/feedback` | 反馈开关 |
| GET | `/settings/context` | 上下文压缩配置 |
| PUT | `/settings/context` | 更新上下文压缩配置 |
| GET | `/settings/archive` | 自动归档配置 |
| PUT | `/settings/archive` | 更新自动归档配置 |
| GET | `/settings/sandbox` | 沙箱配置与状态 |
| GET | `/settings/sandbox/status` | 执行模式 |
| GET | `/settings/readiness` | 首屏运行自检（LLM / 沙箱） |
| PUT | `/settings/sandbox` | 更新沙箱配置 |
| POST | `/settings/sandbox/check` | 探测沙箱端口 |
| GET | `/agents` | MyAgent 列表 |
| GET | `/agents/{agent_id}` | MyAgent 详情 |
| POST | `/agents` | 新增 MyAgent |
| PUT | `/agents/{agent_id}` | 更新 MyAgent |
| DELETE | `/agents/{agent_id}` | 删除 MyAgent |
| GET | `/skills` | 技能列表 |
| GET | `/skills/content` | 技能详情 |
| POST | `/skills/toggle` | 启停技能 |
| DELETE | `/skills/item` | 删除技能 |
| POST | `/skills/create` | 手动创建技能 |
| POST | `/skills/upload` | 上传 ZIP 安装 |
| POST | `/skills/download-url` | 从 URL 安装 |
| GET | `/skills/jobs` / `/skills/jobs/{job_id}` | 安装任务 |
| GET | `/skills/storage` | 技能存储路径 |
| POST | `/skills/reveal` | 打开技能目录 |
| GET | `/task-flows` | 任务流列表 |
| POST | `/task-flows` | 新建任务流 |
| GET | `/task-flows/{flow_id}` | 任务流详情 |
| PUT | `/task-flows/{flow_id}` | 更新任务流 |
| DELETE | `/task-flows/{flow_id}` | 删除任务流 |
| POST | `/task-flows/{flow_id}/publish` | 发布 |
| POST | `/task-flows/{flow_id}/fork` | 复制 |
| GET | `/task-flows/{flow_id}/executions` | 执行记录 |
| GET | `/task-flows/executions/{execution_id}` | 执行详情 |
| GET | `/projects` | 项目列表 |
| POST | `/projects` | 新建 / 附着项目 |
| POST | `/projects/validate` | 校验目录 |
| POST | `/projects/{project_id}/validate` | 重新校验 |
| PATCH | `/projects/{project_id}` | 重命名 / 置顶 |
| DELETE | `/projects/{project_id}` | 移出登记 |
| GET | `/projects/{project_id}/conversations` | 项目会话 |
| GET | `/projects/{project_id}/artifacts` | 项目产物 |

---

## 3. MCP 服务管理

### 3.1 运行时与配置

**GET** `/mcp/runtime`

```json
{
  "initialized": true,
  "config_error": "",
  "pool": {},
  "server_count": 3,
  "main_servers": ["crm", "markmap"]
}
```

**GET** `/mcp/config`

```json
{"format": "json", "config_path": "F:\\...\\mcp\\servers.json", "root": "F:\\...\\mcp", "config_error": ""}
```

### 3.2 导入前预检

**POST** `/mcp/preflight`

| 字段 | 类型 | 必填 | 默认 | 说明 |
| :-- | :-- | :--: | :-- | :-- |
| `json_text` | string | 二选一 | `""` | 粘贴的厂商 JSON |
| `json_data` | object | 二选一 | `null` | 直接传对象，优先 |
| `enabled` | bool | 否 | `true` | 导入后是否启用 |
| `integration` | bool | 否 | `false` | 标记为集成项目 |

支持的 JSON 容器：`mcpServers` / `mcp_servers` / `servers`，或直接服务映射。

```json
{
  "servers": [
    {
      "server_id": "crm",
      "transport": "network",
      "ready": true,
      "requires_api_key": true,
      "checks": [],
      "hint": "这是网络传输的 MCP：通常需要在 JSON 的 headers 中配置 API Key。"
    }
  ],
  "invalid": [],
  "ready": true,
  "requires_api_key": ["crm"],
  "npm_cache": null
}
```

### 3.3 导入服务

**POST** `/mcp/import`，请求体同 3.2。

```json
{
  "ok": true,
  "imported": ["crm"],
  "failed": [],
  "servers": [{"server_id": "crm", "state": "Dormant", "tool_count": 0}],
  "config_path": "F:\\...\\mcp\\servers.json"
}
```

导入后服务状态为 `Dormant`，不会立即连接。

### 3.4 服务列表与详情

**GET** `/mcp/servers`

| 参数 | 类型 | 必填 | 默认 | 说明 |
| :-- | :-- | :--: | :-- | :-- |
| `page` | int | 否 | 1 | 页码 |
| `page_size` | int | 否 | 20 | 1–100 |
| `keyword` | string | 否 | `""` | 模糊匹配 |
| `state` | string | 否 | `""` | 状态过滤 |
| `enabled` | bool | 否 | - | 启用过滤 |
| `integration` | bool | 否 | - | 集成项目过滤 |

```json
{
  "items": [
    {
      "server_id": "crm",
      "transport": "streamable_http",
      "connection": {"url": "https://api.example.com/mcp", "headers": {"Authorization": "***"}},
      "enabled": true,
      "preconnect": false,
      "integration": true,
      "criticality": "optional",
      "namespace": "crm",
      "allow_tools": [],
      "deny_tools": [],
      "connect_timeout_ms": 15000,
      "call_timeout_ms": 120000,
      "has_secret": true,
      "state": "Dormant",
      "connected": false,
      "tool_count": 0,
      "tool_source": "none",
      "error": "",
      "error_code": "",
      "error_category": "",
      "retry_at": null,
      "retry_count": 0,
      "epoch": 1,
      "connected_at": null,
      "last_used_at": null,
      "latency_ms": null,
      "pool_key": "streamable_http:abc123",
      "pool_reused": false,
      "conflicts": {}
    }
  ],
  "total": 1,
  "page": 1,
  "page_size": 20,
  "pages": 1,
  "config_error": "",
  "pool": {}
}
```

**GET** `/mcp/servers/{server_id}` 返回同结构，另加 `tools[]` 与 `pool_stats`。

**状态值**

| 状态 | 含义 |
| :-- | :-- |
| `Dormant` | 已配置未连接 |
| `Disabled` | 已停用 |
| `Starting` | 正在连接 |
| `Connected` | 可用 |
| `Degraded` | 可用但延迟超标 |
| `Pending` | 瞬时失败，等待重试 |
| `NeedsAuth` | 需要重新授权 |
| `Failed` | 配置或不可恢复错误 |
| `Stopping` | 正在断开 |

### 3.5 新增 / 更新 / 删除

**POST** `/mcp/servers`、**PUT** `/mcp/servers/{server_id}`

| 字段 | 类型 | 必填 | 默认 | 说明 |
| :-- | :-- | :--: | :-- | :-- |
| `server_id` | string | 是 | - | 1–64 字符 |
| `description` | string | 否 | `""` | 描述 |
| `transport` | string | 否 | `stdio` | `stdio` / `streamable_http` / `sse` / `http` |
| `command` / `args` / `env` / `cwd` | - | stdio 必填 command | - | stdio 参数 |
| `url` / `headers` | - | 网络必填 url | - | 网络参数 |
| `enabled` / `preconnect` | bool | 否 | `true` / `false` | 启用与预连接 |
| `integration` | bool | 否 | `false` | 集成项目 |
| `criticality` | string | 否 | `optional` | `required` / `optional` |
| `namespace` | string | 否 | `server_id` | 工具前缀 |
| `allow_tools` / `deny_tools` | string[] | 否 | `[]` | 白名单 / 黑名单 |
| `connect_timeout_ms` / `call_timeout_ms` | int | 否 | 15000 / 120000 | 超时 |
| `retry` / `health_check` | object | 否 | 继承 defaults | 重试与健康检查 |
| `tags` | string[] | 否 | `[]` | 标签 |

响应为服务详情。同名新增返回 409 `MCP_SERVER_EXISTS`，配置非法返回 422 `MCP_CONFIG_INVALID`。

**DELETE** `/mcp/servers/{server_id}` → `{"ok": true}`。

### 3.6 启用 / 停用

**POST** `/mcp/servers/{server_id}/enable`

```json
{"enabled": true, "discover": false}
```

- `enabled`：启用或停用。
- `discover`：启用后立即探测一次。

响应为服务详情。启用默认只改配置，不建立连接。

### 3.7 测试连接

**POST** `/mcp/servers/{server_id}/probe`

```json
{
  "probe": {"state": "Connected", "reused": false},
  "server": {"server_id": "crm", "state": "Connected", "connected": true, "tool_count": 3}
}
```

连接失败通常仍返回 200，需要检查 `server.state` 与 `probe.error`。服务不存在返回 404，已停用返回 503。

### 3.8 重启

**POST** `/mcp/servers/{server_id}/restart`

关闭旧连接、重置重试与熔断，状态回到 `Dormant`。响应为服务详情。

### 3.9 工具清单

**GET** `/mcp/servers/{server_id}/tools`

| 参数 | 类型 | 必填 | 默认 | 说明 |
| :-- | :-- | :--: | :-- | :-- |
| `discover` | bool | 否 | `false` | `true` 时先建立连接发现工具 |

```json
{
  "server_id": "crm",
  "state": "Dormant",
  "tools": [
    {
      "server_id": "crm",
      "remote_name": "query_customer",
      "exposed_name": "crm__query_customer",
      "description": "按客户 ID 查询客户信息",
      "input_schema": {"type": "object"},
      "allowlisted": true,
      "source": "lkg_cache"
    }
  ],
  "conflicts": {}
}
```

### 3.10 测试调用工具

**POST** `/mcp/servers/{server_id}/tools/{tool_name}/test`

```json
{"arguments": {"customer_id": "C1001"}}
```

```json
{"server_id": "crm", "tool": "query_customer", "state": "Connected", "text": "客户 C1001：张三"}
```

工具执行失败时 `text` 是统一错误对象的 JSON 字符串，HTTP 仍为 200。被白名单拦截返回 403 `MCP_TOOL_BLOCKED`。

### 3.11 日志

**GET** `/mcp/servers/{server_id}/logs?limit=200`

```json
{"server_id": "crm", "logs": [{"level": "info", "message": "连接成功"}]}
```

### 3.12 集成项目列表

**GET** `/mcp/integrations`

```json
{"items": [{"server_id": "crm", "state": "Connected", "integration": true, "tool_count": 3}], "total": 1}
```

---

## 4. RAG Provider 管理

### 4.1 配置与列表

**GET** `/rag/config`

```json
{"format": "json", "config_path": "F:\\...\\rag\\providers.json", "provider_count": 2}
```

**GET** `/rag/providers`

| 参数 | 类型 | 必填 | 默认 |
| :-- | :-- | :--: | :-- |
| `protocol` | string | 否 | `""`（`http` / `mcp`） |
| `enabled` | bool | 否 | - |
| `integration_server_id` | string | 否 | `""` |

```json
{
  "items": [
    {
      "provider_id": "rag_crm",
      "name": "CRM 知识库",
      "protocol": "mcp",
      "mcp_server_id": "crm",
      "capabilities": ["retrieve", "health"],
      "knowledge_domains": ["contract"],
      "citation_format": "chunk",
      "limits": {"top_k_max": 20, "max_chars_per_chunk": 2000},
      "tools": {"retrieve": "rag_retrieve", "manifest": "rag_manifest"},
      "timeout_ms": 30000,
      "enabled": true,
      "source": "mcp_discovery",
      "has_secret": false,
      "state": "Ready",
      "ready": true,
      "last_error": "",
      "last_checked_at": "2026-11-11T08:00:00Z",
      "latency_ms": 12,
      "document_count": 128
    }
  ],
  "total": 1
}
```

**GET** `/rag/providers/{provider_id}` 返回同结构单条。不存在返回 404 `RAG_PROVIDER_NOT_FOUND`。

### 4.2 新增 / 更新

**POST** `/rag/providers`、**PUT** `/rag/providers/{provider_id}`

| 字段 | 类型 | 必填 | 默认 | 说明 |
| :-- | :-- | :--: | :-- | :-- |
| `provider_id` | string | 是 | - | 1–64，建议 `rag_<domain>` |
| `name` | string | 否 | provider_id | 展示名 |
| `description` | string | 否 | `""` | 描述 |
| `protocol` | string | 否 | `http` | `http` / `mcp` |
| `endpoint` | string | http 必填 | `""` | HTTPS 地址 |
| `mcp_server_id` | string | mcp 必填 | `""` | 已配置的 MCP 服务 ID |
| `auth` | object | 否 | `{}` | `{"type":"bearer","secret_ref":"RAG_TOKEN"}` |
| `capabilities` | string[] | 否 | `["retrieve","health"]` | `retrieve` / `ingest` / `health` / `list_documents` / `delete_document` / `stats` |
| `knowledge_domains` | string[] | 否 | `[]` | 知识域 |
| `citation_format` | string | 否 | `chunk` | `chunk` / `document` / `custom` |
| `limits` | object | 否 | `{}` | `top_k_max` / `max_chars_per_chunk` / `qps` |
| `tools` | object | 否 | 默认工具名 | 工具名映射 |
| `timeout_ms` | int | 否 | 30000 | 超时 |
| `enabled` | bool | 否 | `true` | 启用 |
| `integration_server_id` | string | 否 | `""` | 关联的集成 MCP |

响应为 Provider 详情。`PUT` 时路径与请求体 `provider_id` 必须一致，否则 422 `RAG_CONFIG_INVALID`。

**DELETE** `/rag/providers/{provider_id}` → `{"ok": true}`。

### 4.3 启用 / 停用 / 健康检查

**POST** `/rag/providers/{provider_id}/enable`

```json
{"enabled": true}
```

**POST** `/rag/providers/{provider_id}/probe`

做一次健康检查，返回 Provider 详情；失败时 `state` 为 `Unavailable` 并带 `last_error`。

### 4.4 检索

**POST** `/rag/retrieve`

| 字段 | 类型 | 必填 | 默认 | 说明 |
| :-- | :-- | :--: | :-- | :-- |
| `query` | string | 是 | - | 1–2000 字符 |
| `top_k` | int | 否 | 5 | 1–50 |
| `provider_ids` | string[] | 否 | `[]` | 为空表示全部启用 Provider |
| `filters` | object | 否 | `{}` | 过滤条件 |
| `user_scope` | object | 否 | `{}` | 权限范围 |
| `trace_id` | string | 否 | `""` | 追踪 ID |

```json
{
  "query": "合同续签流程",
  "top_k": 5,
  "chunks": [
    {
      "citation_id": "rag_crm:c1",
      "content": "合同续签需在到期前 30 天发起……",
      "score": 0.82,
      "source": {"title": "合同管理办法", "uri": "kb://contract/001", "page": 3},
      "metadata": {"domain": "contract"},
      "provider_id": "rag_crm",
      "provider_name": "CRM 知识库"
    }
  ],
  "providers": ["rag_crm"],
  "errors": []
}
```

- 无可用 Provider 时返回空 `chunks` 并附 `note`。
- 全部 Provider 失败返回 503 `RAG_UNAVAILABLE`。
- 部分失败时 `errors[]` 记录失败原因，成功结果照常返回。

### 4.5 灌入与知识管理

**POST** `/rag/providers/{provider_id}/ingest`

```json
{
  "documents": [
    {"document_id": "doc-001", "title": "合同管理办法", "uri": "kb://contract/001", "content": "全文", "metadata": {"domain": "contract"}}
  ],
  "options": {"overwrite": true}
}
```

```json
{"ok": true, "provider_id": "rag_crm", "result": {"accepted": 1, "document_ids": ["doc-001"]}}
```

**GET** `/rag/providers/{provider_id}/knowledge/documents?limit=50&cursor=`

**DELETE** `/rag/providers/{provider_id}/knowledge/documents/{document_id}`

**GET** `/rag/providers/{provider_id}/knowledge/stats`

```json
{"provider_id": "rag_crm", "stats": {"document_count": 128, "chunk_count": 5400}}
```

需要 Provider 在 `capabilities` 中声明对应能力，否则返回 422 `RAG_UNSUPPORTED`。

### 4.6 从 MCP 发现 RAG

**POST** `/rag/discover`

```json
{"mcp_server_id": "crm", "discovery_tool": "rag_manifest", "auto_register": true}
```

```json
{"server_id": "crm", "discovered": [{"provider_id": "rag_crm", "name": "CRM 知识库"}], "registered": true}
```

要求甲方 MCP 服务实现 `rag_manifest` 工具，返回 JSON 格式的 manifest。manifest 规范见 [MCP服务接入API.md](./MCP服务接入API.md) 第 5 章。

---

## 5. 设置

> 设置按用户隔离；会热重建该用户的 Agent 图。

### 5.1 模型

**GET** `/settings/model?user_id=`

```json
{
  "model_name": "deepseek-v4-flash",
  "base_url": "https://api.deepseek.com",
  "has_api_key": true,
  "thinking_enabled": true,
  "web_search_enabled": false,
  "context_limit": 128000
}
```

**PUT** `/settings/model?user_id=`

| 字段 | 类型 | 必填 | 说明 |
| :-- | :-- | :--: | :-- |
| `model_name` | string | 否 | 模型名 |
| `base_url` | string | 否 | 必须以 `http://` / `https://` 开头 |
| `api_key` | string | 否 | 非空才更新；空串保留原值 |
| `thinking_enabled` | bool | 否 | 思考模式 |
| `web_search_enabled` | bool | 否 | 联网搜索 |
| `context_limit` | int | 否 | 16000–2000000 |

```json
{"ok": true, "updated": ["model_name"], "current": {"model_name": "deepseek-v4-flash", "has_api_key": true}}
```

**GET** `/settings/feedback` → `{"enabled": true}`

### 5.2 上下文压缩

**GET** `/settings/context?user_id=`

```json
{
  "enabled": true,
  "trigger": 0.7,
  "hard": 0.85,
  "keep": 0.4,
  "alert_threshold": 0.6,
  "min_rounds": 4,
  "cooldown": 2,
  "max_defer": 3,
  "timeout_seconds": 60,
  "eval_enabled": false,
  "eval_judge": false,
  "heartbeat_seconds": 5,
  "budget": {}
}
```

**PUT** `/settings/context?user_id=`，字段同上（均可选）。

必须满足 `0 < keep < trigger < hard <= 1`，否则 422。响应 `{"ok": true, "updated": [], "current": {}}`。

### 5.3 自动归档

**GET** `/settings/archive?user_id=` → `{"days": 30}`

**PUT** `/settings/archive?user_id=`

```json
{"days": 30}
```

`0` 表示关闭自动归档；范围 0–3650。

```json
{"ok": true, "current": {"days": 30}}
```

### 5.4 沙箱

**GET** `/settings/sandbox?user_id=`

```json
{
  "sandbox": {
    "configured": true,
    "mode": "sandbox",
    "host": "127.0.0.1",
    "port": 18083,
    "has_api_key": true,
    "image": "myagent-sandbox:1",
    "default_timeout_seconds": 120,
    "sandbox_timeout_seconds": 3600,
    "prewarm_enabled": true,
    "use_server_proxy": true,
    "notice": ""
  },
  "status": {},
  "local_env": {},
  "integrity": {"level": "full", "low_integrity": false, "hint": ""},
  "paths": {}
}
```

**GET** `/settings/sandbox/status` → 执行模式摘要。

**PUT** `/settings/sandbox?user_id=`

| 字段 | 类型 | 必填 | 说明 |
| :-- | :-- | :--: | :-- |
| `host` | string | 否 | 主机 |
| `port` | int | 否 | 1–65535 |
| `api_key` | string | 否 | 非空才更新 |
| `clear_api_key` | bool | 否 | 清空密钥 |
| `image` | string | 否 | 镜像名 |
| `default_timeout_seconds` | int | 否 | 10–3600 |
| `sandbox_timeout_seconds` | int | 否 | 600–2592000 |
| `prewarm_enabled` | bool | 否 | 预热 |
| `use_server_proxy` | bool | 否 | 服务端代理 |

**POST** `/settings/sandbox/check?host=&port=&user_id=`

```json
{"reachable": true, "host": "127.0.0.1", "port": 18083, "message": "端口可达，凭据已配置。"}
```

### 5.5 运行自检

**GET** `/settings/readiness` → 工作台首屏调用；只读配置，不触发数据库或沙箱连接。

```json
{
  "ready": false,
  "blocking": true,
  "issues": ["未配置 DEEPSEEK_API_KEY，对话无法调用模型"],
  "llm": {"configured": false, "model_name": "deepseek-v4-flash", "base_url": "https://api.deepseek.com"},
  "sandbox": {"configured": true, "mode": "sandbox", "notice": ""},
  "env_path": "F:\\Python\\HarnessEngineering\\CommonProject\\.env"
}
```

`blocking=true` 表示 LLM 未配置、前端应阻断并引导到系统设置；沙箱未配置只写入 `issues`，不阻断。

---

## 6. MyAgent 档案

### 6.1 列表 / 详情

**GET** `/agents?user_id=`

```json
{"user_id": "u_1001", "items": [], "default_agent_id": "default"}
```

**GET** `/agents/{agent_id}?user_id=`

```json
{
  "id": "agent_ab12cd34",
  "name": "合同助理",
  "role_title": "法务支持",
  "description": "处理合同问题",
  "avatar": "合",
  "system_prompt": "你是合同助理……",
  "model": {"model_name": "deepseek-v4-flash", "base_url": "https://api.deepseek.com", "has_api_key": true},
  "mcp_server_ids": ["crm"],
  "enabled": true,
  "is_default": false,
  "created_at": "2026-11-11T08:00:00Z",
  "updated_at": "2026-11-11T08:00:00Z"
}
```

### 6.2 新增 / 更新 / 删除

**POST** `/agents?user_id=`（201）

| 字段 | 类型 | 必填 | 说明 |
| :-- | :-- | :--: | :-- |
| `name` | string | 是 | 1–80 |
| `role_title` | string | 否 | ≤80 |
| `description` | string | 否 | ≤500 |
| `avatar` | string | 否 | ≤2 字符 |
| `system_prompt` | string | 否 | ≤20000 |
| `model` | object | 否 | `model_name` / `base_url` / `api_key` / `thinking_enabled` / `context_limit` |
| `mcp_server_ids` | string[] | 否 | 绑定 MCP 服务 |
| `enabled` | bool | 否 | 默认 `true` |

**PUT** `/agents/{agent_id}?user_id=` 字段均可选。

**DELETE** `/agents/{agent_id}?user_id=` → `{"ok": true}`

---

## 7. 技能管理

### 7.1 列表

**GET** `/skills`

| 参数 | 类型 | 必填 | 默认 |
| :-- | :-- | :--: | :-- |
| `page` | int | 否 | 1 |
| `page_size` | int | 否 | 12（1–100） |
| `keyword` | string | 否 | `""` |
| `enabled` | bool | 否 | - |
| `target` | string | 否 | `""` |

```json
{
  "items": [
    {
      "skill_id": "main:docx",
      "name": "docx",
      "title": "Word 文档处理",
      "description": "读写 docx",
      "target": "main",
      "enabled": true,
      "protected": false,
      "file_count": 12,
      "size_bytes": 204800,
      "updated_at": "2026-11-01T08:00:00Z",
      "path": "F:\\...\\skills\\main\\docx"
    }
  ],
  "total": 1,
  "page": 1,
  "page_size": 12,
  "pages": 1
}
```

### 7.2 详情

**GET** `/skills/content?target=main&name=docx`

```json
{"skill": {}, "content": "SKILL.md 正文", "truncated": false}
```

### 7.3 启停 / 删除 / 创建

**POST** `/skills/toggle`

```json
{"target": "main", "name": "docx", "enabled": false}
```

响应为技能条目。

**DELETE** `/skills/item?target=main&name=docx` → `{"ok": true}`

**POST** `/skills/create`

```json
{"name": "my-skill", "description": "我的技能", "content": "正文"}
```

响应为技能条目；同名返回 409。

### 7.4 安装

**POST** `/skills/upload`（202，`multipart/form-data`）

| 字段 | 位置 | 类型 | 必填 |
| :-- | :-- | :-- | :--: |
| `file` | form | ZIP 文件 | 是 |
| `target` | form | string | 否（固定 `main`） |

**POST** `/skills/download-url`（202）

```json
{"url": "https://example.com/skill.zip"}
```

两者响应均为安装任务：

```json
{"job_id": "a1b2c3", "job": {"job_id": "a1b2c3", "kind": "zip", "label": "skill.zip", "status": "queued", "stage": "queued", "progress": 0, "message": "排队中", "error": "", "skill": null, "created_at": "...", "updated_at": "..."}}
```

**GET** `/skills/jobs`、**GET** `/skills/jobs/{job_id}` 查询进度。

### 7.5 存储与目录

**GET** `/skills/storage`

```json
{"skills_root": "F:\\...\\skills", "main_root": "F:\\...\\skills\\main", "disabled_root": "F:\\...\\skills-disabled", "max_package_mb": 32}
```

**POST** `/skills/reveal` → 在服务器本机打开技能目录。

```json
{"target": "main", "name": "docx"}
```

---

## 8. 任务流管理

### 8.1 列表 / 详情

**GET** `/task-flows?page=1&size=20&user_id=`

```json
{"items": [], "total": 0, "page": 1, "size": 20}
```

**GET** `/task-flows/{flow_id}` 返回任务流记录；内置流对所有用户只读。

### 8.2 新建 / 更新

**POST** `/task-flows`（201）、**PUT** `/task-flows/{flow_id}`

```json
{
  "payload": {
    "schema_version": "1.0",
    "name": "深度研究报告",
    "description": "给定主题检索并产出报告",
    "trigger_examples": ["帮我调研一下", "做个研究报告"],
    "mcp_services": ["crm"],
    "steps": [
      {
        "id": "s1",
        "name": "需求澄清",
        "instruction": "确认主题与输出格式",
        "skills": "auto",
        "executor": "main",
        "constraints": "只澄清，不检索"
      },
      {
        "id": "s2",
        "name": "并行检索",
        "instruction": "分头检索并提炼要点",
        "skills": "auto",
        "executor": "auto",
        "checkpoint": true,
        "parallel": [
          {"name": "中文资料", "steps": [{"id": "s2a", "name": "中文检索", "instruction": "检索中文资料", "executor": "auto"}]},
          {"name": "英文资料", "steps": [{"id": "s2b", "name": "英文检索", "instruction": "检索英文资料", "executor": "auto"}]}
        ]
      }
    ]
  }
}
```

```json
{"id": "tf_xxx", "status": "draft", "schema_hash": "sha256:..."}
```

字段说明：

| 字段 | 说明 |
| :-- | :-- |
| `steps[].executor` | `main` / `auto` / 指定子 Agent |
| `steps[].skills` | `auto` 或技能名列表 |
| `steps[].checkpoint` | 是否在该步骤暂停 |
| `steps[].parallel` | 并行分支，每项含 `name` 与 `steps[]` |
| `steps[].example` | 输出示例 |

校验失败返回 422，`detail` 为问题数组（含 `step_id`）。

### 8.3 发布 / 复制 / 删除

| 方法 | 路径 | 说明 |
| :-- | :-- | :-- |
| POST | `/task-flows/{flow_id}/publish` | 发布，返回 `{"id","status":"published","schema_hash"}` |
| POST | `/task-flows/{flow_id}/fork`（201） | 复制为我的 draft，返回 `{"id","status":"draft","forked_from"}` |
| DELETE | `/task-flows/{flow_id}`（204） | 删除；内置流返回 403 |

### 8.4 执行记录

**GET** `/task-flows/{flow_id}/executions?page=1&size=20`

```json
{"items": [], "total": 0, "page": 1, "size": 20}
```

**GET** `/task-flows/executions/{execution_id}` 返回单条执行详情（含步骤轨迹与状态）。

---

## 9. 项目管理

### 9.1 列表

**GET** `/projects?user_id=`

```json
{
  "projects": [
    {
      "id": "p1",
      "name": "合同项目",
      "path": "F:\\data\\projects\\p1",
      "pinned": false,
      "marker_state": "ok",
      "status": "ready",
      "conversation_count": 3,
      "created_at": "2026-11-01T08:00:00Z",
      "updated_at": "2026-11-11T08:00:00Z",
      "last_opened_at": "2026-11-11T08:00:00Z",
      "is_default": false
    }
  ]
}
```

### 9.2 新建 / 附着

**POST** `/projects?user_id=`

| 字段 | 类型 | 必填 | 默认 | 说明 |
| :-- | :-- | :--: | :-- | :-- |
| `mode` | string | 否 | `attach` | `create` / `attach` |
| `name` | string | 否 | `""` | 项目名 |
| `path` | string | 是 | - | 目录路径 |
| `adopt` | bool | 否 | `false` | 接管已有标记 |

响应为项目对象。

### 9.3 校验

**POST** `/projects/validate`

```json
{"path": "F:\\data\\projects\\p1"}
```

```json
{"ok": true, "path": "F:\\data\\projects\\p1", "marker_state": "ok", "message": "目录可用", "project_id": "p1"}
```

**POST** `/projects/{project_id}/validate?user_id=&repair=true`

重新校验；`repair=true` 时重建标记。返回项目对象。

### 9.4 修改 / 删除

**PATCH** `/projects/{project_id}?user_id=`

```json
{"name": "新名称", "pinned": true}
```

**DELETE** `/projects/{project_id}?user_id=` → `{"ok": true}`

只移出登记，不删除磁盘目录。

### 9.5 项目会话与产物

**GET** `/projects/{project_id}/conversations?user_id=`

```json
{"sessions": []}
```

**GET** `/projects/{project_id}/artifacts?user_id=&limit=200`

```json
{"artifacts": []}
```

---

## 10. 权限与安全建议

| 接口组 | 建议权限 |
| :-- | :-- |
| `/mcp/*` 读 | 管理员 / 运维 |
| `/mcp/*` 写（含 `stdio.command`） | 超级管理员 |
| `/rag/*` 写 | 管理员 |
| `/settings/model`、`/settings/sandbox` | 超级管理员（涉及密钥） |
| `/settings/context`、`/settings/archive` | 普通用户可改自己的 |
| `/agents/*` | 用户可管自己的档案 |
| `/skills/*` 写 | 管理员（会落盘并影响所有会话） |
| `/task-flows/*` | 用户可管自己的；内置流只读 |
| `/projects/*` | 用户可管自己的登记 |

工程要求：

1. 网关必须剥离客户端自带的 `X-User-Id`，注入可信身份。
2. `/settings/model` 的 `api_key` 只写不读，响应只返回 `has_api_key`。
3. `/mcp/servers` 响应中的 `headers` / `env` 已脱敏为 `***`，不要把明文写回前端。
4. `POST /skills/reveal` 会在服务器本机弹窗，公网部署应禁用。
5. 所有写操作都建议记录审计：操作人、时间、对象、变更字段、结果。
