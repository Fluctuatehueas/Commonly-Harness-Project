"""只读文件系统后端。

无沙箱模式下把宿主技能目录挂给 Agent 读取时使用：技能是 Agent 的
"知识"，不是它应该改写的文件。写入类操作统一拒绝，避免模型（或被注入的
提示词）篡改宿主上的 SKILL.md。
"""

from __future__ import annotations

from typing import Any

from deepagents.backends import FilesystemBackend


_DENIED = "技能目录为只读挂载，不允许通过文件工具修改"


class ReadOnlyFilesystemBackend(FilesystemBackend):
    """允许读取、禁止一切写入的 FilesystemBackend。"""

    def write(self, *args: Any, **kwargs: Any) -> Any:  # noqa: D102
        raise PermissionError(_DENIED)

    async def awrite(self, *args: Any, **kwargs: Any) -> Any:  # noqa: D102
        raise PermissionError(_DENIED)

    def edit(self, *args: Any, **kwargs: Any) -> Any:  # noqa: D102
        raise PermissionError(_DENIED)

    async def aedit(self, *args: Any, **kwargs: Any) -> Any:  # noqa: D102
        raise PermissionError(_DENIED)

    def delete(self, *args: Any, **kwargs: Any) -> Any:  # noqa: D102
        raise PermissionError(_DENIED)

    async def adelete(self, *args: Any, **kwargs: Any) -> Any:  # noqa: D102
        raise PermissionError(_DENIED)

    def upload_files(self, *args: Any, **kwargs: Any) -> Any:  # noqa: D102
        raise PermissionError(_DENIED)

    async def aupload_files(self, *args: Any, **kwargs: Any) -> Any:  # noqa: D102
        raise PermissionError(_DENIED)
