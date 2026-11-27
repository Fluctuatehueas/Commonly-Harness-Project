"""本地插件 stdio 传输。

总线拉起子进程，stdout 一行一条 JSON-RPC 消息，stderr 落插件日志。
插件反向发起的 ``host.*`` 请求由 HostBridge 处理，未声明的权限直接拒绝。

进程使用 ``subprocess.Popen`` + 后台读线程实现，而不是
``asyncio.create_subprocess_exec``：Windows 上项目统一使用 Selector 事件循环
（psycopg 要求），而 Selector 不支持 asyncio 子进程。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import subprocess
import threading
from pathlib import Path
from typing import Any

from plugin_bus import protocol
from plugin_bus.errors import PluginError, PluginTimeout
from plugin_bus.transport.base import HostHandler, Transport

logger = logging.getLogger(__name__)

MAX_LINE_BYTES = 8 * 1024 * 1024
ENV_WHITELIST = ("PATH", "LANG", "LC_ALL", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT")
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _error_code(name: str) -> int:
    inverse = {value: key for key, value in protocol.ERROR_NAMES.items()}
    return inverse.get(name, protocol.INTERNAL_ERROR)


def _error_to_response(request_id: Any, error: Exception) -> dict[str, Any]:
    if isinstance(error, PluginError):
        return protocol.error_response(
            request_id, _error_code(error.code), error.message
        )
    return protocol.error_response(
        request_id, protocol.ERROR_TOOL_FAILED, str(error)
    )


class StdioTransport(Transport):
    """宿主侧持久子进程传输。"""

    def __init__(
        self,
        *,
        plugin_id: str,
        version: str,
        cwd: Path,
        command: str,
        args: list[str] | tuple[str, ...] = (),
        host_handler: HostHandler | None = None,
        log_path: Path | None = None,
    ) -> None:
        super().__init__(
            plugin_id=plugin_id,
            version=version,
            host_handler=host_handler,
            log_path=log_path,
        )
        self.cwd = Path(cwd)
        self.command = command
        self.args = list(args)
        self._process: subprocess.Popen[bytes] | None = None
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._threads: list[threading.Thread] = []
        self._write_lock = threading.Lock()
        self._log_handle: Any = None
        self._log_lock = threading.Lock()
        self._closed = False

    def _environment(self) -> dict[str, str]:
        """只透传运行必需变量，不继承宿主密钥。"""
        env = {key: os.environ[key] for key in ENV_WHITELIST if key in os.environ}
        env["PYTHONPATH"] = str(self.cwd)
        env["PYTHONUNBUFFERED"] = "1"
        return env

    async def open(self) -> None:
        if self._process is not None:
            return
        if self.log_path is not None:
            try:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                self._log_handle = self.log_path.open("a", encoding="utf-8")
            except OSError:
                logger.debug("打开插件日志失败: %s", self.log_path, exc_info=True)
        self._loop = asyncio.get_running_loop()
        self._process = subprocess.Popen(
            [self.command, *self.args],
            cwd=str(self.cwd),
            env=self._environment(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            creationflags=CREATE_NO_WINDOW,
        )
        self._threads = [
            threading.Thread(
                target=self._pump_stdout,
                name=f"plugin-stdout-{self.plugin_id}",
                daemon=True,
            ),
            threading.Thread(
                target=self._pump_stderr,
                name=f"plugin-stderr-{self.plugin_id}",
                daemon=True,
            ),
        ]
        for thread in self._threads:
            thread.start()

    def _pump_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            for raw in iter(process.stdout.readline, b""):
                text = raw.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                loop = self._loop
                if loop is None or loop.is_closed():
                    continue
                loop.call_soon_threadsafe(self._on_line, text)
        except Exception:
            logger.debug("读取插件 stdout 失败: %s", self.plugin_id, exc_info=True)
        finally:
            loop = self._loop
            if loop is not None and not loop.is_closed():
                loop.call_soon_threadsafe(self._fail_pending, "插件进程已退出")

    def _pump_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        try:
            for raw in iter(process.stderr.readline, b""):
                text = raw.decode("utf-8", errors="replace").rstrip()
                if not text:
                    continue
                if self._log_handle is not None:
                    with self._log_lock:
                        self._log_handle.write(text + "\n")
                        self._log_handle.flush()
                else:
                    logger.debug("[plugin:%s] %s", self.plugin_id, text)
        except Exception:
            logger.debug("读取插件 stderr 失败: %s", self.plugin_id, exc_info=True)

    def _on_line(self, text: str) -> None:
        try:
            message = protocol.parse_message(text)
        except ValueError:
            logger.warning("插件返回非法 JSON（%s）：%s", self.plugin_id, text[:300])
            return
        kind = protocol.message_kind(message)
        if kind == "response":
            self._resolve(message)
        elif kind in {"request", "notification"}:
            asyncio.create_task(self._handle_host(message))

    async def _handle_host(self, message: dict[str, Any]) -> None:
        method = str(message.get("method") or "")
        request_id = message.get("id")
        params = message.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        if self.host_handler is None:
            if request_id is not None:
                await self._write(
                    protocol.error_response(
                        request_id,
                        protocol.ERROR_PERMISSION_DENIED,
                        "当前传输不支持反向调用宿主",
                    )
                )
            return
        try:
            result = await self.host_handler(method, params)
        except Exception as error:  # noqa: BLE001 - 统一转成协议错误
            if request_id is not None:
                await self._write(_error_to_response(request_id, error))
            else:
                logger.warning("插件通知处理失败: %s %s", method, error)
            return
        if request_id is not None:
            await self._write(protocol.success_response(request_id, result))

    def _resolve(self, message: dict[str, Any]) -> None:
        request_id = str(message.get("id"))
        future = self._pending.pop(request_id, None)
        if future is None or future.done():
            return
        if "error" in message:
            error = message.get("error") or {}
            future.set_exception(
                PluginError(
                    str(error.get("message") or "插件调用失败"),
                    code=_code_name(error.get("code")),
                    plugin_id=self.plugin_id,
                    detail={"rpc_code": error.get("code")},
                )
            )
        else:
            future.set_result(message.get("result"))

    def _fail_pending(self, message: str) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(
                    PluginError(message, code="PLUGIN_CRASHED", plugin_id=self.plugin_id)
                )
        self._pending.clear()

    async def _write(self, message: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.poll() is not None:
            raise PluginError(
                "插件进程未启动", code="PLUGIN_NOT_READY", plugin_id=self.plugin_id
            )
        data = (protocol.dumps(message) + "\n").encode("utf-8")
        try:
            with self._write_lock:
                process.stdin.write(data)
                process.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise PluginError(
                "插件进程已退出", code="PLUGIN_CRASHED", plugin_id=self.plugin_id
            ) from error

    async def request(
        self, method: str, params: dict[str, Any], *, timeout: float
    ) -> Any:
        if self._process is None:
            raise PluginError(
                "插件进程未启动", code="PLUGIN_NOT_READY", plugin_id=self.plugin_id
            )
        request_id = protocol.new_request_id()
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._write(protocol.request(method, params, request_id=request_id))
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError as error:
            raise PluginTimeout(
                f"插件调用超时: {method}", plugin_id=self.plugin_id
            ) from error
        finally:
            self._pending.pop(request_id, None)

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        await self._write(protocol.notification(method, params))

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        process = self._process
        self._process = None
        if process is not None:
            if process.poll() is None:
                with contextlib.suppress(OSError):
                    process.terminate()
                try:
                    await asyncio.to_thread(process.wait, 5)
                except subprocess.TimeoutExpired:
                    with contextlib.suppress(OSError):
                        process.kill()
                    with contextlib.suppress(Exception):
                        await asyncio.to_thread(process.wait, 5)
            for stream in (process.stdin, process.stdout, process.stderr):
                with contextlib.suppress(Exception):
                    stream and stream.close()
        for thread in self._threads:
            if thread.is_alive():
                thread.join(timeout=2)
        self._threads = []
        self._fail_pending("插件连接已关闭")
        if self._log_handle is not None:
            with contextlib.suppress(Exception):
                self._log_handle.close()
            self._log_handle = None

    @property
    def connected(self) -> bool:
        return self._process is not None and self._process.poll() is None


def _code_name(code: Any) -> str:
    try:
        return protocol.ERROR_NAMES.get(int(code), "PLUGIN_TOOL_FAILED")
    except (TypeError, ValueError):
        return "PLUGIN_TOOL_FAILED"


__all__ = ["StdioTransport"]
