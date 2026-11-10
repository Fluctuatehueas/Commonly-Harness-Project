"""Harness Agent 的最小对话 HTTP 接口。

对外协议：
- ``POST /chat``：非流式对话
- ``POST /chat/stream``：SSE 流式对话
- ``POST /chat/{thread_id}/resume``：恢复被 HITL 中断的会话

本模块不依赖任何业务模型；业务能力通过工具的注册与子 Agent 配置注入。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langgraph.types import Command

from agent.config import FEEDBACK_ENABLED, MAIN_AGENT_MODEL_RUN_LIMIT
from agent.project_context import (
    reset_project_context,
    reset_project_thread,
    set_project_context,
    set_project_thread,
)
from agent.schema import (
    ChatRequest,
    ChatResponse,
    HarnessContext,
    ResumeChatRequest,
    RetryChatRequest,
    WithdrawChatRequest,
    WithdrawChatResponse,
)
from agent.feedback_store import save_retry_event
from agent.middlewares.self_judge import HIGH_RISK_TOOLS
from agent.trajectory_store import (
    delete_trajectory,
    delete_trajectory_events_since,
)
from agent.tools.async_sandbox_tools import START_ASYNC_TASK_TOOL_NAME
from api.agent_loader import agent_loader
from api.agents import router as agents_router
from api.async_tasks import extract_async_task_id
from api.async_tasks import router as async_tasks_router
from api.context import build_user_content
from api.context import router as context_router
from api.identity import UserIdentityMiddleware, effective_user_id
from api.evolution import router as evolution_router
from api.feedback import router as feedback_router
from api.history import router as history_router
from api.history import sessions_router as history_sessions_router
from api.settings import router as settings_router
from api.trajectory import router as trajectory_router
from api.skills import router as skills_router
from api.mcp import router as mcp_router
from api.projects import router as projects_router
from api.rag import router as rag_router
from api.session_files import router as session_files_router
from api.task_flow import router as task_flow_router
from task_flow.agent_bridge import execute_task_flow, route_task_flow
from task_flow.models import Execution
from task_flow.router import RouteDecision
from api.message_utils import (
    content_to_text,
    make_session_title,
    serialize_interrupt as _serialize_interrupt,
)
from utils.integrity import integrity_hint, is_low_integrity, process_integrity


logger = logging.getLogger(__name__)

router = APIRouter()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """在 FastAPI 生命周期中初始化并清理 Agent 加载器。"""
    # 持久化连接属于应用级资源，不能在每个请求中重复创建。
    await agent_loader.initialize()
    try:
        yield
    finally:
        await agent_loader.shutdown()


app = FastAPI(title="Harness Agent API", lifespan=lifespan)

# 浏览器前端与 API 不同源时，按需开启 CORS。
# 显式配置优先；留空时回退到本地 Vite 开发来源。
# 注意：os.getenv 对"已设置为空字符串"的变量不会走默认值，必须显式判空，
# 否则 .env 里的 HARNESS_CORS_ORIGINS= 会把 CORS 整体禁用。
_cors_raw = (os.getenv("HARNESS_CORS_ORIGINS") or "").strip()
if not _cors_raw:
    _cors_raw = "http://localhost:5173,http://127.0.0.1:5173"
_cors_origins = [origin.strip() for origin in _cors_raw.split(",") if origin.strip()]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# 用户身份解析必须在 CORS 之后注册，保证所有接口都能读到当前用户。
app.add_middleware(UserIdentityMiddleware)


@app.get("/", include_in_schema=False)
async def index() -> dict[str, str]:
    """返回服务标识，真实接口文档见 ``/docs``。"""
    return {"name": "Harness Agent API", "docs": "/docs"}


@app.get("/health")
async def health() -> dict[str, Any]:
    """轻量存活探针；附带进程完整性级别，便于前端定位写入失败。"""
    return {
        "status": "ok",
        "integrity": process_integrity(),
        "low_integrity": is_low_integrity(),
        "integrity_hint": integrity_hint(),
    }


def _extract_answer(result: Any) -> str:
    """从 Agent 返回结果中提取最后一条助手消息的文本。"""
    # DeepAgents 版本和调用方式可能返回字符串、状态字典或消息对象。
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        messages = result.get("messages")
        if messages:
            last_message = messages[-1]
            content = getattr(last_message, "content", None)
            if content is None and isinstance(last_message, dict):
                content = last_message.get("content")
            return content_to_text(content)
        return content_to_text(result.get("answer", result.get("output", "")))
    return content_to_text(getattr(result, "content", result))


async def _bind_async_tasks_from_result(
    result: Any,
    *,
    user_id: str,
    username: str,
    thread_id: str,
) -> None:
    """为非流式 Agent 结果登记异步任务归属。

    流式响应会在工具消息到达时绑定任务；``ainvoke`` 会一次性返回相同的最终
    消息列表，因此必须在客户端开始轮询任务状态前完成同样的登记。
    """
    messages = result.get("messages", []) if isinstance(result, dict) else []
    for message in messages:
        tool_name = getattr(message, "name", None)
        content = getattr(message, "content", None)
        status = getattr(message, "status", None)
        if isinstance(message, dict):
            tool_name = message.get("name", tool_name)
            content = message.get("content", content)
            status = message.get("status", status)
        if tool_name != START_ASYNC_TASK_TOOL_NAME or status == "error":
            continue
        task_id = extract_async_task_id(content)
        if task_id:
            await agent_loader.bind_async_task(
                task_id,
                user_id=user_id,
                username=username,
                thread_id=thread_id,
            )


def _create_sse_message(data: dict[str, Any]) -> str:
    """将事件数据编码为浏览器可解析的 SSE 消息。"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _get_stream_message(chunk: Any) -> tuple[Any, dict[str, Any]] | None:
    """从 LangGraph 消息流事件中提取 token 和元数据。"""
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


def _get_custom_event(chunk: Any) -> dict[str, Any] | None:
    """提取中间件通过 StreamWriter 发出的自定义事件。

    - 主图的压缩事件：只接受 ns 为空，避免子 Agent 压缩干扰聊天区状态；
    - 子 Agent 进度事件：允许来自子图，按 run_id 区分并发实例。
    """
    if not isinstance(chunk, dict) or chunk.get("type") != "custom":
        return None
    data = chunk.get("data")
    if not isinstance(data, dict):
        return None
    event_type = str(data.get("type") or "")
    if event_type == "subagent_progress":
        return {
            "type": event_type,
            **{key: value for key, value in data.items() if key != "type"},
        }
    if chunk.get("ns"):
        return None
    if not event_type.startswith("compaction_"):
        return None
    return {"type": event_type, **{key: value for key, value in data.items() if key != "type"}}


def _get_tool_chunks(token: Any) -> list[dict[str, Any]]:
    """读取模型增量消息中的工具调用分片。"""
    tool_call_chunks = getattr(token, "tool_call_chunks", None) or []
    return [chunk for chunk in tool_call_chunks if isinstance(chunk, dict)]


# 这些工具会启动子 Agent；其执行期间的事件应挂到本次委派节点下。
DELEGATION_TOOL_NAMES = {"task", START_ASYNC_TASK_TOOL_NAME}


