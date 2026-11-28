"""Python ChatService gRPC 服务端。

- 复用现有 Harness Agent（DeepAgents / AgentLoader），不另起一套 Agent。
- C# 工具在启动时通过 ToolService.ListTools 发现，并注入 Agent 工具列表。
- CHAT 模式隐藏 C# 业务工具；TASK 模式暴露全部工具。
- ChatChunk 事件映射：文本 chunk、完整 message、工具调用/结果、轨迹、思考增量、done、error。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid
from contextlib import aclosing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_BASE = Path(__file__).resolve().parent
sys.path[:0] = [str(_BASE / "model"), str(_BASE / "grpc_service")]

import grpc  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from langgraph.types import Command  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402

import Agent_pb2  # noqa: E402
import Agent_pb2_grpc  # noqa: E402
import File_pb2_grpc  # noqa: E402
import Harness_pb2_grpc  # noqa: E402
import Session_pb2_grpc  # noqa: E402
import Trajectory_pb2_grpc  # noqa: E402

from agent.config import PLANNER_ENABLED, PLANNER_MAX_STEPS  # noqa: E402
from agent.hitl import RESUME_SCHEMA  # noqa: E402
from agent.schema import HarnessContext  # noqa: E402
from api.agent_loader import AgentLoader  # noqa: E402
from api.message_utils import (  # noqa: E402
    content_to_text,
    make_session_title,
    serialize_interrupt,
)
from protocals.scripts.file_service import FileServiceServicer  # noqa: E402
from protocals.scripts.harness_service import HarnessServiceServicer  # noqa: E402
from protocals.scripts.session_service import SessionServiceServicer  # noqa: E402
from protocals.scripts.tool_client import (  # noqa: E402
    CONTRACT_VERSION,
    CSharpToolService,
    build_langchain_tools,
    set_tool_context,
    wait_until_ready,
)
from protocals.scripts.trajectory_service import TrajectoryServiceServicer  # noqa: E402


logger = logging.getLogger(__name__)


def _contract_compatible(remote_version: str) -> bool:
    if not remote_version:
        return True
    return remote_version.split(".")[0] == CONTRACT_VERSION.split(".")[0]


def _error_chunk(
    code: str,
    message: str,
    hint: str = "",
    retryable: bool = False,
) -> Agent_pb2.ChatChunk:
    return Agent_pb2.ChatChunk(
        error=Agent_pb2.ErrorEvent(
            code=code,
            message=message,
            hint=hint,
            retryable=retryable,
        )
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _trajectory_chunk(
    *,
    run_id: str,
    thread_id: str,
    event_type: str,
    name: str,
    status: str,
    args_json: str = "",
    result_json: str = "",
    started_at: str = "",
    ended_at: str = "",
    parent_id: str = "",
    metadata_json: str = "",
) -> Agent_pb2.ChatChunk:
    """实时轨迹事件：让 C# 在流式过程中就能渲染轨迹分组（最终以 GetTrajectory 为准）。"""
    return Agent_pb2.ChatChunk(
        trajectory=Agent_pb2.TrajectoryEvent(
            event_id=uuid.uuid4().hex,
            run_id=run_id,
            thread_id=thread_id,
            parent_id=parent_id,
            type=event_type,
            name=name,
            status=status,
            args_json=args_json,
            result_json=result_json,
            started_at=started_at or _now_iso(),
            ended_at=ended_at,
            metadata_json=metadata_json,
        )
    )


def _plan_step_info(value: Any) -> Agent_pb2.PlanStepInfo:
    data = value if isinstance(value, dict) else {}
    return Agent_pb2.PlanStepInfo(
        step_id=str(data.get("step_id") or ""),
        domain=str(data.get("domain") or ""),
        goal=str(data.get("goal") or ""),
        depends_on=[str(item) for item in data.get("depends_on") or []],
        expected_tools=[str(item) for item in data.get("expected_tools") or []],
        acceptance=str(data.get("acceptance") or ""),
        is_visual_check=bool(data.get("is_visual_check", False)),
        status=str(data.get("status") or "pending"),
        error=str(data.get("error") or ""),
    )


def _plan_chunk(
    event: dict[str, Any],
    payload: dict[str, Any],
    *,
    run_id: str,
    thread_id: str,
) -> Agent_pb2.ChatChunk:
    """把 Planner 的 plan 快照翻译成强类型 PlanEvent。"""
    version = int(event.get("plan_version") or payload.get("version") or 1)
    plan_id = str(payload.get("plan_id") or f"{run_id}:v{version}")
    return Agent_pb2.ChatChunk(
        plan=Agent_pb2.PlanEvent(
            plan_id=plan_id,
            thread_id=thread_id,
            run_id=run_id,
            version=version,
            steps=[_plan_step_info(item) for item in payload.get("steps") or []],
            replan_reason=str(payload.get("replan_reason") or ""),
            status=str(payload.get("status") or "active"),
            created_at=str(event.get("started_at") or _now_iso()),
        )
    )


def _hitl_option(value: Any) -> Agent_pb2.HitlOption:
    data = value if isinstance(value, dict) else {}
    return Agent_pb2.HitlOption(
        value=str(data.get("value") or ""),
        label=str(data.get("label") or data.get("value") or ""),
        description=str(data.get("description") or ""),
        recommended=bool(data.get("recommended", False)),
    )


