|能力|验证方法|已验证|
|:---|:---|:-:|
|沙箱内部执行||✅|
|文件操作||✅|
|网络操作||✅|
|控制沙箱内的运行时环境|Agent自行安装了C#的mono编译器（mcs）详见[验证](./模型在沙箱内自行控制运行时环境.png)|✅|
|自写SKILL|让Agent写一个C#相关的SKILL|✅|

---

# 疑惑解答

先给结论速查：

| 问题 | 一句话结论 |
|:---|:---|
| 沙箱会一直存在吗 | 不会。默认 TTL 24h，每次访问续期；过期/失效即被服务端回收 |
| 一个会话一个沙箱吗 | 不是。粒度是 **user_id**，同一用户的多个会话共用一个沙箱 |
| 沙箱可以复用吗 | 可以：进程内代理复用、跨重启按 sandbox_id 重连、预热池转交给新用户 |
| 沙箱文件存哪 | 远端 Linux 容器文件系统里，宿主没有副本，沙箱销毁即一起消失 |
| MCP 用连接池吗 | 是。按「transport + connection 签名」复用**活会话**，空闲 5 分钟才回收 |

---

## 1. 构建好的沙箱会一直存在吗？

**不会。**

- 创建时带过期时间：`_create_backend()` 传 `timeout=timedelta(seconds=OPEN_SANDBOX_SANDBOX_TIMEOUT_SECONDS)`，默认 **86400 秒（24 小时）**（[config.py](../src/agent/config.py) 的 `OPEN_SANDBOX_SANDBOX_TIMEOUT_SECONDS`）。
- 每次 `get_backend(user_id)` 都会续期：`_renew_on_access()` → `renew_sandbox(...)`，所以**只要还在用就一直活着**，连续 24h 不访问才会到期。
- 后端重启**不会**销毁沙箱：`SandboxManager.close()` 只做「停止接纳新请求、排空任务、销毁未分配的预热实例、关闭本地客户端」，代码注释明确写了「不销毁远端用户沙箱」。
- 只有确认失效才新建：`_is_confirmed_gone()` 判定 `TERMINATED` / `FAILED` / 查询 404 才允许换新沙箱；健康检查失败、超时、503 都只保留原沙箱。

参考：[sandbox_manager.py](../src/agent/backends/sandbox_manager.py)（`_create_backend` / `_renew_on_access` / `_is_confirmed_gone` / `close`）。

## 2. 一个会话一个沙箱吗？

**不是，粒度是用户（user_id），不是会话（thread_id）。**

