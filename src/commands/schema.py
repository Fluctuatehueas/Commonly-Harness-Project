"""命令系统的数据结构与错误类型。

命令目录、执行结果与错误码在这里统一定义；前端、HTTP 接口与 Agent 工具
共用同一份结构，避免三处各写一套。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ERROR_NOT_FOUND = "COMMAND_NOT_FOUND"
ERROR_BAD_SYNTAX = "COMMAND_BAD_SYNTAX"
ERROR_EXECUTOR_MISMATCH = "COMMAND_EXECUTOR_MISMATCH"
ERROR_NOT_ALLOWED = "COMMAND_NOT_ALLOWED"
ERROR_TARGET_NOT_FOUND = "COMMAND_TARGET_NOT_FOUND"
ERROR_TIMEOUT = "COMMAND_TIMEOUT"
ERROR_CONFIRM_REQUIRED = "COMMAND_CONFIRM_REQUIRED"


class CommandError(Exception):
    """命令执行失败；API 层按 ``status`` 转成 HTTP 响应。"""

    def __init__(self, code: str, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class CommandSpec:
    """一条命令的静态描述。"""

    name: str
    usage: str
    summary: str
    category: str
    executor: str = "frontend"  # frontend / backend
    risk: str = "low"  # low / medium / high
    requires_confirm: bool = False
    candidates: str = ""  # agents / skills / mcp / plugins / taskflows
    flags: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()
    visible: bool = True
    aliases: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "aliases": list(self.aliases),
            "usage": self.usage,
            "summary": self.summary,
            "category": self.category,
            "executor": self.executor,
            "risk": self.risk,
            "requires_confirm": self.requires_confirm,
            "candidates": self.candidates,
            "flags": list(self.flags),
            "examples": list(self.examples),
            "visible": self.visible,
        }


@dataclass
class CommandContext:
    """一次命令执行所需的上下文；禁止处理器自己读全局身份。"""

    user_id: str
    name: str = ""
    thread_id: str = ""
    username: str = ""
    args: list[str] = field(default_factory=list)
    flags: dict[str, Any] = field(default_factory=dict)
    raw: str = ""


@dataclass
class CommandResult:
    """命令的统一回执结构。"""

    ok: bool = True
    command: str = ""
    kind: str = "text"  # text / table / list
    title: str = ""
    text: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "command": self.command,
            "kind": self.kind,
            "title": self.title,
            "text": self.text,
            "columns": list(self.columns),
            "rows": [list(row) for row in self.rows],
            "warnings": list(self.warnings),
            "meta": dict(self.meta),
        }


def error_result(
    command: str, error: CommandError, *, warnings: list[str] | None = None
) -> dict[str, Any]:
    """把错误包装成与成功一致的响应结构。"""
    return {
        "ok": False,
        "command": command,
        "kind": "text",
        "title": f"/{command} 执行失败",
        "text": error.message,
        "columns": [],
        "rows": [],
        "warnings": list(warnings or []),
        "meta": {"error_code": error.code},
    }


__all__ = [
    "CommandContext",
    "CommandError",
    "CommandResult",
    "CommandSpec",
    "ERROR_BAD_SYNTAX",
    "ERROR_CONFIRM_REQUIRED",
    "ERROR_EXECUTOR_MISMATCH",
    "ERROR_NOT_ALLOWED",
    "ERROR_NOT_FOUND",
    "ERROR_TARGET_NOT_FOUND",
    "ERROR_TIMEOUT",
    "error_result",
]
