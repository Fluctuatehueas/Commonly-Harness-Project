"""
基本工具组
"""

from __future__ import annotations
from typing import Any
import platform
import os
import subprocess
import re
import ast
import inspect
import json
import operator
import random
import types
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Callable, Union, get_args, get_origin, get_type_hints

from zoneinfo import ZoneInfo

from langchain_core.tools import tool

BASIC_TOOLS = []

def basic_tool(tool: Callable):
    """装饰器：自动将工具注册到基本工具组。"""
    BASIC_TOOLS.append(tool)
    return tool
def _safe_getattr(obj, attr, default=None):
    if obj is None:
        return default
    return getattr(obj, attr, default)


def _get_cpu_model() -> str | None:
    """跨平台获取 CPU 型号名称。"""
    system = platform.system()

    if system == "Linux":
        try:
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if "model name" in line:
                        return line.split(":", 1)[1].strip()
        except Exception:
            pass

    if system == "Darwin":
        try:
            result = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True, text=True, timeout=2, errors="ignore",
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception:
            pass

    if system == "Windows":
        try:
            result = subprocess.run(
                ["wmic", "cpu", "get", "name"],
                capture_output=True, text=True, timeout=3, errors="ignore",
            )
            if result.returncode == 0:
                lines = [l.strip() for l in result.stdout.splitlines() if l.strip() and "Name" not in l]
                if lines:
                    return lines[0]
        except Exception:
            pass

    return platform.processor() or None


# ==================== GPU ====================

def _get_gpu_info() -> list[dict[str, Any]]:
    """跨平台获取 GPU 硬件信息（零额外依赖，不含序列号等标识）。"""
    system = platform.system()

    # 1) 优先：nvidia-smi（跨平台，NVIDIA 驱动自带，最准确）
    gpus = _gpu_via_nvidia_smi()
    if gpus:
        return gpus

    # 2) Windows：PowerShell Get-CimInstance
    if system == "Windows":
        return _gpu_via_powershell()

    # 3) macOS：system_profiler
    if system == "Darwin":
        return _gpu_via_system_profiler()

    # 4) Linux：lspci
    if system == "Linux":
        return _gpu_via_lspci()

    return []


def _gpu_via_nvidia_smi() -> list[dict[str, Any]]:
    """通过 nvidia-smi 枚举 NVIDIA 显卡（跨平台）。"""
    gpus: list[dict[str, Any]] = []

    # 可能的可执行路径
    candidates = ["nvidia-smi"]
    if platform.system() == "Windows":
        candidates += [
            r"C:\Windows\System32\nvidia-smi.exe",
            r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
        ]

    cmd_args = [
        "--query-gpu=name,memory.total,driver_version",
        "--format=csv,noheader,nounits",
    ]

    for exe in candidates:
        try:
            result = subprocess.run(
                [exe] + cmd_args,
                capture_output=True, text=True, timeout=5, errors="ignore",
            )
            if result.returncode != 0:
                continue
            for line in result.stdout.strip().splitlines():
                if not line.strip():
                    continue
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 3:
                    continue
                name, vram, driver = parts[0], parts[1], parts[2]
                gpu: dict[str, Any] = {"name": name, "source": "nvidia-smi"}
                try:
                    gpu["vram_total_mb"] = int(vram)
                except ValueError:
                    pass
                if driver:
                    gpu["driver_version"] = driver
                gpus.append(gpu)
            if gpus:
                return gpus
        except Exception:
            continue

    return gpus


def _gpu_via_powershell() -> list[dict[str, Any]]:
    """Windows：通过 Get-CimInstance 枚举所有显卡（含集显）。"""
    gpus: list[dict[str, Any]] = []
    ps = (
        "Get-CimInstance -ClassName Win32_VideoController | "
        "Select-Object Name, AdapterRAM | ConvertTo-Json -Compress"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=10, errors="ignore",
        )
        if result.returncode != 0 or not result.stdout.strip():
            return gpus

        data = json.loads(result.stdout.strip())
        if isinstance(data, dict):
            data = [data]

        for item in data:
            name = item.get("Name")
            if not name:
                continue
            gpu: dict[str, Any] = {"name": name, "source": "Win32_VideoController"}
            ram = item.get("AdapterRAM")
            if ram:
                try:
                    ram_int = int(ram)
                    if ram_int > 0:
                        gpu["vram_total_mb"] = round(ram_int / (1024 ** 2))
                        # 注：AdapterRAM 为 32 位字段，>4GB 显存可能显示上限值
                except (ValueError, TypeError):
                    pass
            gpus.append(gpu)
    except Exception:
        pass
    return gpus


def _gpu_via_system_profiler() -> list[dict[str, Any]]:
    """macOS：通过 system_profiler 枚举显卡。"""
    gpus: list[dict[str, Any]] = []
    try:
        result = subprocess.run(
            ["system_profiler", "SPDisplaysDataType"],
            capture_output=True, text=True, timeout=8, errors="ignore",
        )
        if result.returncode != 0:
            return gpus

        current: dict[str, Any] | None = None
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("Chipset Model:"):
                if current:
                    gpus.append(current)
                current = {
                    "name": stripped.split(":", 1)[1].strip(),
                    "source": "system_profiler",
                }
            elif "VRAM (Total):" in stripped and current is not None:
                vram = stripped.split(":", 1)[1].strip()
                current["vram_total_str"] = vram
        if current:
            gpus.append(current)
    except Exception:
        pass
    return gpus


