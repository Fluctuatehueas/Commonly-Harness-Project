# API Reference

All routes are served by the FastAPI app in `src/api/chat.py`. The OpenAPI schema is always authoritative: start the backend and open `http://127.0.0.1:8000/docs`.

## 1. Conventions

- Base URL in development: `http://127.0.0.1:8000`
- Identity: `X-User-Id` header in personal mode
- Integration mode: identity is injected by the gateway; client-supplied identity headers must be stripped
- Streaming: `POST /chat/stream` uses SSE
- Errors: HTTP status plus a JSON body; tool-level errors follow the MCP error contract

## 2. Chat

| Method | Path | Purpose |
| :-- | :-- | :-- |
| `POST` | `/chat` | One chat turn |
| `POST` | `/chat/stream` | Streaming chat over SSE |
| `POST` | `/chat/{thread_id}/resume` | Resume after an interrupt |
| `POST` | `/chat/{thread_id}/retry` | Retry the last turn |
| `POST` | `/chat/{thread_id}/withdraw` | Withdraw a message |

Typical request:

```json
{
  "message": "hello",
  "user_id": "alice",
  "thread_id": "optional-existing-thread",
  "project_id": "optional-project"
}
```

Actual fields are defined in `src/api/chat.py` request models. Use `/docs` for the generated schema.

Interrupts are returned as structured payloads. The frontend renders three outcomes: provide more information, approve/edit tool arguments, or reject. `resume` continues from the checkpoint.

## 3. Sessions

| Method | Path | Purpose |
| :-- | :-- | :-- |
| `POST` | `/history` | Create session |
| `GET` | `/history` | List sessions |
| `GET` | `/history/{thread_id}/messages` | Messages |
| `GET` | `/history/{thread_id}/usage` | Token usage |
| `PATCH` | `/history/{thread_id}` | Rename / pin / archive |
| `DELETE` | `/history/{thread_id}` | Delete |
| `POST` | `/history/auto-archive` | Archive expired sessions |
| `POST` | `/history/auto-purge` | Purge expired archived sessions |

Compatibility alias: `PATCH /sessions/{thread_id}` (not in OpenAPI).

## 4. Context and attachments

| Method | Path | Purpose |
| :-- | :-- | :-- |
| `GET` | `/context/formats` | Supported formats |
| `POST` | `/context/upload` | Upload |
| `POST` | `/context/url` | Import URL |
| `DELETE` | `/context/{file_id}` | Delete |
| `POST` | `/context/resolve` | Resolve attachments |

## 5. Trajectory

| Method | Path | Purpose |
| :-- | :-- | :-- |
| `GET` | `/trajectory/{thread_id}` | Trajectory for a thread |

Trajectory events include turns, model thinking, tool calls, tool arguments, results, latency, and token usage.

## 6. Async tasks

| Method | Path | Purpose |
| :-- | :-- | :-- |
| `GET` | `/async-tasks` | List tasks |
| `GET` | `/async-tasks/{task_id}` | Status |
| `POST` | `/async-tasks/{task_id}/cancel` | Cancel |

## 7. Session files

| Method | Path | Purpose |
| :-- | :-- | :-- |
| `GET` | `/threads/{thread_id}/files` | List |
| `GET` | `/threads/{thread_id}/files/content` | Read |
| `GET` | `/threads/{thread_id}/files/download` | Download |

## 8. Projects

| Method | Path | Purpose |
| :-- | :-- | :-- |
| `GET` | `/projects` | List |
| `POST` | `/projects` | Create and register |
| `POST` | `/projects/validate` | Validate a path |
| `POST` | `/projects/{project_id}/validate` | Validate a project |
| `PATCH` | `/projects/{project_id}` | Update |
| `DELETE` | `/projects/{project_id}` | Remove |
| `GET` | `/projects/{project_id}/conversations` | Conversations |
| `GET` | `/projects/{project_id}/artifacts` | Artifacts |

## 9. Settings

