# Sandbox

The sandbox is the execution boundary. Project files persist; commands, scripts, builds, and tests run in an isolated OpenSandbox instance.

Code:

| File | Responsibility |
| :-- | :-- |
| `src/agent/backends/sandbox_manager.py` | Per-user sandbox lifecycle |
| `src/agent/backends/sandbox_proxy.py` | Stable reference and hot replacement |
| `src/agent/backends/open_sandbox.py` | OpenSandbox SDK adapter |
| `src/agent/backends/sandbox_files_backend.py` | File operations inside the sandbox |
| `src/agent/backends/project_backend.py` | Project file routing and command delegation |
| `src/agent/backends/skill_sync.py` | Incremental skill synchronization |
| `sandbox/` | Dockerfile, runtime setup, server configuration |

## 1. Why a sandbox

An agent that can run commands is an agent that can delete files, install packages, exfiltrate data, or hang. Isolation gives four guarantees:

1. Commands do not execute on the host serving the API.
2. A crashed or corrupted environment can be replaced without rebuilding the graph.
3. Per-user resources are separated.
4. Resource limits and TTLs are enforceable.

## 2. Components

```text
Graph
  │ holds
  ▼
SandboxBackendProxy          stable object for the graph's lifetime
  │ delegates to
  ▼
SandboxManager               creates, finds, and replaces instances per user
  │ uses
  ▼
OpenSandboxBackend           SDK adapter
  │ talks to
  ▼
OpenSandbox Server           remote service + image
```

The proxy is the reason a sandbox can die without killing the conversation. Tools and middleware hold the proxy, not the concrete instance.

## 3. Server setup

On a Linux host:

```bash
DOCKER_BUILDKIT=1 docker build -t myagent-sandbox:1 sandbox
uv tool install 'opensandbox-server==0.1.14'
opensandbox-server --config /opt/opensandbox/sandbox.toml
```

Configuration must agree across three places:

| Setting | In `sandbox.toml` | In `.env` |
| :-- | :-- | :-- |
| Port | `port` | `OPEN_SANDBOX_PORT` |
| API key | `api_key` | `OPEN_SANDBOX_API_KEY` |
| Listening host | `server.host = 0.0.0.0` for remote | Host reachable from the API server |

If the port is blocked by a firewall or cloud security group, the symptom is usually a timeout, not an authentication error. Open the port for `firewalld`, `ufw`, or `iptables`—whichever is actually active—and verify with `ss -lntp` and `curl /health`.

## 4. Environment variables

| Variable | Default | Meaning |
| :-- | :-- | :-- |
| `OPEN_SANDBOX_HOST` | `127.0.0.1` | Sandbox server host |
| `OPEN_SANDBOX_PORT` | `18083` | Sandbox server port |
| `OPEN_SANDBOX_API_KEY` | — | API key |
| `OPEN_SANDBOX_IMAGE` | `myagent-sandbox:1` | Image used for instances |
| `OPEN_SANDBOX_DEFAULT_TIMEOUT_SECONDS` | `300` in `.env.example` | Per-command timeout |
| `OPEN_SANDBOX_SANDBOX_TIMEOUT_SECONDS` | `86400` | Instance TTL |
| `OPEN_SANDBOX_PREWARM_ENABLED` | `true` | Prewarm instances |
| `OPEN_SANDBOX_USE_SERVER_PROXY` | `true` | Required for Windows, Docker Desktop, and remote servers |

Prewarming trades memory for latency. A prewarmed instance reduces the first-command delay but holds resources even when idle; tune it against actual concurrency.

## 5. Lifecycle

1. The first request from a `user_id` triggers sandbox acquisition.
2. `SandboxManager` returns an existing instance or creates one. Prewarming may satisfy the request immediately.
3. `SandboxBackendProxy` holds the instance reference for the graph.
4. Commands execute with a timeout. Long-running commands must be chunked or moved to async tasks.
5. On failure, the proxy replaces the instance. The graph, checkpoint, and memory remain valid.
6. After `OPEN_SANDBOX_SANDBOX_TIMEOUT_SECONDS`, the instance is reclaimed.

Important: sandbox files are not durable storage. Anything that must survive should be published to the project or written to memory.

## 6. File vs command boundary

| Operation | Path | Executes? |
| :-- | :-- | :-- |
| Read/write project files | `ProjectWorkspaceBackend` | No |
| List/read sandbox files | `SandboxFilesBackend` | No |
| Run command | `run_in_sandbox` / `compile_project` | Yes |
| Publish artifact | `publish_to_project` | Copies from sandbox to project |
| Host fallback | `host_execute` | Yes, only if enabled, always HITL |

The system prompt states this boundary, and the backend enforces it. A project file is never executed in place; it must be staged into the sandbox.

## 7. Skill synchronization

When a sandbox exists, `SandboxSkillSynchronizer` incrementally copies:

- Host-maintained skill directories
- The fixed `AGENTS.md` policy file

Synchronization runs through `SandboxSkillsMiddleware` before DeepAgents reads skill metadata, so the current run sees the latest copy. `create_skill_management_tools` receives the same synchronizer, so a skill installed at runtime is available to the same session.

Without a sandbox, skills are mounted read-only from the host under `/skills/`; agents can read them but cannot modify `SKILL.md`.

## 8. Settings and diagnostics

| Method | Path | Purpose |
| :-- | :-- | :-- |
| `GET` | `/settings/sandbox` | Read sandbox settings |
| `PUT` | `/settings/sandbox` | Update sandbox settings |
| `GET` | `/settings/sandbox/status` | Current status |
| `POST` | `/settings/sandbox/check` | Probe host and port |
| `GET` | `/settings/readiness` | Overall readiness including sandbox |

When the workbench opens, it calls readiness once and guides the user to settings if the sandbox is missing. If the API itself is unreachable, the frontend can only show troubleshooting hints.

## 9. Failure modes and responses

| Failure | Symptom | Response |
| :-- | :-- | :-- |
| Server not running | Creation fails or readiness reports missing | Start `opensandbox-server`; check the port |
| API key mismatch | Authentication error | Compare `sandbox.toml` and `.env` |
| Clock skew | Token or certificate validation fails | Synchronize time |
| Firewall blocks port | Timeout, not 401 | Open the port; verify `0.0.0.0` listening |
| Instance dies mid-run | Tool error, then proxy replacement | Retry the step; checkpoint preserves state |
| Command exceeds timeout | Timeout result | Chunk the work, raise the timeout deliberately, or use async tasks |
| Sandbox files expected later | Files missing after TTL | Publish artifacts before the instance is reclaimed |

## 10. Security rules

- Do not mount host Docker sockets or sensitive directories into the sandbox.
- Do not execute project files on the host because "it is faster".
- Keep host tools disabled unless there is a clear operational need; every call must go through HITL.
- Treat sandbox network access as untrusted; restrict egress where possible.
- Never log secrets from sandbox environment variables.

## Next

- [[Sessions-Memory-and-Workspace]] for what survives the sandbox
- [[Tools-and-MCP]] for tools that call the sandbox
- [[Deployment-and-Operations]] for host hardening
