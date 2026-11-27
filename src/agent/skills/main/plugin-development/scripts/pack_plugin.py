"""把插件目录打包成可安装 ZIP，并输出 sha256。

用法：
    python scripts/pack_plugin.py <plugin_dir> -o dist/<plugin_id>-<version>.zip
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

REQUIRED_FIELDS = (
    "plugin_id",
    "name",
    "version",
    "protocolVersion",
    "author",
    "capabilities",
    "runtime",
    "transport",
    "permissions",
    "harnessVersion",
)
SKIP_DIRS = {"__pycache__", ".git", "dist", "node_modules"}
SKIP_SUFFIXES = {".pyc", ".pyo"}


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def validate_manifest(plugin_dir: Path) -> dict:
    manifest_path = plugin_dir / "manifest.json"
    if not manifest_path.is_file():
        fail(f"缺少 manifest.json: {plugin_dir}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"manifest.json 读取失败: {error}")
    if not isinstance(manifest, dict):
        fail("manifest.json 必须是对象")
    missing = [field for field in REQUIRED_FIELDS if field not in manifest]
    if missing:
        fail(f"manifest 缺少字段: {missing}")
    if not isinstance(manifest.get("author"), dict) or not manifest["author"].get("name"):
        fail("manifest.author.name 必填")
    runtime = manifest.get("runtime")
    if not isinstance(runtime, dict):
        fail("manifest.runtime 必须是对象")
    kind = runtime.get("kind")
    if kind == "subprocess" and not runtime.get("command"):
        fail("subprocess 插件缺少 runtime.command")
    if kind == "inprocess" and not runtime.get("entry"):
        fail("inprocess 插件缺少 runtime.entry")
    return manifest


def collect_files(plugin_dir: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(plugin_dir.rglob("*")):
        if path.is_dir():
            if path.name in SKIP_DIRS:
                continue
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(plugin_dir).parts):
            continue
        if path.suffix in SKIP_SUFFIXES:
            continue
        if path.is_symlink():
            fail(f"包内不允许符号链接: {path}")
        files.append(path)
    if not files:
        fail("插件目录为空")
    return files


def main() -> None:
    parser = argparse.ArgumentParser(description="打包 Harness 插件")
    parser.add_argument("plugin_dir", help="插件目录（包含 manifest.json）")
    parser.add_argument("-o", "--output", required=True, help="输出 ZIP 路径")
    args = parser.parse_args()

    plugin_dir = Path(args.plugin_dir).resolve()
    if not plugin_dir.is_dir():
        fail(f"插件目录不存在: {plugin_dir}")
    manifest = validate_manifest(plugin_dir)
    files = collect_files(plugin_dir)

    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(plugin_dir).as_posix())

    data = output.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    summary = {
        "output": str(output),
        "size": len(data),
        "sha256": digest,
        "plugin_id": manifest.get("plugin_id"),
        "version": manifest.get("version"),
        "files": len(files),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
