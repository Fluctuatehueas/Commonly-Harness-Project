# MCP 服务接入 API 文档

> 版本：v1.0 · 2026-11-10
> 读者：甲方后端开发
> 目标：甲方后端按本文实现后，可作为一个 MCP 服务被接入为「集成项目」，并可被发现其中的 RAG 知识库。

本文只写**甲方需要实现什么**，不涉及接入方内部实现。

---

## 目录

- [1. 公共约束](#1-公共约束)
- [2. 需要实现的 API](#2-需要实现的-api)
- [3. MCP 协议实现](#3-mcp-协议实现)
- [4. 业务工具定义](#4-业务工具定义)
- [5. RAG 详细规范](#5-rag-详细规范)
- [6. 代码示例](#6-代码示例)
- [7. 自测清单](#7-自测清单)

---

## 1. 公共约束

### 1.1 传输方式

| 项 | 约定 |
| :-- | :-- |
| 协议 | JSON-RPC 2.0 |
| 传输 | Streamable HTTP（推荐）或 SSE |
| 地址 | `https://{甲方域名}/mcp` |
| 请求编码 | `Content-Type: application/json; charset=utf-8` |
| 响应编码 | `application/json` 或 `text/event-stream` |

**必须接受的请求头**

```http
Content-Type: application/json
Accept: application/json, text/event-stream
Authorization: Bearer <token>
MCP-Protocol-Version: 2025-06-18
MCP-Session-Id: <initialize 之后调用方带回>
```

- 初始化响应建议返回 `Mcp-Session-Id`，后续请求都会携带。
- 鉴权只放 Header，禁止放在 URL、工具参数、日志里。
- 生产环境必须 HTTPS；内网可加 mTLS。

### 1.2 JSON-RPC 消息

请求：

```json
{"jsonrpc": "2.0", "id": "req-1", "method": "tools/call", "params": {}}
```

响应：

```json
{"jsonrpc": "2.0", "id": "req-1", "result": {}}
```

通知（没有 `id`，如 `notifications/initialized`、`notifications/cancelled`）：返回 HTTP 202，空响应体。

### 1.3 工具结果结构

```json
{
  "content": [{"type": "text", "text": "给人和模型看的文本"}],
  "structuredContent": {"customer_id": "C1001", "name": "张三"},
  "isError": false
}
```

| 字段 | 必填 | 说明 |
| :-- | :--: | :-- |
| `content` | 是 | 文本块数组；至少一个 |
| `structuredContent` | 否 | 结构化结果，小结果优先用它 |
| `isError` | 是 | 失败时必须为 `true` |

### 1.4 错误结构

工具执行失败时返回 `isError: true`，并给出统一错误对象：

```json
{
  "content": [
    {
      "type": "text",
      "text": "{\"code\":\"NOT_FOUND\",\"message\":\"客户不存在\",\"category\":\"business\",\"retryable\":false,\"details\":{\"customer_id\":\"C1001\"}}"
    }
  ],
  "isError": true
}
```

| 字段 | 必填 | 说明 |
| :-- | :--: | :-- |
| `code` | 是 | 稳定错误码，如 `INVALID_ARGUMENT` |
| `message` | 是 | 可读信息，要能指导修正 |
| `category` | 是 | `tool` / `business` / `security` / `runtime` |
| `retryable` | 是 | 是否可安全重试 |
| `details` | 否 | 结构化细节，不含敏感信息 |

### 1.5 通用错误码

| 错误码 | 含义 | 可重试 |
| :-- | :-- | :--: |
| `INVALID_ARGUMENT` | 参数缺失或格式错误 | 否 |
| `UNAUTHORIZED` | 未认证 | 否 |
| `FORBIDDEN` | 无权限 | 否 |
| `NOT_FOUND` | 对象不存在 | 否 |
| `CONFLICT` | 状态/并发冲突 | 视情况 |
| `RATE_LIMITED` | 触发限流 | 是 |
| `TIMEOUT` | 超时 | 是 |
| `UPSTREAM_UNAVAILABLE` | 上游不可用 | 是 |
| `BUSINESS_RULE_VIOLATION` | 业务规则不允许 | 否 |
| `NEEDS_CLARIFICATION` | 信息不足 | 否 |
| `CANCELLED` | 被取消 | 否 |

### 1.6 超时、重试、幂等

| 项 | 约定 |
| :-- | :-- |
| 连接超时 | 15s（含首次 `tools/list`） |
| 单次工具超时 | 120s |
| 超时处理 | 收到 `notifications/cancelled` 后停止执行；已产生的副作用必须可查询、可补偿 |
| 重试 | 只对 `retryable: true` 的错误重试，指数退避，最多 4 次 |
| 幂等 | 有副作用的工具必须支持 `idempotency_key`（`arguments` 顶层字段），同一键重复调用返回同一结果，建议保留 24h |

### 1.7 数据格式

- 统一 UTF-8。
- 时间用 ISO 8601 UTC，如 `2026-11-10T08:00:00Z`。
- ID 一律字符串。
- 金额用十进制字符串或最小货币单位整数，不用浮点。
- 禁止返回堆栈、SQL、密钥、绝对路径。

---

## 2. 需要实现的 API

### 2.1 API 一览

| 方法 | 路径 | 作用 | 是否必选 |
| :-- | :-- | :-- | :--: |
| POST | `/mcp` | MCP 协议入口（initialize / tools/list / tools/call / ping） | 必选 |
| GET | `/mcp` | 服务端 SSE 推送（一般可返回 405） | 可选 |
| DELETE | `/mcp` | 结束会话 | 可选 |
| GET | `/health` | 健康检查 | 必选 |
| POST | `/retrieve` | RAG 检索（HTTP 模式） | 条件必选 |
| POST | `/knowledge/ingest` | 知识灌入（HTTP 模式） | 可选 |
| GET | `/knowledge/documents` | 文档列表（HTTP 模式） | 可选 |
| DELETE | `/knowledge/documents/{document_id}` | 删除文档（HTTP 模式） | 可选 |
| GET | `/knowledge/stats` | 知识统计（HTTP 模式） | 可选 |

> RAG 二选一：走 MCP 工具（推荐，无需额外 HTTP 接口），或走 HTTP 接口（上表后五个）。

### 2.2 MCP 工具一览

**业务工具（按实际业务提供）**

| 工具名 | 作用 |
| :-- | :-- |
| `query_customer` | 按客户 ID 查询客户信息（只读） |
| `search_orders` | 按条件分页查询订单（只读） |
| `create_ticket` | 创建工单（写入，需幂等） |
| `update_ticket_status` | 更新工单状态（写入，需幂等） |
| `delete_record` | 删除业务记录（破坏性，需审批） |
| `generate_report` | 生成报表（长任务，返回 `task_id`） |

以上只是示例清单，工具名按业务自定义，但必须遵守第 4 章规范。

**RAG 工具（做知识库时提供）**

| 工具名 | 作用 | 是否必选 |
| :-- | :-- | :--: |
| `rag_manifest` | 上报知识库信息（发现入口） | 必选 |
| `rag_retrieve` | 检索知识片段 | 必选 |
| `rag_ingest` | 灌入文档 | 可选 |
| `rag_list_documents` | 文档列表 | 可选 |
| `rag_delete_document` | 删除文档 | 可选 |
| `rag_knowledge_stats` | 知识统计 | 可选 |
| `rag_health` | 知识库健康检查 | 可选 |

**最小必选集合**

- 协议方法：`initialize`、`tools/list`、`tools/call`、`ping`
- 业务工具：至少 1 个，名称按业务自定义
- 如果提供 RAG：`rag_manifest` + `rag_retrieve`（名称固定）

---

## 3. MCP 协议实现

### 3.1 initialize

**请求**

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": "2025-06-18",
    "capabilities": {},
    "clientInfo": {"name": "harness", "version": "1.0.0"}
  }
}
```

**响应**

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": "2025-06-18",
    "capabilities": {"tools": {"listChanged": false}},
    "serverInfo": {"name": "crm-mcp", "version": "1.0.0"}
  }
}
```

要求：

- 必须返回 `protocolVersion`、`capabilities`、`serverInfo`。
- 未声明的能力不会被调用；`capabilities.tools` 必须声明。
- 版本不兼容时返回 JSON-RPC 错误，不要假装成功。

初始化完成后客户端会发送 `notifications/initialized`，返回 202 即可。

### 3.2 tools/list

**请求**

```json
{"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
```

**响应**

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "tools": [
      {
        "name": "query_customer",
        "description": "用途：按客户 ID 查询客户信息。何时使用：需要客户资料时。副作用：read。",
        "inputSchema": {
          "type": "object",
          "additionalProperties": false,
          "required": ["customer_id"],
          "properties": {
            "customer_id": {"type": "string", "minLength": 1, "maxLength": 64, "description": "客户 ID"}
          }
        },
        "annotations": {
          "readOnlyHint": true,
          "destructiveHint": false,
          "idempotentHint": true,
          "openWorldHint": false
        }
      }
    ]
  }
}
```

### 3.3 tools/call

**请求**

```json
{
  "jsonrpc": "2.0",
  "id": "call-1",
  "method": "tools/call",
  "params": {
    "name": "query_customer",
    "arguments": {"customer_id": "C1001"},
    "_meta": {"trace_id": "trace-1", "user_id": "u_1001", "tenant_id": "t_001"}
  }
}
```

**成功响应**

```json
{
  "jsonrpc": "2.0",
  "id": "call-1",
  "result": {
    "content": [{"type": "text", "text": "客户 C1001：张三，状态 active"}],
    "structuredContent": {"customer_id": "C1001", "name": "张三", "status": "active"},
    "isError": false
  }
}
```

`_meta` 用于透传 Trace 与用户上下文，可以选择不用，但不能篡改。

### 3.4 ping

```json
{"jsonrpc": "2.0", "id": 3, "method": "ping"}
```

响应 `result: {}`，必须快速返回。

### 3.5 notifications/cancelled

用户停止、调用超时、会话关闭时客户端会发送：

```json
{"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": "call-1"}}
```

收到后应停止执行。无法取消的长任务必须在工具描述里注明。

### 3.6 其他要求

- 未知方法返回 JSON-RPC 错误 `-32601`。
- 工具不存在返回 `isError: true` + `NOT_FOUND`。
- 服务端必须自行校验参数，不能假设调用方已经校验过。
- HTTP 状态码统一 200（协议层错误可用 4xx/5xx），业务失败通过 `isError` 表达。

---

## 4. 业务工具定义

### 4.1 命名

| 规则 | 说明 |
| :-- | :-- |
| 字符集 | `[a-z0-9_]`，小写蛇形 |
| 长度 | ≤ 64 |
| 唯一 | 同一服务内不得重名 |
| 禁用 | 不要用 `execute_sql`、`run_shell`、`call_any_api` 这类万能工具 |

### 4.2 描述模板

```text
用途：一句话说明这个工具做什么。
何时使用：在什么业务场景、满足什么条件时调用。
不适用：什么情况下不要调用。
副作用：read / write / destructive / external。
权限：需要什么角色或数据范围。
幂等：是否支持 idempotency_key，重复调用行为。
限制：返回大小、分页、速率限制。
```

### 4.3 参数 Schema

- 必须提供 `inputSchema`。
- 每个字段必须有 `description`、类型、是否必填。
- 枚举必须列全。
- 字符串设 `maxLength`，数组设 `maxItems`，数字设范围。
- 建议 `additionalProperties: false`。
- Schema 里不能放密钥、真实数据、内部 URL。

### 4.4 注解

| 注解 | 含义 | 调用方行为 |
| :-- | :-- | :-- |
| `readOnlyHint: true` | 只读 | 可直接执行 |
| `destructiveHint: true` | 破坏性 | 需要人工审批 |
| `idempotentHint: true` | 幂等 | 可用同一幂等键重试 |
| `openWorldHint: true` | 外部副作用 | 默认审批 |
| 不提供注解 | 视为有副作用 | 默认审批 |

### 4.5 返回值

- 小结果优先 `structuredContent`。
- 非结构化内容放 `content` 文本。
- 大结果返回引用，不内联：

```json
{
  "file_id": "f_001",
  "uri": "https://files.example.com/f_001",
  "mime_type": "application/pdf",
  "size": 1048576,
  "sha256": "..."
}
```

- 单次返回文本建议不超过 8KB；超长要截断并标注 `truncated: true`。
- 失败必须 `isError: true`，并给出可操作的修正建议。

### 4.6 工具清单模板

交付时每个工具按此模板登记：

```json
{
  "name": "create_ticket",
  "description": "用途：创建工单。何时使用：用户确认问题需要跟进时。副作用：write。幂等：支持 idempotency_key。",
  "inputSchema": {
    "type": "object",
    "additionalProperties": false,
    "required": ["title", "idempotency_key"],
    "properties": {
      "title": {"type": "string", "minLength": 1, "maxLength": 120, "description": "工单标题"},
      "detail": {"type": "string", "maxLength": 4000, "description": "问题描述"},
      "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 128, "description": "幂等键"}
    }
  },
  "annotations": {
    "readOnlyHint": false,
    "destructiveHint": false,
    "idempotentHint": true,
    "openWorldHint": false
  },
  "timeout_ms": 10000,
  "version": "1.0.0"
}
```

---

## 5. RAG 详细规范

### 5.1 两种接入方式

| 方式 | 做法 | 适用 |
| :-- | :-- | :-- |
| MCP 工具 | 在同一个 MCP 服务上暴露 `rag_manifest` / `rag_retrieve` 等工具 | 推荐 |
| HTTP 接口 | 提供 `/retrieve`、`/knowledge/*` 等 REST 接口 | 已有独立检索服务 |

两种方式的数据结构完全一致，只是传输不同。

### 5.2 rag_manifest

**作用**：告诉调用方“我有知识库，能力是什么，怎么调”。

**入参**：无（`arguments: {}`）。

**返回格式**：必须把 JSON 放进 `content[].text`（能被 `json.loads` 解析），不要只返回自然语言。

**出参**：支持单个对象、数组、`{"provider": {...}}`、`{"providers": [...]}`。推荐单个对象：

```json
{
  "provider_id": "rag_crm",
  "name": "CRM 知识库",
  "protocol": "mcp",
  "description": "合同、产品与工单知识",
  "capabilities": ["retrieve", "ingest", "health", "list_documents", "delete_document", "stats"],
  "knowledge_domains": ["contract", "product", "ticket"],
  "citation_format": "chunk",
  "limits": {"top_k_max": 20, "max_chars_per_chunk": 2000, "qps": 10},
  "tools": {
    "retrieve": "rag_retrieve",
    "ingest": "rag_ingest",
    "list_documents": "rag_list_documents",
    "delete_document": "rag_delete_document",
    "stats": "rag_knowledge_stats",
    "health": "rag_health"
  },
  "timeout_ms": 30000,
  "enabled": true
}
```

| 字段 | 必填 | 说明 |
| :-- | :--: | :-- |
| `provider_id` | 是 | 全局唯一，建议 `rag_<domain>`；不传时默认 `rag_<server_id>` |
| `name` | 是 | 展示名 |
| `protocol` | 否 | 固定 `mcp` |
| `description` | 否 | 用途说明 |
| `capabilities` | 是 | 必须包含 `retrieve`；其余按实际能力声明 |
| `knowledge_domains` | 建议 | 知识域，用于权限与路由 |
| `citation_format` | 否 | `chunk` / `document` / `custom`，默认 `chunk` |
| `limits` | 否 | `top_k_max`（默认 20，最大 50）、`max_chars_per_chunk`（默认 2000，最大 8000）、`qps` |
| `tools` | 否 | 工具名映射；不传时用上面的默认名 |
| `timeout_ms` | 否 | 默认 30000 |
| `enabled` | 否 | 默认 `true` |

多个知识库：返回数组，每个元素一个 manifest。

### 5.3 rag_retrieve

**入参**

| 字段 | 类型 | 必填 | 默认 | 说明 |
| :-- | :-- | :--: | :-- | :-- |
| `query` | string | 是 | - | 检索问题，≤ 2000 字符 |
| `top_k` | int | 否 | 5 | 返回条数，受 `limits.top_k_max` 限制 |
| `filters` | object | 否 | `{}` | 过滤条件，如 `{"domain":"contract"}` |
| `user_scope` | object | 否 | `{}` | 权限范围：`tenant_id`、`user_id`、`roles` |
| `trace_id` | string | 否 | `""` | 链路追踪 ID |

```json
{
  "query": "合同续签流程是什么",
  "top_k": 5,
  "filters": {"domain": "contract"},
  "user_scope": {"tenant_id": "t_001", "user_id": "u_1001", "roles": ["sales"]},
  "trace_id": "trace-1"
}
```

**出参**

```json
{
  "chunks": [
    {
      "citation_id": "c1",
      "content": "合同续签需在到期前 30 天发起……",
      "score": 0.82,
      "source": {"title": "合同管理办法", "uri": "kb://contract/001", "page": 3},
      "metadata": {"domain": "contract", "updated_at": "2026-09-01T00:00:00Z"}
    }
  ],
  "provider_id": "rag_crm",
  "trace_id": "trace-1"
}
```

同样要求：返回的 JSON 必须放在 `content[].text` 里。

| 字段 | 必填 | 说明 |
| :-- | :--: | :-- |
| `chunks` | 是 | 片段数组；无结果返回 `[]` |
| `chunks[].citation_id` | 是 | 单次响应内唯一，调用方会加上 Provider 前缀 |
| `chunks[].content` | 是 | 片段正文，≤ `max_chars_per_chunk`，必须脱敏 |
| `chunks[].score` | 是 | 0–1 归一化分数 |
| `chunks[].source` | 是 | 可展示、可审计：标题、URI、页码 |
| `chunks[].metadata` | 否 | 业务附加信息 |
| `provider_id` | 否 | 当前知识库 ID |
| `trace_id` | 否 | 回传追踪 ID |

**硬性要求**

1. 必须按 `user_scope` 做数据权限过滤，不能返回越权内容。
2. 无结果返回空数组，不要返回错误。
3. 只有检索服务故障才返回错误。
4. `content` 不得包含密钥、内部路径、未脱敏个人信息。
5. 引用必须真实来自知识库，不能编造。
6. 不同知识库的 `score` 没有可比性，不需要跨库归一。

### 5.4 rag_ingest

**入参**

```json
{
  "documents": [
    {
      "document_id": "doc-001",
      "title": "合同管理办法",
      "uri": "kb://contract/001",
      "content": "全文或分片内容",
      "metadata": {"domain": "contract"}
    }
  ],
  "options": {"chunk_size": 800, "overwrite": true}
}
```

**出参**

```json
{"accepted": 1, "document_ids": ["doc-001"], "task_id": "ingest-001"}
```

要求：灌入必须异步化或返回 `task_id`；重复 `document_id` 按 `overwrite` 处理。

### 5.5 rag_list_documents

**入参**：`{"limit": 50, "cursor": ""}`

**出参**

```json
{"documents": [{"document_id": "doc-001", "title": "合同管理办法", "updated_at": "2026-09-01T00:00:00Z"}], "next_cursor": "", "has_more": false}
```

### 5.6 rag_delete_document

**入参**：`{"document_id": "doc-001"}`

**出参**：`{"ok": true, "document_id": "doc-001"}`

### 5.7 rag_knowledge_stats

**入参**：`{}`

**出参**

```json
{"document_count": 128, "chunk_count": 5400, "updated_at": "2026-11-01T00:00:00Z"}
```

### 5.8 rag_health

**入参**：`{}`

**出参**：`{"status": "ok", "latency_ms": 12}`

### 5.9 HTTP 模式等价接口

| 方法 | 路径 | 对应能力 |
| :-- | :-- | :-- |
| POST | `/retrieve` | `rag_retrieve` |
| POST | `/knowledge/ingest` | `rag_ingest` |
| GET | `/knowledge/documents?limit=&cursor=` | `rag_list_documents` |
| DELETE | `/knowledge/documents/{document_id}` | `rag_delete_document` |
| GET | `/knowledge/stats` | `rag_knowledge_stats` |
| GET | `/health` | `rag_health` |

请求体、响应体与 MCP 工具入参/出参一致。

### 5.10 RAG 错误码

| 错误码 | 场景 | 可重试 |
| :-- | :-- | :--: |
| `INVALID_ARGUMENT` | query 为空、top_k 越界 | 否 |
| `UNAUTHORIZED` | 未认证 | 否 |
| `FORBIDDEN` | 无权访问该知识域 | 否 |
| `NOT_FOUND` | 文档不存在 | 否 |
| `RAG_UNAVAILABLE` | 向量库/检索服务不可用 | 是 |
| `TIMEOUT` | 检索超时 | 是 |

---

## 6. 代码示例

以下示例都实现了同一组最小能力：

- `POST /mcp`：`initialize` / `tools/list` / `tools/call` / `ping`
- 业务工具：`query_customer`
- RAG 工具：`rag_manifest` / `rag_retrieve`
- `GET /health`

### 6.1 Python（FastAPI）

```python
import json

from fastapi import FastAPI, Request
from fastapi.responses import Response

app = FastAPI()

TOOLS = [
    {
        "name": "query_customer",
        "description": "用途：按客户 ID 查询客户信息。副作用：read。",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["customer_id"],
            "properties": {
                "customer_id": {
                    "type": "string", "minLength": 1, "maxLength": 64,
                    "description": "客户 ID",
                }
            },
        },
        "annotations": {
            "readOnlyHint": True, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": False,
        },
    },
    {
        "name": "rag_manifest",
        "description": "上报知识库信息。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "rag_retrieve",
        "description": "检索知识片段。",
        "inputSchema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string", "maxLength": 2000},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                "filters": {"type": "object"},
                "user_scope": {"type": "object"},
                "trace_id": {"type": "string"},
            },
        },
        "annotations": {"readOnlyHint": True},
    },
]

MANIFEST = {
    "provider_id": "rag_crm",
    "name": "CRM 知识库",
    "protocol": "mcp",
    "capabilities": ["retrieve", "health"],
    "knowledge_domains": ["contract"],
    "citation_format": "chunk",
    "limits": {"top_k_max": 20, "max_chars_per_chunk": 2000},
}


def ok(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def rpc_error(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def tool_result(data, is_error=False):
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": None if isinstance(data, str) else data,
        "isError": is_error,
    }


@app.post("/mcp")
async def mcp(request: Request):
    body = await request.json()
    rid = body.get("id")
    if rid is None:                      # notifications/initialized 等
        return Response(status_code=202)
    method = body.get("method")

    if method == "initialize":
        return ok(rid, {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "crm-mcp", "version": "1.0.0"},
        })
    if method == "ping":
        return ok(rid, {})
    if method == "tools/list":
        return ok(rid, {"tools": TOOLS})
    if method != "tools/call":
        return rpc_error(rid, -32601, f"unknown method: {method}")

    params = body.get("params") or {}
    name = params.get("name")
    args = params.get("arguments") or {}

    if name == "query_customer":
        customer_id = str(args.get("customer_id") or "")
        if not customer_id:
            return ok(rid, tool_result(
                {"code": "INVALID_ARGUMENT", "message": "customer_id 必填",
                 "category": "tool", "retryable": False}, True))
        return ok(rid, tool_result(
            {"customer_id": customer_id, "name": "张三", "status": "active"}))

    if name == "rag_manifest":
        return ok(rid, tool_result(MANIFEST))

    if name == "rag_retrieve":
        query = str(args.get("query") or "").strip()
        if not query:
            return ok(rid, tool_result(
                {"code": "INVALID_ARGUMENT", "message": "query 不能为空",
                 "category": "tool", "retryable": False}, True))
        top_k = min(int(args.get("top_k") or 5), 20)
        # 这里替换成你自己的向量库检索
        chunks = [
            {"citation_id": "c1", "content": f"与「{query}」相关的片段",
             "score": 0.82, "source": {"title": "合同管理办法", "uri": "kb://contract/001"}}
        ][:top_k]
        return ok(rid, tool_result({"chunks": chunks, "provider_id": "rag_crm",
                                    "trace_id": args.get("trace_id", "")}))

    return ok(rid, tool_result(
        {"code": "NOT_FOUND", "message": f"未知工具: {name}",
         "category": "tool", "retryable": False}, True))


@app.get("/health")
def health():
    return {"status": "ok"}
```

依赖：`pip install fastapi uvicorn`，启动：`uvicorn main:app --host 0.0.0.0 --port 8080`。

### 6.2 Java（Spring Boot 3）

```java
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import java.util.*;

@RestController
public class McpController {

    private static final List<Map<String, Object>> TOOLS = List.of(
        Map.of(
            "name", "query_customer",
            "description", "用途：按客户 ID 查询客户信息。副作用：read。",
            "inputSchema", Map.of(
                "type", "object",
                "additionalProperties", false,
                "required", List.of("customer_id"),
                "properties", Map.of("customer_id", Map.of(
                    "type", "string", "minLength", 1, "maxLength", 64,
                    "description", "客户 ID"))
            ),
            "annotations", Map.of(
                "readOnlyHint", true, "destructiveHint", false,
                "idempotentHint", true, "openWorldHint", false)
        ),
        Map.of(
            "name", "rag_manifest",
            "description", "上报知识库信息。",
            "inputSchema", Map.of("type", "object", "properties", Map.of())
        ),
        Map.of(
            "name", "rag_retrieve",
            "description", "检索知识片段。",
            "inputSchema", Map.of(
                "type", "object",
                "required", List.of("query"),
                "properties", Map.of(
                    "query", Map.of("type", "string", "maxLength", 2000),
                    "top_k", Map.of("type", "integer", "minimum", 1, "maximum", 20)
                )
            )
        )
    );

    private static final Map<String, Object> MANIFEST = Map.of(
        "provider_id", "rag_crm",
        "name", "CRM 知识库",
        "protocol", "mcp",
        "capabilities", List.of("retrieve", "health"),
        "knowledge_domains", List.of("contract"),
        "citation_format", "chunk",
        "limits", Map.of("top_k_max", 20, "max_chars_per_chunk", 2000)
    );

    @PostMapping("/mcp")
    public ResponseEntity<?> mcp(@RequestBody Map<String, Object> body) {
        Object id = body.get("id");
        if (id == null) {
            return ResponseEntity.accepted().build();   // notification
        }
        String method = String.valueOf(body.get("method"));

        Map<String, Object> result = switch (method) {
            case "initialize" -> Map.of(
                "protocolVersion", "2025-06-18",
                "capabilities", Map.of("tools", Map.of("listChanged", false)),
                "serverInfo", Map.of("name", "crm-mcp", "version", "1.0.0"));
            case "ping" -> Map.of();
            case "tools/list" -> Map.of("tools", TOOLS);
            case "tools/call" -> callTool(body);
            default -> null;
        };
        if (result == null) {
            return ResponseEntity.ok(Map.of(
                "jsonrpc", "2.0", "id", id,
                "error", Map.of("code", -32601, "message", "unknown method: " + method)));
        }
        return ResponseEntity.ok(Map.of("jsonrpc", "2.0", "id", id, "result", result));
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> callTool(Map<String, Object> body) {
        Map<String, Object> params = (Map<String, Object>) body.getOrDefault("params", Map.of());
        String name = String.valueOf(params.get("name"));
        Map<String, Object> args = (params.get("arguments") instanceof Map<?, ?> m)
            ? (Map<String, Object>) m : new HashMap<>();

        return switch (name) {
            case "query_customer" -> queryCustomer(args);
            case "rag_manifest" -> toolResult(MANIFEST, false);
            case "rag_retrieve" -> ragRetrieve(args);
            default -> toolResult(Map.of(
                "code", "NOT_FOUND", "message", "未知工具: " + name,
                "category", "tool", "retryable", false), true);
        };
    }

    private Map<String, Object> queryCustomer(Map<String, Object> args) {
        String customerId = String.valueOf(args.getOrDefault("customer_id", ""));
        if (customerId.isBlank() || "null".equals(customerId)) {
            return toolResult(Map.of(
                "code", "INVALID_ARGUMENT", "message", "customer_id 必填",
                "category", "tool", "retryable", false), true);
        }
        return toolResult(Map.of(
            "customer_id", customerId, "name", "张三", "status", "active"), false);
    }

    private Map<String, Object> ragRetrieve(Map<String, Object> args) {
        String query = String.valueOf(args.getOrDefault("query", "")).trim();
        if (query.isBlank()) {
            return toolResult(Map.of(
                "code", "INVALID_ARGUMENT", "message", "query 不能为空",
                "category", "tool", "retryable", false), true);
        }
        // 这里替换成你自己的向量库检索
        Map<String, Object> chunk = Map.of(
            "citation_id", "c1",
            "content", "与「" + query + "」相关的片段",
            "score", 0.82,
            "source", Map.of("title", "合同管理办法", "uri", "kb://contract/001"));
        return toolResult(Map.of(
            "chunks", List.of(chunk),
            "provider_id", "rag_crm"), false);
    }

    private Map<String, Object> toolResult(Object data, boolean isError) {
        String text;
        try {
            text = new com.fasterxml.jackson.databind.ObjectMapper()
                .writeValueAsString(data);
        } catch (Exception e) {
            text = String.valueOf(data);
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("content", List.of(Map.of("type", "text", "text", text)));
        out.put("structuredContent", data);
        out.put("isError", isError);
        return out;
    }

    @GetMapping("/health")
    public Map<String, Object> health() {
        return Map.of("status", "ok");
    }
}
```

依赖：`spring-boot-starter-web`。默认端口 8080。

### 6.3 C#（ASP.NET Core Minimal API，.NET 8）

```csharp
using System.Text.Json.Nodes;

var builder = WebApplication.CreateBuilder(args);
var app = builder.Build();

var tools = new JsonArray
{
    new JsonObject
    {
        ["name"] = "query_customer",
        ["description"] = "用途：按客户 ID 查询客户信息。副作用：read。",
        ["inputSchema"] = new JsonObject
        {
            ["type"] = "object",
            ["additionalProperties"] = false,
            ["required"] = new JsonArray("customer_id"),
            ["properties"] = new JsonObject
            {
                ["customer_id"] = new JsonObject
                {
                    ["type"] = "string",
                    ["minLength"] = 1,
                    ["maxLength"] = 64,
                    ["description"] = "客户 ID"
                }
            }
        },
        ["annotations"] = new JsonObject
        {
            ["readOnlyHint"] = true,
            ["destructiveHint"] = false,
            ["idempotentHint"] = true,
            ["openWorldHint"] = false
        }
    },
    new JsonObject
    {
        ["name"] = "rag_manifest",
        ["description"] = "上报知识库信息。",
        ["inputSchema"] = new JsonObject { ["type"] = "object", ["properties"] = new JsonObject() }
    },
    new JsonObject
    {
        ["name"] = "rag_retrieve",
        ["description"] = "检索知识片段。",
        ["inputSchema"] = new JsonObject
        {
            ["type"] = "object",
            ["required"] = new JsonArray("query"),
            ["properties"] = new JsonObject
            {
                ["query"] = new JsonObject { ["type"] = "string", ["maxLength"] = 2000 },
                ["top_k"] = new JsonObject { ["type"] = "integer", ["minimum"] = 1, ["maximum"] = 20 }
            }
        }
    }
};

var manifest = new Dictionary<string, object?>
{
    ["provider_id"] = "rag_crm",
    ["name"] = "CRM 知识库",
    ["protocol"] = "mcp",
    ["capabilities"] = new[] { "retrieve", "health" },
    ["knowledge_domains"] = new[] { "contract" },
    ["citation_format"] = "chunk",
    ["limits"] = new Dictionary<string, object?>
    {
        ["top_k_max"] = 20,
        ["max_chars_per_chunk"] = 2000
    }
};

app.MapPost("/mcp", (JsonNode body) =>
{
    var id = body["id"];
    if (id is null) return Results.Accepted();      // notification

    var method = body["method"]?.GetValue<string>();
    if (method == "initialize")
    {
        return Results.Json(RpcOk(id, new
        {
            protocolVersion = "2025-06-18",
            capabilities = new { tools = new { listChanged = false } },
            serverInfo = new { name = "crm-mcp", version = "1.0.0" }
        }));
    }
    if (method == "ping") return Results.Json(RpcOk(id, new { }));
    if (method == "tools/list") return Results.Json(RpcOk(id, new { tools }));

    if (method == "tools/call")
    {
        var name = body["params"]?["name"]?.GetValue<string>();
        var args = body["params"]?["arguments"] as JsonObject ?? new JsonObject();

        switch (name)
        {
            case "query_customer":
            {
                var customerId = args["customer_id"]?.GetValue<string>() ?? "";
                if (string.IsNullOrWhiteSpace(customerId))
                {
                    return Results.Json(RpcOk(id, ToolResult(new Dictionary<string, object?>
                    {
                        ["code"] = "INVALID_ARGUMENT",
                        ["message"] = "customer_id 必填",
                        ["category"] = "tool",
                        ["retryable"] = false
                    }, true)));
                }
                return Results.Json(RpcOk(id, ToolResult(new
                {
                    customer_id = customerId,
                    name = "张三",
                    status = "active"
                }, false)));
            }
            case "rag_manifest":
                return Results.Json(RpcOk(id, ToolResult(manifest, false)));

            case "rag_retrieve":
            {
                var query = args["query"]?.GetValue<string>() ?? "";
                if (string.IsNullOrWhiteSpace(query))
                {
                    return Results.Json(RpcOk(id, ToolResult(new Dictionary<string, object?>
                    {
                        ["code"] = "INVALID_ARGUMENT",
                        ["message"] = "query 不能为空",
                        ["category"] = "tool",
                        ["retryable"] = false
                    }, true)));
                }
                // 这里替换成你自己的向量库检索
                var result = new
                {
                    chunks = new[]
                    {
                        new
                        {
                            citation_id = "c1",
                            content = $"与「{query}」相关的片段",
                            score = 0.82,
                            source = new { title = "合同管理办法", uri = "kb://contract/001" }
                        }
                    },
                    provider_id = "rag_crm"
                };
                return Results.Json(RpcOk(id, ToolResult(result, false)));
            }
            default:
                return Results.Json(RpcOk(id, ToolResult(new Dictionary<string, object?>
                {
                    ["code"] = "NOT_FOUND",
                    ["message"] = $"未知工具: {name}",
                    ["category"] = "tool",
                    ["retryable"] = false
                }, true)));
        }
    }

    return Results.Json(new
    {
        jsonrpc = "2.0",
        id,
        error = new { code = -32601, message = $"unknown method: {method}" }
    });
});

app.MapGet("/health", () => Results.Json(new { status = "ok" }));

app.Run();

static object RpcOk(JsonNode? id, object result) => new { jsonrpc = "2.0", id, result };

static object ToolResult(object data, bool isError) => new
{
    content = new[] { new { type = "text", text = System.Text.Json.JsonSerializer.Serialize(data) } },
    structuredContent = data,
    isError
};
```

依赖：.NET 8 SDK。运行：`dotnet run`。

### 6.4 Go（标准库 net/http）

```go
package main

import (
	"encoding/json"
	"io"
	"log"
	"net/http"
)

type RPCRequest struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      json.RawMessage `json:"id"`
	Method  string          `json:"method"`
	Params  struct {
		Name      string                 `json:"name"`
		Arguments map[string]interface{} `json:"arguments"`
	} `json:"params"`
}

var tools = []map[string]interface{}{
	{
		"name":        "query_customer",
		"description": "用途：按客户 ID 查询客户信息。副作用：read。",
		"inputSchema": map[string]interface{}{
			"type":                 "object",
			"additionalProperties": false,
			"required":             []string{"customer_id"},
			"properties": map[string]interface{}{
				"customer_id": map[string]interface{}{
					"type": "string", "minLength": 1, "maxLength": 64,
					"description": "客户 ID",
				},
			},
		},
		"annotations": map[string]interface{}{
			"readOnlyHint": true, "destructiveHint": false,
			"idempotentHint": true, "openWorldHint": false,
		},
	},
	{
		"name":        "rag_manifest",
		"description": "上报知识库信息。",
		"inputSchema": map[string]interface{}{"type": "object", "properties": map[string]interface{}{}},
	},
	{
		"name":        "rag_retrieve",
		"description": "检索知识片段。",
		"inputSchema": map[string]interface{}{
			"type":     "object",
			"required": []string{"query"},
			"properties": map[string]interface{}{
				"query": map[string]interface{}{"type": "string", "maxLength": 2000},
				"top_k": map[string]interface{}{"type": "integer", "minimum": 1, "maximum": 20},
			},
		},
	},
}

var manifest = map[string]interface{}{
	"provider_id":        "rag_crm",
	"name":               "CRM 知识库",
	"protocol":           "mcp",
	"capabilities":       []string{"retrieve", "health"},
	"knowledge_domains":  []string{"contract"},
	"citation_format":    "chunk",
	"limits":             map[string]interface{}{"top_k_max": 20, "max_chars_per_chunk": 2000},
}

func ok(id json.RawMessage, result interface{}) map[string]interface{} {
	return map[string]interface{}{"jsonrpc": "2.0", "id": id, "result": result}
}

func toolResult(data interface{}, isError bool) map[string]interface{} {
	text, _ := json.Marshal(data)
	return map[string]interface{}{
		"content":           []map[string]interface{}{{"type": "text", "text": string(text)}},
		"structuredContent": data,
		"isError":           isError,
	}
}

func mcpHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	var req RPCRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		w.WriteHeader(http.StatusBadRequest)
		return
	}
	if len(req.ID) == 0 { // notification
		w.WriteHeader(http.StatusAccepted)
		return
	}

	var result interface{}
	switch req.Method {
	case "initialize":
		result = map[string]interface{}{
			"protocolVersion": "2025-06-18",
			"capabilities":    map[string]interface{}{"tools": map[string]interface{}{"listChanged": false}},
			"serverInfo":      map[string]interface{}{"name": "crm-mcp", "version": "1.0.0"},
		}
	case "ping":
		result = map[string]interface{}{}
	case "tools/list":
		result = map[string]interface{}{"tools": tools}
	case "tools/call":
		name := req.Params.Name
		args := req.Params.Arguments
		switch name {
		case "query_customer":
			customerID, _ := args["customer_id"].(string)
			if customerID == "" {
				result = toolResult(map[string]interface{}{
					"code": "INVALID_ARGUMENT", "message": "customer_id 必填",
					"category": "tool", "retryable": false,
				}, true)
			} else {
				result = toolResult(map[string]interface{}{
					"customer_id": customerID, "name": "张三", "status": "active",
				}, false)
			}
		case "rag_manifest":
			result = toolResult(manifest, false)
		case "rag_retrieve":
			query, _ := args["query"].(string)
			if query == "" {
				result = toolResult(map[string]interface{}{
					"code": "INVALID_ARGUMENT", "message": "query 不能为空",
					"category": "tool", "retryable": false,
				}, true)
			} else {
				// 这里替换成你自己的向量库检索
				result = toolResult(map[string]interface{}{
					"chunks": []map[string]interface{}{
						{
							"citation_id": "c1",
							"content":     "与「" + query + "」相关的片段",
							"score":       0.82,
							"source":      map[string]interface{}{"title": "合同管理办法", "uri": "kb://contract/001"},
						},
					},
					"provider_id": "rag_crm",
				}, false)
			}
		default:
			result = toolResult(map[string]interface{}{
				"code": "NOT_FOUND", "message": "未知工具: " + name,
				"category": "tool", "retryable": false,
			}, true)
		}
	default:
		json.NewEncoder(w).Encode(map[string]interface{}{
			"jsonrpc": "2.0", "id": req.ID,
			"error": map[string]interface{}{"code": -32601, "message": "unknown method: " + req.Method},
		})
		return
	}
	json.NewEncoder(w).Encode(ok(req.ID, result))
}

func healthHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	io.WriteString(w, `{"status":"ok"}`)
}

func main() {
	http.HandleFunc("/mcp", mcpHandler)
	http.HandleFunc("/health", healthHandler)
	log.Println("listening on :8080")
	log.Fatal(http.ListenAndServe(":8080", nil))
}
```

运行：`go run main.go`。

---

## 7. 自测清单

**协议**

- [ ] `initialize` 返回 `protocolVersion` / `capabilities` / `serverInfo`
- [ ] `tools/list` 返回的工具与文档一致
- [ ] 未知方法返回 `-32601`
- [ ] `notifications/initialized` 返回 202
- [ ] `ping` 能快速响应

**工具**

- [ ] 每个工具有 `description`、`inputSchema`、`annotations`
- [ ] 参数在服务端二次校验
- [ ] 失败返回 `isError: true` + 统一错误对象
- [ ] 有副作用工具支持 `idempotency_key`
- [ ] 返回值不含堆栈、SQL、密钥、绝对路径
- [ ] 大对象使用引用或分页

**RAG**

- [ ] `rag_manifest` 返回的字段完整，`provider_id` 唯一
- [ ] `rag_retrieve` 按 `user_scope` 过滤权限
- [ ] 无结果返回 `chunks: []`，不是错误
- [ ] `citation_id` 单次响应内唯一
- [ ] `content` 不含未脱敏敏感信息
- [ ] `score` 在 0–1 之间
- [ ] 检索故障返回 `RAG_UNAVAILABLE`，可重试
