"""模型与 PostgreSQL 异步持久化的集中配置。"""

# ============================================================
# 模型配置
# ============================================================
import asyncio # Windows 事件循环策略，以及提供异步 API。
import hashlib
import json
import logging
import os
import shutil
import sys # 判断平台用
import tempfile
from contextlib import AsyncExitStack # 异步上下文管理器栈，用于在异常/取消时成对回收多个资源。
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus # 构造数据库 URI 时对用户名/密码/库名做 URL 编码（密码里含 @、:、/ 等字符时不会破坏 URI）。
# 第三方库
from dotenv import load_dotenv
from langchain_core.rate_limiters import InMemoryRateLimiter
from langchain_deepseek import ChatDeepSeek

# 这是一个全局副作用，只在 Windows 生效，Linux/macOS 走 sys.platform 判断直接跳过。
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

PROJECT_ROOT = Path(__file__).resolve().parents[2]  # resolve：转换为绝对路径，目的是找到.env 文件
# override=False 表示外部环境变量优先于 .env 文件，便于容器/CI 注入配置；
# 本地开发时 .env 仍会补齐缺失项。
load_dotenv(PROJECT_ROOT / ".env", override=False)

logger = logging.getLogger(__name__)

# 前端上传的上下文附件（图片/文档/文本）落盘位置。
# 目录布局：<CONTEXT_ROOT>/<user_id>/<file_id>/<原始文件名>
CONTEXT_ROOT = Path(
    os.getenv("HARNESS_CONTEXT_DIR") or PROJECT_ROOT / ".harness" / "context"
).resolve()


# ============================================================
# Agent相关模型设置
# ============================================================
# 主 Agent 模型
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL")
DEEPSEEK_MODEL_NAME = os.getenv("DEEPSEEK_MODEL_NAME", "deepseek-v4-flash")  # 默认值为 deepseek-v4-flash
# 模型思考/推理模式开关：true/1/yes/on 视为开启，默认关闭。
MODEL_THINKING_ENABLED = os.getenv("HARNESS_MODEL_THINKING", "").lower() in {"1", "true", "yes", "on"}
# 主模型是否支持图片输入。langchain_deepseek 自带的静态 ModelProfile 对较新模型
# 可能标记为 image_inputs=False，deepagents 会据此把 HumanMessage 里的 image_url
# 块替换成占位文本，导致模型只能看到文件名。这里显式声明能力覆盖静态档案。
MODEL_IMAGE_INPUTS = os.getenv("HARNESS_MODEL_IMAGE_INPUTS", "true").strip().lower() not in {
    "0",
    "false",
    "off",
    "no",
}

# ------------------------------------------------------------
# 模型客户端限流与重试
# ------------------------------------------------------------
# requests_per_second=0 表示关闭限流；默认 1 次/秒、突发 1，避免瞬时并发打满厂商连接。
MODEL_REQUESTS_PER_SECOND = float(os.getenv("HARNESS_MODEL_REQUESTS_PER_SECOND", "1"))
MODEL_RATE_LIMIT_BURST = int(os.getenv("HARNESS_MODEL_RATE_LIMIT_BURST", "1"))
# OpenAI 兼容客户端的 429 重试次数（指数退避，服务端有 Retry-After 时优先遵循）。
MODEL_MAX_RETRIES = int(os.getenv("HARNESS_MODEL_MAX_RETRIES", "5"))
# 默认为开启：主 Agent 需要在一条消息里并发委派多个子 Agent 实例。
# 如果并行 HITL 卡片带来困扰，可显式设置 HARNESS_MODEL_PARALLEL_TOOL_CALLS=0 关闭。
MODEL_PARALLEL_TOOL_CALLS = os.getenv(
    "HARNESS_MODEL_PARALLEL_TOOL_CALLS", "1"
).strip().lower() in {"1", "true", "yes", "on"}

_MODEL_RATE_LIMITER: InMemoryRateLimiter | None = None

# ------------------------------------------------------------
# 网络搜索服务配置（DeepSeek 官方搜索 API）
# ------------------------------------------------------------
DEEPSEEK_WEB_SEARCH_API_KEY = os.getenv("DEEPSEEK_WEB_SEARCH_API_KEY")
DEEPSEEK_SEARCH_BASE_URL = os.getenv("DEEPSEEK_SEARCH_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_SEARCH_TIMEOUT = float(os.getenv("DEEPSEEK_SEARCH_TIMEOUT", "15"))
# 网络搜索总开关：关闭后 search_web / fetch_web 不再绑定到 Agent。
# 由 /settings/model 热更新，前端聊天框的「联网搜索」开关即写这个值。
WEB_SEARCH_ENABLED = os.getenv("HARNESS_WEB_SEARCH", "true").strip().lower() not in {
    "0",
    "false",
    "off",
    "no",
}


# ------------------------------------------------------------
# 宿主能力（本机执行 / 项目外读写）配置
# ------------------------------------------------------------
def _env_flag(name: str, default: bool = False) -> bool:
    """解析布尔环境变量；空串按默认值处理。"""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"", "0", "false", "off", "no"}


def _env_list(name: str) -> list[str]:
    """解析分号/逗号分隔的列表。"""
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return []
    parts = raw.replace(";", ",").split(",")
    return [item.strip() for item in parts if item.strip()]


def _env_paths(name: str) -> list[Path]:
    """解析路径列表；Windows 路径可能包含冒号，因此只用分号/逗号分隔。"""
    return [Path(item).expanduser().resolve() for item in _env_list(name)]


# 总开关：关闭时不注册任何 host 工具，模型完全看不到。
HOST_ACCESS_ENABLED = _env_flag("HARNESS_HOST_ACCESS_ENABLED", False)
# 分项开关：仅在总开关打开时生效。
HOST_EXEC_ENABLED = _env_flag("HARNESS_HOST_EXEC_ENABLED", True)
HOST_READ_ENABLED = _env_flag("HARNESS_HOST_READ_ENABLED", True)
HOST_WRITE_ENABLED = _env_flag("HARNESS_HOST_WRITE_ENABLED", False)

# 执行限制
HOST_EXEC_TIMEOUT = int(os.getenv("HARNESS_HOST_EXEC_TIMEOUT", "120"))
HOST_EXEC_MAX_OUTPUT = int(os.getenv("HARNESS_HOST_EXEC_MAX_OUTPUT", "30000"))
HOST_EXEC_DENY_COMMANDS = _env_list("HARNESS_HOST_EXEC_DENY_COMMANDS")

# 读写限制
HOST_READ_MAX_MB = int(os.getenv("HARNESS_HOST_READ_MAX_MB", "10"))
HOST_WRITE_MAX_MB = int(os.getenv("HARNESS_HOST_WRITE_MAX_MB", "10"))
# 允许写入项目外的根目录；为空表示项目外一律禁止写入。
HOST_WRITE_ROOTS = _env_paths("HARNESS_HOST_WRITE_ROOTS")
# 额外拒绝路径（在默认系统/凭据目录之外）。
HOST_DENY_EXTRA_PATHS = _env_paths("HARNESS_HOST_DENY_PATHS")


