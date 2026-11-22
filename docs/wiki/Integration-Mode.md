# Integration Mode

Integration mode is the server-side deployment where Harness is embedded into a customer platform. Personal mode is the local developer experience; integration mode is the delivery mode.

## 1. Ownership boundary

| Role | Owns | Must not own |
| :-- | :-- | :-- |
| Customer business backend | Business data, business APIs, RAG service, MCP Server, business permissions | Agent orchestration, context, sandbox |
| Customer gateway | Login, tenant identification, roles, request signing, audit entry | Agent runtime |
| Harness | Agent graph, context, memory, HITL, sandbox, MCP Host, RAG adapter, trajectory | Business data, business rules, vector storage |

The delivery formula:

> The customer provides **MCP services + subagent YAML + skills**; Harness organizes them into reliable execution.

## 2. Topology

```text
User
  │
  ▼
Customer portal / business UI
  │  authenticated request
  ▼
Customer gateway
  │  strips client identity headers
  │  injects tenant_id / user_id / roles
  ▼
Harness API (integration mode)
  ├─ Agent runtime
  ├─ Per-user managed workspace
  ├─ PostgreSQL (sessions, memory, trajectory)
  └─ Sandbox
        │
        ├─ MCP Client ──► customer MCP Server ──► business data
        └─ RAG Adapter ─► customer RAG service ─► knowledge
```

Harness does not expose an MCP endpoint for the customer's business system to call back into. It is the Host/Client side.

## 3. Identity

The gateway must:

1. Authenticate the user.
2. Strip any client-supplied identity headers, including `X-User-Id`.
3. Inject trusted `tenant_id`, `user_id`, and `roles`.
4. Optionally sign the request and record an audit entry.

Harness does not implement login, registration, tenancy, or role management. It enforces permissions at tool and workspace boundaries but treats gateway-injected identity as authoritative.

Isolation keys:

| Key | Scope |
| :-- | :-- |
| `tenant_id` | Deployment boundary |
| `user_id` | Memory, sandbox, workspace, settings |
| `thread_id` | Conversation and checkpoint |
| `project_id` | Workspace and artifacts |

## 4. Workspace

In integration mode, projects live on the server:

```text
USER_DATA_DIR/users/{user_id}/workspaces/{project_id}/
```

On Linux the default `USER_DATA_DIR` is `~/.harness`, unless overridden by `HARNESS_USER_DATA_DIR`.

Rules:

- Project roots must stay under the managed workspace root. Do not attach arbitrary server paths.
- The `.commonharness` marker defines the writable trust boundary.
- Commands still run only in the sandbox.
- Project, session, file, and publish APIs keep the same semantics as personal mode.

This is the main code change needed when moving from local to server deployment: path validation must target the managed root, not a Windows local path.

## 5. MCP contract

The customer implements an MCP Server with:

- `initialize`, `tools/list`, `tools/call`, `ping`
- At least one business tool
- For knowledge: `rag_manifest`, `rag_retrieve`

A session is bound to one integration server:

```text
integration_server_id
```

Only tools from that server and the shared public MCP configuration are exposed. Business tools from other integration servers are not visible, which prevents cross-tenant data leakage.

Tool requirements:

- Structured results with `structuredContent` and `isError`
- Unified errors with `category` and `retryable`
- `idempotency_key` for side-effecting tools
- Clear descriptions and constrained input schemas

The full contract is in `docs/api/MCP服务接入API.md` and summarized in [[Tools-and-MCP]].

## 6. RAG

Harness provides an adapter and registry, not a knowledge base:

- It does not chunk, embed, index, or store knowledge originals.
- It does not host a vector database.
- It normalizes retrieval results and citations.
- It passes `user_scope` through; permission filtering is the customer's responsibility.

Either expose `rag_manifest` / `rag_retrieve` on the MCP server or provide HTTP `/retrieve` and `/knowledge/*`. See [[RAG-Integration]].

## 7. What stays out of this repository

Do not add:

- Customer business tools
- Customer business prompts
- Customer business subagents
- Direct SQL connections to business databases
- Business data files in the Harness workspace

The repository must remain reusable. Business-specific artifacts belong in the customer's integration layer.

## 8. Rollout phases

| Phase | Work | Acceptance |
| :-- | :-- | :-- |
| 1. Connect | Gateway identity injection; one chat turn; one read-only MCP tool | Forged identity ignored; tool call visible in trajectory |
| 2. Read-only | 3–5 read-only tools with descriptions and error codes | Correct tool selection; errors classified |
| 3. Controlled writes | Write tools with idempotency keys; destructive tools behind HITL | Repeated writes consistent; destructive actions require approval |
| 4. Knowledge and observability | RAG provider, eval set, regression run | Answers cite sources; failures traceable |

Do not skip to phase 3. Read-only integration exposes contract and permission problems cheaply.

## 9. Acceptance checklist

| # | Check | Pass criteria |
| :-- | :-- | :-- |
| 1 | Identity injection | Client-forged identity has no effect; unauthorized requests return 403 |
| 2 | MCP connectivity | No connections at startup; first call connects; one failing server does not break others |
| 3 | Tool contract | Description, constrained schema, unified error code, truthful `retryable` |
| 4 | Idempotency and approval | Same-key repeated writes consistent; destructive operations require confirmation |
| 5 | RAG citations | Citations trace to document and page; empty results do not error |
| 6 | Isolation | Two users cannot see each other's sessions, memory, or files |
| 7 | Observability | A failure can be traced to tool, arguments, and cause |
| 8 | Recovery | Interrupt or restart resumes from checkpoint without repeating side effects |
| 9 | Data boundary | Business data stays in customer systems; Harness stores only runtime state |

## 10. Operational notes

- Restrict management endpoints (`/settings`, `/mcp`, `/rag`, `/evolution`, `/agents`) to the intranet or administrators.
- Put the API behind a gateway that enforces rate limits and request size limits.
- Back up PostgreSQL and the user data directory together.
- Keep one Harness deployment per tenant boundary unless the workspace and identity model is proven safe for shared tenancy.
- Session mutex currently works only within a single process; multi-worker deployment needs additional design.

## Next

- [[API-Reference]] for endpoints
- [[Deployment-and-Operations]] for production setup
- [[Troubleshooting-FAQ]] for integration symptoms
