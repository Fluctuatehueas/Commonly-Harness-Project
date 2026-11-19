# Common Harness

> 业务无关、可源码交付、可嵌入既有后端系统的通用 Agent 运行时。
> 用 Harness Engineering 的方法，把大模型的概率性生成能力约束成**可操作、可恢复、可审计、可持续改进**的任务执行能力。

| 项目 | 内容 |
| :-- | :-- |
| 版本 | v1.0（2026-11） |
| 内核 | Python 3.12+ · FastAPI · LangGraph · deepagents |
| 前端 | Vue 3 · TypeScript · Element Plus · Pinia · ECharts · Electron |
| 部署 | [项目部署说明书](./docs/项目部署说明书.docx) |
| 文档 | [设计文档索引](./docs/core_design/README.md) · [接口文档](./docs/api/README.md) · [使用指南](./docs/for_use/) |

---

## 目录

- [1. 项目简述](#1-项目简述)
- [2. 快速使用](#2-快速使用)
- [3. 前端使用说明](#3-前端使用说明)
- [4. 平台接入开发建议](#4-平台接入开发建议)
- [5. 文档索引](#5-文档索引)
- [6. 已知限制](#6-已知限制)

---

## 1. 项目简述

### 1.1 它是什么

直接调用大模型 API 只能得到「一次生成」，得不到「一次可靠执行」。两者之间的差距，就是 Harness 要补的部分：

| 层 | 解决的问题 | 本项目的实现 |
| :-- | :-- | :-- |
| 上下文 | 每轮该看到什么、太长怎么办 | 上下文注入、渐进式披露、工具结果卸载、自动摘要、主动压缩 |
| 工具 | 工具从哪来、在哪执行、怎么隔离 | MCP 工具、内置工具、子 Agent 工具、OpenSandbox 隔离执行 |
| 编排 | 先做什么、何时委派、何时停 | LangGraph 主图、`write_todos`、同步/异步子 Agent、HITL 审批 |
| 记忆 | 会话到哪了、重启后还在不在 | PostgreSQL Checkpointer + Store、用户偏好、项目与会话索引 |
| 观测 | 中间做了什么、为什么失败 | 轨迹事件、Token 统计、上下文占用监控、调度检查点 |
| 约束 | 调错工具、越权、循环、沙箱挂掉 | 调用上限、路径校验、HITL、沙箱代理热替换、重试与降级 |

### 1.2 它不是什么

| 不是 | 原因 |
| :-- | :-- |
| 不是行业业务系统 | 仓库内不编写任何外部项目的业务工具、业务提示词或业务子 Agent |
| 不是 RAG 产品或向量库 | 知识、切片、嵌入、索引归甲方 RAG 后端；本项目只做适配与编排 |
| 不是 MCP Server | 集成模式下由甲方实现 MCP Server，本项目只做 MCP Host / Client |
| 不是账号与权限中心 | 登录、租户、角色由甲方网关负责；本项目只做权限执行点 |
| 不是低代码平台 | 编排以代码、YAML、中间件与工具注册为主，不提供拖拽式编排 |

### 1.3 核心能力

| 能力 | 状态 | 说明 |
| :-- | :-- | :-- |
| 对话与 SSE 流式 | 已具备 | `/chat`、`/chat/stream`、中断恢复、历史、附件 |
| HITL 中断与审批 | 已具备 | 补充信息、工具审批、参数编辑、拒绝 |
| 沙箱与热替换 | 已具备 | 代理模式、按用户生命周期、预热池、降级 |
| 技能管理 | 已具备 | 下载、分配、更新、删除、双端同步 |
| MCP 客户端 | 可用待完善 | 配置声明、代理工具、热更新；生产化生命周期在演进中 |
| 同步 / 异步子 Agent | 已具备 | YAML 注册、独立上下文、Agent Protocol 异步任务 |
| 上下文工程 | 已具备 | 注入、卸载、摘要、压缩、评估与自动调参 |
| 长期记忆 | 已具备 | 用户偏好写回 Store，按 `user_id` 隔离 |
| 项目工作区 | 已具备 | 项目登记、文件域、产物发布 |
| 轨迹与用量 | 已具备 | 轨迹事件、甘特图、上下文占用、会话 Token |
| 模型热更新 | 已具备 | `/settings/model` 写 `.env` 并重建图 |
| 运行自检 | 已具备 | 工作台首屏检测后端 / LLM / 沙箱，缺失时全息弹窗引导到系统设置 |
| Agent 团队 | 已具备 | 主 Agent 招募、独立提示词 / 模型 / MCP 绑定、团队委派 |
| 消息反馈与重试 | 已具备 | 赞同 / 不赞同落库、失败重试与反馈开关 |
| 上下文压缩评估 | 已具备 | 压缩前后评估、LLM 裁判、阈值在线调整与自动调参 |
| 会话归档 | 已具备 | 按天数自动归档，支持恢复与永久删除 |
| RAG 接入 | 已具备 | Provider 注册、统一检索与引用归一化 |
| 演进控制面 | 部分具备 | 评估集、判官、经验写回、发布与回滚；外部 Trace 未接入 |
| 任务调度（分档 / 计划 / 检查点） | 部分具备 | 两级计划、依赖波次、检查点打分；HTN 方法层未落地，见[设计文档 §16](./docs/core_design/任务调度模块设计.md) |

### 1.4 技术栈与目录结构

```text
CommonProject/
├── main.py                 FastAPI 入口（含 Windows 事件循环策略）
├── langgraph.json          Agent Protocol 图声明（异步子 Agent）
├── requirements.txt        Python 依赖
├── .env.example            环境变量模板
├── src/
│   ├── api/                HTTP / SSE 接口层
│   ├── agent/              主 Agent、子 Agent、中间件、上下文、记忆
│   │   ├── agent_team/     多 Agent 团队（招募 / 成员 / 委派）
│   │   ├── backends/       工作区与沙箱后端、技能同步
│   │   ├── context/        上下文工程与压缩评估
│   │   ├── evolution/      评估集、判官、经验写回与发布控制面
│   │   ├── mcp/            MCP 客户端与配置
│   │   ├── middlewares/    工具与运行时中间件
│   │   ├── runtime/        运行时异常捕获与守护
│   │   ├── skills/         技能加载
│   │   ├── subagents/      YAML 子 Agent
│   │   ├── tools/          内置工具
│   │   └── verification/   结果校验
│   ├── mcp_server/         内置 MCP 示例服务端（调试与联调用）
│   ├── rag/                RAG Provider 适配
│   ├── scheduler/          任务调度（分档 / 计划 / 检查点 / 重规划）
│   ├── task_flow/          任务流
│   ├── sql/                SQL 查询定义
│   └── utils/              日志、完整性、模型 Web 服务
├── frontend/
│   ├── HarnessControlPlatform/   Web 控制台（Vue 3 + Vite）
│   └── Desktop/HarnessDesktop/   Electron 桌面端
├── sandbox/                沙箱镜像与 OpenSandbox 配置
├── mcp/                    MCP 服务配置（servers.json，运行时生成）
├── rag/                    RAG Provider 配置（providers.json，运行时生成）
├── docs/                   设计、开发、接口、使用、运维文档
├── test/ · tests/          单元与集成测试
└── logs/ · .harness/       运行日志与用户数据（运行时生成）
```

### 1.5 两种工作模式

| 维度 | 个人模式 | 集成模式（Embedded） |
| :-- | :-- | :-- |
| 部署位置 | 用户本机 | 甲方服务器 / 内网 |
| 工作区 | 本机真实项目目录 | 服务端受管工作区，按 `user_id` 隔离 |
| 业务能力来源 | 本地工具、技能、MCP、沙箱 | 甲方后端提供的 MCP Server |
| 身份 | 前端演示用户 `X-User-Id` | 甲方网关注入可信身份 |
| 数据边界 | 数据留在本机 | 业务数据留在甲方；本服务只存会话、记忆、轨迹 |

---

## 2. 快速使用

> 这一节是「本机最快跑起来」。生产部署、Nginx、systemd、备份与回滚见 [项目部署说明书](./docs/项目部署说明书.docx)。

### 2.1 前置条件

| 依赖 | 要求 |
| :-- | :-- |
| Python | 3.12+（仓库验证 3.14） |
| Node.js | `^22.18.0` 或 `>=24.12.0`（前端 package.json engines；仓库验证 24.x） |
| PostgreSQL | 13+（推荐 16） |
| Docker | 可构建镜像；用于 PostgreSQL 与沙箱镜像 |
| OpenSandbox Server | 可访问地址 + API Key |
| 模型服务 | DeepSeek 兼容接口 |

### 2.2 五步启动

**① 创建虚拟环境并装依赖**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Linux / macOS 激活命令为 `source .venv/bin/activate`。

**② 准备配置**

```powershell
Copy-Item .env.example .env
```

至少填写这 6 项，其余可先用默认值：

```dotenv
DEEPSEEK_API_KEY=sk-xxx
DB_HOST=127.0.0.1
DB_PASSWORD=你的数据库密码
OPEN_SANDBOX_HOST=127.0.0.1
OPEN_SANDBOX_PORT=18083
OPEN_SANDBOX_API_KEY=sk-harness-local-7c1a9e4b2d8f6a35
```

**③ 启动 PostgreSQL**

```powershell
docker run -d --name harness-postgres `
  -e POSTGRES_USER=harness -e POSTGRES_PASSWORD=123456 -e POSTGRES_DB=harness_db `
  -p 5432:5432 -v harness_pgdata:/var/lib/postgresql/data postgres:16
```

相应地设置 `DB_USER=harness`、`DB_NAME=harness_db`。表结构由各模块在首次使用时自动创建，不需要手工建表。

**④ 部署沙箱服务（Linux 宿主机）**

沙箱镜像必须由宿主机上的 Dockerfile 构建，所以先把三个文件拷过去（它们不会随依赖自动同步）：

```bash
# 开发机（仓库根目录）
tar czf sandbox-deploy.tar.gz sandbox/Dockerfile sandbox/setup-runtime.sh sandbox/sandbox.toml
scp sandbox-deploy.tar.gz root@<Linux主机IP>:/opt/opensandbox/

# Linux 宿主机
mkdir -p /opt/opensandbox && cd /opt/opensandbox
tar xzf sandbox-deploy.tar.gz && mv sandbox/sandbox.toml sandbox.toml
chmod +x sandbox/setup-runtime.sh
sed -i 's/\r$//' sandbox/setup-runtime.sh        # Windows 拷贝过来是 CRLF，必须转换
sed -i '1s/^\xEF\xBB\xBF//' sandbox.toml          # 清除 BOM

DOCKER_BUILDKIT=1 docker build -t myagent-sandbox:1 sandbox
uv tool install 'opensandbox-server==0.1.14'
opensandbox-server --config /opt/opensandbox/sandbox.toml
```

目录关系：`sandbox/Dockerfile` 通过 `COPY setup-runtime.sh` 引用同目录脚本，二者必须放在同一个构建上下文里；`sandbox.toml` 放在 `sandbox/` 的上一层。

`sandbox/sandbox.toml` 中的 `api_key` 必须与 `.env` 的 `OPEN_SANDBOX_API_KEY` 一致，`port` 必须与 `OPEN_SANDBOX_PORT` 一致。

**开放沙箱端口（防火墙 / 安全组）**

远程访问沙箱时，如果 Linux 主机防火墙或云安全组未放行 `OPEN_SANDBOX_PORT`，典型现象是**连接超时**而不是 401。默认端口为 `18083`；如果你的环境改成 `18031` 等其他端口，把下面命令里的端口同步替换即可。

```bash
# 1. 先确认当前启用的防火墙
systemctl status firewalld
ufw status
iptables -L -n

# 2. firewalld（CentOS / RHEL / Rocky）
sudo firewall-cmd --permanent --add-port=18083/tcp
sudo firewall-cmd --reload
sudo firewall-cmd --list-ports

# 3. ufw（Ubuntu / Debian）
sudo ufw allow 18083/tcp comment 'opensandbox'
sudo ufw reload
sudo ufw status

# 4. iptables（前两者都未使用时；规则默认不持久）
sudo iptables -I INPUT -p tcp --dport 18083 -j ACCEPT
sudo iptables -L INPUT -n --line-numbers
# 持久化（按系统二选一）：
sudo netfilter-persistent save                 # Debian / Ubuntu
sudo service iptables save                     # CentOS / RHEL（需 iptables-services）
```

放行后验证监听地址与连通性：

```bash
# Linux 主机：必须是 0.0.0.0:18083，不能是 127.0.0.1:18083
ss -lntp | grep 18083

# 跨机验证
curl http://<Linux主机IP>:18083/health
```

```powershell
# Windows 侧
Test-NetConnection <Linux主机IP> -Port 18083
```

两个容易漏的点：`sandbox.toml` 的 `server.host` 必须是 `0.0.0.0`；云主机还要在控制台安全组放行同一端口。`firewalld`、`ufw`、`iptables` 不要混用，只选当前系统实际启用的那一套。

**⑤ 启动后端**

```powershell
.\.venv\Scripts\python.exe main.py
```

### 2.3 验证

| 检查项 | 命令 | 期望 |
| :-- | :-- | :-- |
| 存活探针 | `curl http://127.0.0.1:8000/health` | `{"status":"ok",...}` |
| 接口文档 | 浏览器打开 `http://127.0.0.1:8000/docs` | Swagger 页面可加载 |
| 沙箱可达 | `curl http://127.0.0.1:18083/health` | 200 |
| 运行自检 | `curl http://127.0.0.1:8000/settings/readiness` | `llm` / `sandbox` 的 `configured` 与实际配置一致 |
| 一条对话 | `POST /chat`，body `{"message":"你好","user_id":"alice"}` | 返回模型回答 |

### 2.4 常见问题

| 现象 | 原因 | 处理 |
| :-- | :-- | :-- |
| `error while attempting to bind on address` | 端口被占用 | 换 `HARNESS_API_PORT`，或停止占用进程 |
| 日志出现「用户数据目录不可写」 | 默认目录无写权限 | 设置 `HARNESS_USER_DATA_DIR` 到可写路径 |
| 后端启动失败、前端显示「后端服务不可达」 | PostgreSQL 不可达或 `.env` 未配置数据库连接 | 先确认 `DB_HOST` / `DB_USER` / `DB_PASSWORD`，再启动数据库 |
| 工作台弹出「大模型尚未配置」 | `.env` 的 `DEEPSEEK_API_KEY` 为空 | 点击「前往配置模型」在系统设置中填写并保存（热更新） |
| 对话报 `未配置 DEEPSEEK_API_KEY` | 后端能启动，但模型调用前校验失败 | 填写 Key 后保存或重启后端 |
| 沙箱创建失败 | 服务端未启动 / API Key 不一致 / 时间不同步 | 检查 18083、比对 Key、同步时间 |
| 前端请求被浏览器拦截 | 跨域未放行 | 把前端地址加入 `HARNESS_CORS_ORIGINS` |

---

## 3. 前端使用说明

### 3.1 两个前端

| 形态 | 目录 | 技术栈 | 适用 |
| :-- | :-- | :-- | :-- |
| Web 控制台 | `frontend/HarnessControlPlatform` | Vue 3 + Vite + Element Plus | 浏览器访问、服务端部署、可嵌入门户 |
| 桌面端 | `frontend/Desktop/HarnessDesktop` | Electron + Vue 3 | 本机启动与守护后端；界面仍在向 Web 控制台收敛 |

Web 控制台是当前功能完整的入口。桌面端复用同一套后端接口，主要负责拉起 `main.py`、托管窗口与进程生命周期。

### 3.2 启动与构建

```bash
# Web 控制台
cd frontend/HarnessControlPlatform
npm install
npm run dev            # 开发，默认 5173
npm run build           # 产物在 dist/

# 桌面端
cd frontend/Desktop/HarnessDesktop
npm install
npm run dev             # electron-vite 开发
npm run build:win       # 打包 Windows 安装包，另有 build:mac / build:linux
```

前端默认请求 `http://127.0.0.1:8000`。后端不在同源时，用环境变量覆盖：

```bash
# frontend/HarnessControlPlatform/.env.production
VITE_API_BASE_URL=https://harness.example.com
```

### 3.3 页面导览

| 页面 | 用途 | 关键操作 |
| :-- | :-- | :-- |
| 工作台 | 首屏入口 | 轨道式模块导航；首次进入自动做运行自检（后端 / LLM / 沙箱），缺失时弹出全息提示并跳转系统设置 |
| 对话 | 主入口，SSE 流式输出 | 新建会话、附件上传、中断后继续、审批卡片、轨迹下钻 |
| 轨迹 | 执行过程记录 | 按轮查看思考、工具参数、结果、耗时、Token |
| 我的任务流 | 可复用的执行流程 | 复制内置流、可视化编辑、发布、回放 |
| 插件市场 | 插件与工具包入口 | 当前为占位页，后续接入技能、MCP 与任务流的一键安装 |
| 技能 | `SKILL.md` 为入口的能力包 | 下载、分配、启停、更新 |
| MCP 服务 | 外部工具接入 | 新增服务、测试连通、启停、查看工具清单 |
| 知识库 | RAG Provider 管理 | 注册 Provider、统一检索、查看引用 |
| MyAgent | 主 Agent 管理 | 招募 Agent、编辑提示词、绑定 MCP、为每个 Agent 选择模型 |
| 系统设置 | 模型与环境 | 全局模型（写 `.env`）、思考 / 联网开关、沙箱参数、上下文阈值、会话归档 |

### 3.4 多用户与隔离

前端通过 `X-User-Id` 头识别用户，后端据此隔离记忆、沙箱、工作区、会话索引。内置 4 个演示用户，可在 [identity.ts](./frontend/HarnessControlPlatform/src/api/identity.ts) 中增删：

`console_user`（默认）、`alice`、`bob`、`carol`。

真实接入时把「从 localStorage 读用户」换成「从网关注入的身份读用户」即可，其余逻辑不变。

### 3.5 前端部署

构建产物是纯静态文件，放到 Nginx 即可：

```nginx
server {
    listen 80;
    server_name harness.example.com;

    root /srv/harness-web;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;   # 前端路由回退
    }
}
```

注意三件事：`VITE_API_BASE_URL` 指向真实后端；后端 `HARNESS_CORS_ORIGINS` 包含前端域名；SSE 需要关闭代理缓冲（见部署说明书 5.6）。

---

## 4. 平台接入开发建议

### 4.1 先划清边界

| 角色 | 负责 | 不负责 |
| :-- | :-- | :-- |
| 甲方业务后端 | 业务数据、业务 API、RAG 服务、MCP Server、业务权限 | Agent 编排、上下文、沙箱 |
| 甲方网关 | 登录、租户识别、角色、请求签名、审计入口 | Agent 运行时 |
| Harness | Agent 图、上下文、记忆、HITL、沙箱、MCP Host、RAG 适配、轨迹 | 业务数据、业务规则、向量存储 |

一句话：**甲方提供「MCP 服务 + 子 Agent YAML + 技能」，Harness 负责把它们组织成可靠执行。**

### 4.2 建议的四阶段

不建议一次性把业务全量接进来。按下面顺序推进，每阶段都有可验收信号：

| 阶段 | 做什么 | 验收信号 |
| :-- | :-- | :-- |
| ① 打通链路 | 网关注入身份，跑通一轮对话 + 一个只读 MCP 工具 | 客户端自报身份被忽略；工具调用出现在轨迹里 |
| ② 只读业务 | 接入 3–5 个只读工具（查询、检索），配好工具描述与错误码 | 模型能选对工具；错误码可通过 `category`/`retryable` 分类处理 |
| ③ 受控写入 | 接入写操作，全部支持 `idempotency_key`，破坏性操作走 HITL | 重复调用结果一致；删除类操作必须人工确认 |
| ④ 知识与观测 | 接 RAG（`rag_manifest` + `rag_retrieve`），建立评估集与回归 | 回答带引用；同一问题在评估集上稳定复现 |

### 4.3 MCP 契约要点

传输：JSON-RPC 2.0，Streamable HTTP 优先；入口 `POST /mcp`，健康检查 `GET /health`；鉴权只放 Header。

最小必选集合：

- 协议方法：`initialize`、`tools/list`、`tools/call`、`ping`
- 业务工具：至少 1 个
- 做知识库时额外要求：`rag_manifest` + `rag_retrieve`

工具结果必须可被结构化消费：

```json
{
  "content": [{"type": "text", "text": "给人与模型看的文本"}],
  "structuredContent": {"customer_id": "C1001"},
  "isError": false
}
```

错误必须统一，否则无法区分「重试有用」和「重试也没用」：

```json
{"code":"NOT_FOUND","message":"客户不存在","category":"business","retryable":false,"details":{}}
```

`category` 取 `tool|business|security|runtime`；`retryable` 必须如实填写，调用方只对 `true` 重试。

三条硬约定：

1. **幂等**：有副作用的工具必须接受 `idempotency_key`，同键重复调用返回同一结果，建议保留 24h。
2. **超时**：连接 15s，单次工具 120s；收到取消通知后停止执行，已产生的副作用必须可查询、可补偿。
3. **描述即契约**：`description` 写清「用途 / 何时使用 / 副作用」，`inputSchema` 用 `additionalProperties: false` 收敛参数。模型选错工具，八成是描述没写清，不是模型不行。

完整字段、示例与自测清单见 [MCP 服务接入 API](./docs/api/MCP服务接入API.md)。

### 4.4 RAG 接入

两种方式二选一，数据结构完全一致：

| 方式 | 做法 | 适用 |
| :-- | :-- | :-- |
| MCP 工具（推荐） | 在同一 MCP 服务上暴露 `rag_manifest` / `rag_retrieve` | 已有 MCP Server |
| HTTP 接口 | 提供 `/retrieve`、`/knowledge/*` | 已有独立检索服务 |

`rag_manifest` 供发现，必须把 JSON 放进 `content[].text`：

```json
{
  "provider_id": "rag_crm",
  "name": "CRM 知识库",
  "protocol": "mcp",
  "capabilities": ["retrieve", "ingest", "health"],
  "knowledge_domains": ["contract", "product"],
  "citation_format": "chunk",
  "limits": {"top_k_max": 20, "max_chars_per_chunk": 2000, "qps": 10}
}
```

`rag_retrieve` 出参要带可引用的来源：

```json
{
  "chunks": [
    {
      "citation_id": "c1",
      "content": "合同续签需在到期前 30 天发起……",
      "score": 0.82,
      "source": {"title": "合同管理办法", "uri": "kb://contract/001", "page": 3}
    }
  ],
  "provider_id": "rag_crm"
}
```

建议：`content` 单条不超过 8KB，超长截断并标 `truncated: true`；无结果返回 `chunks: []`，不要报错；`user_scope`（租户、用户、角色）必须透传到检索侧，权限过滤在 RAG 后端做，不要交给模型。

### 4.5 身份与隔离

- 网关必须先**剥离**客户端自带的身份头，再注入可信的 `tenant_id` / `user_id` / `roles`。
- 隔离键：`tenant_id`（部署级）、`user_id`（记忆/沙箱/工作区）、`thread_id`（会话）、`project_id`（工作区）。
- 工作区只做持久文件，**命令只在沙箱执行**；集成模式下项目根必须限制在受管工作区根内，禁止 attach 任意服务器路径。
- 有副作用的工具默认走 HITL：批准、编辑参数、拒绝三种结果都要在前端可表达。

### 4.6 接入验收清单

| # | 检查项 | 通过标准 |
| :-- | :-- | :-- |
| 1 | 身份注入 | 客户端伪造 `X-User-Id` 无效；越权请求返回 403 |
| 2 | MCP 连通 | 启动零连接，首次调用才建连；单服务故障不影响其他工具 |
| 3 | 工具契约 | 每个工具有描述、约束 schema、统一错误码与 `retryable` |
| 4 | 幂等与审批 | 同键重复写入结果一致；破坏性操作必须人工确认 |
| 5 | RAG 引用 | 回答里的引用可回溯到文档与页码；无结果不报错 |
| 6 | 隔离 | 两个用户互相看不到对方的会话、记忆、工作区文件 |
| 7 | 可观测 | 一次失败能在轨迹里定位到工具、参数、错误原因 |
| 8 | 恢复 | 中断、重启后可以从检查点继续，不重复已完成的副作用 |

---

## 5. 文档索引

| 文档 | 内容 |
| :-- | :-- |
| [项目部署说明书](./docs/项目部署说明书.docx) | Linux 生产部署、systemd、Nginx、备份升级、故障排查、环境变量总表 |
| [项目描述](./docs/core_design/项目描述.md) | 产品定位、两种模式、接入拓扑、演进路线 |
| [接口文档](./docs/api/README.md) | 业务主链路 API、管理与配置 API、MCP 服务接入 API |
| [接入全流程示例与避坑指南](./docs/for_use/接入全流程示例与避坑指南.md) | 从零跑到流式对话 + 工具 + HITL + 异步任务 |
| [如何编写 SubAgent](./docs/for_use/如何编写SubAgent.md) | 子 Agent YAML 结构与注册 |
| [拓展技能与工具的流程](./docs/for_use/拓展技能与工具的流程.md) | 技能包与工具接入 |
| [Agent 团队设计](./docs/core_design/AgentTeam设计.md) · [开发文档](./docs/develope/AgentTeam开发文档.md) | 主 Agent 招募、团队委派与成员隔离 |
| [RAG 模块设计](./docs/core_design/RAG模块设计.md) | Provider 注册、检索与引用归一化 |
| [上下文工程设计](./docs/develope/上下文工程设计.md) | 注入、卸载、摘要、压缩与评估 |
| [任务调度模块设计](./docs/core_design/任务调度模块设计.md) | 分档、计划、检查点、重规划，含实现现状与待办 |
| [任务流模块设计](./docs/core_design/任务流模块设计.md) | 任务流的编译、执行与接管 |
| [消息反馈与重试设计](./docs/core_design/消息反馈与重试设计.md) · [归档功能](./docs/core_design/归档功能.md) | 反馈落库、重试链路、会话归档 |
| [运行时异常捕获与重启机制](./docs/develope/运行时异常捕获与重启机制设计开发.md) | 进程守护、异常恢复与重启策略 |
| [架构图与核心时序](./docs/core_design/架构图与核心时序.md) | 组件关系与关键链路 |

---

## 6. 已知限制

1. **会话互斥仅单进程有效**：多 worker 部署需要额外设计。
2. **外部 Trace 与评估集未接入**：内置轨迹与演进控制面（评估集 / 判官 / 经验）已可用，Langfuse / LangSmith / DeepEval 仍为演进项。
3. **MCP 生产化未完成**：懒连接、熔断、命名空间、管理 UI 仍在推进。
4. **任务调度未达 HTN**：当前是「两级任务表 + 依赖 DAG + 拓扑波次 + 检查点打分」，方法层与 `(S,R,D)` 闭环未落地。
5. **Linux 项目路径校验需复核**：当前校验以 Windows 本机为基线，服务端场景需按部署说明书 8.5 加固。
6. **运行自检只在前端首屏**：工作台首次进入时检测一次；后端完全不可达时只能给出排障提示，无法在线修复配置。

---

Author: GuiQiHueas · License: 内部交付，未经许可请勿外传
