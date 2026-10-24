"""沙箱技能生命周期与受限本地持久化；不在宿主执行下载的技能。"""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from collections.abc import Iterator
import hashlib
from io import BytesIO
import json
import logging
from pathlib import Path, PurePosixPath, PureWindowsPath
import shlex
import shutil
import tempfile
from typing import Any
from uuid import uuid4
import zipfile

from agent.backends.skill_sync import SKILL_PERSISTENCE_LOCK

MAX_BYTES = 32 * 1024 * 1024
MAX_FILES = 2048
_PERSISTENCE_LOCK = SKILL_PERSISTENCE_LOCK
logger = logging.getLogger(__name__)

@contextmanager
def _publication_directory(root: Path) -> Iterator[Path]:
    """
    失败时保留恢复材料；暂存目录位于技能根外，避免同步器读取备份。
    """
    directory = Path(tempfile.mkdtemp(prefix=".skill-publish-", dir=root.parent))
    try:
        yield directory
    except Exception:
        raise
    else:
        try:
            shutil.rmtree(directory)
        except OSError:
            logger.warning("技能已发布，但本地事务暂存清理失败")


# 将项目内的受信任的脚本："skill_management.py", "sandbox_runtime.py"打包进沙箱去执行
# 这确保了skill的唯一归属
def _runtime_package() -> bytes:
    """
    打包可信脚本；沙箱镜像须提供 Python 3 和 PyYAML。
    """
    from agent.tools.skill_tools import SKILL_MANAGEMENT_SCRIPT

    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in ("skill_management.py", "sandbox_runtime.py"):
            archive.write(SKILL_MANAGEMENT_SCRIPT.parent / name, name)
    return stream.getvalue()

# 从沙箱拿回来的文件路径写入本地前的校验，比上一轮的 _skill_directory 更严格
def _local_path(root: Path, relative: str) -> Path:
    """
    同时验证 Windows 与 POSIX 路径，并拒绝本地目录链接。
    """
    parts = PurePosixPath(relative).parts # 确保路径是规范化的 POSIX 形式。
    if (not parts or PurePosixPath(relative).as_posix() != relative
            or PurePosixPath(relative).is_absolute() or "\\" in relative
            or ":" in relative or any(part in {".", ".."} for part in parts)
            or any(PureWindowsPath(part).is_reserved() or part.endswith((".", " ")) for part in parts)):
        raise ValueError("不安全的技能导出路径")
    destination = root.joinpath(*parts)
    for path in [destination, *destination.parents]:
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError("本地技能目录不能包含链接")
        if path == root:
            break
    return destination

