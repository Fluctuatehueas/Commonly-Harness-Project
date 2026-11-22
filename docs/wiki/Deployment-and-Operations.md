# Deployment and Operations

This page covers the backend and its dependencies. The canonical deployment guide is `docs/项目部署说明书.docx` (Chinese); this page is the developer-oriented summary.

## 1. Components

| Component | Required | Notes |
| :-- | :-- | :-- |
| Python backend | Yes | `main.py`, single process |
| PostgreSQL | Yes | Checkpoints, memory, sessions, trajectory, scheduler |
| OpenSandbox Server | Yes for execution | Linux host, Docker image |
| LLM endpoint | Yes | DeepSeek-compatible |
| Web console | Optional | Static files behind Nginx |
| Agent Protocol service | Optional | Async subagents |

## 2. Environment groups

The full template is `.env.example`. Grouped essentials:

**Model**

```dotenv
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL_NAME=deepseek-v4-flash
HARNESS_MODEL_THINKING=true
HARNESS_MODEL_CONTEXT_LIMIT=256000
```

**API**

```dotenv
HARNESS_API_HOST=0.0.0.0
HARNESS_API_PORT=8000
HARNESS_CORS_ORIGINS=https://harness.example.com
```

`main.py` defaults to `127.0.0.1`; change it explicitly for server exposure.

**PostgreSQL**

```dotenv
DB_HOST=
DB_PORT=5432
DB_NAME=
DB_USER=
DB_PASSWORD=
DB_SSLMODE=disable
```

**Sandbox**

```dotenv
OPEN_SANDBOX_HOST=
OPEN_SANDBOX_PORT=18083
OPEN_SANDBOX_API_KEY=
OPEN_SANDBOX_IMAGE=myagent-sandbox:1
OPEN_SANDBOX_DEFAULT_TIMEOUT_SECONDS=300
OPEN_SANDBOX_SANDBOX_TIMEOUT_SECONDS=86400
OPEN_SANDBOX_PREWARM_ENABLED=true
OPEN_SANDBOX_USE_SERVER_PROXY=true
```

**Storage**

```dotenv
HARNESS_USER_DATA_DIR=/var/lib/harness
```

**Feature switches**

`HARNESS_WEB_SEARCH`, `HARNESS_CONTEXT_COMPACT_*`, `HARNESS_FEEDBACK_ENABLED`, `HARNESS_PDCA_ENABLED`, `HARNESS_SCHEDULER_ENABLED`, `HARNESS_SCHEDULER_EXEC`, `HARNESS_VERIFICATION_*`, `HARNESS_EVOLUTION_*`.

## 3. PostgreSQL

```bash
docker run -d --name harness-postgres \
  -e POSTGRES_USER=harness -e POSTGRES_PASSWORD='<strong-password>' -e POSTGRES_DB=harness_db \
  -p 5432:5432 -v harness_pgdata:/var/lib/postgresql/data postgres:16
```

Do not use the development password in production. Restrict network access to the backend host.

## 4. Backend process

There is no separate startup script; `main.py` is the entry point.

```bash
/path/to/.venv/bin/python main.py
```

A minimal systemd unit:

