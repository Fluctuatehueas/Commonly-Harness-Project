# Common Harness Developer Wiki

Common Harness is a business-agnostic, source-deliverable Agent runtime. It wraps a LangGraph / DeepAgents graph with the parts that make LLM output usable in production: context engineering, tool isolation, durable memory, human-in-the-loop approval, sandbox execution, trajectory observability, and an evolution control plane.

This wiki is written for **developers and integrators**. It explains how the runtime is assembled, where each responsibility lives in the code, and how to extend it without breaking its boundaries.

> The `docs/` directory remains the canonical design source and is written in Chinese. This wiki is an English synthesis for developers; when a design detail matters, follow the link to the original document.

## Start Here

| If you want to... | Read |
| :-- | :-- |
| Run the stack locally | [[Getting-Started]] |
| Understand the runtime in one page | [[Architecture]] |
| Know how the main agent graph is assembled | [[Agent-Runtime]] |
| Work on context, compaction, or token pressure | [[Context-Engineering]] |
| Understand sessions, checkpoints, memory, and workspaces | [[Sessions-Memory-and-Workspace]] |
| Add tools or connect MCP servers | [[Tools-and-MCP]] |
| Understand sandbox execution and hot-swap | [[Sandbox]] |
| Write subagents or use the agent team | [[Subagents-and-Agent-Team]] |
| Work on scheduling or task flows | [[Scheduling-and-Task-Flows]] |
| Connect a RAG backend | [[RAG-Integration]] |
| Work on verification or self-evolution | [[Verification-and-Evolution]] |
| Call the HTTP API | [[API-Reference]] |
| Embed Harness into a customer platform | [[Integration-Mode]] |
| Work on the web console or desktop app | [[Frontend]] |
| Deploy and operate it | [[Deployment-and-Operations]] |
| Extend the codebase | [[Development-Guide]] |
| Debug a failure | [[Troubleshooting-FAQ]] |

## Mental Model

```text
HTTP / SSE
   │
   ▼
FastAPI (src/api)
   │  AgentLoader: one cached graph per user
   ▼
DeepAgents graph (src/agent/main_agent.py)
   ├─ middleware: context, limits, trajectory, memory, verification
   ├─ tools: built-in + MCP + skills + scheduler + task flows
   └─ subagents: sync YAML + async Agent Protocol
   │
   ├─ CompositeBackend ──► project workspace / sandbox / Store memory
   ├─ PostgreSQL ─────────► checkpoints + long-term memory
   ├─ OpenSandbox ────────► isolated command execution
   └─ Agent Protocol ─────► asynchronous subagent runs
```

Three invariants to keep in mind:

1. **The API layer never builds a graph directly.** It asks `AgentLoader` for the graph cached for a `user_id`.
2. **The agent layer never connects to a database directly.** Reads and writes go through a backend; `CompositeBackend` routes `/memories/`, `/artifacts/`, and normal paths.
3. **Files persist; commands execute.** Project files are written through file tools, while every command, script, and build runs in the sandbox.

## Wiki Conventions

- Cross-page links use GitHub Wiki syntax (`[[Page-Name]]`). Push the contents of `docs/wiki/` to a GitHub Wiki repository to make them clickable.
- Source references are given as repository-relative paths in backticks, for example `src/agent/main_agent.py`.
- Environment variables use their literal names, for example `HARNESS_CONTEXT_COMPACT_TRIGGER`.
- "Current" means the state of the code in this repository, not a future design.

## Status Snapshot

| Area | Status |
| :-- | :-- |
| Chat, SSE, interrupt/resume | Available |
| HITL approval | Available |
| Sandbox + proxy hot-swap | Available |
| MCP client | Usable, evolving toward production lifecycle |
| Sync/async subagents, agent team | Available |
| Context compaction and evaluation | Available |
| Scheduling (tier / plan / checkpoint) | Partial; HTN method layer not implemented |
| Verification and evolution control plane | Partial; external tracing not wired |
| RAG adapter | Available |
