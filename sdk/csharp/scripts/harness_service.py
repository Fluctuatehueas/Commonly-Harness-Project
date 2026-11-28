"""Harness 核心能力发现与配置 gRPC 服务。

只读取 Harness 自身的元信息（模型、技能、子 Agent、MCP、上下文格式），
以及模型配置的读写；不复制业务能力。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

_BASE = Path(__file__).resolve().parent
sys.path[:0] = [str(_BASE / "model"), str(_BASE / "grpc_service")]

import grpc  # noqa: E402
import yaml  # noqa: E402

import Harness_pb2  # noqa: E402
import Harness_pb2_grpc  # noqa: E402

from agent import config as agent_config  # noqa: E402
from agent.config import (  # noqa: E402
    ASYNC_AGENT_PROTOCOL_URL,
    MODEL_THINKING_ENABLED,
    OPEN_SANDBOX_API_KEY,
    PLANNER_ENABLED,
    PLANNER_VISION_ENABLED,
    SKILLS_ROOT,
    reload_model_config,
)
from agent.tools.commom_tools import get_basic_tools  # noqa: E402
from agent.tools.mcp_client import load_mcp_config  # noqa: E402
from api.agent_loader import AgentLoader  # noqa: E402
from api.agent_loader import agent_loader as http_agent_loader  # noqa: E402
from api.context import MAX_FILE_MB, MAX_FILES, SUPPORTED_FORMATS  # noqa: E402
from api.settings import _current_state, _update_env_file  # noqa: E402


SUBAGENT_CONFIG_DIR = Path(agent_config.__file__).resolve().parent / "subagents" / "configs"
MAIN_SKILLS_DIR = SKILLS_ROOT / "main"


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def _frontmatter(content: str) -> dict[str, Any]:
    if not content.startswith("---"):
        return {}
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        loaded = yaml.safe_load(parts[1])
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def _list_skills() -> list[Harness_pb2.SkillSummary]:
    if not MAIN_SKILLS_DIR.exists():
        return []

    skills: list[Harness_pb2.SkillSummary] = []
    for directory in sorted(MAIN_SKILLS_DIR.iterdir()):
        skill_file = directory / "SKILL.md"
        if not directory.is_dir() or not skill_file.exists():
            continue

        content = skill_file.read_text(encoding="utf-8")
        meta = _frontmatter(content)
        metadata_file = directory / "metadata.json"
        metadata_json = (
            metadata_file.read_text(encoding="utf-8")
            if metadata_file.exists()
            else "{}"
        )

        name = str(meta.get("name") or directory.name)
        skills.append(
            Harness_pb2.SkillSummary(
                name=name,
                display_name=str(meta.get("display_name") or name),
                description=str(meta.get("description") or ""),
                path=str(directory),
                metadata_json=metadata_json,
            )
        )
    return skills


def _str_list(value: Any) -> list[str]:
    """把 YAML 中的字符串列表规整为去重列表；非法值返回空列表。"""
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item) for item in value if isinstance(item, str) and item))


def _list_subagents() -> list[Harness_pb2.SubagentSummary]:
    if not SUBAGENT_CONFIG_DIR.exists():
        return []

    subagents: list[Harness_pb2.SubagentSummary] = []
    for path in sorted(SUBAGENT_CONFIG_DIR.glob("*.yaml")):
        config = _read_yaml(path)
        name = str(config.get("name") or path.stem)
        model = config.get("model")
        subagents.append(
            Harness_pb2.SubagentSummary(
                name=name,
                description=str(config.get("description") or ""),
                path=str(path),
                config_json=json.dumps(config, ensure_ascii=False, default=str),
                tools=_str_list(config.get("tools")),
                optional_tools=_str_list(config.get("optional_tools")),
                mcp_servers=_str_list(config.get("mcp_servers")),
                skills=_str_list(config.get("skills")),
                model=model if isinstance(model, str) else "",
            )
        )
    return subagents


def _subagent_refs() -> dict[str, list[str]]:
    """统计每个 MCP Server 被哪些子 Agent 引用。"""
    usage: dict[str, list[str]] = {}
    if not SUBAGENT_CONFIG_DIR.exists():
        return usage
    for path in sorted(SUBAGENT_CONFIG_DIR.glob("*.yaml")):
        config = _read_yaml(path)
        name = str(config.get("name") or path.stem)
        for server in _str_list(config.get("mcp_servers")):
            usage.setdefault(server, []).append(name)
    return usage


def _list_mcp_servers() -> list[Harness_pb2.McpServerSummary]:
    # 以项目根 mcp_servers.yaml 为唯一事实源；子 Agent YAML 只引用名称。
    try:
        config = load_mcp_config()
    except Exception:
        return []

    usage = _subagent_refs()
    servers: list[Harness_pb2.McpServerSummary] = []
    for name, server in sorted(config["servers"].items()):
        servers.append(
            Harness_pb2.McpServerSummary(
                name=name,
                transport=str(server.get("transport") or ""),
                description=str(server.get("description") or ""),
                config_json=json.dumps(server, ensure_ascii=False, default=str),
                optional=bool(server.get("optional", False)),
                enabled=bool(server.get("enabled", True)),
                used_by_subagents=usage.get(name, []),
            )
        )
    return servers


class HarnessServiceServicer(Harness_pb2_grpc.HarnessServiceServicer):
    """Harness 能力发现、模型配置与只读资源清单。"""

    def __init__(self, loader: AgentLoader) -> None:
        self._loader = loader

    async def GetCapabilities(
        self,
        request: Harness_pb2.GetCapabilitiesRequest,
        context: grpc.aio.ServicerContext,
    ) -> Harness_pb2.GetCapabilitiesResponse:
        user_id = request.user_id.strip() or "anonymous"
        sessions = await self._loader.list_sessions(user_id)
        skills = _list_skills()
        subagents = _list_subagents()
        mcp_servers = _list_mcp_servers()

        capabilities = [
            Harness_pb2.Capability(name="chat_stream", enabled=True, detail="ChatService.Chat"),
            Harness_pb2.Capability(name="tool_calling", enabled=True, detail="ToolService.CallTool"),
            Harness_pb2.Capability(name="session_history", enabled=True, detail="SessionService.GetSessionHistory"),
            Harness_pb2.Capability(name="session_management", enabled=True, detail="Create/Rename/Pin/Delete"),
            Harness_pb2.Capability(name="trajectory", enabled=True, detail="TrajectoryService.GetTrajectory"),
            Harness_pb2.Capability(name="skills_read", enabled=True, detail="ListSkills / GetSkill"),
            Harness_pb2.Capability(name="skills_write", enabled=False, detail="技能写入仍由 Agent 工具管理"),
            Harness_pb2.Capability(name="subagents_read", enabled=True, detail="ListSubagents"),
            Harness_pb2.Capability(name="mcp_read", enabled=True, detail="ListMcpServers"),
            Harness_pb2.Capability(name="model_settings", enabled=True, detail="Get/UpdateModelSettings"),
            Harness_pb2.Capability(name="hitl_resume", enabled=True, detail="ChatService.ResumeChat"),
            Harness_pb2.Capability(
                name="planner",
                enabled=PLANNER_ENABLED,
                detail="TASK 模式 Plan-and-Execute；HARNESS_PLANNER_ENABLED=0 可回退",
            ),
            Harness_pb2.Capability(
                name="plan_stream",
                enabled=PLANNER_ENABLED,
                detail="ChatChunk.plan 全量计划快照",
            ),
            Harness_pb2.Capability(
                name="planner_vision",
                enabled=PLANNER_ENABLED and PLANNER_VISION_ENABLED,
                detail="搭建任务自动插入视觉验收步骤",
            ),
            Harness_pb2.Capability(name="file_browser", enabled=True, detail="FileService state backend"),
            Harness_pb2.Capability(name="context_formats", enabled=True, detail="ListContextFormats"),
            Harness_pb2.Capability(name="context_upload", enabled=False, detail="附件上传/URL 导入待接入"),
            Harness_pb2.Capability(
                name="sandbox",
                enabled=False,
                detail="gRPC ChatService 当前使用无沙箱模式；HTTP 链路可按配置启用",
            ),
            Harness_pb2.Capability(
                name="memory",
                enabled=True,
                detail="通过 Agent 工具 / AGENTS.md 管理，gRPC 直连未开放",
            ),
            Harness_pb2.Capability(
                name="async_tasks",
                enabled=bool(ASYNC_AGENT_PROTOCOL_URL),
                detail=ASYNC_AGENT_PROTOCOL_URL or "未配置 Agent Protocol",
            ),
            Harness_pb2.Capability(
                name="csharp_tool_service",
                enabled=True,
                detail="C# ToolService 127.0.0.1:50052",
            ),
        ]

        return Harness_pb2.GetCapabilitiesResponse(
            contract_version="1.0.0",
            model_name=os.getenv("DEEPSEEK_MODEL_NAME", ""),
            model_thinking_enabled=bool(MODEL_THINKING_ENABLED),
            sandbox_enabled=bool(OPEN_SANDBOX_API_KEY),
            python_tool_count=len(get_basic_tools()),
            skill_count=len(skills),
            subagent_count=len(subagents),
            mcp_server_count=len(mcp_servers),
            session_count=len(sessions),
            capabilities=capabilities,
        )

    async def GetModelSettings(
        self,
        request: Harness_pb2.GetModelSettingsRequest,
        context: grpc.aio.ServicerContext,
    ) -> Harness_pb2.ModelSettings:
        return self._model_settings()

    async def UpdateModelSettings(
        self,
        request: Harness_pb2.UpdateModelSettingsRequest,
        context: grpc.aio.ServicerContext,
    ) -> Harness_pb2.ModelSettings:
        updates: dict[str, str] = {}

        if request.HasField("model_name"):
            value = request.model_name.strip()
            if not value:
                await context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT,
                    "model_name 不能为空",
                )
            updates["DEEPSEEK_MODEL_NAME"] = value

        if request.HasField("base_url"):
            value = request.base_url.strip()
            if value and not value.startswith(("http://", "https://")):
                await context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT,
                    "base_url 必须以 http:// 或 https:// 开头",
                )
            updates["DEEPSEEK_BASE_URL"] = value

        if request.HasField("api_key") and request.api_key.strip():
            updates["DEEPSEEK_API_KEY"] = request.api_key.strip()

        if request.HasField("thinking_enabled"):
            updates["HARNESS_MODEL_THINKING"] = (
                "true" if request.thinking_enabled else "false"
            )

        if not updates:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "没有需要更新的字段",
            )

        try:
            _update_env_file(updates)
        except OSError as exc:
            await context.abort(
                grpc.StatusCode.INTERNAL,
                f"写入 .env 失败: {exc}",
            )

        reload_model_config()
        await self._loader.reset_agents()
        await http_agent_loader.reset_agents()
        return self._model_settings()

    async def ListSkills(
        self,
        request: Harness_pb2.ListSkillsRequest,
        context: grpc.aio.ServicerContext,
    ) -> Harness_pb2.ListSkillsResponse:
        return Harness_pb2.ListSkillsResponse(skills=_list_skills())

    async def GetSkill(
        self,
        request: Harness_pb2.GetSkillRequest,
        context: grpc.aio.ServicerContext,
    ) -> Harness_pb2.GetSkillResponse:
        for skill in _list_skills():
            if skill.name != request.name:
                continue
            content_path = Path(skill.path) / "SKILL.md"
            content = (
                content_path.read_text(encoding="utf-8")
                if content_path.exists()
                else ""
            )
            return Harness_pb2.GetSkillResponse(skill=skill, content=content)

        await context.abort(
            grpc.StatusCode.NOT_FOUND,
            f"未找到技能 {request.name}",
        )

    async def ListSubagents(
        self,
        request: Harness_pb2.ListSubagentsRequest,
        context: grpc.aio.ServicerContext,
    ) -> Harness_pb2.ListSubagentsResponse:
        return Harness_pb2.ListSubagentsResponse(subagents=_list_subagents())

    async def ListMcpServers(
        self,
        request: Harness_pb2.ListMcpServersRequest,
        context: grpc.aio.ServicerContext,
    ) -> Harness_pb2.ListMcpServersResponse:
        return Harness_pb2.ListMcpServersResponse(servers=_list_mcp_servers())

    async def ListContextFormats(
        self,
        request: Harness_pb2.ListContextFormatsRequest,
        context: grpc.aio.ServicerContext,
    ) -> Harness_pb2.ListContextFormatsResponse:
        formats = [
            Harness_pb2.ContextFormat(
                extension=extension,
                kind=kind,
                mime_type=mime_type,
            )
            for extension, (kind, mime_type) in sorted(SUPPORTED_FORMATS.items())
        ]
        return Harness_pb2.ListContextFormatsResponse(
            formats=formats,
            max_file_mb=MAX_FILE_MB,
            max_files=MAX_FILES,
        )

    @staticmethod
    def _model_settings() -> Harness_pb2.ModelSettings:
        state = _current_state()
        return Harness_pb2.ModelSettings(
            model_name=str(state.get("model_name", "")),
            base_url=str(state.get("base_url", "")),
            has_api_key=bool(state.get("has_api_key", False)),
            thinking_enabled=bool(state.get("thinking_enabled", False)),
        )
