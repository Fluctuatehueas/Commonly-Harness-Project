"""将项目维护的技能增量镜像到用户沙箱。"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
from pathlib import Path
from threading import RLock

from agent.backends.sandbox_proxy import SandboxBackendProxy

SANDBOX_SKILLS_ROOT = "/skills"
SANDBOX_MANIFEST_PATH = "/.myagent/skills-manifest.json"
# 删除旧文件时单条命令最多带多少个路径，避免命令行过长。
REMOVE_BATCH_SIZE = 50
# 被sandbox_skill_management内部和这个同步器共用一个锁
SKILL_PERSISTENCE_LOCK = RLock()

# 被sandbox_skill_management.py的skillNamagement类使用，是一个同步器
class SandboxSkillSynchronizer:
    """以本地技能目录为权威来源，同时让 Agent 只读取沙箱副本。"""

    def __init__(
        self,
        skills_roots: Path | list[Path],
        agents_file: Path,
        *,
        sandbox_prefixes: list[str] | None = None,
        excluded: list[set[str]] | None = None,
    ) -> None:
        if isinstance(skills_roots, (str, Path)):
            roots = [Path(skills_roots)]
        else:
            roots = [Path(item) for item in skills_roots]
        prefixes = list(sandbox_prefixes or ["/skills"] * len(roots))
        excludes = [set(item) for item in (excluded or [set() for _ in roots])]
        if len(prefixes) != len(roots) or len(excludes) != len(roots):
            raise ValueError("skills_roots / sandbox_prefixes / excluded 长度必须一致")
        self._roots = roots
        self._prefixes = prefixes
        self._excluded = excludes
        self._agents_file = agents_file

    def sync(self, backend: SandboxBackendProxy) -> bool:
        """只上传变更文件，并删除本地源中已经不存在的文件。

        manifest 保存在每个用户自己的沙箱中，因此开发者的修改会在该用户
        下次运行前被检测到，不依赖宿主机 mtime 或进程内缓存。
        """
        with SKILL_PERSISTENCE_LOCK, backend.transaction(): # 双重锁：第一个锁保证与技能管理事务互斥，第二个保证同步期间不允许替换沙箱
            return self._sync_locked(backend)

    @staticmethod
    def _validate_local_path(path: Path, root: Path) -> None:
        """
        拒绝符号链接、目录联接和解析后越出技能根目录的路径。
        """
        if path.is_symlink() or path.is_junction() or not path.resolve().is_relative_to(root):
            raise ValueError("技能目录包含符号链接、目录联接或越界路径，已停止同步")

    @staticmethod
    def _is_excluded(relative: str, excluded: set[str]) -> bool:
        """按 ``main:<name>`` / ``subagents/<agent>:<name>`` 判断是否跳过。"""
        if not excluded:
            return False
        parts = relative.split("/")
        if len(parts) >= 2 and parts[0] == "main":
            return f"main:{parts[1]}" in excluded
        if len(parts) >= 3 and parts[0] == "subagents":
            return f"subagents/{parts[1]}:{parts[2]}" in excluded
        return False

    # 读取本地技能目录
    def _local_files(self) -> dict[str, bytes]:
        """
        读取所有技能根目录（后面的根覆盖前面的同名文件），并拒绝链接和越界路径。
        """
        files: dict[str, bytes] = {"/AGENTS.md": self._agents_file.read_bytes()}
        for root, prefix, excluded in zip(
            self._roots, self._prefixes, self._excluded, strict=True
        ):
            if not root.is_dir():
                continue
            resolved = root.resolve()
            self._validate_local_path(root, resolved)
            # 先裁剪目录再继续遍历，防止 Windows 目录联接绕过 followlinks=False。
            for directory, directories, filenames in os.walk(root, followlinks=False):
                parent = Path(directory)
                self._validate_local_path(parent, resolved)
                for name in directories:
                    self._validate_local_path(parent / name, resolved)
                directories[:] = [
                    name for name in directories if name != "__pycache__"
                ]
                for name in filenames:
                    path = parent / name
                    self._validate_local_path(path, resolved)
                    if path.suffix == ".pyc" or not path.is_file():
                        continue
                    if name == ".builtin-version":
                        continue
                    relative = path.relative_to(root).as_posix()
                    if self._is_excluded(relative, excluded):
                        continue
                    files[f"{prefix.rstrip('/')}/{relative}"] = path.read_bytes()
        return files

    @staticmethod
    def _hash(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()


    def _read_manifest(self, backend: SandboxBackendProxy) -> dict[str, str]:
        """
        只有确认文件不存在才视为首次同步；读取失败时保留旧追踪状态。
        """
        responses = backend.download_files([SANDBOX_MANIFEST_PATH])
        if len(responses) != 1 or responses[0].path != SANDBOX_MANIFEST_PATH:
            raise RuntimeError("技能清单下载响应无效")
        response = responses[0]
        if response.error == "file_not_found":
            return {}
        if response.error or response.content is None:
            raise RuntimeError("技能清单读取失败，已停止同步以保留旧文件记录")
        try:
            value = json.loads(response.content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeError("技能清单格式损坏，已停止同步") from error
        if not isinstance(value, dict):
            raise RuntimeError("技能清单必须是 JSON 对象，已停止同步")
        for path, digest in value.items():
            # 沙箱内容不可信：禁止目录穿越，也禁止指向其他宿主机根路径。
            parts = path.split("/")
            is_skill_file = any(
                path.startswith(f"{prefix.rstrip('/')}/")
                for prefix in self._prefixes
            ) and all(part not in {"", ".", ".."} for part in parts[2:])
            if (
                (path != "/AGENTS.md" and not is_skill_file)
                or "\x00" in path
                or "\\" in path
                or not isinstance(digest, str)
            ):
                raise RuntimeError("技能清单包含非法路径或摘要")
        return value
    
    def _sync_locked(self, backend: SandboxBackendProxy) -> bool:
        """
        所有文件变更成功后，最后才提交 manifest。
        """
        files = self._local_files() # 所有技能文件
        # 清单
        manifest = {path: self._hash(content) for path, content in files.items()} # 将二进制内容进行散列计算
        previous = self._read_manifest(backend) # 读取沙箱内部的清单
        changed = [path for path, digest in manifest.items() if previous.get(path) != digest] # 寻找清单内相异的地方
        removed = sorted(set(previous) - set(manifest)) # 是"上次同步时记录在 manifest 里、现在本地已经删掉"的路径。
        if not changed and not removed: # 如果没有变更，没有本地删掉文件
            return False

        uploads = [(path, files[path]) for path in changed] # 找到变更的路径
        if uploads:
            self._upload_checked(backend, uploads) 
        # 批量删除：一次命令清一批，避免几百个旧文件逐个 rm 导致长时间刷屏。
        self._remove_paths(backend, removed)
        # manifest 是本次同步的提交标记，必须最后写入，避免失败时留下错误索引。
        self._upload_checked(backend, [
            (SANDBOX_MANIFEST_PATH, json.dumps(manifest, sort_keys=True).encode("utf-8")),
        ])
        return True

    def _remove_paths(
        self, backend: SandboxBackendProxy, removed: list[str]
    ) -> None:
        """按批删除沙箱中的旧技能文件；单批失败直接中止同步。"""
        for start in range(0, len(removed), REMOVE_BATCH_SIZE):
            batch = removed[start : start + REMOVE_BATCH_SIZE]
            command = "rm -f -- " + " ".join(shlex.quote(path) for path in batch)
            result = backend.execute(command)
            if result.exit_code != 0:
                raise RuntimeError(
                    f"无法删除沙箱中的旧技能文件: {batch[0]} 等 {len(batch)} 个"
                )
    @staticmethod
    def _upload_checked(backend: SandboxBackendProxy, files: list[tuple[str, bytes]]) -> None:
        """
        要求每个上传文件都收到且仅收到成功确认。
        """
        responses = backend.upload_files(files)
        if (
            len(responses) != len(files)
            or sorted(response.path for response in responses) != sorted(path for path, _ in files)
            or any(response.error for response in responses)
        ):
            raise RuntimeError("技能同步失败：上传响应缺失、不匹配或包含错误")
