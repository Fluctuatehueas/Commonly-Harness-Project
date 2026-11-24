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
from dataclasses import dataclass
from typing import Any, AsyncIterator

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from langgraph.types import Command

from agent.config import (
    AGENT_TEAM_ENABLED,
    EVOLUTION_ENABLED,
    EVOLUTION_JUDGE_ENABLED,
    EVOLUTION_MODE,
    FEEDBACK_ENABLED,
    MAIN_AGENT_MODEL_RUN_LIMIT,
    PDCA_ENABLED,
    RUNTIME_BACKOFF_MS,
    RUNTIME_MAX_ATTEMPTS,
    RUNTIME_NO_PROGRESS_LIMIT,
    RUNTIME_RETRY_ENABLED,
)
from agent.runtime import RunSupervisor, RuntimeNotice
from agent.runtime.errors import classify
from utils.log_context import bind_log_fields, clear_log_fields, new_run_id, new_trace_id
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
from agent.time_context import MESSAGE_TIME_KEY, now_iso, strip_time_tags
from agent.feedback_store import save_retry_event
from agent.middlewares.pdc_guard import HIGH_RISK_TOOLS
from agent.trajectory_store import (
    delete_trajectory,
    delete_trajectory_events_since,
)
from agent.tools.async_sandbox_tools import START_ASYNC_TASK_TOOL_NAME
from api.agent_loader import agent_loader
from api.agent_groups import router as agent_groups_router
from api.agents import router as agents_router
from api.async_tasks import extract_async_task_id
from api.async_tasks import router as async_tasks_router
from api.context import attachment_metadata, build_user_content
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
from api.scheduler import router as scheduler_router
from api.session_files import router as session_files_router
from api.task_flow import router as task_flow_router
from task_flow.agent_bridge import route_task_flow
from task_flow.injection import (
    agent_executor_ids,
    build_flow_state,
    build_task_flow_spec,
)
from task_flow.models import Execution, StepTrace, new_execution_id, utcnow
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
    # 后台调度只存在于进程内：重启后把上一进程遗留的 created/running
    # 运行标记为中断，避免前端一直等待一个不会再推进的 run。
    try:
        from scheduler.store import SchedulerStore

        store = SchedulerStore()
        try:
            interrupted = await store.mark_runs_interrupted()
        finally:
            await store.close()
        if interrupted:
            logger.warning("已将 %s 个未完成的调度运行标记为中断", interrupted)
    except Exception:
        logger.warning("清理遗留调度运行失败，已忽略", exc_info=True)
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
    # 先从 AI 消息的工具调用里建立 call_id -> 子 Agent 名称映射，
    # 任务栏需要用它展示「异步子任务 · researcher」而不是裸 UUID。
    names: dict[str, str] = {}
    for message in messages:
        tool_calls = getattr(message, "tool_calls", None)
        if isinstance(message, dict):
            tool_calls = message.get("tool_calls", tool_calls)
        for call in tool_calls or []:
            if not isinstance(call, dict) or call.get("name") != START_ASYNC_TASK_TOOL_NAME:
                continue
            args = call.get("args") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            name = args.get("subagent_type") if isinstance(args, dict) else ""
            if name:
                names[str(call.get("id") or "")] = str(name)
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
            tool_call_id = getattr(message, "tool_call_id", None)
            if isinstance(message, dict):
                tool_call_id = message.get("tool_call_id", tool_call_id)
            await agent_loader.bind_async_task(
                task_id,
                user_id=user_id,
                username=username,
                thread_id=thread_id,
                agent_name=names.get(str(tool_call_id or ""), ""),
            )