def _hitl_field(value: Any) -> Agent_pb2.HitlField:
    data = value if isinstance(value, dict) else {}
    default = data.get("default")
    default_json = "" if default is None else json.dumps(
        default, ensure_ascii=False, default=str
    )
    return Agent_pb2.HitlField(
        name=str(data.get("name") or ""),
        label=str(data.get("label") or data.get("name") or ""),
        type=str(data.get("type") or "text"),
        required=bool(data.get("required", True)),
        default_json=default_json,
        placeholder=str(data.get("placeholder") or ""),
        description=str(data.get("description") or ""),
        options=[_hitl_option(item) for item in data.get("options") or []],
    )


def _hitl_chunk(event: dict[str, Any], thread_id: str) -> Agent_pb2.ChatChunk:
    """把 serialize_interrupt 的结果翻译成结构化 HitlEvent。"""
    interrupt_type = str(event.get("interrupt_type") or "unknown")
    resume_schema = event.get("resume_schema") or RESUME_SCHEMA

    if interrupt_type == "hitl_request":
        data = event.get("hitl") or {}
        return Agent_pb2.ChatChunk(
            hitl=Agent_pb2.HitlEvent(
                hitl_id=str(
                    data.get("hitl_id") or event.get("interrupt_id") or ""
                ),
                thread_id=thread_id,
                kind=str(data.get("kind") or "information"),
                title=str(data.get("title") or "需要人工介入"),
                description=str(data.get("description") or ""),
                fields=[_hitl_field(item) for item in data.get("fields") or []],
                options=[_hitl_option(item) for item in data.get("options") or []],
                allow_multiple=bool(data.get("allow_multiple", False)),
                allow_free_text=bool(data.get("allow_free_text", True)),
                submit_label=str(data.get("submit_label") or "提交"),
                cancel_label=str(data.get("cancel_label") or "取消"),
                resume_schema_json=json.dumps(
                    resume_schema, ensure_ascii=False, default=str
                ),
                metadata_json=json.dumps(
                    data.get("metadata") or {}, ensure_ascii=False, default=str
                ),
            )
        )

    if interrupt_type == "hitl_approval":
        requests = event.get("action_requests") or []
        return Agent_pb2.ChatChunk(
            hitl=Agent_pb2.HitlEvent(
                hitl_id=str(event.get("interrupt_id") or uuid.uuid4().hex),
                thread_id=thread_id,
                kind="approval",
                title="需要审批",
                description=json.dumps(requests, ensure_ascii=False, default=str),
                options=[
                    _hitl_option({"value": "approve", "label": "批准", "recommended": True}),
                    _hitl_option({"value": "reject", "label": "拒绝"}),
                ],
                allow_multiple=False,
                allow_free_text=False,
                submit_label="提交",
                cancel_label="拒绝",
                resume_schema_json=json.dumps(
                    {
                        "type": "object",
                        "properties": {
                            "decisions": {
                                "type": "array",
                                "items": {"type": "string"},
                            }
                        },
                        "required": ["decisions"],
                    },
                    ensure_ascii=False,
                ),
                metadata_json=json.dumps(
                    {"source": "python", "action_requests": requests},
                    ensure_ascii=False,
                    default=str,
                ),
            )
        )

    # 兼容旧 information_request 与无法识别的中断。
    text = str(
        event.get("information_needed")
        or event.get("interrupt_value")
        or "需要人工确认"
    )
    return Agent_pb2.ChatChunk(
        hitl=Agent_pb2.HitlEvent(
            hitl_id=str(event.get("interrupt_id") or uuid.uuid4().hex),
            thread_id=thread_id,
            kind="information",
            title="需要补充信息",
            description=text,
            fields=[
                Agent_pb2.HitlField(
                    name="information",
                    label="补充内容",
                    type="textarea",
                    required=True,
                    description=text[:300],
                )
            ],
            allow_multiple=False,
            allow_free_text=False,
            submit_label="提交",
            cancel_label="取消",
            resume_schema_json=json.dumps(
                RESUME_SCHEMA, ensure_ascii=False, default=str
            ),
            metadata_json=json.dumps(
                {"source": "python", "interrupt_type": interrupt_type},
                ensure_ascii=False,
            ),
        )
    )


# ---------------------------------------------------------------------------
# 流事件解析：与 api/chat.py 的 SSE 映射保持一致
# ---------------------------------------------------------------------------


def _get_stream_message(chunk: Any) -> tuple[Any, dict[str, Any]] | None:
    if isinstance(chunk, dict):
        if chunk.get("type") != "messages":
            return None
        data = chunk.get("data")
    else:
        data = chunk

    if not isinstance(data, (list, tuple)) or len(data) != 2:
        return None
    token, metadata = data
    return token, metadata if isinstance(metadata, dict) else {}


def _get_tool_chunks(token: Any) -> list[dict[str, Any]]:
    tool_call_chunks = getattr(token, "tool_call_chunks", None) or []
    return [chunk for chunk in tool_call_chunks if isinstance(chunk, dict)]


def _is_tool_result(token: Any) -> bool:
    return getattr(token, "type", None) == "tool"


def _get_stream_interrupts(chunk: Any) -> list[Any]:
    if not isinstance(chunk, dict) or chunk.get("type") != "values":
        return []
    interrupts = chunk.get("interrupts")
    if interrupts is None and isinstance(chunk.get("data"), dict):
        interrupts = chunk["data"].get("interrupts")
    return list(interrupts or [])


def _get_stream_source(chunk: Any, metadata: dict[str, Any] | None = None) -> str:
    for key in ("lc_agent_name", "subagent_name", "agent_name"):
        value = (metadata or {}).get(key)
        if value:
            return str(value)
    namespace = chunk.get("ns", ()) if isinstance(chunk, dict) else ()
    return "subagent" if namespace else "main"


