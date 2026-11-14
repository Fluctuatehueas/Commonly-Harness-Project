# 业务主链路 API

> 版本：v1.0 · 2026-11-11
> 读者：甲方后端 / 前端开发
> 范围：对话、会话、反馈、附件、轨迹、异步任务、会话文件。
> 这些接口可以经甲方网关对外提供；管理与配置接口见 [管理与配置API.md](./管理与配置API.md)。

---

## 目录

- [1. 公共约定](#1-公共约定)
- [2. API 一览](#2-api-一览)
- [3. 健康检查](#3-健康检查)
- [4. 对话](#4-对话)
- [5. 会话](#5-会话)
- [6. 消息反馈](#6-消息反馈)
- [7. 附件与上下文](#7-附件与上下文)
- [8. 执行轨迹](#8-执行轨迹)
- [9. 异步任务](#9-异步任务)
- [10. 会话文件](#10-会话文件)
- [11. 错误码与联调建议](#11-错误码与联调建议)

---

## 1. 公共约定

### 1.1 地址与编码

| 项 | 约定 |
| :-- | :-- |
| Base URL | `http://{harness-host}:8000`（默认只监听内网，由网关反代） |
| 请求编码 | `application/json; charset=utf-8` |
| 流式响应 | `text/event-stream` |
| 时间 | ISO 8601 UTC，如 `2026-11-11T08:00:00Z` |
| ID | 字符串 |

### 1.2 身份头

| Header | 必填 | 说明 |
| :-- | :--: | :-- |
| `X-User-Id` | 是（多用户） | 可信用户 ID，由网关注入；会覆盖 query / body 中的 `user_id` |

网关必须剥离客户端自带的 `X-User-Id` 后再注入可信值。系统本身没有登录能力，直接暴露公网等于身份可伪造。

### 1.3 错误响应

普通错误：

```json
{"detail": "会话不存在或无权访问"}
```

结构化错误：

```json
{"detail": {"code": "MCP_TOOL_BLOCKED", "message": "工具被白名单拦截", "tool": "delete_record"}}
```

客户端要同时兼容 `detail` 为字符串和对象两种情况。

### 1.4 会话互斥

同一 `thread_id` 同一时刻只允许一轮执行；冲突返回 **409**：

```json
{"detail": "会话正在处理中，请稍后重试"}
```

---

## 2. API 一览

| 方法 | 路径 | 作用 |
| :-- | :-- | :-- |
| GET | `/health` | 存活探针 |
| POST | `/chat` | 非流式单轮对话 |
| POST | `/chat/stream` | SSE 流式对话（主入口） |
| POST | `/chat/{thread_id}/resume` | 恢复人工中断 |
| POST | `/chat/{thread_id}/retry` | 重新生成最后一条回答 |
| POST | `/chat/{thread_id}/withdraw` | 撤回最后一轮 |
| POST | `/history` | 创建会话 |
| GET | `/history` | 会话列表 |
| GET | `/history/{thread_id}/messages` | 会话消息 |
| GET | `/history/{thread_id}/usage` | 会话 Token 用量 |
| PATCH | `/history/{thread_id}` | 重命名 / 置顶 / 归档 |
| DELETE | `/history/{thread_id}` | 删除会话 |
| POST | `/history/auto-archive` | 按天数自动归档 |
| POST | `/feedback` | 消息点赞 / 拉踩 |
| GET | `/context/formats` | 支持的上传格式 |
| POST | `/context/upload` | 上传附件 |
| POST | `/context/url` | 从 URL 导入附件 |
| POST | `/context/resolve` | 批量校验附件 |
| DELETE | `/context/{file_id}` | 删除附件 |
| GET | `/trajectory/{thread_id}` | 执行轨迹 |
| GET | `/async-tasks/{task_id}` | 异步任务状态 |
| GET | `/threads/{thread_id}/files` | 会话文件列表 |
| GET | `/threads/{thread_id}/files/content` | 文件预览 |
| GET | `/threads/{thread_id}/files/download` | 文件下载 |

---

## 3. 健康检查

**GET** `/health`

```json
{
  "status": "ok",
  "integrity": "full",
  "low_integrity": false,
  "integrity_hint": ""
}
```

| 字段 | 说明 |
| :-- | :-- |
| `status` | 固定 `ok` |
| `integrity` | 进程完整性级别 |
| `low_integrity` | 是否处于低完整性（写入能力受限） |
| `integrity_hint` | 修复提示 |

---

## 4. 对话

### 4.1 非流式对话

**POST** `/chat`

**请求体**

| 字段 | 类型 | 必填 | 默认 | 说明 |
| :-- | :-- | :--: | :-- | :-- |
| `message` | string | 是 | - | 用户消息 |
| `thread_id` | string | 否 | 自动生成 | 会话 ID；传了必须存在且属于当前用户 |
| `user_id` | string | 否 | `anonymous` | 用户 ID（请求头优先） |
| `username` | string | 否 | 同 `user_id` | 用户名 |
| `name` | string | 否 | - | `username` 的兼容字段 |
| `tenant_id` | string | 否 | `""` | 部署级租户 |
| `attachments` | string[] | 否 | `[]` | 附件 ID 列表，先调 `/context/upload` |
| `flow_id` | string | 否 | - | 显式指定任务流 |
| `entry_step` | string | 否 | - | 任务流中途接管入口 |
| `user_state` | string | 否 | `""` | 中途接管时用户已完成部分的描述 |
| `agent_id` | string | 否 | 会话绑定值 | MyAgent 档案 ID |
| `output_schema` | object | 否 | - | 最终回答必须满足的 JSON Schema（含 `required`）；提供后启用确定性校验 |
| `numeric_ranges` | object | 否 | - | 数值断言，如 `{"total":{"value":120,"min":0,"max":200}}` |

**响应**

```json
{
  "thread_id": "th-001",
  "user_id": "u_1001",
  "username": "张三",
  "answer": "本轮完整回答",
  "messages": [],
  "interrupt": null
}
```

| 字段 | 说明 |
| :-- | :-- |
| `thread_id` | 会话 ID |
| `answer` | 本轮回答文本 |
| `messages` | 保留字段，通常为空 |
| `interrupt` | 等待恢复的中断，结构见 4.6 |

**错误**

| HTTP | 场景 |
| :--: | :-- |
| 404 | `thread_id` 不存在或无权访问 |
| 409 | 会话正在处理中 |
| 500 / 502 / 503 | 模型或运行时异常 |

> 服务端在返回最终回答前会做 PDCA 自检：规则检查 → L1 模型自检 → L2 确定性验证（`output_schema` / `numeric_ranges`）→ 计划完成度。未通过时最多带反馈重做 2 轮（`HARNESS_PDCA_MAX_ROUNDS`），会增加响应时间与 Token 消耗。

### 4.2 流式对话

**POST** `/chat/stream`

请求体同 4.1。响应 `text/event-stream`。

**SSE 帧格式**

```text
data: {"type":"token","message_id":"assistant-1","content":"你好","source":"main"}

```

**事件表**

| `type` | 关键字段 | 说明 |
| :-- | :-- | :-- |
| `reasoning` | `message_id`、`content`、`source` | 思考过程增量 |
| `token` | `message_id`、`content`、`source` | 正文增量 |
| `subagent_token` | `message_id`、`content`、`source`、`parent_tool_call_id` | 子 Agent 过程输出 |
| `tool_start` | `tool_call_id`、`tool_name`、`source`、`subagent_name?` | 工具开始 |
| `tool_args` | `tool_call_id`、`args`、`source` | 工具参数（字符串，可能多次） |
| `tool_result` | `tool_call_id`、`tool_name`、`text`、`status`、`tool_status` | 工具结果；`status` 为 `done` / `error` |
| `tool_end` | `tool_call_id`、`tool_name`、`status` | 工具结束 |
| `interrupt` | `thread_id`、`interrupt_id`、`interrupt_type`、载荷 | 人工介入 |
| `context_marker` | `marker_type`、`thread_id`、`items` | 上下文压缩 / 经验注入标记 |
| `retry_start` | `thread_id` | 重新生成开始 |
| `pdc_revision` | `round`、`reason` | 自检未通过，开始第 N 轮修正；前端可丢弃上一版草稿 |
| `task_flow_start` | `flow_id`、`flow_name`、`steps` | 任务流开始 |
| `task_flow_progress` | `flow_id`、`step_id`、`status`、`output` | 任务流步骤进度 |
| `task_flow_end` | `flow_id`、`status` | 任务流结束 |
| `done` | 见下 | 本轮结束 |
| `error` | `message`、`status_code?` | 流内错误 |

**`done` 事件**

```json
{
  "type": "done",
  "thread_id": "th-001",
  "user_id": "u_1001",
  "username": "张三",
  "content": "本轮完整回答",
  "interrupted": false,
  "usage": {"input": 1200, "output": 300, "total": 1500},
  "session_usage": {"input": 1200, "output": 300, "total": 1500, "spent": 5400, "rounds": 3, "context_window": 128000, "context_used": 1200},
  "final_message_id": "assistant-xxx",
  "round_limit": 50,
  "duration_ms": 4210
}
```

客户端规则：

1. 收到 `done` 后关闭连接；`interrupted: true` 不是失败。
2. `error` 事件也可能出现在 HTTP 200 的流里，必须按事件处理。
3. `tool_result.status == "error"` 时，`text` 是 JSON 字符串形式的错误对象。
4. `usage` 是本轮消耗，`session_usage` 是会话累计。

### 4.3 恢复人工中断

**POST** `/chat/{thread_id}/resume`

**请求体**

| 字段 | 类型 | 必填 | 说明 |
| :-- | :-- | :--: | :-- |
| `resume` | object | 是 | 补充信息或审批决策 |
| `user_id` / `username` / `tenant_id` | string | 否 | 身份 |

响应同样是 SSE 流。中断类型见 4.6。

### 4.4 重新生成最后一条回答

**POST** `/chat/{thread_id}/retry`

**请求体**

| 字段 | 类型 | 必填 | 说明 |
| :-- | :-- | :--: | :-- |
| `message_id` | string | 否 | 前端展示的被替换消息 ID，用于一致性校验 |
| `user_id` / `username` / `tenant_id` | string | 否 | 身份 |

响应为 SSE 流。只允许重试最后一条回答；包含副作用工具或处于人工确认时返回 409。

**错误**

| HTTP | 场景 |
| :--: | :-- |
| 403 | 重新回答功能已关闭 |
| 404 | 会话不存在 |
| 409 | 当前消息不允许重试 |

### 4.5 撤回最后一轮

**POST** `/chat/{thread_id}/withdraw`

**请求体**

| 字段 | 类型 | 必填 |
| :-- | :-- | :--: |
| `user_id` / `username` / `tenant_id` | string | 否 |

**响应**

```json
{
  "thread_id": "th-001",
  "content": "被撤回的用户原文",
  "session_usage": {"input": 0, "output": 300, "total": 300, "spent": 5400, "rounds": 2}
}
```

语义：

- 撤回最新一条用户消息及其之后的回答与轨迹。
- Token 计费与磁盘文件不回滚。
- 首条消息撤回时重置会话，标题回到「新会话」。

### 4.6 人工中断载荷

| `interrupt_type` | 载荷字段 | 恢复方式 |
| :-- | :-- | :-- |
| `hitl_approval` | `action_requests[]`、`review_configs?` | `resume.decisions[]` |
| `hitl_request` | `hitl`、`resume_schema` | 按 `resume_schema` 提交表单 |
| `host_approval` | `host_approval` | `resume.decision` |
| `information_request` | `information_needed`、`context` | `resume.text` |
| `unknown` | `interrupt_value`（纯文本） | `resume.text` |

**审批型中断**

```json
{
  "type": "interrupt",
  "thread_id": "th-001",
  "interrupt_id": "int-001",
  "interrupt_type": "hitl_approval",
  "action_requests": [
    {"name": "delete_record", "args": {"id": "A1001"}, "description": "删除记录"}
  ]
}
```

**恢复审批**

```json
{
  "resume": {
    "decisions": [
      {"type": "approve"},
      {"type": "edit", "edited_action": {"name": "delete_record", "args": {"id": "A1001"}}},
      {"type": "reject", "message": "不允许删除"}
    ]
  }
}
```

`decisions` 顺序与数量必须与 `action_requests` 一致。

**补充信息**

```json
{"resume": {"values": {"order_id": "A1001"}, "free_text": "补充说明"}}
```

**宿主能力审批**

```json
{"resume": {"decision": "approve"}}
```

`decision` 可选：

| 值 | 含义 |
| :-- | :-- |
| `approve` | 允许本次 |
| `approve_project` | 本项目内允许（仅本机编译命令） |
| `approve_thread` | 仅本会话允许 |
| `reject` | 拒绝，可带 `message` |

`compile_project` 在本机执行编译前一定会触发 `host_approval`，载荷中带 `options`（允许本次 / 本项目内允许 / 仅本会话允许 / 拒绝）。`host_execute` 的项目外命令只提供「允许本次 / 仅本会话允许 / 拒绝」。

---

## 5. 会话

### 5.1 创建会话

**POST** `/history`

| 参数 | 位置 | 类型 | 必填 | 默认 | 说明 |
| :-- | :-- | :-- | :--: | :-- | :-- |
| `user_id` | query | string | 否 | `anonymous` | 用户 ID |
| `project_id` | query | string | 否 | - | 绑定项目工作区 |
| `integration_server_id` | query | string | 否 | - | 绑定集成 MCP 服务 |
| `agent_id` | query | string | 否 | `default` | MyAgent 档案 |

`project_id` 与 `integration_server_id` 互斥。

**响应（Session）**

```json
{
  "thread_id": "th-001",
  "title": "新会话",
  "created_at": "2026-11-11T08:00:00Z",
  "updated_at": "2026-11-11T08:00:00Z",
  "message_count": 0,
  "pinned": false,
  "project_id": null,
  "integration_server_id": "crm",
  "agent_id": "default",
  "archived": false,
  "archived_at": null
}
```

**错误**

| HTTP | 场景 |
| :--: | :-- |
| 404 | 项目或集成服务不存在 |
| 422 | 两者同时传；集成服务已停用 |
| 409 | 集成服务未连接 |

### 5.2 会话列表

**GET** `/history`

| 参数 | 类型 | 必填 | 默认 | 说明 |
| :-- | :-- | :--: | :-- | :-- |
| `user_id` | string | 否 | `anonymous` | 用户 ID |
| `archived` | bool | 否 | `false` | `true` 返回已归档会话 |

```json
{"sessions": [{"thread_id": "th-001", "title": "合同咨询", "message_count": 6, "pinned": true, "archived": false}]}
```

### 5.3 会话消息

**GET** `/history/{thread_id}/messages`

| 参数 | 类型 | 必填 | 默认 |
| :-- | :-- | :--: | :-- |
| `user_id` | string | 否 | `anonymous` |

**响应**

```json
{
  "thread_id": "th-001",
  "messages": [
    {
      "id": "m1",
      "role": "user",
      "content": "客户 C1001 什么状态",
      "created_at": "2026-11-11T08:00:00Z",
      "source": null
    },
    {
      "id": "call_1",
      "role": "tool",
      "content": "",
      "tool_call_id": "call_1",
      "tool_name": "crm__query_customer",
      "tool_status": "done",
      "args": "{\"customer_id\": \"C1001\"}",
      "text": "客户 C1001：张三，状态 active",
      "source": "main",
      "created_at": "2026-11-11T08:00:00Z"
    },
    {
      "id": "m2",
      "role": "assistant",
      "content": "客户 C1001 当前状态为 active。",
      "tokens": {"input": 1200, "output": 300, "total": 1500},
      "feedback": "like",
      "created_at": "2026-11-11T08:00:01Z"
    }
  ],
  "interrupt": null,
  "session_usage": {"input": 1200, "output": 300, "total": 1500, "spent": 5400, "rounds": 3, "context_window": 128000, "context_used": 1200}
}
```

**Message 字段**

| 字段 | 类型 | 说明 |
| :-- | :-- | :-- |
| `id` | string | 消息 ID |
| `role` | string | `user` / `assistant` / `reasoning` / `tool` / `delegation` |
| `content` | string | 文本内容 |
| `created_at` | string | 创建时间 |
| `source` | string\|null | `main` 或子 Agent 名称 |
| `tool_call_id` | string\|null | 工具调用 ID |
| `tool_name` | string\|null | 工具名 |
| `tool_status` | string\|null | `calling` / `done` / `error` |
| `args` | string\|null | 工具参数（JSON 字符串） |
| `text` | string\|null | 工具结果文本 |
| `async_task_id` | string\|null | 可继续轮询的异步任务 ID |
| `tokens` | object\|null | 单轮 Token：`input` / `output` / `total` |
| `feedback` | string\|null | `like` / `dislike` / `null` |

### 5.4 会话用量

**GET** `/history/{thread_id}/usage?user_id=`

```json
{"input": 1200, "output": 300, "total": 1500, "spent": 5400, "rounds": 3, "context_window": 128000, "context_used": 1200}
```

| 字段 | 说明 |
| :-- | :-- |
| `input` | 最近一轮输入 Token（当前上下文占用） |
| `output` | 历轮输出 Token 之和 |
| `total` | `input + output` |
| `spent` | 真实计费口径：历轮 input + output |
| `rounds` | 轮数 |
| `context_window` | 模型上下文窗口 |
| `context_used` | 当前上下文占用 |

已归档会话允许查询。

### 5.5 修改会话

**PATCH** `/history/{thread_id}?user_id=`

| 字段 | 类型 | 必填 | 说明 |
| :-- | :-- | :--: | :-- |
| `title` | string | 否 | 1–120 字符 |
| `pinned` | bool | 否 | 置顶 |
| `archived` | bool | 否 | `true` 归档，`false` 恢复 |

`project_id` 在创建时固定，传入会返回 422。已归档会话在未显式传 `archived` 时返回 409。

响应为 Session 对象。

### 5.6 删除会话

**DELETE** `/history/{thread_id}?user_id=`

```json
{"success": true}
```

### 5.7 自动归档

**POST** `/history/auto-archive?user_id=`

```json
{"archived": 2, "days": 30}
```

按用户配置的天数归档长期无新记录的会话；`days = 0` 表示关闭。

---

## 6. 消息反馈

**POST** `/feedback`

| 字段 | 类型 | 必填 | 默认 | 说明 |
| :-- | :-- | :--: | :-- | :-- |
| `thread_id` | string | 是 | - | 会话 ID |
| `message_id` | string | 是 | - | 稳定的助手消息 ID |
| `action` | string | 是 | - | `like` / `dislike` / `none` |
| `user_id` | string | 否 | `anonymous` | 用户 ID |
| `user_excerpt` | string | 否 | `""` | 用户消息摘要（≤400） |
| `answer_excerpt` | string | 否 | `""` | 回答摘要（≤800） |
| `meta` | object | 否 | `null` | 附加信息 |

**响应**

```json
{"message_id": "assistant-1", "action": "like"}
```

**错误**

| HTTP | 场景 |
| :--: | :-- |
| 403 | 反馈功能关闭 |
| 404 | 会话不存在或已归档 |
| 429 | 今日反馈次数达上限 |

---

## 7. 附件与上下文

### 7.1 支持格式

**GET** `/context/formats`

```json
{
  "accept": ".csv,.docx,.gif,.htm,.html,.jpeg,.jpg,.json,.log,.md,.pdf,.png,.py,.sql,.txt,.webp,.xml,.yaml,.yml",
  "max_files": 6,
  "max_file_mb": 20,
  "groups": {
    "image": [".png", ".jpg", ".jpeg", ".webp", ".gif"],
    "document": [".docx", ".pdf"],
    "text": [".txt", ".md", ".csv", ".json", ".yaml", ".xml", ".html", ".py", ".js", ".ts", ".java", ".sql"]
  }
}
```

`groups` 为节选，实际以 `accept` 与 `groups` 的完整返回为准。

### 7.2 上传附件

**POST** `/context/upload`

`multipart/form-data`：

| 字段 | 位置 | 类型 | 必填 | 说明 |
| :-- | :-- | :-- | :--: | :-- |
| `file` | form | file | 是 | 文件内容 |
| `user_id` | query | string | 是 | 用户 ID |

**响应**

```json
{"id": "3f2a9c0d8e7b4a1c9d6e5f4a3b2c1d0e", "name": "合同.pdf", "ext": ".pdf", "kind": "document", "mime": "application/pdf", "size": 1048576}
```

| 字段 | 说明 |
| :-- | :-- |
| `id` | 附件 ID，32 位十六进制 |
| `kind` | `image` / `document` / `text` |

**错误**

| HTTP | 场景 |
| :--: | :-- |
| 413 | 超过 20 MB |
| 415 | 不支持的扩展名 |

### 7.3 URL 导入

**POST** `/context/url`

```json
{"url": "https://example.com/report.pdf", "user_id": "u_1001"}
```

响应同 7.2。只允许公网 `http(s)`，每一跳重定向都会校验，防 SSRF。

### 7.4 批量校验附件

**POST** `/context/resolve`

```json
{"user_id": "u_1001", "ids": ["3f2a...", "bad-id"]}
```

```json
{
  "files": [{"id": "3f2a...", "name": "合同.pdf", "ext": ".pdf", "kind": "document", "mime": "application/pdf", "size": 1048576}],
  "missing": ["bad-id"]
}
```

### 7.5 删除附件

**DELETE** `/context/{file_id}?user_id=`

```json
{"ok": true}
```

非法 ID 返回 400。

---

## 8. 执行轨迹

**GET** `/trajectory/{thread_id}?user_id=`

```json
{
  "thread_id": "th-001",
  "latest_run_id": "run-2",
  "event_count": 42,
  "generated_at": "2026-11-11T08:10:00Z",
  "runs": [
    {
      "run_id": "run-2",
      "started_at": "2026-11-11T08:00:00Z",
      "ended_at": "2026-11-11T08:00:04Z",
      "status": "done",
      "events": [],
      "summary": {
        "model_calls": 3,
        "tool_calls": 2,
        "skill_calls": 0,
        "delegations": 0,
        "hitl": 0,
        "compact_count": 1,
        "tokens": {"input": 1200, "output": 300, "total": 1500},
        "peak_context_tokens": 9800,
        "min_context_tokens": 1200,
        "tokens_saved": 4200,
        "duration_ms": 4000,
        "event_count": 42
      }
    }
  ]
}
```

| 字段 | 说明 |
| :-- | :-- |
| `runs[].status` | `done` / `running` / `interrupted` |
| `events[]` | 中间件写入的结构化事件，按时间排序 |
| `summary.duration_ms` | 本轮耗时 |
| `summary.tokens_saved` | 上下文压缩节省的 Token |

事件类型包括 `run`、`model`、`tool`、`skill`、`delegation`、`hitl`、`compaction`。可直接用于审计视图。

---

## 9. 异步任务

**GET** `/async-tasks/{task_id}?user_id=`

| 参数 | 类型 | 必填 | 说明 |
| :-- | :-- | :--: | :-- |
| `user_id` | string | 是 | 必须是任务归属用户 |

```json
{
  "task_id": "0f8c...",
  "status": "running",
  "done": false,
  "delivered": false,
  "result": null,
  "error": null,
  "run_id": "run-1",
  "updated_at": "2026-11-11T08:00:03Z"
}
```

| `status` | 说明 |
| :-- | :-- |
| `pending` / `running` | 执行中 |
| `success` | 完成 |
| `error` / `cancelled` / `timeout` / `interrupted` | 终态 |

规则：

1. 以 `done = true` 停止轮询。
2. 终态结果幂等写回主会话；`delivered` 表示是否已投递。
3. 主会话处于人工中断时暂不投递。
4. 不存在或不属于当前用户返回 404；查询上游失败返回 502。

---

## 10. 会话文件

### 10.1 文件列表

**GET** `/threads/{thread_id}/files?user_id=`

```json
{
  "source": "project+sandbox",
  "workspace": "F:\\data\\projects\\p1",
  "sandbox_root": "/workspace/p1",
  "project_id": "p1",
  "files": [
    {"path": "/report.md", "size": 2048, "modified_at": "2026-11-11T08:00:00Z", "origin": "sandbox"},
    {"path": "docs/readme.md", "size": 512, "modified_at": "2026-11-10T08:00:00Z", "origin": "project"}
  ]
}
```

| 字段 | 说明 |
| :-- | :-- |
| `source` | `sandbox` 或 `project+sandbox` |
| `origin` | `project`（本机项目目录）或 `sandbox`（执行环境） |

自由会话只有沙箱文件；沙箱不可用返回 503。

### 10.2 文件预览

**GET** `/threads/{thread_id}/files/content`

| 参数 | 类型 | 必填 | 默认 | 说明 |
| :-- | :-- | :--: | :-- | :-- |
| `user_id` | string | 是 | - | 用户 ID |
| `path` | string | 是 | - | 文件相对路径 |
| `source` | string | 否 | `sandbox` | `project` / `sandbox` |

```json
{"path": "/report.md", "content": "# 标题\n...", "encoding": "utf-8", "source": "sandbox"}
```

**错误**：404 文件不存在；413 超过 10 MB；415 二进制文件。

### 10.3 文件下载

**GET** `/threads/{thread_id}/files/download`

参数同 10.2，另有 `inline`（bool，默认 `false`）。

- 图片且 `inline=true` 时按 `media_type` 内联返回；
- 其他文件强制 `application/octet-stream` + `Content-Disposition: attachment`。

---

## 11. 错误码与联调建议

| HTTP | 场景 |
| :--: | :-- |
| 400 | 参数非法（如附件 ID 格式错误） |
| 403 | 功能关闭 |
| 404 | 会话 / 任务 / 文件不存在或无权访问 |
| 409 | 会话正在处理；已归档限制 |
| 413 | 文件超限 |
| 415 | 文件类型不支持或无法预览 |
| 422 | 字段校验失败 |
| 429 | 反馈次数超限 |
| 500 | 运行时异常 |
| 502 | Agent 未返回有效回答；上游任务查询失败 |
| 503 | 模型 / 沙箱不可用 |

联调建议：

1. 先 `GET /health`，再 `POST /history` 建会话。
2. 用 `POST /chat/stream` 跑通 `token → done`。
3. 再验证工具事件、人工中断、`resume`。
4. 最后接附件、轨迹、文件抽屉与异步任务轮询。
5. 所有请求都带 `X-User-Id`，并用两个不同用户验证隔离。