def _create_sse_message(data: dict[str, Any]) -> str:
    """将事件数据编码为浏览器可解析的 SSE 消息。"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _pdc_buffering_active() -> bool:
    """PDCA 自检是否会拦截正文。

    自检关闭时 ``PdcGuardMiddleware`` 不会改写回答，正文没有缓冲的必要：
    继续缓冲只会把逐 token 的增量合并成 done 前的一条大消息，表现为「没有流式」。
    这里与 ``PdcGuardMiddleware._judging_enabled`` 使用同一组开关。
    """
    return (
        PDCA_ENABLED
        and EVOLUTION_ENABLED
        and EVOLUTION_JUDGE_ENABLED
        and EVOLUTION_MODE != "observe"
    )


def _drain_buffer(buffer: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """取出并清空缓冲事件；正文只有验证通过或确认是过程文本后才渲染。"""
    pending = [*buffer]
    buffer.clear()
    merged: list[dict[str, Any]] = []
    for event in pending:
        content = str(event.get("content") or "")
        if (
            merged
            and merged[-1].get("type") == "token"
            and merged[-1].get("message_id") == event.get("message_id")
        ):
            merged[-1]["content"] += content
            continue
        merged.append({**event, "content": content})
    # 时间标记只给模型做判断，不允许出现在正文里：放行前统一清洗。
    for event in merged:
        if event.get("type") == "token":
            event["content"] = strip_time_tags(str(event.get("content") or ""))
    return [
        event
        for event in merged
        if event.get("type") != "token" or event.get("content")
    ]


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
    # pdc_verified 是「正文可以放行」的信号：漏掉它，正文会一直缓冲到 done，
    # 表现就是正式回答没有流式输出、结束后才一次性出现。
    if event_type in {"pdc_revision", "pdc_verified"}:
        if chunk.get("ns"):
            return None
        return {"type": event_type, **{key: value for key, value in data.items() if key != "type"}}
    if event_type == "task_flow_progress":
        # 任务流步骤上报只来自主图；子图事件不参与执行卡片。
        if chunk.get("ns"):
            return None
        return {"type": event_type, **{key: value for key, value in data.items() if key != "type"}}
    if event_type == "scheduler_progress":
        # 调度事件来自主图的调度工具；节点子图内部不直发该事件。
        if chunk.get("ns"):
            return None
        return {"type": event_type, **{key: value for key, value in data.items() if key != "type"}}
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


async def _run_chat(
    request: ChatRequest, *, flow_turn: FlowTurn | None = None
) -> ChatResponse:
    """在会话 guard 中校验归属并执行非流式对话。"""
    request.user_id = effective_user_id(request.user_id)
    thread_id = request.thread_id or str(uuid.uuid4())
    async with agent_loader.thread_operation(thread_id):
        if request.thread_id:
            await agent_loader.require_session(request.user_id, thread_id)
        # Agent / 组是会话级选择：显式提供（含显式清空组）时记到当前会话，
        # 切回该会话时恢复；未提供才保留原绑定。
        explicit_group = (
            request.group_id if "group_id" in request.model_fields_set else None
        )
        await agent_loader.save_session(
            request.user_id,
            thread_id,
            make_session_title(request.message),
            agent_id=request.agent_id,
            bind_agent=not request.thread_id
            or "agent_id" in request.model_fields_set,
            group_id=explicit_group,
            bind_group=not request.thread_id or explicit_group is not None,
        )
        return await _run_chat_unlocked(request, thread_id, flow_turn=flow_turn)


async def _run_chat_unlocked(
    request: ChatRequest, thread_id: str, *, flow_turn: FlowTurn | None = None
) -> ChatResponse:
    """执行一轮非流式对话，并返回标准化响应。"""
    username = request.username or request.user_id
    # 本轮绑定的项目决定文件落点；Agent 图本身按用户缓存，不随项目重建。
    project = await agent_loader.resolve_project(request.user_id, thread_id)
    project_token = set_project_context(project)
    thread_token = set_project_thread(thread_id)
    try:
        return await _run_chat_turn(request, thread_id, username, flow_turn=flow_turn)
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


async def _resolve_group_id(
    user_id: str,
    thread_id: str,
    explicit: str | None = None,
) -> str:
    """本轮生效组：请求显式选择 > 会话绑定 > 无组。

    默认不自动选组：组是会话级选择，新会话以独立 Agent 身份执行，
    只有用户显式选过（或该会话之前绑定过）才带上组。
    """
    if not AGENT_TEAM_ENABLED:
        return ""
    selected = str(explicit or "").strip()
    if selected:
        return selected
    session = await agent_loader.get_session(user_id, thread_id)
    return str((session or {}).get("group_id") or "").strip()


async def _run_chat_turn(
    request: ChatRequest,
    thread_id: str,
    username: str,
    *,
    flow_turn: FlowTurn | None = None,
) -> ChatResponse:
    """持有会话工作目录上下文执行一轮对话。"""
    agent_id = await _resolve_agent_id(
        request.user_id, thread_id, getattr(request, "agent_id", None)
    )
    group_id = await _resolve_group_id(
        request.user_id, thread_id, getattr(request, "group_id", None)
    )
    agent = await agent_loader.get_agent_for_user(
        user_id=request.user_id,
        username=username,
        thread_id=thread_id,
        agent_id=agent_id,
        group_id=group_id,
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
        evolution_output_schema=getattr(request, "output_schema", None),
        evolution_numeric_ranges=getattr(request, "numeric_ranges", None),
        agent_id=agent_id,
        group_id=group_id,
    )

    try:
        if flow_turn is not None:
            # 命中任务流：用户原文一条 + 任务流规范一条（内部标记，历史不渲染）。
            agent_input = {
                "messages": _flow_messages(flow_turn, request.message),
                "task_flow": flow_turn.state,
            }
            await _carry_over_flow_progress(agent, config, flow_turn.state)
        else:
            # 有附件时组装成多模态内容块（文本 + 图片 + 文档抽取文本）。
            user_content = await build_user_content(
                user_id=request.user_id,
                message=request.message,
                attachment_ids=request.attachments,
            )
            # 附件元信息随用户消息落 checkpoint，历史回放时恢复缩略图与图标。
            attachments_meta = attachment_metadata(request.user_id, request.attachments)
            # task_flow 置空：新一轮普通消息不应继承上一轮的流程快照。
            agent_input = {
                "messages": [
                    HumanMessage(
                        content=user_content,
                        # 用户发送时刻就是这条消息的时间属性，供后续轮次回看。
                        additional_kwargs={
                            MESSAGE_TIME_KEY: now_iso(),
                            **({"attachments": attachments_meta} if attachments_meta else {}),
                        },
                    )
                ],
                "task_flow": {},
            }
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
    # 任务流一轮结束：用图状态里的步骤轨迹落执行记录（中断轮不落，等恢复后收尾）。
    flow_state = result.get("task_flow") if isinstance(result, dict) else None
    if flow_turn is not None and isinstance(flow_state, dict) and not interrupts:
        await _save_flow_execution(request, thread_id, flow_state)
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


@dataclass
class FlowTurn:
    """一次命中任务流后的注入信息。"""

    flow_id: str
    flow_name: str
    content: str
    state: dict[str, Any]


async def _flow_executor_names(flow: Any, user_id: str) -> dict[str, str]:
    """把任务流执行者引用的 MyAgent id 解析成名称，注入规范时给出可读目标。

    解析失败不阻断执行：名称缺失时注入层退化为只写 agent_id。
    """
    ids = agent_executor_ids(flow)
    if not ids:
        return {}
    try:
        from agent.agent_profiles import AgentProfileStore

        await agent_loader.initialize()
        store = agent_loader.store
        if store is None:
            return {}
        profiles = await AgentProfileStore(store, user_id).list()
        names = {
            str(item.get("id") or ""): str(item.get("name") or "")
            for item in profiles
        }
        return {agent_id: names[agent_id] for agent_id in ids if names.get(agent_id)}
    except Exception:
        logger.exception("任务流执行者名称解析失败")
        return {}


async def _prepare_flow_turn(request: ChatRequest) -> FlowTurn | None:
    """路由命中则构造注入消息与图状态；未命中返回 None。"""
    if _skip_task_flow_route(request):
        return None
    decision = await route_task_flow(
        request.message,
        request.user_id,
        flow_id=request.flow_id,
        entry_step=request.entry_step,
        user_state=request.user_state,
    )
    if decision is None or decision.flow is None:
        return None
    flow = decision.flow
    state = build_flow_state(flow, entry_step=decision.entry_step)
    content = build_task_flow_spec(
        flow,
        entry_step=decision.entry_step,
        user_state=decision.user_state,
        executor_names=await _flow_executor_names(flow, request.user_id),
    )
    return FlowTurn(flow_id=flow.id, flow_name=flow.name, content=content, state=state)


TASK_FLOW_INTERNAL_MARKER = "task_flow_spec"


def _flow_messages(flow_turn: FlowTurn, user_text: str) -> list[Any]:
    """构造任务流一轮的输入消息。

    用户原文与任务流规范都是 HumanMessage，但职责不同：
    - 用户原文保持原样，是气泡与历史里唯一可见的内容；
    - 任务流规范带上 ``harness_internal`` 标记，历史渲染时会被过滤掉，
      避免把整段提示词灌进用户气泡。
    """
    return [
        HumanMessage(
            content=user_text,
            additional_kwargs={MESSAGE_TIME_KEY: now_iso()},
        ),
        HumanMessage(
            content=flow_turn.content,
            additional_kwargs={
                "harness_internal": TASK_FLOW_INTERNAL_MARKER,
                MESSAGE_TIME_KEY: now_iso(),
            },
        ),
    ]


async def _carry_over_flow_progress(
    agent: Any, config: dict[str, Any], flow_state: dict[str, Any]
) -> None:
    """跨轮续跑：沿用上一轮已完成的步骤，避免卡片进度退回全量等待。

    只有上一轮仍有未完成步骤时才续跑；整条流已走完则视为新一轮执行，
    与"任务流真正走完后卡片不再出现"的展示规则保持一致。
    """
    steps = flow_state.get("steps") or []
    if not steps:
        return
    get_state = getattr(agent, "aget_state", None)
    if not callable(get_state):
        return
    try:
        state = await get_state(config)
    except Exception:  # noqa: BLE001 - 读不到历史就按全新执行，不影响本轮
        logger.warning("event=taskflow.resume.probe_failed", exc_info=True)
        return
    previous = (getattr(state, "values", {}) or {}).get("task_flow")
    if not isinstance(previous, dict) or previous.get("flow_id") != flow_state.get("flow_id"):
        return
    previous_steps = {
        str(item.get("id")): item
        for item in previous.get("steps") or []
        if isinstance(item, dict)
    }
    unfinished = [
        step_id
        for step_id, item in previous_steps.items()
        if item.get("status") not in ("done", "skipped")
    ]
    if not unfinished:
        return
    for step in steps:
        prev = previous_steps.get(str(step.get("id")))
        if prev and prev.get("status") in ("done", "skipped"):
            step["status"] = prev["status"]
            step["summary"] = prev.get("summary") or ""
    flow_state["resumed"] = True
    logger.info(
        "event=taskflow.resume flow_id=%s done_steps=%s",
        flow_state.get("flow_id"),
        sum(1 for step in steps if step.get("status") in ("done", "skipped")),
    )


async def _stored_flow_state(agent: Any, config: dict[str, Any]) -> dict[str, Any] | None:
    """读取 checkpoint 里的任务流快照；读不到返回 None。"""
    get_state = getattr(agent, "aget_state", None)
    if not callable(get_state):
        return None
    try:
        state = await get_state(config)
    except Exception:  # noqa: BLE001 - 读不到就按普通轮次继续
        logger.warning("event=taskflow.resume.probe_failed", exc_info=True)
        return None
    stored = (getattr(state, "values", {}) or {}).get("task_flow")
    if isinstance(stored, dict) and stored.get("steps"):
        return stored
    return None


def _flow_start_event(
    flow_turn: FlowTurn,
    thread_id: str,
    flow_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """命中卡片骨架：步骤 id 必须与后续 progress 事件一致。

    ``flow_state`` 带步骤状态时一并下发，续跑与中断恢复后的卡片直接按进度渲染，
    不会把已完成的步骤重置成等待。
    """
    state = flow_state if isinstance(flow_state, dict) and flow_state.get("steps") else flow_turn.state
    steps: list[dict[str, Any]] = []
    for item in state.get("steps", []):
        entry: dict[str, Any] = {
            "id": item.get("id", ""),
            "name": item.get("name", ""),
            "branch": item.get("branch"),
        }
        status = str(item.get("status") or "pending")
        if status != "pending":
            # skipped 在前端归入已完成，避免新增一种卡片状态。
            entry["status"] = "done" if status == "skipped" else status
        steps.append(entry)
    return {
        "type": "task_flow_start",
        "thread_id": thread_id,
        "flow_id": flow_turn.flow_id,
        "flow_name": flow_turn.flow_name,
        "steps": steps,
    }


def _flow_traces(flow_state: dict[str, Any]) -> list[dict[str, Any]]:
    """把图状态里的步骤快照转成执行记录的轨迹结构。"""
    traces: list[dict[str, Any]] = []
    for index, step in enumerate(flow_state.get("steps") or [], start=1):
        status = str(step.get("status") or "pending")
        summary = str(step.get("summary") or "")
        traces.append(
            {
                "seq": index,
                "step_id": str(step.get("id") or ""),
                "branch": step.get("branch"),
                "status": (
                    status if status in ("done", "failed", "skipped", "degraded") else "skipped"
                ),
                "attempts": 1,
                "input_summary": "",
                "output_summary": summary[:200],
                "output": summary[:20000],
                # 步骤起止时间来自 task_flow_step_update 的上报，旧数据为空。
                "started_at": step.get("started_at") or None,
                "finished_at": step.get("finished_at") or None,
                "duration_ms": 0,
                "check_result": None,
                "state_source": "step",
            }
        )
    return traces


async def _save_flow_execution(
    request: ChatRequest, thread_id: str, flow_state: dict[str, Any]
) -> str:
    """把图状态里的步骤轨迹落成执行记录；返回执行 id。

    执行 id 在命中时生成并写入图状态，HITL 恢复后会复用同一 id 做 upsert。
    """
    traces = _flow_traces(flow_state)
    execution_id = str(flow_state.get("execution_id") or new_execution_id())
    failed = any(trace["status"] == "failed" for trace in traces)
    execution = Execution(
        id=execution_id,
        flow_id=str(flow_state.get("flow_id") or ""),
        user_id=request.user_id,
        thread_id=thread_id,
        schema_hash=str(flow_state.get("schema_hash") or ""),
        entry_step=flow_state.get("entry_step"),
        status="failed" if failed else "done",
        step_traces=[StepTrace(**trace) for trace in traces],
        finished_at=utcnow(),
    )
    try:
        from api.task_flow import get_store

        store = await get_store()
        await store.save_execution(execution)
    except Exception:
        # 落库失败不吞掉本轮结果，只记日志。
        logger.exception("任务流执行记录落库失败：%s", execution_id)
    return execution_id


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """接收一条消息并返回完整的 Agent 回答。"""
    request.user_id = effective_user_id(request.user_id)
    # 命中任务流时把流程规范注入用户消息，仍由主 Agent 图执行。
    flow_turn = await _prepare_flow_turn(request)
    return await _run_chat(request, flow_turn=flow_turn)


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
    # 前端要撤回的这一轮并没有写进检查点（例如请求在建图/注册会话阶段就失败了）：
    # 此时 head 的最新用户消息其实是上一轮，必须原地返回，不能回退。
    skipped: bool = False


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


def _is_internal_human(message: Any) -> bool:
    """任务流规范等内部 HumanMessage 不算用户发来的那一轮。"""
    kwargs = _state_value(message, "additional_kwargs", {}) or {}
    return (
        isinstance(kwargs, dict)
        and kwargs.get("harness_internal") == TASK_FLOW_INTERNAL_MARKER
    )


def _normalize_user_text(text: str) -> str:
    """比较用户原文时忽略空白差异，避免前端 trim 后对不上。"""
    return " ".join(str(text or "").split())


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
    group_id = await _resolve_group_id(user_id, thread_id, None)
    agent = await agent_loader.get_agent_for_user(
        user_id=user_id,
        username=username,
        thread_id=thread_id,
        agent_id=agent_id,
        group_id=group_id,
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


def _skipped_withdraw_plan(expected_content: str) -> WithdrawPlan:
    """这一轮不在检查点里：不碰 head，只把原文还给前端回填。"""
    return WithdrawPlan(
        checkpoint_config={},
        as_node="",
        content=expected_content,
        cutoff="",
        message_id="",
        skipped=True,
    )


async def _prepare_withdraw(
    *,
    user_id: str,
    username: str,
    thread_id: str,
    expected_content: str | None = None,
) -> tuple[Any, WithdrawPlan]:
    """定位「最新用户消息之前」的检查点，并校验可以撤回。

    ``expected_content`` 是前端点击撤回的那一轮用户原文：只有 head 的最新
    用户消息与之匹配才真的回退，否则说明这一轮压根没写进检查点（例如请求
    在建图阶段就失败了），必须原样返回，不能把上一轮当成目标撤掉。
    """
    agent_id = await _resolve_agent_id(user_id, thread_id)
    group_id = await _resolve_group_id(user_id, thread_id, None)
    agent = await agent_loader.get_agent_for_user(
        user_id=user_id,
        username=username,
        thread_id=thread_id,
        agent_id=agent_id,
        group_id=group_id,
    )
    config = agent_loader.create_config(
        thread_id=thread_id,
        user_id=user_id,
        username=username,
    )
    state = await agent.aget_state(config)
    messages = list((getattr(state, "values", {}) or {}).get("messages", []) or [])
    if not messages:
        if expected_content is not None:
            return agent, _skipped_withdraw_plan(expected_content)
        raise _retry_denied("当前会话还没有可撤回的用户消息")

    last_human_index = -1
    for index, message in enumerate(messages):
        if _state_role(message) == "user" and not _is_internal_human(message):
            last_human_index = index
    if last_human_index < 0:
        if expected_content is not None:
            return agent, _skipped_withdraw_plan(expected_content)
        raise _retry_denied("当前会话还没有可撤回的用户消息")

    human = messages[last_human_index]
    human_id = str(_state_value(human, "id", "") or "")
    if not human_id:
        raise _retry_denied("用户消息缺少稳定 ID，请刷新后再试")

    if expected_content is not None and _normalize_user_text(
        _user_display_text(human)
    ) != _normalize_user_text(expected_content):
        return agent, _skipped_withdraw_plan(expected_content)

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
    flow_turn: FlowTurn | None = None,
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
                flow_turn=flow_turn,
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
    flow_turn: FlowTurn | None = None,
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
    # 正文先缓冲不直发：PDCA 自检可能重做，草稿渲染出去只会被撤回重放。
    # 自检通过（pdc_verified）、确认是工具前的过程文本（出现工具调用）或本轮
    # 结束时才真正下发给前端。自检整体关闭时不做缓冲，保持逐 token 流式。
    answer_buffer: list[dict[str, Any]] = []
    buffer_answer = _pdc_buffering_active()
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

    # 监督者与 run_id 必须在 try 之前就位：注册会话等前置步骤也可能失败，
    # 异常兜底需要它们来记录与收尾。
    supervisor = RunSupervisor(
        enabled=RUNTIME_RETRY_ENABLED,
        max_attempts=RUNTIME_MAX_ATTEMPTS,
        backoff_ms=RUNTIME_BACKOFF_MS,
        no_progress_limit=RUNTIME_NO_PROGRESS_LIMIT,
    )
    run_id = new_run_id()
    # 关联字段一次绑定：本请求内所有日志（含中间件、工具）都自动带上。
    bind_log_fields(
        trace_id=new_trace_id(),
        run_id=run_id,
        thread_id=thread_id,
        user_id=request.user_id,
    )
    try:
        if not is_resume and retry_plan is None:
            assert isinstance(request, ChatRequest)
            # 中断发生前也必须建立会话索引，否则前端无法用 thread_id 恢复它。
            explicit_group = (
                request.group_id if "group_id" in request.model_fields_set else None
            )
            await agent_loader.save_session(
                request.user_id,
                thread_id,
                make_session_title(request.message),
                agent_id=request.agent_id,
                bind_agent=not request.thread_id
                or "agent_id" in request.model_fields_set,
                group_id=explicit_group,
                bind_group=not request.thread_id or explicit_group is not None,
            )
        # 会话索引已存在后解析项目；解析失败由 store 回落到默认项目。
        project = await agent_loader.resolve_project(request.user_id, thread_id)
        project_token = set_project_context(project)
        thread_token = set_project_thread(thread_id)
        agent_id = await _resolve_agent_id(
            request.user_id, thread_id, getattr(request, "agent_id", None)
        )
        group_id = await _resolve_group_id(
            request.user_id, thread_id, getattr(request, "group_id", None)
        )
        agent = await agent_loader.get_agent_for_user(
            user_id=request.user_id,
            username=username,
            thread_id=thread_id,
            agent_id=agent_id,
            group_id=group_id,
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
            evolution_output_schema=getattr(request, "output_schema", None),
            evolution_numeric_ranges=getattr(request, "numeric_ranges", None),
            agent_id=agent_id,
            group_id=group_id,
        )
        _attach_hitl_resume(context, request, is_resume)
        # 必须使用异步流式调用：MCP StructuredTool 不支持同步 invoke/stream。
        # values 流用于捕获 interrupt；同时订阅子图以保证审批和补充信息能冒泡，
        # 但普通子 Agent 事件仅是内部执行细节，不发送给用户界面。
        # 瞬时异常由监督者在同一事件循环内重试：复用 thread_id 从 checkpoint 续跑。
        # 恢复（Command）与重新生成（分叉 checkpoint）不能重复执行同一命令。
        resumable = not is_resume and retry_plan is None
        if flow_turn is not None and isinstance(agent_input, dict):
            # 续跑沿用上一轮已完成的步骤；整条流已完成则回到全量 pending。
            await _carry_over_flow_progress(
                agent, config, agent_input.get("task_flow") or {}
            )
        # 命中任务流：先给前端卡片骨架（含续跑进度），后续步骤由工具上报更新。
        if flow_turn is not None:
            yield _create_sse_message(
                _flow_start_event(flow_turn, thread_id, agent_input.get("task_flow"))
            )
        elif is_resume:
            # 中断恢复：卡片骨架从 checkpoint 里的流程快照重建，避免恢复后卡片消失。
            stored = await _stored_flow_state(agent, config)
            if stored:
                yield _create_sse_message(
                    _flow_start_event(
                        FlowTurn(
                            flow_id=str(stored.get("flow_id") or ""),
                            flow_name=str(stored.get("flow_name") or ""),
                            content="",
                            state=stored,
                        ),
                        thread_id,
                        stored,
                    )
                )
        async for chunk in supervisor.stream(
            agent,
            agent_input,
            config=config,
            context=context,
            run_id=run_id,
            thread_id=thread_id,
            resumable=resumable,
            has_partial_answer=lambda: bool(answer_parts),
        ):
            if isinstance(chunk, RuntimeNotice):
                # 重试前收尾上一轮的半成品：结束悬挂工具占位、清空草稿正文，
                # 否则重放会拼出两段回答或留下转圈的工具卡片。
                for tool_call_id in list(pending_tool_ids):
                    yield _create_sse_message(
                        {"type": "tool_end", "tool_call_id": tool_call_id}
                    )
                pending_tool_ids.clear()
                active_delegations.clear()
                active_by_name.clear()
                answer_parts.clear()
                answer_buffer.clear()
                assistant_message_id = None
                yield _create_sse_message(chunk.to_event(thread_id))
                continue
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
                    # PDCA 修正轮：丢弃上一版草稿的累计正文，避免 done 拼出两份回答。
                    # 草稿仍在缓冲区，从未渲染，所以这里只需丢弃，前端不会看到撤回。
                    if custom_event.get("type") == "pdc_revision":
                        answer_parts.clear()
                        answer_buffer.clear()
                        assistant_message_id = None
                    elif custom_event.get("type") == "pdc_verified":
                        # 自检通过：此刻才把缓冲正文渲染给前端。
                        for pending in _drain_buffer(answer_buffer):
                            yield _create_sse_message(pending)
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

            # 带工具调用的消息正文只是过程说明：先放行缓冲正文，再发工具卡片，
            # 保证气泡顺序与阅读顺序一致。
            if tool_chunks:
                for pending in _drain_buffer(answer_buffer):
                    yield _create_sse_message(pending)
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
                            agent_name=tool_subagent_names.get(tool_call_id, ""),
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
            if not buffer_answer:
                # 自检关闭：不缓冲，逐 token 直接下发，恢复实时流式。
                cleaned = strip_time_tags(content)
                if cleaned:
                    yield _create_sse_message(
                        {
                            "type": "token",
                            "message_id": assistant_message_id,
                            "content": cleaned,
                            "source": source,
                        }
                    )
                continue
            # 正文先入缓冲，等自检结果或明确的工具调用信号再渲染。
            answer_buffer.append(
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
        # 兜底：未经自检（或自检被跳过）的正文在此统一放行，保证内容不丢。
        for pending in _drain_buffer(answer_buffer):
            yield _create_sse_message(pending)
        # Token 统计唯一数据源是 TokenUsageMiddleware 写入的 Store：
        # rounds 最后一条 = 本轮最终回答的消耗；session = 会话累计。
        usage_data = await agent_loader.get_thread_usage(request.user_id, thread_id)
        rounds = usage_data.get("rounds", [])
        # 最终回答 ID 以最新 head 为准；usage 只作为兜底，避免模型未返回
        # usage 时把上一轮的消息 ID 错配给本轮。
        final_message_id = ""
        flow_state: dict[str, Any] | None = None
        if callable(get_state):
            try:
                head_state = await get_state(
                    agent_loader.create_config(
                        thread_id=thread_id,
                        user_id=request.user_id,
                        username=username,
                    )
                )
                head_values = getattr(head_state, "values", {}) or {}
                stored_flow = head_values.get("task_flow")
                if isinstance(stored_flow, dict):
                    flow_state = stored_flow
                head_messages = list(head_values.get("messages", []) or [])
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
        # 任务流收尾：中断轮不收尾，等恢复后由后续请求补发 task_flow_end。
        if (
            isinstance(flow_state, dict)
            and flow_state.get("steps")
            and not pending_interrupts
        ):
            traces = _flow_traces(flow_state)
            execution_id = await _save_flow_execution(request, thread_id, flow_state)
            yield _create_sse_message(
                {
                    "type": "task_flow_end",
                    "thread_id": thread_id,
                    "flow_id": flow_state.get("flow_id", ""),
                    "flow_name": flow_state.get("flow_name", ""),
                    "execution_id": execution_id,
                    "status": (
                        "failed"
                        if any(trace["status"] == "failed" for trace in traces)
                        else "done"
                    ),
                    "step_traces": traces,
                }
            )
        yield _create_sse_message(
            {
                "type": "done",
                "thread_id": thread_id,
                "user_id": request.user_id,
                "username": username,
                "content": strip_time_tags("".join(answer_parts)),
                "interrupted": bool(pending_interrupts),
                "usage": rounds[-1] if rounds else {},
                "session_usage": usage_data.get("session", {}),
                "final_message_id": final_message_id,
                "round_limit": MAIN_AGENT_MODEL_RUN_LIMIT,
                "duration_ms": int((time.monotonic() - turn_started) * 1000),
            }
        )
    except Exception as exc:
        info = classify(exc)
        logger.error(
            "event=agent.run.failed run_id=%s thread_id=%s category=%s "
            "error_class=%s fingerprint=%s",
            run_id,
            thread_id,
            info.category.value,
            info.error_class,
            info.fingerprint,
            exc_info=True,
        )
        for tool_call_id in pending_tool_ids:
            yield _create_sse_message({"type": "tool_end", "tool_call_id": tool_call_id})
        # RuntimeError 都是配置/环境级错误，消息可读，直接透传给前端。
        detail = (
            str(exc)
            if isinstance(exc, RuntimeError) and str(exc)
            else "内部异常，已停止自动重试，已记录日志"
        )
        # run_id 是用户报障时的唯一锚点：前端复制它即可在日志中定位。
        yield _create_sse_message(
            {"type": "error", "message": detail, "run_id": run_id}
        )
    finally:
        await supervisor.aclose()
        if thread_token is not None:
            reset_project_thread(thread_token)
        if project_token is not None:
            reset_project_context(project_token)
        clear_log_fields()


@router.post("/chat/stream")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    """以 SSE 格式发送一条消息的流式回答和完成事件。"""
    request.user_id = effective_user_id(request.user_id)
    # 禁用代理缓冲，确保 token 和工具状态在生成时立即到达浏览器。
    thread_id = request.thread_id or str(uuid.uuid4())
    if request.thread_id:
        await agent_loader.require_session(request.user_id, thread_id)
    # 任务流路由：命中则把流程规范注入用户消息，仍由主 Agent 图执行。
    flow_turn = await _prepare_flow_turn(request)
    if flow_turn is not None:
        agent_input = {
            "messages": _flow_messages(flow_turn, request.message),
            "task_flow": flow_turn.state,
        }
    else:
        # 有附件时组装成多模态内容块；解析失败会抛 4xx，不会静默丢附件。
        user_content = await build_user_content(
            user_id=request.user_id,
            message=request.message,
            attachment_ids=request.attachments,
        )
        # 附件元信息随用户消息落 checkpoint，历史回放时恢复缩略图与图标。
        attachments_meta = attachment_metadata(request.user_id, request.attachments)
        # task_flow 置空：新一轮普通消息不应继承上一轮的流程快照。
        agent_input = {
            "messages": [
                HumanMessage(
                    content=user_content,
                    # 用户发送时刻就是这条消息的时间属性，供后续轮次回看。
                    additional_kwargs={
                        MESSAGE_TIME_KEY: now_iso(),
                        **({"attachments": attachments_meta} if attachments_meta else {}),
                    },
                )
            ],
            "task_flow": {},
        }
    return StreamingResponse(
        _stream_response(
            request,
            thread_id=thread_id,
            agent_input=agent_input,
            is_resume=False,
            flow_turn=flow_turn,
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
            expected_content=request.content,
        )
        config = agent_loader.create_config(
            thread_id=thread_id,
            user_id=request.user_id,
            username=username,
        )
        if plan.skipped:
            # 要撤回的这一轮没有写进检查点：head 保持不动，避免误撤上一轮。
            logger.info(
                "撤回跳过：该轮未写入检查点 thread=%s", thread_id
            )
        elif plan.reset_thread:
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
        if plan.skipped:
            pass
        elif plan.reset_thread:
            session_usage["context_used"] = 0
        elif len(rounds) >= 2:
            session_usage["context_used"] = int(rounds[-2].get("input") or 0)
        return WithdrawChatResponse(
            thread_id=thread_id,
            content=plan.content,
            session_usage=session_usage,
            removed=not plan.skipped,
        )


app.include_router(router)
app.include_router(agents_router)
app.include_router(agent_groups_router)
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
app.include_router(scheduler_router)
app.include_router(projects_router)
app.include_router(session_files_router)
app.include_router(history_sessions_router)
app.include_router(task_flow_router)
