"""Harness Agent 的最小对话 HTTP 接口。

对外协议：
- ``POST /chat``：非流式对话
- ``POST /chat/stream``：SSE 流式对话
- ``POST /chat/{thread_id}/resume``：恢复被 HITL 中断的会话

本模块不依赖任何业务模型；业务能力通过工具的注册与子 Agent 配置注入。
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from contextlib import aclosing, asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langgraph.types import Command

from agent.config import MAIN_AGENT_MODEL_RUN_LIMIT
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
)
from agent.tools.async_sandbox_tools import START_ASYNC_TASK_TOOL_NAME
from api.agent_loader import agent_loader
from api.async_tasks import extract_async_task_id
from api.async_tasks import router as async_tasks_router
from api.context import build_user_content
from api.context import router as context_router
from api.evolution import router as evolution_router
from api.history import router as history_router
from api.history import sessions_router as history_sessions_router
from api.settings import router as settings_router
from api.trajectory import router as trajectory_router
from api.skills import router as skills_router
from api.mcp import router as mcp_router
from api.projects import router as projects_router
from api.rag import router as rag_router
from api.session_files import router as session_files_router
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

    只接受主图（ns 为空）的压缩事件，避免子 Agent 压缩干扰聊天区状态。
    """
    if not isinstance(chunk, dict) or chunk.get("type") != "custom":
        return None
    if chunk.get("ns"):
        return None
    data = chunk.get("data")
    if not isinstance(data, dict):
        return None
    event_type = str(data.get("type") or "")
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
    thread_id = request.thread_id or str(uuid.uuid4())
    async with agent_loader.thread_operation(thread_id):
        if request.thread_id:
            await agent_loader.require_session(request.user_id, thread_id)
        await agent_loader.save_session(
            request.user_id, thread_id, make_session_title(request.message),
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


async def _run_chat_turn(
    request: ChatRequest, thread_id: str, username: str,
) -> ChatResponse:
    """持有会话工作目录上下文执行一轮对话。"""
    agent = await agent_loader.get_agent_for_user(
        user_id=request.user_id,
        username=username,
        thread_id=thread_id,
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


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """接收一条消息并返回完整的 Agent 回答。"""
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


async def _stream_response(
    request: ChatRequest | ResumeChatRequest,
    *,
    thread_id: str,
    agent_input: dict[str, Any] | Command,
    is_resume: bool,
) -> AsyncIterator[str]:
    """在整个 SSE 生命周期持有会话 guard，取消和异常时也释放。"""
    try:
        async with agent_loader.thread_operation(thread_id):
            if is_resume or getattr(request, "thread_id", None):
                await agent_loader.require_session(request.user_id, thread_id)
            async with aclosing(_stream_response_unlocked(
                request,
                thread_id=thread_id,
                agent_input=agent_input,
                is_resume=is_resume,
            )) as response:
                async for event in response:
                    yield event
    except HTTPException as exc:
        yield _create_sse_message(
            {"type": "error", "message": exc.detail, "status_code": exc.status_code}
        )


async def _stream_response_unlocked(
    request: ChatRequest | ResumeChatRequest,
    *,
    thread_id: str,
    agent_input: dict[str, Any] | Command,
    is_resume: bool,
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
    pending_interrupts: dict[str, dict[str, Any]] = {}
    answer_parts: list[str] = []
    assistant_message_id: str | None = None

    def _tool_event_id(tool_chunk: dict[str, Any], message_id: str) -> str:
        """为同一工具调用的多个分片分配稳定的前端展示 ID。"""
        chunk_index = tool_chunk.get("index", 0)
        chunk_key = f"{message_id}:{chunk_index}"
        source_id = tool_chunk.get("id")
        if source_id:
            tool_call_ids[chunk_key] = str(source_id)
        return tool_call_ids.setdefault(chunk_key, chunk_key)

    stream = None
    try:
        if not is_resume:
            assert isinstance(request, ChatRequest)
            # 中断发生前也必须建立会话索引，否则前端无法用 thread_id 恢复它。
            await agent_loader.save_session(
                request.user_id,
                thread_id,
                make_session_title(request.message),
            )
        # 会话索引已存在后解析项目；解析失败由 store 回落到默认项目。
        project = await agent_loader.resolve_project(request.user_id, thread_id)
        project_token = set_project_context(project)
        thread_token = set_project_thread(thread_id)
        agent = await agent_loader.get_agent_for_user(
            user_id=request.user_id,
            username=username,
            thread_id=thread_id,
        )
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
            parent_tool_call_id = (
                active_delegations[-1]
                if is_subagent and active_delegations
                else ""
            )
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
                result_parent = (
                    active_delegations[-1]
                    if is_subagent and active_delegations
                    else ""
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
            state = await get_state(config)
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
        for event in pending_interrupts.values():
            yield _create_sse_message(event)
        for tool_call_id in pending_tool_ids:
            yield _create_sse_message({"type": "tool_end", "tool_call_id": tool_call_id})
        # Token 统计唯一数据源是 TokenUsageMiddleware 写入的 Store：
        # rounds 最后一条 = 本轮最终回答的消耗；session = 会话累计。
        usage_data = await agent_loader.get_thread_usage(request.user_id, thread_id)
        rounds = usage_data.get("rounds", [])
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
    # 禁用代理缓冲，确保 token 和工具状态在生成时立即到达浏览器。
    thread_id = request.thread_id or str(uuid.uuid4())
    if request.thread_id:
        await agent_loader.require_session(request.user_id, thread_id)
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


app.include_router(router)
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
