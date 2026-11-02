"""宿主能力工具：本机执行命令、读取/写入项目外文件。

这三个工具只在 ``HARNESS_HOST_ACCESS_ENABLED=true`` 时注册给 Agent；每一次
调用都会在内置 ``interrupt()`` 处暂停，必须由用户明确批准后才执行。

审批支持三种结果：
- ``approve``：只批准这一次；
- ``approve_thread``：批准并记住“当前会话 + 同一命令/同一路径”，后续同参数
  调用不再询问；换会话后重新审批；
- ``reject``：拒绝并返回可读原因。

工具内部仍会做独立校验（路径、系统目录、大小、超时、进程树），即使审批通过
也不会跳过这些边界。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Literal

from langchain_core.tools import tool
from langgraph.types import interrupt

import agent.config as host_config
from agent.project_context import (
    ProjectContextError,
    current_project,
    current_project_optional,
    current_thread_id,
)
from agent.project_marker import MARKER_DIRNAME


MAX_TIMEOUT_SECONDS = 600
_ALLOWLIST_LIMIT = 200
# thread_id -> 已批准的命令/路径 key；只在当前进程内有效。
_THREAD_ALLOWLIST: dict[str, OrderedDict[str, None]] = {}

DEFAULT_DENY_COMMANDS = (
    "format ",
    "diskpart",
    "shutdown",
    "poweroff",
    "reboot",
    "mkfs",
    "rm -rf",
    "rd /s",
    "del /s",
    "remove-item -recurse",
)


class HostPathError(ValueError):
    """宿主路径校验失败。"""


# ============================================================
# 通用工具
# ============================================================


def _error(code: str, message: str, **extra: Any) -> str:
    return json.dumps(
        {"ok": False, "error": {"code": code, "message": message}, **extra},
        ensure_ascii=False,
    )


def _norm(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if limit <= 0 or len(text) <= limit:
        return text, False
    return text[:limit] + "\n...(内容已截断)", True


def _protected_paths() -> list[Path]:
    """系统目录、凭据目录与用户数据目录；读写都拒绝。"""
    candidates: list[Path] = []
    for key in ("WINDIR", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        value = os.environ.get(key)
        if value:
            candidates.append(Path(value))
    home = Path.home()
    for name in (".ssh", ".aws", ".gnupg", ".config"):
        candidates.append(home / name)
    candidates.append(Path(host_config.USER_DATA_DIR))
    candidates.append(Path(host_config.PROJECT_ROOT) / ".harness")
    candidates.extend(host_config.HOST_DENY_EXTRA_PATHS)

    resolved: list[Path] = []
    for item in candidates:
        try:
            resolved.append(item.resolve())
        except (OSError, ValueError):
            continue
    return resolved


def _assert_not_forbidden(path: Path) -> None:
    """拒绝系统目录、凭据目录、.commonharness 与 .env。"""
    target = path.resolve()
    if target.name.lower() == ".env":
        raise HostPathError("拒绝访问 .env 环境变量文件")
    try:
        relative_parts = target.parts
    except ValueError:
        relative_parts = ()
    if any(part.lower() == MARKER_DIRNAME for part in relative_parts):
        raise HostPathError(f".{MARKER_DIRNAME} 是系统保留目录")

    for protected in _protected_paths():
        if _norm(target) == _norm(protected):
            raise HostPathError(f"不允许访问受保护路径：{target}")
        try:
            if target.is_relative_to(protected):
                raise HostPathError(f"不允许访问受保护路径：{target}")
        except ValueError:
            continue


def _resolve_host_path(
    raw_path: str,
    *,
    for_write: bool = False,
    must_exist: bool = True,
) -> Path:
    """把模型给的路径解析为宿主绝对路径，并执行越界与受保护路径校验。"""
    text = (raw_path or "").strip()
    if not text:
        raise HostPathError("路径不能为空")
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        project = current_project_optional()
        if project is None or project.root is None:
            raise HostPathError("相对路径只能在项目会话中使用；请提供绝对路径")
        candidate = project.root / text.lstrip("/")

    if candidate.is_symlink():
        raise HostPathError("拒绝符号链接路径")
    resolved = candidate.resolve()
    if must_exist and not resolved.exists():
        raise HostPathError(f"路径不存在：{resolved}")
    if resolved.exists() and resolved.is_dir():
        raise HostPathError(f"路径是目录，不是文件：{resolved}")
    _assert_not_forbidden(resolved)

    if for_write:
        if not _write_target_allowed(resolved):
            raise HostPathError(
                "目标不在允许写入的范围内：项目目录内可直接写入；"
                "项目外需配置 HARNESS_HOST_WRITE_ROOTS"
            )
    return resolved


def _write_target_allowed(path: Path) -> bool:
    """项目内允许；项目外必须落在 HARNESS_HOST_WRITE_ROOTS 内。"""
    project = current_project_optional()
    if project is not None and project.root is not None:
        try:
            if path.resolve().is_relative_to(project.root.resolve()):
                return True
        except ValueError:
            pass
    for root in host_config.HOST_WRITE_ROOTS:
        try:
            if path.resolve().is_relative_to(root):
                return True
        except ValueError:
            continue
    return False


def _check_command(command: str) -> None:
    """拒绝明显高危的命令组合；审批仍然是主边界。"""
    lowered = " ".join(command.lower().split())
    deny = [*DEFAULT_DENY_COMMANDS, *host_config.HOST_EXEC_DENY_COMMANDS]
    for pattern in deny:
        if pattern and pattern.lower() in lowered:
            raise HostPathError(f"命令命中禁用规则：{pattern}")


def _thread_key() -> str:
    """当前会话 ID；拿不到时返回空串，此时不做会话级白名单。"""
    return current_thread_id().strip()


def _allowlist_key(command_key: str) -> bool:
    thread_id = _thread_key()
    if not thread_id:
        return False
    entries = _THREAD_ALLOWLIST.get(thread_id)
    return bool(entries and command_key in entries)


def _remember(command_key: str) -> None:
    thread_id = _thread_key()
    if not thread_id:
        return
    entries = _THREAD_ALLOWLIST.setdefault(thread_id, OrderedDict())
    entries[command_key] = None
    entries.move_to_end(command_key)
    while len(entries) > _ALLOWLIST_LIMIT:
        entries.popitem(last=False)


def _reset_allowlist() -> None:
    """清空进程内白名单；仅供测试或设置页“清除会话授权”使用。"""
    _THREAD_ALLOWLIST.clear()


def _parse_decision(value: Any) -> tuple[Literal["approve", "approve_thread", "reject"], str]:
    """解析前端恢复载荷；未知值按拒绝处理（fail closed）。"""
    if isinstance(value, dict):
        raw = str(value.get("decision") or value.get("type") or "").strip().lower()
        reason = str(value.get("message") or "").strip()
    else:
        raw, reason = "", ""
    if raw in {"approve", "approved", "once"}:
        return "approve", reason
    if raw in {"approve_thread", "approve_session", "remember", "allow_thread"}:
        return "approve_thread", reason
    return "reject", reason


def _request_approval(
    *,
    tool_name: str,
    key: str,
    summary: str,
    args: dict[str, Any],
    reason: str,
) -> tuple[Literal["approve", "approve_thread", "reject"], str]:
    """命中会话白名单直接放行；否则用 interrupt 暂停等待人工审批。"""
    if _allowlist_key(key):
        return "approve", ""
    payload = {
        "type": "host_approval",
        "host_approval": {
            "tool": tool_name,
            "summary": summary,
            "reason": reason,
            "args": args,
        },
    }
    decision, message = _parse_decision(interrupt(payload))
    if decision == "approve_thread":
        _remember(key)
    return decision, message


def _run_command(command: str, shell: str, cwd: Path, timeout: int) -> dict[str, Any]:
    """同步执行命令；超时杀进程树。"""
    if sys.platform == "win32":
        if shell == "cmd":
            argv = ["cmd.exe", "/c", command]
        else:
            argv = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command]
        kwargs: dict[str, Any] = {}
    else:
        if shell == "cmd":
            argv = ["cmd", "-c", command]
        else:
            argv = ["bash", "-lc", command]
        kwargs = {"start_new_session": True}

    started = time.monotonic()
    proc = subprocess.Popen(
        argv,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        close_fds=True,
        **kwargs,
    )
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_process_tree(proc)
        stdout, stderr = proc.communicate()
        code = -1

    budget = max(1000, int(host_config.HOST_EXEC_MAX_OUTPUT))
    stdout_text, cut_out = _truncate(stdout or "", budget)
    stderr_text, cut_err = _truncate(stderr or "", max(1000, budget - len(stdout_text)))
    return {
        "exit_code": code,
        "stdout": stdout_text,
        "stderr": stderr_text,
        "timed_out": timed_out,
        "truncated": cut_out or cut_err,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "cwd": str(cwd),
    }


def _kill_process_tree(proc: subprocess.Popen[Any]) -> None:
    """跨平台杀掉命令及其子进程。"""
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/pid", str(proc.pid), "/f", "/t"],
                capture_output=True,
                text=True,
                timeout=10,
                errors="ignore",
            )
        except (OSError, subprocess.SubprocessError):
            proc.kill()
        return
    try:
        os.killpg(os.getpgid(proc.pid), 9)
    except (OSError, ProcessLookupError):
        proc.kill()


# ============================================================
# 工具实现
# ============================================================


@tool
async def host_execute(
    command: str,
    reason: str,
    timeout: int = 120,
    shell: str = "",
) -> str:
    """在本机执行一条命令（仅项目会话；cwd 固定为项目根目录）。

    只有沙箱缺少目标运行环境时才使用。调用会暂停等待人工审批：
    批准、仅本会话允许、拒绝。命令与工作目录会原样展示给审批人。

    Args:
        command: 要执行的命令。
        reason: 为什么必须在本机执行（必填，会展示给审批人）。
        timeout: 超时秒数，最大 600。
        shell: powershell / cmd / bash；留空按平台默认。
    """
    if not (host_config.HOST_ACCESS_ENABLED and host_config.HOST_EXEC_ENABLED):
        return _error("HOST_DISABLED", "宿主执行能力未开启")
    if not (reason or "").strip():
        return _error("REASON_REQUIRED", "必须说明为什么需要在本机执行")
    try:
        project = current_project()
    except ProjectContextError as exc:
        return _error("PROJECT_REQUIRED", str(exc))
    if project.kind != "project" or project.root is None:
        return _error("PROJECT_REQUIRED", "自由会话没有项目目录，禁止本机执行")

    text = (command or "").strip()
    if not text:
        return _error("COMMAND_REQUIRED", "命令不能为空")
    selected_shell = (shell or "").strip().lower()
    if not selected_shell:
        selected_shell = "powershell" if sys.platform == "win32" else "bash"
    if selected_shell not in {"powershell", "cmd", "bash"}:
        return _error("SHELL_UNSUPPORTED", f"不支持的 shell：{selected_shell}")
    try:
        _check_command(text)
    except HostPathError as exc:
        return _error("COMMAND_DENIED", str(exc))

    seconds = max(1, min(int(timeout or host_config.HOST_EXEC_TIMEOUT), MAX_TIMEOUT_SECONDS))
    cwd = project.root.resolve()
    key = f"exec|{selected_shell}|{text}|{_norm(cwd)}"
    decision, message = _request_approval(
        tool_name="host_execute",
        key=key,
        summary=f"在本机执行命令（cwd={cwd}）：{text}",
        args={
            "command": text,
            "cwd": str(cwd),
            "shell": selected_shell,
            "timeout": seconds,
            "reason": reason,
        },
        reason=reason,
    )
    if decision == "reject":
        return _error(
            "HOST_REJECTED",
            f"用户拒绝执行该命令{f'：{message}' if message else ''}。不要重试同一命令。",
        )

    try:
        result = await asyncio.to_thread(_run_command, text, selected_shell, cwd, seconds)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return _error("EXEC_FAILED", f"本机执行失败：{exc}")
    return json.dumps(
        {"ok": result["exit_code"] == 0 and not result["timed_out"], "shell": selected_shell, **result},
        ensure_ascii=False,
    )


@tool
async def host_read_file(
    path: str,
    reason: str,
    offset: int = 0,
    limit: int = 2000,
) -> str:
    """读取本机文件（项目外也允许），每次调用都需要审批。

    仅支持文本文件；二进制、目录、符号链接、系统目录与凭据目录会被拒绝。

    Args:
        path: 绝对路径；项目会话中也接受项目相对路径。
        reason: 为什么需要读取该文件（会展示给审批人）。
        offset: 起始行（0 开始）。
        limit: 最多读取行数。
    """
    if not (host_config.HOST_ACCESS_ENABLED and host_config.HOST_READ_ENABLED):
        return _error("HOST_DISABLED", "宿主读取能力未开启")
    if not (reason or "").strip():
        return _error("REASON_REQUIRED", "必须说明为什么需要读取该文件")
    try:
        target = _resolve_host_path(path)
    except HostPathError as exc:
        return _error("PATH_DENIED", str(exc))

    try:
        size = target.stat().st_size
    except OSError as exc:
        return _error("READ_FAILED", f"读取文件失败：{exc}")
    max_bytes = max(1, host_config.HOST_READ_MAX_MB) * 1024 * 1024
    if size > max_bytes:
        return _error("TOO_LARGE", f"文件超过 {host_config.HOST_READ_MAX_MB} MB，已拒绝")

    key = f"read|{_norm(target)}"
    decision, message = _request_approval(
        tool_name="host_read_file",
        key=key,
        summary=f"读取本机文件：{target}（{size} 字节）",
        args={"path": str(target), "size": size, "offset": offset, "limit": limit, "reason": reason},
        reason=reason,
    )
    if decision == "reject":
        return _error(
            "HOST_REJECTED",
            f"用户拒绝读取该文件{f'：{message}' if message else ''}。",
        )

    try:
        raw = await asyncio.to_thread(target.read_bytes)
    except OSError as exc:
        return _error("READ_FAILED", f"读取文件失败：{exc}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return _error("BINARY_NOT_SUPPORTED", "文件不是 UTF-8 文本，无法预览")
    if "\x00" in text:
        return _error("BINARY_NOT_SUPPORTED", "文件包含空字符，按二进制拒绝")

    start = max(0, int(offset or 0))
    count = max(1, int(limit or 2000))
    lines = text.splitlines()
    selected = lines[start : start + count]
    content = "\n".join(selected)
    truncated = start + count < len(lines)
    return json.dumps(
        {
            "ok": True,
            "path": str(target),
            "size": size,
            "offset": start,
            "limit": count,
            "total_lines": len(lines),
            "content": content,
            "truncated": truncated,
        },
        ensure_ascii=False,
    )


@tool
async def host_write_file(
    path: str,
    reason: str,
    content: str | None = None,
    source_path: str | None = None,
    overwrite: bool = False,
) -> str:
    """向本机写文件（项目外需配置允许根目录），每次调用都需要审批。

    Args:
        path: 目标绝对路径；项目会话中也接受项目相对路径。
        reason: 为什么需要写入该文件（会展示给审批人）。
        content: 直接写入的文本内容；与 source_path 二选一。
        source_path: 项目内相对路径，复制该文件内容到目标。
        overwrite: 目标已存在时是否覆盖，默认 false。
    """
    if not (host_config.HOST_ACCESS_ENABLED and host_config.HOST_WRITE_ENABLED):
        return _error("HOST_DISABLED", "宿主写入能力未开启")
    if not (reason or "").strip():
        return _error("REASON_REQUIRED", "必须说明为什么需要写入该文件")
    if (content is None) == (source_path is None):
        return _error("SOURCE_REQUIRED", "content 与 source_path 必须且只能提供一个")

    data: bytes
    if content is not None:
        data = str(content).encode("utf-8")
    else:
        try:
            project = current_project()
        except ProjectContextError as exc:
            return _error("PROJECT_REQUIRED", f"source_path 只能用项目内文件：{exc}")
        if project.root is None:
            return _error("PROJECT_REQUIRED", "自由会话没有项目目录")
        try:
            source = (project.root / str(source_path).lstrip("/")).resolve()
            source.relative_to(project.root.resolve())
        except (OSError, ValueError) as exc:
            return _error("SOURCE_DENIED", f"source_path 必须是项目内相对路径：{exc}")
        if not source.is_file():
            return _error("SOURCE_NOT_FOUND", f"项目内不存在文件：{source_path}")
        try:
            data = await asyncio.to_thread(source.read_bytes)
        except OSError as exc:
            return _error("READ_FAILED", f"读取源文件失败：{exc}")

    max_bytes = max(1, host_config.HOST_WRITE_MAX_MB) * 1024 * 1024
    if len(data) > max_bytes:
        return _error("TOO_LARGE", f"内容超过 {host_config.HOST_WRITE_MAX_MB} MB，已拒绝")

    try:
        target = _resolve_host_path(path, for_write=True, must_exist=False)
    except HostPathError as exc:
        return _error("PATH_DENIED", str(exc))
    if target.exists() and not overwrite:
        return _error("ALREADY_EXISTS", f"目标已存在；如需覆盖请显式设置 overwrite=true：{target}")
    if not target.parent.exists() or not target.parent.is_dir():
        return _error("PARENT_MISSING", f"父目录不存在，已拒绝自动创建：{target.parent}")

    digest = hashlib.sha256(data).hexdigest()
    key = f"write|{_norm(target)}|{bool(overwrite)}|{digest}"
    decision, message = _request_approval(
        tool_name="host_write_file",
        key=key,
        summary=f"写入本机文件：{target}（{len(data)} 字节，覆盖={bool(overwrite)}）",
        args={
            "path": str(target),
            "size": len(data),
            "overwrite": bool(overwrite),
            "source_path": source_path,
            "reason": reason,
        },
        reason=reason,
    )
    if decision == "reject":
        return _error(
            "HOST_REJECTED",
            f"用户拒绝写入该文件{f'：{message}' if message else ''}。",
        )

    overwritten = target.exists()
    try:
        await asyncio.to_thread(target.write_bytes, data)
    except OSError as exc:
        return _error("WRITE_FAILED", f"写入文件失败：{exc}")
    return json.dumps(
        {
            "ok": True,
            "path": str(target),
            "size": len(data),
            "sha256": digest,
            "overwritten": overwritten,
        },
        ensure_ascii=False,
    )


def build_host_tools() -> list[Any]:
    """按开关组装宿主工具；关闭时返回空列表，模型完全不可见。"""
    if not host_config.HOST_ACCESS_ENABLED:
        return []
    tools: list[Any] = []
    if host_config.HOST_EXEC_ENABLED:
        tools.append(host_execute)
    if host_config.HOST_READ_ENABLED:
        tools.append(host_read_file)
    if host_config.HOST_WRITE_ENABLED:
        tools.append(host_write_file)
    return tools


__all__ = [
    "HostPathError",
    "build_host_tools",
    "host_execute",
    "host_read_file",
    "host_write_file",
    "_resolve_host_path",
    "_check_command",
    "_reset_allowlist",
]