| Method | Path | Purpose |
| :-- | :-- | :-- |
| `GET` | `/settings/model` | Per-user model settings |
| `PUT` | `/settings/model` | Update and rebuild graph |
| `GET` | `/settings/model/global` | Global model settings |
| `PUT` | `/settings/model/global` | Update global model |
| `GET` | `/settings/feedback` | Feedback settings |
| `GET` | `/settings/context` | Context settings |
| `PUT` | `/settings/context` | Update context settings |
| `GET` | `/settings/archive` | Archive settings |
| `PUT` | `/settings/archive` | Update archive settings |
| `GET` | `/settings/sandbox` | Sandbox settings |
| `PUT` | `/settings/sandbox` | Update sandbox settings |
| `GET` | `/settings/sandbox/status` | Sandbox status |
| `POST` | `/settings/sandbox/check` | Probe sandbox port |
| `GET` | `/settings/readiness` | Overall readiness |

`PUT /settings/model` writes `.env` and rebuilds the graph. Treat it as a configuration change with a release-like risk.

## 10. MCP

| Method | Path | Purpose |
| :-- | :-- | :-- |
| `GET` | `/mcp/runtime` | Runtime info |
| `GET` | `/mcp/config` | Configuration |
| `POST` | `/mcp/preflight` | Preflight import |
| `POST` | `/mcp/import` | Import configuration |
| `GET` | `/mcp/integrations` | Integration list |
| `GET` | `/mcp/servers` | List servers |
| `GET` | `/mcp/servers/{id}` | Detail |
| `POST` | `/mcp/servers` | Create |
| `PUT` | `/mcp/servers/{id}` | Update |
| `DELETE` | `/mcp/servers/{id}` | Delete |
| `POST` | `/mcp/servers/{id}/enable` | Enable / disable |
| `POST` | `/mcp/servers/{id}/probe` | Probe |
| `POST` | `/mcp/servers/{id}/restart` | Restart |
| `GET` | `/mcp/servers/{id}/tools` | Tools |
| `POST` | `/mcp/servers/{id}/tools/{tool}/test` | Test tool |
| `GET` | `/mcp/servers/{id}/logs` | Logs |

## 11. RAG

| Method | Path | Purpose |
| :-- | :-- | :-- |
| `GET` | `/rag/config` | Configuration |
| `GET` | `/rag/providers` | List |
| `GET` | `/rag/providers/{id}` | Detail |
| `POST` | `/rag/providers` | Create |
| `PUT` | `/rag/providers/{id}` | Update |
| `DELETE` | `/rag/providers/{id}` | Delete |
| `POST` | `/rag/providers/{id}/enable` | Enable / disable |
| `POST` | `/rag/providers/{id}/probe` | Probe |
| `POST` | `/rag/retrieve` | Retrieve |
| `POST` | `/rag/providers/{id}/ingest` | Ingest |
| `GET` | `/rag/providers/{id}/knowledge/documents` | Documents |
| `DELETE` | `/rag/providers/{id}/knowledge/documents/{doc_id}` | Delete document |
| `GET` | `/rag/providers/{id}/knowledge/stats` | Stats |
| `POST` | `/rag/discover` | Discover |

## 12. Skills

Prefix: `/skills`

| Method | Path | Purpose |
| :-- | :-- | :-- |
| `GET` | `/skills` | List |
| `GET` | `/skills/content` | Read `SKILL.md` |
| `POST` | `/skills/toggle` | Enable / disable |
| `POST` | `/skills/create` | Create |
| `POST` | `/skills/upload` | Upload package |
| `POST` | `/skills/download-url` | Download from URL |
| `DELETE` | `/skills/item` | Delete |
| `GET` | `/skills/jobs` | Install jobs |
| `GET` | `/skills/jobs/{job_id}` | Job status |
| `GET` | `/skills/storage` | Storage info |
| `POST` | `/skills/reveal` | Reveal in file manager |

`skill-management` is a protected skill and cannot be deleted.

## 13. Agents and groups

