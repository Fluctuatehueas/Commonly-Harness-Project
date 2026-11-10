"""视觉分析工具：把本地图片交给视觉模型并返回文字结论。

为什么单独做一个工具，而不是只依赖 ``read_file``：
- ``read_file`` 是否把图片作为多模态内容传给模型，取决于模型 profile 与
  后端能力；部分兼容端点会退化成占位文本。
- 这里直接把图片编码成 OpenAI 兼容的 ``image_url`` 块调用 ``get_vision_model``，
  视觉子 Agent 因此能稳定工作。

支持的图片来源（按顺序尝试）：
1. 项目目录内的相对/绝对路径；
2. 用户上传的上下文目录（``CONTEXT_ROOT``）；
3. 沙箱内路径（通过 ``SandboxBackendProxy.download_files`` 读取）。
"""

from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from agent.config import CONTEXT_ROOT, get_vision_model
from agent.project_context import current_project_optional

MAX_IMAGE_MB = 20
DEFAULT_QUESTION = "请详细描述这张图片的内容，并指出其中与当前任务相关的关键信息。"


class ImageError(Exception):
    """带错误码的图片读取/分析异常。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def _error_payload(code: str, message: str, path: str = "") -> str:
    return json.dumps(
        {"ok": False, "path": path, "analysis": None, "error": {"code": code, "message": message}},
        ensure_ascii=False,
    )


def _read_local_file(raw: str) -> tuple[bytes, str] | None:
    """按项目目录、上下文目录的顺序读取本地图片。"""
    root = Path(raw).expanduser()
    candidates: list[Path] = []
    if root.is_absolute():
        candidates.append(root)
    else:
        project = current_project_optional()
        if project is not None and project.root is not None:
            candidates.append(project.root / raw.lstrip("/"))
        candidates.append(CONTEXT_ROOT / raw.lstrip("/"))
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved.is_file():
            return resolved.read_bytes(), str(resolved)
    return None


def _read_sandbox_file(raw: str, sandbox_backend: Any) -> tuple[bytes, str] | None:
    """读取沙箱内的图片；路径无效或不存在时返回 None。"""
    if sandbox_backend is None:
        return None
    try:
        responses = sandbox_backend.download_files([raw])
    except Exception:
        return None
    for response in responses:
        content = getattr(response, "content", None)
        if content:
            return content, raw
    return None


def _image_mime(path: str) -> str:
    mime, _ = mimetypes.guess_type(path)
    if not mime or not mime.startswith("image/"):
        raise ImageError(
            "UNSUPPORTED_FORMAT",
            f"不支持的图片格式：{path}；仅支持 png/jpg/jpeg/webp/gif/bmp",
        )
    return mime


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return str(content or "")


def create_vision_tools(sandbox_backend: Any = None) -> list[Any]:
    """创建视觉分析工具；sandbox_backend 为空时只读宿主项目与上下文目录。"""

    @tool
    async def analyze_image(path: str, question: str = DEFAULT_QUESTION) -> str:
        """分析指定图片并返回结论。

        Args:
            path: 图片路径；项目内相对路径、上传附件的 host_path 或沙箱路径均可。
            question: 希望视觉模型重点回答的问题。

        Returns:
            JSON 字符串：{ok, path, mime, bytes, analysis, error}
        """
        raw = str(path or "").strip()
        if not raw:
            return _error_payload("MISSING_PATH", "未提供图片路径")
        try:
            loaded = _read_local_file(raw) or _read_sandbox_file(raw, sandbox_backend)
            if loaded is None:
                raise ImageError("FILE_NOT_FOUND", f"图片不存在或无权读取：{raw}")
            data, resolved = loaded
            if len(data) > MAX_IMAGE_MB * 1024 * 1024:
                raise ImageError(
                    "TOO_LARGE",
                    f"图片超过 {MAX_IMAGE_MB}MB，请先压缩或裁剪后再分析",
                )
            mime = _image_mime(resolved)
            encoded = base64.b64encode(data).decode("ascii")
        except ImageError as exc:
            return _error_payload(exc.code, exc.message, raw)
        except Exception as exc:  # noqa: BLE001 - 工具边界统一转结构化错误
            return _error_payload("READ_FAILED", f"读取图片失败：{exc}", raw)

        prompt = str(question or "").strip() or DEFAULT_QUESTION
        message = {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{encoded}"},
                },
            ],
        }
        try:
            response = await get_vision_model().ainvoke([message])
            analysis = _content_to_text(getattr(response, "content", response)).strip()
        except Exception as exc:  # noqa: BLE001 - 模型失败也不能抛给上层图
            return _error_payload("VISION_FAILED", f"视觉模型调用失败：{exc}", resolved)

        return json.dumps(
            {
                "ok": True,
                "path": resolved,
                "mime": mime,
                "bytes": len(data),
                "analysis": analysis or "视觉模型未返回文本结论",
                "error": None,
            },
            ensure_ascii=False,
        )

    return [analyze_image]