def _gpu_via_lspci() -> list[dict[str, Any]]:
    """Linux：通过 lspci 枚举显示设备。"""
    gpus: list[dict[str, Any]] = []
    try:
        result = subprocess.run(
            ["lspci"], capture_output=True, text=True, timeout=3, errors="ignore",
        )
        if result.returncode != 0:
            return gpus
        for line in result.stdout.splitlines():
            if re.search(r"VGA|3D|Display", line, re.I):
                name = line.split(":", 2)[-1].strip()
                gpus.append({"name": name, "source": "lspci"})
    except Exception:
        pass
    return gpus


# 时间日期类
@basic_tool
@tool
def get_current_time(
    timezone: Annotated[str, "IANA 时区名，如 Asia/Shanghai、UTC、America/New_York"] = "Asia/Shanghai",
) -> dict[str, Any]:
    """获取指定时区的当前日期和时间。"""
    try:
        tz = ZoneInfo(timezone)
    except Exception:
        raise ValueError(f"未知时区: {timezone}（Linux/macOS 通常自带 tzdata，Windows 需 pip install tzdata）")
    now = datetime.now(tz)
    return {
        "iso": now.isoformat(),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "weekday": now.strftime("%A"),
        "timezone": timezone,
        "timestamp": round(now.timestamp(), 3),
    }
@basic_tool
@tool
def date_diff(
    date1: Annotated[str, "日期1，格式 YYYY-MM-DD"],
    date2: Annotated[str, "日期2，格式 YYYY-MM-DD"],
) -> dict[str, int]:
    """计算两个日期之间的天数差。"""
    from datetime import datetime
    
    d1 = datetime.strptime(date1, "%Y-%m-%d")
    d2 = datetime.strptime(date2, "%Y-%m-%d")
    delta = abs((d2 - d1).days)
    
    return {
        "days": delta,
        "weeks": delta // 7,
    }
@basic_tool
@tool
def random_number(
    low: Annotated[int, "最小值（含）"] = 0,
    high: Annotated[int, "最大值（含）"] = 100,
) -> int:
    """生成指定闭区间内的随机整数。"""
    if low > high:
        raise ValueError("low 不能大于 high")
    return random.randint(low, high)




@basic_tool
@tool
def get_system_info() -> dict[str, Any]:
    """获取系统基本信息、CPU 与 GPU 硬件信息（不含网络敏感数据）。"""
    info: dict[str, Any] = {
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        "cwd": os.getcwd(),
        "username": os.getenv("USER") or os.getenv("USERNAME"),
        "cpu": {},
        "gpu": [],
        "memory_total_gb": None,
    }

    # ========== CPU ==========
    try:
        import psutil
        info["cpu"] = {
            "physical_cores": psutil.cpu_count(logical=False),
            "logical_threads": psutil.cpu_count(logical=True),
            "freq_current_mhz": _safe_getattr(psutil.cpu_freq(), "current"),
            "freq_min_mhz": _safe_getattr(psutil.cpu_freq(), "min"),
            "freq_max_mhz": _safe_getattr(psutil.cpu_freq(), "max"),
            "model": _get_cpu_model(),
        }
        mem = psutil.virtual_memory()
        info["memory_total_gb"] = round(mem.total / (1024 ** 3), 1)
    except Exception:
        pass

    # ========== GPU ==========
    info["gpu"] = _get_gpu_info()

    return info



# 数据计算与转换
@basic_tool
@tool
def calculate(
    expression: Annotated[str, "数学表达式，如 '2 + 3 * 4'"],
) -> float:
    """安全地计算数学表达式。"""
    import ast
    import operator
    
    allowed_operators = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Pow: operator.pow,
    }
    
    def eval_node(node):
        if isinstance(node, ast.Constant):
            return node.value
        elif isinstance(node, ast.BinOp) and type(node.op) in allowed_operators:
            left = eval_node(node.left)
            right = eval_node(node.right)
            return allowed_operators[type(node.op)](left, right)
        else:
            raise ValueError(f"不支持的操作: {ast.dump(node)}")
    
    tree = ast.parse(expression, mode='eval')
    return eval_node(tree.body)
@basic_tool
@tool
def format_json(
    data: Annotated[Union[str, dict, list], "JSON 字符串或对象"],
    indent: Annotated[int, "缩进空格数"] = 2,
) -> str:
    """格式化 JSON 数据。"""
    if isinstance(data, str):
        data = json.loads(data)
    return json.dumps(data, ensure_ascii=False, indent=indent)

# 搜索类
@basic_tool
@tool
def search_web(
    query: Annotated[str, "搜索关键词"],
    num_results: Annotated[int, "返回结果数量"],
) -> list[dict[str, str]]:
    """执行网络搜索（需要配置搜索 API）。"""
    raise NotImplementedError("搜索功能尚未配置，请接入搜索 API")

def get_basic_tools() -> list:
    """返回基本工具组的所有工具。"""
    return BASIC_TOOLS.copy()