def get_model_rate_limiter() -> InMemoryRateLimiter | None:
    """全进程共享的模型请求限流器。

    主模型、摘要模型、视觉模型共用同一个桶，保证 Python 作为客户端
    不会同时向厂商打过多请求。
    """
    global _MODEL_RATE_LIMITER
    if MODEL_REQUESTS_PER_SECOND <= 0:
        return None
    if _MODEL_RATE_LIMITER is None:
        _MODEL_RATE_LIMITER = InMemoryRateLimiter(
            requests_per_second=MODEL_REQUESTS_PER_SECOND,
            check_every_n_seconds=0.05,
            max_bucket_size=MODEL_RATE_LIMIT_BURST,
        )
    return _MODEL_RATE_LIMITER


def _model_kwargs() -> dict[str, object]:
    """统一请求参数：默认关闭并行工具调用。"""
    return {"parallel_tool_calls": MODEL_PARALLEL_TOOL_CALLS}

# 视觉子 Agent 专用模型：未单独配置时回退到主模型。
# 三个变量都缺省时 get_vision_model() 直接复用主模型单例。
VISION_MODEL_NAME = os.getenv("HARNESS_VISION_MODEL_NAME")
VISION_BASE_URL = os.getenv("HARNESS_VISION_BASE_URL")
VISION_API_KEY = os.getenv("HARNESS_VISION_API_KEY")

# 模型在首次构图/调用时才创建：缺少 API Key 时应用仍可导入并启动只读接口，
# 真正的模型调用会给出明确错误。
# 配置按用户隔离后，模型对象以 ``角色:用户:配置指纹`` 缓存，同一用户复用、
# 不同用户互不覆盖；系统设置保存时整体清空。
_MODEL_CACHE: dict[str, ChatDeepSeek] = {}


def _require_model_credentials(api_key: str | None) -> None:
    """模型调用前确认凭据已配置。"""
    if not api_key:
        raise RuntimeError("未配置 DEEPSEEK_API_KEY，无法调用模型服务")


def _api_key_fingerprint(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12] if api_key else "none"


def build_model(
    settings: dict[str, Any],
    *,
    role: str = "main",
    user_id: str = "",
) -> ChatDeepSeek:
    """按一组生效配置构建模型客户端；同配置按用户复用。

    ``settings`` 至少包含 ``model_name`` / ``base_url`` / ``api_key`` /
    ``thinking_enabled``；Agent 档案可以用自己的配置覆盖用户默认值。
    """
    model_name = str(settings.get("model_name") or "")
    base_url = str(settings.get("base_url") or "")
    api_key = str(settings.get("api_key") or "")
    thinking = bool(settings.get("thinking_enabled"))
    if role == "judge" and not JUDGE_THINKING:
        # 判官只做局部比对：默认关闭思考，避免回退主模型时也走重推理。
        thinking = False
    _require_model_credentials(api_key)
    key = ":".join(
        [
            role,
            user_id or "-",
            model_name,
            base_url,
            _api_key_fingerprint(api_key),
            "think" if thinking else "fast",
        ]
    )
    cached = _MODEL_CACHE.get(key)
    if cached is not None:
        return cached
    kwargs: dict[str, Any] = {
        "model": model_name,
        "extra_body": {"thinking": {"type": "enabled" if thinking else "disabled"}},
        "api_key": api_key,
        "base_url": base_url or None,
        "max_retries": MODEL_MAX_RETRIES,
        "rate_limiter": get_model_rate_limiter(),
        "model_kwargs": _model_kwargs(),
    }
    if role == "summary":
        kwargs["temperature"] = 0.3
    elif role == "judge":
        # 判官只做局部比对：低温、短输出，避免自由发挥与成本失控。
        kwargs["temperature"] = JUDGE_TEMPERATURE
        kwargs["max_tokens"] = JUDGE_MAX_TOKENS
    client = ChatDeepSeek(**kwargs)
    if MODEL_IMAGE_INPUTS:
        # 覆盖静态档案里的 image_inputs=False，否则 deepagents 的 Filesystem 中间件
        # 会把图片块替换为 “[... does not support image content ...]” 占位文本。
        client.profile = {**(client.profile or {}), "image_inputs": True}
    _MODEL_CACHE[key] = client
    return client


def _user_id_or_current(user_id: str | None) -> str:
    if user_id:
        return user_id
    from agent.identity import current_user_id

    return current_user_id()


def get_main_model(user_id: str | None = None) -> ChatDeepSeek:
    """返回当前用户生效的主 Agent 模型。"""
    from agent.user_data import model_settings

    resolved_user = _user_id_or_current(user_id)
    return build_model(
        model_settings(resolved_user),
        role="main",
        user_id=resolved_user,
    )


def get_summary_model(user_id: str | None = None) -> ChatDeepSeek:
    """返回当前用户生效的摘要模型；与主模型共用配置。"""
    from agent.user_data import model_settings

    resolved_user = _user_id_or_current(user_id)
    return build_model(
        model_settings(resolved_user),
        role="summary",
        user_id=resolved_user,
    )


def get_judge_model(user_id: str | None = None) -> ChatDeepSeek:
    """返回验证判官模型；未配置独立模型时回退当前用户的主模型配置。"""
    from agent.user_data import model_settings

    resolved_user = _user_id_or_current(user_id)
    settings = model_settings(resolved_user)
    if JUDGE_MODEL_NAME:
        settings = {
            **settings,
            "model_name": JUDGE_MODEL_NAME,
            "base_url": JUDGE_BASE_URL or settings.get("base_url", ""),
            "api_key": JUDGE_API_KEY or settings.get("api_key", ""),
        }
    return build_model(settings, role="judge", user_id=resolved_user)


def get_vision_model(user_id: str | None = None) -> ChatDeepSeek:
    """返回视觉子 Agent 专用模型。

    ``HARNESS_VISION_MODEL_NAME`` 未配置时回退到当前用户的主模型。
    """
    resolved_user = _user_id_or_current(user_id)
    if not VISION_MODEL_NAME:
        return get_main_model(resolved_user)
    api_key = VISION_API_KEY or os.getenv("DEEPSEEK_API_KEY") or ""
    base_url = VISION_BASE_URL or os.getenv("DEEPSEEK_BASE_URL") or ""
    return build_model(
        {
            "model_name": VISION_MODEL_NAME,
            "base_url": base_url,
            "api_key": api_key,
            "thinking_enabled": MODEL_THINKING_ENABLED,
        },
        role="vision",
        user_id=resolved_user,
    )


def context_limit_for(user_id: str | None = None) -> int:
    """该用户生效的上下文窗口上限。"""
    from agent.user_data import model_settings

    return int(model_settings(_user_id_or_current(user_id))["context_limit"])