def _is_stream_visible(chunk: Any, metadata: dict[str, Any]) -> bool:
    """子图流白名单：主图与显式标记的子图可见，其余子 Agent 内部过程不上抛。"""
    if metadata.get("stream_visible") is True:
        return True
    if metadata.get("graph") == "planner":
        return True
    namespace = chunk.get("ns", ()) if isinstance(chunk, dict) else ()
    if any("planner" in str(part) for part in (namespace or ())):
        return True
    return _get_stream_source(chunk, metadata) == "main"


def _is_internal_middleware_stream(metadata: dict[str, Any]) -> bool:
    return metadata.get("langgraph_node") == "MemoryUpdateMiddleware.after_agent"


def _serialize_tool_args(args: Any) -> str:
    if args is None:
        return ""
    if isinstance(args, (dict, list, tuple)):
        return json.dumps(args, ensure_ascii=False)
    return str(args)


def _reasoning_delta(token: Any) -> str:
    """读取模型思考增量，兼容 additional_kwargs 与内容块两种形态。"""
    additional = getattr(token, "additional_kwargs", None) or {}
    if isinstance(additional, dict):
        for key in ("reasoning_content", "reasoning", "thinking"):
            value = additional.get(key)
            if value:
                return str(value)
    content = getattr(token, "content", None)
    if isinstance(content, list):
        return "".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") in {"reasoning", "thinking"}
        )
    return ""


def _get_custom_event(chunk: Any) -> dict[str, Any] | None:
    """取 Planner 节点通过 get_stream_writer() 发出的自定义事件。"""
    if isinstance(chunk, dict) and chunk.get("type") == "custom":
        data = chunk.get("data")
        return data if isinstance(data, dict) else None
    return None


def _custom_chunks(
    event: dict[str, Any],
    *,
    run_id: str,
    thread_id: str,
) -> list[Agent_pb2.ChatChunk]:
    """把自定义事件信封翻译成 ChatChunk：token/reasoning 走专用分支，其余走轨迹。"""
    kind = str(event.get("kind") or "")
    payload = event.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    if not kind:
        return []

    event_run_id = str(event.get("run_id") or run_id)
    step_id = str(event.get("step_id") or "")

    if kind == "token":
        text = str(payload.get("text") or payload.get("delta") or "")
        return [Agent_pb2.ChatChunk(chunk=text)] if text else []

    if kind == "reasoning":
        text = str(payload.get("text") or payload.get("delta") or "")
        if not text:
            return []
        return [
            Agent_pb2.ChatChunk(
                reasoning=Agent_pb2.ReasoningChunk(
                    thread_id=thread_id,
                    delta=text,
                    run_id=event_run_id,
                    step_id=step_id,
                )
            )
        ]

    if kind == "plan":
        return [
            _plan_chunk(
                event,
                payload,
                run_id=event_run_id,
                thread_id=thread_id,
            )
        ]

    if kind == "tool_call":
        tool_name = str(payload.get("tool_name") or "")
        if not tool_name:
            return []
        return [
            Agent_pb2.ChatChunk(
                tool_call=Agent_pb2.ToolCallEvent(
                    thread_id=thread_id,
                    tool_name=tool_name,
                    arguments_json=str(payload.get("arguments_json") or ""),
                    call_id=str(payload.get("call_id") or ""),
                    run_id=event_run_id,
                    is_partial=bool(payload.get("is_partial", True)),
                )
            )
        ]

    if kind == "tool_result":
        tool_name = str(payload.get("tool_name") or "")
        if not tool_name:
            return []
        failed = str(payload.get("status") or "").lower() in {"error", "denied", "failed"}
        return [
            Agent_pb2.ChatChunk(
                tool_result=Agent_pb2.ToolResultEvent(
                    thread_id=thread_id,
                    tool_name=tool_name,
                    result_json=str(payload.get("result_json") or ""),
                    call_id=str(payload.get("call_id") or ""),
                    status=(
                        Agent_pb2.TOOL_STATUS_ERROR
                        if failed
                        else Agent_pb2.TOOL_STATUS_OK
                    ),
                    error_json=(
                        str(payload.get("result_json") or "") if failed else ""
                    ),
                )
            )
        ]

    metadata = {
        "graph": str(event.get("graph") or "planner"),
        "plan_version": event.get("plan_version"),
        "step_id": step_id,
        "domain": event.get("domain"),
        "payload": payload,
    }
    return [
        _trajectory_chunk(
            run_id=event_run_id,
            thread_id=thread_id,
            event_type=kind,
            name=str(payload.get("name") or kind),
            status=str(payload.get("status") or "done"),
            args_json=json.dumps(payload, ensure_ascii=False, default=str),
            started_at=str(event.get("started_at") or ""),
            ended_at=str(event.get("ended_at") or ""),
            parent_id=str(event.get("parent_id") or ""),
            metadata_json=json.dumps(metadata, ensure_ascii=False, default=str),
        )
    ]


def _extract_final_answer(messages: list[Any]) -> str:
    """从最终状态里取"最后一条有文本的 AI 消息"。

    为什么要它：主 Agent 的最终回答有时不在 messages 流里（例如被委派到子 Agent 之后，
    最后一轮回答只在 values 的最终状态中），C# 会表现为"任务跑完但正式回答不出现"，
    必须切会话重新拉历史才看得到。这里用它做兜底。
    """
    for message in reversed(messages):
        if getattr(message, "type", None) not in ("ai", "assistant"):
            continue
        text = content_to_text(getattr(message, "content", ""))
        if text and text.strip():
            return text
    return ""


