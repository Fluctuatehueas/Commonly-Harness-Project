"""组装单个用户可复用的主 Agent 图。

本模块是 Agent 层的组合根：选择后端、工具、子 Agent 和中间件，并交给
``create_deep_agent``。图由 ``api.agent_loader`` 按 ``user_id`` 缓存；
同一用户的不同会话共用本图和沙箱代理，但通过不同 ``thread_id`` 使用独立
checkpoint；长期记忆则经由 ``/memories/`` 路由到该用户的 PostgreSQL Store。
"""


from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, StateBackend, StoreBackend
from deepagents.middleware.filesystem import FilesystemPermission
from langchain_core.runnables import RunnableConfig

from agent.backends.project_backend import ProjectWorkspaceBackend
from agent.backends.readonly_backend import ReadOnlyFilesystemBackend
from agent.backends.sandbox_proxy import SandboxBackendProxy
from agent.backends.skill_sync import SandboxSkillSynchronizer
import agent.config as agent_config
from agent.config import (
    AGENTS_MD_FILENAME,
    ASYNC_AGENT_PROTOCOL_URL,
    MAIN_AGENT_MODEL_RUN_LIMIT,
    MAIN_AGENT_TOOL_RUN_LIMIT,
    MAIN_SKILLS_PATH,
    build_model,
    context_limit_for,
)
from agent.user_data import model_settings, user_skills_root
from agent.memory.prompts import build_web_search_policy
from agent.memory.prompts import system_prompt as CODE_DEFAULT_AGENT_PROMPT
from agent.middlewares.agent_protection import build_agent_protection_middleware
from agent.middlewares.context_injection import ContextInjectionMiddleware
from agent.middlewares.context_window_monitor import ContextWindowMonitorMiddleWare
from agent.middlewares.evolution import EvolutionMiddleware
from agent.middlewares.experience_injection import ExperienceInjectionMiddleware
from agent.middlewares.memory_update import MemoryUpdateMiddleware
from agent.middlewares.self_judge import SelfJudgeMiddleware
from agent.middlewares.skills_sync import SandboxSkillsMiddleware
from agent.middlewares.subagent_progress import SubagentProgressMiddleware
from agent.middlewares.token_usage import TokenUsageMiddleware
from agent.middlewares.trajectory import TrajectoryMiddleware
from agent.middlewares.visualization import VisualizationMiddleware
from agent.schema import HarnessContext
from agent.subagents.async_registry import (
    get_async_subagent_instructions,
    get_async_subagent_names,
    get_async_subagent_specs,
)
from agent.subagents.configs import config_path
from agent.subagents.loader import read_subagent_config
from agent.tools.async_sandbox_tools import create_async_sandbox_tools
from agent.tools.document_tools import read_word_document
from agent.tools.hitl_tool import request_additional_info, request_user_choice
from agent.tools.host_tools import build_host_tools
from agent.tools.planning_tools import create_planning_tools
from agent.tools.rag_tools import create_rag_tools
from agent.mcp.runtime import get_mcp_runtime
from agent.agent_profiles import AgentProfileStore
from agent.tools.skill_tools import create_skill_management_tools
from agent.tools.commom_tools import get_basic_tools
from agent.tools.vision_tools import create_vision_tools
from agent.tools.workspace_tools import (
    create_publish_to_project_tool,
    create_run_in_sandbox_tool,
)

logger = logging.getLogger(__name__)

# 同步子 Agent 扩展点：
# - 已经拿到工具对象的静态配置放进 SYNC_SUBAGENTS；
# - 需要从 YAML 声明加载 MCP 工具时，把 YAML 路径放进 SYNC_SUBAGENT_CONFIG_FILES。
# 同步子 Agent 与主 Agent 共享同一沙箱和会话状态。
SYNC_SUBAGENTS: list[dict[str, Any]] = []
SYNC_SUBAGENT_CONFIG_FILES: list[str | Path] = [
    config_path("document_reader.yaml"),
    config_path("intent_parsing.yaml"),
    config_path("parallel_executor.yaml"),
    config_path("web_researcher.yaml"),
    config_path("vision_analyst.yaml"),
    config_path("skill_curator.yaml"),
]


