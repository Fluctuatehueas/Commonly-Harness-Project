# Development Guide

This page is the practical guide for changing the codebase.

## 1. Repository map

```text
main.py                     FastAPI entry, Windows event loop policy, logging
langgraph.json              Agent Protocol graph declaration
src/api/                    HTTP / SSE routers and AgentLoader
src/agent/
  main_agent.py             Composition root
  agent_team/               Team assembly, context, tools, runner
  backends/                 Workspace, sandbox, skill sync
  context/                  Anchors, budget, evaluator, autotune
  evolution/                Eval sets, attribution, experience, releases
  mcp/                      MCP runtime and configuration
  memory/                   Prompts and AGENTS.md
  middlewares/              Context, limits, trajectory, verification
  runtime/                  Supervision and error handling
  skills/                   Bundled skills
  subagents/                YAML subagents and async entry
  tools/                    Built-in tools
  verification/             L0/L1/L2 verification
src/mcp_server/             Sample FastMCP server
src/rag/                    RAG provider adapter
src/scheduler/              Task scheduling
src/task_flow/              Task flow compiler and executor
src/utils/                  Logging, integrity, model web service
frontend/                   Web console and Electron app
sandbox/                    Dockerfile, setup script, server config
docs/                       Chinese design and integration docs
tests/ · test/              Tests
```

## 2. Local setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

The project uses `uv` in some workflows:

```powershell
uv pip install --python .\.venv\Scripts\python.exe -r requirements.txt
```

Start PostgreSQL and the sandbox as described in [[Getting-Started]].

## 3. Code conventions

- **Comments are Chinese.** Match the existing code. Keep comments short; explain why, not what.
- **Atomic writes.** Configuration and user data use temporary files plus replace. Follow this pattern for new persistent files.
- **Path safety.** Sanitize user-derived path segments; never concatenate raw input into a filesystem path.
- **No direct DB access from agent code.** Go through Store, checkpointer, or the owning store module.
- **No host command execution from tools.** Delegate to the sandbox, except host tools with HITL.
- **Unique tool names.** Graph construction fails on duplicates; that is intentional.
- **Fail loudly at composition time.** A missing mandatory tool or duplicate name should raise during graph build, not at the first user request.
- **Do not add business logic.** The repository must stay reusable.

## 4. Add a built-in tool

1. Create the implementation in `src/agent/tools/`.
2. Use a typed signature and a model-facing docstring.
3. Decide the audience: main agent, a subagent, or both.
4. Add HITL and an idempotency key if it has side effects.
5. Register it in `main_tools` in `main_agent.py`, or in `local_tools` for subagents.
6. Check for name collisions.
7. Add a deterministic test that calls the tool without the model.

## 5. Add a middleware

Middleware order is semantic. Read the order table in [[Agent-Runtime]] before inserting anything.

A middleware typically implements:

- `before_agent` / `before_model` / `before_tool`
- `after_tool` / `after_model` / `after_agent`

Rules:

- Observation middleware must not mutate state.
- Anything that rewrites messages belongs before the final model call and must be compatible with compaction.
- `after_*` hooks run in reverse order; do not assume the same order as `before_*`.
- Log with a stable prefix so failures are searchable.

## 6. Add a subagent

1. Create a YAML in `src/agent/subagents/configs/`.
2. Include `name`, `description`, `system_prompt`, `tools`.
3. Use `optional_tools` for capabilities that may be absent.
4. Use `deny_tools` or `permissions` to keep it narrow.
5. Add the filename to `SYNC_SUBAGENT_CONFIG_FILES` in `main_agent.py` if it should be global.
6. Verify the name is unique.
7. Test with a direct prompt before exposing it.

Details are in [[Subagents-and-Agent-Team]].

## 7. Add a skill

A skill is a directory with a `SKILL.md` entry point and optional scripts/assets. Bundled skills live in `src/agent/skills/`, and per-user copies live under `<USER_DATA_DIR>/users/<id>/skills/`.

Rules:

