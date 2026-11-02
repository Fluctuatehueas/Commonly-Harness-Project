"""本地运行时探测：供沙箱降级提示和 MCP stdio 前置条件检查复用。

探测只执行 ``<command> --version``，不启动任何 MCP 服务，
结果带安装引导，前端可以直接展示“需要安装 Node.js 18+，点击下载”。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

# 每个可执行文件的安装引导与最低版本要求。
# 版本号只做“提示 + 阻断”判断，不参与命令执行。
RUNTIME_SPECS: dict[str, dict[str, Any]] = {
    "node": {
        "label": "Node.js",
        "min_version": "18",
        "download": "https://nodejs.org/zh-cn/download",
        "download_target": "Node.js 官方安装包（npm / npx 随附）",
        "hint": "需要安装 Node.js 18+（npx 随 Node.js 一起安装）",
    },
    "npx": {
        "label": "npx",
        "min_version": "9",
        "download": "https://nodejs.org/zh-cn/download",
        "download_target": "Node.js 官方安装包（npx 随附）",
        "hint": "npx 随 Node.js 18+ 安装；安装后重开终端确认 `npx -v` 可用",
    },
    "npm": {
        "label": "npm",
        "min_version": "9",
        "download": "https://nodejs.org/zh-cn/download",
        "download_target": "Node.js 官方安装包（npm 随附）",
        "hint": "npm 随 Node.js 18+ 安装",
    },
    "python": {
        "label": "Python",
        "min_version": "3.10",
        "download": "https://www.python.org/downloads/",
        "download_target": "Python 官方安装包",
        "hint": "需要 Python 3.10+，安装时勾选 Add Python to PATH",
    },
    "python3": {
        "label": "Python 3",
        "min_version": "3.10",
        "download": "https://www.python.org/downloads/",
        "download_target": "Python 官方安装包",
        "hint": "需要 Python 3.10+，安装时勾选 Add Python to PATH",
    },
    "uv": {
        "label": "uv",
        "min_version": "0.4",
        "download": "https://docs.astral.sh/uv/getting-started/installation/",
        "download_target": "uv 官方安装脚本 / 安装包",
        "hint": "需要安装 uv（uvx 随 uv 一起安装）",
    },
    "uvx": {
        "label": "uvx",
        "min_version": "0.4",
        "download": "https://docs.astral.sh/uv/getting-started/installation/",
        "download_target": "uv 官方安装脚本 / 安装包（uvx 随附）",
        "hint": "uvx 随 uv 安装；安装后重开终端确认 `uvx --version` 可用",
    },
    "docker": {
        "label": "Docker",
        "min_version": "",
        "download": "https://docs.docker.com/desktop/",
        "download_target": "Docker Desktop 安装包",
        "hint": "需要安装并启动 Docker Desktop",
    },
    "deno": {
        "label": "Deno",
        "min_version": "1.40",
        "download": "https://deno.com/",
        "download_target": "Deno 官方安装包",
        "hint": "需要安装 Deno 1.40+",
    },
    "bun": {
        "label": "Bun",
        "min_version": "1.0",
        "download": "https://bun.sh/",
        "download_target": "Bun 官方安装脚本",
        "hint": "需要安装 Bun 1.0+",
    },
}

_VERSION_PATTERN = re.compile(r"(\d+(?:\.\d+)+)")


def _command_path(command: str) -> str | None:
    """在 PATH 中查找可执行文件；Windows 下兼容 .cmd/.exe。"""
    found = shutil.which(command)
    if found:
        return found
    if sys.platform == "win32":
        for suffix in (".exe", ".cmd", ".bat"):
            candidate = shutil.which(f"{command}{suffix}")
            if candidate:
                return candidate
    return None


def _parse_version(text: str) -> str:
    match = _VERSION_PATTERN.search(text or "")
    return match.group(1) if match else ""


def _version_tuple(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in value.split("."))
    except ValueError:
        return ()


def _run_probe(
    command_path: str,
    *args: str,
    inherit_env: bool = False,
) -> tuple[bool, str, str]:
    """执行一次探测命令，返回 (是否成功, stdout, stderr)。

    ``inherit_env=False`` 时只保留 PATH/PATHEXT，避免外部注入影响版本探测；
    npm/npx 这类依赖 APPDATA、USERPROFILE 的命令需要完整环境，否则会直接崩溃。
    """
    env = (
        dict(os.environ)
        if inherit_env
        else {"PATH": os.environ.get("PATH", ""), "PATHEXT": os.environ.get("PATHEXT", "")}
    )
    try:
        completed = subprocess.run(
            [command_path, *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as error:
        return False, "", f"{type(error).__name__}: {error}"
    return True, completed.stdout or "", completed.stderr or ""


def _advice(available: bool, satisfied: bool, version: str, min_version: str) -> str:
    """下载链接旁边给用户的建议，避免“已满足还去重复安装”。"""
    if not available:
        return "未检测到该运行环境，建议先按链接安装，安装后重开终端再回来检测。"
    if not satisfied:
        return (
            f"当前版本 {version or '未知'} 低于要求（{min_version}+），"
            "建议升级后再连接；升级方式以官方安装包为准。"
        )
    return "当前已满足，不建议下载：重复安装容易出现多版本冲突和 PATH 混乱。"


def probe_command(command: str) -> dict[str, Any]:
    """探测单个命令是否存在及版本是否满足最低要求。"""
    base = Path(command).stem.lower()
    spec = RUNTIME_SPECS.get(base, {})
    path = _command_path(command)
    result: dict[str, Any] = {
        "command": base,
        "label": spec.get("label", base),
        "available": bool(path),
        "path": path or "",
        "version": "",
        "min_version": spec.get("min_version", ""),
        "satisfied": bool(path),
        "download": spec.get("download", ""),
        "download_target": spec.get("download_target", "该运行环境的官方安装包"),
        "hint": spec.get("hint", ""),
    }
    if not path:
        result["advice"] = _advice(False, False, "", result["min_version"])
        return result

    ok, stdout, stderr = _run_probe(path, "--version", inherit_env=True)
    if not ok:
        result["version"] = ""
        result["satisfied"] = False
        result["error"] = f"版本探测失败: {stderr[:200]}"
        result["advice"] = _advice(True, False, "", result["min_version"])
        return result

    output = f"{stdout}\n{stderr}".strip()
    version = _parse_version(output)
    result["version"] = version
    min_version = str(spec.get("min_version") or "")
    if version and min_version:
        result["satisfied"] = _version_tuple(version) >= _version_tuple(min_version)
    else:
        result["satisfied"] = True
    if not result["satisfied"] and spec.get("hint"):
        result["message"] = (
            f"{spec.get('label', base)} 版本过低（当前 {version or '未知'}，"
            f"需要 {min_version}+）。{spec.get('hint')}"
        )
    result["advice"] = _advice(True, result["satisfied"], version, min_version)
    return result


def probe_npm_cache() -> dict[str, Any]:
    """探测系统 npm 缓存目录是否可写。

    ``npx -y`` 首次运行要把包下载进 npm 缓存；缓存目录若位于安装目录
    （例如 ``D:\\nodejs\\node_cache``，普通用户只有读权限），npm 会以 EPERM
    退出，客户端只能看到“连接被关闭”。这里提前把真实原因暴露给用户。
    """
    npm = _command_path("npm")
    result: dict[str, Any] = {
        "available": bool(npm),
        "command": npm or "",
        "cache_dir": "",
        "writable": False,
        "error": "",
    }
    if not npm:
        result["error"] = "未检测到 npm"
        return result

    ok, stdout, stderr = _run_probe(npm, "config", "get", "cache", inherit_env=True)
    # npm 可能把告警写到 stderr；只在 stdout 里找目录，且取最后一个非空行。
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    cache_dir = lines[-1] if lines else ""
    if not ok or not cache_dir:
        result["error"] = f"读取 npm 缓存目录失败: {(stderr or '无输出')[:200]}"
        return result
    result["cache_dir"] = cache_dir

    try:
        directory = Path(cache_dir).expanduser()
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / f".write-probe-{os.getpid()}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as error:
        result["error"] = f"{type(error).__name__}: {error}"
        return result
    result["writable"] = True
    return result


def detect_local_environment(commands: list[str] | None = None) -> dict[str, Any]:
    """检测本机可用的执行环境，用于“未配置沙箱时本地运行”的提示。"""
    names = commands or ["python", "node", "npm", "npx", "uv", "uvx", "git", "docker"]
    probes = [probe_command(name) for name in names]
    return {
        "platform": sys.platform,
        "runnable": sum(1 for item in probes if item["available"]),
        "items": probes,
    }


def required_commands_for_stdio(command: str, args: list[str] | None = None) -> list[str]:
    """根据 stdio MCP 的启动命令推导需要预检的运行时。

    - ``npx`` 依赖 node/npm；
    - ``uvx`` / ``uv run`` 依赖 uv；
    - ``python -m ...`` 依赖 python；
    - 其余命令按自身探测。
    """
    base = Path(command).stem.lower()
    if base in {"npx", "npm"}:
        return ["node", "npx"]
    if base == "node":
        return ["node"]
    if base in {"uvx", "uv"}:
        return ["uv"]
    if base in {"python", "python3", "py"}:
        return ["python"]
    if base in RUNTIME_SPECS:
        return [base]
    return []
