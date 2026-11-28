"""命令系统对外入口。"""

from __future__ import annotations

from commands.schema import (
    CommandContext,
    CommandError,
    CommandResult,
    CommandSpec,
)
from commands.service import execute, execute_for_agent

__all__ = [
    "CommandContext",
    "CommandError",
    "CommandResult",
    "CommandSpec",
    "execute",
    "execute_for_agent",
]
