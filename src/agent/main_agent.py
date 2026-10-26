"""组装单个用户可复用的主 Agent 图。

本模块是 Agent 层的组合根：选择后端、工具、子 Agent 和中间件，并交给
``create_deep_agent``。图由 ``api.agent_loader`` 按 ``user_id`` 缓存；
同一用户的不同会话共用本图和沙箱代理，但通过不同 ``thread_id`` 使用独立
checkpoint；长期记忆则经由 ``/memories/`` 路由到该用户的 PostgreSQL Store。
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, StateBackend, StoreBackend
from deepagents.middleware.filesystem import FilesystemPermission
from langchain_core.runnables import RunnableConfig

from agent.backends.readonly_backend import ReadOnlyFilesystemBackend
from agent.backends.sandbox_proxy import SandboxBackendProxy
from agent.backends.skill_sync import SandboxSkillSynchronizer
from agent.config import (
    AGENTS_MD_FILENAME,
    ASYNC_AGENT_PROTOCOL_URL,
    MAIN_AGENT_MODEL_RUN_LIMIT,
    MAIN_AGENT_TOOL_RUN_LIMIT,
    MAIN_SKILLS_PATH,
    SKILLS_ROOT,
    get_main_model,
    get_summary_model,
)
from agent.memory.prompts import system_prompt
from agent.middlewares.agent_protection import build_agent_protection_middleware
from agent.middlewares.context_injection import ContextInjectionMiddleware
from agent.middlewares.memory_update import MemoryUpdateMiddleware
from agent.middlewares.skills_sync import SandboxSkillsMiddleware
from agent.schema import HarnessContext
from agent.subagents.async_registry import (
    get_async_subagent_instructions,
    get_async_subagent_names,
    get_async_subagent_specs,
)
from agent.subagents.configs import config_path
from agent.tools.async_sandbox_tools import create_async_sandbox_tools
from agent.tools.document_tools import read_word_document
from agent.tools.hitl_tool import request_additional_info
from agent.tools.mcp_client import load_main_mcp_tools
from agent.tools.skill_tools import create_skill_management_tools


# 同步子 Agent 扩展点：
# - 已经拿到工具对象的静态配置放进 SYNC_SUBAGENTS；
# - 需要从 YAML 声明加载 MCP 工具时，把 YAML 路径放进 SYNC_SUBAGENT_CONFIG_FILES。
# 同步子 Agent 与主 Agent 共享同一沙箱和会话状态。
SYNC_SUBAGENTS: list[dict[str, Any]] = []
SYNC_SUBAGENT_CONFIG_FILES: list[str | Path] = [
    config_path("document_reader.yaml"),
    config_path("intent_parsing.yaml"),
]


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


async def _load_sync_subagents() -> list[dict[str, Any]]:
    """加载静态配置和 YAML 中声明的同步子 Agent。"""
    from agent.subagents.loader import aload_subagent

    loaded = list(SYNC_SUBAGENTS)
    for config_file in SYNC_SUBAGENT_CONFIG_FILES:
        # read_word_document 不是 MCP 工具，必须作为 local_tools 传进去，
        # 否则 YAML 里声明它的子 Agent 会在构图时报"缺少配置工具"。
        subagent = await aload_subagent(
            config_file, local_tools=[read_word_document]
        )
        loaded.append(_coerce_permissions(subagent))
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
) -> Any:
    """创建绑定用户身份、持久化资源和共享沙箱的主 Agent 图。

    Args:
        config: 调用配置；``configurable.user_id`` 决定记忆隔离范围。
        store: PostgreSQL 长期记忆与会话索引使用的 Store。
        checkpointer: 按 ``thread_id`` 保存和恢复 LangGraph 执行状态的组件。
        sandbox_backend: 用户级稳定沙箱代理；传 ``None`` 表示无沙箱的纯对话
            模式（文件工具落到线程状态，禁用技能同步与异步任务）。

    Returns:
        已配置主工具、子 Agent、中间件和持久化后端的 DeepAgents 图。
    """
    configurable = config.get("configurable", {})
    user_id = str(configurable.get("user_id", "anonymous"))
    sandbox_enabled = sandbox_backend is not None

    # 文件路径决定存储位置：/memories/ 下的文件写入 PostgreSQL；
    # 其余路径有沙箱时进入 OpenSandbox，无沙箱时落到线程状态（纯对话模式）。
    routes: dict[str, Any] = {
        "/memories/": StoreBackend(
            namespace=lambda runtime: (user_id,),
            store=store,
        ),
    }
    if not sandbox_enabled:
        # 无沙箱时把宿主技能目录只读挂载到 /skills/：
        # 技能仍能渐进式加载，但 Agent 不能改写宿主上的 SKILL.md。
        routes["/skills/"] = ReadOnlyFilesystemBackend(root_dir=SKILLS_ROOT)
    backend = CompositeBackend(
        default=sandbox_backend if sandbox_enabled else StateBackend(),
        routes=routes,
    )

    # 同步子 Agent 可以来自静态对象，也可以来自带 MCP 声明的 YAML。
    sync_subagents = await _load_sync_subagents()

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
            SKILLS_ROOT,
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
        sync_subagent_names = {
            str(subagent.get("name", "")) for subagent in sync_subagents
        } - {""}
        skill_management_tools = create_skill_management_tools(
            SKILLS_ROOT,
            {"main", *get_async_subagent_names(), *sync_subagent_names},
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

    # 主 Agent 只保留通用能力：MCP 公共工具、异步任务提交、信息补齐和技能管理。
    # 业务读写工具应只挂到对应子 Agent 的 YAML 配置中。
    mcp_tools = await load_main_mcp_tools()
    main_tools = [
        *mcp_tools,
        *async_tools,
        request_additional_info,
        read_word_document,
        *skill_management_tools,
    ]
    _ensure_unique_tool_names(main_tools)

    delegation_instructions = (
        get_async_subagent_instructions() if sandbox_enabled else ""
    )
    full_system_prompt = (
        f"{system_prompt}\n\n{delegation_instructions}"
        if delegation_instructions
        else system_prompt
    )

    # 中间件顺序具有语义：before_* 按声明顺序运行，after_* 反序运行。
    return create_deep_agent(
        model=get_main_model(),
        system_prompt=full_system_prompt,
        memory=[AGENTS_MD_FILENAME],
        tools=main_tools,
        subagents=[*sync_subagents, *async_subagents],
        middleware=[
            ContextInjectionMiddleware(),
            *skill_middleware,
            # 自动摘要 + 主动压缩 + 模型/工具调用上限。
            *build_agent_protection_middleware(
                backend=backend,
                summary_model=get_summary_model(),
                model_run_limit=MAIN_AGENT_MODEL_RUN_LIMIT,
                tool_run_limit=MAIN_AGENT_TOOL_RUN_LIMIT,
                enable_compaction_tool=True,
            ),
            # after_agent 阶段反序执行，最后写回用户长期偏好。
            MemoryUpdateMiddleware(get_summary_model()),
        ],
        skills=skills_sources,
        backend=backend,
        store=store,
        checkpointer=checkpointer,
        context_schema=HarnessContext,
    )
