"""MCP Client 冒烟测试：stdio 与 Streamable HTTP。

直接运行：
    .\\.venv\\Scripts\\python.exe test\\test_mcp_integration.py

也兼容 pytest：
    pytest test/test_mcp_integration.py -q
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
DEMO_SERVER = ROOT / "test" / "mcp_demo_server.py"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent.tools.mcp_client import load_mcp_tools_for_servers  # noqa: E402


def _write_config(directory: Path, server_name: str, server_config: dict) -> Path:
    """把单个 MCP Server 配置写入临时 YAML。"""
    path = directory / "mcp_servers.yaml"
    path.write_text(
        yaml.safe_dump(
            {"main_servers": [], "servers": {server_name: server_config}},
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


async def _assert_tools(tools: list) -> None:
    """验证工具发现与两个工具的真实调用。"""
    by_name = {str(getattr(tool, "name", "")): tool for tool in tools}
    assert {"echo", "add", "current_time"} <= set(by_name), by_name.keys()

    echo_result = await by_name["echo"].ainvoke({"text": "hello-mcp"})
    assert "hello-mcp" in str(echo_result), echo_result

    add_result = await by_name["add"].ainvoke({"a": 2, "b": 3})
    assert "5" in str(add_result), add_result


async def _run_stdio(config_path: Path) -> None:
    tools = await load_mcp_tools_for_servers(
        ["demo_stdio"], config_path=config_path,
    )
    await _assert_tools(tools)


async def _run_subagent_yaml(config_path: Path, subagent_path: Path) -> None:
    """验证 YAML 的 mcp_servers 能通过 aload_subagent 注入工具。"""
    from agent.subagents.loader import aload_subagent

    subagent = await aload_subagent(
        subagent_path,
        mcp_config_path=config_path,
    )
    names = {str(getattr(tool, "name", "")) for tool in subagent["tools"]}
    assert {"echo", "add"} <= names, names


async def _run_http(config_path: Path) -> None:
    tools = await load_mcp_tools_for_servers(
        ["demo_http"], config_path=config_path,
    )
    await _assert_tools(tools)


def _free_port() -> int:
    """获取一个空闲的本机端口。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _wait_port(port: int, process: subprocess.Popen, timeout: float = 15) -> None:
    """等待 MCP HTTP 服务端口可连接。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("MCP HTTP 服务启动失败")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return
        except OSError:
            time.sleep(0.25)
    raise TimeoutError("等待 MCP HTTP 服务超时")


def test_mcp_stdio() -> None:
    """验证 stdio 传输：Client 拉起子进程并调用工具。"""
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        config_path = _write_config(
            directory,
            "demo_stdio",
            {
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(DEMO_SERVER)],
                "cwd": str(ROOT),
            },
        )
        asyncio.run(_run_stdio(config_path))


def test_mcp_subagent_yaml() -> None:
    """验证子 Agent YAML 的 mcp_servers 声明。"""
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        config_path = _write_config(
            directory,
            "demo_stdio",
            {
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(DEMO_SERVER)],
                "cwd": str(ROOT),
            },
        )
        subagent_path = directory / "subagent.yaml"
        subagent_path.write_text(
            yaml.safe_dump(
                {
                    "name": "mcp_smoke_agent",
                    "description": "验证 MCP 工具注入的子 Agent。",
                    "tools": ["echo", "add"],
                    "mcp_servers": ["demo_stdio"],
                    "system_prompt": "你是测试助手。",
                },
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        asyncio.run(_run_subagent_yaml(config_path, subagent_path))


def test_mcp_http() -> None:
    """验证 Streamable HTTP 传输：独立进程 + URL 连接。"""
    port = _free_port()
    environment = os.environ.copy()
    environment["MCP_DEMO_PORT"] = str(port)
    process = subprocess.Popen(
        [sys.executable, str(DEMO_SERVER), "--http"],
        cwd=str(ROOT),
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_port(port, process)
        with tempfile.TemporaryDirectory() as temporary:
            config_path = _write_config(
                Path(temporary),
                "demo_http",
                {
                    "transport": "streamable_http",
                    "url": f"http://127.0.0.1:{port}/mcp",
                },
            )
            asyncio.run(_run_http(config_path))
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    test_mcp_stdio()
    print("stdio MCP test passed")
    test_mcp_subagent_yaml()
    print("subagent MCP YAML test passed")
    test_mcp_http()
    print("http MCP test passed")