class SandboxSkillManager:
    """
    串行化完整沙箱事务，并为本地发布保留可恢复副本。
    """
    def __init__(self, root: Path, names: set[str], backend: Any,
                synchronize: Any = None) -> None:
       self.root = root.absolute() # 本地技能根目录，宿主机
       self.names = names # 允许接受技能的子Agent白名单
       self.backend = backend # 沙箱后端【代理】
       self.synchronize = synchronize # 可选回调，操作前把本地技能树同步到沙箱。
       self.runtime = _runtime_package() # 可信运行包字节，构造时生成一次。

    # 沙箱代理运行函数，将沙箱内部的错误不暴露给宿主机。
    def _call(self, runtime: str, operation: str, **arguments: Any) -> dict:
          """
          仅执行固定运行器，所有外部参数通过 shell 引用后的 JSON 传入。
          """
          request = json.dumps({"operation": operation, "names": sorted(self.names), **arguments})
          code = "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('sandbox_runtime',run_name='__main__')"
          response = self.backend.execute(
              f"python3 -I -c {shlex.quote(code)} {shlex.quote(runtime)} {shlex.quote(request)}",
              timeout=120,
          )
          if response.exit_code != 0 or getattr(response, "truncated", False):
              raise RuntimeError("沙箱技能操作失败；请检查沙箱运行环境和技能格式")
          try:
              result = json.loads(response.output)
          except (ValueError, TypeError) as error:
              raise RuntimeError("沙箱返回无效的技能事务结果") from error
          if "error" in result:
              raise RuntimeError("沙箱技能校验或操作失败")
          return result["result"]

    def _export(self, result: dict, stage: Path, expected: str) -> None:
        """
        验证清单、下载结果和摘要后，再解析元信息；不导入技能代码。
        """
        from agent.tools.skill_tools import _skill_management_script as management

        if result["directory"] != expected:
            raise ValueError("沙箱导出目录与请求不一致")
        files = result["files"]
        if not files or len(files) > MAX_FILES:
            raise ValueError("技能导出文件数无效")
        seen = set()
        total = 0
        for item in files:
            relative = item["path"]
            _local_path(stage, relative)
            key = relative.casefold()
            if key in seen or not isinstance(item["size"], int) or item["size"] < 0:
                raise ValueError("技能导出清单无效")
            seen.add(key)
            total += item["size"]
        if total > MAX_BYTES:
            raise ValueError("技能导出超过大小限制")
        paths = [f"/skills/{expected}/{item['path']}" for item in files]
        responses = self.backend.download_files(paths)
        if len(responses) != len(paths):
            raise RuntimeError("沙箱文件下载不完整")
        for item, path, response in zip(files, paths, responses):
            content = response.content
            if (response.path != path or response.error or not isinstance(content, bytes)
                    or len(content) != item["size"]
                    or hashlib.sha256(content).hexdigest() != item["sha256"]):
                raise RuntimeError("沙箱文件下载失败或内容已变化")
            destination = _local_path(stage, item["path"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        management._parse_skill_metadata(stage) # skill_management.py
    # 完整沙箱技能管理状态机
    # download：宿主将新技能资源发送给沙箱，沙乡内进行下载解析。若测试成功后，沙箱保留这份技能
    # assign：宿主发送assign操作给沙箱，并附带技能信息，从沙箱内部进行下载。沙乡内部会进行备份，然后返回要下载技能
    # 宿主本地也要备份一份技能，然后将skill移动到目标智能体目录下。
    # update：宿主从url解析skill名字，然后同步地发送技能树给沙箱，沙箱内部execute dispatch
    #   沙箱内备份旧版（文件移动），然后download新的要沙箱内部skill/main目录下，然后assign到subagent/skill下
    #   如果成功，删除备份，失败的化，恢复旧版
    #   返回manifest
    #   然后宿主本地备份：把旧版skill移到temporary/old-*,然后宿主发布新版的skill到目标位置，最后finish - release 清理runtime，沙箱清理备份资源
    


    def run(self, operation: str, **arguments: Any) -> str:
       """
       完成远端变更和本地发布；任一发布步骤失败时尝试两端回滚。
       """
       from agent.tools.skill_tools import _skill_management_script as management
       with _PERSISTENCE_LOCK, (self.backend.transaction() if hasattr(self.backend, "transaction") else nullcontext()):
           token = uuid4().hex
           runtime = f"/tmp/myagent-skill-{token}.zip"
           uploads = self.backend.upload_files([(runtime, self.runtime)])
           if len(uploads) != 1 or uploads[0].path != runtime or uploads[0].error:
               raise RuntimeError("无法传入可信技能运行包")
           try:
               if operation in {"assign", "delete", "update", "list"} and self.synchronize is not None:
                   # 本地技能树是权威来源。这里只刷新 manifest 摘要发生变化的文件；
                   # 不在 manifest 中的沙箱临时技能仍需保留给下载和后续分配使用。
                   self.synchronize()
               if operation in {"download", "list"}:
                   return json.dumps(self._call(runtime, operation, **arguments), ensure_ascii=False)
               target = arguments["target"]
               management._validate_target_name(target, self.names)
               skill = arguments.get("skill")
               if operation == "update":
                   skill, _, _ = management._resolve_skill_source(arguments["url"])
               management._skill_directory(Path("."), skill)
               relative = f"main/{skill}" if target == "main" else f"subagents/{target}/{skill}"
               destination = _local_path(self.root, relative)
               source = _local_path(self.root, f"main/{skill}")
               backups = []
               published = False
               try:
                   result = self._call(runtime, operation, token=token, **arguments)
                   self.root.mkdir(parents=True, exist_ok=True)
                   with _publication_directory(self.root) as temporary:
                       stage = Path(temporary) / "new"
                       stage.mkdir()
                       if operation != "delete":
                           self._export(result, stage, relative)
                       affected = [destination]
                       if operation == "assign" and source != destination:
                           affected.append(source)
                       try:
                           for index, path in enumerate(affected):
                               if path.exists():
                                   backup = Path(temporary) / f"old-{index}"
                                   path.replace(backup)
                                   backups.append((path, backup))
                           if operation != "delete":
                               destination.parent.mkdir(parents=True, exist_ok=True)
                               stage.replace(destination)
                               published = True
                           self._call(runtime, "finish", token=token)
                       except Exception:
                           if published:
                               shutil.rmtree(destination)
                           for path, backup in reversed(backups):
                               backup.replace(path)
                           raise
               except Exception as error:
                   try:
                       self._call(runtime, "rollback", token=token)
                   except Exception as rollback_error:
                       raise RuntimeError("技能持久化失败且沙箱回滚未确认；需要恢复事务 " + token) from rollback_error
                   raise RuntimeError("技能持久化失败；沙箱已回滚") from error
               try:
                   self._call(runtime, "release", token=token)
               except Exception:
                   # 两端已确认提交，清理失败不能再撤销其中一端。
                   logger.warning("技能已发布，但沙箱事务备份清理失败")
               return json.dumps({"ok": True, "skill_name": skill, "target": target, "path": f"/skills/{relative}"}, ensure_ascii=False)
           finally:
               try:
                   response = self.backend.execute("rm -f -- " + shlex.quote(runtime))
                   if response.exit_code != 0:
                       logger.warning("沙箱可信技能运行包清理失败")
               except Exception:
                   logger.warning("沙箱可信技能运行包清理失败")


# 定义了一些列工具
# 内敛run函数->SandboxSkillManager -> skill_management.py
def create_sandbox_skill_tools(
    root: Path,
    names: set[str],
    backend: Any,
    *,
    synchronize: Any = None,
) -> list[Any]:
    """
    注册沙箱工具；LangChain 为同步工具提供线程化异步调用。
    """
    from langchain_core.tools import tool

    manager = SandboxSkillManager(root, names, backend, synchronize=synchronize)

    def run(operation: str, **arguments: Any) -> str:
        try:
            return manager.run(operation, **arguments)
        except Exception as error:
            # 不把宿主路径或底层 SDK 错误暴露给模型。
            if isinstance(error, RuntimeError) and str(error).startswith(("技能持久化失败", "沙箱")):
                return str(error)
            return "技能操作失败；请检查技能名、目标、沙箱状态及持久化权限"

    @tool
    def download_skill(skill_url: str) -> str:
        """
        在沙箱下载技能到 /skills/main/；测试通过后调用 assign_skill 持久化。
        """
        return run("download", url=skill_url)

    @tool
    def assign_skill(skill_name: str, subagent_name: str) -> str:
        """
        校验沙箱已创建或下载的技能，分配到目标后持久化；main 表示主 Agent。
        """
        return run("assign", skill=skill_name, target=subagent_name)

    @tool
    def list_subagent_skills(subagent_name: str | None = None) -> str:
        """
        从沙箱查询技能名称和描述；省略目标查询全部子 Agent，main 查询主 Agent。
        """
        return run("list", target=subagent_name)

    @tool
    def delete_subagent_skill(skill_name: str, subagent_name: str) -> str:
        """
        删除子 Agent 技能并同步本地持久副本；失败时恢复旧版本。
        """
        return run("delete", skill=skill_name, target=subagent_name)

    @tool
    def update_subagent_skill(skill_url: str, subagent_name: str) -> str:
        """
        在沙箱下载同名新版本并同步持久化；失败时恢复两端旧版本。
        """
        return run("update", url=skill_url, target=subagent_name)

    return [download_skill, assign_skill, list_subagent_skills, delete_subagent_skill, update_subagent_skill]