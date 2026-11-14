"""代码编译/测试工具：本机运行时优先，缺失时转沙箱。

策略：

1. 先根据项目标记文件识别语言（C# / Java / Go / Node / Python / Rust / C++）；
2. 探测本机是否已有对应运行时，并检查依赖产物是否已存在；
3. 本机可用时只执行固定编译命令，**不允许安装语言运行时或系统包**；
   构建工具自身的依赖还原（如 dotnet restore、Maven 拉包）属于构建步骤；
4. 本机缺少运行时、缺少必要产物，或本地编译失败时，转沙箱；
5. 沙箱内可以安装运行时与依赖，编译产物不回写本机。

工具只接受 ``build`` / ``test`` 与项目内相对路径，不接受任意命令，
避免模型把编译入口变成宿主命令执行通道。
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Literal

from langchain_core.tools import tool
from langgraph.errors import GraphBubbleUp
from langgraph.types import interrupt

import agent.config as agent_config
from agent.backends.project_backend import IGNORED_NAMES
from agent.project_context import (
    ProjectContextError,
    current_project,
    current_thread_id,
    sandbox_project_dir,
)

# 编译审批的作用域：本项目 / 本会话。进程内有效，重启后重新审批。
_PROJECT_BUILD_ALLOWLIST: set[str] = set()
_SESSION_BUILD_ALLOWLIST: dict[str, set[str]] = {}

MAX_OUTPUT_CHARS = 12_000
MAX_FILES = 2_000
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 50 * 1024 * 1024

_EXTRA_IGNORED = {
    "obj",
    "bin",
    "target",
    ".harness",
    ".idea",
    ".vscode",
    ".pytest_cache",
    ".mypy_cache",
    "__pycache__",
}

_FALLBACK_MARKERS = (
    "module lookup disabled",
    "offline mode",
    "could not resolve dependencies",
    "cannot access central",
    "cannot find module",
    "no such file or directory: node_modules",
)

_SPECS: dict[str, dict[str, Any]] = {
    "csharp": {
        "label": "C# / .NET",
        "runtime": ["dotnet"],
        "install": ".NET SDK 8",
        "sandbox_prepare": (
            "if ! command -v curl >/dev/null 2>&1; then "
            "apt-get update -qq && apt-get install -y -qq curl; fi; "
            "if ! command -v dotnet >/dev/null 2>&1; then "
            "curl -fsSL https://dot.net/v1/dotnet-install.sh -o /tmp/dotnet-install.sh "
            "&& bash /tmp/dotnet-install.sh --channel 8.0 >/dev/null "
            "&& export PATH=\"$HOME/.dotnet:$PATH\"; fi"
        ),
        "build": "dotnet build --nologo",
        "test": "dotnet test --nologo",
    },
    "java_maven": {
        "label": "Java / Maven",
        "runtime": ["mvn", "java"],
        "install": "Maven",
        "sandbox_prepare": (
            "if ! command -v mvn >/dev/null 2>&1; then "
            "apt-get update -qq && apt-get install -y -qq maven; fi"
        ),
        "build": "mvn -q -DskipTests package",
        "test": "mvn -q test",
    },
    "java_gradle": {
        "label": "Java / Gradle",
        "runtime": ["gradle", "java"],
        "install": "Gradle",
        "sandbox_prepare": (
            "if [ -f ./gradlew ]; then chmod +x ./gradlew; "
            "elif ! command -v gradle >/dev/null 2>&1; then "
            "apt-get update -qq && apt-get install -y -qq gradle; fi"
        ),
        "build": "if [ -f ./gradlew ]; then ./gradlew --no-daemon build -x test; "
        "else gradle --no-daemon build -x test; fi",
        "test": "if [ -f ./gradlew ]; then ./gradlew --no-daemon test; "
        "else gradle --no-daemon test; fi",
    },
    "go": {
        "label": "Go",
        "runtime": ["go"],
        "install": "",
        "sandbox_prepare": "true",
        "build": "go mod download && go build ./...",
        "test": "go mod download && go test ./...",
    },
    "node": {
        "label": "Node.js",
        "runtime": ["node", "npm"],
        "install": "Node.js 依赖",
        "sandbox_prepare": "true",
        "build": "npm install --no-audit --no-fund && npm run build --if-present",
        "test": "npm install --no-audit --no-fund && npm test --if-present",
    },
    "python": {
        "label": "Python",
        "runtime": ["python"],
        "install": "pytest（测试时）",
        "sandbox_prepare": "true",
        "build": "python -m compileall -q .",
        "test": "python -m pip install -q pytest && python -m pytest -q",
    },
    "rust": {
        "label": "Rust",
        "runtime": ["cargo"],
        "install": "Rust 工具链",
        "sandbox_prepare": (
            "if ! command -v curl >/dev/null 2>&1; then "
            "apt-get update -qq && apt-get install -y -qq curl; fi; "
            "if ! command -v cargo >/dev/null 2>&1; then "
            "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs "
            "-o /tmp/rustup.sh && sh /tmp/rustup.sh -y --profile minimal "
            "&& . \"$HOME/.cargo/env\"; fi"
        ),
        "build": "cargo build",
        "test": "cargo test",
    },
    "cpp": {
        "label": "C / C++",
        "runtime": ["cmake"],
        "install": "CMake 与 C++ 工具链",
        "sandbox_prepare": (
            "if ! command -v cmake >/dev/null 2>&1 || ! command -v g++ >/dev/null 2>&1; "
            "then apt-get update -qq && apt-get install -y -qq build-essential cmake; fi"
        ),
        "build": "cmake -S . -B build && cmake --build build -j2",
        "test": "cmake -S . -B build && cmake --build build -j2 && ctest --test-dir build",
    },
}


@dataclass
class BuildPlan:
    """一次编译/测试的执行计划。"""

    language: str
    label: str
    mode: Literal["local", "sandbox", "unsupported"]
    runtime: list[dict[str, Any]] = field(default_factory=list)
    command: str = ""
    argv: list[str] = field(default_factory=list)
    sandbox_prepare: str = ""
    installs: list[str] = field(default_factory=list)
    reason: str = ""
    missing: list[str] = field(default_factory=list)


def detect_language(root: Path) -> str:
    """按标记文件识别项目语言；顺序避免 Python/Node 误判。"""
    if next(root.rglob("*.sln"), None) is not None:
        return "csharp"
    if next(root.rglob("*.csproj"), None) is not None:
        return "csharp"
    if (root / "pom.xml").is_file():
        return "java_maven"
    if any(
        (root / name).is_file()
        for name in ("build.gradle", "build.gradle.kts", "settings.gradle")
    ):
        return "java_gradle"
    if (root / "Cargo.toml").is_file():
        return "rust"
    if (root / "go.mod").is_file():
        return "go"
    if (root / "package.json").is_file():
        return "node"
    if any(
        (root / name).is_file()
        for name in ("pyproject.toml", "requirements.txt", "setup.py")
    ) or any(root.glob("*.py")):
        return "python"
    if (root / "CMakeLists.txt").is_file():
        return "cpp"
    return ""


def _probe_python_pytest() -> bool:
    executable = shutil.which("python") or shutil.which("python3")
    if not executable:
        return False
    try:
        completed = subprocess.run(
            [executable, "-m", "pytest", "--version"],
            check=False,
            capture_output=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def _missing_toolchain(language: str) -> list[str]:
    """检查“命令存在但工具链不完整”的情况，例如只有 dotnet CLI 没有 SDK。"""
    if language == "csharp":
        executable = shutil.which("dotnet")
        if not executable:
            return ["dotnet-sdk"]
        try:
            completed = subprocess.run(
                [executable, "--list-sdks"],
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError):
            return ["dotnet-sdk"]
        return [] if completed.stdout.strip() else ["dotnet-sdk"]
    if language == "java_maven":
        return [] if shutil.which("javac") else ["jdk"]
    if language == "cpp":
        has_compiler = any(
            shutil.which(name) for name in ("g++", "clang++", "cl")
        )
        return [] if has_compiler else ["c++ compiler"]
    return []


def _exe(name: str) -> str:
    """本地可执行文件路径；Windows 下用于避免 .cmd 被 shell 解析。"""
    return shutil.which(name) or name


def _local_command(language: str, root: Path, action: str) -> tuple[list[str] | None, str]:
    """返回本机可执行命令；不满足离线/依赖条件时返回 (None, 原因)。"""
    if language == "csharp":
        # 有 SDK 就让 dotnet 正常 build（restore 属于构建步骤，不是安装运行时）。
        return [_exe("dotnet"), "test" if action == "test" else "build", "--nologo"], ""

    if language == "node":
        if not (root / "node_modules").is_dir():
            return None, "本机没有 node_modules，为避免在本机安装依赖，转到沙箱"
        if action == "test":
            return [_exe("npm"), "test", "--if-present"], ""
        return [_exe("npm"), "run", "build", "--if-present"], ""

    if language == "python":
        if action == "test":
            if not _probe_python_pytest():
                return None, "本机未安装 pytest，测试将转到沙箱"
            return [_exe("python"), "-m", "pytest", "-q"], ""
        return [_exe("python"), "-m", "compileall", "-q", "."], ""

    if language == "go":
        return [_exe("go"), "test" if action == "test" else "build", "./..."], ""

    if language == "java_maven":
        base = [_exe("mvn"), "-q"]
        if action == "test":
            return [*base, "test"], ""
        return [*base, "-DskipTests", "package"], ""

    if language == "java_gradle":
        if shutil.which("gradle") is None:
            return None, "本机只有 Gradle Wrapper；Wrapper 会下载发行版，按策略不在本机执行"
        base = [_exe("gradle"), "-q"]
        if action == "test":
            return [*base, "test"], ""
        return [*base, "build", "-x", "test"], ""

    if language == "rust":
        return [_exe("cargo"), "test" if action == "test" else "build"], ""

    if language == "cpp":
        if not (root / "build" / "CMakeCache.txt").is_file():
            return None, "本机没有已配置的 build 目录，需先在沙箱完成 cmake 配置"
        return [_exe("cmake"), "--build", "build", "-j2"], ""

    return None, "未识别的项目类型"


def build_plan(
    root: Path,
    action: str = "build",
    *,
    probe: Callable[[str], dict[str, Any]] | None = None,
    check_toolchain: bool = True,
) -> BuildPlan:
    """生成本机优先、沙箱兜底的执行计划。"""
    from utils.runtime_probe import probe_command

    language = detect_language(root)
    if not language:
        return BuildPlan(
            language="",
            label="未知",
            mode="unsupported",
            reason="未找到可识别的项目标记文件（.sln/.csproj、pom.xml、go.mod、package.json、pyproject.toml、Cargo.toml、CMakeLists.txt）",
        )

    spec = _SPECS[language]
    probe_fn = probe or probe_command
    runtime = [probe_fn(name) for name in spec["runtime"]]
    missing = [
        str(item["command"])
        for item in runtime
        if not item.get("available") or not item.get("satisfied")
    ]
    if check_toolchain:
        missing.extend(_missing_toolchain(language))
    local_cmd, local_reason = _local_command(language, root, action)

    if agent_config.LOCAL_BUILD_ENABLED and not missing and local_cmd:
        return BuildPlan(
            language=language,
            label=str(spec["label"]),
            mode="local",
            runtime=runtime,
            command=" ".join(local_cmd),
            argv=local_cmd,
        )

    reason = local_reason
    if missing:
        reason = f"本机缺少运行时: {', '.join(missing)}"
    return BuildPlan(
        language=language,
        label=str(spec["label"]),
        mode="sandbox",
        runtime=runtime,
        command=str(spec["test"] if action == "test" else spec["build"]),
        sandbox_prepare=str(spec["sandbox_prepare"]),
        installs=[str(spec["install"])] if spec.get("install") else [],
        reason=reason,
        missing=missing,
    )


def sandbox_plan(language: str, action: str, *, reason: str) -> BuildPlan:
    """构造强制沙箱执行的计划（用于本地离线失败后的兜底）。"""
    spec = _SPECS[language]
    return BuildPlan(
        language=language,
        label=str(spec["label"]),
        mode="sandbox",
        command=str(spec["test"] if action == "test" else spec["build"]),
        sandbox_prepare=str(spec["sandbox_prepare"]),
        installs=[str(spec["install"])] if spec.get("install") else [],
        reason=reason,
    )


def _truncate(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text, False
    head = MAX_OUTPUT_CHARS * 2 // 3
    tail = MAX_OUTPUT_CHARS - head
    return f"{text[:head]}\n\n...（中间省略）...\n\n{text[-tail:]}", True


def _local_env() -> dict[str, str]:
    env = dict(os.environ)
    env["DOTNET_CLI_TELEMETRY_OPTOUT"] = "1"
    env["DOTNET_NOLOGO"] = "1"
    env["npm_config_audit"] = "false"
    env["npm_config_fund"] = "false"
    env["npm_config_update_notifier"] = "false"
    return env


def _kill_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                check=False,
                capture_output=True,
            )
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (OSError, ProcessLookupError):
        try:
            process.kill()
        except OSError:
            pass


def _run_local(argv: list[str], cwd: Path, timeout: int) -> dict[str, Any]:
    """在本机执行固定编译命令；只使用已存在的环境。"""
    started = time.monotonic()
    kwargs: dict[str, Any] = {
        "cwd": str(cwd),
        "shell": False,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "env": _local_env(),
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    else:
        kwargs["start_new_session"] = True

    process = subprocess.Popen(argv, **kwargs)
    timed_out = False
    try:
        output, _ = process.communicate(timeout=max(1, int(timeout)))
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(process)
        output, _ = process.communicate()
    text, truncated = _truncate(output or "")
    return {
        "exit_code": int(process.returncode or 0),
        "output": text,
        "truncated": truncated,
        "timed_out": timed_out,
        "duration_ms": int((time.monotonic() - started) * 1000),
    }


def _needs_sandbox(output: str) -> bool:
    text = (output or "").lower()
    return any(marker in text for marker in _FALLBACK_MARKERS)


def collect_project_files(
    root: Path,
    subdir: str = "",
) -> tuple[list[tuple[str, bytes]], list[dict[str, str]]]:
    """收集项目文件，跳过构建产物与超大文件；返回（相对路径+字节, 跳过项）。"""
    base = (root / subdir).resolve() if subdir else root.resolve()
    try:
        base.relative_to(root.resolve())
    except ValueError as error:
        raise ValueError("project_path 越出项目目录") from error

    uploads: list[tuple[str, bytes]] = []
    skipped: list[dict[str, str]] = []
    total = 0
    ignored = {name.lower() for name in IGNORED_NAMES} | _EXTRA_IGNORED

    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [
            name
            for name in dirnames
            if name.lower() not in ignored and not name.startswith(".")
        ]
        for filename in filenames:
            if len(skipped) >= 200:
                break
            path = Path(dirpath) / filename
            if filename.lower() == ".env":
                skipped.append({"path": str(path), "reason": "敏感文件"})
                continue
            try:
                size = path.stat().st_size
            except OSError as error:
                skipped.append({"path": str(path), "reason": str(error)})
                continue
            if size > MAX_FILE_BYTES:
                skipped.append({"path": str(path), "reason": "单文件超过 5MB"})
                continue
            if total + size > MAX_TOTAL_BYTES or len(uploads) >= MAX_FILES:
                skipped.append({"path": str(path), "reason": "超过项目上传上限"})
                continue
            try:
                data = path.read_bytes()
            except OSError as error:
                skipped.append({"path": str(path), "reason": str(error)})
                continue
            relative = path.relative_to(root.resolve()).as_posix()
            uploads.append((relative, data))
            total += size
        if len(skipped) >= 200:
            break
    return uploads, skipped


async def _run_sandbox(
    plan: BuildPlan,
    *,
    sandbox_backend: Any,
    sandbox_dir: str,
    project_path: str,
    timeout: int,
) -> dict[str, Any]:
    if sandbox_backend is None:
        return {
            "ok": False,
            "error": "本机缺少编译环境，且当前未配置沙箱；按安全策略不会在本机自动安装",
            "mode": "sandbox",
            "language": plan.language,
            "missing": plan.missing,
        }

    project = current_project()
    root = project.root if project.root is not None else None
    if root is None:
        return {
            "ok": False,
            "error": "自由会话没有本机项目目录；请使用 run_in_sandbox 执行编译命令",
            "mode": "sandbox",
            "language": plan.language,
        }

    uploads, skipped = collect_project_files(root, project_path)
    if not uploads:
        return {
            "ok": False,
            "error": "没有可上传到沙箱的项目文件",
            "skipped": skipped[:20],
            "mode": "sandbox",
        }

    full_uploads = [(f"{sandbox_dir}/{relative}", data) for relative, data in uploads]
    workdir = f"{sandbox_dir}/{project_path}".rstrip("/") if project_path else sandbox_dir
    parents = sorted({str(PurePosixPath(path).parent) for path, _ in full_uploads})
    try:
        await asyncio.to_thread(
            sandbox_backend.execute,
            "mkdir -p " + " ".join(f"'{parent}'" for parent in parents),
            timeout=30,
        )
        responses = await asyncio.to_thread(sandbox_backend.upload_files, full_uploads)
        failed = [
            {"path": item.path, "error": item.error}
            for item in responses
            if item.error
        ]
        if failed:
            return {
                "ok": False,
                "error": "上传项目到沙箱失败",
                "failed": failed[:20],
                "mode": "sandbox",
            }

        command = plan.command
        if plan.sandbox_prepare and plan.sandbox_prepare != "true":
            command = f"{plan.sandbox_prepare} && {command}"
        response = await asyncio.to_thread(
            sandbox_backend.execute,
            f"cd '{workdir}' && {command}",
            timeout=max(60, int(timeout)),
        )
    except Exception as error:  # noqa: BLE001 - 工具边界统一转为可读错误
        return {
            "ok": False,
            "error": f"沙箱编译失败：{error}",
            "mode": "sandbox",
            "language": plan.language,
        }

    text, truncated = _truncate(str(getattr(response, "output", "") or ""))
    return {
        "ok": getattr(response, "exit_code", 1) == 0,
        "mode": "sandbox",
        "language": plan.language,
        "label": plan.label,
        "command": command,
        "exit_code": getattr(response, "exit_code", 1),
        "output": text,
        "truncated": truncated,
        "sandbox_dir": workdir,
        "installed_in_sandbox": plan.installs,
        "skipped_files": skipped[:20],
        "reason": plan.reason,
    }


def _build_scope_key(project_id: str, language: str) -> str:
    return f"build|project|{project_id}|{language}"


def _session_scope_key(thread_id: str, language: str) -> str:
    return f"build|session|{thread_id}|{language}"


def _parse_build_decision(value: Any) -> tuple[str, str]:
    """解析编译审批载荷；未知值按拒绝处理。"""
    if isinstance(value, str):
        raw, message = value.strip().lower(), ""
    elif isinstance(value, dict):
        raw = str(value.get("decision") or value.get("type") or "").strip().lower()
        message = str(value.get("message") or "").strip()
    else:
        raw, message = "", ""
    if raw in {"approve", "approved", "once"}:
        return "once", message
    if raw in {"approve_project", "project", "allow_project"}:
        return "project", message
    if raw in {"approve_thread", "approve_session", "remember", "allow_thread"}:
        return "session", message
    return "reject", message


def _ask_local_build(
    *,
    project_id: str,
    language: str,
    label: str,
    action: str,
    command: str,
    cwd: Path,
) -> tuple[bool, str, str]:
    """本机执行编译前必须审批；返回（是否允许, 作用域, 拒绝原因）。"""
    project_key = _build_scope_key(project_id, language)
    if project_key in _PROJECT_BUILD_ALLOWLIST:
        return True, "project", ""

    thread_id = current_thread_id().strip()
    session_key = _session_scope_key(thread_id, language) if thread_id else ""
    if thread_id and session_key in _SESSION_BUILD_ALLOWLIST.get(thread_id, set()):
        return True, "session", ""

    payload = {
        "type": "host_approval",
        "host_approval": {
            "tool": "compile_project",
            "summary": f"在本机执行{label} {action}：{command}",
            "reason": "本机已具备该运行时。批准后只执行固定编译命令，不安装运行时或系统包。",
            "args": {
                "command": command,
                "cwd": str(cwd),
                "language": language,
                "action": action,
            },
            "scope": "project",
            "options": [
                {"value": "approve", "label": "允许本次"},
                {"value": "approve_project", "label": "本项目内允许"},
                {"value": "approve_thread", "label": "仅本会话允许"},
                {"value": "reject", "label": "拒绝"},
            ],
        },
    }
    try:
        decision, message = _parse_build_decision(interrupt(payload))
    except GraphBubbleUp:
        # interrupt() 通过抛出 GraphInterrupt 暂停图；必须原样冒泡，
        # 否则审批永远到不了前端，调用会被误判为「通道不可用」。
        raise
    except Exception:
        return False, "reject", "编译审批通道不可用，已拒绝在本机执行"

    if decision == "once":
        return True, "once", ""
    if decision == "project":
        _PROJECT_BUILD_ALLOWLIST.add(project_key)
        return True, "project", ""
    if decision == "session":
        if thread_id:
            _SESSION_BUILD_ALLOWLIST.setdefault(thread_id, set()).add(session_key)
        return True, "session", ""
    return False, "reject", message or "用户拒绝在本机执行编译"


def create_build_tools(sandbox_backend: Any | None) -> list[Any]:
    """构造 ``compile_project``：本机运行时优先，缺失时自动转沙箱。"""

    @tool
    async def compile_project(
        action: Literal["build", "test"] = "build",
        project_path: str = "",
        timeout: int = 600,
    ) -> str:
        """编译或测试项目代码。

        优先使用本机已有的运行时；本机缺少运行时、依赖产物，或离线编译失败时，
        自动把项目上传到沙箱，在沙箱安装环境后编译。不会在本机安装任何东西。

        Args:
            action: build=编译，test=运行测试。
            project_path: 项目内相对路径，默认为项目根目录。
            timeout: 超时秒数，默认 600。
        """
        try:
            project = current_project()
        except ProjectContextError as error:
            return json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False)
        if project.kind != "project" or project.root is None:
            return json.dumps(
                {
                    "ok": False,
                    "error": "compile_project 只支持项目会话；自由会话请使用 run_in_sandbox",
                },
                ensure_ascii=False,
            )

        root = project.root.resolve()
        relative = (project_path or "").strip().strip("/\\")
        workdir = (root / relative).resolve() if relative else root
        try:
            workdir.relative_to(root)
        except ValueError:
            return json.dumps(
                {"ok": False, "error": "project_path 越出项目目录"},
                ensure_ascii=False,
            )

        plan = build_plan(workdir, action)
        if plan.mode == "unsupported":
            return json.dumps(
                {
                    "ok": False,
                    "error": plan.reason,
                    "mode": "unsupported",
                },
                ensure_ascii=False,
            )

        local_error: dict[str, Any] | None = None
        if plan.mode == "local":
            allowed, scope, deny_reason = _ask_local_build(
                project_id=str(project.id),
                language=plan.language,
                label=plan.label,
                action=action,
                command=plan.command,
                cwd=workdir,
            )
            if not allowed:
                return json.dumps(
                    {
                        "ok": False,
                        "mode": "local",
                        "rejected": True,
                        "scope": scope,
                        "language": plan.language,
                        "command": plan.command,
                        "error": deny_reason,
                    },
                    ensure_ascii=False,
                )
            result = await asyncio.to_thread(
                _run_local, plan.argv, workdir, max(1, int(timeout))
            )
            payload = {
                "ok": result["exit_code"] == 0 and not result["timed_out"],
                "mode": "local",
                "approval_scope": scope,
                "language": plan.language,
                "label": plan.label,
                "command": plan.command,
                **result,
            }
            if payload["ok"] or not _needs_sandbox(str(result.get("output") or "")):
                return json.dumps(payload, ensure_ascii=False)
            # 本地离线失败且原因是缺少依赖：保留本地错误，转沙箱重试。
            local_error = payload
            plan = sandbox_plan(
                plan.language,
                action,
                reason="本地离线编译缺少依赖，已转沙箱",
            )

        sandbox_dir = sandbox_project_dir(project.id)
        result = await _run_sandbox(
            plan,
            sandbox_backend=sandbox_backend,
            sandbox_dir=sandbox_dir,
            project_path=relative,
            timeout=max(60, int(timeout)),
        )
        if local_error is not None:
            result["local_attempt"] = {
                "exit_code": local_error["exit_code"],
                "output": local_error["output"],
                "reason": "本地离线编译缺少依赖，已转沙箱",
            }
        return json.dumps(result, ensure_ascii=False)

    return [compile_project]