def reload_model_config() -> None:
    """重新读取 .env 并重置模型单例（模型热更新入口）。

    调用方（api/settings）先写 .env 再调本函数；override=True 强制以文件为准。
    已缓存的 Agent 图仍持有旧模型引用，需同时调用 AgentLoader.reset_agents()。
    """
    global DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL_NAME
    global MODEL_THINKING_ENABLED, MODEL_IMAGE_INPUTS, WEB_SEARCH_ENABLED, MODEL_CONTEXT_LIMIT
    global VISION_MODEL_NAME, VISION_BASE_URL, VISION_API_KEY
    global _MODEL_RATE_LIMITER
    global CONTEXT_COMPACT_ENABLED, CONTEXT_COMPACT_TRIGGER, CONTEXT_COMPACT_HARD
    global CONTEXT_COMPACT_KEEP, CONTEXT_COMPACT_MIN_ROUNDS
    global CONTEXT_COMPACT_COOLDOWN, CONTEXT_COMPACT_MAX_DEFER
    global CONTEXT_COMPACT_TIMEOUT, CONTEXT_COMPACT_EVAL_ENABLED
    global CONTEXT_COMPACT_EVAL_JUDGE, CONTEXT_COMPACT_HEARTBEAT
    global CONTEXT_ALERT_THRESHOLD
    global CONTEXT_AUTOTUNE_ENABLED, CONTEXT_AUTOTUNE_INTERVAL
    global COMPACTION_SIGNING_KEY
    load_dotenv(PROJECT_ROOT / ".env", override=True)
    _MODEL_RATE_LIMITER = None
    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
    DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL")
    DEEPSEEK_MODEL_NAME = os.getenv("DEEPSEEK_MODEL_NAME", "deepseek-v4-flash")
    MODEL_THINKING_ENABLED = os.getenv("HARNESS_MODEL_THINKING", "").lower() in {"1", "true", "yes", "on"}
    MODEL_IMAGE_INPUTS = os.getenv("HARNESS_MODEL_IMAGE_INPUTS", "true").strip().lower() not in {
        "0",
        "false",
        "off",
        "no",
    }
    MODEL_CONTEXT_LIMIT = int(os.getenv("HARNESS_MODEL_CONTEXT_LIMIT", "128000"))
    WEB_SEARCH_ENABLED = os.getenv("HARNESS_WEB_SEARCH", "true").strip().lower() not in {
        "0",
        "false",
        "off",
        "no",
    }
    VISION_MODEL_NAME = os.getenv("HARNESS_VISION_MODEL_NAME")
    VISION_BASE_URL = os.getenv("HARNESS_VISION_BASE_URL")
    VISION_API_KEY = os.getenv("HARNESS_VISION_API_KEY")
    # 用户级模型缓存整体失效，下一次构图按各自配置重建。
    _MODEL_CACHE.clear()
    # 上下文压缩参数同样支持热更新；重建 Agent 图后新阈值生效。
    CONTEXT_ALERT_THRESHOLD = float(os.getenv("HARNESS_CONTEXT_ALERT_THRESHOLD", "0.70"))
    CONTEXT_COMPACT_ENABLED = _env_flag("HARNESS_CONTEXT_COMPACT_ENABLED", True)
    CONTEXT_COMPACT_TRIGGER = float(os.getenv("HARNESS_CONTEXT_COMPACT_TRIGGER", "0.70"))
    CONTEXT_COMPACT_HARD = float(os.getenv("HARNESS_CONTEXT_COMPACT_HARD", "0.88"))
    CONTEXT_COMPACT_KEEP = float(os.getenv("HARNESS_CONTEXT_COMPACT_KEEP", "0.25"))
    CONTEXT_COMPACT_MIN_ROUNDS = int(os.getenv("HARNESS_CONTEXT_COMPACT_MIN_ROUNDS", "6"))
    CONTEXT_COMPACT_COOLDOWN = int(os.getenv("HARNESS_CONTEXT_COMPACT_COOLDOWN", "3"))
    CONTEXT_COMPACT_MAX_DEFER = int(os.getenv("HARNESS_CONTEXT_COMPACT_MAX_DEFER", "3"))
    CONTEXT_COMPACT_TIMEOUT = int(os.getenv("HARNESS_CONTEXT_COMPACT_TIMEOUT", "60"))
    CONTEXT_COMPACT_EVAL_ENABLED = _env_flag("HARNESS_CONTEXT_COMPACT_EVAL_ENABLED", True)
    CONTEXT_COMPACT_EVAL_JUDGE = _env_flag("HARNESS_CONTEXT_EVAL_JUDGE", False)
    CONTEXT_COMPACT_HEARTBEAT = int(os.getenv("HARNESS_CONTEXT_COMPACT_HEARTBEAT", "5"))
    CONTEXT_AUTOTUNE_ENABLED = _env_flag("HARNESS_CONTEXT_AUTOTUNE_ENABLED", True)
    CONTEXT_AUTOTUNE_INTERVAL = int(os.getenv("HARNESS_CONTEXT_AUTOTUNE_INTERVAL", "20"))
    COMPACTION_SIGNING_KEY = os.getenv("HARNESS_COMPACTION_SIGNING_KEY", "")


# ============================================================
# 上下文窗口监控与轨迹记录
# ============================================================
# 模型上下文窗口上限（tokens）。既是展示口径，也是压缩判定分母；
# 不同模型可通过环境变量覆盖。
MODEL_CONTEXT_LIMIT = int(os.getenv("HARNESS_MODEL_CONTEXT_LIMIT", "128000"))
# 上下文占用告警阈值（0~1）；与软水位对齐，只写日志，不改变 Agent 状态。
CONTEXT_ALERT_THRESHOLD = float(os.getenv("HARNESS_CONTEXT_ALERT_THRESHOLD", "0.70"))

