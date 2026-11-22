# Getting Started

This page gets a developer from a clean checkout to a working chat request with a sandbox.

## 1. Prerequisites

| Dependency | Requirement |
| :-- | :-- |
| Python | 3.12+ (verified with 3.14) |
| Node.js | `^22.18.0` or `>=24.12.0` |
| PostgreSQL | 13+ (16 recommended) |
| Docker | Builds the sandbox image and can host PostgreSQL |
| OpenSandbox Server | A reachable endpoint and API key |
| LLM endpoint | DeepSeek-compatible API |

Python dependencies are pinned in `requirements.txt`, including `langgraph==1.2.11`, `deepagents==0.7.13`, `fastapi==0.141.1`, `opensandbox==0.1.16`, and `langgraph-checkpoint-postgres==3.1.2`.

## 2. Backend

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

On Linux or macOS, activate with `source .venv/bin/activate`.

Fill in at least these values in `.env`:

```dotenv
DEEPSEEK_API_KEY=sk-xxx
DB_HOST=127.0.0.1
DB_PASSWORD=your-db-password
OPEN_SANDBOX_HOST=127.0.0.1
OPEN_SANDBOX_PORT=18083
OPEN_SANDBOX_API_KEY=sk-harness-local-7c1a9e4b2d8f6a35
```

Optional but commonly needed:

| Variable | Purpose |
| :-- | :-- |
| `DEEPSEEK_BASE_URL` | Model endpoint |
| `DEEPSEEK_MODEL_NAME` | Default model name |
| `DB_USER`, `DB_NAME` | PostgreSQL credentials |
| `HARNESS_CORS_ORIGINS` | Allowed browser origins |
| `HARNESS_WEB_SEARCH` | Enable web search tools |
| `HARNESS_CONTEXT_COMPACT_*` | Compaction thresholds; see [[Context-Engineering]] |

The full template, including host-tool, feedback, and scheduler flags, is in `.env.example`.

## 3. PostgreSQL

```powershell
docker run -d --name harness-postgres `
  -e POSTGRES_USER=harness -e POSTGRES_PASSWORD=123456 -e POSTGRES_DB=harness_db `
  -p 5432:5432 -v harness_pgdata:/var/lib/postgresql/data postgres:16
```

Set `DB_USER=harness` and `DB_NAME=harness_db`. Tables are created on first use; there is no migration step to run by hand.

PostgreSQL stores three different things, so do not treat it as a scratch database:

- LangGraph checkpoints, keyed by `thread_id`
- Long-term memory (Store), keyed by `user_id`
- Session index, feedback, trajectory, usage, scheduler runs, and related records

See [[Sessions-Memory-and-Workspace]] for the exact ownership.

## 4. Sandbox host

The sandbox image is built from `sandbox/Dockerfile` on a Linux host. Copy the build inputs first:

```bash
# On the dev machine, from the repository root
tar czf sandbox-deploy.tar.gz sandbox/Dockerfile sandbox/setup-runtime.sh sandbox/sandbox.toml
scp sandbox-deploy.tar.gz root@<linux-host>:/opt/opensandbox/
```

```bash
# On the Linux host
mkdir -p /opt/opensandbox && cd /opt/opensandbox
tar xzf sandbox-deploy.tar.gz && mv sandbox/sandbox.toml sandbox.toml
chmod +x sandbox/setup-runtime.sh
sed -i 's/\r$//' sandbox/setup-runtime.sh
sed -i '1s/^\xEF\xBB\xBF//' sandbox.toml

DOCKER_BUILDKIT=1 docker build -t myagent-sandbox:1 sandbox
uv tool install 'opensandbox-server==0.1.14'
opensandbox-server --config /opt/opensandbox/sandbox.toml
```

`api_key` in `sandbox.toml` must equal `OPEN_SANDBOX_API_KEY`; `port` must equal `OPEN_SANDBOX_PORT`; `server.host` must be `0.0.0.0` for remote access.

Open the port on the host firewall and the cloud security group. A blocked port usually looks like a timeout rather than a 401. Details are in [[Sandbox]] and [[Troubleshooting-FAQ]].

## 5. Run the backend

```powershell
.\.venv\Scripts\python.exe main.py
```

`main.py` does three things before starting Uvicorn:

1. Adds `src/` to `sys.path`.
2. On Windows, switches to `WindowsSelectorEventLoopPolicy` because the async psycopg driver does not support the default Proactor loop.
3. Configures structured logging (human-readable console + JSON Lines file + async queue).

## 6. Verify

| Check | Command | Expected |
| :-- | :-- | :-- |
| Liveness | `curl http://127.0.0.1:8000/health` | `{"status":"ok",...}` |
| Swagger | open `http://127.0.0.1:8000/docs` | UI loads |
| Readiness | `curl http://127.0.0.1:8000/settings/readiness` | `llm` and `sandbox` report the actual configured state |
| Sandbox | `curl http://<sandbox-host>:18083/health` | HTTP 200 |
| Chat | `POST /chat` with `{"message":"hello","user_id":"alice"}` | Model answer |

A minimal chat call:

```bash
curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -H "X-User-Id: alice" \
  -d '{"message":"hello"}'
```

## 7. Frontend

```bash
cd frontend/HarnessControlPlatform
npm install
npm run dev
```

The console defaults to `http://127.0.0.1:8000`. Set `VITE_API_BASE_URL` when the API is elsewhere.

The Electron shell lives in `frontend/Desktop/HarnessDesktop` and uses the same backend APIs.

## 8. First extension

A safe first extension path:

1. Add a read-only MCP server and confirm its tools appear in the chat trajectory. See [[Tools-and-MCP]].
2. Add a subagent YAML that only uses those read-only tools. See [[Subagents-and-Agent-Team]].
3. Add a skill package with a `SKILL.md` entry point. See [[Development-Guide]].
4. Only then integrate write operations, with idempotency keys and HITL approval.

## Next

- [[Architecture]] for the component map
- [[Agent-Runtime]] for graph assembly
- [[Troubleshooting-FAQ]] when the first run fails
