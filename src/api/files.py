"""线程文件读写接口：供前端预览、编辑、下载会话产物。

两种存储形态按是否配置沙箱自动分流：
- 无沙箱（OPEN_SANDBOX_API_KEY 为空）：文件存在 LangGraph 线程状态的 files 通道。
- 有沙箱：经 SandboxBackendProxy 执行命令与传输文件；SDK 是阻塞的，统一用 to_thread 包装。
"""

from __future__ import annotations

import asyncio
import json
import shlex
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from agent.config import OPEN_SANDBOX_API_KEY
from api.agent_loader import agent_loader


router = APIRouter()

# 沙箱内遍历文件系统的 python3 脚本：跳过系统/内部目录，单文件 >5MB 或总量 >500 条截断。
_SANDBOX_LIST_SCRIPT = (
    "import json, os\n"
    "from datetime import datetime, timezone\n"
    "skip = ('/proc', '/sys', '/dev', '/usr', '/bin', '/sbin', '/lib', '/lib64',\n"
    "        '/etc', '/var', '/opt', '/tmp', '/skills', '/.myagent')\n"
    "def hidden(p):\n"
    "    return any(p == s or p.startswith(s + '/') for s in skip)\n"
    "out = []\n"
    "for root, dirs, files in os.walk('/'):\n"
    "    dirs[:] = [d for d in dirs if not hidden(os.path.join(root, d))]\n"
    "    for name in files:\n"
    "        p = os.path.join(root, name)\n"
    "        if hidden(p):\n"
    "            continue\n"
    "        try:\n"
    "            st = os.stat(p)\n"
    "        except OSError:\n"
    "            continue\n"
    "        if st.st_size > 5 * 1024 * 1024:\n"
    "            continue\n"
    "        out.append({'path': p, 'size': st.st_size,\n"
    "                    'modified_at': datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat()})\n"
    "        if len(out) >= 500:\n"
    "            break\n"
    "    if len(out) >= 500:\n"
    "        break\n"
    "print(json.dumps(out))\n"
)


class FileContentUpdate(BaseModel):
    """PUT /threads/{thread_id}/files/content 请求体。"""

    path: str = Field(..., min_length=1)
    content: str = ""


def _validate_path(path: str) -> None:
    # 只允许绝对路径且禁止目录穿越；文件 API 不做路径规范化，交给存储层原样寻址。
    if not path or not path.startswith("/") or ".." in path:
        raise HTTPException(status_code=422, detail="path 必须非空、以 / 开头且不含 ..")


def _sandbox_available() -> bool:
    return bool(OPEN_SANDBOX_API_KEY)


async def _get_sandbox_proxy(user_id: str) -> Any:
    if agent_loader._sandbox_manager is None:
        raise HTTPException(status_code=500, detail="沙箱管理器未初始化")
    try:
        return await agent_loader._sandbox_manager.get_backend(user_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"沙箱不可用: {exc}") from exc


async def _get_state_files(user_id: str, thread_id: str) -> dict[str, Any]:
    """读取线程状态里的 files 通道（path -> {content, created_at, modified_at}）。"""
    agent = await agent_loader.get_agent_for_user(
        user_id=user_id, username=user_id, thread_id=thread_id,
    )
    config = agent_loader.create_config(
        thread_id=thread_id, user_id=user_id, username=user_id,
    )
    state = await agent.aget_state(config)
    files = state.values.get("files") or {}
    return dict(files)


def _state_content_to_text(content: Any) -> str:
    # 兼容两种形态：行数组 list[str] 或整段 str。
    if isinstance(content, list):
        return "\n".join(str(line) for line in content)
    return str(content or "")


def _state_content_size(content: Any) -> int:
    # 估算字节数：行数组按无分隔拼接，与写入端 split("\n") 的行数语义一致。
    if isinstance(content, list):
        return len("".join(str(line) for line in content).encode("utf-8"))
    return len(str(content or "").encode("utf-8"))


def _parse_json_lines(output: str) -> list[dict[str, Any]]:
    # execute 的 output 可能混有 stdout 噪声，从末尾找第一段可解析的 JSON 数组。
    for line in reversed(output.splitlines()):
        line = line.strip()
        if line.startswith("["):
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, list):
                return parsed
    raise ValueError("沙箱文件列表输出解析失败")


