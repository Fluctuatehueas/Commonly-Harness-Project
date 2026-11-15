# AgentTeam 组长委派链路：核实与排障

Time: 2026/11/14
配套设计：[AgentTeam 设计文档](../core_design/AgentTeam设计.md)（D4 / D7 / D8、4.2、4.5）

---

## 0. 结论摘要

| 问题 | 结论 |
|:--|:--|
| 组长能否把组员当 SubAgent 委派？ | **能，且这是唯一路径**。组员被编译成 deepagents 的声明式 SubAgent，组长通过内置的 `task` 工具委派，没有单独的 `delegate_to_member` 工具 |
| 为什么实测时主 Agent 全程自己干？ | 机制是通的，但**触发条件有 5 个可能断点**，其中"组员没被注册成 subagent"和"委派信号太弱"最常见。当前没有任何构图日志，所以只能靠猜 |
| 主 Agent 是否需要动态创建？ | **不需要**。当前架构是"静态档案 + 按 `(agent_id, group_id)` 缓存构图"，动态的只是每次委派的一次性子 Agent 运行。这已经是设计决策 D8，本轮不建议变更 |

---

## 1. 机制核实：组内委派到底走哪条链路

### 1.1 三条委派路径

| 场景 | 工具 | 谁可用 | 实现位置 |
|:--|:--|:--|:--|
| 组长 → 本组组员 | `task(subagent_type="<agent_id>")` | 组长 | deepagents 的 `SubAgentMiddleware` |
| 组长 → 其他组组长 | `delegate_to_group_leader(group_id, task)` | 仅活跃组长 | `src/agent/agent_team/tools.py:159-192` |
| 组员 → 任何人 | 无 | — | 组员是叶子节点 |

**关键认知：本组委派没有自研工具。** 项目里只实现了跨组的 `delegate_to_group_leader`；本组委派复用的是 DeepAgents 原生的 `task`，因为设计决策 D4/D7 选择了"组 = 一张图，组长 = 主 Agent，组员 = 子 Agent"。

### 1.2 组员变成 SubAgent 的过程

```
AgentLoader.get_agent_for_user(agent_id, group_id)
  └─ AgentContextFactory.build()                     # 读档案 + 组 + 组籍，构造 TeamAgentContext
      └─ create_main_agent(context=TeamAgentContext)
          └─ build_team_subagents()                  # src/agent/agent_team/assembly.py:94-133
              ├─ build_member_subagents()            # 每个组员 → 一个 SubAgent dict
              └─ + 组白名单里的全局 YAML
          └─ create_deep_agent(subagents=[...])      # src/agent/main_agent.py:538-543
              └─ deepagents 自动生成 task 工具
```

组员 SubAgent 的关键字段（`src/agent/agent_team/assembly.py:54-90`）：

```python
subagents.append({
    "name": card.agent_id,          # task(subagent_type=...) 用的就是这个
    "description": f"{profile['name']}｜{profile['role_title']}：{...}",
    "system_prompt": member_context.render() + "## 委派边界 ...",
    "tools": own_mcp_tools + local_tools,
    "model": 成员级模型,
    "middleware": [ ..., TeamPermissionMiddleware(allowed=frozenset()), ... ],
})
```

### 1.3 模型实际看到的委派入口

`task` 工具的 description 由 deepagents 拼装（`.venv/Lib/site-packages/deepagents/middleware/subagents.py:426-437, 603-616`）：

```
Launch an ephemeral subagent to handle a complex, multi-step task.

Available agent types and the tools they have access to:
- agent_ab12cd: 张三｜文献综述专家：负责检索与综述，输出带引用的调研稿
- agent_ef34gh: 李四｜：组成员            ← 描述为空时就是这样
...
```

**这行文本就是模型唯一的委派依据。** 它由 `{name}｜{role_title}：{description or '组成员'}` 拼出来，所以"组员档案里没写 role_title / description"会直接退化成无信息的一行。

### 1.4 权限边界（已实现，无需改）

| 边界 | 落点 |
|:--|:--|
| 组员看不到团队工具 | `TeamPermissionMiddleware(allowed=frozenset())`，`src/agent/agent_team/middleware.py:38-73` |
| 组员不能横向委派 | 子 Agent 的 `system_prompt` 明写"不要向其他成员横向委派"，且 subagent 内不含 `task` |
| 组长才能跨组 | `allowed_team_tools()` 按 `is_active_leader` 返回，`src/agent/agent_team/context.py:406-412` |
| `private` 组不可被跨组委派 | `discover` 只返回 `visibility == "discoverable"`，`tools.py:138-157` |

