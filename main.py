"""Harness Agent API 启动入口。

用法：
    .\\.venv\\Scripts\\python.exe main.py

环境变量：
    HARNESS_API_HOST  默认 127.0.0.1
    HARNESS_API_PORT  默认 8000
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# psycopg 的异步连接不支持 Windows 默认的 ProactorEventLoop。
# 必须在导入 api.chat 之前切换事件循环策略。
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn  # noqa: E402

from api.chat import app  # noqa: E402


def main() -> None:
    """以单进程模式启动 FastAPI 服务。"""
    host = os.getenv("HARNESS_API_HOST", "127.0.0.1")
    port = int(os.getenv("HARNESS_API_PORT", "8000"))
    # loop="none" 表示沿用上面已设置的 Selector 事件循环策略。
    uvicorn.run(app, host=host, port=port, loop="none")


if __name__ == "__main__":
    main()
