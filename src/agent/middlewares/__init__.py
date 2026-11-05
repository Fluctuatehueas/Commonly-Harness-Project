"""Harness 自定义中间件。"""

from agent.middlewares.agent_protection import build_agent_protection_middleware
from agent.middlewares.context_injection import ContextInjectionMiddleware
from agent.middlewares.evolution import EvolutionMiddleware
from agent.middlewares.experience_injection import ExperienceInjectionMiddleware
from agent.middlewares.memory_update import MemoryUpdateMiddleware
from agent.middlewares.self_judge import SelfJudgeMiddleware
from agent.middlewares.skills_sync import SandboxSkillsMiddleware
from agent.middlewares.context_window_monitor import ContextWindowMonitorMiddleWare
from agent.middlewares.token_usage import TokenUsageMiddleware
from agent.middlewares.trajectory import TrajectoryMiddleware
__all__ = [
    "ContextInjectionMiddleware",
    "EvolutionMiddleware",
    "ExperienceInjectionMiddleware",
    "MemoryUpdateMiddleware",
    "SandboxSkillsMiddleware",
    "build_agent_protection_middleware",
    "ContextWindowMonitorMiddleWare",
    "SelfJudgeMiddleware",
    "TokenUsageMiddleware",
    "TrajectoryMiddleware",
]
