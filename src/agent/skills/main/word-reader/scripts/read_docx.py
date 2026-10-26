#!/usr/bin/env python3
"""不依赖第三方库的 .docx 文本提取器，供沙箱内 `python3 -I` 执行。

用法：
    python3 -I read_docx.py /path/to/file.docx
    python3 -I read_docx.py /path/to/file.docx --json --max-chars 20000
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path


WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def paragraph_text(paragraph: ET.Element) -> str:
    """拼接段落内所有 w:t 文本节点。"""
    return "".join(node.text or "" for node in paragraph.iter(f"{WORD}t")).strip()


def table_rows(table: ET.Element) -> list[str]:
    """按行拼接单元格文本。"""
    rows: list[str] = []
    for row in table.findall(f"{WORD}tr"):
        cells = [
            " ".join(
                paragraph_text(paragraph)
                for paragraph in cell.findall(f"{WORD}p")
            ).strip()
            for cell in row.findall(f"{WORD}tc")
        ]
        rows.append(" | ".join(cells))
    return rows


def extract(path: Path) -> dict[str, object]:
    """返回 {"paragraphs": [...], "tables": [[...]]}。"""
    with zipfile.ZipFile(path) as archive:
        with archive.open("word/document.xml") as stream:
            root = ET.parse(stream).getroot()

    body = root.find(f"{WORD}body")
    if body is None:
        return {"paragraphs": [], "tables": []}

    paragraphs: list[str] = []
    tables: list[list[str]] = []
    for child in list(body):
        if child.tag == f"{WORD}p":
            text = paragraph_text(child)
            if text:
                paragraphs.append(text)
        elif child.tag == f"{WORD}tbl":
            tables.append(table_rows(child))
    return {"paragraphs": paragraphs, "tables": tables}


def to_text(data: dict[str, object]) -> str:
    """把结构化结果拼成可读文本。"""
    blocks = list(data["paragraphs"])  # type: ignore[arg-type]
    for index, table in enumerate(data["tables"], start=1):  # type: ignore[arg-type]
        blocks.append(f"[表格 {index}]\n" + "\n".join(table))
    return "\n\n".join(blocks)


def main() -> int:
    # 沙箱/Windows 控制台编码不一致时强制 UTF-8，避免中文乱码或被截断。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="提取 .docx 文本")
    parser.add_argument("path", help=".docx 文件路径")
    parser.add_argument("--json", action="store_true", help="输出结构化 JSON")
    parser.add_argument("--max-chars", type=int, default=60000, help="最大输出字符数")
    args = parser.parse_args()

    path = Path(args.path)
    if not path.is_file():
        print(f"文件不存在: {path}", file=sys.stderr)
        return 2
    if path.suffix.lower() != ".docx":
        print(f"只支持 .docx，当前为 {path.suffix}", file=sys.stderr)
        return 2

    try:
        data = extract(path)
    except (zipfile.BadZipFile, ET.ParseError) as exc:
        print(f"无法解析 docx: {exc}", file=sys.stderr)
        return 3

    if args.json:
        output = json.dumps(data, ensure_ascii=False)
    else:
        output = to_text(data)

    truncated = len(output) > args.max_chars
    print(output[: args.max_chars])
    if truncated:
        print(f"\n[内容已截断，共 {len(output)} 字符]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
