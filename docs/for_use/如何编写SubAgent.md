# 怎么写一个 SubAgent，并且让主 Agent 能发现、能委派

这篇文档只讲一件事：**从零新增一个子 Agent，到主 Agent 能稳定调用它，需要改哪些文件、写哪些代码。**

按下面的顺序做，基本不会踩坑。最后有完整示例和常见报错对照表。

---

## 0. 先理解调用链

主 Agent 并不是"自己去找子 Agent"，而是通过 DeepAgents 的 `task` 工具委派。链路是：

```
YAML 配置
  → src/agent/subagents/loader.py 读取并绑定工具
  → src/agent/main_agent.py 注册进 SYNC_SUBAGENT_CONFIG_FILES
  → create_deep_agent(subagents=[...])
  → 主 Agent 的 task 工具描述里自动出现你的子 Agent
  → 主模型调用 task(subagent_type="你的名字", description="任务")
  → 子 Agent 在自己的隔离状态里执行，返回一段文本给主 Agent
```

所以你要做的事情只有三件：

1. 写 YAML；
2. 在 `main_agent.py` 注册；
3. 如果用了自定义工具，把工具对象交给 loader。

---

## 1. 写 YAML 配置

目录：`src/agent/subagents/configs/`

文件名建议和 `name` 一致，例如 `parallel_executor.yaml`。

最小可用配置：

```yaml
name: parallel_executor

description: |
  一句话说明这个子 Agent 负责什么、适合什么场景、不适合什么场景。
  主模型是看这段描述来决定要不要委派的，写得越具体，委派越准。

tools:
  - get_current_time

system_prompt: |
  你是「并行执行者」……

  ## 职责
  - ……

  ## 禁止
  - ……
```

### 字段说明

| 字段 | 必填 | 说明 |
|:--|:--|:--|
| `name` | 是 | 全局唯一。主模型用这个名字调用 `task(subagent_type=...)`。重名会在构图时报错。 |
| `description` | 是 | 给主模型看的"招聘启事"。写清楚适用场景和不适用场景。 |
| `system_prompt` | 是 | 子 Agent 的角色、边界、输出格式。 |
| `tools` | 是 | 必须全部加载成功的工具名列表；少一个就构图失败。 |
| `optional_tools` | 否 | "有就绑定，没有就跳过"的工具。联网搜索、技能管理、视觉分析都建议放这里。 |
| `mcp_servers` | 否 | 声明 MCP 服务名，loader 会把该服务的全部工具绑定进来。 |
| `model` | 否 | `main` 或 `vision`。不写默认继承主 Agent 模型。 |
| `deny_tools` | 否 | 工具级拒绝名单，适合做只读子 Agent。 |
| `skills` | 否 | 子 Agent 独立技能目录，必须在 `/skills/subagents/<name>/` 下。 |
| `interrupt_on` | 否 | 工具级人工审批配置，透传给 DeepAgents。 |
| `permissions` | 否 | 路径级文件权限，会被 `main_agent.py` 转成 `FilesystemPermission`。 |

### 文件读写能力是自动给的

声明式子 Agent 在构图时，DeepAgents 会自动补一层 `FilesystemMiddleware`，因此
**每个子 Agent 天然拥有**：

- `read_file`
- `write_file`
- `edit_file`
- `ls` / `glob` / `grep`
- `execute`（有沙箱时）

所以不要在 `tools` 里写这些名字——它们不是普通工具对象，写了会报"缺少配置工具"。
如果你想让某个子 Agent 只读，用：

```yaml
deny_tools: ["write_file", "edit_file", "execute"]
```

---

## 2. 注册到主 Agent

打开 `src/agent/main_agent.py`，找到：

```python
SYNC_SUBAGENT_CONFIG_FILES: list[str | Path] = [
    config_path("document_reader.yaml"),
    config_path("intent_parsing.yaml"),
    # ...
]
```

把你的 YAML 加进去：

```python
SYNC_SUBAGENT_CONFIG_FILES: list[str | Path] = [
    config_path("document_reader.yaml"),
    config_path("intent_parsing.yaml"),
    config_path("my_new_agent.yaml"),   # ← 新增
]
```

