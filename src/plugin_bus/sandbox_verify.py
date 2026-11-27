"""安装前沙箱试运行。

在 OpenSandbox 内完成 ``initialize → describe → health`` 冒烟，并采集进程输出。
超时、崩溃、缺少响应都直接判失败，避免把不可运行或越权的包装进宿主。
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import uuid
from pathlib import Path
from typing import Any

from plugin_bus import protocol
from plugin_bus.errors import PluginPackageInvalid
from plugin_bus.manifest import Manifest
from plugin_bus.storage import iter_package_files

logger = logging.getLogger(__name__)


def _build_shell(manifest: Manifest, remote_root: str, requests: list[dict[str, Any]]) -> str:
    lines = " ".join(shlex.quote(protocol.dumps(item)) for item in requests)
    command = " ".join(
        [shlex.quote(manifest.runtime.command), *(shlex.quote(arg) for arg in manifest.runtime.args)]
    )
    return f"cd {shlex.quote(remote_root)} && printf '%s\\n' {lines} | {command}"


def _run(
    backend: Any,
    manifest: Manifest,
    package_root: Path,
    remote_root: str,
    timeout: int,
) -> dict[str, Any]:
    backend.execute(f"mkdir -p {shlex.quote(remote_root)}")
    files = [
        (f"{remote_root}/{path.relative_to(package_root).as_posix()}", data)
        for path, data in iter_package_files(package_root)
    ]
    responses = backend.upload_files(files)
    failed = [item for item in responses if getattr(item, "error", None)]
    if failed:
        raise PluginPackageInvalid(f"插件包上传沙箱失败: {failed[0].error}")

    initialize = protocol.request(
        protocol.METHOD_INITIALIZE,
        {
            "protocolVersion": protocol.PROTOCOL_VERSION,
            "plugin_id": manifest.plugin_id,
            "version": manifest.version,
            "user_id": "__verify__",
            "permissions": list(manifest.permissions),
            "config": {},
        },
    )
    describe = protocol.request(protocol.METHOD_DESCRIBE, {})
    health = protocol.request(protocol.METHOD_HEALTH, {})
    shell = _build_shell(manifest, remote_root, [initialize, describe, health])
    result = backend.execute(shell, timeout=timeout)
    output = str(getattr(result, "output", "") or "")
    exit_code = getattr(result, "exit_code", None)

    parsed: dict[str, dict[str, Any]] = {}
    for line in output.splitlines():
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            message = protocol.parse_message(text)
        except ValueError:
            continue
        if protocol.message_kind(message) == "response" and "id" in message:
            parsed[str(message.get("id"))] = message

    missing = [
        item["method"]
        for item in (initialize, describe, health)
        if str(item["id"]) not in parsed
    ]
    if missing:
        raise PluginPackageInvalid(
            f"沙箱试运行未完成，缺少响应: {missing}（exit={exit_code}）: {output[-400:]}"
        )
    for item in (initialize, describe, health):
        response = parsed[str(item["id"])]
        if "error" in response:
            error = response.get("error") or {}
            raise PluginPackageInvalid(
                f"沙箱试运行失败: {item['method']} -> {error.get('message')}"
            )
    return {
        "ok": True,
        "skipped": False,
        "exit_code": exit_code,
        "output_tail": output[-2000:],
        "describe": parsed[str(describe["id"])].get("result"),
    }


async def verify_package(
    *,
    package_root: Path,
    manifest: Manifest,
    sandbox_backend: Any | None,
    required: bool = True,
    timeout: int = 60,
) -> dict[str, Any]:
    """执行沙箱试运行；没有沙箱时按 ``required`` 决定拒绝还是跳过。"""
    if manifest.runtime.kind == "remote":
        # 远程插件没有包内容，试运行由安装后的握手与健康检查承担。
        return {"ok": True, "skipped": True, "reason": "remote"}
    if sandbox_backend is None:
        if required:
            raise PluginPackageInvalid(
                "沙箱不可用，已拒绝安装（HARNESS_PLUGIN_SANDBOX_VERIFY=true）"
            )
        return {"ok": True, "skipped": True, "reason": "sandbox_unavailable"}
    remote_root = f"/tmp/harness-plugin-verify/{uuid.uuid4().hex}"
    try:
        result = await asyncio.to_thread(
            _run, sandbox_backend, manifest, package_root, remote_root, timeout
        )
        return result
    finally:
        try:
            await asyncio.to_thread(
                sandbox_backend.execute, f"rm -rf {shlex.quote(remote_root)}"
            )
        except Exception:
            logger.debug("清理沙箱试运行目录失败", exc_info=True)


__all__ = ["verify_package"]