- `SandboxManager.get_backend(user_id)`，缓存键是 `self._proxies[user_id]`，持久化键是 Store 的 `sandboxes/<user_id>/current`。
- 同一用户的多个会话共用同一个沙箱、同一个 Agent 实例；只有 checkpoint 按 thread 独立。这个约定写在 [agent_loader.py](../src/api/agent_loader.py) 的类注释里：「同一 `user_id` 的不同 `thread_id` 共用 Agent，但 checkpoint 相互独立」。
- 具体调用点：[agent_loader.py](../src/api/agent_loader.py#L182)（每次聊天取沙箱）、[files.py](../src/api/files.py#L143)（文件面板取沙箱）。
- 后果：**文件、已安装的运行时、/skills 都是用户级共享**，不同会话之间会串。

## 3. 沙箱可以复用吗？

**可以，一共四层复用：**

| 层次 | 机制 | 位置 |
|:---|:---|:---|
| 进程内 | `_proxies[user_id]` 缓存稳定代理；健康就续期直接返回 | `sandbox_manager._get_backend` |
| 后端失效 | `SandboxBackendProxy.replace_backend()` 原子替换内部后端，**不重建 Agent 图** | `sandbox_proxy.py` |
| 跨进程 / 重启 | Store 里存 `sandbox_id`，`SandboxSync.connect(id)` 重新接回原沙箱 | `_resolve_backend` / `_connect_existing` |
| 新用户 | 一个**预热实例**（已同步好技能和 AGENTS.md），原子摘除后立刻补一个新的 | `_prewarm` / `_create_reserve` |

不能复用的部分：
- 跨用户不共享（预热实例一旦分配给某用户就归属该用户）。
- 预热池只有 **1 个**实例，多个新用户同时首次访问会退化成串行新建。

## 4. 沙箱内部文件存在哪？沙箱销毁后谁来删？

**在远端 Linux 容器的文件系统里，宿主机上不保留副本。**

- **怎么读写**：全部走 OpenSandbox SDK 的三个原语——
  - `SandboxSync.commands.run()` 执行命令（[open_sandbox.py](../src/agent/backends/open_sandbox.py#L64)）
  - `SandboxSync.files.write_files()` 上传（`upload_files`）
  - `SandboxSync.files.read_bytes()` 下载（`download_files`）
  DeepAgents 的 `BaseSandbox` 其余文件操作都是从这三个原语派生，模型拿不到宿主机文件系统。
- **技能与 AGENTS.md**：`SandboxSkillSynchronizer` 以宿主技能目录为权威源，用沙箱内的 `/.myagent/skills-manifest.json` 做增量比对，把文件推到沙箱的 `/skills` 和 `/AGENTS.md`（[skill_sync.py](../src/agent/backends/skill_sync.py)）。
- **会话文件**：有沙箱时，`GET /threads/{id}/files` 直接在沙箱里执行遍历脚本（返回 `source: sandbox`）；无沙箱时才回落到宿主机会话目录（[files.py](../src/api/files.py#L203)）。
- **生命周期**：
  - 沙箱活着（24h TTL，且访问续期）→ 文件在；
  - 服务端判定过期 / `TERMINATED` / `FAILED` → 容器被删，**文件一起消失**；
  - 后端重启 → 不影响（按 `sandbox_id` 重连同一个容器）；
  - 清理宿主 `.harness/`、`.mcp/` 也不会碰沙箱文件。
- **要长期保留**只能主动拉回宿主（`download_files`，即文件面板的下载）。

## 5. 现在沙箱服务有什么弊端？

1. **文件不持久**：24h TTL + 无快照/备份。长期不聊，工作区直接没了；沙箱内装的运行时（比如验证表里的 mono 编译器）也一起没了，下次要重装。
2. **隔离粒度是用户不是会话**：同一用户多个会话共用文件系统和进程环境，容易互相污染。而且 [sandbox_proxy.py](../src/agent/backends/sandbox_proxy.py) 的 `execute` / `upload_files` / `download_files` 都持同一把 `RLock`，同一用户的并发会话实际是**串行排队**执行，吞吐受限于单用户串行。
3. **每个活跃用户常驻一个容器（最长 24h）**：空转也占服务端资源；预热池只有一个，多用户冷启动串行。
4. **每次取沙箱都有远程探测开销**：`get_backend` 每请求都做 `is_healthy()` + `renew_sandbox()`，链路里多了两次 HTTP/gRPC 往返，会抬高首 token 延迟。
5. **部署约束在进程内**：`_proxies`、`_user_locks`、预热池都在单进程内存里；[agent_loader.py](../src/api/agent_loader.py) 注释明确写「只支持单 worker 部署，多 worker 前必须加分布式协调」。
6. **依赖链路较长**：Windows 上容器 IP 不可直连，`OPEN_SANDBOX_USE_SERVER_PROXY` 默认开，实际是「客户端 → 生命周期服务端 → 沙箱」，故障面更多（服务端 503 / 代理超时都表现为「沙箱暂时不可用」）。
7. **执行时长受限**：默认 `OPEN_SANDBOX_DEFAULT_TIMEOUT_SECONDS=120s`，长任务必须显式传 `timeout`。
8. **默认不是网络隔离沙箱**：验证表已确认容器内可联网，因此提示注入导致的外泄风险要靠上层策略（HITL、工具白名单）兜底。
9. **缺少容器级指标**：当前只记录执行结果、退出码和耗时（`ExecuteResponse`），没有采集容器 CPU/内存/磁盘使用。

## 6. MCP 服务现在是用连接池来避免频繁连接开销吗？

**是。** 实现在 [client_registry.py](../src/agent/mcp/client_registry.py) 的 `MCPClientRegistry`：

- 池键 `pool_key = transport + sha256(connection)`（`ServerConfig.pool_key`），只要 `command/args/env/url/headers` 不变就命中同一个键。
- 空闲回收：`idle_ttl_ms = 300000`（5 分钟，来自 `mcp/servers.json` 的 `defaults.pool`），由 `_reap_loop` 周期回收。
- 上限：`max_pool_size = 32`，超限时按 `last_used` 淘汰引用计数为 0 的连接。
- 单飞：同一 `pool_key` 的并发建连只会执行一次（`_key_lock`）。

省下来的不是「一次 socket 连接」这么轻的开销：

- **stdio 型**：拉起子进程 + MCP `initialize` 握手 + `tools/list` 工具发现。如果是 `npx` 型，还叠加 npm 解析包的时间——本机实测 markmap 首次/暖态都要 **约 49 秒**。
- **HTTP 型（streamable_http / sse）**：TCP+TLS 建立 + 会话初始化。

## 7. 是「Agent 连接时先检索池里有没有建过客户端，没有就建，有就直接拿」吗？

**方向对，但池里存的不是「客户端对象缓存」，而是「还没关闭的活会话」。** `acquire(config)` 的实际步骤：

1. 算 `pool_key`。
2. `_entries` 里命中且 `not entry.closed` → `ref_count += 1`、`touch()`、标记 `reused=True` 直接返回（池命中）。
3. 未命中 → `create_task(_worker)` 建立会话；同一 key 用锁串行化，保证并发只建一次。
4. `ready` 在 `connect_timeout_ms` 内没完成或建连抛错 → **从池里移除 + 关闭**，异常抛给调用方（失败不会污染池）。
5. 成功 → `ref_count = 1` 入池，之后调用方 `release()` 递减。

补充两点容易误解的地方：
- 工具调用结束后连接**不会立刻关闭**，而是等引用计数为 0 且空闲超过 5 分钟才被回收。
- 改了 `env`/`args`/`url` → `pool_key` 变化 → 旧会话变成孤儿，由 reaper 淘汰；新配置走新连接，不会误用旧配置。

## 8. 连接池的原理是什么？为什么它能「保持连接」？

**因为它缓存的是真正的连接对象，而不是配置。**

- 池里每个 `PooledSession` 持有：`ClientSession`（MCP 会话）+ 底层传输（stdio 子进程 或 HTTP client）+ 一个**常驻 worker task**。
- 为什么必须这样：AnyIO 的 cancel scope 只能在创建它的 task 内退出，所以「一个物理连接 = 一个常驻 task」；工具调用不是直接调 `session.call_tool`，而是通过 `asyncio.Queue` 把请求投递给 worker，由 worker 串行执行（[client_registry.py](../src/agent/mcp/client_registry.py) 的 `_worker`）。
- 因此所谓「保持连接」就是：
  - stdio：子进程一直活着，stdin/stdout 通道不关；
  - HTTP：底层 httpx 会话不关，省掉重连和握手。

它确实同时是「缓存」和「连接保持」，两层含义都对：

| 视角 | 作用 |
|:---|:---|
| 缓存 | 避免每次工具调用都重新 `spawn 进程 / 建 HTTP 会话`，并复用 `initialize` + `tools/list` 的结果 |
| 连接保持 | 引用计数归零后不立即断，留 5 分钟给同一会话的后续调用继续命中 |
| 资源治理 | `max_pool_size=32` + 空闲淘汰 + 单飞，防止连接无限增长或并发建连风暴 |

与 HTTP 连接池（aiohttp/httpx PoolManager）的类比：同样是「复用 + 空闲回收 + 上限 + 单飞」，只是这里的「连接」可能是一个操作系统进程。

代价与边界：
- 常驻子进程占内存；进程崩溃/被系统杀掉时，`entry.closed` 会置位，下次 `acquire` 自动重建（属于 transient，走重试）。
- 池是**进程内**的，多 worker 部署各自维护一份，不共享。
- `router.call()` 会先按 `ServerState` 拦截 `Failed / Pending / NeedsAuth / Disabled` 的服务，所以重试与退避发生在 runtime 层，池只负责「有健康连接就复用」。

