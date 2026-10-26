"""上下文附件接口：上传、格式声明，以及聊天前的消息组装。

支持哪些格式只在 ``SUPPORTED_FORMATS`` 里定义，前端通过
``GET /context/formats`` 动态读取，不需要改前端代码。

存储布局（宿主磁盘）::

    <CONTEXT_ROOT>/<user_id>/<file_id>/<原始文件名>
    <CONTEXT_ROOT>/<user_id>/<file_id>/meta.json
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import os
import re
import shutil
import socket
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote, urlparse

import httpx
from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from agent.config import CONTEXT_ROOT
from agent.tools.document_tools import extract_docx_text


router = APIRouter(prefix="/context", tags=["context"])


# ============================================================
# 支持格式（唯一配置源）
# ============================================================
# 新增格式时只需在这里加一行；前端上传选择框会自动跟随。
SUPPORTED_FORMATS: dict[str, tuple[str, str]] = {
    # 图片：以多模态 image_url 块随消息发送
    ".png": ("image", "image/png"),
    ".jpg": ("image", "image/jpeg"),
    ".jpeg": ("image", "image/jpeg"),
    ".webp": ("image", "image/webp"),
    ".gif": ("image", "image/gif"),
    # 文档：服务端抽取文本后注入上下文
    ".docx": ("document", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ".pdf": ("document", "application/pdf"),
    # 文本：直接以文本块注入
    ".txt": ("text", "text/plain"),
    ".md": ("text", "text/markdown"),
    ".markdown": ("text", "text/markdown"),
    ".csv": ("text", "text/csv"),
    ".json": ("text", "application/json"),
    ".yaml": ("text", "application/yaml"),
    ".yml": ("text", "application/yaml"),
    ".log": ("text", "text/plain"),
    ".xml": ("text", "application/xml"),
    ".html": ("text", "text/html"),
    ".htm": ("text", "text/html"),
    ".py": ("text", "text/x-python"),
    ".js": ("text", "text/javascript"),
    ".ts": ("text", "text/typescript"),
    ".java": ("text", "text/x-java"),
    ".sql": ("text", "text/plain"),
    ".ini": ("text", "text/plain"),
    ".toml": ("text", "text/plain"),
}
# 前端 <input accept> 用的扩展名串
ACCEPT_ATTRIBUTE = ",".join(sorted(SUPPORTED_FORMATS))

MAX_FILE_MB = int(os.getenv("HARNESS_CONTEXT_MAX_MB", "20"))
MAX_FILES = int(os.getenv("HARNESS_CONTEXT_MAX_FILES", "6"))
# 单个文本/文档注入到提示词的最大字符数，避免上下文爆炸
MAX_INLINE_CHARS = int(os.getenv("HARNESS_CONTEXT_MAX_CHARS", "30000"))
# Word 文档先注入一段预览，完整内容交给 read_word_document 工具按需读取
DOCX_PREVIEW_CHARS = 4000

_TMP = "meta.json"

# 网络链接导入时，按响应 Content-Type 推断扩展名
_CONTENT_TYPE_EXT: dict[str, str] = {
    "text/plain": ".txt",
    "text/markdown": ".md",
    "text/csv": ".csv",
    "text/html": ".html",
    "application/json": ".json",
    "application/xml": ".xml",
    "text/xml": ".xml",
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

# URL 导入的超时与跳转上限
_URL_TIMEOUT_SECONDS = 20.0
_URL_MAX_REDIRECTS = 3


# ============================================================
# 数据结构
# ============================================================


@dataclass(frozen=True)
class Attachment:
    """一次聊天中引用的一条已上传文件。"""

    id: str
    name: str
    ext: str
    kind: Literal["image", "document", "text"]
    size: int
    mime: str
    path: Path


# ============================================================
# 路径与校验
# ============================================================


def _safe_user(user_id: str) -> str:
    """用户目录名只保留安全字符，避免路径穿越。"""
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", user_id)[:64]
    return cleaned or "anonymous"


def _safe_name(name: str) -> str:
    """去掉文件名里的路径分隔符和控制字符。"""
    base = Path(name.replace("\\", "/")).name
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", base).strip(" .")
    return cleaned[:120] or "file"


def _user_dir(user_id: str) -> Path:
    return CONTEXT_ROOT / _safe_user(user_id)


def _read_meta(folder: Path) -> dict[str, Any] | None:
    meta_file = folder / _TMP
    if not meta_file.is_file():
        return None
    try:
        return json.loads(meta_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _to_attachment(user_id: str, file_id: str) -> Attachment:
    folder = _user_dir(user_id) / file_id
    meta = _read_meta(folder)
    target = folder / str(meta.get("name", "")) if meta else None
    if meta is None or target is None or not target.is_file():
        raise HTTPException(status_code=400, detail=f"附件不存在或已过期: {file_id}")
    return Attachment(
        id=file_id,
        name=str(meta.get("name", target.name)),
        ext=str(meta.get("ext", target.suffix)),
        kind=str(meta.get("kind", "text")),  # type: ignore[arg-type]
        size=int(meta.get("size", target.stat().st_size)),
        mime=str(meta.get("mime", "application/octet-stream")),
        path=target,
    )


def resolve_attachments(user_id: str, file_ids: list[str]) -> list[Attachment]:
    """把前端传来的附件 ID 还原成磁盘文件；数量超限或 ID 非法时报 400。"""
    if len(file_ids) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"一次最多附带 {MAX_FILES} 个文件")
    attachments: list[Attachment] = []
    seen: set[str] = set()
    for file_id in file_ids:
        if file_id in seen:
            continue
        seen.add(file_id)
        if not re.fullmatch(r"[0-9a-f]{32}", file_id):
            raise HTTPException(status_code=400, detail=f"非法附件 ID: {file_id}")
        attachments.append(_to_attachment(user_id, file_id))
    return attachments


# ============================================================
# 文本抽取
# ============================================================


def _extract_text(att: Attachment) -> str:
    """按文件类型抽取纯文本。"""
    if att.ext == ".docx":
        text = extract_docx_text(att.path)
    elif att.ext == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(att.path))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
    else:
        text = att.path.read_text(encoding="utf-8", errors="replace")

    text = text.strip()
    if len(text) > MAX_INLINE_CHARS:
        text = text[:MAX_INLINE_CHARS] + f"\n[内容已截断，共 {len(text)} 字符]"
    return text


def _image_block(att: Attachment) -> dict[str, Any]:
    """把图片编码成 OpenAI 兼容的 image_url 块。"""
    payload = base64.b64encode(att.path.read_bytes()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{att.mime};base64,{payload}"}}


def _docx_preview(att: Attachment) -> str:
    """Word 只注入预览，完整内容由 read_word_document 工具读取。"""
    text = extract_docx_text(att.path).strip()
    if len(text) > DOCX_PREVIEW_CHARS:
        return text[:DOCX_PREVIEW_CHARS] + "\n[预览结束，完整内容请调用 read_word_document]"
    return text


def _build_content(user_id: str, message: str, file_ids: list[str]) -> str | list[dict[str, Any]]:
    """组装最终发送给模型的用户消息内容。"""
    attachments = resolve_attachments(user_id, file_ids)
    if not attachments:
        return message

    blocks: list[dict[str, Any]] = [{"type": "text", "text": message}]
    notes: list[str] = []

    for att in attachments:
        try:
            if att.kind == "image":
                blocks.append(_image_block(att))
                notes.append(f"- {att.name}：已作为图片随消息提供")
            elif att.ext == ".docx":
                preview = _docx_preview(att)
                blocks.append({
                    "type": "text",
                    "text": f"\n\n[附件 {att.name} 预览]\n{preview}\n[预览结束]",
                })
                notes.append(
                    f"- {att.name}：host_path={att.path}，可用 read_word_document 读取全文"
                )
            else:
                blocks.append({
                    "type": "text",
                    "text": f"\n\n[附件 {att.name}]\n{_extract_text(att)}\n[附件结束]",
                })
                notes.append(f"- {att.name}：文本已内联")
        except Exception as exc:
            raise HTTPException(
                status_code=415,
                detail=f"无法解析附件 {att.name}: {type(exc).__name__}",
            ) from exc

    blocks.append({
        "type": "text",
        "text": "\n\n[附件元信息]\n" + "\n".join(notes) + "\n[/附件元信息]",
    })
    return blocks


async def build_user_content(
    *, user_id: str, message: str, attachment_ids: list[str]
) -> str | list[dict[str, Any]]:
    """异步入口：文件解析和图片编码都在线程里做。"""
    if not attachment_ids:
        return message
    return await asyncio.to_thread(_build_content, user_id, message, attachment_ids)


# ============================================================
# 写入与网络导入
# ============================================================


def _persist_bytes(
    user_id: str,
    filename: str,
    data: bytes,
    *,
    original_name: str | None = None,
) -> dict[str, Any]:
    """把字节写入上下文目录，返回前端需要的附件描述。"""
    name = _safe_name(filename)
    ext = Path(name).suffix.lower()
    format_info = SUPPORTED_FORMATS.get(ext)
    if format_info is None:
        raise HTTPException(
            status_code=415,
            detail=f"不支持的文件类型 {ext or '(无扩展名)'}",
        )
    kind, mime = format_info

    file_id = uuid.uuid4().hex
    folder = _user_dir(user_id) / file_id
    folder.mkdir(parents=True, exist_ok=True)
    try:
        (folder / name).write_bytes(data)
        meta = {
            "name": name,
            "ext": ext,
            "kind": kind,
            "mime": mime,
            "size": len(data),
            "original_name": original_name,
        }
        (folder / _TMP).write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8"
        )
    except OSError as exc:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"保存文件失败: {exc}") from exc

    return {
        "id": file_id,
        "name": name,
        "ext": ext,
        "kind": kind,
        "mime": mime,
        "size": len(data),
    }


def _assert_public_url(url: str) -> None:
    """拒绝非 http(s) 和内网地址，降低 SSRF 风险。"""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=400, detail="只支持 http/https 链接")
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as exc:
        raise HTTPException(
            status_code=400, detail=f"无法解析域名: {parsed.hostname}"
        ) from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        ):
            raise HTTPException(status_code=400, detail="不允许访问内网或本机地址")


def _guess_filename(url: str, content_type: str | None) -> str:
    """优先用链接里的文件名，其次按 Content-Type 推断扩展名。"""
    path_name = unquote(Path(urlparse(url).path).name)
    if path_name and Path(path_name).suffix.lower() in SUPPORTED_FORMATS:
        return path_name
    ctype = (content_type or "").split(";")[0].strip().lower()
    ext = _CONTENT_TYPE_EXT.get(ctype, "")
    stem = Path(path_name).stem if path_name else "web-context"
    if ext:
        return f"{stem}{ext}"
    raise HTTPException(
        status_code=415,
        detail=f"无法识别链接内容类型: {ctype or '(未知)'}",
    )


async def _download_url(url: str) -> tuple[bytes, str | None]:
    """手动跟随跳转，每一跳都重新做内网校验。"""
    limit = MAX_FILE_MB * 1024 * 1024
    current = url
    async with httpx.AsyncClient(
        follow_redirects=False,
        timeout=_URL_TIMEOUT_SECONDS,
    ) as client:
        for _ in range(_URL_MAX_REDIRECTS + 1):
            _assert_public_url(current)
            async with client.stream("GET", current) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise HTTPException(status_code=502, detail="跳转缺少 Location")
                    current = str(response.url.join(location))
                    continue
                if response.status_code >= 400:
                    raise HTTPException(
                        status_code=502, detail=f"链接返回 {response.status_code}"
                    )
                content_type = response.headers.get("content-type")
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > limit:
                        raise HTTPException(
                            status_code=413,
                            detail=f"链接内容超过 {MAX_FILE_MB} MB 限制",
                        )
                    chunks.append(chunk)
                return b"".join(chunks), content_type
    raise HTTPException(status_code=502, detail="跳转次数超过上限")


# ============================================================
# HTTP 接口
# ============================================================


@router.get("/formats")
async def list_formats() -> dict[str, Any]:
    """告诉前端允许上传哪些扩展名、单文件大小与数量上限。"""
    groups: dict[str, list[str]] = {"image": [], "document": [], "text": []}
    for ext, (kind, _) in SUPPORTED_FORMATS.items():
        groups[kind].append(ext)
    return {
        "accept": ACCEPT_ATTRIBUTE,
        "max_files": MAX_FILES,
        "max_file_mb": MAX_FILE_MB,
        "groups": groups,
    }


@router.post("/upload")
async def upload_context_file(
    file: UploadFile = File(...),
    user_id: str = Query(..., min_length=1),
) -> dict[str, Any]:
    """保存一个上传文件；超过大小或类型不支持时直接拒绝。"""
    limit = MAX_FILE_MB * 1024 * 1024
    chunks: list[bytes] = []
    size = 0
    while chunk := await file.read(1024 * 1024):
        size += len(chunk)
        if size > limit:
            raise HTTPException(
                status_code=413,
                detail=f"文件超过 {MAX_FILE_MB} MB 限制",
            )
        chunks.append(chunk)
    return _persist_bytes(
        user_id,
        file.filename or "file",
        b"".join(chunks),
        original_name=file.filename,
    )


class UrlImportRequest(BaseModel):
    """把一个公网链接抓取为上下文附件。"""

    url: str = Field(..., min_length=8, description="http/https 链接")
    user_id: str = Field(..., min_length=1, description="用户唯一标识")


@router.post("/url")
async def import_context_url(request: UrlImportRequest) -> dict[str, Any]:
    """抓取公网链接并保存为上下文附件。

    只允许 http/https 公网地址；每一跳跳转都会重新校验，
    防止通过跳转访问内网（基础 SSRF 防护）。
    """
    data, content_type = await _download_url(request.url)
    name = _guess_filename(request.url, content_type)
    return _persist_bytes(
        request.user_id,
        name,
        data,
        original_name=request.url,
    )


@router.delete("/{file_id}")
async def delete_context_file(file_id: str, user_id: str = Query(..., min_length=1)) -> dict[str, bool]:
    """删除尚未发送的附件，避免占用磁盘。"""
    if not re.fullmatch(r"[0-9a-f]{32}", file_id):
        raise HTTPException(status_code=400, detail="非法附件 ID")
    folder = _user_dir(user_id) / file_id
    if folder.is_dir():
        shutil.rmtree(folder, ignore_errors=True)
    return {"ok": True}
