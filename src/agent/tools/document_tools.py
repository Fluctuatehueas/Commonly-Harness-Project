"""宿主侧 Word 文档读取工具。

这个工具演示"手动新增一个工具"的最小步骤：
1. 用 ``@tool`` 装饰一个函数，函数名就是模型看到的工具名；
2. 在 ``main_agent.py`` 的 ``main_tools`` 里注册；
3. 需要配套技能时，在 ``skills/main/<name>/`` 下写 SKILL.md。

工具直接在宿主进程执行，不依赖沙箱；只允许读取前端上传到
``CONTEXT_ROOT`` 下的文件，避免模型访问任意宿主路径。

返回值统一是 JSON 字符串，字段与 ``document_reader`` 子 Agent 的约定一致：
``{ok, mode, doc, structure, content, warnings, error, notes}``。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from agent.config import CONTEXT_ROOT


MAX_FILE_MB = 50
DEFAULT_MAX_CHARS = 4000
VALID_MODES = {"meta", "structure", "summary", "text", "range"}


def _error(code: str, message: str, *, mode: str | None = None) -> str:
    """构造与子 Agent 约定一致的错误 JSON。"""
    payload: dict[str, Any] = {
        "ok": False,
        "mode": mode,
        "doc": None,
        "structure": None,
        "content": None,
        "warnings": [],
        "error": {"code": code, "message": message},
        "notes": None,
    }
    return json.dumps(payload, ensure_ascii=False)


def _resolve_path(raw_path: str) -> Path:
    """把模型给的路径限制在上下文目录内。"""
    root = CONTEXT_ROOT.resolve()
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = root / raw_path.lstrip("/")
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            "只能读取用户上传到上下文目录的文件；请使用附件清单里给出的 host_path"
        ) from exc
    return resolved


def _heading_level(style_name: str) -> int | None:
    """从 Word 样式名推断标题层级，识别中英文两种写法。"""
    lowered = style_name.lower()
    if not (lowered.startswith("heading") or style_name.startswith("标题")):
        return None
    digits = "".join(char for char in style_name if char.isdigit())
    return int(digits) if digits else 1


def _paragraph_texts(document: Any) -> list[str]:
    return [paragraph.text for paragraph in document.paragraphs]


def extract_docx_text(path: Path) -> str:
    """给上下文预览用的纯文本提取（段落 + 表格），不含结构信息。"""
    from docx import Document

    document = Document(str(path))
    parts = [text.strip() for text in _paragraph_texts(document) if text.strip()]
    for index, table in enumerate(document.tables, start=1):
        rows = [
            " | ".join(cell.text.strip() for cell in row.cells)
            for row in table.rows
        ]
        parts.append(f"[表格 {index}]\n" + "\n".join(rows))
    return "\n\n".join(parts)


def _structure(document: Any, paragraphs: list[str]) -> dict[str, Any]:
    """提取标题树、表格清单与图片数量。"""
    headings: list[dict[str, Any]] = []
    for index, paragraph in enumerate(document.paragraphs):
        style_name = getattr(getattr(paragraph, "style", None), "name", "") or ""
        level = _heading_level(style_name)
        if level is not None and paragraph.text.strip():
            headings.append(
                {
                    "level": level,
                    "text": paragraph.text.strip(),
                    "paragraph_index": index,
                }
            )

    tables: list[dict[str, Any]] = []
    for index, table in enumerate(document.tables, start=1):
        header = (
            [cell.text.strip() for cell in table.rows[0].cells] if table.rows else []
        )
        tables.append(
            {
                "index": index,
                "rows": len(table.rows),
                "cols": len(table.columns),
                "header": header,
            }
        )

    return {
        "headings": headings,
        "tables": tables,
        "image_count": len(document.inline_shapes),
        "paragraph_count": sum(1 for text in paragraphs if text.strip()),
    }


def _doc_meta(path: Path, document: Any, paragraphs: list[str], headings: list[dict[str, Any]]) -> dict[str, Any]:
    """汇总文档元信息；缺失字段填 null 而不是编造。"""
    props = document.core_properties

    def as_iso(value: Any) -> str | None:
        try:
            return value.isoformat() if value else None
        except (AttributeError, ValueError):
            return None

    return {
        "path": str(path),
        "format": path.suffix.lower().lstrip("."),
        "size_bytes": path.stat().st_size,
        "title": props.title or (headings[0]["text"] if headings else path.stem),
        "author": props.author or None,
        "created_at": as_iso(props.created),
        "modified_at": as_iso(props.modified),
        "paragraph_count": sum(1 for text in paragraphs if text.strip()),
        "encrypted": False,
    }


def _build_payload(
    path: Path,
    *,
    mode: str,
    max_chars: int,
    from_paragraph: int | None,
    to_paragraph: int | None,
) -> str:
    """解析文档并按 mode 组装返回 JSON。"""
    from docx import Document

    document = Document(str(path))
    paragraphs = _paragraph_texts(document)
    structure = _structure(document, paragraphs)
    headings = structure["headings"]
    doc_meta = _doc_meta(path, document, paragraphs, headings)

    warnings: list[str] = []
    if structure["image_count"]:
        warnings.append(
            f"文档包含 {structure['image_count']} 张内联图片，仅统计数量，未提取图片内容"
        )

    text: str | None = None
    truncated = False
    covered: dict[str, Any] | None = None

    if mode in {"summary", "text"}:
        body = "\n".join(item for item in paragraphs if item.strip())
        text = body[:max_chars]
        truncated = len(body) > max_chars
        covered = {
            "from_paragraph": 0,
            "to_paragraph": len(paragraphs) - 1 if paragraphs else 0,
            "char_count": len(text),
        }
        if truncated:
            warnings.append(f"正文超过 {max_chars} 字符，已截断")
    elif mode == "range":
        if from_paragraph is None and to_paragraph is None:
            return json.dumps(
                {
                    "ok": False,
                    "mode": mode,
                    "doc": doc_meta,
                    "structure": structure,
                    "content": None,
                    "warnings": warnings,
                    "error": {
                        "code": "MISSING_RANGE",
                        "message": "mode=range 时必须提供 from_paragraph 与 to_paragraph",
                    },
                    "notes": None,
                },
                ensure_ascii=False,
            )
        start = max(0, from_paragraph if from_paragraph is not None else 0)
        end = to_paragraph if to_paragraph is not None else len(paragraphs) - 1
        end = min(end, len(paragraphs) - 1)
        if start > end:
            return _error(
                "INVALID_RANGE",
                f"区间非法：from_paragraph={start} 大于 to_paragraph={end}",
                mode=mode,
            )
        selected = [item for item in paragraphs[start : end + 1] if item.strip()]
        body = "\n".join(selected)
        text = body[:max_chars]
        truncated = len(body) > max_chars
        covered = {
            "from_paragraph": start,
            "to_paragraph": end,
            "char_count": len(text),
        }
        if truncated:
            warnings.append(f"区间正文超过 {max_chars} 字符，已截断")

    payload = {
        "ok": True,
        "mode": mode,
        "doc": doc_meta,
        # meta 模式只返回元信息；structure 与其余模式都返回结构概览。
        "structure": None if mode == "meta" else structure,
        "content": {
            "mode": mode,
            "text": text,
            "truncated": truncated,
            "covered": covered,
        },
        "warnings": warnings,
        "error": None,
        "notes": None,
    }
    return json.dumps(payload, ensure_ascii=False)


@tool
async def read_word_document(
    path: str,
    mode: str = "summary",
    max_chars: int = DEFAULT_MAX_CHARS,
    from_paragraph: int | None = None,
    to_paragraph: int | None = None,
) -> str:
    """读取用户上传的 Word (.docx) 文档，返回 JSON 字符串。

    Args:
        path: 附件清单里的 host_path，必须以 .docx 结尾。
        mode: meta / structure / summary / text / range 之一，默认 summary。
        max_chars: summary、text、range 模式返回正文的最大字符数。
        from_paragraph: mode=range 时的起始段落索引（含）。
        to_paragraph: mode=range 时的结束段落索引（含）。
    """
    if not path or not path.strip():
        return _error("MISSING_PATH", "未提供文档路径")
    if mode not in VALID_MODES:
        return _error("INVALID_MODE", f"不支持的 mode: {mode}", mode=mode)

    try:
        target = _resolve_path(path)
    except ValueError as exc:
        # 返回而不是抛异常：模型能读到原因并自行改用正确路径。
        return _error("PATH_NOT_ALLOWED", str(exc), mode=mode)

    if target.suffix.lower() != ".docx":
        return _error(
            "UNSUPPORTED_FORMAT",
            f"只支持 .docx，当前为 {target.suffix or '(无扩展名)'}",
            mode=mode,
        )
    if not target.is_file():
        return _error("FILE_NOT_FOUND", f"文件不存在: {target.name}", mode=mode)
    if target.stat().st_size > MAX_FILE_MB * 1024 * 1024:
        return _error(
            "TOO_LARGE",
            f"文档超过 {MAX_FILE_MB} MB，请改用 mode=range 分段读取",
            mode=mode,
        )

    # 解析可能耗时，放到线程里避免阻塞事件循环。
    try:
        return await asyncio.to_thread(
            _build_payload,
            target,
            mode=mode,
            max_chars=max(1, int(max_chars)),
            from_paragraph=from_paragraph,
            to_paragraph=to_paragraph,
        )
    except Exception as exc:  # 损坏的 docx / 缺少依赖
        return _error(
            "CORRUPTED",
            f"解析 Word 失败: {type(exc).__name__}: {exc}",
            mode=mode,
        )
