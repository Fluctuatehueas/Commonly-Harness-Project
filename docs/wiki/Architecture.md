# Architecture

This page describes module boundaries, call direction, state ownership, and the request lifecycle.

## 1. One-page view

```text
Browser / customer gateway
        │ HTTP + SSE
        ▼
FastAPI (src/api/chat.py)
  /chat  /chat/stream  /chat/{thread_id}/resume
  /history  /async-tasks  /health
        │
        ▼
AgentLoader (src/api/agent_loader.py)
  user_id    → cached agent graph
  thread_id  → independent checkpoint
        │
        ├───────────────────────────┐
        ▼                           ▼
DeepAgents graph              SandboxManager
src/agent/main_agent.py        per-user sandbox proxy
  ├ middleware                        │
  ├ tools                             ▼
  └ subagents                 SandboxBackendProxy
        │                       └ OpenSandboxBackend
        │                           └ OpenSandbox
        ├──────────────► PostgreSQL (Store + Checkpointer)
        └──────────────► LLM endpoint
                          │
                          └ async tools → Agent Protocol (async_subagent)
```

The original Chinese design document with more sequence diagrams is `docs/core_design/架构图与核心时序.md`.

## 2. Four "single sources"

| Single source | Location | Meaning |
| :-- | :-- | :-- |
| Request entry | `api.chat.app` | Chat, history, async tasks, and management endpoints all enter here |
| Agent cache | `AgentLoader` | One graph per user, isolated between users |
| Sandbox reference | `SandboxBackendProxy` | The graph holds a stable proxy; the sandbox instance can be replaced |
| Persistence | PostgreSQL | Checkpoints, user memory, session index, scheduler ownership |

These are deliberate constraints. Bypassing the loader, constructing a sandbox client inside a tool, or writing memory to a local file will break isolation and recovery guarantees.

## 3. Layers and responsibilities

| Layer | Directory | Responsibility | Must not |
| :-- | :-- | :-- | :-- |
| API | `src/api` | HTTP/SSE protocol, request validation, identity header handling, response serialization | Build a graph or connect to the database directly |
| Agent | `src/agent` | Graph assembly, middleware, tools, subagents, prompts, context | Open its own DB connection |
| Execution backends | `src/agent/backends` | Route file operations, proxy the sandbox, synchronize skills | Leak sandbox details into API responses |
| Scheduling | `src/scheduler` | Tiering, planning, waves, checkpoints, replanning | Execute business tools itself |
| Task flows | `src/task_flow` | Compile, validate, store, publish, and execute reusable flows | Bypass HITL for destructive steps |
| RAG | `src/rag` | Provider registry, unified retrieval, citation normalization | Store knowledge originals or vectors |
| MCP sample server | `src/mcp_server` | A FastMCP server for development and demos | Act as the integration delivery surface |
| Utilities | `src/utils` | Logging, integrity checks, model web service, visualization artifacts | Own agent state |

## 4. Request lifecycle

A streaming chat request:

1. `POST /chat/stream` enters `src/api/chat.py`.
2. Identity is resolved from `X-User-Id` (or gateway-injected headers in integration mode).
3. `AgentLoader` returns the graph cached for `user_id`; the graph is built on first use by `create_main_agent`.
4. `thread_id` selects the checkpoint. A new thread starts from an empty state.
5. The graph runs: middleware injects context, the model selects tools, tools execute (MCP, sandbox, file, HITL), and results stream back as SSE events.
6. `TrajectoryMiddleware` persists run events; `TokenUsageMiddleware` records usage; memory middleware may write preferences back to the Store.
7. If the model requests human input, the run is interrupted and the interrupt payload is returned. `POST /chat/{thread_id}/resume` continues from the checkpoint.
8. If the sandbox dies, the proxy replaces the underlying instance; the graph and checkpoint remain valid.

A non-streaming request follows the same path and returns a single `ChatResponse`.

## 5. State ownership

| State | Owner | Key | Lifetime |
| :-- | :-- | :-- | :-- |
| Conversation messages and graph state | LangGraph checkpointer | `thread_id` | Until session deletion |
| User preferences and long-term memory | DeepAgents Store | `user_id` | Long-lived |
| Session index, titles, pinning, archive | Session store (PostgreSQL) | `user_id` / `thread_id` | Long-lived |
| Project registry and artifacts | Project store + workspace | `user_id` / `project_id` | Long-lived |
| Sandbox filesystem | OpenSandbox instance | `user_id` | Sandbox TTL (`OPEN_SANDBOX_SANDBOX_TIMEOUT_SECONDS`) |
| Trajectory and usage | PostgreSQL tables | `thread_id` / `run_id` | Configurable retention |
| Scheduler runs and events | Scheduler store | `run_id` | Until cleanup |
| Task flow definitions and executions | Task flow store | `flow_id` / `execution_id` | Long-lived |

## 6. Storage routing

`create_main_agent` constructs a `CompositeBackend`:

| Path prefix | Backend | Purpose |
| :-- | :-- | :-- |
| `/memories/` | `StoreBackend` | Long-term memory scoped to `user_id` |
| `/artifacts/` | `StateBackend` | Large tool results and intermediate artifacts that must not enter the project directory |
| `/skills/` | `ReadOnlyFilesystemBackend` | Only when no sandbox is enabled; host skills are mounted read-only |
| everything else | `ProjectWorkspaceBackend` | Project files, with command execution delegated to the sandbox |

The rule behind this table: **persisting files and executing commands are different capabilities with different trust levels.** See [[Sessions-Memory-and-Workspace]] and [[Sandbox]].

## 7. Two operating modes

| Dimension | Personal mode | Integration mode |
| :-- | :-- | :-- |
| Deployment | Local machine | Customer server / intranet |
| Workspace | Real local project directory | Managed workspace under `USER_DATA_DIR/users/{user_id}/workspaces/` |
| Business tools | Local tools, MCP, skills, sandbox | Customer MCP servers |
| Identity | Demo `X-User-Id` | Trusted gateway-injected identity |
| Data boundary | Data stays local | Business data stays with the customer |

Integration details are in [[Integration-Mode]].

## 8. Where to change what

| Task | Start here |
| :-- | :-- |
| Add a graph-level behavior | `src/agent/main_agent.py` and a middleware |
| Add a tool | `src/agent/tools/` |
| Add an MCP integration | MCP configuration API + `src/agent/mcp/` |
| Change compaction | `src/agent/middlewares/context_compaction.py` |
| Change the sandbox lifecycle | `src/agent/backends/sandbox_manager.py` |
| Add a verification rule | `src/agent/verification/` |
| Add an API endpoint | the matching `src/api/*.py` router |
| Change scheduling | `src/scheduler/` |
| Change a prompt policy | `src/agent/main_agent.py` policy blocks or `src/agent/memory/` |

## Next

- [[Agent-Runtime]]
- [[Context-Engineering]]
- [[Sessions-Memory-and-Workspace]]