| Method | Path | Purpose |
| :-- | :-- | :-- |
| `GET` | `/agents` | List profiles |
| `GET` | `/agents/{agent_id}` | Detail |
| `POST` | `/agents` | Create |
| `PUT` | `/agents/{agent_id}` | Update |
| `DELETE` | `/agents/{agent_id}` | Delete |
| `GET` | `/agents/{agent_id}/groups` | Groups of an agent |
| `GET` | `/agent-groups` | List groups |
| `GET` | `/agent-groups/{group_id}` | Detail |
| `POST` | `/agent-groups` | Create |
| `PUT` | `/agent-groups/{group_id}` | Update |
| `DELETE` | `/agent-groups/{group_id}` | Delete |
| `POST` | `/agent-groups/{group_id}/members` | Add member |
| `DELETE` | `/agent-groups/{group_id}/members/{agent_id}` | Remove member |
| `PUT` | `/agent-groups/{group_id}/leader` | Set leader |
| `DELETE` | `/agent-groups/{group_id}/leader` | Clear leader |

## 14. Scheduler and task flows

| Method | Path | Purpose |
| :-- | :-- | :-- |
| `GET` | `/scheduler/runs` | List runs |
| `POST` | `/scheduler/runs/{run_id}/cancel` | Cancel |
| `GET` | `/scheduler/runs/{run_id}/events` | Events |

| Method | Path | Purpose |
| :-- | :-- | :-- |
| `GET` | `/task-flows` | List |
| `POST` | `/task-flows` | Create |
| `GET` | `/task-flows/{flow_id}` | Detail |
| `PUT` | `/task-flows/{flow_id}` | Update |
| `DELETE` | `/task-flows/{flow_id}` | Delete |
| `POST` | `/task-flows/{flow_id}/publish` | Publish |
| `PATCH` | `/task-flows/{flow_id}/enabled` | Enable / disable |
| `POST` | `/task-flows/{flow_id}/fork` | Fork |
| `GET` | `/task-flows/{flow_id}/executions` | Executions |
| `GET` | `/task-flows/executions/{execution_id}` | Execution detail |

## 15. Evolution and feedback

| Method | Path | Purpose |
| :-- | :-- | :-- |
| `GET` | `/evolution/config` | Configuration |
| `GET` | `/evolution/experiences` | Experience list |
| `GET` | `/evolution/experiences/{id}` | Experience detail |
| `POST` | `/evolution/experiences/{id}/approve` | Approve |
| `POST` | `/evolution/experiences/{id}/quarantine` | Quarantine |
| `DELETE` | `/evolution/experiences/{id}` | Delete |
| `GET` | `/evolution/tool-health` | Tool health |
| `POST` | `/evolution/rewards` | Record reward |
| `GET` | `/evolution/rewards` | Rewards |
| `GET` | `/evolution/candidates` | Candidates |
| `POST` | `/evolution/candidates/{id}/apply` | Apply |
| `POST` | `/evolution/candidates/{id}/reject` | Reject |
| `GET` | `/evolution/releases` | Releases |
| `POST` | `/evolution/releases/{id}/rollback` | Rollback |
| `GET` | `/evolution/eval-sets` | Eval sets |
| `POST` | `/evolution/eval-sets` | Add case |
| `GET` | `/evolution/training-data` | Training data |
| `GET` | `/evolution/metrics` | Metrics |
| `POST` | `/feedback` | Submit feedback |

## 16. Health

| Method | Path | Purpose |
| :-- | :-- | :-- |
| `GET` | `/` | Index |
| `GET` | `/health` | Liveness |

## 17. API design notes

- Management endpoints under `/settings`, `/mcp`, `/rag`, `/evolution`, and `/agents` should be restricted to intranet or administrator access.
- All endpoints identify the user through headers; do not accept a `user_id` from an untrusted body in integration mode.
- The frontend's `X-User-Id` is a development convenience, not an authentication mechanism.
- Chat and SSE are the only endpoints expected to be exposed through a customer gateway without extra hardening beyond identity injection and rate limits.

For the full request/response schemas and examples, see `docs/api/业务主链路API.md`, `docs/api/管理与配置API.md`, and `docs/api/MCP服务接入API.md`.

## Next

- [[Integration-Mode]] for gateway and identity requirements
- [[Frontend]] for how the console calls these routes
- [[Troubleshooting-FAQ]] for error symptoms
