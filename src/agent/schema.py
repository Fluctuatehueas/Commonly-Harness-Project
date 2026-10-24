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


class Session(BaseModel):
    """表示侧边栏中展示的一条用户会话。"""

    thread_id: str = Field(..., description="会话 ID")
    title: str = Field(..., description="由首条用户消息生成的会话标题")
    created_at: datetime = Field(..., description="会话创建时间")
    updated_at: datetime = Field(..., description="最后一次消息写入时间")
    message_count: int = Field(0, ge=0, description="会话中的展示消息数")


class SessionListResponse(BaseModel):
    """返回当前用户可访问的会话列表。"""

    sessions: List[Session] = Field(default_factory=list, description="按更新时间倒序的会话")


class SessionMessagesResponse(BaseModel):
    """返回恢复单个会话所需的完整展示消息。"""

    thread_id: str = Field(..., description="会话 ID")
    messages: List[Message] = Field(default_factory=list, description="按原顺序排列的消息")
    interrupt: Optional[Dict[str, Any]] = Field(None, description="等待恢复的中断")


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