# ---------- 上下文压缩 ----------
# 是否启用自动压缩；关闭后行为退化为“不压缩”。
CONTEXT_COMPACT_ENABLED = _env_flag("HARNESS_CONTEXT_COMPACT_ENABLED", True)
# 软水位 / 硬水位 / 保留预算，均相对可用预算 B = window - reserve。
CONTEXT_COMPACT_TRIGGER = float(os.getenv("HARNESS_CONTEXT_COMPACT_TRIGGER", "0.70"))
CONTEXT_COMPACT_HARD = float(os.getenv("HARNESS_CONTEXT_COMPACT_HARD", "0.88"))
CONTEXT_COMPACT_KEEP = float(os.getenv("HARNESS_CONTEXT_COMPACT_KEEP", "0.25"))
# 少于该轮次不压缩，避免短会话频繁压缩。
CONTEXT_COMPACT_MIN_ROUNDS = int(os.getenv("HARNESS_CONTEXT_COMPACT_MIN_ROUNDS", "6"))
# 距离上次压缩至少间隔的模型调用次数。
CONTEXT_COMPACT_COOLDOWN = int(os.getenv("HARNESS_CONTEXT_COMPACT_COOLDOWN", "3"))
# 命中软水位但信号不宜压缩时，最多延迟次数；超过后强制压缩。
CONTEXT_COMPACT_MAX_DEFER = int(os.getenv("HARNESS_CONTEXT_COMPACT_MAX_DEFER", "3"))
# 摘要调用超时（秒）。
CONTEXT_COMPACT_TIMEOUT = int(os.getenv("HARNESS_CONTEXT_COMPACT_TIMEOUT", "60"))
# 压缩评估开关（v1 为规则评估，零额外模型调用）。
CONTEXT_COMPACT_EVAL_ENABLED = _env_flag("HARNESS_CONTEXT_COMPACT_EVAL_ENABLED", True)
# LLM 时机裁判（v1.5）：额外一次摘要模型调用，默认关闭。
CONTEXT_COMPACT_EVAL_JUDGE = _env_flag("HARNESS_CONTEXT_EVAL_JUDGE", False)
# 压缩进度心跳间隔（秒），用于长时间摘要期间维持 SSE 活跃与前端断线检测。
CONTEXT_COMPACT_HEARTBEAT = int(os.getenv("HARNESS_CONTEXT_COMPACT_HEARTBEAT", "5"))
# v2 评估驱动自动调参：每累计 N 次压缩评估自动小步调整阈值。
CONTEXT_AUTOTUNE_ENABLED = _env_flag("HARNESS_CONTEXT_AUTOTUNE_ENABLED", True)
CONTEXT_AUTOTUNE_INTERVAL = int(os.getenv("HARNESS_CONTEXT_AUTOTUNE_INTERVAL", "20"))
# 压缩回执可选 HMAC 签名密钥；为空时只做 sha256 完整性摘要。
COMPACTION_SIGNING_KEY = os.getenv("HARNESS_COMPACTION_SIGNING_KEY", "")
# 轨迹事件中单字段（参数 / 结果 / 模型输出）最大保留字符数，防止 Store 行膨胀。
TRAJECTORY_MAX_TEXT = int(os.getenv("HARNESS_TRAJECTORY_MAX_TEXT", "4000"))
# 长期记忆更新是否放到后台执行。同步执行会阻塞本轮 done（多一次模型调用）。
MEMORY_UPDATE_ASYNC = os.getenv("HARNESS_MEMORY_UPDATE_ASYNC", "1").strip().lower() not in {
    "0",
    "false",
    "off",
    "no",
}

# ============================================================
# 消息反馈（赞同 / 不赞同 / 重试）
# ============================================================
# 总开关：关闭后前端隐藏按钮，写入与重试接口直接拒绝。
FEEDBACK_ENABLED = _env_flag("HARNESS_FEEDBACK_ENABLED", True)
# 单用户单日反馈写入上限，防止异常客户端刷量。
FEEDBACK_DAILY_LIMIT = int(os.getenv("HARNESS_FEEDBACK_DAILY_LIMIT", "500"))
# 账本只保留摘要，服务端再次截断，避免客户端提交超长文本。
FEEDBACK_MAX_USER_EXCERPT = int(os.getenv("HARNESS_FEEDBACK_MAX_USER_EXCERPT", "200"))
FEEDBACK_MAX_ANSWER_EXCERPT = int(os.getenv("HARNESS_FEEDBACK_MAX_ANSWER_EXCERPT", "400"))
# 账本保留策略：超过天数或条数后由维护任务清理。
FEEDBACK_RETENTION_DAYS = int(os.getenv("HARNESS_FEEDBACK_RETENTION_DAYS", "180"))
FEEDBACK_MAX_RECORDS = int(os.getenv("HARNESS_FEEDBACK_MAX_RECORDS", "20000"))

# ============================================================
# 运行时异常捕获与重试
# ============================================================
# 只对瞬时异常（网络、模型 5xx/429、连接中断）重试；确定性异常直接失败。
RUNTIME_RETRY_ENABLED = _env_flag("HARNESS_RUNTIME_RETRY_ENABLED", True)
# 首次执行 + 重试的总尝试次数。
RUNTIME_MAX_ATTEMPTS = int(os.getenv("HARNESS_RUNTIME_MAX_ATTEMPTS", "3"))
# 首次重试的退避毫秒数；后续按 2 倍指数增长并带 ±20% 抖动。
RUNTIME_BACKOFF_MS = int(os.getenv("HARNESS_RUNTIME_BACKOFF_MS", "1000"))
# 连续两次重试后 checkpoint 无进展，即判定毒丸并停止重试。
RUNTIME_NO_PROGRESS_LIMIT = int(os.getenv("HARNESS_RUNTIME_NO_PROGRESS_LIMIT", "2"))


# ============================================================
# Agent 运行保护
# ============================================================
# 限制按单次 Agent run 计数；恢复中断（checkpoint resume）会开启新的 run，避免长期会话被历史调用耗尽。
# 0 表示不限制；长任务流里工具调用本来就多，硬上限会把正常执行判成异常。
# 需要防跑飞时再用环境变量打开。
MAIN_AGENT_MODEL_RUN_LIMIT = int(os.getenv("HARNESS_MAIN_AGENT_MODEL_RUN_LIMIT", "0"))
MAIN_AGENT_TOOL_RUN_LIMIT = int(os.getenv("HARNESS_MAIN_AGENT_TOOL_RUN_LIMIT", "0"))
SUBAGENT_MODEL_RUN_LIMIT = int(os.getenv("HARNESS_SUBAGENT_MODEL_RUN_LIMIT", "0"))
SUBAGENT_TOOL_RUN_LIMIT = int(os.getenv("HARNESS_SUBAGENT_TOOL_RUN_LIMIT", "0"))
ASYNC_SUBAGENT_MODEL_RUN_LIMIT = int(
    os.getenv("HARNESS_ASYNC_SUBAGENT_MODEL_RUN_LIMIT", "0")
)
ASYNC_SUBAGENT_TOOL_RUN_LIMIT = int(
    os.getenv("HARNESS_ASYNC_SUBAGENT_TOOL_RUN_LIMIT", "0")
)

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


def reload_sandbox_config() -> None:
    """重新读取 .env 中的沙箱配置（系统设置保存后调用）。

    沙箱管理器在每次新建实例时通过 ``agent.config`` 读取本模块属性，
    因此这里刷新模块级变量即可让新配置生效。
    """
    global OPEN_SANDBOX_HOST, OPEN_SANDBOX_PORT, OPEN_SANDBOX_API_KEY
    global OPEN_SANDBOX_IMAGE, OPEN_SANDBOX_DEFAULT_TIMEOUT_SECONDS
    global OPEN_SANDBOX_SANDBOX_TIMEOUT_SECONDS, OPEN_SANDBOX_PREWARM_ENABLED
    global OPEN_SANDBOX_USE_SERVER_PROXY
    load_dotenv(PROJECT_ROOT / ".env", override=True)
    OPEN_SANDBOX_HOST = os.getenv("OPEN_SANDBOX_HOST", "127.0.0.1")
    OPEN_SANDBOX_PORT = int(os.getenv("OPEN_SANDBOX_PORT", "18083"))
    OPEN_SANDBOX_API_KEY = os.getenv("OPEN_SANDBOX_API_KEY") or None
    OPEN_SANDBOX_IMAGE = os.getenv("OPEN_SANDBOX_IMAGE", "myagent-sandbox:1")
    OPEN_SANDBOX_DEFAULT_TIMEOUT_SECONDS = int(
        os.getenv("OPEN_SANDBOX_DEFAULT_TIMEOUT_SECONDS", "120")
    )
    OPEN_SANDBOX_SANDBOX_TIMEOUT_SECONDS = int(
        os.getenv("OPEN_SANDBOX_SANDBOX_TIMEOUT_SECONDS", "86400")
    )
    OPEN_SANDBOX_PREWARM_ENABLED = os.getenv(
        "OPEN_SANDBOX_PREWARM_ENABLED", "true"
    ).lower() in {"1", "true", "yes", "on"}
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

