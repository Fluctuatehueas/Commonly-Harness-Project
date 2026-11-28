"""Python → C# ToolService 客户端与动态 LangChain 工具包装。

- 工具清单以 C# `[Tool]` 注册表为唯一事实源，禁止在 Python 硬编码工具名。
- 参数 Schema 直接使用 ListTools 返回的 args_schema_json。
- 需要审批的工具：CallTool 返回 NEEDS_APPROVAL，本客户端按演示策略发起
  ResolveApproval；最终 OK / DENIED / ERROR 都作为文本回给模型。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid
from contextvars import ContextVar
from pathlib import Path
from typing import Any

_BASE = Path(__file__).resolve().parent
sys.path[:0] = [str(_BASE / "model"), str(_BASE / "grpc_service")]

import grpc  # noqa: E402
from langchain_core.tools import StructuredTool  # noqa: E402

import Agent_pb2  # noqa: E402
import Agent_pb2_grpc  # noqa: E402


logger = logging.getLogger(__name__)

CONTRACT_VERSION = "1.3.0"
LIST_TOOLS_TOOL_NAME = "list_csharp_tools"

CURRENT_THREAD_ID: ContextVar[str] = ContextVar("csharp_thread_id", default="")
CURRENT_RUN_ID: ContextVar[str] = ContextVar("csharp_run_id", default="")
CURRENT_MODE: ContextVar[int] = ContextVar(
    "csharp_chat_mode", default=Agent_pb2.CHAT_MODE_TASK
)


def set_tool_context(*, thread_id: str, run_id: str, mode: int) -> None:
    """在每轮 Agent 调用前设置工具上下文，供动态工具闭包读取。"""
    CURRENT_THREAD_ID.set(thread_id)
    CURRENT_RUN_ID.set(run_id)
    CURRENT_MODE.set(mode)


def _contract_compatible(remote_version: str) -> bool:
    """主版本一致即可联调；远端为空时放行，便于首次联调。"""
    if not remote_version:
        return True
    return remote_version.split(".")[0] == CONTRACT_VERSION.split(".")[0]


class CSharpToolService:
    """C# ToolService 的异步客户端。"""

    def __init__(self, address: str = "127.0.0.1:50052") -> None:
        self.address = address
        self._channel = grpc.aio.insecure_channel(address)
        self._stub = Agent_pb2_grpc.ToolServiceStub(self._channel)

    async def close(self) -> None:
        await self._channel.close()

    async def list_tools(self, thread_id: str = "") -> Agent_pb2.ListToolsResponse:
        request = Agent_pb2.ListToolsRequest(
            contract_version=CONTRACT_VERSION,
            thread_id=thread_id,
        )
        return await self._stub.ListTools(request, timeout=10)

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any] | str,
        *,
        thread_id: str,
        run_id: str,
        call_id: str,
        mode: int,
    ) -> Agent_pb2.ToolCallResult:
        arguments_json = (
            arguments
            if isinstance(arguments, str)
            else json.dumps(arguments or {}, ensure_ascii=False)
        )
        request = Agent_pb2.ToolCallRequest(
            tool_name=tool_name,
            arguments_json=arguments_json,
            thread_id=thread_id,
            call_id=call_id,
            run_id=run_id,
            mode=mode,
            trace_id=run_id,
            contract_version=CONTRACT_VERSION,
        )
        result = await self._stub.CallTool(request, timeout=360)

        if result.status == Agent_pb2.TOOL_STATUS_NEEDS_APPROVAL:
            # 决策交给 C# 宿主（WPF 聊天面板的"批准/拒绝"卡片）：
            # 这里传 UNSPECIFIED，C# 侧的 ToolApprovalBroker.DecisionProvider 会阻塞等待用户点击，
            # 超时（C# 4 分钟）按拒绝处理。若宿主不支持审批 UI，C# 默认放行。
            logger.info("工具 %s 需要审批：approval_id=%s，等待 C# 界面决策", tool_name, result.approval_id)
            result = await self.resolve_approval(
                result.approval_id,
                Agent_pb2.APPROVAL_DECISION_UNSPECIFIED,
                thread_id=thread_id,
                call_id=call_id,
            )

        return result

    async def resolve_approval(
        self,
        approval_id: str,
        decision: int,
        *,
        thread_id: str,
        call_id: str,
        edited_arguments_json: str = "",
    ) -> Agent_pb2.ToolCallResult:
        request = Agent_pb2.ResolveApprovalRequest(
            approval_id=approval_id,
            decision=decision,
            edited_arguments_json=edited_arguments_json,
            thread_id=thread_id,
            call_id=call_id,
            contract_version=CONTRACT_VERSION,
        )
        return await self._stub.ResolveApproval(request, timeout=360)


