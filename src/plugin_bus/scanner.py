"""插件包静态扫描。

安装前的第一道本地闸门：检查包结构、体积、路径穿越、符号链接与危险调用。
扫描只产出报告与风险等级，不代替沙箱试运行，也不直接执行插件代码。
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from plugin_bus.errors import PluginPackageInvalid

DANGEROUS_PATTERNS: dict[str, list[tuple[str, str]]] = {
    ".py": [
        (r"\bos\.system\s*\(", "调用 os.system 执行系统命令"),
        (r"\bsubprocess\.", "调用 subprocess 创建子进程"),
        (r"\beval\s*\(", "调用 eval"),
        (r"\bexec\s*\(", "调用 exec"),
        (r"\bctypes\b", "使用 ctypes 访问原生内存"),
        (r"\bsocket\.", "直接使用 socket"),
        (r"shutil\.rmtree\s*\(", "递归删除目录"),
        (r"__import__\s*\(", "动态导入模块"),
    ],
    ".js": [
        (r"child_process", "使用 child_process 创建子进程"),
        (r"\beval\s*\(", "调用 eval"),
        (r"new\s+Function\s*\(", "动态构造函数"),
        (r"process\.env", "读取进程环境变量"),
    ],
    ".ts": [
        (r"child_process", "使用 child_process 创建子进程"),
        (r"\beval\s*\(", "调用 eval"),
        (r"process\.env", "读取进程环境变量"),
    ],
    ".sh": [
        (r"rm\s+-rf", "递归删除命令"),
        (r"curl[^|]*\|\s*(bash|sh)", "下载并直接执行脚本"),
        (r"wget[^|]*\|\s*(bash|sh)", "下载并直接执行脚本"),
    ],
    ".ps1": [
        (r"Invoke-Expression", "动态执行表达式"),
        (r"Remove-Item\s+.*-Recurse", "递归删除目录"),
    ],
}

COMPILED_SUFFIXES = {".exe", ".dll", ".so", ".dylib", ".node", ".jar", ".bin"}
SKIP_SUFFIXES = {".pyc", ".pyo"}


@dataclass
class ScanIssue:
    level: str
    code: str
    message: str
    path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "code": self.code,
            "message": self.message,
            "path": self.path,
        }


@dataclass
class ScanReport:
    files: int = 0
    size_bytes: int = 0
    issues: list[ScanIssue] = field(default_factory=list)
    binary_hashes: dict[str, str] = field(default_factory=dict)

    @property
    def risk_level(self) -> str:
        if any(issue.level == "high" for issue in self.issues):
            return "high"
        if any(issue.level == "medium" for issue in self.issues):
            return "medium"
        return "low"

    @property
    def ok(self) -> bool:
        return not any(issue.level == "high" for issue in self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "risk_level": self.risk_level,
            "files": self.files,
            "size_bytes": self.size_bytes,
            "issues": [issue.to_dict() for issue in self.issues],
            "binary_hashes": dict(self.binary_hashes),
        }


def _scan_text(path: Path, suffix: str, report: ScanReport) -> None:
    patterns = DANGEROUS_PATTERNS.get(suffix)
    if not patterns:
        return
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    # 只标记风险，不因单个命中直接拒绝；结合作者声明与沙箱行为综合判断。
    for pattern, message in patterns:
        if re.search(pattern, text):
            report.issues.append(
                ScanIssue(
                    level="medium",
                    code="DANGEROUS_CALL",
                    message=message,
                    path=str(path),
                )
            )


def scan_directory(
    root: str | Path,
    *,
    max_bytes: int = 32 * 1024 * 1024,
    max_files: int = 5000,
) -> ScanReport:
    """扫描插件目录；体积/文件数超限直接抛 ``PluginPackageInvalid``。"""
    base = Path(root)
    report = ScanReport()
    if not base.is_dir():
        raise PluginPackageInvalid(f"插件目录不存在: {base}")
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        dirnames[:] = [name for name in dirnames if name != "__pycache__"]
        for dirname in list(dirnames):
            candidate = Path(dirpath) / dirname
            if candidate.is_symlink():
                report.issues.append(
                    ScanIssue(
                        level="high",
                        code="SYMLINK",
                        message="包内不允许符号链接",
                        path=str(candidate),
                    )
                )
                dirnames.remove(dirname)
        for filename in filenames:
            path = Path(dirpath) / filename
            if path.suffix in SKIP_SUFFIXES:
                continue
            if path.is_symlink():
                report.issues.append(
                    ScanIssue(
                        level="high",
                        code="SYMLINK",
                        message="包内不允许符号链接",
                        path=str(path),
                    )
                )
                continue
            try:
                stat_result = path.stat()
            except OSError:
                continue
            report.files += 1
            report.size_bytes += stat_result.st_size
            if report.files > max_files:
                raise PluginPackageInvalid(f"包内文件过多，上限 {max_files}")
            if report.size_bytes > max_bytes:
                raise PluginPackageInvalid(
                    f"包体积超过 {max_bytes // (1024 * 1024)} MB 限制"
                )
            suffix = path.suffix.lower()
            if suffix in COMPILED_SUFFIXES:
                try:
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()
                except OSError:
                    digest = ""
                report.binary_hashes[str(path.relative_to(base))] = digest
                report.issues.append(
                    ScanIssue(
                        level="medium",
                        code="COMPILED_BINARY",
                        message="包含编译型产物，已记录哈希；需签名或沙箱试运行确认",
                        path=str(path),
                    )
                )
                continue
            _scan_text(path, suffix, report)
    return report


__all__ = ["ScanIssue", "ScanReport", "scan_directory", "DANGEROUS_PATTERNS"]
