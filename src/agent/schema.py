"""Harness 运行时、对话和会话历史共用的数据模型。

本文件只描述通用概念：用户身份、会话、消息和异步任务。
业务字段应放在接入方的工具参数或 YAML 配置中，不要写进这里。
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator


# ============================================================
# 运行时上下文
# ============================================================


@dataclass
class HarnessContext:
    """主 Agent 的运行时上下文，由 API 层在 invoke 时传入。"""

    user_id: str  # 必填，用户唯一标识，决定记忆隔离范围
    username: str  # 必填，用户姓名或登录名
    # 轨迹记录需要知道当前会话；同一用户的不同 thread 可能并发执行。
    thread_id: str = ""
    # 中间件在每轮开始时生成，用于把同一次 run 的事件归到一个轨迹分组。
    trajectory_run_id: str = ""
    # HITL 恢复：用户在中断卡片里提交内容的摘要与结论（answered/approved/rejected），
    # 由轨迹中间件写进本轮轨迹，供「轨迹」页展示「已回复」记录。
    hitl_summary: str = ""
    hitl_status: str = ""


@dataclass
class AsyncSandboxContext:
    """传给 Agent Protocol 异步图的身份、共享沙箱和子 Agent 名称。"""

    user_id: str
    username: str
    sandbox_id: str
    subagent_name: str


@dataclass
class UserPreferences:
    """持久化在用户 StoreBackend 中的精简个性化偏好。"""

    recent_queries: list[str] = field(default_factory=list)
    preferred: dict[str, Any] = field(default_factory=dict)


# ============================================================
# 对话相关
# ============================================================


class ChatRequest(BaseModel):
    """接收单轮对话请求，并兼容 name 字段。"""

    message: str = Field(..., min_length=1, description="用户消息")
    thread_id: Optional[str] = Field(None, description="会话 ID，为空则创建新会话")
    user_id: str = Field("anonymous", min_length=1, description="用户唯一标识")
    username: Optional[str] = Field(None, min_length=1, description="用户名")
    name: Optional[str] = Field(None, min_length=1, description="username 的兼容字段")
    attachments: List[str] = Field(
        default_factory=list,
        description="上下文附件 ID 列表（先调用 /context/upload 获得）",
    )

    @model_validator(mode="after")
    def set_username(self) -> "ChatRequest":
        """统一 username 和兼容字段，保证调用链始终能读到用户名。"""
        self.username = self.username or self.name or self.user_id
        return self


class ChatResponse(BaseModel):
    """返回非流式对话结果。"""

    thread_id: str = Field(..., description="会话 ID")
    messages: List["Message"] = Field(default_factory=list, description="消息列表")
    user_id: Optional[str] = Field(None, description="用户唯一标识")
    username: Optional[str] = Field(None, description="用户名")
    answer: Optional[str] = Field(None, description="单轮对话回答")
    interrupt: Optional[Dict[str, Any]] = Field(None, description="等待恢复的中断")


class ResumeChatRequest(BaseModel):
    """接收中断恢复数据，并传给 LangGraph ``Command(resume=...)``。"""

    resume: Dict[str, Any] = Field(..., description="补充信息或人工审批决策")
    user_id: str = Field("anonymous", min_length=1, description="用户唯一标识")
    username: Optional[str] = Field(None, min_length=1, description="用户名")

    @model_validator(mode="after")
    def set_username(self) -> "ResumeChatRequest":
        """未显式提供用户名时退回用户 ID。"""
        self.username = self.username or self.user_id
        return self


class AsyncTaskStatusResponse(BaseModel):
    """返回异步子 Agent 任务的状态及其主会话投递状态。"""

    task_id: str = Field(..., description="Agent Protocol 任务线程 ID")
    status: str = Field(..., description="任务运行状态")
    done: bool = Field(..., description="任务是否已进入终态")
    delivered: bool = Field(False, description="终态结果是否已写入主会话")
    result: Optional[str] = Field(None, description="终态任务结果文本")
    error: Optional[str] = Field(None, description="失败原因")
    run_id: Optional[str] = Field(None, description="最近一次运行 ID")
    updated_at: Optional[str] = Field(None, description="最近更新时间")


# ============================================================
# 消息与会话
# ============================================================


class Message(BaseModel):
    """表示前端展示的一条用户、助手、工具或委派消息。"""

    id: str = Field(..., description="消息唯一标识")
    role: str = Field(..., description="消息角色: user/assistant/tool/delegation")
    content: str = Field("", description="消息内容")
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")
    tool_calls: Optional[List[Dict[str, Any]]] = Field(None, description="工具调用信息")
    tool_call_id: Optional[str] = Field(None, description="工具调用 ID")
    source: Optional[str] = Field(None, description="消息来源: main 或子 Agent 名称")
    # 以下字段只用于工具消息，使历史恢复后的展示与 SSE 过程一致。
    tool_name: Optional[str] = Field(None, description="工具名称")
    tool_status: Optional[str] = Field(None, description="工具状态: calling / done / error")
    text: Optional[str] = Field(None, description="工具结果文本")
    args: Optional[str] = Field(None, description="工具调用参数")
    async_task_id: Optional[str] = Field(None, description="可继续轮询的远程异步任务 ID")
    # 助手消息：本轮最终回答的 Token 消耗（来自 TokenUsageMiddleware 统计）。
    tokens: Optional[Dict[str, int]] = Field(None, description="单轮 Token 消耗 input/output/total")


class Session(BaseModel):
    """表示侧边栏中展示的一条用户会话。"""

    thread_id: str = Field(..., description="会话 ID")
    title: str = Field(..., description="由首条用户消息生成的会话标题")
    created_at: datetime = Field(..., description="会话创建时间")
    updated_at: datetime = Field(..., description="最后一次消息写入时间")
    message_count: int = Field(0, ge=0, description="会话中的展示消息数")
    pinned: bool = Field(False, description="是否置顶")
    # 会话归属的项目（工作区目录）ID；null 表示归入「对话」分组。
    project_id: Optional[str] = Field(None, description="所属项目 ID")


class SessionUpdateRequest(BaseModel):
    """会话重命名 / 置顶 / 归属项目请求；None 表示不修改该字段。"""

    title: Optional[str] = Field(
        None, min_length=1, max_length=120, description="新的会话标题"
    )
    pinned: Optional[bool] = Field(None, description="是否置顶")
    # 显式传 null 表示移回「对话」组；未传则保持原归属。
    project_id: Optional[str] = Field(None, description="所属项目 ID，null 表示移出项目")


class SessionListResponse(BaseModel):
    """返回当前用户可访问的会话列表。"""

    sessions: List[Session] = Field(default_factory=list, description="按更新时间倒序的会话")


class SessionMessagesResponse(BaseModel):
    """返回恢复单个会话所需的完整展示消息。"""

    thread_id: str = Field(..., description="会话 ID")
    messages: List[Message] = Field(default_factory=list, description="按原顺序排列的消息")
    interrupt: Optional[Dict[str, Any]] = Field(None, description="等待恢复的中断")
    session_usage: Dict[str, int] = Field(
        default_factory=dict, description="会话累计 Token 消耗 input/output/total"
    )


class DeleteSessionResponse(BaseModel):
    """返回删除会话后的操作结果。"""

    success: bool = Field(..., description="是否已删除")


ChatResponse.model_rebuild()


# ============================================================
# 进程内运行时状态
# ============================================================


@dataclass(frozen=True)
class AsyncTaskBinding:
    """记录远程异步任务归属的主会话，用于完成后回写 checkpoint。"""

    task_id: str
    user_id: str
    username: str
    thread_id: str


@dataclass
class UserGroup:
    """当前进程中的用户分组索引。

    对话状态和 memories 由 PostgreSQL checkpoint/store 持久化；此结构只
    负责在当前进程内归拢用户的 Agent 实例和会话 ID。
    """

    username: str  # 用户最近一次使用的名称
    thread_ids: set[str] = field(default_factory=set)  # 用户关联的会话 ID
    agent: Any | None = None  # 用户专属的 Agent 实例

# ============================================================
# 网络搜索数据模型
# ============================================================


@dataclass
class SearchResult:
    """单条搜索结果。"""
    rerank_score: float
    title: str
    url: str
    snippet: str = ""
    summary: str = ""
    content: str = ""
    site_name: Optional[str] = None
    site_icon: Optional[str] = None
    date: Optional[str] = None
    authority: int = -1

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SearchResult":
        def _float(value: Any, default: float = 0.0) -> float:
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        def _int(value: Any, default: int = -1) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        def _str(value: Any) -> str:
            return "" if value is None else str(value)

        return cls(
            rerank_score=_float(d.get("rerank_score")),
            title=_str(d.get("title")),
            url=_str(d.get("url")),
            snippet=_str(d.get("snippet")),
            summary=_str(d.get("summary")),
            content=_str(d.get("content")),
            site_name=d.get("site_name") or None,
            site_icon=d.get("site_icon") or None,
            date=d.get("date") or None,
            authority=_int(d.get("authority")),
        )

    def to_dict(self, *, include_content: bool = True) -> dict[str, Any]:
        """转成工具可直接返回的原生类型，避免数字被字符串化。"""
        data: dict[str, Any] = {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "summary": self.summary,
            "site_name": self.site_name or "",
            "date": self.date or "",
            "authority": self.authority,
            "rerank_score": self.rerank_score,
            "is_quality": self.is_quality,
        }
        if include_content:
            data["content"] = self.content
        return data

    @property
    def is_quality(self) -> bool:
        """rerank_score >= 1.5 为优质结果；authority >= 3 为极权威。-1表示未知"""
        return self.rerank_score >= 1.5 or self.authority >= 3


@dataclass
class SearchResponse:
    """一次搜索的完整结果。"""
    results: list[SearchResult] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, *, include_content: bool = True) -> dict[str, Any]:
        return {
            "results": [
                item.to_dict(include_content=include_content)
                for item in self.results
            ],
            "count": len(self.results),
        }


class SearchServiceError(Exception):
    """搜索服务统一异常，携带原始错误信息。"""

    def __init__(self, message: str, *, code: str | None = None,
                 status: int | None = None):
        super().__init__(message)
        self.code = code
        self.status = status
