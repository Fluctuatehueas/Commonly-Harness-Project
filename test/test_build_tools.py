"""编译工具的语言识别与本地/沙箱选择逻辑测试。"""

from __future__ import annotations

from pathlib import Path

from agent.project_context import (
    ProjectContext,
    reset_project_context,
    reset_project_thread,
    set_project_context,
    set_project_thread,
)
from agent.tools import build_tools as bt
from agent.tools.build_tools import (
    _build_scope_key,
    _parse_build_decision,
    _session_scope_key,
    build_plan,
    collect_project_files,
    detect_language,
    sandbox_plan,
)


def _probe(available: bool, command: str = "dotnet") -> dict:
    return {
        "command": command,
        "available": available,
        "satisfied": available,
        "version": "8.0.100" if available else "",
    }


def test_detect_language(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.csproj").write_text("<Project />", encoding="utf-8")
    assert detect_language(tmp_path) == "csharp"

    (tmp_path / "go.mod").write_text("module demo\n", encoding="utf-8")
    assert detect_language(tmp_path) == "csharp"  # C# 优先，避免多语言仓库误判

    only_go = tmp_path / "go"
    only_go.mkdir()
    (only_go / "go.mod").write_text("module demo\n", encoding="utf-8")
    assert detect_language(only_go) == "go"

    only_cpp = tmp_path / "cpp"
    only_cpp.mkdir()
    (only_cpp / "CMakeLists.txt").write_text("project(demo)\n", encoding="utf-8")
    assert detect_language(only_cpp) == "cpp"


def test_csharp_local_when_runtime_and_assets_exist(tmp_path: Path) -> None:
    (tmp_path / "App.csproj").write_text("<Project />", encoding="utf-8")
    (tmp_path / "obj").mkdir()
    (tmp_path / "obj" / "project.assets.json").write_text("{}", encoding="utf-8")

    plan = build_plan(
        tmp_path, "build", probe=lambda name: _probe(True, name), check_toolchain=False
    )
    assert plan.mode == "local"
    assert plan.argv[1] == "build"
    assert "dotnet" in plan.argv[0].lower()


def test_csharp_sandbox_when_runtime_missing(tmp_path: Path) -> None:
    (tmp_path / "App.csproj").write_text("<Project />", encoding="utf-8")
    (tmp_path / "obj").mkdir()
    (tmp_path / "obj" / "project.assets.json").write_text("{}", encoding="utf-8")

    plan = build_plan(
        tmp_path, "build", probe=lambda name: _probe(False, name), check_toolchain=False
    )
    assert plan.mode == "sandbox"
    assert "dotnet" in plan.missing
    assert "dotnet build" in plan.command


def test_node_without_modules_goes_to_sandbox(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"build":"tsc"}}', encoding="utf-8")

    plan = build_plan(
        tmp_path,
        "build",
        probe=lambda name: _probe(True, name),
        check_toolchain=False,
    )
    assert plan.mode == "sandbox"
    assert "node_modules" in plan.reason


def test_collect_skips_dependency_and_sensitive_files(tmp_path: Path) -> None:
    (tmp_path / "Program.cs").write_text("class P {}", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "pkg.js").write_text("x", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=1", encoding="utf-8")

    uploads, skipped = collect_project_files(tmp_path)
    paths = {path for path, _ in uploads}
    assert "Program.cs" in paths
    assert not any("node_modules" in path for path in paths)
    assert any("敏感文件" == item["reason"] for item in skipped)


def test_sandbox_plan_has_install_and_command() -> None:
    plan = sandbox_plan("csharp", "build", reason="local missing")
    assert plan.mode == "sandbox"
    assert "dotnet" in plan.sandbox_prepare
    assert any(".NET SDK" in item for item in plan.installs)


def test_build_approval_decision_scopes() -> None:
    assert _parse_build_decision({"decision": "approve"})[0] == "once"
    assert _parse_build_decision({"decision": "approve_project"})[0] == "project"
    assert _parse_build_decision({"decision": "approve_thread"})[0] == "session"
    assert _parse_build_decision({"decision": "reject", "message": "不编译"}) == (
        "reject",
        "不编译",
    )
    assert _parse_build_decision("unknown")[0] == "reject"
    assert _build_scope_key("p1", "csharp") == "build|project|p1|csharp"
    assert _session_scope_key("t1", "go") == "build|session|t1|go"


def test_local_build_allowlist_scopes(tmp_path: Path) -> None:
    bt._PROJECT_BUILD_ALLOWLIST.clear()
    bt._SESSION_BUILD_ALLOWLIST.clear()
    project = ProjectContext(id="p-allow", name="demo", root=tmp_path)
    token = set_project_context(project)
    thread_token = set_project_thread("t-allow")
    original = bt.interrupt
    try:
        bt.interrupt = lambda payload: {"decision": "approve_project"}
        allowed, scope, _ = bt._ask_local_build(
            project_id=project.id,
            language="csharp",
            label="C#",
            action="build",
            command="dotnet build",
            cwd=tmp_path,
        )
        assert allowed is True and scope == "project"

        bt.interrupt = lambda payload: {"decision": "approve_thread"}
        allowed, scope, _ = bt._ask_local_build(
            project_id=project.id,
            language="go",
            label="Go",
            action="build",
            command="go build ./...",
            cwd=tmp_path,
        )
        assert allowed is True and scope == "session"

        bt.interrupt = lambda payload: {"decision": "reject", "message": "不允许"}
        allowed, scope, reason = bt._ask_local_build(
            project_id=project.id,
            language="python",
            label="Python",
            action="build",
            command="python -m compileall .",
            cwd=tmp_path,
        )
        assert allowed is False and scope == "reject" and reason == "不允许"
    finally:
        bt.interrupt = original
        reset_project_thread(thread_token)
        reset_project_context(token)
        bt._PROJECT_BUILD_ALLOWLIST.clear()
        bt._SESSION_BUILD_ALLOWLIST.clear()