def _get_reasoning_text(token: Any) -> str:
    """读取模型增量消息中的推理内容。

    DeepSeek / OpenAI 兼容端点通常把思考内容放在 ``additional_kwargs`` 的
    ``reasoning_content``；部分实现会以结构化 reasoning 内容块返回。
    """
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


def _is_tool_result(token: Any) -> bool:
    """判断当前流式 token 是否为工具返回结果。"""
    return getattr(token, "type", None) == "tool"


def _get_stream_interrupts(chunk: Any) -> list[Any]:
    """保留流式中断对象及其 ID，使 SSE 和历史使用同一恢复协议。"""
    if not isinstance(chunk, dict) or chunk.get("type") != "values":
        return []
    interrupts = chunk.get("interrupts")
    if interrupts is None and isinstance(chunk.get("data"), dict):
        interrupts = chunk["data"].get("interrupts")
    return list(interrupts or [])


def _get_stream_source(chunk: Any, metadata: dict[str, Any] | None = None) -> str:
    """返回主 Agent 或子 Agent 的稳定名称。

    DeepAgents 为声明式子 Agent 注入 ``lc_agent_name`` 元数据；
    namespace 只作为旧版本或异常事件的来源兜底，不能用于猜测具体子 Agent 名称。
    """
    for key in ("lc_agent_name", "subagent_name", "agent_name"):
        value = (metadata or {}).get(key)
        if value:
            return str(value)
    namespace = chunk.get("ns", ()) if isinstance(chunk, dict) else ()
    return "subagent" if namespace else "main"


def _is_subagent_stream(source: str) -> bool:
    """判断消息流是否来自不应直接展示的子 Agent 内部执行。"""
    return source != "main"


def _is_internal_middleware_stream(metadata: dict[str, Any]) -> bool:
    """判断消息是否来自不应向用户展示的内部模型调用。

    - 记忆更新中间件有自己的图节点名，按节点名识别；
    - 摘要/压缩等内部调用由 LangChain 写入 ``lc_source`` 元数据；
    - 其他内部调用统一带 ``nostream`` 标签（由 StreamMessagesHandler 直接丢弃）。
    """
    if metadata.get("langgraph_node") == "MemoryUpdateMiddleware.after_agent":
        return True
    if metadata.get("lc_source") in {"summarization", "tool_summarization"}:
        return True
    tags = metadata.get("tags")
    return isinstance(tags, list) and "nostream" in tags


def _get_subagent_name(value: Any) -> str | None:
    """从 task 工具参数中读取声明式子 Agent 原名。"""
    if isinstance(value, dict):
        name = value.get("subagent_type")
        return str(name) if name else None
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        return _get_subagent_name(parsed)
    match = re.search(r"['\"]subagent_type['\"]\s*:\s*['\"]([^'\"\\]+)", value)
    return match.group(1) if match else None


def _serialize_tool_args(args: Any) -> str:
    """将工具参数编码为前端可稳定解析的文本。"""
    if isinstance(args, (dict, list, tuple)):
        return json.dumps(args, ensure_ascii=False)
    return str(args)


async def _run_chat(request: ChatRequest) -> ChatResponse:
    """在会话 guard 中校验归属并执行非流式对话。"""
    request.user_id = effective_user_id(request.user_id)
    thread_id = request.thread_id or str(uuid.uuid4())
    async with agent_loader.thread_operation(thread_id):
        if request.thread_id:
            await agent_loader.require_session(request.user_id, thread_id)
        await agent_loader.save_session(
            request.user_id,
            thread_id,
            make_session_title(request.message),
            agent_id=request.agent_id,
            bind_agent=not request.thread_id,
        )
        return await _run_chat_unlocked(request, thread_id)


async def _run_chat_unlocked(request: ChatRequest, thread_id: str) -> ChatResponse:
    """执行一轮非流式对话，并返回标准化响应。"""
    username = request.username or request.user_id
    # 本轮绑定的项目决定文件落点；Agent 图本身按用户缓存，不随项目重建。
    project = await agent_loader.resolve_project(request.user_id, thread_id)
    project_token = set_project_context(project)
    thread_token = set_project_thread(thread_id)
    try:
        return await _run_chat_turn(request, thread_id, username)
    finally:
        reset_project_thread(thread_token)
        reset_project_context(project_token)


async def _resolve_agent_id(
    user_id: str, thread_id: str, explicit: str | None = None
) -> str:
    """本轮使用的 Agent：请求显式选择 > 会话绑定 > 默认。"""
    selected = str(explicit or "").strip()
    if selected:
        return selected
    session = await agent_loader.get_session(user_id, thread_id)
    return str((session or {}).get("agent_id") or "default")


async def _run_chat_turn(
    request: ChatRequest, thread_id: str, username: str,
) -> ChatResponse:
    """持有会话工作目录上下文执行一轮对话。"""
    agent_id = await _resolve_agent_id(
        request.user_id, thread_id, getattr(request, "agent_id", None)
    )
    agent = await agent_loader.get_agent_for_user(
        user_id=request.user_id,
        username=username,
        thread_id=thread_id,
        agent_id=agent_id,
    )
    # thread_id 用于 checkpoint 恢复会话，user_id 和 username 用于 Agent 身份隔离。
    config = agent_loader.create_config(
        thread_id=thread_id,
        user_id=request.user_id,
        username=username,
    )
    context = HarnessContext(
        user_id=request.user_id,
        username=username,
        thread_id=thread_id,
        tenant_id=request.tenant_id,
    )

    try:
        # 有附件时组装成多模态内容块（文本 + 图片 + 文档抽取文本）。
        user_content = await build_user_content(
            user_id=request.user_id,
            message=request.message,
            attachment_ids=request.attachments,
        )
        agent_input = {"messages": [{"role": "user", "content": user_content}]}
        result = await agent.ainvoke(
            agent_input,
            config=config,
            context=context,
        )
    except Exception as exc:
        logger.exception("Agent 调用异常: %s", type(exc).__name__)
        if type(exc).__name__ in {
            "OpenAIConnectionError",
            "APIConnectionError",
            "ConnectError",
        }:
            raise HTTPException(
                status_code=503,
                detail="模型服务暂时不可用，请检查网络和模型配置",
            ) from exc
        # RuntimeError 都是配置/环境级错误，消息本身已是可读中文，直接透传。
        if isinstance(exc, RuntimeError) and str(exc):
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        raise HTTPException(status_code=500, detail="Agent 调用失败") from exc

    answer = _extract_answer(result).strip()
    interrupts = result.get("__interrupt__", []) if isinstance(result, dict) else []
    if not answer and not interrupts:
        raise HTTPException(status_code=502, detail="Agent 未返回有效回答")
    await _bind_async_tasks_from_result(
        result,
        user_id=request.user_id,
        username=username,
        thread_id=thread_id,
    )
    await agent_loader.save_session(
        request.user_id,
        thread_id,
        make_session_title(request.message),
    )
    return ChatResponse(
        thread_id=thread_id,
        user_id=request.user_id,
        username=username,
        answer=answer,
        interrupt=(
            _serialize_interrupt(interrupts[0], thread_id)
            if interrupts
            else None
        ),
    )


# ============================================================
# 任务流路由前置
# ============================================================