def _missing_answer_suffix(streamed: str, final_text: str) -> str:
    """计算"已经流出去的内容"与最终回答之间缺失的后缀，避免重复输出。"""
    if not final_text:
        return ""
    if final_text in streamed:
        return ""
    # 只差空白（流式分片与最终消息的空格/换行可能不同）也视为已输出
    if "".join(final_text.split()) in "".join(streamed.split()):
        return ""

    # 已流出的尾部可能是最终回答的前缀（部分 token 已到达），只补缺失部分
    limit = min(len(streamed), len(final_text), 2000)
    for size in range(limit, 0, -1):
        if streamed.endswith(final_text[:size]):
            return final_text[size:]
    return final_text


# ---------------------------------------------------------------------------
# 会话历史投影：Planner 使用派生 thread，最终仍把一轮问答写回主会话
# ---------------------------------------------------------------------------

_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


async def _write_history_projection(
    loader: AgentLoader,
    *,
    user_id: str,
    username: str,
    thread_id: str,
    human_text: str,
    ai_text: str,
    run_id: str,
) -> None:
    """把 TASK 模式的一轮问答写回主会话 checkpoint。

    Planner 的完整执行状态留在 ``{thread_id}#planner``；主会话只保留
    用户可见的问答对，历史接口与列表预览因此不需要跨图聚合。
    """
    try:
        async with loader.thread_operation(thread_id, defer=True) as acquired:
            if not acquired:
                logger.info("会话正在处理新请求，跳过 Planner 历史投影：%s", thread_id)
                return
            main_agent = await loader.get_agent_for_user(
                user_id=user_id,
                username=username,
                thread_id=thread_id,
            )
            config = loader.create_config(
                thread_id=thread_id,
                user_id=user_id,
                username=username,
            )
            state = await main_agent.aget_state(config)
            if getattr(state, "next", None) or getattr(state, "interrupts", None):
                logger.info("主会话处于暂停状态，跳过 Planner 历史投影：%s", thread_id)
                return
            await main_agent.aupdate_state(
                config,
                {
                    "messages": [
                        HumanMessage(
                            id=f"planner-human-{run_id}",
                            content=human_text,
                        ),
                        AIMessage(
                            id=f"planner-ai-{run_id}",
                            content=ai_text,
                            additional_kwargs={"source": "main"},
                        ),
                    ]
                },
            )
    except Exception:  # noqa: BLE001
        logger.warning("Planner 历史投影失败：thread=%s", thread_id, exc_info=True)


def _spawn_history_projection(
    loader: AgentLoader,
    *,
    user_id: str,
    username: str,
    thread_id: str,
    human_text: str,
    ai_text: str,
    run_id: str,
) -> None:
    """后台写历史投影，避免在 done 之后再阻塞用户输入。"""
    task = asyncio.create_task(
        _write_history_projection(
            loader,
            user_id=user_id,
            username=username,
            thread_id=thread_id,
            human_text=human_text,
            ai_text=ai_text,
            run_id=run_id,
        )
    )
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


# ---------------------------------------------------------------------------
# gRPC 服务
# ---------------------------------------------------------------------------


