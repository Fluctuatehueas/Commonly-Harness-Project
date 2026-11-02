"""宿主能力工具的最小验证脚本（不依赖 pytest）。

运行：
    .\\.venv\\Scripts\\python.exe test\\test_host_access.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import agent.config as host_config
from agent.project_context import set_project_thread
from agent.tools import host_tools


def check_build_switches() -> None:
    original = (
        host_config.HOST_ACCESS_ENABLED,
        host_config.HOST_EXEC_ENABLED,
        host_config.HOST_READ_ENABLED,
        host_config.HOST_WRITE_ENABLED,
    )
    try:
        host_config.HOST_ACCESS_ENABLED = False
        assert host_tools.build_host_tools() == []
        host_config.HOST_ACCESS_ENABLED = True
        host_config.HOST_EXEC_ENABLED = True
        host_config.HOST_READ_ENABLED = True
        host_config.HOST_WRITE_ENABLED = True
        names = {tool.name for tool in host_tools.build_host_tools()}
        assert names == {"host_execute", "host_read_file", "host_write_file"}, names
    finally:
        (
            host_config.HOST_ACCESS_ENABLED,
            host_config.HOST_EXEC_ENABLED,
            host_config.HOST_READ_ENABLED,
            host_config.HOST_WRITE_ENABLED,
        ) = original
    print("ok: build switches")


def check_paths() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        normal = root / "notes.txt"
        normal.write_text("hello", encoding="utf-8")
        resolved = host_tools._resolve_host_path(str(normal))
        assert resolved == normal.resolve()

        for forbidden in (root / ".commonharness" / "project.json", root / ".env"):
            try:
                host_tools._resolve_host_path(str(forbidden), must_exist=False)
            except host_tools.HostPathError:
                continue
            raise AssertionError(f"应当拒绝：{forbidden}")

        link = root / "link.txt"
        try:
            link.symlink_to(normal)
        except (OSError, NotImplementedError):
            print("skip: 当前环境无法创建符号链接")
        else:
            try:
                host_tools._resolve_host_path(str(link))
            except host_tools.HostPathError:
                pass
            else:
                raise AssertionError("应当拒绝符号链接")
    print("ok: path policy")


def check_write_roots() -> None:
    with tempfile.TemporaryDirectory() as temp:
        outside = Path(temp) / "out" / "a.txt"
        outside.parent.mkdir()
        original = host_config.HOST_WRITE_ROOTS
        try:
            host_config.HOST_WRITE_ROOTS = []
            assert not host_tools._write_target_allowed(outside)
            host_config.HOST_WRITE_ROOTS = [Path(temp).resolve()]
            assert host_tools._write_target_allowed(outside)
        finally:
            host_config.HOST_WRITE_ROOTS = original
    print("ok: write roots")


def check_commands() -> None:
    for denied in ("shutdown /s", "diskpart", "rm -rf /tmp/x"):
        try:
            host_tools._check_command(denied)
        except host_tools.HostPathError:
            continue
        raise AssertionError(f"应当拒绝命令：{denied}")
    host_tools._check_command("python --version")
    host_tools._check_command("git status")
    print("ok: command policy")


def check_thread_allowlist() -> None:
    host_tools._reset_allowlist()
    set_project_thread("thread-a")
    host_tools._remember("exec|bash|echo hi|/tmp")
    assert host_tools._allowlist_key("exec|bash|echo hi|/tmp")
    set_project_thread("thread-b")
    assert not host_tools._allowlist_key("exec|bash|echo hi|/tmp")
    host_tools._reset_allowlist()
    set_project_thread("")
    print("ok: thread allowlist")


def check_run_command() -> None:
    command = "Start-Sleep -Seconds 5" if sys.platform == "win32" else "sleep 5"
    with tempfile.TemporaryDirectory() as temp:
        result = asyncio.run(
            asyncio.to_thread(
                host_tools._run_command,
                command,
                "powershell" if sys.platform == "win32" else "bash",
                Path(temp),
                1,
            )
        )
    assert result["timed_out"] is True, result
    assert result["exit_code"] == -1, result
    print("ok: timeout kill")


def check_tool_disabled_guard() -> None:
    original = host_config.HOST_ACCESS_ENABLED
    try:
        host_config.HOST_ACCESS_ENABLED = False
        payload = asyncio.run(
            host_tools.host_execute.ainvoke(
                {"command": "echo hi", "reason": "test"}
            )
        )
        data = json.loads(payload)
        assert data["ok"] is False and data["error"]["code"] == "HOST_DISABLED", data
    finally:
        host_config.HOST_ACCESS_ENABLED = original
    print("ok: disabled guard")


def check_interrupt_flow() -> None:
    """用最小 LangGraph 验证：中断 → 批准并记住 → 同会话不再中断 → 换会话重新中断。"""
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, MessagesState, StateGraph
    from langgraph.prebuilt import ToolNode
    from langgraph.types import Command

    from agent.project_context import ProjectContext, set_project_context

    originals = (
        host_config.HOST_ACCESS_ENABLED,
        host_config.HOST_READ_ENABLED,
    )
    host_config.HOST_ACCESS_ENABLED = True
    host_config.HOST_READ_ENABLED = True
    host_tools._reset_allowlist()

    try:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "hello.txt"
            target.write_text("hello host\nline2\n", encoding="utf-8")
            token = set_project_context(
                ProjectContext(
                    id="test-project",
                    name="test",
                    root=root,
                    kind="project",
                    status="active",
                    marker_state="ok",
                )
            )
            set_project_thread("interrupt-thread")

            builder = StateGraph(MessagesState)
            builder.add_node("tools", ToolNode([host_tools.host_read_file]))
            builder.add_edge(START, "tools")
            builder.add_edge("tools", END)
            graph = builder.compile(checkpointer=InMemorySaver())

            def tool_call(call_id: str) -> AIMessage:
                return AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "host_read_file",
                            "args": {"path": str(target), "reason": "test"},
                            "id": call_id,
                        }
                    ],
                )

            config = {"configurable": {"thread_id": "interrupt-thread"}}

            async def run_flow() -> None:
                await graph.ainvoke({"messages": [tool_call("call-1")]}, config)
                interrupts = graph.get_state(config).interrupts
                assert interrupts, "首次调用应触发审批中断"
                payload = interrupts[0].value
                assert payload.get("type") == "host_approval", payload
                assert payload["host_approval"]["tool"] == "host_read_file", payload

                resumed = await graph.ainvoke(
                    Command(resume={"decision": "approve_thread"}),
                    config,
                )
                tool_messages = [
                    message
                    for message in resumed["messages"]
                    if getattr(message, "type", "") == "tool"
                ]
                assert tool_messages and "hello host" in str(tool_messages[-1].content)

                # 同一会话同一路径：不应再次中断。
                second = await graph.ainvoke({"messages": [tool_call("call-2")]}, config)
                assert not graph.get_state(config).interrupts, "白名单内的调用不应再次审批"
                second_tools = [
                    message
                    for message in second["messages"]
                    if getattr(message, "type", "") == "tool"
                ]
                assert second_tools and "hello host" in str(second_tools[-1].content)

                # 换会话：必须重新审批。
                set_project_thread("another-thread")
                other_config = {"configurable": {"thread_id": "another-thread"}}
                await graph.ainvoke({"messages": [tool_call("call-3")]}, other_config)
                assert graph.get_state(other_config).interrupts, "换会话后应重新审批"

            asyncio.run(run_flow())
    finally:
        (
            host_config.HOST_ACCESS_ENABLED,
            host_config.HOST_READ_ENABLED,
        ) = originals
        host_tools._reset_allowlist()
        set_project_thread("")
    print("ok: interrupt + thread allowlist flow")


def main() -> None:
    check_build_switches()
    check_paths()
    check_write_roots()
    check_commands()
    check_thread_allowlist()
    check_run_command()
    check_tool_disabled_guard()
    check_interrupt_flow()
    print("all host access checks passed")


if __name__ == "__main__":
    main()