重启后端。主 Agent 的 `task` 工具描述里就会自动出现你的子 Agent。
不需要改系统提示词，也不需要手动注册工具。

> 注意：`SYNC_SUBAGENTS` 是另一种扩展点，适合"工具对象已经在 Python 里现成可用"的场景；
> 绝大多数情况用 YAML 就够了。

---

## 3. 需要自定义工具时怎么接

分两种情况。

### 情况 A：工具是普通本地工具

1. 在 `src/agent/tools/` 下新建工具文件，用 `@tool` 装饰：

```python
from langchain_core.tools import tool

@tool
def my_tool(query: str) -> str:
    """一句话说明工具做什么，模型会看这段 docstring。"""
    return "..."
```

2. 在 `main_agent.py` 的 `_load_sync_subagents()` 里放进 `local_tools`：

```python
from agent.tools.my_tools import my_tool

async def _load_sync_subagents(extra_local_tools=()):
    local_tools = [
        read_word_document,
        request_additional_info,
        request_user_choice,
        my_tool,                    # ← 新增
        *extra_local_tools,
    ]
    ...
```

3. YAML 里按名字声明：

```yaml
tools:
  - my_tool
```

> 基础工具 `get_current_time / calculate / format_json` 已经默认在 `local_tools` 里，
> 直接写进 YAML 的 `tools` 即可，不需要再改代码。

注意：`@tool` 的函数名就是工具名；YAML 里必须一字不差。

### 情况 B：工具依赖沙箱 / 运行时对象

参考技能管理工具的做法：写一个工厂函数，在 `create_main_agent()` 里创建，
再作为 `extra_local_tools` 传给 `_load_sync_subagents()`。

```python
# main_agent.py
from agent.tools.my_sandbox_tools import create_my_tools

my_tools = create_my_tools(sandbox_backend)

sync_subagents = await _load_sync_subagents(
    extra_local_tools=[*skill_management_tools, *vision_tools, *my_tools]
)
```

YAML 里把这类工具放 `optional_tools`，这样没有沙箱时不会阻断构图。

### 情况 C：MCP 工具

YAML 里写服务名即可：

```yaml
mcp_servers:
  - my_mcp_server
```

loader 会把这个服务的全部工具绑定进来，不需要在 `tools` 里逐个列名字。

---

## 4. 怎么保证"同一类型可以并发多个实例"

DeepAgents 的 `task` 工具本身支持并发：主模型在**一条消息里发多个 tool call**，
就会并行启动多个子 Agent 实例。

子 Agent 之间状态隔离，同一个 `subagent_type` 可以同时开 N 个。
你需要做的是：

1. 在 `description` 里写清楚"一个实例只处理一个任务"；
2. 在 `system_prompt` 里禁止子 Agent 互相等待、互相汇总；
3. 不要把共享可变状态放在子 Agent 的全局变量里。

`main_agent.py` 里的 `parallel_policy` 已经默认要求主 Agent：

- 独立任务必须并发调用多个 `task`；
- 同一类型可以开多个实例；
- 有依赖才串行；
- 汇总时只取各实例结论。

---

## 5. 进度回调是怎么工作的

所有通过 `SYNC_SUBAGENT_CONFIG_FILES` 注册的子 Agent，都会在
`_load_sync_subagents()` 里自动挂上 `SubagentProgressMiddleware`
（`src/agent/middlewares/subagent_progress.py`）。

它做两件事：

1. 每次子 Agent 运行生成唯一 `run_id`；
2. 通过 LangGraph 的 custom stream 通道发出事件：

```json
{
  "type": "subagent_progress",
  "subagent": "parallel_executor",
  "run_id": "a1b2c3d4",
  "event": "started | tool_start | tool_end | finished",
  "tool": "read_file",
  "status": "done | error",
  "message": "阶段说明或结果摘要"
}
```

因为 `run_id` 是每次运行独立的，所以：

- 同一个子 Agent 并发 3 个实例，不会互相覆盖进度；
- 前端按 `run_id` 建独立节点，再绑定到对应的 `task` 委派节点。

后端 `src/api/chat.py` 的 `_get_custom_event()` 会把这些事件透传到 SSE；
前端 `src/stores/chat.ts` 的 `subagent_progress` 分支负责渲染。

