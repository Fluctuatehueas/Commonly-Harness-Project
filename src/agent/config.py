"""模型与 PostgreSQL 异步持久化的集中配置。"""

# ============================================================
# 模型配置
# ============================================================
import asyncio # Windows 事件循环策略，以及提供异步 API。
import os
import sys # 判断平台用
from contextlib import AsyncExitStack # 异步上下文管理器栈，用于在异常/取消时成对回收多个资源。
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus # 构造数据库 URI 时对用户名/密码/库名做 URL 编码（密码里含 @、:、/ 等字符时不会破坏 URI）。
# 第三方库
from dotenv import load_dotenv
from langchain_deepseek import ChatDeepSeek

# 这是一个全局副作用，只在 Windows 生效，Linux/macOS 走 sys.platform 判断直接跳过。
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

PROJECT_ROOT = Path(__file__).resolve().parents[2]  # resolve：转换为绝对路径，目的是找到.env 文件
# override=False 表示外部环境变量优先于 .env 文件，便于容器/CI 注入配置；
# 本地开发时 .env 仍会补齐缺失项。
load_dotenv(PROJECT_ROOT / ".env", override=False)


# ============================================================
# Agent相关模型设置
# ============================================================
# 主 Agent 模型
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL")
DEEPSEEK_MODEL_NAME = os.getenv("DEEPSEEK_MODEL_NAME", "deepseek-v4-flash")  # 默认值为 deepseek-v4-flash
# 模型思考/推理模式开关：true/1/yes/on 视为开启，默认关闭。
MODEL_THINKING_ENABLED = os.getenv("HARNESS_MODEL_THINKING", "").lower() in {"1", "true", "yes", "on"}

# 模型在首次构图/调用时才创建：缺少 API Key 时应用仍可导入并启动只读接口，
# 真正的模型调用会给出明确错误。
_MAIN_MODEL: ChatDeepSeek | None = None
_SUMMARY_MODEL: ChatDeepSeek | None = None


def _require_model_credentials() -> None:
    """模型调用前确认凭据已配置。"""
    if not DEEPSEEK_API_KEY:
        raise RuntimeError("未配置 DEEPSEEK_API_KEY，无法调用模型服务")


def get_main_model() -> ChatDeepSeek:
    """返回主 Agent 模型（进程内单例）。"""
    global _MAIN_MODEL
    if _MAIN_MODEL is None:
        _require_model_credentials()
        _MAIN_MODEL = ChatDeepSeek(
            model=DEEPSEEK_MODEL_NAME,
            # 思考模式由 HARNESS_MODEL_THINKING 控制，默认关闭。
            extra_body={"thinking": {"type": "enabled" if MODEL_THINKING_ENABLED else "disabled"}},
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
        )
    return _MAIN_MODEL


def get_summary_model() -> ChatDeepSeek:
    """返回会话摘要专用模型（进程内单例）。"""
    global _SUMMARY_MODEL
    if _SUMMARY_MODEL is None:
        _require_model_credentials()
        _SUMMARY_MODEL = ChatDeepSeek(
            model=DEEPSEEK_MODEL_NAME,
            temperature=0.3,  # 摘要要求输出稳定，减少发挥
            extra_body={"thinking": {"type": "enabled" if MODEL_THINKING_ENABLED else "disabled"}},
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
        )
    return _SUMMARY_MODEL


def reload_model_config() -> None:
    """重新读取 .env 并重置模型单例（模型热更新入口）。

    调用方（api/settings）先写 .env 再调本函数；override=True 强制以文件为准。
    已缓存的 Agent 图仍持有旧模型引用，需同时调用 AgentLoader.reset_agents()。
    """
    global DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL_NAME
    global MODEL_THINKING_ENABLED
    global _MAIN_MODEL, _SUMMARY_MODEL
    load_dotenv(PROJECT_ROOT / ".env", override=True)
    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
    DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL")
    DEEPSEEK_MODEL_NAME = os.getenv("DEEPSEEK_MODEL_NAME", "deepseek-v4-flash")
    MODEL_THINKING_ENABLED = os.getenv("HARNESS_MODEL_THINKING", "").lower() in {"1", "true", "yes", "on"}
    _MAIN_MODEL = None
    _SUMMARY_MODEL = None

# ============================================================
# Agent 运行保护
# ============================================================
# 限制按单次 Agent run 计数；恢复中断（checkpoint resume）会开启新的 run，避免长期会话被历史调用耗尽。
MAIN_AGENT_MODEL_RUN_LIMIT = 50  # 单词Agent对话次数
MAIN_AGENT_TOOL_RUN_LIMIT = 50 # 单词Agent可调用工具限制
SUBAGENT_MODEL_RUN_LIMIT = 50 # 子智能体对话次数
SUBAGENT_TOOL_RUN_LIMIT = 50 # 子智能体工具运行限制
ASYNC_SUBAGENT_MODEL_RUN_LIMIT = 50 # 异步子 Agent 的预算。
# 不改变每次新询问重新计数的语义，也继续保留循环保护。
ASYNC_SUBAGENT_TOOL_RUN_LIMIT = 32