---

## 2. 为什么这次没触发委派

按可能性排序。每个分支都给出**可验证的判据**，不需要读代码猜。

### 分支 1：组员根本没进 subagents（最常见）

`src/agent/agent_team/assembly.py:47-50`：

```python
profile = profile_map.get(card.agent_id)
if profile is None or not bool(profile.get("enabled", True)):
    logger.warning("组成员档案缺失或停用，已跳过：%s", card.agent_id)
    continue
```

组里只剩组长 → `sync_subagents = []` → `create_deep_agent` 不挂 `SubAgentMiddleware` → **`task` 工具不存在**。

此时模型手里的工具是 `search_web / fetch_web / execute / read_file / write_file`（`src/agent/main_agent.py:375-396`），它的最优策略就是全自己干 —— 与实测截图完全一致。

**判据**

- 启动日志含 `组成员档案缺失或停用，已跳过`
- 该轮 system prompt 的"组内成员"段落为空，或工具列表里没有 `task`

### 分支 2：本次会话没进 TEAM 模式

`src/api/agent_loader.py:209-213` + `src/api/chat.py:465-481`：

- 前端 `group_id` 为空（`selectedGroupId` 没选组）
- 或 `HARNESS_AGENT_TEAM_ENABLED=0`

都会退化成 `StandaloneAgentContext`，`subagents` 只剩全局 YAML，组员完全不参与。

**判据**：system prompt 里没有 `## 你所在的组` 段落。

### 分支 3：组长没设置，或当前 Agent 其实是组员

`leader_agent_id` 为空时，所有人都是 `member`：

- `is_active_leader == False` → `allowed_team_tools()` 不含 `delegate_to_group_leader`
- `GroupContext.render()` 不会输出 `## 组长执行约束`（`context.py:233-246`）

`task` 仍在（组员依旧被注册），但提示词里没有任何"你负责统筹、要分派"的角色设定。

**判据**：有 `## 你所在的组` 但没有 `## 组长执行约束`，且 `- 你的角色：组员`。

### 分支 4：委派信号太弱（设计层面的真问题）

组员 `role_title` / `description` 为空时，`task` 描述里的那一行是 `李四｜：组成员`。再对比主 Agent 自己手里的工具集，模型没有任何理由相信"交给他更快更好"。

**判据**：看该轮 system prompt 的成员列表，或直接在组管理里看组员的角色描述。若都是空，这就是主因。

### 分支 5：提示词允许"主 Agent 自己干"

`src/agent/main_agent.py:489-512` 的并行策略写的是"单步小任务直接做"，第 5 条还明确让主 Agent 自己也干活；而团队约束只有 `context.py:223-231` 一句"组内成员（组长可直接用 `task` 委派）"。

结论：**当前是软约束**。模型判定"我自己干也符合规则"完全合理。

---

## 3. 修复方案（按性价比排序）

### P0：先让系统可观测（必做，改动最小）

在 `src/agent/main_agent.py:342`（`_assert_unique_subagent_names` 之后）加一条结构化日志：

```python
logger.info(
    "agent_graph_built mode=%s agent=%s group=%s subagents=%s has_task=%s",
    context.mode.value,
    context.identity.agent_id,
    (context.member_context.active_group_id if context.member_context else ""),
    [item["name"] for item in sync_subagents],
    bool(sync_subagents),
)
```

没有这条日志，分支 1/2/3 只能靠复现猜。这是整个排障的前置条件。

### P1：让组员"值得被委派"

- 招募/编辑组员时，`role_title` 与 `description` 必填，且按"负责什么 → 输入 → 输出"写。
- `duty`（组内职责，`MemberPayload.duty`）已经在渲染链里，但为空时没有兜底文案。
- 效果：`task` 描述从 `李四｜：组成员` 变成 `李四｜文献综述专家：负责检索与综述，输入主题与年限，输出带引用的调研稿`。**这是投入产出比最高的一步。**

### P2：提示词层加"委派优先"

在 `parallel_policy`（`main_agent.py:489-512`）里补一条硬性语义：

```
- 只要组内存在职责匹配的成员，必须先 task 委派，主 Agent 只负责拆解、汇总与决策；
  不得代做组员职责范围内的工作。
```

### P3：要"必须委派"，只能靠工具权限

