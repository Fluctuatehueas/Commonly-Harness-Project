# Tools and MCP

Tools are the only way the model affects the world. This page covers the built-in tool surface, the MCP client runtime, and the rules for adding new tools.

## 1. Tool groups

The main agent receives a deliberately small general-purpose tool set. Subagents can receive narrower sets declared in YAML.

| Category | Tools | Notes |
| :-- | :-- | :-- |
| Basic | `get_current_time`, calculation, JSON helpers | No side effects |
| HITL | `request_additional_info`, `request_user_choice` | Interrupt and wait for human input |
| Documents | `read_word_document` | Word extraction |
| Planning | `create_work_plan` | Produces waves and conflicts for parallel delegation |
| Workspace | `run_in_sandbox`, `publish_to_project` | Execution isolation and artifact publishing |
| Build | `compile_project` | Local runtime if available, otherwise sandbox |
| Skills | `create_skill_management_tools(...)` | Uses the stable sandbox proxy |
| RAG | `create_rag_tools()` | Empty when no provider is configured |
| Async | `create_async_sandbox_tools(...)` | Submits Agent Protocol runs |
| Scheduler | `create_scheduler_tools(...)` | Only when `HARNESS_SCHEDULER_EXEC=true` |
| Task flow | `create_task_flow_tools()` | Step reporting |
| Host | `host_execute`, `host_read_file`, `host_write_file` | Off by default; always HITL |
| MCP | Discovered from configured servers | See below |

Every tool has a unique name at the main-agent level. Duplicate names abort graph construction.

## 2. MCP runtime

Code:

| File | Responsibility |
| :-- | :-- |
| `src/agent/mcp/runtime.py` | Runtime lifecycle and tool exposure |
| `src/agent/mcp/client_registry.py` | Client instances and connection management |
| `src/agent/mcp/config_store.py` | Persistent server configuration |
| `src/agent/mcp/router.py` | Routing and scoping |
| `src/agent/mcp/registry.py` | Server and tool registry |
| `src/agent/mcp/observer.py` | Health and observation hooks |
| `src/agent/mcp/types.py` | Shared types |

Key behaviors:

- **Lazy discovery.** No connection is opened at process startup. Tools are discovered when the graph is built for a user, and a failing server does not prevent the graph from being built.
- **Per-user configuration.** Server definitions live in `<USER_DATA_DIR>/users/<user_id>/mcp/servers.json`. The repository-level `mcp/servers.json` is the development default.
- **Per-tool scoping.** `tools_for_main_agent(integration_server_id, mcp_server_ids)` exposes only the selected servers. In integration mode, the session is bound to one customer backend.
- **Stable references.** Tool wrappers survive server restart; the proxy reconnects when needed.

## 3. MCP management API

| Method | Path | Purpose |
| :-- | :-- | :-- |
| `GET` | `/mcp/runtime` | Runtime information |
| `GET` | `/mcp/config` | Configuration view |
| `POST` | `/mcp/preflight` | Validate a vendor configuration before import |
| `POST` | `/mcp/import` | Import a vendor configuration |
| `GET` | `/mcp/integrations` | List integration candidates |
| `GET` | `/mcp/servers` | List servers |
| `GET` | `/mcp/servers/{server_id}` | Server detail |
| `POST` | `/mcp/servers` | Create a server |
| `PUT` | `/mcp/servers/{server_id}` | Update a server |
| `DELETE` | `/mcp/servers/{server_id}` | Delete a server |
| `POST` | `/mcp/servers/{server_id}/enable` | Enable or disable |
| `POST` | `/mcp/servers/{server_id}/probe` | Probe connectivity |
| `POST` | `/mcp/servers/{server_id}/restart` | Restart the client |
| `GET` | `/mcp/servers/{server_id}/tools` | List tools |
| `POST` | `/mcp/servers/{server_id}/tools/{tool_name}/test` | Test one tool |
| `GET` | `/mcp/servers/{server_id}/logs` | Server logs |

Transports:

- `stdio` for local child processes
- Streamable HTTP for remote services

Environment overrides include `HARNESS_MCP_CONFIG_PATH`, `MCP_TRANSPORT`, `MCP_HOST`, `MCP_PORT`, and `MCP_PATH`.

## 4. Sample MCP server

`src/mcp_server/` is a FastMCP server used for development and demos:

- `server_main.py` — entry point
- `server_config.py` — server configuration
- `tools/` — sample tools

It is **not** the integration delivery surface. In integration mode, the customer implements the MCP server on their side; Harness is only the Host/Client.

## 5. Contract for an MCP server

Minimum protocol methods:

- `initialize`
- `tools/list`
- `tools/call`
- `ping`

Tool results should be structurally consumable:

```json
{
  "content": [{"type": "text", "text": "text for humans and models"}],
  "structuredContent": {"customer_id": "C1001"},
  "isError": false
}
```

Unified errors:

```json
{
  "code": "NOT_FOUND",
  "message": "customer not found",
  "category": "business",
  "retryable": false,
  "details": {}
}
```

`category` is one of `tool`, `business`, `security`, `runtime`. Callers retry only when `retryable` is `true`.

Hard rules:

1. **Idempotency.** Side-effecting tools accept `idempotency_key`; repeated calls return the same result (24h retention recommended).
2. **Timeout.** 15s connect, 120s per call. On cancellation, stop and leave side effects queryable and compensable.
3. **Description is the contract.** `description` states purpose, when to use, and side effects. `inputSchema` uses `additionalProperties: false`. Most wrong-tool selections are description bugs.

Full field definitions, examples in Python/Java/C#/Go, and a self-test checklist are in `docs/api/MCP服务接入API.md`.

## 6. Adding a built-in tool

1. Create the tool in `src/agent/tools/`. Prefer a narrow, typed signature and a clear docstring; the docstring becomes the model-facing description.
2. Decide who gets it: main agent, a specific subagent YAML, or both. Do not add everything to the main agent.
3. If the tool has side effects, route it through HITL and require an idempotency key.
4. If it executes commands, delegate to the sandbox; never run `subprocess` directly from an agent tool.
5. Add it to `main_tools` in `src/agent/main_agent.py`, or to `local_tools` in `_load_sync_subagents`.
6. Confirm no name collision; graph construction fails on duplicates.
7. Add a deterministic test under `test/` or `tests/` that calls the tool directly without the model.

## 7. MCP security checklist

MCP servers are a supply chain. Treat tool descriptions and results as untrusted input.

| Risk | Mitigation |
| :-- | :-- |
| Tool poisoning in descriptions | Review descriptions; pin server versions; do not auto-import unknown configs |
| Prompt injection in tool output | Never let tool output change system policy; keep output bounded and structured |
| Over-broad credentials | Per-server secrets; least privilege; no shared admin token |
| Data exfiltration | Restrict network egress; bind integration sessions to one backend |
| Unbounded output | Truncate and structure results; offload large payloads |
| Destructive writes | HITL approval + idempotency keys |

## 8. When to choose MCP vs a built-in tool

| Use MCP when | Use a built-in tool when |
| :-- | :-- |
| The capability belongs to a customer or external system | The capability is part of the runtime itself |
| The tool must be independently deployed and updated | The tool needs direct access to runtime state |
| Multiple harness instances should share one tool server | The tool is trivial and stable |
| The organization already standardizes on MCP | Adding a network hop would be pure overhead |

Do not wrap a built-in tool in an MCP server "for consistency". That adds latency and a failure mode without a benefit.

## Next

- [[Sandbox]] for execution
- [[RAG-Integration]] for knowledge tools
- [[Integration-Mode]] for customer MCP servers
