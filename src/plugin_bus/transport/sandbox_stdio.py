"""沙箱内一次性插件传输。

项目级插件与高风险插件不允许在宿主执行；每次调用在 OpenSandbox 内上传包、
拉起一次进程完成「initialize → 目标方法 → 退出」，不做常驻与心跳。
"""

from __future__ import annotations

import asyncio
import json
import logging
import shlex
from pathlib import Path
from typing import Any

from plugin_bus import protocol
from plugin_bus.errors import PluginError
from plugin_bus.storage import iter_package_files
from plugin_bus.transport.base import HostHandler, Transport

logger = logging.getLogger(__name__)


class SandboxStdioTransport(Transport):
    """通过沙箱 backend 执行一次性的 stdio 会话。

    ``host.*`` 反向调用在此模式下不可用：一次性进程无法与宿主往返通信，
    插件应把状态放在 ``host.storage``（由 runtime 在调用前注入）或沙箱文件。
    """

    def __init__(
        self,
        *,
        plugin_id: str,
        version: str,
        package_root: Path,
        command: str,
        args: list[str] | tuple[str, ...] = (),
        sandbox_backend: Any,
        remote_root: str,
        host_handler: HostHandler | None = None,
    ) -> None:
        super().__init__(
            plugin_id=plugin_id,
            version=version,
            host_handler=host_handler,
            log_path=None,
        )
        self.package_root = Path(package_root)
        self.command = command
        self.args = list(args)
        self.sandbox_backend = sandbox_backend
        self.remote_root = remote_root.rstrip("/")
        self._init_params: dict[str, Any] = {}
        self._uploaded = False

    async def open(self) -> None:
        if self._uploaded:
            return
        await asyncio.to_thread(self._upload)

    def _upload(self) -> None:
        result = self.sandbox_backend.execute(
            f"mkdir -p {shlex.quote(self.remote_root)}"
        )
        if getattr(result, "exit_code", None) not in (0, None):
            raise PluginError(
                f"沙箱目录创建失败: {result}",
                code="PLUGIN_PACKAGE_INVALID",
                plugin_id=self.plugin_id,
            )
        files: list[tuple[str, bytes]] = []
        for path, data in iter_package_files(self.package_root):
            relative = path.relative_to(self.package_root).as_posix()
            files.append((f"{self.remote_root}/{relative}", data))
        responses = self.sandbox_backend.upload_files(files)
        failed = [item for item in responses if getattr(item, "error", None)]
        if failed:
            raise PluginError(
                f"插件包上传沙箱失败: {failed[0].error}",
                code="PLUGIN_PACKAGE_INVALID",
                plugin_id=self.plugin_id,
            )
        self._uploaded = True

    async def request(
        self, method: str, params: dict[str, Any], *, timeout: float
    ) -> Any:
        if method == protocol.METHOD_INITIALIZE:
            self._init_params = dict(params)
            batch = [(protocol.METHOD_INITIALIZE, params)]
        else:
            batch = [
                (protocol.METHOD_INITIALIZE, self._init_params),
                (method, params),
            ]
        responses = await asyncio.to_thread(self._run_batch, batch, timeout)
        if method == protocol.METHOD_INITIALIZE:
            return responses[0]
        return responses[-1]

    def _run_batch(
        self,
        batch: list[tuple[str, dict[str, Any]]],
        timeout: float,
    ) -> list[Any]:
        requests = [protocol.request(method, params) for method, params in batch]
        lines = " ".join(shlex.quote(protocol.dumps(item)) for item in requests)
        command = " ".join(
            [shlex.quote(self.command), *(shlex.quote(arg) for arg in self.args)]
        )
        shell = (
            f"cd {shlex.quote(self.remote_root)} && "
            f"printf '%s\\n' {lines} | {command}"
        )
        result = self.sandbox_backend.execute(shell, timeout=int(timeout))
        output = str(getattr(result, "output", "") or "")
        exit_code = getattr(result, "exit_code", None)
        responses: dict[str, dict[str, Any]] = {}
        for line in output.splitlines():
            text = line.strip()
            if not text.startswith("{"):
                continue
            try:
                message = protocol.parse_message(text)
            except ValueError:
                continue
            if protocol.message_kind(message) == "response" and "id" in message:
                responses[str(message.get("id"))] = message
        ordered: list[Any] = []
        for item in requests:
            response = responses.get(str(item["id"]))
            if response is None:
                raise PluginError(
                    f"沙箱插件未返回结果（exit={exit_code}）: {output[-500:]}",
                    code="PLUGIN_CRASHED",
                    plugin_id=self.plugin_id,
                )
            if "error" in response:
                error = response.get("error") or {}
                raise PluginError(
                    str(error.get("message") or "沙箱插件调用失败"),
                    code=_code_name(error.get("code")),
                    plugin_id=self.plugin_id,
                )
            ordered.append(response.get("result"))
        if exit_code not in (0, None) and not ordered:
            raise PluginError(
                f"沙箱插件进程失败（exit={exit_code}）",
                code="PLUGIN_CRASHED",
                plugin_id=self.plugin_id,
            )
        return ordered

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        # 一次性进程无法接收通知，忽略。
        return

    async def close(self) -> None:
        self._uploaded = False

    @property
    def connected(self) -> bool:
        return self._uploaded


def _code_name(code: Any) -> str:
    try:
        return protocol.ERROR_NAMES.get(int(code), "PLUGIN_TOOL_FAILED")
    except (TypeError, ValueError):
        return "PLUGIN_TOOL_FAILED"


__all__ = ["SandboxStdioTransport"]
