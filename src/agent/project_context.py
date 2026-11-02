"""一次对话运行期间的项目上下文。

Agent 图按用户缓存，项目路径不能固化在图里；每轮对话开始时把当前项目写入
ContextVar，文件后端据此解析真实根目录。这样切换项目不需要重建 Agent。
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ProjectStatus = Literal["active", "untrusted", "unavailable"]
SessionKind = Literal["free", "project"]


class ProjectContextError(RuntimeError):
    """项目上下文缺失或当前项目不可写时抛出。"""


@dataclass(frozen=True)
class ProjectContext:
    """当前对话绑定的项目。

    Attributes:
        id: 项目稳定 ID。
        name: 项目展示名。
        root: 本机项目根目录（已解析为绝对路径）。
        status: 本轮运行的项目状态；只有 ``active`` 允许写入。
        marker_state: ``.commonharness`` 标记校验结果，用于错误提示。
    """

    id: str
    name: str
    root: Path | None
    kind: SessionKind = "project"
    status: ProjectStatus = "active"
    marker_state: str = "ok"


_CURRENT_PROJECT: ContextVar[ProjectContext | None] = ContextVar(
    "harness_project_context", default=None
)
_CURRENT_THREAD: ContextVar[str] = ContextVar("harness_project_thread", default="")


def set_project_context(project: ProjectContext) -> Token:
    """在 Agent 调用前绑定当前项目，返回用于恢复的 Token。"""
    return _CURRENT_PROJECT.set(project)


def reset_project_context(token: Token) -> None:
    """恢复调用前的项目上下文。"""
    _CURRENT_PROJECT.reset(token)


def set_project_thread(thread_id: str) -> Token:
    """绑定当前会话 ID；Agent 图按用户缓存，工具必须在运行时读取。"""
    return _CURRENT_THREAD.set(str(thread_id or ""))


def reset_project_thread(token: Token) -> None:
    """恢复调用前的会话上下文。"""
    _CURRENT_THREAD.reset(token)


def current_thread_id() -> str:
    """返回当前对话的 thread_id；未绑定时返回空串。"""
    return _CURRENT_THREAD.get()


def current_project_optional() -> ProjectContext | None:
    """返回当前项目；未绑定时返回 None。"""
    return _CURRENT_PROJECT.get()


def current_project() -> ProjectContext:
    """返回当前项目；未绑定时抛错，避免文件操作落到任意目录。"""
    project = _CURRENT_PROJECT.get()
    if project is None:
        raise ProjectContextError("当前对话未绑定项目，已拒绝文件操作")
    return project


def require_writable_project() -> ProjectContext:
    """返回可写项目；标记缺失/目录不可用时不降级，直接拒绝写入。"""
    project = current_project()
    if project.kind != "project" or project.root is None:
        raise ProjectContextError("自由会话没有本机项目目录，文件保留在沙箱")
    if project.status != "active":
        raise ProjectContextError(
            f"项目「{project.name}」当前状态为 {project.status}（标记 {project.marker_state}），"
            "已禁止写入；请先在项目设置中修复目录标记"
        )
    return project


def is_free_session() -> bool:
    """当前对话是否为自由会话（未归属任何项目）。"""
    project = current_project_optional()
    return project is not None and project.kind == "free"


def _safe_sandbox_name(value: str) -> str:
    safe = "".join(
        char for char in str(value or "") if char.isalnum() or char in {"-", "_"}
    )[:64]
    return safe or "default"


def sandbox_session_dir(thread_id: str) -> str:
    """自由会话在沙箱内的独立文件目录。"""
    return f"/workspace/sessions/{_safe_sandbox_name(thread_id)}"


def sandbox_project_dir(project_id: str) -> str:
    """项目会话在沙箱内的工作目录。"""
    return f"/workspace/{_safe_sandbox_name(project_id)}"