提示词是软约束。真正的硬约束是**能力隔离**：把检索 / 分析类 MCP 只配给组员，主 Agent 不配。这样主 Agent 想自己干也干不了，只能委派。

代价：主 Agent 失去通用兜底能力，简单任务也要多一跳。

### 实施状态（2026/11/14）

| 项 | 状态 | 落点 |
|:--|:--|:--|
| P0 构图日志 | 已实施 | `src/agent/main_agent.py`，日志键 `agent_graph_built` |
| P1 组员描述可委派 | 已实施 | `src/agent/agent_team/assembly.py` 的 `_member_description()` |
| P2 委派优先提示词 | 已实施 | `src/agent/agent_team/context.py` 组长执行约束 + `src/agent/main_agent.py` 的 `team_policy` |
| 组员是叶子节点 | 已修复 | `build_team_subagents()` 对非组长直接返回空清单 |
| 组未设组长的静默失效 | 已修复 | 构图时 `logger.warning`，不再无提示退化 |
| P3 工具权限隔离 | 未实施 | 需产品决策：是否允许主 Agent 失去通用检索能力 |

已知残留：组员档案 `description` 与组内 `duty` 都为空时，`task` 描述会退化为
"承担 X 相关子任务"，同时打 warning。这是数据问题，不是代码问题，需要在
招募页或组管理里补齐。

---

## 4. 主 Agent 是否需要动态创建

**不需要。** 现有架构已经覆盖了需求场景：

| 维度 | 现状 |
|:--|:--|
| Agent 定义 | 持久化档案（`AgentProfileStore`），招募时固定 prompt / 模型 / 工具 |
| 组 | 档案集合，持久化在 `agent_groups` 命名空间 |
| 图的创建时机 | 首次请求时构图，按 `(agent_id, group_id, integration_server_id)` 缓存（`src/api/agent_loader.py:237-272`） |
| 配置变更 | 所有组/档案写操作后 `reset_user_agents`，下次请求重建（`src/api/agent_groups.py:102-103`） |
| 真正动态的部分 | 每次 `task` 调用新建一个 isolated 子 Agent 的一次性运行，状态不共享 |

这正是设计决策 **D8：只做构建期动态，不引入动态成员图**。

什么时候才需要运行时动态创建 Agent：

- 需要"现场生成专家"（连 prompt、工具、模型都按任务即时合成）的开放式 Agent 合成产品；
- 需要成员级独立 context / 独立审计粒度（设计文档 4.7 的备选方案）。

代价：无法预先建立评估基线、trace 语义爆炸、权限边界无法静态审计。

**判断**：当前瓶颈是"静态组员没被用起来"，不是"Agent 不够动态"。先做 P0-P2，再看是否需要 P3，动态创建不在本轮范围内。

---

## 5. 验证方式

改动前后用同一组会话回放对比。最小验收集：

| 编号 | 用例 | 期望 |
|:--|:--|:--|
| E1 | 组内 2 名组员，档案完整且启用 | 构图日志 `subagents` 含 2 个 `agent_id`，`has_task=true` |
| E2 | 同上，给一个跨检索+写作的多步任务 | 轨迹中出现 `task` 调用，且参数 `subagent_type` 属于组员 |
| E3 | 组内 1 名组员被停用 | 日志出现"已跳过"，`task` 描述中不含该成员 |
| E4 | 不选组对话 | system prompt 无 `## 你所在的组`，`subagents` 只剩全局 YAML |
| E5 | 组长把任务委派给组外成员 | 工具不存在 / 中间件拒绝，模型不得绕过 |

E1 与 E4 是回归基线，必须长期保留在评估集里。

---

## 6. 关键文件索引

| 职责 | 文件 |
|:--|:--|
| 组员 → SubAgent 编译 | `src/agent/agent_team/assembly.py:23-133` |
| 组上下文与权限矩阵 | `src/agent/agent_team/context.py:189-299, 398-438` |
| 团队工具（组发现 / 跨组委派） | `src/agent/agent_team/tools.py:42-195` |
| 团队工具可见性与硬拒绝 | `src/agent/agent_team/middleware.py:38-110` |
| 主 Agent 组合根（工具 / 子 Agent / 提示词） | `src/agent/main_agent.py:324-577` |
| 图缓存与上下文装配 | `src/api/agent_loader.py:184-273` |
| `task` 工具描述生成（框架侧） | `.venv/Lib/site-packages/deepagents/middleware/subagents.py:426-451, 603-616` |