async def wait_until_ready(
    service: CSharpToolService,
    timeout: float = 60.0,
) -> Agent_pb2.ListToolsResponse:
    """等待 C# ToolService 就绪；启动顺序允许 Python 先起、C# 后起。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    last_error: Exception | None = None

    while loop.time() < deadline:
        try:
            response = await service.list_tools()
            if not _contract_compatible(response.contract_version):
                raise RuntimeError(
                    f"C# 契约版本 {response.contract_version} 与 Python {CONTRACT_VERSION} 不兼容"
                )
            logger.info(
                "C# ToolService 已就绪：%s，技能数=%s",
                service.address,
                len(response.skills),
            )
            return response
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            await asyncio.sleep(1.0)

    raise RuntimeError(f"C# ToolService 未就绪（{service.address}）：{last_error}")


def _format_tool_list(response: Agent_pb2.ListToolsResponse) -> str:
    """把 ListTools 结果格式化为模型可读的文本。"""
    lines = [f"C# 工具清单（contract_version={response.contract_version}）"]
    for skill in response.skills:
        lines.append(f"[{skill.name}] {skill.description}")
        for tool in skill.tools:
            sandbox = Agent_pb2.SandboxLevel.Name(tool.sandbox_level)
            lines.append(
                f"- {tool.name}：{tool.description}"
                f"（domain={tool.domain}，sandbox={sandbox}，write={tool.is_write}）"
                f" 参数={tool.args_schema_json}"
            )
    if len(lines) == 1:
        lines.append("（当前没有可用工具）")
    return "\n".join(lines)


def _format_tool_result(result: Agent_pb2.ToolCallResult) -> str:
    """把 ToolCallResult 转成模型可读文本；错误必须带 hint。"""
    status_name = Agent_pb2.ToolStatus.Name(result.status)

    if result.status == Agent_pb2.TOOL_STATUS_OK:
        return result.result_json or "{}"

    detail = result.detail
    message = detail.message or result.error or status_name
    hint = f"；建议：{detail.hint}" if detail.hint else ""

    if result.status == Agent_pb2.TOOL_STATUS_DENIED:
        return f"DENIED：{message}{hint}"

    return f"ERROR [{detail.code or status_name}]：{message}{hint}"


def _parse_args_schema(schema_json: str) -> dict[str, Any]:
    """解析 C# 返回的 JSON Schema；非法时退化为空对象。"""
    if not schema_json:
        return {"type": "object", "properties": {}, "required": []}
    try:
        schema = json.loads(schema_json)
        if isinstance(schema, dict) and schema.get("type") == "object":
            return schema
    except json.JSONDecodeError:
        logger.warning("工具参数 Schema 不是合法 JSON：%s", schema_json[:200])
    return {"type": "object", "properties": {}, "required": []}


async def build_langchain_tools(
    service: CSharpToolService,
) -> tuple[list[StructuredTool], set[str]]:
    """按 ListTools 结果动态生成 LangChain 工具。

    Returns:
        (tools, task_only_tool_names)
        - tools 同时包含 C# 业务工具与 list_csharp_tools 元工具。
        - task_only_tool_names 仅包含业务工具；元工具在 CHAT 模式也可见。
    """
    response = await service.list_tools()
    tools: list[StructuredTool] = []
    task_only: set[str] = set()

    for skill in response.skills:
        for descriptor in skill.tools:
            if descriptor.name == LIST_TOOLS_TOOL_NAME:
                logger.warning(
                    "C# 工具 %s 与 Python 元工具重名，已跳过 C# 侧定义。",
                    descriptor.name,
                )
                continue

            tool = _make_business_tool(service, descriptor)
            tools.append(tool)
            task_only.add(descriptor.name)

    tools.append(_make_list_tools_tool(service))
    logger.info(
        "已注入 C# 工具 %s 个，任务模式专用 %s 个，元工具 1 个",
        len(tools),
        len(task_only),
    )
    return tools, task_only


def _make_business_tool(
    service: CSharpToolService,
    descriptor: Agent_pb2.ToolDescriptor,
) -> StructuredTool:
    """为单个 C# 工具创建异步 LangChain StructuredTool。"""
    schema = _parse_args_schema(descriptor.args_schema_json)

    async def _invoke(**kwargs: Any) -> str:
        call_id = uuid.uuid4().hex
        try:
            result = await service.call_tool(
                descriptor.name,
                kwargs,
                thread_id=CURRENT_THREAD_ID.get(),
                run_id=CURRENT_RUN_ID.get(),
                call_id=call_id,
                mode=CURRENT_MODE.get(),
            )
            return _format_tool_result(result)
        except Exception as exc:  # noqa: BLE001
            logger.exception("调用 C# 工具 %s 失败", descriptor.name)
            return f"ERROR [GRPC_CALL_FAILED]：{exc}；建议：确认 C# ToolService 在 127.0.0.1:50052 运行。"

    metadata = {
        "domain": descriptor.domain,
        "skill": descriptor.skill,
        "display_name": descriptor.display_name,
        "is_write": bool(descriptor.is_write),
        "is_destructive": bool(descriptor.is_destructive),
        "sandbox_level": int(descriptor.sandbox_level),
    }
    return StructuredTool.from_function(
        coroutine=_invoke,
        name=descriptor.name,
        description=descriptor.description or descriptor.display_name or descriptor.name,
        args_schema=schema,
        infer_schema=False,
        metadata=metadata,
    )


def _make_list_tools_tool(service: CSharpToolService) -> StructuredTool:
    """元工具：让模型可以主动列出当前 C# 端提供的全部工具。"""

    async def _list_tools(**_: Any) -> str:
        try:
            response = await service.list_tools(thread_id=CURRENT_THREAD_ID.get())
            return _format_tool_list(response)
        except Exception as exc:  # noqa: BLE001
            logger.exception("获取 C# 工具清单失败")
            return f"ERROR [LIST_TOOLS_FAILED]：{exc}；建议：确认 C# ToolService 已启动。"

    return StructuredTool.from_function(
        coroutine=_list_tools,
        name=LIST_TOOLS_TOOL_NAME,
        description=(
            "列出当前 C# 端通过 gRPC 共享的全部工具，包括工具名、用途、参数 Schema 与沙箱等级。"
            "当用户询问你有哪些工具、能否完成某个操作，或你不确定可用工具时，优先调用本工具。"
        ),
        args_schema={"type": "object", "properties": {}, "required": []},
        infer_schema=False,
        metadata={"domain": "utility", "is_write": False},
    )
