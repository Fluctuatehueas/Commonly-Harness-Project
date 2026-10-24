"""可信沙箱事务入口；只运行管理代码，技能内容始终作为数据处理。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys

import skill_management as management

ROOT = Path("/skills")
MAX_BYTES = 32 * 1024 * 1024
MAX_FILES = 2048


def safe_path(path: Path) -> Path:
    """
    拒绝路径任意层级的链接，防止复制或回滚越界。
    """
    path.relative_to(ROOT) # 返回path相对于ROOT的相对路径
    if ".." in path.parts: # 有可能会出现 ../ROOT/*，这种情况，因此需要做校验
        raise ValueError("技能路径不能包含上级目录")
    for item in [path, *path.parents]:
        if item.is_symlink():
            raise ValueError("技能路径不能包含符号链接")
        if item == ROOT:
            break
    return path

# 没有考虑到对于权限不足的目录不能遍历到的问题
def manifest(directory: Path) -> list[dict]:
    """
    限制导出大小并记录摘要，控制面下载后重新核验。
    """
    safe_path(directory) # 校验根目录是否合法，不合法报错
    result = []
    size = 0
    entries = 0 # 沙箱返回文件数量
    # os.walk(): yield 一个三元组 (dirpath:长在便利的目录字符串路径, dirnames：子目录列表, filenames：文件名列表)
    for parent, directories, files in os.walk(directory, followlinks=False): # 遇到符号链接不进入，但是walk仍会将符号链接检索出来
        entries += len(directories) + len(files) # 记录该目录下的实体数量
        if entries > MAX_FILES:
            raise ValueError("技能目录项超过限制")
        for name in directories + files:
            path = safe_path(Path(parent) / name) # 最当前目录下的每一个子目录做安全校验
            # st_mode: 文件模式，一个整数，包含文件类型（高 4 位）+ 权限位（低 12 位）
            mode = path.lstat().st_mode # 不跟随符号链接，返回链接本身的信息。
            if not stat.S_ISDIR(mode) and not stat.S_ISREG(mode): # 如果不是目录或者普通文件，就报错
                raise ValueError("技能只能包含普通文件和目录")
        for name in files: # 做文件大小校验
            path = Path(parent) / name
            size += path.stat().st_size # 上面用了lstat，所以这里用stat一样。
            if size > MAX_BYTES or len(result) >= MAX_FILES:
                raise ValueError("技能导出超过文件数或大小限制")
            # 记录为一个list(dict)
            result.append({"path": path.relative_to(directory).as_posix(),
                           "size": path.stat().st_size,
                           "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return result


def restore(transaction: Path, state: dict) -> None:
    """
    按持久事务记录恢复原目录；恢复失败时保留备份供重试。
    param:
        transaction : 
        state : 
    """
    for index, relative in  (state["paths"]):
        destination = safe_path(ROOT / relative)
        if destination.exists():
            manifest(destination)
            shutil.rmtree(destination)
        backup = transaction / str(index)
        if backup.exists():
            shutil.copytree(backup, destination)
    shutil.rmtree(transaction)


def dispatch(request: dict) -> dict:
    """
    执行一次下载、查询或可回滚变更；调用者负责最终提交。
    1. token校验
    2. Subagent名称校验，并创建对应skill目录
    3. 事务状态分支流转：
        rollback有两个幂等的判断：
            如果事务不存在，或被release了，不报错，仍安全回滚
            如果事务存在，那就获取state.json的快照，进行回滚
        release：删除事务
        finish：宿主持久化成功，返回ok
    """
    operation = request["operation"]
    # 1.每个子 Agent 名过 _skill_directory（名字白名单正则 + 不是 skill-management）和 safe_path。
    if "token" in request:
        token = request["token"]
        if len(token) != 32 or any(char not in "0123456789abcdef" for char in token):
            raise ValueError("无效事务标识")
    names = set(request["names"])
    # 2. 子agent进行names校验，names是子智能体的名字列表
    for name in names:
        management._skill_directory(ROOT, name)
        safe_path(ROOT / "subagents" / name) # 校验子智能体路径
    safe_path(ROOT / "main") # 校验主智能体路径
    management._ensure_skill_directories(ROOT, names) # 路径安全性校验通过后，宿主机创建skills/subagent/{name}目录
    # 3
    if operation in {"rollback", "finish", "release"}:
        token = request["token"]
        transaction = safe_path(ROOT / ".skill-transactions" / token)
        if operation == "rollback" and not transaction.exists():
            return {"ok": True}
        state = json.loads((transaction / "state.json").read_text())
        if operation == "rollback":
            restore(transaction, state)
        elif operation == "release":
            shutil.rmtree(transaction)
        # finish 只确认事务仍可提交；收到确认前保留备份，以便响应丢失时回滚。
        return {"ok": True}
    
    target = request.get("target") # target：目标智能体
    if operation == "list":
        selected = [target] if target else sorted(names) # 如果没指定，那就是全部子智能体
        for name in selected:
            management._validate_target_name(name, names)
            # 选择是那个智能体的skill目录
            directory = ROOT / "main" if name == "main" else ROOT / "subagents" / name
            for child in directory.iterdir():
                safe_path(child)
                if child.is_dir():
                    manifest(child)
        return management._list_subagent_skills(ROOT, target, names) # 列出子智能体的所有skill
    
    skill = request.get("skill")
    if operation in {"download", "update"}:
        skill, _, _ = management._resolve_skill_source(request["url"])
    source = safe_path(management._skill_directory(ROOT / "main", skill)) # 宿主机的主agent的技能目录
    
    # 因为下载只是往沙箱暂存区写（避免出现恶意代码），还没持久化到宿主。
    # 如果下载失败，_download_skill 自己会清理临时目录。只有 assign 才建事务并持久化。
    if operation == "download": # 如果是下载skill的话，就从请求的url中下载
        return management._download_skill(ROOT, request["url"])

    #-----------
    # 否则就是更新skill，需要将宿主的skill移动一份到
    management._validate_target_name(target, names)
    destination = source if target == "main" else safe_path(
        management._skill_directory(ROOT / "subagents" / target, skill))
    paths = list(dict.fromkeys([source, destination] if operation != "delete" else [destination]))
    for path in paths:
        if path.exists():
            manifest(path)
    transaction = safe_path(ROOT / ".skill-transactions" / request["token"])
    transaction.mkdir(parents=True, exist_ok=False)
    state = {"paths": [path.relative_to(ROOT).as_posix() for path in paths]}
    for index, path in enumerate(paths):
        if path.exists():
            shutil.copytree(path, transaction / str(index))
    (transaction / "state.json").write_text(json.dumps(state))
    try:
        if operation == "assign": # 校验skill信息，移动skill至目标路径，确保skill唯一存在
            management._ensure_skill_metadata(source)
            management._assign_skill(ROOT, skill, target, names)
        elif operation == "update":
            management._update_subagent_skill(ROOT, request["url"], target, names)
        elif operation == "delete":
            management._delete_subagent_skill(ROOT, skill, target, names)
        else:
            raise ValueError("未知技能操作")
        files = manifest(destination) if operation != "delete" else []
        return {"skill": skill, "directory": destination.relative_to(ROOT).as_posix(), "files": files}
    except Exception:
        restore(transaction, state) # 回滚
        raise


if __name__ == "__main__":
    try:
        print(json.dumps({"result": dispatch(json.loads(sys.argv[1]))}, ensure_ascii=False))
    except Exception as error:
        # 原始错误可能含内部路径；控制面只展示稳定的错误类别。
        print(json.dumps({"error": type(error).__name__}))
        sys.exit(1)