def _skip_task_flow_route(request: ChatRequest) -> bool:
    """带附件的消息跳过任务流路由：任务流步骤暂时无法消费附件内容。"""
    return bool(request.attachments)


def _task_flow_answer(
    decision: RouteDecision, execution: Execution, final_output: str
) -> str:
    """把任务流执行结果汇总为本轮回答文本。"""
    flow_name = decision.flow.name if decision.flow else ""
    if execution.status == "done":
        return final_output.strip() or f"任务流「{flow_name}」已执行完成。"
    failed = [t for t in execution.step_traces if t.status == "failed"]
    failed_steps = "、".join(t.step_id for t in failed) or "未知步骤"
    return (
        f"任务流「{flow_name}」执行失败：步骤 {failed_steps} 重试后仍未完成，"
        "已降级处理并记录执行详情，可在任务流执行记录中查看。"
    )


def _task_flow_steps_payload(decision: RouteDecision) -> list[dict[str, Any]]:
    """把执行计划展开成前端执行卡片需要的步骤视图（含 fork 分支）。"""
    flow, plan = decision.flow, decision.plan
    if flow is None or plan is None:
        return []

    step_map: dict[str, Any] = {}

    def walk(steps: list[Any]) -> None:
        for step in steps:
            step_map[step.id] = step
            for sub in step.parallel or []:
                walk(sub.steps)

    walk(flow.steps)
    items: list[dict[str, Any]] = []
    for unit in plan.units:
        if unit.type == "fork":
            for branch in unit.branches or []:
                for sub_unit in branch.units:
                    step = step_map.get(sub_unit.ref)
                    items.append(
                        {
                            "id": sub_unit.ref,
                            "name": step.name if step else sub_unit.ref,
                            "branch": branch.name,
                        }
                    )
            continue
        step = step_map.get(unit.ref)
        items.append(
            {
                "id": unit.ref,
                "name": step.name if step else unit.ref,
                "branch": None,
            }
        )
    return items


async def _persist_task_flow_turn(
    request: ChatRequest, thread_id: str, answer: str
) -> None:
    """把任务流这一轮写入 checkpoint，刷新后历史会话仍能看到问答。"""
    await agent_loader.append_thread_messages(
        user_id=request.user_id,
        username=request.username or request.user_id,
        thread_id=thread_id,
        user_content=request.message,
        assistant_content=answer,
    )


async def _try_task_flow_chat(request: ChatRequest) -> ChatResponse | None:
    """``/chat`` 的任务流前置分支：命中则执行并直接回答，未命中返回 None。"""
    if _skip_task_flow_route(request):
        return None
    decision = await route_task_flow(
        request.message,
        request.user_id,
        flow_id=request.flow_id,
        entry_step=request.entry_step,
        user_state=request.user_state,
    )
    if decision is None:
        return None
    thread_id = request.thread_id or str(uuid.uuid4())
    username = request.username or request.user_id
    async with agent_loader.thread_operation(thread_id):
        if request.thread_id:
            await agent_loader.require_session(request.user_id, thread_id)
        # 会话索引照常建立，保证侧边栏与后续 resume 能找到本线程。
        await agent_loader.save_session(
            request.user_id, thread_id, make_session_title(request.message),
        )
        execution, final_output = await execute_task_flow(
            decision, user_id=request.user_id, thread_id=thread_id,
        )
        answer = _task_flow_answer(decision, execution, final_output)
        await _persist_task_flow_turn(request, thread_id, answer)
    logger.info(
        "任务流执行完成：flow=%s execution=%s status=%s",
        decision.flow.id if decision.flow else "",
        execution.id,
        execution.status,
    )
    return ChatResponse(
        thread_id=thread_id,
        user_id=request.user_id,
        username=username,
        answer=answer,
    )