# ============================================================
# OpenSandbox 配置
# ============================================================
# API key 只从环境变量读取，避免把凭据写入项目文件或日志。
OPEN_SANDBOX_HOST = os.getenv("OPEN_SANDBOX_HOST", "127.0.0.1")
OPEN_SANDBOX_PORT = int(os.getenv("OPEN_SANDBOX_PORT", "18083"))
OPEN_SANDBOX_API_KEY = os.getenv("OPEN_SANDBOX_API_KEY")
# 先用 sandbox/Dockerfile 构建本地多语言镜像；默认值仅影响新建沙箱。
OPEN_SANDBOX_IMAGE = os.getenv("OPEN_SANDBOX_IMAGE", "myagent-sandbox:1")
OPEN_SANDBOX_DEFAULT_TIMEOUT_SECONDS = int(
    os.getenv("OPEN_SANDBOX_DEFAULT_TIMEOUT_SECONDS", "120")
)
# 沙箱的生命周期24h，沙箱实例本身的生命周期上限，到期回收，防止长期占用资源。
OPEN_SANDBOX_SANDBOX_TIMEOUT_SECONDS = int(
    os.getenv("OPEN_SANDBOX_SANDBOX_TIMEOUT_SECONDS", "86400")
)
# 启动只调度一个无用户预热实例；创建与每次访问续期共用上面的有效期。
# 预热失败退回按需创建，不启动周期保活任务。
OPEN_SANDBOX_PREWARM_ENABLED = os.getenv(
    "OPEN_SANDBOX_PREWARM_ENABLED", "true"
).lower() in {"1", "true", "yes", "on"}
# Windows / Docker Desktop / 远程服务端场景下，客户端通常无法直连沙箱容器 IP，
# 需要让 SDK 通过生命周期服务端代理执行命令和文件操作。
OPEN_SANDBOX_USE_SERVER_PROXY = os.getenv(
    "OPEN_SANDBOX_USE_SERVER_PROXY", "true"
).lower() in {"1", "true", "yes", "on"}

# ============================================================
# 异步子 Agent（Agent Protocol）配置
# ============================================================
# Agent Protocol 服务的地址。异步子 Agent 通过该地址创建 thread/run，
# 主 Agent 只负责提交任务和轮询结果。
ASYNC_AGENT_PROTOCOL_URL = os.getenv(
    "HARNESS_ASYNC_AGENT_PROTOCOL_URL",
    "http://127.0.0.1:18082",
)

# ============================================================
# Agent 文件常量
# ============================================================
# 主 Agent 只读指引文件
AGENTS_MD_FILENAME = "/AGENTS.md"
# 技能文件仅保存在项目内的受限目录。主 Agent 和每个子 Agent 使用独立子目录，
# 分配技能时会把文件从主 Agent 的暂存目录移动到对应子 Agent 的目录。
# 这里是技能的隔离机制，子智能体要保留主智能体的技能文件，必须在主智能体的技能目录里复制一份。
SKILLS_ROOT = Path(__file__).parent / "skills"
MAIN_SKILLS_PATH = "/skills/main/"
SUBAGENT_SKILLS_PATH = "/skills/subagents"

# ============================================================
# PostgreSQL 持久化配置
# ============================================================
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "fluctuate_db")
DB_USER = os.getenv("DB_USER", "fluctuate_user")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_SSLMODE = os.getenv("DB_SSLMODE", "disable") # 默认关闭 SSL，本地/内网方便；公网或云上应改为 require/verify-full。
POSTGRES_URI = (
    f"postgresql://{quote_plus(DB_USER)}:{quote_plus(DB_PASSWORD)}"
    f"@{DB_HOST}:{DB_PORT}/{quote_plus(DB_NAME)}"
    f"?sslmode={quote_plus(DB_SSLMODE)}"
)
# 对用户名、密码、库名、sslmode 做 quote_plus 编码：
# 密码里含 @（如 p@ss）不编码会让 URI 解析在第一个 @ 处截断，连接错主机。
# 密码里的 :、/、#、? 同理。
# quote_plus 还会把空格转成 +，符合 URL 查询串约定。

async def create_async_persistence() -> tuple[Any, Any]:
    """
    创建供异步 Agent 调用使用的 PostgreSQL Store 和 Checkpointer。
    """
    # 函数内延迟导入，好处：
    # 本模块被其他只读配置的代码导入时，不会因为 langgraph/psycopg 未安装而失败。
    # 让"配置读取"与"运行时依赖"解耦，导入成本更可控。
    from langgraph.store.postgres.aio import AsyncPostgresStore # LangGraph 的异步键值存储，保存长期记忆/跨会话数据。
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver # LangGraph 的异步检查点存储，保存图执行状态（用于中断恢复）。
    from psycopg import AsyncConnection # psycopg3 的异步连接。
    from psycopg.rows import dict_row # 行工厂，查询结果每行是 dict 而不是 tuple，方便按列名取值。

    # 任意建连或 setup 失败（包括取消）都回收已经创建的连接；成功后移交给 Loader。
    async with AsyncExitStack() as resources:
        store_connection = await AsyncConnection.connect(
            POSTGRES_URI, autocommit=True, prepare_threshold=0, row_factory=dict_row,
        )
        resources.push_async_callback(store_connection.close)
        checkpointer_connection = await AsyncConnection.connect(
            POSTGRES_URI, autocommit=True, prepare_threshold=0, row_factory=dict_row, # 查询结果行按字典返回。
        )
        resources.push_async_callback(checkpointer_connection.close)
        store = AsyncPostgresStore(conn=store_connection)
        checkpointer = AsyncPostgresSaver(conn=checkpointer_connection)
        # setup 只维护框架表结构，不覆盖历史数据。
        await store.setup()
        await checkpointer.setup()
        resources.pop_all()
        return store, checkpointer # 长期记忆/短期记忆

async def close_async_persistence(store: Any, checkpointer: Any) -> None:
    """
    关闭 Store 和 Checkpointer 各自持有的数据库连接。
    """
    # 连接由 create_async_persistence 创建，服务停止时必须成对释放。
    try:
        await checkpointer.conn.close()
    finally:
        await store.conn.close()