def _declared_subagent_names() -> set[str]:
    """不完整构图，只读出已声明子 Agent 的名称，供技能工具校验目标使用。"""
    names = {
        str(subagent.get("name"))
        for subagent in SYNC_SUBAGENTS
        if subagent.get("name")
    }
    for config_file in SYNC_SUBAGENT_CONFIG_FILES:
        try:
            names.add(str(read_subagent_config(config_file)["name"]))
        except Exception:
            logger.warning("读取子 Agent 配置名称失败：%s", config_file, exc_info=True)
    return names


def _coerce_permissions(config: dict[str, Any]) -> dict[str, Any]:
    """把 YAML 里的权限字典转成 FilesystemPermission 数据类。

    ``create_deep_agent`` 只接受数据类实例；YAML 读出来是 dict，
    必须在构图前转换，否则子 Agent 的权限规则不会生效。
    """
    raw = config.get("permissions")
    if not raw:
        return config

    rules: list[FilesystemPermission] = []
    for item in raw:
        if isinstance(item, FilesystemPermission):
            rules.append(item)
        elif isinstance(item, dict):
            rules.append(
                FilesystemPermission(
                    operations=list(item.get("operations", ["read"])),
                    paths=list(item.get("paths", ["/**"])),
                    mode=item.get("mode", "allow"),
                )
            )
        else:
            raise ValueError(f"子 Agent permissions 配置无效: {item!r}")

    coerced = dict(config)
    coerced["permissions"] = rules
    return coerced


async def _load_sync_subagents(
    extra_local_tools: Iterable[Any] = (),
) -> list[dict[str, Any]]:
    """加载静态配置和 YAML 中声明的同步子 Agent。

    子 Agent 的 YAML 可以用 ``optional_tools`` 声明“有则绑定、无则跳过”的工具，
    未加载的工具不会阻断构图。
    """
    from agent.subagents.loader import aload_subagent

    local_tools = [
        read_word_document,
        request_additional_info,
        request_user_choice,
        # 基础工具（时间/计算/JSON）也允许子 Agent 在 YAML 的 tools 中声明。
        *get_basic_tools(),
        # 技能管理、视觉分析等工具由主 Agent 构图时创建，按需注入。
        *extra_local_tools,
    ]
    loaded = list(SYNC_SUBAGENTS)
    for config_file in SYNC_SUBAGENT_CONFIG_FILES:
        # read_word_document / request_additional_info 不是 MCP 工具，必须作为
        # local_tools 传进去，否则 YAML 里声明它们的子 Agent 会在构图时报"缺少配置工具"。
        subagent = await aload_subagent(config_file, local_tools=local_tools)
        subagent = _coerce_permissions(subagent)
        # 每个子 Agent 都挂进度回调：并发委派时按 run_id 区分实例。
        subagent.setdefault("middleware", [])
        if isinstance(subagent["middleware"], list):
            subagent["middleware"].append(
                SubagentProgressMiddleware(str(subagent["name"]))
            )
        loaded.append(subagent)
    return loaded


def _ensure_unique_tool_names(tools: list[Any]) -> None:
    """拒绝主 Agent 内同名工具，避免模型调用时出现歧义。"""
    names = [str(getattr(tool, "name", "")) for tool in tools]
    duplicates = sorted(
        name for name, count in Counter(names).items() if count > 1
    )
    if duplicates:
        raise RuntimeError(f"主 Agent 工具名称重复: {duplicates}")


# ============================================================
# 主 Agent 图装配
# ============================================================


