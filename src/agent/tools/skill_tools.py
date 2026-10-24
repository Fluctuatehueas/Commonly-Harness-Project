"""
将 skill-management 技能脚本注册为主 Agent 可调用工具。
这段代码是一个技能脚本的动态加载与工具注册层。
它的核心目的是：把存放在 skills/main/skill-management/scripts/skill_management.py 
里的技能实现，不复制、不硬编码地加载进来，
并把其中定义的工厂函数 create_skill_management_tools 提升为本模块的公开 API，
供 Agent 构建时调用。

"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

# agemt/skills/main/skill-management/skill_management.py
SKILL_MANAGEMENT_SCRIPT = (
    Path(__file__).parent.parent
    / "skills"
    / "main"
    / "skill-management"
    / "scripts"
    / "skill_management.py"
)
# 等价于一个从任意路径进行import的操作，由于技能目录路径不固定，因此这么做
def _load_skill_management_script() -> ModuleType:
    """
    加载技能目录中的管理实现，避免把技能逻辑复制到工具模块。
    是单一事实来源（Single Source of Truth）原则
    """
    # spec ModuleSpec 对象，规格说明书
    spec = importlib.util.spec_from_file_location(
        "agent_skill_management_script", # 指定文件的模块名
        SKILL_MANAGEMENT_SCRIPT # 目标文件
    )
    #  如果目标文件不存在，spec会是None，此时就不能有解释器解释该文件
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载 skill-namagement 脚本")
    # module: ModuleType对象，一开始时空客，需要exec_module
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module=module)
    return module

# 一个ModuleTpye，维护一个字典
_skill_management_script = _load_skill_management_script() # 获取skill_management模块
# __dict__初测这个值 nodule["create_skill_management_tools"] = create_skill_management_tools,
# 这个才是对外接口，可以import，上面的不行，因为他是模块本身。
create_skill_management_tools = _skill_management_script.create_skill_management_tools
