# Sessions, Memory, and Workspace

Three persistence mechanisms are often confused because they all "remember something". They have different keys, lifetimes, and owners.

| Mechanism | Key | Stores | Backend |
| :-- | :-- | :-- | :-- |
| Checkpoint | `thread_id` | Conversation state and graph state | LangGraph PostgreSQL checkpointer |
| Store | `user_id` | Long-term memory and user preferences | DeepAgents Store (PostgreSQL) |
| User data directory | `user_id` | Settings, MCP config, RAG config, skills, project registry | Local filesystem |
| Project workspace | `user_id` + `project_id` | Project files and published artifacts | Filesystem / sandbox |

## 1. Isolation keys

```text
tenant_id  → deployment boundary (integration mode)
user_id    → memory, sandbox, workspace, settings
thread_id  → conversation and checkpoint
project_id → project workspace and artifacts
```

Identity is taken from `X-User-Id` in personal mode. In integration mode the gateway must strip client-supplied identity headers and inject trusted `tenant_id` / `user_id` / `roles`. Harness does not implement login.

## 2. User data directory

Resolution order in `src/agent/config.py`:

1. `HARNESS_USER_DATA_DIR`, if set and writable
2. Windows: `%LOCALAPPDATA%/CommonHarness`; Linux/macOS: `~/.harness`
3. Fallback: `<project>/.harness/user-data`
4. Fallback: system temp `/CommonHarness`

Each candidate is probed with a real write test. If the preferred directory is not writable, startup continues with a warning and `settings/readiness` reports the degraded path.

Per-user layout:

```text
<USER_DATA_DIR>/users/<user_id>/
├── settings.json          # model / sandbox / context / archive settings
├── agents.json            # note only; MyAgent profiles live in PostgreSQL
├── mcp/servers.json       # per-user MCP server configuration
├── rag/providers.json     # per-user RAG provider configuration
├── skills/                # per-user skill library (copied from bundled skills on first use)
├── skills-disabled/       # disabled skills
└── projects.json          # project registry
```

Writes use sanitized directory names and atomic replacement to prevent path traversal and partial configuration files.

## 3. Checkpoints and sessions

`thread_id` is the unit of conversation state. The checkpointer stores:

- Messages
- Tool calls and results
- Interrupt payloads
- State required to resume

Session metadata is stored separately so listing sessions does not require replaying checkpoints.

### Session API

| Method | Path | Purpose |
| :-- | :-- | :-- |
| `POST` | `/history` | Create a session |
| `GET` | `/history` | List sessions |
| `GET` | `/history/{thread_id}/messages` | Read serialized messages |
| `GET` | `/history/{thread_id}/usage` | Token usage |
| `PATCH` | `/history/{thread_id}` | Rename, pin, archive, and similar updates |
| `DELETE` | `/history/{thread_id}` | Delete a session |
| `POST` | `/history/auto-archive` | Archive sessions older than the configured days |
| `POST` | `/history/auto-purge` | Permanently remove expired archived sessions |

There is also a compatibility alias `PATCH /sessions/{thread_id}`, excluded from the OpenAPI schema.

`GET /history/{thread_id}/messages` serializes checkpoint messages into the frontend message model. It is read-only and does not resume execution.

### Archive

`HARNESS_SESSION_ARCHIVE_DAYS` controls automatic archiving (default `7`; `0` disables it). Archived sessions keep their data but leave the active list. Permanent deletion is a separate action.

## 4. Long-term memory

`MemoryUpdateMiddleware` runs in the `after_agent` phase and writes durable user preferences into the Store. The Store namespace is `(user_id,)`, and memory files are addressed through `/memories/`.

What belongs in memory:

- Stable user preferences (language, tone, recurring constraints)
- Durable facts the user explicitly asks to remember

What does not belong:

- Business records
- Sensitive data the customer expects to stay in their systems
- Everything from the current conversation

`HARNESS_MEMORY_UPDATE_ASYNC=1` (default) makes the update asynchronous so it does not block the response. Turning it off makes memory writes synchronous and easier to test.

## 5. Projects and workspace

A project is a directory with a `.commonharness` marker directory. The marker establishes the trust boundary: Harness may create subdirectories and write files under a marked project, but it may not execute project files directly.

Project rules:

| Rule | Detail |
| :-- | :-- |
| Registration | Stored per user in `projects.json` |
| Marker | `.commonharness` must exist at the project root |
| Ignore list | `.git`, `node_modules`, `.venv`, `dist`, `build`, `__pycache__`, `.commonharness` |
| Audit | `.commonharness/audit.jsonl` records creation, attach, marker repair, removal, validation failure, and artifact publishing |
| Execution | Commands run only in the sandbox |
| Publishing | Sandbox outputs are published to the project through `publish_to_project` |

Project API:

| Method | Path | Purpose |
| :-- | :-- | :-- |
| `GET` | `/projects` | List projects |
| `POST` | `/projects` | Create and register a project |
| `POST` | `/projects/validate` | Validate a candidate path |
| `POST` | `/projects/{project_id}/validate` | Re-validate a registered project |
| `PATCH` | `/projects/{project_id}` | Update metadata |
| `DELETE` | `/projects/{project_id}` | Remove from registry |
| `GET` | `/projects/{project_id}/conversations` | Conversations attached to the project |
| `GET` | `/projects/{project_id}/artifacts` | Published artifacts |

## 6. Session files

Session-scoped files are listed and downloaded through:

| Method | Path | Purpose |
| :-- | :-- | :-- |
| `GET` | `/threads/{thread_id}/files` | List files |
| `GET` | `/threads/{thread_id}/files/content` | Read content |
| `GET` | `/threads/{thread_id}/files/download` | Download |

Free sessions use virtual paths rooted at the session directory. Project sessions use the project root as `/`. Do not mix `/workspace/<project_id>` sandbox paths with virtual file paths; this is a common source of "file not found" confusion.

## 7. Free vs project sessions

| | Free session | Project session |
| :-- | :-- | :-- |
| Root for file tools | Session directory | Project directory |
| Commands | Sandbox session directory | Sandbox, with project files staged when listed in `files` |
| Artifacts | Stay in the sandbox and are visible in the file drawer | Can be published to the project |
| `publish_to_project` | Not used | Used for final deliverables |

The system prompt encodes these rules, and the backend enforces them. If a change makes free and project sessions behave the same, the boundary is wrong.

## 8. Backup and recovery

Back up both sides together:

- PostgreSQL: checkpoints, Store, session index, trajectory, usage, scheduler, feedback
- User data directory: settings, MCP/RAG configuration, skills, project registry

A checkpoint without its Store still resumes, but loses memory; a Store without a checkpoint loses the conversation. Restore both to recover behavior.

## Next

- [[Context-Engineering]] for what consumes the checkpoint
- [[Sandbox]] for execution isolation
- [[Integration-Mode]] for server-side workspaces
