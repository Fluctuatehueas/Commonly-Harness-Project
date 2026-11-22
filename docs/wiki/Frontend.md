# Frontend

There are two frontends that share one backend API.

| Form | Directory | Stack | Role |
| :-- | :-- | :-- | :-- |
| Web console | `frontend/HarnessControlPlatform` | Vue 3, Vite, TypeScript, Element Plus, Pinia, ECharts | Full-featured entry point |
| Desktop app | `frontend/Desktop/HarnessDesktop` | Electron, Vue 3, TypeScript | Launches and supervises the local backend |

The web console is the primary development target. The desktop app reuses the same APIs and focuses on process lifecycle and window hosting.

## 1. Web console

### Scripts

```bash
cd frontend/HarnessControlPlatform
npm install
npm run dev          # Vite dev server, default 5173
npm run build        # type-check + production build
npm run preview      # preview the build
npm run lint         # oxlint + eslint
npm run format       # prettier
```

Node engine: `^22.18.0` or `>=24.12.0`.

### Key dependencies

| Package | Use |
| :-- | :-- |
| `element-plus` | Component library |
| `pinia` | State management |
| `vue-router` | Routing |
| `echarts` | Charts |
| `marked`, `dompurify`, `highlight.js` | Markdown rendering and sanitization |
| `mermaid` | Diagrams |
| `katex` | Math rendering |

### API base URL

Default: `http://127.0.0.1:8000`.

Override with Vite env:

```bash
# .env.production
VITE_API_BASE_URL=https://harness.example.com
```

The backend must include the frontend origin in `HARNESS_CORS_ORIGINS` when they are not same-origin.

### User identity

`src/api/identity.ts` holds the demo user list:

```text
console_user (default), alice, bob, carol
```

The selected user is sent as `X-User-Id` and isolates memory, sandbox, workspace, and sessions.

For real integration, replace the localStorage lookup with the gateway-injected identity. Do not add authentication logic to Harness; it belongs to the gateway.

## 2. Pages

| Page | Purpose | Notable behavior |
| :-- | :-- | :-- |
| Workbench | Landing page | Orbital module navigation; first-entry readiness check for backend, LLM, and sandbox; guides to settings when missing |
| Chat | Main interaction | SSE streaming, attachments, interrupt resume, approval cards, trajectory drill-down |
| Trajectory | Execution record | Thinking, tool arguments, results, latency, tokens per turn |
| My Task Flows | Reusable flows | Copy built-ins, visual editing, JSON import/export, publish, replay |
| Plugin Market | Plugin entry | Placeholder for one-click skills, MCP, and flow installation |
| Skills | Capability packages | Download, assign, enable/disable, update |
| MCP Services | External tools | Add server, probe, enable/disable, inspect tools |
| Knowledge Base | RAG providers | Register, retrieve, inspect citations |
| MyAgent | Agent profiles | Recruit, edit prompts, bind MCP, choose model per agent |
| System Settings | Runtime configuration | Global model, thinking/search toggles, sandbox, context thresholds, archiving |

## 3. Streaming behavior

Chat uses SSE. The frontend must:

- Render token events incrementally
- Render tool call cards from tool events
- Render interrupt cards for HITL and submit `resume`
- Recover after a dropped connection by re-reading history

If a reverse proxy buffers SSE, tokens arrive in bursts or only at the end. Disable buffering for the chat route.

## 4. Build and deploy

```bash
npm run build
```

The output is static files under `dist/`. Nginx example:

```nginx
server {
    listen 80;
    server_name harness.example.com;

    root /srv/harness-web;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

Checklist:

1. `VITE_API_BASE_URL` points to the real API.
2. Backend CORS includes the frontend origin.
3. SSE routes have proxy buffering disabled.
4. Gzip or Brotli is enabled for static assets.
5. Cache headers are long for hashed assets and short for `index.html`.

## 5. Desktop app

```bash
cd frontend/Desktop/HarnessDesktop
npm install
npm run dev
npm run build:win      # build:mac / build:linux also available
```

The desktop app:

- Starts and manages the local backend process
- Hosts the UI in an Electron window
- Reuses backend APIs

It is not a separate runtime. If behavior differs between desktop and web, the web console is authoritative.

## 6. UI conventions for agent output

Some rendering rules are part of the runtime contract:

- **THINK blocks** are collapsible. Intermediate blocks should be grouped into one collapsible unit, collapsed by default, with a placeholder.
- **Charts** are produced as self-contained HTML files and rendered as file cards. Do not expect the model to inline ECharts JSON.
- **Tool cards** show arguments and results in a structured form, not raw JSON only.
- **Interrupt cards** must express all HITL outcomes: approve, edit arguments, reject, provide information.
- **Execution state** should distinguish running, waiting for approval, failed, and completed.

These are not cosmetic. They determine whether a user can audit and correct an agent run.

## 7. Frontend development rules

- Do not call the model directly from the frontend; all model access goes through the backend.
- Do not store secrets in frontend code or localStorage.
- Keep identity resolution in one place (`src/api/identity.ts`) so integration replacement is trivial.
- Sanitize rendered markdown; the backend may return model-generated HTML.
- Treat SSE events as an append-only log; do not assume a single final payload.
- When adding a page, add its route, navigation entry, and API module together.

## Next

- [[API-Reference]] for endpoints
- [[Getting-Started]] for the backend
- [[Deployment-and-Operations]] for Nginx and systemd
