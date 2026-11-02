"""Windows 进程完整性级别（Integrity Level）自检。

用途：判断当前 Python 进程是否运行在低完整性上下文（例如被文件系统沙箱
降级）。低完整性进程受 no-write-up 约束，无法写入普通 Medium 目录，表现为
``WinError 5``。这里只读检测，不修改任何权限。

非 Windows 平台返回 ``unknown``。
"""

from __future__ import annotations

import ctypes
import sys
from functools import lru_cache

TOKEN_QUERY = 0x0008
_TOKEN_INTEGRITY_LEVEL = 25
_MAX_SID_SUB_AUTHORITIES = 15

_RID_TO_LEVEL = {
    0x0000: "untrusted",
    0x1000: "low",
    0x2000: "medium",
    0x3000: "high",
    0x4000: "system",
    0x5000: "protected",
}


class _SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Sid", ctypes.c_void_p),
        ("Attributes", ctypes.c_ulong),
    ]


class _TOKEN_MANDATORY_LABEL(ctypes.Structure):
    _fields_ = [("Label", _SID_AND_ATTRIBUTES)]


@lru_cache(maxsize=1)
def process_integrity() -> str:
    """返回当前进程完整性级别：medium / low / high / ...；不可知时返回 unknown。"""
    if sys.platform != "win32":
        return "unknown"
    try:
        return _windows_integrity()
    except Exception:  # noqa: BLE001 - 检测失败不能影响服务启动
        return "unknown"


@lru_cache(maxsize=1)
def is_low_integrity() -> bool:
    """当前进程是否低于 Medium（被沙箱降权，无法写入普通目录）。"""
    return process_integrity() in {"untrusted", "low"}


def integrity_hint() -> str:
    """面向用户的完整性说明；Medium 及以上返回空串。"""
    level = process_integrity()
    if level in {"untrusted", "low"}:
        return (
            f"后端进程完整性级别为 {level}，受 Windows 强制完整性策略限制，"
            "无法写入普通目录（表现为 WinError 5）。"
            "请从普通终端或由 Electron 主进程启动后端，避免在文件系统沙箱中运行。"
        )
    return ""


def _windows_integrity() -> str:
    """通过进程令牌读取完整性级别 SID 的最后一个子授权值。"""
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
    advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
    advapi32.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    advapi32.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)
    ):
        raise OSError(ctypes.get_last_error(), "OpenProcessToken 失败")

    try:
        size = wintypes.DWORD()
        advapi32.GetTokenInformation(
            token,
            _TOKEN_INTEGRITY_LEVEL,
            None,
            0,
            ctypes.byref(size),
        )
        if size.value <= 0:
            raise OSError(ctypes.get_last_error(), "无法获取令牌信息长度")
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(
            token,
            _TOKEN_INTEGRITY_LEVEL,
            buffer,
            size.value,
            ctypes.byref(size),
        ):
            raise OSError(ctypes.get_last_error(), "GetTokenInformation 失败")

        label = ctypes.cast(buffer, ctypes.POINTER(_TOKEN_MANDATORY_LABEL)).contents
        sid = label.Label.Sid
        if not sid:
            return "unknown"
        count = advapi32.GetSidSubAuthorityCount(sid).contents.value
        if count <= 0:
            return "unknown"
        rid = advapi32.GetSidSubAuthority(sid, count - 1).contents.value
        return _RID_TO_LEVEL.get(rid, f"0x{rid:x}")
    finally:
        kernel32.CloseHandle(token)