```ini
[Unit]
Description=Common Harness API
After=network-online.target

[Service]
Type=simple
User=harness
WorkingDirectory=/opt/harness
EnvironmentFile=/opt/harness/.env
ExecStart=/opt/harness/.venv/bin/python main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Notes:

- Run as a dedicated non-root user.
- `main.py` itself is single-process. Do not enable multiple Uvicorn workers; session mutex and in-process caches assume one process.
- Logs are written to console and to `HARNESS_LOG_DIR` (default `./logs`) as JSON Lines.

## 5. Nginx

```nginx
server {
    listen 443 ssl;
    server_name harness.example.com;

    root /srv/harness-web;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }

    location /chat/stream {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_buffering off;
        proxy_read_timeout 3600s;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

The exact `location` blocks depend on whether the frontend and API share a domain. The critical detail is **`proxy_buffering off` for SSE**.

## 6. Logs

| Log | Location | Purpose |
| :-- | :-- | :-- |
| Application logs | `HARNESS_LOG_DIR` (default `logs/`) | Console-readable and JSON Lines |
| Sandbox logs | `/mcp/servers/{id}/logs` for MCP; OpenSandbox logs on host | Connectivity and execution |
| Trajectory | PostgreSQL and `/trajectory/{thread_id}` | Per-turn execution record |
| Scheduler events | `/scheduler/runs/{run_id}/events` | Scheduling progress |
| Audit | `<project>/.commonharness/audit.jsonl` | Project and publish operations |

`src/utils/logging_setup.py` uses an async queue so logging does not block the event loop. Flush on shutdown is handled in `main.py`; do not kill the process with `SIGKILL` if log completeness matters.

## 7. Backup

Back up both sides:

1. PostgreSQL dump: checkpoints, Store, sessions, trajectory, usage, scheduler, feedback, evolution
2. `HARNESS_USER_DATA_DIR`: settings, MCP/RAG configuration, skills, project registry
3. Project directories: actual user files and `.commonharness/audit.jsonl`

A checkpoint without the Store loses memory; a Store without the checkpoint loses conversation state. Restore them together.

## 8. Upgrade and rollback

Suggested procedure:

1. Snapshot the database and user data directory.
2. Stop the service.
3. Deploy the new code and install dependencies.
4. Start the service.
5. Check `/health`, `/settings/readiness`, and one chat turn.
6. Run a representative eval case.
7. Roll back code and restore the snapshot if step 5 or 6 fails.

Schema changes are created on first use by modules; still take a backup before upgrading, because "auto-create" does not mean "auto-migrate incompatible changes".

## 9. Security checklist

| Item | Expected |
| :-- | :-- |
| API exposure | Only chat/SSE exposed through the gateway; management endpoints intranet-only |
| Identity | Gateway strips client identity headers and injects trusted values |
| Secrets | `.env` readable only by the service user; never committed |
| PostgreSQL | Strong password; network restricted |
| Sandbox | API key matches; port restricted to the backend host or VPN |
| Sandbox image | Built from a reviewed Dockerfile; no host socket mount |
| Host tools | Disabled unless required; every call goes through HITL |
| CORS | Exact frontend origins, not `*` |
| Logs | No secrets, tokens, or full business payloads |
| Backups | Encrypted and access-controlled |

## 10. Scaling constraints

- **Single process only.** Session mutex and some caches are in-process. Multi-worker deployment needs a distributed lock and a redesign of per-user graph caching.
- **One sandbox per user.** Concurrency is bounded by sandbox resources and prewarm settings.
- **Model concurrency.** `HARNESS_MODEL_REQUESTS_PER_SECOND` and the concurrency middleware limit parallel model calls.
- **Scheduler `max_parallel` defaults to 1.** Raising it requires validating tool and sandbox concurrency.
- **MCP servers are remote dependencies.** One slow server can slow the tools it owns; it should not block unrelated tools.

## 11. Health and readiness

| Check | Meaning |
| :-- | :-- |
| `GET /health` | Process is alive |
| `GET /settings/readiness` | LLM and sandbox configured as expected |
| `GET /settings/sandbox/status` | Sandbox reachability |
| `POST /settings/sandbox/check` | Probe host and port |
| `GET /mcp/runtime` | MCP runtime state |
| `GET /evolution/metrics` | Evolution metrics |

Readiness is a configuration check, not a guarantee that every dependency is healthy. Pair it with an external monitor that performs one real chat turn.

## 12. Incident response

1. Check `/health` and `/settings/readiness`.
2. Read the application log; look for the first error, not the last.
3. Check PostgreSQL connectivity and disk space.
4. Check sandbox health and port reachability.
5. Check the failing thread's trajectory to identify the tool and arguments.
6. If the sandbox is at fault, restart the sandbox service; the proxy should recover on the next call.
7. If a compaction or scheduler change caused the incident, roll back the configuration or code, then reproduce on an eval case.

## Next

- [[Getting-Started]] for local setup
- [[Sandbox]] for sandbox specifics
- [[Troubleshooting-FAQ]] for symptom-level guidance