async def create_main_agent(
    config: RunnableConfig,
    *,
    store: Any,
    checkpointer: Any,
    sandbox_backend: SandboxBackendProxy | None,
    integration_server_id: str | None = None,
    agent_profile: dict[str, Any] | None = None,
) -> Any:
    """创建绑定用户身份、持久化资源和共享沙箱的主 Agent 图。

    Args:
        config: 调用配置；``configurable.user_id`` 决定记忆隔离范围。
        store: PostgreSQL 长期记忆与会话索引使用的 Store。
        checkpointer: 按 ``thread_id`` 保存和恢复 LangGraph 执行状态的组件。
        sandbox_backend: 用户级稳定沙箱代理；传 ``None`` 表示无沙箱的纯对话
            模式（文件工具落到线程状态，禁用技能同步与异步任务）。
        integration_server_id: 会话绑定的集成 MCP；只有该后端与公共 MCP
            的工具可用，其他集成后端的业务工具不会暴露。
        agent_profile: 用户在 MyAgent 中配置的 Agent 档案；决定系统提示词、
            模型覆盖与 MCP 绑定。``None`` 时使用默认主 Agent。

    Returns:
        已配置主工具、子 Agent、中间件和持久化后端的 DeepAgents 图。
    """
    configurable = config.get("configurable", {})
    user_id = str(configurable.get("user_id", "anonymous"))
    sandbox_enabled = sandbox_backend is not None

    # 每个用户 / 每个 Agent 都有自己的模型配置；未覆盖时继承用户默认模型。
    profile = agent_profile or {}
    effective_model = AgentProfileStore.merge_model(profile, model_settings(user_id))
    model = build_model(effective_model, role="main", user_id=user_id)
    summary_model = build_model(effective_model, role="summary", user_id=user_id)
    effective_context_limit = int(
        effective_model.get("context_limit") or context_limit_for(user_id)
    )
    # 技能库按用户隔离；首次使用时由 user_data 从内置技能复制。
    skills_root = user_skills_root(user_id)
    mcp_server_ids = [
        str(item) for item in (profile.get("mcp_server_ids") or []) if item
    ] or None

    # 文件路径决定存储位置：/memories/ 写 PostgreSQL，/artifacts/ 留在图状态，
    # 其余路径落当前项目目录；命令执行始终走沙箱。
    routes: dict[str, Any] = {
        "/memories/": StoreBackend(
            namespace=lambda runtime: (user_id,),
            store=store,
        ),
        # 大工具结果与对话历史留在图状态，不能写进用户项目目录。
        "/artifacts/": StateBackend(),
    }
    if not sandbox_enabled:
        # 无沙箱时把宿主技能目录只读挂载到 /skills/：
        # 技能仍能渐进式加载，但 Agent 不能改写宿主上的 SKILL.md。
        routes["/skills/"] = ReadOnlyFilesystemBackend(root_dir=skills_root)
    # 文件默认落当前项目目录，命令只走沙箱；没有沙箱时执行会被明确拒绝。
    default_backend = ProjectWorkspaceBackend(sandbox_backend)
    backend = CompositeBackend(
        default=default_backend,
        routes=routes,
        artifacts_root="/artifacts/",
    )

    async_subagents: list[Any] = []
    async_tools: list[Any] = []
    skill_management_tools: list[Any] = []
    skill_middleware: list[Any] = []
    # 主图只发现主技能目录；每个子 Agent 从自己的配置读取独立技能目录。
    # 有沙箱时由同步中间件把技能推到沙箱；无沙箱时直接读只读挂载。
    skills_sources: list[str] = [MAIN_SKILLS_PATH]
    if sandbox_enabled:
        # 同步器把宿主维护的技能和固定 AGENTS.md 增量复制到用户沙箱。
        skill_synchronizer = SandboxSkillSynchronizer(
            skills_root,
            Path(__file__).parent / "memory" / "AGENTS.md",
        )

        # 异步子 Agent 的图不嵌入主图；这里只注册提交工具，调用后立即返回 task_id。
        async_subagents = get_async_subagent_specs(ASYNC_AGENT_PROTOCOL_URL)
        async_tools = (
            create_async_sandbox_tools(async_subagents, sandbox_backend=sandbox_backend)
            if async_subagents
            else []
        )

        # 技能管理工具必须使用稳定代理，才能在沙箱恢复后继续生效。
        # 子 Agent 名称先静态读出，技能工具才能在构图前校验"分配给谁"。
        skill_management_tools = create_skill_management_tools(
            skills_root,
            {"main", *get_async_subagent_names(), *_declared_subagent_names()},
            sandbox_backend=sandbox_backend,
            synchronize=lambda: skill_synchronizer.sync(sandbox_backend),
        )

        # 在 DeepAgents 读取技能元数据前完成同步，保证本次 run 使用最新副本。
        skill_middleware = [
            SandboxSkillsMiddleware(
                backend=backend,
                sources=[MAIN_SKILLS_PATH],
                synchronizer=skill_synchronizer,
            ),
        ]

    # 视觉分析工具注入视觉子 Agent；技能管理与视觉工具都作为 local_tools 参与构图。
    vision_tools = create_vision_tools(sandbox_backend)
    sync_subagents = await _load_sync_subagents(
        extra_local_tools=[*skill_management_tools, *vision_tools]
    )

    # 主 Agent 只保留通用能力：MCP 公共工具、异步任务提交、信息补齐和技能管理。
    # MCP 工具在 Agent 构图时才按 scope 懒发现/取代理，不在进程启动时连接。
    mcp_tools = await get_mcp_runtime(user_id).tools_for_main_agent(
        integration_server_id,
        mcp_server_ids,
    )
    # RAG 工具始终可用；无 Provider 时返回空结果，不阻断普通对话。
    rag_tools = create_rag_tools()
    # 宿主能力默认关闭；开启后每个工具内部都会 interrupt 等待人工审批。
    host_tools = build_host_tools()
    # 复杂任务先产出分工清单，再按波次并发委派。
    planning_tools = create_planning_tools()
    main_tools = [
        *mcp_tools,
        *async_tools,
        *rag_tools,
        *get_basic_tools(),
        *host_tools,
        *planning_tools,
        request_additional_info,
        request_user_choice,
        read_word_document,
        # 项目产物发布与沙箱执行：文件落本机项目，命令只在沙箱运行。
        create_publish_to_project_tool(
            sandbox_backend,
            store=store,
            user_id=user_id,
        ),
        create_run_in_sandbox_tool(sandbox_backend),
        *skill_management_tools,
    ]
    _ensure_unique_tool_names(main_tools)

    delegation_instructions = (
        get_async_subagent_instructions() if sandbox_enabled else ""
    )
    # 联网搜索是运行时开关：每次构图都读取最新配置，配合热更新重建 Agent。
    # 角色卡由 MyAgent 档案提供；这里只追加联网搜索策略，避免重复注入角色。
    search_policy_prompt = build_web_search_policy(
        bool(effective_model.get("web_search_enabled", agent_config.WEB_SEARCH_ENABLED))
    )
    # 文件落盘与执行环境的固定约束：文件落项目，执行只在沙箱。
    host_policy = ""
    if host_tools:
        host_policy = (
            "- 宿主工具 `host_execute` / `host_read_file` / `host_write_file` 是最后手段："
            "只有沙箱缺少目标运行环境、或必须访问项目外路径时才使用，并在 `reason` 中写清原因。\n"
            "- 调用宿主工具会暂停等待人工审批；用户可能选择“仅本会话允许”，"
            "同一会话内相同命令/路径将不再重复询问，换会话后重新审批。\n"
            "- 项目内文件读写请继续使用普通文件工具，不要用宿主工具绕过项目目录。\n"
        )
    execute_policy_line = (
        "- 优先在隔离沙箱中执行命令；沙箱缺少目标环境时，才使用 `host_execute`"
        "（每次调用都会暂停等待人工审批）。\n"
        if host_tools
        else "- 所有命令、脚本、代码必须在隔离沙箱中执行；当前会直接拒绝本机执行。\n"
    )
    integration_policy = ""
    if integration_server_id:
        integration_policy = (
            "\n\n## 集成后端\n"
            f"- 当前会话绑定用户自有后端 `{integration_server_id}`。"
            "业务查询与写入必须使用该后端的 MCP 工具；"
            "不要调用其他集成后端的工具，也不要编造业务数据。\n"
            "- 该后端工具不可用时，明确告知用户后端服务不可达，"
            "不要改用通用工具绕过业务边界。\n"
        )
    execution_policy = (
        "\n\n## 文件与执行环境\n"
        "- 对话分两种模式：项目会话绑定本机项目目录；自由会话没有项目目录。\n"
        "- 项目会话：文件工具中的 `/` 是项目根目录，可写任意类型文件，"
        "但绝不能执行项目内的文件。\n"
        "- 自由会话：文件工具中的 `/` 是该会话在沙箱内的独立目录，"
        "文件保留在沙箱，用户可从右侧文件抽屉查看与下载。\n"
        f"{execute_policy_line}"
        "- 项目会话需要项目文件参与执行时，调用 `run_in_sandbox` 并在 `files` 中"
        "列出项目相对路径；自由会话直接在该会话沙箱目录执行。\n"
        "- 只有项目会话才调用 `publish_to_project` 把成品落到本机；"
        "自由会话不要调用。中间脚本、日志、测试夹具不要发布。\n"
        "- `/.commonharness/` 是项目保留目录，禁止读写。\n"
        f"{host_policy}"
    )
    base_prompt = f"{search_policy_prompt}{integration_policy}{execution_policy}"
    # 并行委派是默认工作方式：独立任务同一条消息里并发开多个子 Agent，
    # 同一类型也可以同时开多个实例；不同实例的状态互不共享。
    parallel_policy = (
        "\n\n## 复杂任务的分工与并行\n"
        "动手前先判断任务规模：单步小任务直接做；多步骤、多个独立子任务、"
        "或包含耗时操作（联网检索、安装依赖、批量执行）的任务，必须先把工作拆开再动手，"
        "禁止串行地想到一件做一件。\n"
        "1. 先调用 `create_work_plan(goal, tasks)` 产出分工清单。每个工作项写清："
        "负责人（main 或具体子 Agent 名称）、依赖、读路径、写路径。\n"
        "2. 按返回的 `waves` 执行：**同一 wave 的所有工作项必须放在同一条消息里并发调用多个 `task`**，"
        "不要等一个返回再发下一个；有 `depends_on` 的工作项等上一波完成后再发。\n"
        "3. 把 `conflicts` 里的写冲突拆到不同 wave，或改由一个实例串行完成；"
        "只读任务、不同目录/不同文件的任务可以放心并行。\n"
        "4. 典型的正确拆分：联网检索与沙箱依赖检查/安装同时进行；"
        "多个互不相关的检索方向各开一个 `web_researcher` 实例；"
        "主 Agent 只负责汇总与决策，不要在等待子 Agent 时重复它已经做过的工作。\n"
        "5. 主 Agent 自己的工作项（检查依赖、读文件、跑命令）也要和 `task` 调用"
        "放在同一条消息里一起发出，不要先等子 Agent 返回再开始。\n"
        "6. 同步子 Agent 的一次 `task` 调用会等待该子 Agent 返回；"
        "因此并行只能通过「同一条消息里多个 task 调用」实现，不能靠连续发多个单 task。\n"
        "- 同一子 Agent 类型可以同时启动多个实例；每个实例只处理一个边界清晰、可独立完成的任务。\n"
        "- 委派描述必须包含：目标、输入、期望产出、禁止事项；需要过程可见时要求按阶段汇报进度。\n"
        "- 汇总时只采纳各实例的结论；不要把某个实例的内部过程当成最终答案。\n"
    )
    harness_policy = (
        f"{base_prompt}{parallel_policy}\n\n{delegation_instructions}"
        if delegation_instructions
        else f"{base_prompt}{parallel_policy}"
    )
    # 用户在 MyAgent 中编辑的角色提示词放最前，固定执行策略紧随其后，
    # 保证自定义风格不会绕过安全与运行环境约束。
    role_prompt = str(profile.get("system_prompt") or "").strip() or (
        CODE_DEFAULT_AGENT_PROMPT.strip()
    )
    full_system_prompt = (
        f"{role_prompt}\n\n{harness_policy}" if role_prompt else harness_policy
    )

    # 中间件顺序具有语义：before_* 按声明顺序运行，after_* 反序运行。
    return create_deep_agent(
        model=model,
        system_prompt=full_system_prompt,
        memory=[AGENTS_MD_FILENAME],
        tools=main_tools,
        subagents=[*sync_subagents, *async_subagents],
        middleware=[
            ContextInjectionMiddleware(),
            # 前端可直接渲染的图表 / 图示 / 图片输出协议。
            VisualizationMiddleware(),
            # 演进模块默认开启；关闭对应环境变量后内部直接短路。
            ExperienceInjectionMiddleware(),
            SelfJudgeMiddleware(summary_model),
            # 只读观测：上下文占用日志 + 过程轨迹落库，不修改 state。
            ContextWindowMonitorMiddleWare(),
            # after_agent 把最终回答的 Token 消耗累计写入 Store（会话统计）。
            TokenUsageMiddleware(),
            # 轨迹结束事件落库后，再异步执行经验/归因/候选编排。
            EvolutionMiddleware(summary_model),
            TrajectoryMiddleware(context_limit=effective_context_limit),
            *skill_middleware,
            # 自动摘要 + 主动压缩 + 模型/工具调用上限。
            *build_agent_protection_middleware(
                backend=backend,
                summary_model=summary_model,
                model_run_limit=MAIN_AGENT_MODEL_RUN_LIMIT,
                tool_run_limit=MAIN_AGENT_TOOL_RUN_LIMIT,
                enable_compaction_tool=True,
            ),
            # after_agent 阶段反序执行，最后写回用户长期偏好。
            MemoryUpdateMiddleware(summary_model),
        ],
        skills=skills_sources,
        backend=backend,
        store=store,
        checkpointer=checkpointer,
        context_schema=HarnessContext,
    )