class ChatServiceServicer(Agent_pb2_grpc.ChatServiceServicer):
    """把现有 Agent 的流式事件翻译成 ChatChunk。"""

    def __init__(self, loader: AgentLoader) -> None:
        self._loader = loader

    async def Chat(
        self,
        request: Agent_pb2.ChatRequest,
        context: grpc.aio.ServicerContext,
    ):
        """普通聊天/任务入口。"""
        async for chunk in self._stream_chat(
            context=context,
            thread_id=request.thread_id or str(uuid.uuid4()),
            user_id=request.user_id or "grpc_demo",
            username=request.username or request.user_id or "grpc_demo",
            mode=request.mode,
            contract_version=request.contract_version,
            agent_input={
                "messages": [{"role": "user", "content": request.humanmsg}]
            },
            is_resume=False,
            save_title=request.humanmsg,
            user_request=request.humanmsg,
            client_context_json=request.client_context_json,
        ):
            yield chunk

    async def ResumeChat(
        self,
        request: Agent_pb2.ResumeChatRequest,
        context: grpc.aio.ServicerContext,
    ):
        """HITL 恢复入口：提交 Command(resume=...) 并复用同一流式翻译。"""
        thread_id = request.thread_id
        if not thread_id:
            yield _error_chunk(
                "INVALID_ARGUMENT",
                "thread_id 不能为空",
                "恢复会话必须指定 thread_id。",
            )
            yield Agent_pb2.ChatChunk(
                done=Agent_pb2.DoneEvent(
                    status=Agent_pb2.CHAT_STATUS_ERROR,
                    finish_reason="invalid_argument",
                )
            )
            return

        try:
            resume_payload = json.loads(request.resume_json or "{}")
        except json.JSONDecodeError as exc:
            yield _error_chunk(
                "INVALID_ARGUMENT",
                f"resume_json 不是合法 JSON：{exc}",
                '传入 Command(resume=...) 的 JSON 对象，例如 {"approved": true}。',
            )
            yield Agent_pb2.ChatChunk(
                done=Agent_pb2.DoneEvent(
                    thread_id=thread_id,
                    status=Agent_pb2.CHAT_STATUS_ERROR,
                    finish_reason="invalid_argument",
                )
            )
            return

        if not isinstance(resume_payload, dict):
            yield _error_chunk(
                "INVALID_ARGUMENT",
                "resume_json 必须是 JSON 对象",
                "传入审批决策或补充信息对象。",
            )
            yield Agent_pb2.ChatChunk(
                done=Agent_pb2.DoneEvent(
                    thread_id=thread_id,
                    status=Agent_pb2.CHAT_STATUS_ERROR,
                    finish_reason="invalid_argument",
                )
            )
            return

        async for chunk in self._stream_chat(
            context=context,
            thread_id=thread_id,
            user_id=request.user_id or "grpc_demo",
            username=request.username or request.user_id or "grpc_demo",
            mode=request.mode,
            contract_version=request.contract_version,
            agent_input=Command(resume=resume_payload),
            is_resume=True,
            save_title=None,
            user_request="",
            client_context_json="",
        ):
            yield chunk

    async def _stream_chat(
        self,
        *,
        context: grpc.aio.ServicerContext,
        thread_id: str,
        user_id: str,
        username: str,
        mode: int,
        contract_version: str,
        agent_input: Any,
        is_resume: bool,
        save_title: str | None,
        user_request: str = "",
        client_context_json: str = "",
    ):
        """Chat / ResumeChat 共用的流式事件翻译。"""
        run_id = uuid.uuid4().hex
        mode_name = "task" if mode == Agent_pb2.CHAT_MODE_TASK else "chat"
        planner_enabled = PLANNER_ENABLED and mode == Agent_pb2.CHAT_MODE_TASK
        graph_name = "planner" if planner_enabled else "main"

        if not _contract_compatible(contract_version):
            yield _error_chunk(
                "CONTRACT_VERSION_MISMATCH",
                f"契约版本不兼容：Python {CONTRACT_VERSION}，客户端 {contract_version}",
                "请使用同一份 Agent.proto 重新生成两端桩代码。",
            )
            yield Agent_pb2.ChatChunk(
                done=Agent_pb2.DoneEvent(
                    thread_id=thread_id,
                    status=Agent_pb2.CHAT_STATUS_ERROR,
                    finish_reason="contract_mismatch",
                )
            )
            return

        if not is_resume:
            pending_graph = self._loader.interrupted_graph(thread_id)
            if pending_graph:
                yield _error_chunk(
                    "HITL_PENDING",
                    f"当前会话还有待处理的人工介入（{pending_graph}）",
                    "请先提交 HITL 卡片；取消请通过 ResumeChat 发送 cancelled=true。",
                )
                yield Agent_pb2.ChatChunk(
                    done=Agent_pb2.DoneEvent(
                        thread_id=thread_id,
                        status=Agent_pb2.CHAT_STATUS_INTERRUPTED,
                        finish_reason="hitl_pending",
                    )
                )
                return

        set_tool_context(thread_id=thread_id, run_id=run_id, mode=mode)

        run_started_at = _now_iso()
        yield _trajectory_chunk(
            run_id=run_id,
            thread_id=thread_id,
            event_type="run",
            name=graph_name,
            status="started",
            started_at=run_started_at,
            metadata_json=json.dumps(
                {"mode": mode_name, "source": "python", "graph": graph_name},
                ensure_ascii=False,
            ),
        )

        answer_parts: list[str] = []
        interrupted = False
        tool_call_ids: dict[str, str] = {}
        tool_started_at: dict[str, str] = {}
        tool_started_keys: set[str] = set()
        last_state_values: dict[str, Any] | None = None
        stream = None

        def _tool_event_id(tool_chunk: dict[str, Any], message_id: str) -> str:
            # call_id 一旦确定就不能再变：增量片段与后续 ToolResult 必须用同一个键，
            # 否则 C# 端按 call_id 累积参数会断成两个卡片。
            chunk_index = tool_chunk.get("index", 0)
            chunk_key = f"{message_id}:{chunk_index}"
            source_id = tool_chunk.get("id")
            if chunk_key not in tool_call_ids:
                tool_call_ids[chunk_key] = str(source_id) if source_id else chunk_key
            return tool_call_ids[chunk_key]

        try:
            async with self._loader.thread_operation(thread_id):
                if is_resume:
                    if await self._loader.get_session(user_id, thread_id) is None:
                        yield _error_chunk(
                            "NOT_FOUND",
                            "会话不存在或无权恢复",
                            "请先确认 thread_id，或通过 Chat 创建会话。",
                        )
                        yield Agent_pb2.ChatChunk(
                            done=Agent_pb2.DoneEvent(
                                thread_id=thread_id,
                                status=Agent_pb2.CHAT_STATUS_ERROR,
                                finish_reason="not_found",
                            )
                        )
                        return
                elif save_title is not None:
                    await self._loader.save_session(
                        user_id,
                        thread_id,
                        make_session_title(save_title),
                    )

                graph_thread_id = f"{thread_id}#planner" if planner_enabled else thread_id
                if planner_enabled:
                    agent = await self._loader.get_planner()
                else:
                    agent = await self._loader.get_agent_for_user(
                        user_id=user_id,
                        username=username,
                        thread_id=thread_id,
                    )
                config = self._loader.create_config(
                    thread_id=graph_thread_id,
                    user_id=user_id,
                    username=username,
                )
                if planner_enabled:
                    config.setdefault("configurable", {}).update(
                        {
                            "base_thread_id": thread_id,
                            "run_id": run_id,
                        }
                    )
                    config["metadata"] = {"graph": "planner", "stream_visible": True}
                    # Planner 每步至少经过 select/ground/execute 三个节点；
                    # 默认 recursion_limit=25 会在 8 步计划中途被耗尽。
                    config["recursion_limit"] = max(25, PLANNER_MAX_STEPS * 5 + 10)
                    if not is_resume:
                        try:
                            client_context = (
                                json.loads(client_context_json)
                                if client_context_json
                                else {}
                            )
                        except json.JSONDecodeError:
                            client_context = {"raw": client_context_json}
                        if not isinstance(client_context, dict):
                            client_context = {"value": client_context}
                        agent_input = {
                            "user_request": user_request,
                            "client_context": client_context,
                        }
                harness_context = HarnessContext(
                    user_id=user_id,
                    username=username,
                    thread_id=thread_id,
                    mode=mode_name,
                )

                stream = agent.astream(
                    agent_input,
                    config=config,
                    context=harness_context,
                    stream_mode=["messages", "values", "custom"],
                    subgraphs=True,
                    version="v2",
                )

                async with aclosing(stream):
                    async for chunk in stream:
                        if context.cancelled():
                            logger.info("客户端取消 Chat 流：%s", thread_id)
                            return

                        if isinstance(chunk, dict) and chunk.get("type") == "values":
                            data = chunk.get("data")
                            if isinstance(data, dict):
                                last_state_values = data

                        custom_event = _get_custom_event(chunk)
                        if custom_event is not None:
                            for custom_chunk in _custom_chunks(
                                custom_event,
                                run_id=run_id,
                                thread_id=thread_id,
                            ):
                                yield custom_chunk
                            continue

                        if _get_stream_interrupts(chunk):
                            interrupted = True
                            self._loader.mark_interrupted(thread_id, graph_name)
                            # 立即停止消费流：避免中断后仍有并行分支继续执行。
                            break

                        stream_message = _get_stream_message(chunk)
                        if stream_message is None:
                            continue

                        token, metadata = stream_message
                        if _is_internal_middleware_stream(metadata):
                            continue
                        if not _is_stream_visible(chunk, metadata):
                            continue

                        message_id = str(getattr(token, "id", None) or uuid.uuid4())

                        reasoning = _reasoning_delta(token)
                        if reasoning:
                            yield Agent_pb2.ChatChunk(
                                reasoning=Agent_pb2.ReasoningChunk(
                                    thread_id=thread_id,
                                    delta=reasoning,
                                    run_id=run_id,
                                )
                            )

                        tool_chunks = _get_tool_chunks(token)

                        for tool_chunk in tool_chunks:
                            chunk_index = tool_chunk.get("index", 0)
                            chunk_key = f"{message_id}:{chunk_index}"
                            tool_call_id = _tool_event_id(tool_chunk, message_id)
                            tool_name = tool_chunk.get("name")
                            args = tool_chunk.get("args")
                            if not tool_name:
                                continue

                            # 同一个工具调用的增量片段只记一条 started 轨迹，
                             # 否则轨迹页会出现 N 条重复的“工具开始”。
                            if chunk_key not in tool_started_keys:
                                tool_started_keys.add(chunk_key)
                                tool_started_at.setdefault(tool_call_id, _now_iso())
                                yield _trajectory_chunk(
                                    run_id=run_id,
                                    thread_id=thread_id,
                                    event_type="tool",
                                    name=str(tool_name),
                                    status="started",
                                    args_json=_serialize_tool_args(args),
                                    started_at=tool_started_at[tool_call_id],
                                )

                            yield Agent_pb2.ChatChunk(
                                tool_call=Agent_pb2.ToolCallEvent(
                                    thread_id=thread_id,
                                    tool_name=str(tool_name),
                                    arguments_json=_serialize_tool_args(args),
                                    call_id=tool_call_id,
                                    run_id=run_id,
                                    is_partial=True,
                                )
                            )

                        if _is_tool_result(token):
                            tool_call_id = str(getattr(token, "tool_call_id", ""))
                            tool_name = str(getattr(token, "name", "tool"))
                            result_text = content_to_text(getattr(token, "content", ""))
                            # LangChain 的工具错误有时以普通 ToolMessage 返回，
                            # 这里同时检查 status 与文本前缀，避免把错误标成 OK。
                            tool_failed = (
                                getattr(token, "status", None) == "error"
                                or result_text.startswith(("ERROR", "DENIED"))
                            )
                            status = (
                                Agent_pb2.TOOL_STATUS_ERROR
                                if tool_failed
                                else Agent_pb2.TOOL_STATUS_OK
                            )
                            yield Agent_pb2.ChatChunk(
                                tool_result=Agent_pb2.ToolResultEvent(
                                    thread_id=thread_id,
                                    tool_name=tool_name,
                                    result_json=result_text,
                                    call_id=tool_call_id,
                                    status=status,
                                    error_json=(
                                        result_text
                                        if status == Agent_pb2.TOOL_STATUS_ERROR
                                        else ""
                                    ),
                                )
                            )
                            yield _trajectory_chunk(
                                run_id=run_id,
                                thread_id=thread_id,
                                event_type="tool",
                                name=tool_name,
                                status="ok" if status == Agent_pb2.TOOL_STATUS_OK else "error",
                                result_json=result_text,
                                started_at=tool_started_at.get(tool_call_id, _now_iso()),
                                ended_at=_now_iso(),
                            )
                            continue

                        content = content_to_text(getattr(token, "content", ""))
                        if content and not tool_chunks:
                            answer_parts.append(content)
                            yield Agent_pb2.ChatChunk(chunk=content)

        except HTTPException as exc:
            logger.warning("会话占用冲突：%s", exc.detail)
            yield _error_chunk(
                "SESSION_BUSY",
                str(exc.detail),
                "同一 thread_id 正在处理另一轮请求，请稍后重试。",
            )
            yield _trajectory_chunk(
                run_id=run_id,
                thread_id=thread_id,
                event_type="run",
                name=graph_name,
                status="error",
                started_at=run_started_at,
                ended_at=_now_iso(),
            )
            yield Agent_pb2.ChatChunk(
                done=Agent_pb2.DoneEvent(
                    thread_id=thread_id,
                    status=Agent_pb2.CHAT_STATUS_ERROR,
                    finish_reason="busy",
                )
            )
            return

        except asyncio.CancelledError:
            logger.info("Chat 流被取消：%s", thread_id)
            raise

        except Exception as exc:  # noqa: BLE001
            logger.exception("Agent 流式调用异常")
            message = (
                str(exc)
                if isinstance(exc, RuntimeError) and str(exc)
                else "Agent 调用失败"
            )
            text = str(exc).lower()
            if any(
                marker in text
                for marker in ("429", "rate limit", "quota", "too many requests")
            ):
                yield _error_chunk(
                    "MODEL_RATE_LIMITED",
                    "模型服务返回 429 或配额不足",
                    "已停止本轮调用；请稍后重试或降低请求频率（HARNESS_MODEL_REQUESTS_PER_SECOND）。",
                )
            else:
                yield _error_chunk(
                    "AGENT_ERROR",
                    message,
                    "检查模型配置、PostgreSQL 连接与 Agent 日志。",
                )
            yield _trajectory_chunk(
                run_id=run_id,
                thread_id=thread_id,
                event_type="run",
                name=graph_name,
                status="error",
                started_at=run_started_at,
                ended_at=_now_iso(),
            )
            yield Agent_pb2.ChatChunk(
                done=Agent_pb2.DoneEvent(
                    thread_id=thread_id,
                    status=Agent_pb2.CHAT_STATUS_ERROR,
                    finish_reason="error",
                )
            )
            return

        if interrupted:
            self._loader.mark_interrupted(thread_id, graph_name)
            serialized: dict[str, Any] | None = None
            try:
                snapshot = await agent.aget_state(config)
                interrupts = getattr(snapshot, "interrupts", None) or ()
                if interrupts:
                    # 必须用根图 checkpoint 的 canonical interrupt，子图 ID 不可恢复。
                    serialized = serialize_interrupt(interrupts[0], thread_id)
            except Exception:  # noqa: BLE001
                logger.warning("读取中断详情失败，降级为通用中断", exc_info=True)

            if serialized:
                yield _hitl_chunk(serialized, thread_id)

            metadata = {
                "resume_via": "ResumeChat",
                "graph": graph_name,
                "interrupt_type": (serialized or {}).get("interrupt_type", "unknown"),
                "hitl_id": ((serialized or {}).get("hitl") or {}).get("hitl_id", ""),
            }
            yield _trajectory_chunk(
                run_id=run_id,
                thread_id=thread_id,
                event_type="hitl",
                name="resume_required",
                status="requested",
                started_at=run_started_at,
                ended_at=_now_iso(),
                metadata_json=json.dumps(metadata, ensure_ascii=False),
            )
            yield _trajectory_chunk(
                run_id=run_id,
                thread_id=thread_id,
                event_type="run",
                name=graph_name,
                status="interrupted",
                started_at=run_started_at,
                ended_at=_now_iso(),
            )
            done_message = (
                str((serialized or {}).get("hitl", {}).get("title") or "")
                or "需要人工介入；请通过 ChatService.ResumeChat 提交决策。"
            )
            yield Agent_pb2.ChatChunk(
                done=Agent_pb2.DoneEvent(
                    msg=done_message,
                    thread_id=thread_id,
                    status=Agent_pb2.CHAT_STATUS_INTERRUPTED,
                    finish_reason="hitl",
                )
            )
            return

        # 兜底：把"只存在于最终状态、没有逐 token 流出"的正式回答补发出去。
        # 优先使用流里的 values 快照，避免每轮结束后再读一次完整 checkpoint。
        final_text = ""
        if planner_enabled and isinstance(last_state_values, dict):
            final_text = str(last_state_values.get("final_response") or "")
        elif isinstance(last_state_values, dict):
            state_messages = last_state_values.get("messages")
            if isinstance(state_messages, list):
                final_text = _extract_final_answer(state_messages)
        if not final_text and not planner_enabled:
            get_state = getattr(agent, "aget_state", None)
            if callable(get_state):
                try:
                    state = await get_state(config)
                    state_values = getattr(state, "values", None)
                    if isinstance(state_values, dict) and isinstance(
                        state_values.get("messages"), list
                    ):
                        final_text = _extract_final_answer(state_values["messages"])
                except Exception:  # noqa: BLE001
                    logger.warning("读取最终状态失败，跳过正式回答兜底", exc_info=True)

        streamed_text = "".join(answer_parts)
        remainder = _missing_answer_suffix(streamed_text, final_text)
        if remainder.strip():
            answer_parts.append(remainder)
            yield Agent_pb2.ChatChunk(chunk=remainder)

        answer_text = "".join(answer_parts)
        finish_reason = "stop"
        self._loader.clear_interrupted(thread_id)
        if planner_enabled and isinstance(last_state_values, dict):
            finish_reason = str(last_state_values.get("finish_reason") or "stop")
        project_human = user_request
        if planner_enabled and isinstance(last_state_values, dict):
            project_human = project_human or str(
                last_state_values.get("user_request") or ""
            )
        if planner_enabled and project_human and answer_text:
            # 历史投影不阻塞 done：主会话写入失败只告警。
            _spawn_history_projection(
                self._loader,
                user_id=user_id,
                username=username,
                thread_id=thread_id,
                human_text=project_human,
                ai_text=answer_text,
                run_id=run_id,
            )

        usage_data = await self._loader.get_thread_usage(user_id, thread_id)
        rounds = usage_data.get("rounds", [])
        session_usage = dict(usage_data.get("session") or {})
        payload = dict(rounds[-1]) if rounds else {}
        payload["context_window"] = int(session_usage.get("context_window") or 0)
        payload["context_used"] = int(
            session_usage.get("context_used") or payload.get("input") or 0
        )
        payload["session"] = session_usage
        usage_json = json.dumps(payload, ensure_ascii=False)
        if finish_reason == "model_rate_limited":
            yield _error_chunk(
                "MODEL_RATE_LIMITED",
                "模型服务返回 429 或配额不足",
                "已停止后续模型调用；请稍后重试或降低请求频率（HARNESS_MODEL_REQUESTS_PER_SECOND）。",
            )
        yield _trajectory_chunk(
            run_id=run_id,
            thread_id=thread_id,
            event_type="run",
            name=graph_name,
            status="done",
            started_at=run_started_at,
            ended_at=_now_iso(),
            result_json=usage_json,
        )
        yield Agent_pb2.ChatChunk(
            done=Agent_pb2.DoneEvent(
                msg="".join(answer_parts),
                thread_id=thread_id,
                status=(
                    Agent_pb2.CHAT_STATUS_ERROR
                    if finish_reason == "model_rate_limited"
                    else Agent_pb2.CHAT_STATUS_OK
                ),
                finish_reason=finish_reason,
                usage_json=usage_json,
            )
        )