async def _task_flow_sse_stream(
    request: ChatRequest, thread_id: str, decision: RouteDecision
) -> AsyncIterator[str]:
    """任务流执行的 SSE 事件流：进度事件驱动前端执行卡片，最终回答复用 token/done。"""
    username = request.username or request.user_id
    flow_id = decision.flow.id if decision.flow else ""
    flow_name = decision.flow.name if decision.flow else ""
    turn_started = time.monotonic()
    # 执行器回调（event_sink）与 SSE 生成器之间用队列解耦。
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

    async def event_sink(event_type: str, payload: dict[str, Any]) -> None:
        event = {
            "type": "task_flow_progress",
            "thread_id": thread_id,
            "flow_id": flow_id,
            "flow_name": flow_name,
            "status": event_type,
            "step_id": payload.get("step_id"),
            "branch": payload.get("branch"),
            "attempts": payload.get("attempt"),
        }
        if payload.get("delta"):
            event["delta"] = payload["delta"]
        if payload.get("error"):
            event["error"] = payload["error"]
        await queue.put(("progress", event))

    async def run_flow() -> None:
        try:
            async with agent_loader.thread_operation(thread_id):
                if request.thread_id:
                    await agent_loader.require_session(request.user_id, thread_id)
                await agent_loader.save_session(
                    request.user_id, thread_id, make_session_title(request.message),
                )
                execution, final_output = await execute_task_flow(
                    decision,
                    user_id=request.user_id,
                    thread_id=thread_id,
                    event_sink=event_sink,
                )
                answer = _task_flow_answer(decision, execution, final_output)
                await _persist_task_flow_turn(request, thread_id, answer)
            await queue.put(("result", (execution, final_output, answer)))
        except Exception as exc:  # noqa: BLE001 - 执行异常统一走 error 事件
            logger.exception("任务流执行异常")
            await queue.put(("error", exc))

    # 先告诉前端这条流命中哪条任务流、包含哪些步骤，卡片才能先渲染骨架。
    yield _create_sse_message(
        {
            "type": "task_flow_start",
            "thread_id": thread_id,
            "flow_id": flow_id,
            "flow_name": flow_name,
            "steps": _task_flow_steps_payload(decision),
        }
    )

    task = asyncio.create_task(run_flow())
    try:
        while True:
            kind, payload = await queue.get()
            if kind == "progress":
                yield _create_sse_message(payload)
                continue
            if kind == "error":
                yield _create_sse_message(
                    {
                        "type": "task_flow_end",
                        "thread_id": thread_id,
                        "flow_id": flow_id,
                        "flow_name": flow_name,
                        "status": "failed",
                    }
                )
                yield _create_sse_message({"type": "error", "message": "任务流执行失败"})
                return
            execution, final_output, answer = payload
            break
    finally:
        if not task.done():
            task.cancel()

    logger.info(
        "任务流执行完成：flow=%s execution=%s status=%s",
        flow_id,
        execution.id,
        execution.status,
    )
    yield _create_sse_message(
        {
            "type": "task_flow_end",
            "thread_id": thread_id,
            "flow_id": flow_id,
            "flow_name": flow_name,
            "execution_id": execution.id,
            "status": execution.status,
            "step_traces": [
                trace.model_dump(mode="json") for trace in execution.step_traces
            ],
        }
    )

    # 最终回答按小片推送，前端逐段渲染，避免末尾突然出现整段文本。
    message_id = f"assistant-{uuid.uuid4()}"
    for offset in range(0, len(answer), 160):
        yield _create_sse_message(
            {
                "type": "token",
                "message_id": message_id,
                "content": answer[offset : offset + 160],
                "source": "main",
            }
        )
        await asyncio.sleep(0)
    yield _create_sse_message(
        {
            "type": "done",
            "thread_id": thread_id,
            "user_id": request.user_id,
            "username": username,
            "content": answer,
            "interrupted": False,
            "usage": {},
            "session_usage": {},
            "round_limit": MAIN_AGENT_MODEL_RUN_LIMIT,
            "duration_ms": int((time.monotonic() - turn_started) * 1000),
        }
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """接收一条消息并返回完整的 Agent 回答。"""
    request.user_id = effective_user_id(request.user_id)
    # 任务流路由前置：命中则由任务流执行并直接返回结果，不再进入主 Agent 循环。
    flow_response = await _try_task_flow_chat(request)
    if flow_response is not None:
        return flow_response
    return await _run_chat(request)


def _summarize_hitl_resume(payload: dict[str, Any]) -> tuple[str, str]:
    """把用户提交的 HITL 内容压成（摘要, 结论），供轨迹页记录「人工回复」。

    ``resume`` 的载荷有三种形态：审批 decisions、新版 hitl 表单、旧版 text。
    这里只做展示用摘要，不参与实际恢复逻辑。
    """
    if not isinstance(payload, dict):
        return "", "answered"

    text = str(payload.get("text") or "").strip()
    if text:
        return text[:120], "answered"

    # 宿主能力审批：{decision: approve|approve_thread|reject, message}
    decision = str(payload.get("decision") or "").strip().lower()
    if decision:
        reason = str(payload.get("message") or "").strip()
        if decision in {"reject", "rejected"}:
            return (f"拒绝：{reason}" if reason else "拒绝执行")[:160], "rejected"
        if decision in {"approve_thread", "approve_session", "remember", "allow_thread"}:
            return "批准并加入本会话白名单", "approved"
        return "批准执行", "approved"

    decisions = payload.get("decisions")
    if isinstance(decisions, list) and decisions:
        rejected = [
            item
            for item in decisions
            if isinstance(item, dict) and item.get("type") == "reject"
        ]
        if rejected:
            reason = next(
                (str(item.get("message") or "").strip() for item in rejected if item.get("message")),
                "",
            )
            return (f"拒绝：{reason}" if reason else "拒绝执行")[:160], "rejected"
        return "批准执行", "approved"

    parts: list[str] = []
    values = payload.get("values")
    if isinstance(values, dict):
        for key, value in values.items():
            if value in ("", None, False) or value == []:
                continue
            rendered = "/".join(str(item) for item in value) if isinstance(value, list) else str(value)
            parts.append(f"{key}={rendered}")
    selected = payload.get("selected")
    if isinstance(selected, list) and selected:
        parts.append("选项=" + "/".join(str(item) for item in selected))
    free_text = str(payload.get("free_text") or "").strip()
    if free_text:
        parts.append(f"补充={free_text}")
    if payload.get("cancelled"):
        parts.append("已取消")
    status = "rejected" if payload.get("cancelled") else "answered"
    return "；".join(parts)[:160], status


def _attach_hitl_resume(context: HarnessContext, request: Any, is_resume: bool) -> None:
    """恢复请求把用户回复写进上下文，轨迹中间件据此落一条 hitl 事件。"""
    if not is_resume:
        return
    summary, status = _summarize_hitl_resume(getattr(request, "resume", None) or {})
    context.hitl_summary = summary
    context.hitl_status = status


@dataclass
class RetryPlan:
    """一次覆盖式重试所需的起点与被替换消息信息。"""

    checkpoint_id: str
    old_message_id: str
    old_excerpt: str


@dataclass
class WithdrawPlan:
    """撤回最新用户消息所需的检查点与轨迹删除边界。"""

    checkpoint_config: dict[str, Any]
    as_node: str
    content: str
    cutoff: str
    message_id: str
    # 撤回首条用户消息时直接重置 checkpoint，物理移除该会话的全部正文。
    reset_thread: bool = False


def _state_value(message: Any, name: str, default: Any = None) -> Any:
    """从 LangChain 消息对象或 checkpoint 字典中读取字段。"""
    return message.get(name, default) if isinstance(message, dict) else getattr(
        message, name, default
    )


def _state_role(message: Any) -> str:
    """把 checkpoint 消息类型归一为 user / assistant / tool。"""
    role = _state_value(message, "role") or _state_value(message, "type")
    return {"human": "user", "ai": "assistant"}.get(str(role), str(role or ""))


def _state_text(message: Any) -> str:
    return content_to_text(_state_value(message, "content", ""))


def _retry_denied(detail: str) -> HTTPException:
    return HTTPException(status_code=409, detail=detail)


async def _prepare_retry(
    *,
    user_id: str,
    username: str,
    thread_id: str,
    requested_message_id: str | None = None,
) -> RetryPlan:
    """校验并定位覆盖式重试的 checkpoint。

    只允许最后一条最终回答；包含副作用工具、等待审批、仍在流式执行的
    会话一律拒绝，避免重复执行写操作或丢弃后续轮次。
    """
    agent_id = await _resolve_agent_id(user_id, thread_id)  # noqa: F841
    agent = await agent_loader.get_agent_for_user(
        user_id=user_id,
        username=username,
        thread_id=thread_id,
        agent_id=agent_id,
    )
    config = agent_loader.create_config(
        thread_id=thread_id,
        user_id=user_id,
        username=username,
    )
    state = await agent.aget_state(config)
    if getattr(state, "interrupts", None):
        raise _retry_denied("会话正在等待人工确认，请先处理审批或补充信息")

    messages = list((getattr(state, "values", {}) or {}).get("messages", []) or [])
    if not messages:
        raise _retry_denied("当前会话还没有可重试的回答")

    last_human_index = -1
    for index, message in enumerate(messages):
        if _state_role(message) == "user":
            last_human_index = index
    if last_human_index < 0:
        raise _retry_denied("当前会话还没有用户消息")

    tail = messages[last_human_index + 1 :]
    if not tail:
        raise _retry_denied("当前轮次还没有生成回答")
    last = tail[-1]
    if _state_role(last) != "assistant" or _state_value(last, "tool_calls"):
        raise _retry_denied("只有最后一条最终回答支持重新回答")
    old_text = _state_text(last)
    if not old_text:
        raise _retry_denied("最后一条回答内容为空，无法重新回答")

    old_message_id = str(_state_value(last, "id", "") or "")
    if not old_message_id:
        raise _retry_denied("回答缺少稳定消息 ID，请刷新后再试")
    if requested_message_id and requested_message_id != old_message_id:
        raise _retry_denied("回答已被更新，请刷新后再试")

    for message in tail:
        for call in list(_state_value(message, "tool_calls", []) or []):
            name = str((call or {}).get("name") or "")
            if name in HIGH_RISK_TOOLS:
                raise _retry_denied(
                    f"该轮包含已执行的写操作（{name}），不支持重新回答"
                )

    human_id = str(_state_value(messages[last_human_index], "id", "") or "")
    checkpoint_id = ""
    async for snapshot in agent.aget_state_history(config):
        snapshot_messages = list(
            (getattr(snapshot, "values", {}) or {}).get("messages", []) or []
        )
        if not snapshot_messages:
            continue
        last_snapshot = snapshot_messages[-1]
        if _state_role(last_snapshot) != "user":
            continue
        snapshot_id = str(_state_value(last_snapshot, "id", "") or "")
        if human_id:
            if snapshot_id != human_id:
                continue
        elif len(snapshot_messages) != last_human_index + 1:
            continue
        # next 为空表示该 checkpoint 已结束，继续执行不会产生新回答。
        if not getattr(snapshot, "next", None):
            continue
        checkpoint_id = str(
            (getattr(snapshot, "config", {}) or {})
            .get("configurable", {})
            .get("checkpoint_id", "")
            or ""
        )
        if checkpoint_id:
            break

    if not checkpoint_id:
        raise _retry_denied("无法定位可重新回答的起始状态，请刷新后再试")
    return RetryPlan(
        checkpoint_id=checkpoint_id,
        old_message_id=old_message_id,
        old_excerpt=old_text,
    )


def _pick_graph_node(agent: Any) -> str:
    """选一个真实节点作为 aupdate_state 的写入方。

    撤回后写回的状态是一轮结束态；选中模型节点后，图会按最后一条
    无工具调用的 AI 消息路由到结束，不会留下待执行任务。
    """
    nodes = getattr(agent, "nodes", None) or {}
    for preferred in ("model", "agent", "chat"):
        if preferred in nodes:
            return preferred
    for name in nodes:
        if name not in {"__start__", "__end__"}:
            return str(name)
    return ""


def _user_display_text(message: Any) -> str:
    """只取用户原话，不把服务端注入的附件正文带回发送框。"""
    content = _state_value(message, "content", "")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return str(block.get("text") or "")
    return content_to_text(content)


async def _prepare_withdraw(
    *,
    user_id: str,
    username: str,
    thread_id: str,
) -> tuple[Any, WithdrawPlan]:
    """定位「最新用户消息之前」的检查点，并校验可以撤回。"""
    agent_id = await _resolve_agent_id(user_id, thread_id)
    agent = await agent_loader.get_agent_for_user(
        user_id=user_id,
        username=username,
        thread_id=thread_id,
        agent_id=agent_id,
    )
    config = agent_loader.create_config(
        thread_id=thread_id,
        user_id=user_id,
        username=username,
    )
    state = await agent.aget_state(config)
    messages = list((getattr(state, "values", {}) or {}).get("messages", []) or [])
    if not messages:
        raise _retry_denied("当前会话还没有可撤回的用户消息")

    last_human_index = -1
    for index, message in enumerate(messages):
        if _state_role(message) == "user":
            last_human_index = index
    if last_human_index < 0:
        raise _retry_denied("当前会话还没有可撤回的用户消息")

    human = messages[last_human_index]
    human_id = str(_state_value(human, "id", "") or "")
    if not human_id:
        raise _retry_denied("用户消息缺少稳定 ID，请刷新后再试")

    # 撤回首条用户消息：没有「上一轮」可回退，直接重置该会话正文。
    if last_human_index == 0:
        return agent, WithdrawPlan(
            checkpoint_config={},
            as_node="",
            content=_user_display_text(human),
            cutoff="",
            message_id=human_id,
            reset_thread=True,
        )

    # 从最新往回找「与 head 前缀完全一致、且已结束」的检查点。
    # 直接取「第一个不含该消息」的检查点可能停在 __start__ / before_agent
    # 这种待执行态（例如上一条用户消息还没被回答），不能作为撤回后的 head。
    prefix_ids = [
        str(_state_value(message, "id", "") or "")
        for message in messages[:last_human_index]
    ]
    target = None
    input_created = ""
    async for snapshot in agent.aget_state_history(config):
        snapshot_messages = list(
            (getattr(snapshot, "values", {}) or {}).get("messages", []) or []
        )
        snapshot_ids = [
            str(_state_value(message, "id", "") or "")
            for message in snapshot_messages
        ]
        if human_id in snapshot_ids:
            input_created = str(getattr(snapshot, "created_at", "") or input_created)
            continue
        if snapshot_ids != prefix_ids:
            continue
        # next 非空代表该检查点还在待执行状态，继续向更早的已结束态找。
        if getattr(snapshot, "next", None):
            continue
        target = snapshot
        break
    if target is None:
        raise _retry_denied("上一轮尚未形成稳定状态（可能仍有未完成任务），暂不能撤回")

    as_node = _pick_graph_node(agent)
    if not as_node:
        raise _retry_denied("无法定位 Agent 图节点，暂不能撤回")

    return agent, WithdrawPlan(
        checkpoint_config=dict(getattr(target, "config", {}) or {}),
        as_node=as_node,
        content=_user_display_text(human),
        cutoff=input_created or str(getattr(target, "created_at", "") or ""),
        message_id=human_id,
    )


async def _stream_response(
    request: ChatRequest | ResumeChatRequest | RetryChatRequest,
    *,
    thread_id: str,
    agent_input: dict[str, Any] | Command | None,
    is_resume: bool,
    retry_plan: RetryPlan | None = None,
) -> AsyncIterator[str]:
    """在整个 SSE 生命周期持有会话 guard，取消和异常时也释放。"""
    request.user_id = effective_user_id(request.user_id)
    try:
        async with agent_loader.thread_operation(thread_id):
            if is_resume or retry_plan is not None or getattr(request, "thread_id", None):
                await agent_loader.require_session(request.user_id, thread_id)
            async with aclosing(_stream_response_unlocked(
                request,
                thread_id=thread_id,
                agent_input=agent_input,
                is_resume=is_resume,
                retry_plan=retry_plan,
            )) as response:
                async for event in response:
                    yield event
    except HTTPException as exc:
        yield _create_sse_message(
            {"type": "error", "message": exc.detail, "status_code": exc.status_code}
        )


async def _stream_response_unlocked(
    request: ChatRequest | ResumeChatRequest | RetryChatRequest,
    *,
    thread_id: str,
    agent_input: dict[str, Any] | Command | None,
    is_resume: bool,
    retry_plan: RetryPlan | None = None,
) -> AsyncIterator[str]:
    """流式发送 Agent 文本、工具和中断事件。

    初始调用与 Command 恢复共享同一条流式处理链，
    确保恢复后仍能再次触发缺字段或审批中断，并沿用原始 checkpoint。
    """
    username = request.username or request.user_id
    project_token = None
    thread_token = None

    # 一轮耗时以服务端处理时间为准，前端不再自行估算。
    turn_started = time.monotonic()
    # 原始工具 ID 跨请求和 checkpoint 稳定，不能改成请求内随机展示 ID。
    # 仅用消息与分片索引关联后续不再携带 ID 的参数增量。
    tool_call_ids: dict[str, str] = {}
    tool_subagent_names: dict[str, str] = {}
    pending_tool_ids: set[str] = set()
    # 当前正在执行的委派工具调用栈：子 Agent 的事件挂到栈顶的 task 节点下。
    active_delegations: list[str] = []
    # 按子 Agent 名称分组的活跃委派：并发不同类型时优先挂到同类型节点，
    # 避免栈顶被另一个类型的委派抢走导致轨迹扁平成根节点。
    active_by_name: dict[str, list[str]] = {}
    pending_interrupts: dict[str, dict[str, Any]] = {}
    answer_parts: list[str] = []
    assistant_message_id: str | None = None
    # 经验命中标签只推一次：注入发生在首次模型调用前，首个 token 到达时即可读到。
    experience_marker_sent = False

    def _tool_event_id(tool_chunk: dict[str, Any], message_id: str) -> str:
        """为同一工具调用的多个分片分配稳定的前端展示 ID。"""
        chunk_index = tool_chunk.get("index", 0)
        chunk_key = f"{message_id}:{chunk_index}"
        source_id = tool_chunk.get("id")
        if source_id:
            tool_call_ids[chunk_key] = str(source_id)
        return tool_call_ids.setdefault(chunk_key, chunk_key)

    def _delegation_parent(source: str) -> str:
        """为子 Agent 事件找到它所属的 task 节点。

        优先按子 Agent 名称匹配：并发不同子 Agent 时栈顶可能是别人；
        同名并发再按启动顺序取第一个，保证事件不会全部掉到根节点。
        """
        candidates = active_by_name.get(source) or []
        if candidates:
            return candidates[0]
        return active_delegations[-1] if active_delegations else ""

    stream = None
    try:
        if not is_resume and retry_plan is None:
            assert isinstance(request, ChatRequest)
            # 中断发生前也必须建立会话索引，否则前端无法用 thread_id 恢复它。
            await agent_loader.save_session(
                request.user_id,
                thread_id,
                make_session_title(request.message),
                agent_id=request.agent_id,
                bind_agent=not request.thread_id,
            )
        # 会话索引已存在后解析项目；解析失败由 store 回落到默认项目。
        project = await agent_loader.resolve_project(request.user_id, thread_id)
        project_token = set_project_context(project)
        thread_token = set_project_thread(thread_id)
        agent_id = await _resolve_agent_id(
            request.user_id, thread_id, getattr(request, "agent_id", None)
        )
        agent = await agent_loader.get_agent_for_user(
            user_id=request.user_id,
            username=username,
            thread_id=thread_id,
            agent_id=agent_id,
        )
        config = agent_loader.create_config(
            thread_id=thread_id,
            user_id=request.user_id,
            username=username,
        )
        if retry_plan is not None:
            # 从「用户消息已写入、回答尚未生成」的 checkpoint 分叉，
            # 新回答会覆盖 head，旧回答只留在 checkpoint 历史里。
            config = {
                "configurable": {
                    **config["configurable"],
                    "checkpoint_id": retry_plan.checkpoint_id,
                }
            }
            yield _create_sse_message(
                {
                    "type": "retry_start",
                    "thread_id": thread_id,
                    "replaced_message_id": retry_plan.old_message_id,
                }
            )
        context = HarnessContext(
            user_id=request.user_id,
            username=username,
            thread_id=thread_id,
            tenant_id=request.tenant_id,
        )
        _attach_hitl_resume(context, request, is_resume)
        # 必须使用异步流式调用：MCP StructuredTool 不支持同步 invoke/stream。
        # values 流用于捕获 interrupt；同时订阅子图以保证审批和补充信息能冒泡，
        # 但普通子 Agent 事件仅是内部执行细节，不发送给用户界面。
        stream = agent.astream(
            agent_input,
            config=config,
            context=context,
            stream_mode=["messages", "values", "custom"],
            subgraphs=True,
            version="v2",
        )
        async for chunk in stream:
            if not experience_marker_sent:
                raw_items = str(
                    getattr(context, "evolution_experience_items", "") or ""
                )
                if raw_items:
                    experience_marker_sent = True
                    try:
                        items = json.loads(raw_items)
                    except json.JSONDecodeError:
                        items = []
                    if isinstance(items, list) and items:
                        yield _create_sse_message(
                            {
                                "type": "context_marker",
                                "marker_type": "experience",
                                "thread_id": thread_id,
                                "items": items,
                            }
                        )
            interrupt_values = _get_stream_interrupts(chunk)
            if interrupt_values:
                for interrupt_value in interrupt_values:
                    event = _serialize_interrupt(interrupt_value, thread_id)
                    key = event.get("interrupt_id") or json.dumps(event, sort_keys=True)
                    pending_interrupts[key] = event

                # 子图先发出中断，父图随后才保存可恢复状态。必须耗尽流，让中断
                # 冒泡并完成 checkpoint；此处关闭流会导致刷新后丢失审批表单。
                continue

            stream_message = _get_stream_message(chunk)
            if stream_message is None:
                custom_event = _get_custom_event(chunk)
                if custom_event is not None:
                    yield _create_sse_message({**custom_event, "thread_id": thread_id})
                continue
            token, metadata = stream_message
            if _is_internal_middleware_stream(metadata):
                continue
            source = _get_stream_source(chunk, metadata)
            # 子 Agent 的事件不再丢弃：推理与工具过程要挂到委派节点下展示，
            # 但其正文不算主 Agent 的最终回答。
            is_subagent = _is_subagent_stream(source)
            parent_tool_call_id = _delegation_parent(source) if is_subagent else ""
            message_id = str(getattr(token, "id", None) or uuid.uuid4())
            tool_chunks = _get_tool_chunks(token)

            # 推理内容与正文可能出现在同一个分片里，必须先发送再处理正文。
            reasoning = _get_reasoning_text(token)
            if reasoning:
                if assistant_message_id is None:
                    assistant_message_id = f"assistant-{uuid.uuid4()}"
                yield _create_sse_message(
                    {
                        "type": "reasoning",
                        "message_id": assistant_message_id,
                        "content": reasoning,
                        "source": source,
                        "parent_tool_call_id": parent_tool_call_id,
                    }
                )

            for tool_chunk in tool_chunks:
                assistant_message_id = None
                # 工具参数可能跨多个 chunk 返回。首次收到工具名称时发送开始事件，
                # 之后仅增量发送参数，避免前端重复创建工具消息。
                tool_call_id = _tool_event_id(tool_chunk, message_id)
                tool_name = tool_chunk.get("name")
                args = tool_chunk.get("args")
                if tool_name and tool_call_id not in pending_tool_ids:
                    pending_tool_ids.add(tool_call_id)
                    start_event = {
                        "type": "tool_start",
                        "tool_call_id": tool_call_id,
                        "tool_name": tool_name,
                        "source": source,
                        "parent_tool_call_id": parent_tool_call_id,
                    }
                    subagent_name = _get_subagent_name(args)
                    if subagent_name:
                        tool_subagent_names[tool_call_id] = subagent_name
                        start_event["subagent_name"] = subagent_name
                    yield _create_sse_message(start_event)
                    # 委派工具开启一个子 Agent 执行窗口，后续子图事件都挂到它下面。
                    if tool_name in DELEGATION_TOOL_NAMES:
                        active_delegations.append(tool_call_id)
                        if subagent_name:
                            active_by_name.setdefault(subagent_name, []).append(tool_call_id)

                if args:
                    args_text = _serialize_tool_args(args)
                    args_event = {
                        "type": "tool_args",
                        "tool_call_id": tool_call_id,
                        "args": args_text,
                        "source": source,
                        "parent_tool_call_id": parent_tool_call_id,
                    }
                    subagent_name = _get_subagent_name(args)
                    if subagent_name:
                        tool_subagent_names[tool_call_id] = subagent_name
                        args_event["subagent_name"] = subagent_name
                        # 参数分片晚于名称分片到达时，在这里补登记委派窗口，
                        # 否则后续子 Agent 事件找不到父 task 节点。
                        if tool_call_id not in active_delegations:
                            active_delegations.append(tool_call_id)
                        if tool_call_id not in active_by_name.setdefault(subagent_name, []):
                            active_by_name[subagent_name].append(tool_call_id)
                    yield _create_sse_message(args_event)

            if _is_tool_result(token):
                assistant_message_id = None
                # 工具结果到达后更新对应占位消息，并按固定顺序发送 result/end，
                # 让前端能够结束加载状态并展示完整结果。
                source_id = str(getattr(token, "tool_call_id", ""))
                tool_call_id = source_id
                tool_name = str(getattr(token, "name", "tool"))
                tool_status = "error" if getattr(token, "status", None) == "error" else "done"
                result_text = content_to_text(getattr(token, "content", ""))
                pending_tool_ids.discard(tool_call_id)
                if tool_call_id in active_delegations:
                    # 委派结束：先出栈，再让结果事件作为委派节点自身的结束记录。
                    active_delegations.remove(tool_call_id)
                    for ids in active_by_name.values():
                        if tool_call_id in ids:
                            ids.remove(tool_call_id)
                result_parent = (
                    _delegation_parent(source) if is_subagent else ""
                )
                if tool_name == START_ASYNC_TASK_TOOL_NAME and tool_status != "error":
                    task_id = extract_async_task_id(getattr(token, "content", ""))
                    if task_id:
                        await agent_loader.bind_async_task(
                            task_id,
                            user_id=request.user_id,
                            username=username,
                            thread_id=thread_id,
                        )
                result_event = {
                    "type": "tool_result",
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "text": result_text,
                    "source": source,
                    "status": tool_status,
                    "tool_status": tool_status,
                    "parent_tool_call_id": result_parent,
                }
                subagent_name = tool_subagent_names.get(tool_call_id)
                if subagent_name:
                    result_event["subagent_name"] = subagent_name
                yield _create_sse_message(result_event)
                yield _create_sse_message(
                    {
                        "type": "tool_end",
                        "status": tool_status,
                        "tool_status": tool_status,
                        "tool_call_id": tool_call_id,
                        "tool_name": tool_name,
                        "source": source,
                        "parent_tool_call_id": result_parent,
                    }
                )
                continue

            content = content_to_text(getattr(token, "content", ""))
            if not content or tool_chunks:
                continue
            if assistant_message_id is None:
                assistant_message_id = f"assistant-{uuid.uuid4()}"
            if is_subagent:
                # 子 Agent 的正文只作为委派节点下的过程输出，不进入最终回答。
                yield _create_sse_message(
                    {
                        "type": "subagent_token",
                        "message_id": assistant_message_id,
                        "content": content,
                        "source": source,
                        "parent_tool_call_id": parent_tool_call_id,
                    }
                )
                continue
            answer_parts.append(content)
            yield _create_sse_message(
                {
                    "type": "token",
                    "message_id": assistant_message_id,
                    "content": content,
                    "source": source,
                }
            )

        # 只有图已结束并完成持久化后才开放人工恢复；同一中断会跨图冒泡，按 ID 去重。
        # 子图事件可能携带中间层 ID；恢复必须使用根图 checkpoint 中的最终 ID，
        # 否则 Command 会重放子图并再次产生同一审批，而不会执行已批准的工具。
        get_state = getattr(agent, "aget_state", None)
        if pending_interrupts and callable(get_state):
            # 重试时 config 指向分叉起点，必须用最新 head 才能读到新产生的中断。
            state = await get_state(
                agent_loader.create_config(
                    thread_id=thread_id,
                    user_id=request.user_id,
                    username=username,
                )
            )
            checkpoint_interrupts = getattr(state, "interrupts", ()) or ()
            canonical_interrupts = [
                _serialize_interrupt(interrupt, thread_id)
                for interrupt in checkpoint_interrupts
            ]
            if canonical_interrupts:
                pending_interrupts = {
                    event.get("interrupt_id") or json.dumps(event, sort_keys=True): event
                    for event in canonical_interrupts
                }
        if not experience_marker_sent:
            # 兜底：某些模型在注入后没有产生可流式正文，仍要在 done 之前补发标签。
            raw_items = str(getattr(context, "evolution_experience_items", "") or "")
            if raw_items:
                try:
                    items = json.loads(raw_items)
                except json.JSONDecodeError:
                    items = []
                if isinstance(items, list) and items:
                    yield _create_sse_message(
                        {
                            "type": "context_marker",
                            "marker_type": "experience",
                            "thread_id": thread_id,
                            "items": items,
                        }
                    )
        for event in pending_interrupts.values():
            yield _create_sse_message(event)
        for tool_call_id in pending_tool_ids:
            yield _create_sse_message({"type": "tool_end", "tool_call_id": tool_call_id})
        # Token 统计唯一数据源是 TokenUsageMiddleware 写入的 Store：
        # rounds 最后一条 = 本轮最终回答的消耗；session = 会话累计。
        usage_data = await agent_loader.get_thread_usage(request.user_id, thread_id)
        rounds = usage_data.get("rounds", [])
        # 最终回答 ID 以最新 head 为准；usage 只作为兜底，避免模型未返回
        # usage 时把上一轮的消息 ID 错配给本轮。
        final_message_id = ""
        if callable(get_state):
            try:
                head_state = await get_state(
                    agent_loader.create_config(
                        thread_id=thread_id,
                        user_id=request.user_id,
                        username=username,
                    )
                )
                head_messages = list(
                    (getattr(head_state, "values", {}) or {}).get("messages", []) or []
                )
                last_human = -1
                for index, message in enumerate(head_messages):
                    if _state_role(message) == "user":
                        last_human = index
                # 只在最后一轮用户消息之后找最终回答，避免中断轮把上一轮
                # 的回答 ID 错配到本轮气泡上。
                for message in reversed(head_messages[last_human + 1 :]):
                    if (
                        _state_role(message) == "assistant"
                        and not _state_value(message, "tool_calls")
                        and _state_text(message)
                    ):
                        final_message_id = str(_state_value(message, "id", "") or "")
                        break
            except Exception:
                logger.debug("读取最终回答消息 ID 失败", exc_info=True)
        if not final_message_id and rounds and not pending_interrupts:
            # head 读取失败时的兜底；中断轮不兜底，避免错配上一轮回答。
            final_message_id = str(rounds[-1].get("message_id") or "")
        if retry_plan is not None and final_message_id:
            try:
                store = await agent_loader.get_store()
                await save_retry_event(
                    store,
                    user_id=request.user_id,
                    thread_id=thread_id,
                    old_message_id=retry_plan.old_message_id,
                    new_message_id=final_message_id,
                    old_excerpt=retry_plan.old_excerpt,
                )
            except Exception:
                logger.warning("写入重试反馈失败", exc_info=True)
        yield _create_sse_message(
            {
                "type": "done",
                "thread_id": thread_id,
                "user_id": request.user_id,
                "username": username,
                "content": "".join(answer_parts),
                "interrupted": bool(pending_interrupts),
                "usage": rounds[-1] if rounds else {},
                "session_usage": usage_data.get("session", {}),
                "final_message_id": final_message_id,
                "round_limit": MAIN_AGENT_MODEL_RUN_LIMIT,
                "duration_ms": int((time.monotonic() - turn_started) * 1000),
            }
        )
    except Exception as exc:
        logger.exception("Agent 流式调用异常")
        for tool_call_id in pending_tool_ids:
            yield _create_sse_message({"type": "tool_end", "tool_call_id": tool_call_id})
        # RuntimeError 都是配置/环境级错误，消息可读，直接透传给前端。
        detail = (
            str(exc)
            if isinstance(exc, RuntimeError) and str(exc)
            else "Agent 调用失败"
        )
        yield _create_sse_message({"type": "error", "message": detail})
    finally:
        if stream is not None:
            await stream.aclose()
        if thread_token is not None:
            reset_project_thread(thread_token)
        if project_token is not None:
            reset_project_context(project_token)


@router.post("/chat/stream")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    """以 SSE 格式发送一条消息的流式回答和完成事件。"""
    request.user_id = effective_user_id(request.user_id)
    # 禁用代理缓冲，确保 token 和工具状态在生成时立即到达浏览器。
    thread_id = request.thread_id or str(uuid.uuid4())
    if request.thread_id:
        await agent_loader.require_session(request.user_id, thread_id)
    # 任务流路由前置：命中则以任务流执行事件流响应，不再进入主 Agent 通用循环。
    if not _skip_task_flow_route(request):
        decision = await route_task_flow(
            request.message,
            request.user_id,
            flow_id=request.flow_id,
            entry_step=request.entry_step,
            user_state=request.user_state,
        )
        if decision is not None:
            return StreamingResponse(
                _task_flow_sse_stream(request, thread_id, decision),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
    # 有附件时组装成多模态内容块；解析失败会抛 4xx，不会静默丢附件。
    user_content = await build_user_content(
        user_id=request.user_id,
        message=request.message,
        attachment_ids=request.attachments,
    )
    agent_input = {"messages": [{"role": "user", "content": user_content}]}
    return StreamingResponse(
        _stream_response(
            request,
            thread_id=thread_id,
            agent_input=agent_input,
            is_resume=False,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/chat/{thread_id}/resume")
async def resume_chat(
    thread_id: str, request: ResumeChatRequest,
) -> StreamingResponse:
    """向同一会话的暂停图提交补充信息或审批决策，并恢复流式执行。"""
    request.user_id = effective_user_id(request.user_id)
    if await agent_loader.get_session(request.user_id, thread_id) is None:
        raise HTTPException(status_code=404, detail="会话不存在或无权恢复")
    return StreamingResponse(
        _stream_response(
            request,
            thread_id=thread_id,
            agent_input=Command(resume=request.resume),
            is_resume=True,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/chat/{thread_id}/retry")
async def retry_chat(
    thread_id: str, request: RetryChatRequest,
) -> StreamingResponse:
    """覆盖式重新生成最后一条最终回答。

    只允许最后一条回答；含副作用工具或等待人工确认时拒绝。
    """
    if not FEEDBACK_ENABLED:
        raise HTTPException(status_code=403, detail="重新回答功能已关闭")
    request.user_id = effective_user_id(request.user_id)  # noqa: PLW2901
    await agent_loader.require_session(request.user_id, thread_id)
    username = request.username or request.user_id
    plan = await _prepare_retry(
        user_id=request.user_id,
        username=username,
        thread_id=thread_id,
        requested_message_id=request.message_id,
    )
    return StreamingResponse(
        _stream_response(
            request,
            thread_id=thread_id,
            agent_input=None,
            is_resume=False,
            retry_plan=plan,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/chat/{thread_id}/withdraw", response_model=WithdrawChatResponse)
async def withdraw_chat(
    thread_id: str, request: WithdrawChatRequest,
) -> WithdrawChatResponse:
    """撤回最新一条用户消息及其之后的回答与轨迹。

    Token 计费与磁盘文件不回滚；撤回动作本身不写入反馈账本。
    """
    request.user_id = effective_user_id(request.user_id)
    await agent_loader.require_session(request.user_id, thread_id)
    username = request.username or request.user_id
    async with agent_loader.thread_operation(thread_id):
        agent, plan = await _prepare_withdraw(
            user_id=request.user_id,
            username=username,
            thread_id=thread_id,
        )
        config = agent_loader.create_config(
            thread_id=thread_id,
            user_id=request.user_id,
            username=username,
        )
        if plan.reset_thread:
            # 首条消息撤回：物理删除本会话 checkpoint，保留用量账本。
            await agent_loader.reset_thread_checkpoint(thread_id)
            await agent_loader.update_session(
                request.user_id, thread_id, title="新会话"
            )
            store = await agent_loader.get_store()
            try:
                await delete_trajectory(
                    store, user_id=request.user_id, thread_id=thread_id
                )
            except Exception:
                logger.warning("清理会话轨迹失败", exc_info=True)
        else:
            # 把 head 重指到「最新用户消息之前」的检查点，后续消息从会话中消失。
            await agent.aupdate_state(
                plan.checkpoint_config,
                {},
                as_node=plan.as_node,
            )
            head = await agent.aget_state(config)
            remaining_ids = {
                str(_state_value(message, "id", "") or "")
                for message in (getattr(head, "values", {}) or {}).get("messages", [])
            }
            if plan.message_id in remaining_ids:
                logger.error("撤回后消息仍在 head 中：thread=%s", thread_id)
                raise HTTPException(status_code=500, detail="撤回未生效，请刷新后重试")

            if plan.cutoff:
                store = await agent_loader.get_store()
                deleted = await delete_trajectory_events_since(
                    store,
                    user_id=request.user_id,
                    thread_id=thread_id,
                    cutoff=plan.cutoff,
                )
                logger.info(
                    "撤回用户消息：thread=%s message=%s 删除轨迹事件=%d",
                    thread_id,
                    plan.message_id,
                    deleted,
                )
        # 用量账本保持不动，已花的 Token 仍计入会话总消耗。
        usage_data = await agent_loader.get_thread_usage(
            request.user_id, thread_id
        )
        session_usage = dict(usage_data.get("session", {}))
        # 上下文占用回退到剩余会话的口径：首条撤回为 0，其余取上一轮输入。
        rounds = list(usage_data.get("rounds", []))
        if plan.reset_thread:
            session_usage["context_used"] = 0
        elif len(rounds) >= 2:
            session_usage["context_used"] = int(rounds[-2].get("input") or 0)
        return WithdrawChatResponse(
            thread_id=thread_id,
            content=plan.content,
            session_usage=session_usage,
        )


app.include_router(router)
app.include_router(agents_router)
app.include_router(feedback_router)
app.include_router(history_router)
app.include_router(async_tasks_router)
app.include_router(settings_router)
app.include_router(context_router)
app.include_router(trajectory_router)
app.include_router(evolution_router)
app.include_router(skills_router)
app.include_router(mcp_router)
app.include_router(rag_router)
app.include_router(projects_router)
app.include_router(session_files_router)
app.include_router(history_sessions_router)
app.include_router(task_flow_router)
