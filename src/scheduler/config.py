"""调度模块配置。P0 默认关闭，只读环境变量，不做运行时热更新。"""

import math
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_SHADOW_PATH = _PROJECT_ROOT / ".harness" / "scheduler_shadow.jsonl"

# 专家先验权重，和为 1。真实样本积累后由有序 logistic 重新拟合。
DEFAULT_WEIGHTS = {
    "complexity": 0.20,
    "uncertainty": 0.18,
    "impact": 0.18,
    "scale": 0.14,
    "dep_depth": 0.10,
    "tool_diversity": 0.08,
    "acceptance": 0.12,
}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class TierConfig:
    enabled: bool = False
    shadow: bool = True
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    # 冷启动边界：缺少历史 S 分布时使用三等分附近的保守值，
    # 上线后用 scheduler.shadow 收集的 S 重新取 32.7% / 84.0% 分位。
    theta1: float = 0.40
    theta2: float = 0.65
    alpha: float = 7.2
    tau: float = 0.55
    delta: float = 0.03
    shadow_path: Path = _DEFAULT_SHADOW_PATH
    preview_chars: int = 80
    with_judge: bool = False  # P0 不调用 LLM 补特征
    validate_on_load: bool = True

    # ---- P1 执行参数 ----
    exec_enabled: bool = False  # 独立开关；只有工具显式调用才进入调度
    max_items: int = 32
    max_parallel: int = 1
    max_replan: int = 3
    max_attempts: int = 2
    node_timeout_sec: float = 900.0
    wave_depth_warn: int = 6
    checkpoint_every: int = 3
    # 检查点打分：p >= p_replan 重规划；p_reorder <= p < p_replan 只允许排序。
    p_replan: float = 0.70
    p_reorder: float = 0.45
    # 上下文占用达到该比例后进入收尾模式：不再展开新阶段
    wrapup_ratio: float = 0.80
    # 节流：同一阶段最多重规划次数、两次重规划之间的最少完成节点数
    phase_replan_limit: int = 1
    replan_min_nodes: int = 2
    beta: tuple[float, float, float, float] = (0.35, 0.25, 0.20, 0.20)
    bias: dict[str, float] = field(
        default_factory=lambda: {"EZ": -0.8, "MD": 0.0, "HD": 0.5}
    )

    def validate(self) -> None:
        if not (0.0 <= self.theta1 < self.theta2 <= 1.0):
            raise ValueError(f"theta 必须满足 0 <= theta1 < theta2 <= 1，当前 {self.theta1}/{self.theta2}")
        if self.theta2 - self.theta1 < 0.05:
            raise ValueError(f"theta2 - theta1 至少 0.05，当前 {self.theta2 - self.theta1:.3f}")
        alpha_min = 2 * math.log(2) / (self.theta2 - self.theta1)
        if self.alpha <= alpha_min:
            raise ValueError(f"alpha 必须大于下限 {alpha_min:.2f}，否则中间档不可选；当前 {self.alpha}")
        if not (0.0 < self.tau < 1.0):
            raise ValueError(f"tau 必须在 (0, 1)，当前 {self.tau}")
        if self.delta < 0:
            raise ValueError(f"delta 不能为负，当前 {self.delta}")
        if any(w < 0 for w in self.weights.values()) or sum(self.weights.values()) <= 0:
            raise ValueError("权重必须非负且和为正")
        if set(self.weights) != set(DEFAULT_WEIGHTS):
            raise ValueError(f"权重维度必须是 {sorted(DEFAULT_WEIGHTS)}")
        if not (0.0 < self.p_reorder < self.p_replan < 1.0):
            raise ValueError(
                f"阈值必须满足 0 < p_reorder < p_replan < 1，当前 {self.p_reorder}/{self.p_replan}"
            )
        if self.max_items < 1 or self.max_parallel < 1 or self.max_replan < 0:
            raise ValueError("max_items / max_parallel 必须 >= 1，max_replan 必须 >= 0")
        if self.max_attempts < 1 or self.checkpoint_every < 1:
            raise ValueError("max_attempts / checkpoint_every 必须 >= 1")
        if not (0.0 < self.wrapup_ratio <= 1.0):
            raise ValueError(f"wrapup_ratio 必须在 (0, 1]，当前 {self.wrapup_ratio}")
        if self.phase_replan_limit < 0 or self.replan_min_nodes < 0:
            raise ValueError("phase_replan_limit / replan_min_nodes 不能为负")


def build_config() -> TierConfig:
    cfg = TierConfig(
        enabled=_env_bool("HARNESS_SCHEDULER_ENABLED", True), # 调度策略开关
        shadow=_env_bool("HARNESS_SCHEDULER_SHADOW", False), # 影子模式开关
        theta1=_env_float("HARNESS_SCHEDULER_THETA1", 0.40), # EZ 与 MD 档位的分界线
        theta2=_env_float("HARNESS_SCHEDULER_THETA2", 0.65), # MD 与 HD及以上的 分界线
        alpha=_env_float("HARNESS_SCHEDULER_ALPHA", 7.2), # `α` 控制过渡带宽度：越大越接近硬阈值，越小过渡越宽、越保守
        tau=_env_float("HARNESS_SCHEDULER_TAU", 0.55), # | 置信阈值 `τ` |
        delta=_env_float("HARNESS_SCHEDULER_DELTA", 0.03), # 由同一任务重复评估的波动幅度估计
        shadow_path=Path(os.getenv("HARNESS_SCHEDULER_SHADOW_PATH") or _DEFAULT_SHADOW_PATH),
        preview_chars=_env_int("HARNESS_SCHEDULER_PREVIEW_CHARS", 80),
        exec_enabled=_env_bool("HARNESS_SCHEDULER_EXEC", True), # 动态任务规划开关
        max_items=_env_int("HARNESS_SCHEDULER_MAX_ITEMS", 32),
        max_parallel=_env_int("HARNESS_SCHEDULER_MAX_PARALLEL", 1),
        max_replan=_env_int("HARNESS_SCHEDULER_MAX_REPLAN", 3),
        max_attempts=_env_int("HARNESS_SCHEDULER_MAX_ATTEMPTS", 2),
        node_timeout_sec=_env_float("HARNESS_SCHEDULER_NODE_TIMEOUT", 900.0),
        wave_depth_warn=_env_int("HARNESS_SCHEDULER_WAVE_DEPTH_WARN", 6),
        checkpoint_every=_env_int("HARNESS_SCHEDULER_CHECKPOINT_EVERY", 3),
        p_replan=_env_float("HARNESS_SCHEDULER_P_REPLAN", 0.70),
        p_reorder=_env_float("HARNESS_SCHEDULER_P_REORDER", 0.45),
        wrapup_ratio=_env_float("HARNESS_SCHEDULER_WRAPUP_RATIO", 0.80),
        phase_replan_limit=_env_int("HARNESS_SCHEDULER_PHASE_REPLANS", 1),
        replan_min_nodes=_env_int("HARNESS_SCHEDULER_REPLAN_MIN_NODES", 2),
    )
    if cfg.validate_on_load:
        cfg.validate()
    return cfg


@lru_cache(maxsize=1)
def get_config() -> TierConfig:
    return build_config()


def reset_config() -> None:
    """测试用：清掉缓存，让新的环境变量生效。"""
    get_config.cache_clear()