- `SKILL.md` describes when to use the skill and what it does.
- Scripts inside a skill are **documentation and sandbox assets**, not code executed on the host.
- The `skill-management` skill is protected.
- When a sandbox exists, skills are synchronized by `SandboxSkillSynchronizer`; do not assume a host path is visible inside the sandbox.
- Without a sandbox, skills are read-only under `/skills/`.

## 8. Add an MCP server configuration

For development, configure through `/mcp/servers` or `mcp/servers.json`. For a customer integration, the customer runs the server and provides its endpoint and credentials.

Checklist:

- Transport is `stdio` or Streamable HTTP.
- Health endpoint is reachable.
- `tools/list` returns the expected tools.
- At least one tool has a constrained input schema.
- Errors include `category` and `retryable`.
- Write tools accept an idempotency key.
- The server can be restarted without breaking the Harness graph.

## 9. Add a RAG provider

1. Implement `rag_manifest` and `rag_retrieve` on an MCP server, or `/retrieve` on HTTP.
2. Register the provider via `/rag/providers` or `rag/providers.json`.
3. Probe it with `/rag/providers/{id}/probe`.
4. Verify citations resolve to real documents.
5. Verify empty results do not raise errors.

See [[RAG-Integration]].

## 10. Add an API route

1. Create or extend a router in `src/api/`.
2. Use a `prefix` and clear tags.
3. Resolve identity from the request context, not from an untrusted body field.
4. Return typed response models.
5. Register the router in `src/api/chat.py` (`app.include_router`).
6. Update `docs/api/` if the endpoint is part of the external contract.

## 11. Testing

Tests live in `tests/` and `test/`. Prefer deterministic tests:

- Call the tool or loader directly, without the model.
- Test YAML validation failures.
- Test path validation and atomic writes.
- Test compaction threshold decisions with synthetic messages.
- Test scheduler planning and wave computation with fixed inputs.
- Test API routes with a test client.

For model-dependent behavior, use an eval set and record a baseline; do not rely on a single manual run.

## 12. Debugging

| Need | Tool |
| :-- | :-- |
| API shape | `/docs` |
| Liveness | `/health` |
| Configuration | `/settings/readiness` |
| One turn's execution | `/trajectory/{thread_id}` |
| Session messages | `/history/{thread_id}/messages` |
| Sandbox | `/settings/sandbox/status`, `/settings/sandbox/check` |
| MCP | `/mcp/servers/{id}/probe`, `/logs`, `/tools` |
| Scheduler | `/scheduler/runs`, `/events` |
| Logs | `logs/` JSON Lines |
| Project audit | `.commonharness/audit.jsonl` |

Start from the first error in the log, then correlate with the trajectory event. Do not guess from the final answer alone.

## 13. Hot updates

- Model settings: `PUT /settings/model` writes `.env` and rebuilds the graph.
- Context thresholds: `PUT /settings/context`.
- Sandbox settings: `PUT /settings/sandbox`.
- MCP and RAG configuration: management APIs persist and reload.

Hot update is convenient but risky. After changing model or thresholds, run one representative eval case before declaring success.

## 14. Common development mistakes

| Mistake | Consequence |
| :-- | :-- |
| Building a graph in the API layer | Breaks per-user caching and isolation |
| Opening a DB connection in a tool | Bypasses Store/checkpointer ownership |
| Running `subprocess` from a tool | Executes on the host, violating the sandbox boundary |
| Adding a mandatory tool to a subagent YAML before it exists | Graph construction fails for every user |
| Reordering middleware without reading the order table | Verification loses `run_id`, timestamps break after compaction |
| Storing full user text in shadow or evolution records | Privacy violation |
| Assuming `after_*` runs in declaration order | Incorrect cleanup and memory writes |
| Adding business-specific logic to this repository | Destroys reusability |

## 15. Definition of done

A change is done when:

1. It has a focused test or a documented eval case.
2. It does not bypass the API → AgentLoader → graph → backend boundaries.
3. It has no new host-execution path.
4. It preserves or improves an existing eval baseline.
5. Documentation that describes the old behavior is updated.

## Next

- [[Architecture]] for boundaries
- [[API-Reference]] for contracts
- [[Verification-and-Evolution]] for evaluation
