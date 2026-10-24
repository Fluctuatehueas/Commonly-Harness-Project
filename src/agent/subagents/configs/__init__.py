"""SubAgent YAML 配置目录。

在 ``configs/`` 下新增 ``<name>.yaml``，再在 ``async_registry.py`` 注册即可。
配置的必填字段：name、description、system_prompt、tools。
可选字段：model、skills、middleware、interrupt_on、permissions、mcp_servers。

``mcp_servers`` 指向项目根目录 ``mcp_servers.yaml`` 中定义的 MCP Server 名称；
使用 ``aload_subagent`` 时会自动加载这些工具。
"""

from pathlib import Path

CONFIGS_DIR = Path(__file__).parent


def config_path(filename: str) -> Path:
    """返回配置目录下的文件路径，避免调用方拼接相对路径。"""
    return CONFIGS_DIR / filename