class GrpcChatRuntime:
    """已启动的 gRPC ChatService 运行时。调用方退出时必须 await stop()。"""

    def __init__(
        self,
        *,
        server: grpc.aio.Server,
        tool_service: CSharpToolService,
        loader: AgentLoader,
        host: str,
        port: int,
    ) -> None:
        self.server = server
        self.tool_service = tool_service
        self.loader = loader
        self.host = host
        self.port = port
        self._stopped = False

    async def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        await self.server.stop(grace=1.0)
        await self.tool_service.close()
        await self.loader.shutdown()
        logger.info("gRPC ChatService 已停止")


async def start_grpc_chat_service(
    *,
    host: str = "127.0.0.1",
    port: int = 50051,
    tool_address: str = "127.0.0.1:50052",
    wait_timeout: float = 60.0,
) -> GrpcChatRuntime:
    """启动 gRPC ChatService：发现 C# 工具 → 构建 Agent → 绑定端口。

    本函数只负责启动并返回运行时；进程退出由调用方 await runtime.stop()。
    """
    tool_service = CSharpToolService(tool_address)
    loader: AgentLoader | None = None

    try:
        logger.info(
            "等待 C# ToolService：%s（最长 %.0f 秒）",
            tool_address,
            wait_timeout,
        )
        ready = await wait_until_ready(tool_service, timeout=wait_timeout)
        logger.info("C# ToolService 就绪，技能数=%s", len(ready.skills))
        extra_tools, task_only = await build_langchain_tools(tool_service)
        loader = AgentLoader(
            extra_tools=extra_tools,
            task_only_tool_names=task_only,
            enable_sandbox=False,
        )
        await loader.initialize()
    except BaseException:
        if loader is not None:
            try:
                await loader.shutdown()
            except Exception:
                logger.exception("清理 gRPC AgentLoader 失败")
        await tool_service.close()
        raise

    server = grpc.aio.server()
    Agent_pb2_grpc.add_ChatServiceServicer_to_server(
        ChatServiceServicer(loader),
        server,
    )
    Session_pb2_grpc.add_SessionServiceServicer_to_server(
        SessionServiceServicer(loader),
        server,
    )
    Trajectory_pb2_grpc.add_TrajectoryServiceServicer_to_server(
        TrajectoryServiceServicer(loader),
        server,
    )
    Harness_pb2_grpc.add_HarnessServiceServicer_to_server(
        HarnessServiceServicer(loader),
        server,
    )
    File_pb2_grpc.add_FileServiceServicer_to_server(
        FileServiceServicer(loader),
        server,
    )
    bound_port = server.add_insecure_port(f"{host}:{port}")
    if bound_port == 0:
        await tool_service.close()
        await loader.shutdown()
        raise RuntimeError(f"无法绑定 {host}:{port}（端口可能已被占用）")

    await server.start()
    logger.info("gRPC ChatService 已启动：%s:%s（h2c）", host, bound_port)
    return GrpcChatRuntime(
        server=server,
        tool_service=tool_service,
        loader=loader,
        host=host,
        port=bound_port,
    )


async def serve() -> None:
    """独立运行入口：直接启动 gRPC ChatService，不启动 FastAPI。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    runtime = await start_grpc_chat_service()
    try:
        await runtime.server.wait_for_termination()
    finally:
        await runtime.stop()


if __name__ == "__main__":
    asyncio.run(serve())
