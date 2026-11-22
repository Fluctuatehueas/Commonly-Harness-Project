# Common Harness

> A business-agnostic, source-deliverable, embeddable general-purpose Agent runtime.
> Using Harness Engineering, it turns the probabilistic generation of LLMs into task execution that is **operable, recoverable, auditable, and continuously improvable**.

[中文](./README.md) | **English**

| Item | Content |
| :-- | :-- |
| Version | v1.0 (2026-11) |
| Core | Python 3.12+ · FastAPI · LangGraph · deepagents |
| Frontend | Vue 3 · TypeScript · Element Plus · Pinia · ECharts · Electron |
| Deployment | [Deployment Guide](./docs/项目部署说明书.docx) (Chinese) |
| Docs | [Design Index](./docs/core_design/README.md) · [API Docs](./docs/api/README.md) · [Usage Guides](./docs/for_use/) (Chinese) |
| Wiki | [Developer Wiki](./docs/wiki/Home.md) |

---

## Table of Contents

- [1. Overview](#1-overview)
- [2. Quick Start](#2-quick-start)
- [3. Frontend](#3-frontend)
- [4. Platform Integration Guidelines](#4-platform-integration-guidelines)
- [5. Documentation Index](#5-documentation-index)
- [6. Known Limitations](#6-known-limitations)

---

## 1. Overview

### 1.1 What It Is

Calling an LLM API directly gives you "one generation", not "one reliable execution". The gap between the two is what a Harness fills:

| Layer | Problem | Implementation in This Project |
| :-- | :-- | :-- |
| Context | What should the model see each turn? What if it is too long? | Context injection, progressive disclosure, tool-result offloading, auto summarization, proactive compaction |
| Tools | Where do tools come from, where do they run, how are they isolated? | MCP tools, built-in tools, subagent tools, OpenSandbox isolated execution |
| Orchestration | What comes first, when to delegate, when to stop | LangGraph main graph, `write_todos`, sync/async subagents, HITL approval |
| Memory | Where is the conversation, does it survive a restart? | PostgreSQL Checkpointer + Store, user preferences, project and session indexes |
| Observability | What happened in between, why did it fail? | Trajectory events, token usage, context pressure monitoring, scheduler checkpoints |
| Constraints | Wrong tool, privilege escalation, loops, sandbox crash | Call limits, path validation, HITL, sandbox proxy hot-swap, retry and degradation |

### 1.2 What It Is Not

| Not | Reason |
| :-- | :-- |
| Not a business system | No business tools, prompts, or subagents for external projects live in this repository |
| Not a RAG product or vector store | Knowledge, chunking, embeddings, and indexes belong to the customer's RAG backend; this project only adapts and orchestrates |
| Not an MCP Server | In integration mode the customer implements the MCP Server; this project is only an MCP Host / Client |
| Not an identity or permission center | Login, tenant, and roles are handled by the customer gateway; this project is only a permission enforcement point |
| Not a low-code platform | Orchestration is code, YAML, middleware, and tool registration; no drag-and-drop builder |

### 1.3 Core Capabilities

| Capability | Status | Description |
| :-- | :-- | :-- |
| Chat and SSE streaming | Available | `/chat`, `/chat/stream`, interrupt recovery, history, attachments |
| HITL interrupt and approval | Available | Supplementary info, tool approval, argument editing, rejection |
| Sandbox and hot-swap | Available | Proxy mode, per-user lifecycle, prewarm pool, degradation |
| Skill management | Available | Download, assign, update, delete, dual-side sync |
| MCP client | Usable, evolving | Configuration declarations, proxy tools, hot reload; production lifecycle in progress |
| Sync / async subagents | Available | YAML registration, isolated context, Agent Protocol async tasks |
| Context engineering | Available | Injection, offloading, summarization, compaction, evaluation, auto-tuning |
| Long-term memory | Available | User preferences written back to Store, isolated by `user_id` |
| Project workspace | Available | Project registry, file domain, artifact publishing |
| Trajectory and usage | Available | Trajectory events, Gantt view, context pressure, session tokens |
| Model hot update | Available | `/settings/model` writes `.env` and rebuilds the graph |
| Runtime readiness check | Available | Workbench first screen checks backend / LLM / sandbox and guides to settings |
| Agent team | Available | Main agent recruitment, per-agent prompt/model/MCP binding, team delegation |
| Message feedback and retry | Available | Like / dislike persistence, retry with feedback toggle |
| Compaction evaluation | Available | Pre/post compaction evaluation, LLM judge, online threshold tuning and auto-tuning |
| Session archiving | Available | Auto archive by days, restore and permanent delete |
| RAG access | Available | Provider registration, unified retrieval, citation normalization |
| Evolution control plane | Partial | Eval sets, judge, experience write-back, release and rollback; external trace not wired |
| Task scheduling (tier / plan / checkpoint) | Partial | Two-level planning, dependency waves, checkpoint scoring; HTN method layer not implemented, see [design doc §16](./docs/core_design/任务调度模块设计.md) |

### 1.4 Tech Stack and Repository Layout

```text
CommonProject/
├── main.py                 FastAPI entry (incl. Windows event loop policy)
├── langgraph.json          Agent Protocol graph declaration (async subagents)
├── requirements.txt        Python dependencies
├── .env.example            Environment variable template
├── src/
│   ├── api/                HTTP / SSE interface layer
│   ├── agent/              Main agent, subagents, middleware, context, memory
│   │   ├── agent_team/     Multi-agent team (recruit / members / delegation)
│   │   ├── backends/       Workspace and sandbox backends, skill sync
│   │   ├── context/        Context engineering and compaction evaluation
│   │   ├── evolution/      Eval sets, judge, experience write-back, release control
│   │   ├── mcp/            MCP client and configuration
│   │   ├── middlewares/    Tool and runtime middleware
│   │   ├── runtime/        Runtime exception capture and supervision
│   │   ├── skills/         Skill loading
│   │   ├── subagents/      YAML subagents
│   │   ├── tools/          Built-in tools
│   │   └── verification/   Result verification
│   ├── mcp_server/         Built-in MCP sample server (for debugging and integration tests)
│   ├── rag/                RAG Provider adapters
│   ├── scheduler/          Task scheduling (tier / plan / checkpoint / replan)
│   ├── task_flow/          Task flows
│   ├── sql/                SQL query definitions
│   └── utils/              Logging, integrity, model web service
├── frontend/
│   ├── HarnessControlPlatform/   Web console (Vue 3 + Vite)
│   └── Desktop/HarnessDesktop/   Electron desktop app
├── sandbox/                Sandbox image and OpenSandbox configuration
├── mcp/                    MCP server configuration (servers.json, generated at runtime)
├── rag/                    RAG Provider configuration (providers.json, generated at runtime)
├── docs/                   Design, development, API, usage, operations docs
├── test/ · tests/          Unit and integration tests
└── logs/ · .harness/       Runtime logs and user data (generated at runtime)
```

### 1.5 Two Operating Modes

| Dimension | Personal Mode | Integration Mode (Embedded) |
| :-- | :-- | :-- |
| Deployment | User's local machine | Customer server / intranet |
| Workspace | Real local project directory | Server-managed workspace, isolated by `user_id` |
| Business capability source | Local tools, skills, MCP, sandbox | MCP Server provided by the customer's backend |
| Identity | Frontend demo user (`X-User-Id`) | Trusted identity injected by the customer gateway |
| Data boundary | Data stays local | Business data stays with the customer; this service stores only sessions, memory, and trajectories |

---

## 2. Quick Start

> This section is "fastest way to run locally". Production deployment, Nginx, systemd, backup, and rollback are covered in the [Deployment Guide](./docs/项目部署说明书.docx) (Chinese).

### 2.1 Prerequisites

| Dependency | Requirement |
| :-- | :-- |
| Python | 3.12+ (verified with 3.14) |
| Node.js | `^22.18.0` or `>=24.12.0` (frontend `package.json` engines; verified with 24.x) |
| PostgreSQL | 13+ (16 recommended) |
| Docker | For building images; used by PostgreSQL and the sandbox image |
| OpenSandbox Server | Reachable address + API key |
| Model service | DeepSeek-compatible endpoint |

### 2.2 Five-Step Startup

**1. Create a virtual environment and install dependencies**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

On Linux / macOS use `source .venv/bin/activate`.

**2. Prepare configuration**

```powershell
Copy-Item .env.example .env
```

At minimum, fill in these six values; the rest can keep defaults:

```dotenv
DEEPSEEK_API_KEY=sk-xxx
DB_HOST=127.0.0.1
DB_PASSWORD=your-db-password
OPEN_SANDBOX_HOST=127.0.0.1
OPEN_SANDBOX_PORT=18083
OPEN_SANDBOX_API_KEY=sk-harness-local-7c1a9e4b2d8f6a35
```

**3. Start PostgreSQL**

```powershell
docker run -d --name harness-postgres `
  -e POSTGRES_USER=harness -e POSTGRES_PASSWORD=123456 -e POSTGRES_DB=harness_db `
  -p 5432:5432 -v harness_pgdata:/var/lib/postgresql/data postgres:16
```

Set `DB_USER=harness` and `DB_NAME=harness_db` accordingly. Tables are created automatically by each module on first use; no manual DDL is needed.

**4. Deploy the sandbox service (Linux host)**

The sandbox image must be built from the Dockerfile on the host, so copy the three files over first (they are not synchronized with dependencies):

```bash
# Dev machine (repository root)
tar czf sandbox-deploy.tar.gz sandbox/Dockerfile sandbox/setup-runtime.sh sandbox/sandbox.toml
scp sandbox-deploy.tar.gz root@<linux-host-ip>:/opt/opensandbox/

# Linux host
mkdir -p /opt/opensandbox && cd /opt/opensandbox
tar xzf sandbox-deploy.tar.gz && mv sandbox/sandbox.toml sandbox.toml
chmod +x sandbox/setup-runtime.sh
sed -i 's/\r$//' sandbox/setup-runtime.sh        # CRLF from Windows must be converted
sed -i '1s/^\xEF\xBB\xBF//' sandbox.toml          # strip BOM

DOCKER_BUILDKIT=1 docker build -t myagent-sandbox:1 sandbox
uv tool install 'opensandbox-server==0.1.14'
opensandbox-server --config /opt/opensandbox/sandbox.toml
```

Directory relationship: `sandbox/Dockerfile` references `setup-runtime.sh` via `COPY`, so both must be in the same build context; `sandbox.toml` goes one level above `sandbox/`.

The `api_key` in `sandbox/sandbox.toml` must match `OPEN_SANDBOX_API_KEY` in `.env`, and `port` must match `OPEN_SANDBOX_PORT`.

**Open the sandbox port (firewall / security group)**

When accessing the sandbox remotely, if the Linux host firewall or cloud security group does not allow `OPEN_SANDBOX_PORT`, the typical symptom is **connection timeout**, not 401. The default port is `18083`; if your environment uses another port such as `18031`, replace it in the commands below.

```bash
# 1. Check which firewall is active
systemctl status firewalld
ufw status
iptables -L -n

# 2. firewalld (CentOS / RHEL / Rocky)
sudo firewall-cmd --permanent --add-port=18083/tcp
sudo firewall-cmd --reload
sudo firewall-cmd --list-ports

# 3. ufw (Ubuntu / Debian)
sudo ufw allow 18083/tcp comment 'opensandbox'
sudo ufw reload
sudo ufw status

# 4. iptables (when neither of the above is used; rules are not persistent by default)
sudo iptables -I INPUT -p tcp --dport 18083 -j ACCEPT
sudo iptables -L INPUT -n --line-numbers
# Persist (choose one):
sudo netfilter-persistent save                 # Debian / Ubuntu
sudo service iptables save                     # CentOS / RHEL (requires iptables-services)
```

After opening the port, verify the listening address and connectivity:

```bash
# Linux host: must be 0.0.0.0:18083, not 127.0.0.1:18083
ss -lntp | grep 18083

# Cross-machine check
curl http://<linux-host-ip>:18083/health
```

```powershell
# On Windows
Test-NetConnection <linux-host-ip> -Port 18083
```

Two easily missed points: `server.host` in `sandbox.toml` must be `0.0.0.0`; cloud hosts also need the same port opened in the console security group. Do not mix `firewalld`, `ufw`, and `iptables`; use only the one actually active on the system.

**5. Start the backend**

```powershell
.\.venv\Scripts\python.exe main.py
```

### 2.3 Verification

| Check | Command | Expected |
| :-- | :-- | :-- |
| Liveness probe | `curl http://127.0.0.1:8000/health` | `{"status":"ok",...}` |
| API docs | Open `http://127.0.0.1:8000/docs` | Swagger UI loads |
| Sandbox reachable | `curl http://127.0.0.1:18083/health` | 200 |
| Readiness | `curl http://127.0.0.1:8000/settings/readiness` | `configured` for `llm` / `sandbox` matches actual configuration |
| One chat turn | `POST /chat` with `{"message":"hello","user_id":"alice"}` | Model answer returned |

### 2.4 Common Issues

| Symptom | Cause | Fix |
| :-- | :-- | :-- |
| `error while attempting to bind on address` | Port already in use | Change `HARNESS_API_PORT` or stop the occupying process |
| Log says "user data directory is not writable" | Default directory lacks write permission | Set `HARNESS_USER_DATA_DIR` to a writable path |
| Backend fails to start; frontend shows "backend unreachable" | PostgreSQL unreachable or DB connection missing in `.env` | Check `DB_HOST` / `DB_USER` / `DB_PASSWORD`, then start the database |
| Workbench shows "LLM not configured" | `DEEPSEEK_API_KEY` is empty in `.env` | Click "Configure model" and save in system settings (hot update) |
| Chat returns `DEEPSEEK_API_KEY not configured` | Backend started but key missing at call time | Fill in the key and save, or restart the backend |
| Sandbox creation fails | Server not running / API key mismatch / clock skew | Check port 18083, compare keys, sync time |
| Frontend requests blocked by browser | CORS not allowed | Add the frontend origin to `HARNESS_CORS_ORIGINS` |

---

## 3. Frontend

### 3.1 Two Frontends

| Form | Directory | Stack | Use Case |
| :-- | :-- | :-- | :-- |
| Web console | `frontend/HarnessControlPlatform` | Vue 3 + Vite + Element Plus | Browser access, server deployment, embeddable portal |
| Desktop app | `frontend/Desktop/HarnessDesktop` | Electron + Vue 3 | Launch and supervise the backend locally; UI still converging to the web console |

The web console is the fully featured entry point. The desktop app reuses the same backend APIs and mainly handles launching `main.py`, window hosting, and process lifecycle.

### 3.2 Start and Build

```bash
# Web console
cd frontend/HarnessControlPlatform
npm install
npm run dev            # development, default 5173
npm run build          # output in dist/

# Desktop app
cd frontend/Desktop/HarnessDesktop
npm install
npm run dev            # electron-vite development
npm run build:win      # Windows installer; also build:mac / build:linux
```

The frontend requests `http://127.0.0.1:8000` by default. If the backend is not same-origin, override it:

```bash
# frontend/HarnessControlPlatform/.env.production
VITE_API_BASE_URL=https://harness.example.com
```

### 3.3 Page Guide

| Page | Purpose | Key Actions |
| :-- | :-- | :-- |
| Workbench | Landing page | Orbital module navigation; runs readiness checks (backend / LLM / sandbox) on first entry and guides to settings when something is missing |
| Chat | Main entry, SSE streaming | New session, attachments, resume after interrupt, approval cards, trajectory drill-down |
| Trajectory | Execution record | Per-turn thinking, tool arguments, results, latency, tokens |
| My Task Flows | Reusable execution flows | Copy built-in flows, visual editing, JSON import/export, publish, replay |
| Plugin Market | Plugin and tool package entry | Placeholder page; one-click install of skills, MCP, and task flows planned |
| Skills | Capability packages with `SKILL.md` as entry | Download, assign, enable/disable, update |
| MCP Services | External tool integration | Add service, test connectivity, enable/disable, view tool list |
| Knowledge Base | RAG Provider management | Register provider, unified retrieval, view citations |
| MyAgent | Main agent management | Recruit agents, edit prompts, bind MCP, choose model per agent |
| System Settings | Model and environment | Global model (writes `.env`), thinking / web search toggles, sandbox parameters, context thresholds, session archiving |

### 3.4 Multi-User and Isolation

The frontend identifies users via the `X-User-Id` header; the backend uses it to isolate memory, sandbox, workspace, and session indexes. Four demo users are built in and can be changed in [identity.ts](./frontend/HarnessControlPlatform/src/api/identity.ts):

`console_user` (default), `alice`, `bob`, `carol`.

For real integration, replace "read user from localStorage" with "read user from gateway-injected identity"; the rest of the logic stays the same.

### 3.5 Frontend Deployment

The build output is static files and can be served by Nginx:

```nginx
server {
    listen 80;
    server_name harness.example.com;

    root /srv/harness-web;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;   # SPA route fallback
    }
}
```

Three things to watch: point `VITE_API_BASE_URL` at the real backend; include the frontend origin in the backend `HARNESS_CORS_ORIGINS`; disable proxy buffering for SSE (see Deployment Guide 5.6).

---

## 4. Platform Integration Guidelines

### 4.1 Draw the Boundary First

| Role | Responsible For | Not Responsible For |
| :-- | :-- | :-- |
| Customer business backend | Business data, business APIs, RAG service, MCP Server, business permissions | Agent orchestration, context, sandbox |
| Customer gateway | Login, tenant identification, roles, request signing, audit entry | Agent runtime |
| Harness | Agent graph, context, memory, HITL, sandbox, MCP Host, RAG adaptation, trajectory | Business data, business rules, vector storage |

In one sentence: **the customer provides "MCP services + subagent YAML + skills", and Harness organizes them into reliable execution.**

### 4.2 Recommended Four Phases

Do not integrate all business capabilities at once. Proceed in this order, with acceptance signals at each phase:

| Phase | What to Do | Acceptance Signal |
| :-- | :-- | :-- |
| 1. Connect the chain | Gateway injects identity; run one chat turn + one read-only MCP tool | Client-supplied identity is ignored; tool call appears in the trajectory |
| 2. Read-only business | Integrate 3–5 read-only tools (query, retrieval) with good descriptions and error codes | Model selects the right tool; error codes classified via `category`/`retryable` |
| 3. Controlled writes | Integrate write operations, all supporting `idempotency_key`; destructive operations go through HITL | Repeated calls return the same result; delete-like operations require human confirmation |
| 4. Knowledge and observability | Integrate RAG (`rag_manifest` + `rag_retrieve`), build eval sets and regressions | Answers carry citations; the same question reproduces reliably on the eval set |

### 4.3 MCP Contract Essentials

Transport: JSON-RPC 2.0, Streamable HTTP preferred; entry point `POST /mcp`, health check `GET /health`; authentication only in headers.

Minimum required set:

- Protocol methods: `initialize`, `tools/list`, `tools/call`, `ping`
- Business tools: at least 1
- Additional requirements for knowledge bases: `rag_manifest` + `rag_retrieve`

Tool results must be structurally consumable:

```json
{
  "content": [{"type": "text", "text": "text for humans and models"}],
  "structuredContent": {"customer_id": "C1001"},
  "isError": false
}
```

Errors must be unified; otherwise callers cannot distinguish "retry helps" from "retry is useless":

```json
{"code":"NOT_FOUND","message":"customer not found","category":"business","retryable":false,"details":{}}
```

`category` is one of `tool|business|security|runtime`; `retryable` must be truthful, and callers retry only when it is `true`.

Three hard rules:

1. **Idempotency**: tools with side effects must accept `idempotency_key`; repeated calls with the same key return the same result, retained for 24h recommended.
2. **Timeout**: 15s to connect, 120s per tool call; stop execution on cancellation, and any side effects must be queryable and compensable.
3. **Description is the contract**: `description` should state "purpose / when to use / side effects"; `inputSchema` should constrain arguments with `additionalProperties: false`. If the model picks the wrong tool, the description is usually the problem, not the model.

See [MCP Service Integration API](./docs/api/MCP服务接入API.md) (Chinese) for full fields, examples, and a self-test checklist.

### 4.4 RAG Integration

Choose one of two approaches; the data structures are identical:

| Approach | Method | Suitable For |
| :-- | :-- | :-- |
| MCP tool (recommended) | Expose `rag_manifest` / `rag_retrieve` on the same MCP service | Existing MCP Server |
| HTTP endpoint | Provide `/retrieve`, `/knowledge/*` | Existing standalone retrieval service |

`rag_manifest` is for discovery and must be placed in `content[].text`:

```json
{
  "provider_id": "rag_crm",
  "name": "CRM Knowledge Base",
  "protocol": "mcp",
  "capabilities": ["retrieve", "ingest", "health"],
  "knowledge_domains": ["contract", "product"],
  "citation_format": "chunk",
  "limits": {"top_k_max": 20, "max_chars_per_chunk": 2000, "qps": 10}
}
```

`rag_retrieve` output must include citable sources:

```json
{
  "chunks": [
    {
      "citation_id": "c1",
      "content": "Contract renewal must be initiated 30 days before expiry...",
      "score": 0.82,
      "source": {"title": "Contract Management Policy", "uri": "kb://contract/001", "page": 3}
    }
  ],
  "provider_id": "rag_crm"
}
```

Recommendations: keep each `content` under 8KB, truncate with `truncated: true` if longer; return `chunks: []` for no results instead of an error; `user_scope` (tenant, user, roles) must be passed through to retrieval, with permission filtering done in the RAG backend, not by the model.

### 4.5 Identity and Isolation

- The gateway must **strip** client-supplied identity headers before injecting trusted `tenant_id` / `user_id` / `roles`.
- Isolation keys: `tenant_id` (deployment), `user_id` (memory/sandbox/workspace), `thread_id` (session), `project_id` (workspace).
- Workspaces persist files only; **commands run only in the sandbox**. In integration mode, project roots must stay under the managed workspace root; attaching arbitrary server paths is forbidden.
- Tools with side effects go through HITL by default: approve, edit arguments, and reject must all be expressible in the UI.

### 4.6 Integration Acceptance Checklist

| # | Check | Pass Criteria |
| :-- | :-- | :-- |
| 1 | Identity injection | Forged client `X-User-Id` is ineffective; unauthorized requests return 403 |
| 2 | MCP connectivity | Zero connections on startup, connect on first call; one failing service does not affect other tools |
| 3 | Tool contract | Every tool has a description, constrained schema, unified error codes, and `retryable` |
| 4 | Idempotency and approval | Same-key repeated writes return consistent results; destructive operations require confirmation |
| 5 | RAG citations | Citations trace back to document and page; empty results do not error |
| 6 | Isolation | Two users cannot see each other's sessions, memory, or workspace files |
| 7 | Observability | A failure can be traced to tool, arguments, and root cause in the trajectory |
| 8 | Recovery | After interrupt or restart, execution resumes from checkpoint without repeating completed side effects |

---

## 5. Documentation Index

| Document | Content |
| :-- | :-- |
| [Deployment Guide](./docs/项目部署说明书.docx) | Linux production deployment, systemd, Nginx, backup and upgrade, troubleshooting, full environment variable list |
| [Developer Wiki](./docs/wiki/Home.md) | Architecture, module guides, extension points, API reference, integration and operations |
| [Project Description](./docs/core_design/项目描述.md) | Product positioning, two modes, integration topology, roadmap |
| [API Docs](./docs/api/README.md) | Business main-chain API, management and configuration API, MCP service integration API |
| [End-to-End Integration Example and Pitfall Guide](./docs/for_use/接入全流程示例与避坑指南.md) | From zero to streaming chat + tools + HITL + async tasks |
| [How to Write a SubAgent](./docs/for_use/如何编写SubAgent.md) | Subagent YAML structure and registration |
| [Extending Skills and Tools](./docs/for_use/拓展技能与工具的流程.md) | Skill packages and tool integration |
| [Agent Team Design](./docs/core_design/AgentTeam设计.md) · [Development Doc](./docs/develope/AgentTeam开发文档.md) | Main agent recruitment, team delegation, member isolation |
| [RAG Module Design](./docs/core_design/RAG模块设计.md) | Provider registration, retrieval, citation normalization |
| [Context Engineering Design](./docs/develope/上下文工程设计.md) | Injection, offloading, summarization, compaction, evaluation |
| [Task Scheduling Design](./docs/core_design/任务调度模块设计.md) | Tiering, planning, checkpoints, replanning, current status and TODOs |
| [Task Flow Design](./docs/core_design/任务流模块设计.md) | Compilation, execution, and takeover of task flows |
| [Feedback and Retry Design](./docs/core_design/消息反馈与重试设计.md) · [Archiving](./docs/core_design/归档功能.md) | Feedback persistence, retry chain, session archiving |
| [Runtime Exception Capture and Restart](./docs/develope/运行时异常捕获与重启机制设计开发.md) | Process supervision, exception recovery, restart strategy |
| [Architecture and Core Sequences](./docs/core_design/架构图与核心时序.md) | Component relationships and key flows |

> Documents under `docs/` are written in Chinese.

---

## 6. Known Limitations

1. **Session mutex works only in a single process**: multi-worker deployment requires additional design.
2. **External tracing and eval sets not wired**: built-in trajectory and evolution control plane (eval sets / judge / experience) are usable, while Langfuse / LangSmith / DeepEval remain roadmap items.
3. **MCP productionization incomplete**: lazy connection, circuit breaking, namespacing, and management UI are still in progress.
4. **Task scheduling has not reached HTN**: currently "two-level task table + dependency DAG + topological waves + checkpoint scoring"; the method layer and `(S,R,D)` loop are not implemented.
5. **Linux project path validation needs review**: current validation is based on Windows local paths; server scenarios need hardening per Deployment Guide 8.5.
6. **Readiness check only on frontend first screen**: checked once when the workbench is first entered; if the backend is completely unreachable, only troubleshooting hints are shown, with no online repair.

---

Author: GuiQiHueas · License: [MIT](./LICENSE)