# 随代码一起分发的内置技能目录。
BUNDLED_SKILLS_ROOT = Path(__file__).parent / "skills"


def _preferred_user_data_dir() -> Path:
    """首选用户数据目录：Windows 为 ``%LOCALAPPDATA%/CommonHarness``。"""
    override = (os.getenv("HARNESS_USER_DATA_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = os.getenv("LOCALAPPDATA") or str(Path.home())
        return Path(base) / "CommonHarness"
    return Path.home() / ".harness"


def _candidate_user_data_dirs() -> list[Path]:
    """用户数据目录候选：首选 AppData，失败时回退到项目或系统临时目录。

    某些受限环境（受控文件夹访问、低权限服务账户、Trae 沙箱）会拒绝对
    ``%LOCALAPPDATA%`` 执行 mkdir（WinError 5）。应用不应因此启动失败。
    """
    preferred = _preferred_user_data_dir()
    candidates = [preferred]
    candidates.append(PROJECT_ROOT / ".harness" / "user-data")
    candidates.append(Path(tempfile.gettempdir()) / "CommonHarness")
    return candidates


def _probe_writable(directory: Path) -> None:
    """实际创建目录并写一个探针文件，确认具备写权限。"""
    directory.mkdir(parents=True, exist_ok=True)
    probe = directory / f".write-probe-{os.getpid()}"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink(missing_ok=True)


_USER_DATA_ERROR = ""


def _resolve_user_data_dir() -> tuple[Path, bool]:
    """返回 (可用目录, 是否降级)。

    逐个尝试候选目录；全部失败时返回最后一个候选并记录错误，
    由调用方在日志和设置页提示用户。
    """
    global _USER_DATA_ERROR
    candidates = _candidate_user_data_dirs()
    for index, candidate in enumerate(candidates):
        try:
            _probe_writable(candidate)
        except OSError as error:
            _USER_DATA_ERROR = f"{type(error).__name__}: {error}"
            logger.warning("用户数据目录不可写，尝试下一个候选 %s（%s）", candidate, _USER_DATA_ERROR)
            continue
        if index > 0:
            logger.warning(
                "默认用户数据目录不可用，已降级使用 %s（原因：%s）",
                candidate,
                _USER_DATA_ERROR,
            )
        return candidate.resolve(), index > 0
    fallback = candidates[-1]
    logger.error("所有用户数据目录均不可写，仍使用 %s", fallback)
    return fallback.resolve(), True


USER_DATA_DIR, USER_DATA_FALLBACK = _resolve_user_data_dir()
USER_DATA_ERROR = _USER_DATA_ERROR


# ============================================================
# 部署基准目录（v1.1 存储结构）
# ============================================================


def _publish_program_path() -> Path | None:
    """发布程序基准目录：只有显式设置 PUBLISH_PROGRAM_PATH 才启用。"""
    raw = (os.getenv("PUBLISH_PROGRAM_PATH") or "").strip()
    if not raw:
        return None
    try:
        return Path(raw).expanduser().resolve()
    except OSError:
        logger.warning("PUBLISH_PROGRAM_PATH 无法解析：%s", raw)
        return None


PUBLISH_PROGRAM_PATH = _publish_program_path()


def _resolve_harness_home() -> Path:
    """可写运行根：部署目录可写时用它，否则回退 USER_DATA_DIR。"""
    if PUBLISH_PROGRAM_PATH is None:
        return USER_DATA_DIR
    try:
        _probe_writable(PUBLISH_PROGRAM_PATH)
    except OSError as error:
        logger.warning(
            "PUBLISH_PROGRAM_PATH 不可写，回退 %s（%s）",
            USER_DATA_DIR,
            type(error).__name__,
        )
        return USER_DATA_DIR
    return PUBLISH_PROGRAM_PATH


HARNESS_HOME = _resolve_harness_home()


def harness_home_info() -> dict[str, Any]:
    """设置页展示用的存储根信息。"""
    return {
        "publish_program_path": (
            str(PUBLISH_PROGRAM_PATH) if PUBLISH_PROGRAM_PATH else ""
        ),
        "harness_home": str(HARNESS_HOME),
        "fallback": PUBLISH_PROGRAM_PATH is None or HARNESS_HOME != PUBLISH_PROGRAM_PATH,
        "user_data_dir": str(USER_DATA_DIR),
    }


def _user_data_dir() -> Path:
    """兼容旧调用点：返回已解析的用户数据目录。"""
    return USER_DATA_DIR


def user_data_info() -> dict[str, Any]:
    """返回用户数据目录的实际位置与降级原因，供设置页展示。"""
    return {
        "user_data_dir": str(USER_DATA_DIR),
        "preferred_dir": str(_preferred_user_data_dir()),
        "fallback": USER_DATA_FALLBACK,
        "error": USER_DATA_ERROR,
    }


# 项目工作区配置见 ``agent.project_store`` / ``agent.project_marker``；
# 旧的全局 workspace.json 由 project_store 在首次启动时迁移。


def _mcp_root() -> Path:
    """MCP 统一管理目录。

    默认跟随 ``HARNESS_HOME``：显式设置 PUBLISH_PROGRAM_PATH 时落在部署目录，
    否则沿用 USER_DATA_DIR；``HARNESS_MCP_ROOT`` 仍可覆盖。
    """
    override = (os.getenv("HARNESS_MCP_ROOT") or "").strip()
    if override:
        root = Path(override).expanduser()
    else:
        root = HARNESS_HOME / "mcp"
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        fallback = USER_DATA_DIR / "mcp"
        fallback.mkdir(parents=True, exist_ok=True)
        logger.warning(
            "MCP 目录创建失败，已回退到 %s（%s）", fallback, type(error).__name__
        )
        root = fallback
    return root.resolve()


MCP_ROOT = _mcp_root()


def _mcp_template_path(root: Path) -> Path:
    """MCP 初始配置模板：新布局优先 template，其次旧的 servers.json。"""
    override = (os.getenv("HARNESS_MCP_CONFIG_PATH") or "").strip()
    if override:
        return Path(override).expanduser()
    candidates = (
        root / "servers.template.json",
        root / "servers.json",
        PROJECT_ROOT / "mcp" / "servers.json",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    # 全新部署且没有模板时，写一份空配置，保证用户首次使用有合法 JSON。
    try:
        root.mkdir(parents=True, exist_ok=True)
        payload = {"version": 2, "servers": {}, "main_servers": []}
        candidates[0].write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        logger.debug("写入 MCP 空模板失败：%s", candidates[0], exc_info=True)
    return candidates[0]


# 所有用户的初始 MCP 配置都从这份模板复制。
MCP_CONFIG_PATH = _mcp_template_path(MCP_ROOT)


def _rag_root() -> Path:
    """RAG Provider 注册表目录。

    集成模式下 Harness 不落知识原文与向量，只保存 Provider 的连接配置；
    源码模式跟随项目 ``rag/``，打包后跟随用户数据目录。
    """
    override = (os.getenv("HARNESS_RAG_ROOT") or "").strip()
    if override:
        root = Path(override).expanduser()
    else:
        frozen = bool(getattr(sys, "frozen", False))
        root = (USER_DATA_DIR / "rag") if frozen else (PROJECT_ROOT / "rag")
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        fallback = Path(tempfile.gettempdir()) / "CommonHarness" / "rag"
        fallback.mkdir(parents=True, exist_ok=True)
        logger.warning(
            "RAG 目录创建失败，已回退到 %s（%s）", fallback, type(error).__name__
        )
        root = fallback
    return root.resolve()


RAG_ROOT = _rag_root()
RAG_CONFIG_PATH = Path(
    (os.getenv("HARNESS_RAG_CONFIG_PATH") or "").strip()
    or RAG_ROOT / "providers.json"
)

_MCP_WORKSPACE_ROOT: Path | None = None


def mcp_workspace_root() -> Path:
    """MCP 服务运行态根目录。

    默认与 MCP_ROOT 同一个可写根（``HARNESS_HOME/mcp``），用户级运行态
    统一落 ``users/{user_id}/runtime/{server_id}``；显式覆盖时仍优先。
    """
    global _MCP_WORKSPACE_ROOT
    if _MCP_WORKSPACE_ROOT is not None:
        return _MCP_WORKSPACE_ROOT

    override = (os.getenv("HARNESS_MCP_WORKSPACE_ROOT") or "").strip()
    if override:
        root = Path(override).expanduser()
        try:
            _probe_writable(root)
        except OSError as error:
            logger.warning(
                "HARNESS_MCP_WORKSPACE_ROOT 不可写，回退 %s（%s）",
                MCP_ROOT,
                type(error).__name__,
            )
            root = MCP_ROOT
    else:
        root = MCP_ROOT

    _MCP_WORKSPACE_ROOT = root.resolve()
    logger.info("MCP 工作目录：%s", _MCP_WORKSPACE_ROOT)
    return _MCP_WORKSPACE_ROOT


def _safe_mcp_dir_name(server_id: str) -> str:
    return "".join(
        char for char in str(server_id) if char.isalnum() or char in {"-", "_"}
    )[:64] or "server"


def _mcp_user_dir(server_id: str) -> Path:
    """MCP 运行态目录按用户隔离：users/{uid}/runtime/{server_id}。"""
    from agent.identity import current_user_id, safe_user_id

    user_dir = safe_user_id(current_user_id())
    return (
        mcp_workspace_root()
        / "users"
        / user_dir
        / "runtime"
        / _safe_mcp_dir_name(server_id)
    )


def mcp_npm_cache_dir(server_id: str) -> Path:
    """npx / npm 型 stdio 服务的专用 npm 缓存目录。

    目录布局：``<MCP 根>/users/<user_id>/runtime/<server_id>/npm-cache``。
    """
    return _mcp_user_dir(server_id) / "npm-cache"


def mcp_stdio_log_path(server_id: str) -> Path:
    """stdio MCP 子进程的 stderr 日志文件（按用户隔离）。"""
    return _mcp_user_dir(server_id) / "logs" / "stdio.log"


def _tree_signature(root: Path) -> str:
    """目录签名：用文件数、总大小与最大 mtime 判断内置技能是否更新。"""
    latest = 0
    total = 0
    count = 0
    try:
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix == ".pyc":
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            latest = max(latest, stat.st_mtime_ns)
            total += stat.st_size
            count += 1
    except OSError:
        pass
    return f"{count}:{total}:{latest}"


def _seed_builtin_skills(root: Path) -> None:
    """把随包技能同步到只读内置目录；随版本变化时整体刷新。"""
    source = BUNDLED_SKILLS_ROOT
    if not source.is_dir():
        root.mkdir(parents=True, exist_ok=True)
        return
    signature = _tree_signature(source)
    marker = root / ".builtin-version"
    try:
        if root.is_dir() and marker.is_file():
            if marker.read_text(encoding="utf-8").strip() == signature:
                return
    except OSError:
        pass

    temporary = root.with_name(f"{root.name}.new-{os.getpid()}")
    try:
        if temporary.exists():
            shutil.rmtree(temporary)
        shutil.copytree(source, temporary)
        (temporary / ".builtin-version").write_text(signature, encoding="utf-8")
        if root.exists():
            shutil.rmtree(root)
        temporary.replace(root)
        logger.info("内置技能已刷新：%s", root)
    except OSError:
        logger.exception("刷新内置技能失败，继续使用现有目录：%s", root)
        try:
            if temporary.exists() and not root.exists():
                shutil.copytree(temporary, root)
        except OSError:
            logger.debug("恢复内置技能临时目录失败", exc_info=True)
        try:
            if temporary.exists():
                shutil.rmtree(temporary)
        except OSError:
            pass
        root.mkdir(parents=True, exist_ok=True)


def _resolve_skills_root() -> Path:
    """内置技能根目录（只读）：``<HARNESS_HOME>/skills/builtin``。

    用户技能不再复制内置技能，统一放在 ``skills/users/{user_id}``，
    读取时先用户、后内置，同名以用户为准。
    """
    override = (os.getenv("HARNESS_SKILLS_ROOT") or "").strip()
    if override:
        root = Path(override).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        logger.info("内置技能目录（HARNESS_SKILLS_ROOT）：%s", root)
        return root.resolve()

    root = HARNESS_HOME / "skills" / "builtin"
    _seed_builtin_skills(root)
    logger.info("内置技能目录：%s", root)
    return root.resolve()


# 技能文件只保存在 HARNESS_HOME 下：builtin 只读，用户技能按 user_id 隔离。
SKILLS_ROOT = _resolve_skills_root()
BUILTIN_SKILLS_ROOT = SKILLS_ROOT
# 兼容旧导入；用户级停用目录由 user_data 按用户计算。
DISABLED_SKILLS_ROOT = HARNESS_HOME / "skills" / "disabled"
MAIN_SKILLS_PATH = "/skills/main/"
BUILTIN_SKILLS_PATH = "/skills-builtin/main/"
SUBAGENT_SKILLS_PATH = "/skills/subagents"

# ============================================================
# 插件系统（v1.1 第一章）
# ============================================================
# 插件目录从 HARNESS_HOME 推导：<HARNESS_HOME>/plugins。
# 发布级（builtin/catalog）只读，用户级按 user_id 隔离。
PLUGINS_ENABLED = _env_flag("HARNESS_PLUGINS_ENABLED", True)
PLUGIN_MARKET_ENABLED = _env_flag("HARNESS_PLUGIN_MARKET_ENABLED", True)
PLUGIN_REGISTRY_URL = (os.getenv("HARNESS_PLUGIN_REGISTRY_URL") or "").strip()
PLUGIN_CATALOG_URL = (os.getenv("HARNESS_PLUGIN_CATALOG_URL") or "").strip()
# 私有/公共目录白名单；非空时只展示和安装列表内的 plugin_id。
PLUGIN_REGISTRY_WHITELIST = _env_list("HARNESS_PLUGIN_REGISTRY_WHITELIST")
PLUGIN_HTTP_TIMEOUT_SECONDS = int(
    os.getenv("HARNESS_PLUGIN_HTTP_TIMEOUT_SECONDS", "10")
)
PLUGIN_DSH_ENABLED = _env_flag("HARNESS_PLUGIN_DSH_ENABLED", True)
PLUGIN_DSH_CATALOG_URL = (os.getenv("HARNESS_PLUGIN_DSH_CATALOG_URL") or "").strip()
PLUGIN_VERSION_KEEP = int(os.getenv("HARNESS_PLUGIN_VERSION_KEEP", "2"))
PLUGIN_CATALOG_CACHE_SECONDS = int(
    os.getenv("HARNESS_PLUGIN_CATALOG_CACHE_SECONDS", "600")
)
PLUGIN_MAX_PROCESSES = int(os.getenv("HARNESS_PLUGIN_MAX_PROCESSES", "8"))
PLUGIN_IDLE_TTL_SECONDS = int(os.getenv("HARNESS_PLUGIN_IDLE_TTL_SECONDS", "300"))
PLUGIN_START_TIMEOUT_SECONDS = int(
    os.getenv("HARNESS_PLUGIN_START_TIMEOUT_SECONDS", "15")
)
PLUGIN_CALL_TIMEOUT_SECONDS = int(
    os.getenv("HARNESS_PLUGIN_CALL_TIMEOUT_SECONDS", "60")
)
PLUGIN_SANDBOX_VERIFY = _env_flag("HARNESS_PLUGIN_SANDBOX_VERIFY", True)
PLUGIN_MAX_PACKAGE_BYTES = int(os.getenv("HARNESS_PLUGIN_MAX_PACKAGE_MB", "32")) * 1024 * 1024
PLUGIN_USER_QUOTA_BYTES = int(os.getenv("HARNESS_PLUGIN_USER_QUOTA_MB", "1024")) * 1024 * 1024
PLUGIN_USER_MAX_PLUGINS = int(os.getenv("HARNESS_PLUGIN_USER_MAX_PLUGINS", "50"))
PLUGIN_AUTO_DISABLE_AFTER_FAILURES = int(
    os.getenv("HARNESS_PLUGIN_AUTO_DISABLE_AFTER_FAILURES", "5")
)
# 宿主版本：用于 manifest.harnessVersion 兼容检查。
HARNESS_VERSION = (os.getenv("HARNESS_VERSION") or "1.1.0").strip()


def _plugin_root() -> Path:
    """插件根目录：显式覆盖优先，否则跟随 HARNESS_HOME。"""
    override = (os.getenv("HARNESS_PLUGIN_ROOT") or "").strip()
    return Path(override).expanduser().resolve() if override else (HARNESS_HOME / "plugins").resolve()


PLUGIN_ROOT = _plugin_root()
PLUGIN_BUILTIN_ROOT = PLUGIN_ROOT / "builtin"
PLUGIN_CATALOG_ROOT = PLUGIN_ROOT / "catalog"
PLUGIN_CATALOG_PATH = PLUGIN_CATALOG_ROOT / "catalog.json"
PLUGIN_USERS_ROOT = PLUGIN_ROOT / "users"


def plugin_user_root(user_id: str) -> Path:
    """用户插件根：``<PLUGIN_ROOT>/users/{uid}``。"""
    from agent.identity import safe_user_id

    return PLUGIN_USERS_ROOT / safe_user_id(user_id)


def plugin_state_path(user_id: str) -> Path:
    """用户级插件状态文件（仅用于缓存/兜底，权威状态在 PG）。"""
    return plugin_user_root(user_id) / "plugins-state.json"


# ============================================================
# PostgreSQL 持久化配置
# ============================================================
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "fluctuate_db")
DB_USER = os.getenv("DB_USER", "fluctuate_user")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_SSLMODE = os.getenv("DB_SSLMODE", "disable") # 默认关闭 SSL，本地/内网方便；公网或云上应改为 require/verify-full。
# PostgreSQL 建连超时：避免数据库不可达时 FastAPI 启动无限挂起。
DB_CONNECT_TIMEOUT = int(os.getenv("DB_CONNECT_TIMEOUT", "5"))
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
    logger.info(
        "连接 PostgreSQL：%s:%s/%s（超时 %ss）",
        DB_HOST,
        DB_PORT,
        DB_NAME,
        DB_CONNECT_TIMEOUT,
    )
    async with AsyncExitStack() as resources:
        store_connection = await AsyncConnection.connect(
            POSTGRES_URI,
            autocommit=True,
            prepare_threshold=0,
            row_factory=dict_row,
            connect_timeout=DB_CONNECT_TIMEOUT,
        )
        resources.push_async_callback(store_connection.close)
        checkpointer_connection = await AsyncConnection.connect(
            POSTGRES_URI,
            autocommit=True,
            prepare_threshold=0,
            row_factory=dict_row, # 查询结果行按字典返回。
            connect_timeout=DB_CONNECT_TIMEOUT,
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


# ============================================================
# Agent 自我迭代与演进
# ============================================================
# 所有演进模块默认开启，作为系统底层规则引擎运行。
# 环境变量只用于关闭某个模块做效果对比、排障或成本控制。
EVOLUTION_ENABLED = _env_flag("HARNESS_EVOLUTION_ENABLED", True)
EVOLUTION_MODE = os.getenv("HARNESS_EVOLUTION_MODE", "auto_low_risk")

EVOLUTION_TRAJECTORY_ENABLED = _env_flag("HARNESS_EVOLUTION_TRAJECTORY", True)
EVOLUTION_REDACTION_ENABLED = _env_flag("HARNESS_EVOLUTION_REDACTION", True)
EVOLUTION_REWARD_ENABLED = _env_flag("HARNESS_EVOLUTION_REWARD", True)
EVOLUTION_TOOL_HEALTH_ENABLED = _env_flag("HARNESS_EVOLUTION_TOOL_HEALTH", True)
EVOLUTION_EXPERIENCE_ENABLED = _env_flag("HARNESS_EVOLUTION_EXPERIENCE", True)
EVOLUTION_EXPERIENCE_INJECTION_ENABLED = _env_flag(
    "HARNESS_EVOLUTION_EXPERIENCE_INJECTION", True
)
EVOLUTION_JUDGE_ENABLED = _env_flag("HARNESS_EVOLUTION_JUDGE", True)
# ============================================================
# 验证（纯观测）
# ============================================================
# 验证只写轨迹与信号，不阻断、不改写、不驱动重做。
VERIFICATION_ENABLED = _env_flag("HARNESS_VERIFICATION_ENABLED", True)
# 默认三个验证点全开：V1 只做 L0（零模型），V2 的 L1 默认 10% 抽样。
VERIFICATION_POINTS = frozenset(
    item.strip()
    for item in os.getenv(
        "HARNESS_VERIFICATION_POINTS",
        "v1_tool,v2_subagent,v3_final",
    ).split(",")
    if item.strip()
)
# V3 在回答放行后后台执行；关闭时在 after_agent 内同步执行（仅调试用）。
VERIFICATION_ASYNC = _env_flag("HARNESS_VERIFICATION_ASYNC", True)
VERIFICATION_MAX_LEVEL = os.getenv("HARNESS_VERIFICATION_MAX_LEVEL", "l2").lower()
VERIFICATION_L1_SAMPLE_RATE = float(
    os.getenv("HARNESS_VERIFICATION_L1_SAMPLE_RATE", "0.1")
)
VERIFICATION_MAX_JUDGE_CALLS = int(
    os.getenv("HARNESS_VERIFICATION_MAX_JUDGE_CALLS", "3")
)
VERIFICATION_MAX_DELEGATIONS = int(
    os.getenv("HARNESS_VERIFICATION_MAX_DELEGATIONS", "1")
)
# L2 自动深评：默认对 V3 最终产出开启；V1/V2 仍只在 fail 时触发。
VERIFICATION_L2_ENABLED = _env_flag("HARNESS_VERIFICATION_L2_ENABLED", True)
# 只有达到该风险等级的最终产出才自动跑 L2，避免闲聊也调用模型。
VERIFICATION_L2_RISK_TIER = os.getenv("HARNESS_VERIFICATION_L2_RISK_TIER", "t1")
VERIFICATION_BLOCKING_SCORE = float(
    os.getenv("HARNESS_VERIFICATION_BLOCKING_SCORE", "0.6")
)
VERIFICATION_MAX_EVIDENCE = int(
    os.getenv("HARNESS_VERIFICATION_MAX_EVIDENCE", "20")
)

# 判官独立配置；未配置模型名时回退当前用户的摘要模型配置。
JUDGE_MODEL_NAME = (os.getenv("HARNESS_JUDGE_MODEL_NAME") or "").strip()
JUDGE_BASE_URL = (os.getenv("HARNESS_JUDGE_BASE_URL") or "").strip()
JUDGE_API_KEY = (os.getenv("HARNESS_JUDGE_API_KEY") or "").strip()
JUDGE_TEMPERATURE = float(os.getenv("HARNESS_JUDGE_TEMPERATURE", "0.1"))
JUDGE_MAX_TOKENS = int(os.getenv("HARNESS_JUDGE_MAX_TOKENS", "800"))
JUDGE_TIMEOUT = float(os.getenv("HARNESS_JUDGE_TIMEOUT", "6"))
# 判官默认关闭思考模式；需要更强推理时显式开启。
JUDGE_THINKING = _env_flag("HARNESS_JUDGE_THINKING", False)
# PDCA 闭环：最终回答未通过自检时，带反馈重做的最大轮次（不含首次执行）。
PDCA_ENABLED = _env_flag("HARNESS_PDCA_ENABLED", True)
PDCA_MAX_ROUNDS = int(os.getenv("HARNESS_PDCA_MAX_ROUNDS", "2"))
# 代码编译：本机已有运行时优先；缺失时转沙箱安装，禁止在本机新建环境。
LOCAL_BUILD_ENABLED = _env_flag("HARNESS_LOCAL_BUILD_ENABLED", True)
EVOLUTION_ATTRIBUTION_ENABLED = _env_flag("HARNESS_EVOLUTION_ATTRIBUTION", True)
EVOLUTION_CONTROL_PLANE_ENABLED = _env_flag(
    "HARNESS_EVOLUTION_CONTROL_PLANE", True
)
EVOLUTION_SHARING_ENABLED = _env_flag("HARNESS_EVOLUTION_SHARING", True)

EVOLUTION_REPLAY_TIER = os.getenv("HARNESS_EVOLUTION_REPLAY_TIER", "approx")
EVOLUTION_KEEP_REASONING = _env_flag("HARNESS_EVOLUTION_KEEP_REASONING", False)
EVOLUTION_EXPERIENCE_MAX_ITEMS = int(
    os.getenv("HARNESS_EVOLUTION_EXPERIENCE_MAX_ITEMS", "200")
)
EVOLUTION_EXPERIENCE_MIN_UTILITY = float(
    os.getenv("HARNESS_EVOLUTION_EXPERIENCE_MIN_UTILITY", "0.2")
)
EVOLUTION_EXPERIENCE_TTL_DAYS = int(
    os.getenv("HARNESS_EVOLUTION_EXPERIENCE_TTL_DAYS", "90")
)
EVOLUTION_EXPERIENCE_INJECT_LIMIT = int(
    os.getenv("HARNESS_EVOLUTION_EXPERIENCE_INJECT_LIMIT", "3")
)
EVOLUTION_JUDGE_RISK_TIER = os.getenv("HARNESS_EVOLUTION_JUDGE_RISK_TIER", "t1")
EVOLUTION_VERIFIER_TIMEOUT = int(
    os.getenv("HARNESS_EVOLUTION_VERIFIER_TIMEOUT", "30")
)
EVOLUTION_REWARD_SAMPLE_RATE = float(
    os.getenv("HARNESS_EVOLUTION_REWARD_SAMPLE_RATE", "1.0")
)
EVOLUTION_TOOL_HEALTH_MIN_SAMPLES = int(
    os.getenv("HARNESS_EVOLUTION_TOOL_HEALTH_MIN_SAMPLES", "20")
)
EVOLUTION_CONTROL_MIN_SAMPLES = int(
    os.getenv("HARNESS_EVOLUTION_CONTROL_MIN_SAMPLES", "3")
)
EVOLUTION_SHADOW_RATE = float(os.getenv("HARNESS_EVOLUTION_SHADOW_RATE", "0.05"))
EVOLUTION_CANARY_RATE = float(os.getenv("HARNESS_EVOLUTION_CANARY_RATE", "0.05"))
EVOLUTION_REQUIRE_HUMAN_APPROVAL = _env_flag(
    "HARNESS_EVOLUTION_REQUIRE_HUMAN_APPROVAL", True
)
EVOLUTION_BUILD_VERSION = os.getenv("HARNESS_BUILD_VERSION", "dev")
EVOLUTION_TENANT_ID = (os.getenv("HARNESS_TENANT_ID") or "").strip()

# ============================================================
# AgentTeam
# ============================================================
# 团队模式总开关：关闭后 group_id 一律忽略，全部按独立 Agent 运行。
AGENT_TEAM_ENABLED = _env_flag("HARNESS_AGENT_TEAM_ENABLED", True)
# 跨组委派最大深度（本组 → 他组 → 再他组）。
AGENT_TEAM_MAX_DEPTH = int(os.getenv("HARNESS_AGENT_TEAM_MAX_DEPTH", "2"))
# 单次跨组委派超时（秒）。
AGENT_TEAM_DELEGATION_TIMEOUT = float(
    os.getenv("HARNESS_AGENT_TEAM_TIMEOUT", "300")
)
# 每用户同时进行的跨组委派上限；忙则拒绝，不排队。
AGENT_TEAM_MAX_CONCURRENT = int(
    os.getenv("HARNESS_AGENT_TEAM_MAX_CONCURRENT", "2")
)