> 如果你新增了子 Agent，不需要额外写进度代码；注册后自动生效。

---

## 6. 验证清单

改完后按这个顺序检查：

1. **YAML 能读通**：启动后端，看日志有没有"子 Agent 配置无效 / 缺少配置工具"。
2. **任务列表出现**：在主 Agent 的 `task` 工具描述里应该能看到你的 `name` 和 `description`。
3. **能委派**：发一句明确要求调用该子 Agent 的话，例如
   "请用 parallel_executor 并行检查 A、B、C 三个目录"。
4. **能并发**：要求"同时开两个 web_researcher，分别查 X 和 Y"，看轨迹里是否出现两个并行节点。
5. **进度不串**：并发同一类型时，每个实例的进度节点应该独立，不互相追加。
6. **文件能力**：让子 Agent 读一个项目文件、写一个临时文件，确认 `read_file` / `write_file` 可用。

---

## 7. 常见报错对照表

| 报错/现象 | 原因 | 处理 |
|:--|:--|:--|
| `子 Agent 配置无效` | YAML 少了 `name` / `description` / `system_prompt` / `tools` | 补齐四个必填字段 |
| `子 Agent 缺少配置工具: xxx` | `tools` 里的名字没有被 loader 绑定 | 检查工具函数名；本地工具要加进 `local_tools`；MCP 工具要写 `mcp_servers` |
| `Duplicate subagent name` | 两个 YAML 的 `name` 相同 | 改名，保持全局唯一 |
| `tools 与 optional_tools 重复` | 同一个工具出现在两个列表 | 只保留在一个列表里 |
| 主模型不委派 | `description` 太笼统 | 写清楚适用/不适用场景，并在主提示词里要求并行拆任务 |
| 子 Agent 没有写文件权限 | `deny_tools` 把 `write_file` 禁了 | 从 `deny_tools` 移除 |
| 子 Agent 执行命令失败 | 没有沙箱 | 配置沙箱；或把该能力改成 `optional_tools` 并在提示词里说明降级行为 |
| 进度节点互相覆盖 | 没有用 `run_id` 区分 | 确认 `SubagentProgressMiddleware` 已挂载；前端按 `run_id` 建节点 |

---

## 8. 完整最小示例

### 8.1 YAML：`src/agent/subagents/configs/my_reviewer.yaml`

```yaml
name: my_reviewer

description: |
  代码审查子 Agent。适合对单个目录或单个 PR 做静态审查，
  输出问题清单与修改建议；不负责直接改代码。
  一个实例只审查一个目标，多个目标由主 Agent 并发开多个实例。

tools:
  - get_current_time
  - format_json

deny_tools: ["execute"]

system_prompt: |
  你是「代码审查员」，只做静态审查。

  ## 规则
  - 只报告能在文件中证实的问题，标注文件路径与行号。
  - 区分"确定问题"与"建议"，不要编造测试结果。
  - 需要读取文件时使用 read_file / grep / glob。
  - 输出：问题清单（严重级别 / 位置 / 原因 / 建议）+ 总体结论。
```

### 8.2 注册：`src/agent/main_agent.py`

```python
SYNC_SUBAGENT_CONFIG_FILES: list[str | Path] = [
    config_path("document_reader.yaml"),
    config_path("intent_parsing.yaml"),
    config_path("parallel_executor.yaml"),
    config_path("web_researcher.yaml"),
    config_path("vision_analyst.yaml"),
    config_path("skill_curator.yaml"),
    config_path("my_reviewer.yaml"),   # ← 新增
]
```

### 8.3 重启并验证

```text
请用 my_reviewer 审查 src/task_flow 目录，同时再开一个实例审查 src/api。
```

预期：

- 轨迹里出现两个 `委派 my_reviewer` 节点；
- 每个节点下面有独立的 `read_file` / `grep` 调用和进度；
- 两个实例的进度不会串到对方节点下；
- 主 Agent 最后汇总两份审查结论。

---

## 9. 一句话总结

**写 YAML → 加进 `SYNC_SUBAGENT_CONFIG_FILES` → 需要工具就交给 `local_tools`。**
文件读写自动有，进度回调自动有，并发多实例由 `task` 工具天然支持。