@router.get("/threads/{thread_id}/files")
async def list_thread_files(
    thread_id: str,
    user_id: str = Query(..., min_length=1),
) -> dict[str, Any]:
    """列出当前会话可预览的文件（沙箱或线程状态）。"""
    await agent_loader.require_session(user_id, thread_id)

    if _sandbox_available():
        proxy = await _get_sandbox_proxy(user_id)
        command = f"python3 -c {shlex.quote(_SANDBOX_LIST_SCRIPT)}"
        result = await asyncio.to_thread(proxy.execute, command)
        if result.exit_code != 0:
            raise HTTPException(status_code=502, detail="沙箱文件遍历失败")
        try:
            entries = _parse_json_lines(result.output or "")
        except ValueError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {
            "source": "sandbox",
            "files": [
                {
                    "path": str(item.get("path", "")),
                    "size": int(item.get("size", 0)),
                    "modified_at": item.get("modified_at"),
                }
                for item in entries
            ],
        }

    files = await _get_state_files(user_id, thread_id)
    entries = []
    for path, meta in files.items():
        content = meta.get("content") if isinstance(meta, dict) else None
        modified_at = meta.get("modified_at") if isinstance(meta, dict) else None
        entries.append(
            {
                "path": str(path),
                "size": _state_content_size(content),
                "modified_at": str(modified_at) if modified_at else None,
            }
        )
    entries.sort(key=lambda item: item["path"])
    return {"source": "state", "files": entries}


@router.get("/threads/{thread_id}/files/content")
async def read_thread_file(
    thread_id: str,
    user_id: str = Query(..., min_length=1),
    path: str = Query(..., min_length=1),
) -> dict[str, Any]:
    """读取单个文件的文本内容；二进制内容返回 415。"""
    await agent_loader.require_session(user_id, thread_id)
    _validate_path(path)

    if _sandbox_available():
        proxy = await _get_sandbox_proxy(user_id)
        responses = await asyncio.to_thread(proxy.download_files, [path])
        response = responses[0] if responses else None
        if response is None or response.error or response.content is None:
            raise HTTPException(status_code=404, detail="文件不存在")
        try:
            text = response.content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=415, detail="文件为二进制，无法预览") from exc
        if "\x00" in text:
            raise HTTPException(status_code=415, detail="文件为二进制，无法预览")
        return {"path": path, "content": text, "encoding": "utf-8"}

    files = await _get_state_files(user_id, thread_id)
    meta = files.get(path)
    if meta is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    content = meta.get("content") if isinstance(meta, dict) else meta
    text = _state_content_to_text(content)
    if "\x00" in text:
        raise HTTPException(status_code=415, detail="文件为二进制，无法预览")
    return {"path": path, "content": text, "encoding": "utf-8"}


@router.put("/threads/{thread_id}/files/content")
async def write_thread_file(
    thread_id: str,
    body: FileContentUpdate,
    user_id: str = Query(..., min_length=1),
) -> dict[str, Any]:
    """写入/覆盖单个文件的文本内容。"""
    await agent_loader.require_session(user_id, thread_id)
    _validate_path(body.path)

    if _sandbox_available():
        proxy = await _get_sandbox_proxy(user_id)
        responses = await asyncio.to_thread(
            proxy.upload_files, [(body.path, body.content.encode("utf-8"))],
        )
        response = responses[0] if responses else None
        if response is None or response.error:
            error = getattr(response, "error", None) or "无响应"
            raise HTTPException(status_code=502, detail=f"沙箱写入文件失败: {error}")
        return {"ok": True}

    files = await _get_state_files(user_id, thread_id)
    existing = files.get(body.path)
    now = datetime.now(timezone.utc).isoformat()
    created_at = (
        str(existing.get("created_at"))
        if isinstance(existing, dict) and existing.get("created_at")
        else now
    )
    agent = await agent_loader.get_agent_for_user(
        user_id=user_id, username=user_id, thread_id=thread_id,
    )
    config = agent_loader.create_config(
        thread_id=thread_id, user_id=user_id, username=user_id,
    )
    # files reducer 为合并语义，只传变更的 path。
    await agent.aupdate_state(
        config,
        {
            "files": {
                body.path: {
                    "content": body.content.split("\n"),
                    "created_at": created_at,
                    "modified_at": now,
                }
            }
        },
    )
    return {"ok": True}
