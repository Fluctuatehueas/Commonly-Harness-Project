"""对插件执行 initialize → describe → health → shutdown 冒烟测试。

用法：
    python scripts/smoke_plugin.py <plugin_dir>

支持 subprocess/stdio 与 inprocess 两种运行形态；只依赖标准库。
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import inspect
import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

REQUIRED_METHODS = ("plugin.initialize", "plugin.describe", "plugin.health", "plugin.shutdown")
METHOD_ATTRS = {
    "plugin.initialize": "initialize",
    "plugin.describe": "describe",
    "plugin.health": "health",
    "plugin.shutdown": "shutdown",
}


def _invoke(target: Any, method: str) -> Any:
    if callable(target) and not hasattr(target, "handle"):
        result = target(method, {})
    else:
        handler = getattr(target, "handle", None)
        if callable(handler):
            result = handler(method, {})
        else:
            impl = getattr(target, METHOD_ATTRS[method], None)
            if not callable(impl):
                fail(f"入口未实现方法: {method}")
            result = impl({})
    if inspect.isawaitable(result):
        result = asyncio.run(result)
    return result


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def load_manifest(plugin_dir: Path) -> dict[str, Any]:
    path = plugin_dir / "manifest.json"
    if not path.is_file():
        fail(f"缺少 manifest.json: {plugin_dir}")
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"manifest.json 读取失败: {error}")
    return {}


def request(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": f"smoke-{method}",
        "method": method,
        "params": params or {},
    }


def run_inprocess(manifest: dict[str, Any], plugin_dir: Path) -> dict[str, Any]:
    entry = str((manifest.get("runtime") or {}).get("entry") or "")
    module_name, _, attr = entry.partition(":")
    module_file = plugin_dir / (module_name if module_name.endswith(".py") else f"{module_name}.py")
    if not module_file.is_file():
        fail(f"inprocess 入口不存在: {module_file}")
    spec = importlib.util.spec_from_file_location("smoke_builtin_plugin", module_file)
    if spec is None or spec.loader is None:
        fail(f"无法加载入口: {module_file}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    target = getattr(module, attr, None) if attr else module
    if target is None:
        fail(f"入口缺少属性: {entry}")
    results: dict[str, Any] = {}
    for method in REQUIRED_METHODS:
        results[method] = _invoke(target, method)
    return results


def run_subprocess(manifest: dict[str, Any], plugin_dir: Path, timeout: float) -> dict[str, Any]:
    runtime = manifest.get("runtime") or {}
    command = str(runtime.get("command") or "")
    args = [str(item) for item in runtime.get("args") or []]
    if not command:
        fail("subprocess 插件缺少 runtime.command")
    process = subprocess.Popen(
        [command, *args],
        cwd=str(plugin_dir),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )
    responses: queue.Queue[str] = queue.Queue()
    errors: list[str] = []

    def pump_stdout() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            if line.strip():
                responses.put(line.strip())

    def pump_stderr() -> None:
        assert process.stderr is not None
        for line in process.stderr:
            if line.strip():
                errors.append(line.strip())

    threading.Thread(target=pump_stdout, daemon=True).start()
    threading.Thread(target=pump_stderr, daemon=True).start()

    def call(method: str) -> Any:
        assert process.stdin is not None
        payload = request(method)
        process.stdin.write(json.dumps(payload) + "\n")
        process.stdin.flush()
        deadline = time.time() + timeout
        while time.time() < deadline:
            if process.poll() is not None and responses.empty():
                fail(
                    f"{method} 未返回，进程已退出（{process.returncode}）；stderr: {errors[-5:]}"
                )
            try:
                line = responses.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(message.get("id")) == str(payload["id"]):
                if "error" in message:
                    fail(f"{method} 返回错误: {message['error']}")
                return message.get("result")
        fail(f"{method} 超时（{timeout}s）")

    results = {method: call(method) for method in REQUIRED_METHODS}
    try:
        if process.stdin:
            process.stdin.write(
                json.dumps({"jsonrpc": "2.0", "method": "event.notify", "params": {}}) + "\n"
            )
            process.stdin.flush()
        process.wait(timeout=3)
    except Exception:
        process.terminate()
    if errors:
        print("stderr（最近 10 行）：", file=sys.stderr)
        for line in errors[-10:]:
            print(line, file=sys.stderr)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="插件协议冒烟测试")
    parser.add_argument("plugin_dir", help="插件目录（包含 manifest.json）")
    parser.add_argument("--timeout", type=float, default=10.0, help="单次请求超时秒数")
    args = parser.parse_args()

    plugin_dir = Path(args.plugin_dir).resolve()
    if not plugin_dir.is_dir():
        fail(f"插件目录不存在: {plugin_dir}")
    manifest = load_manifest(plugin_dir)
    kind = str((manifest.get("runtime") or {}).get("kind") or "")
    if kind == "inprocess":
        results = run_inprocess(manifest, plugin_dir)
    elif kind == "subprocess":
        results = run_subprocess(manifest, plugin_dir, args.timeout)
    else:
        fail(f"不支持的 runtime.kind: {kind}")

    describe = results.get("plugin.describe") or {}
    capabilities = describe.get("capabilities") or {}
    tools = [item.get("name") for item in capabilities.get("tool") or [] if isinstance(item, dict)]
    summary = {
        "ok": True,
        "plugin_id": manifest.get("plugin_id"),
        "version": manifest.get("version"),
        "runtime": kind,
        "tools": tools,
        "capabilities": sorted(capabilities.keys()),
        "health": results.get("plugin.health"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
