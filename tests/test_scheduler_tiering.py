"""P0 调度分档的单元测试。运行：python -m unittest discover -s tests -v"""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from scheduler.config import TierConfig, reset_config  # noqa: E402
from scheduler.features import extract_features  # noqa: E402
from scheduler.models import FeatureVector, Tier  # noqa: E402
from scheduler.shadow import shadow_evaluate, summarize_shadow  # noqa: E402
from scheduler.tiering import (  # noqa: E402
    complexity_score,
    decide_tier,
    decision_boundaries,
    memberships,
    suggest_theta,
)
from task_flow.models import ExecutionPlan, ExecutionUnit, Step, TaskFlow  # noqa: E402


def make_config(**overrides) -> TierConfig:
    base = dict(theta1=0.40, theta2=0.65, alpha=7.2, tau=0.3, delta=0.03)
    base.update(overrides)
    return TierConfig(**base)


def flat_features(value: float, missing=None, structural=None) -> FeatureVector:
    return FeatureVector(
        complexity=value,
        uncertainty=value,
        impact=value,
        scale=value,
        dep_depth=value,
        tool_diversity=value,
        acceptance=value,
        missing=missing or [],
        structural_missing=structural or [],
    )


class TestTierMath(unittest.TestCase):
    def test_probabilities_sum_to_one(self):
        cfg = make_config()
        for i in range(11):
            s = i / 10
            probs = memberships(s, cfg.theta1, cfg.theta2, cfg.alpha)
            self.assertAlmostEqual(sum(probs.values()), 1.0, places=9)
            self.assertTrue(all(p >= 0 for p in probs.values()))

    def test_score_monotonic_in_each_dimension(self):
        cfg = make_config()
        base = flat_features(0.3)
        low = complexity_score(base, cfg.weights)
        for name in base.as_dict():
            higher = base.model_copy(update={name: 0.8})
            self.assertGreater(complexity_score(higher, cfg.weights), low, name)

    def test_alpha_lower_bound_is_enforced(self):
        # gap=0.2 => alpha_min=6.93
        with self.assertRaises(ValueError):
            TierConfig(theta1=0.4, theta2=0.6, alpha=6.0).validate()

    def test_theta_order_is_enforced(self):
        with self.assertRaises(ValueError):
            TierConfig(theta1=0.7, theta2=0.6).validate()

    def test_middle_class_reachable(self):
        cfg = make_config()
        mid = (cfg.theta1 + cfg.theta2) / 2
        probs = memberships(mid, cfg.theta1, cfg.theta2, cfg.alpha)
        self.assertEqual(max(probs, key=probs.get), "MD")

    def test_missing_three_dims_never_ez(self):
        cfg = make_config(tau=0.9)
        x = flat_features(0.02, missing=["impact", "tool_diversity", "dep_depth"])
        decision = decide_tier(x, cfg)
        self.assertNotEqual(decision.tier, Tier.EZ)

    def test_structural_missing_does_not_block_ez(self):
        # P0 的 impact / tool_diversity / dep_depth 没有数据源，属于结构性缺失，
        # 不能因此永久禁止 EZ，否则影子模式永远采不到 EZ 样本。
        cfg = make_config(tau=0.3)
        x = flat_features(
            0.02,
            missing=["impact", "tool_diversity", "dep_depth"],
            structural=["impact", "tool_diversity", "dep_depth"],
        )
        self.assertEqual(decide_tier(x, cfg).tier, Tier.EZ)

    def test_missing_dims_are_renormalized_not_imputed(self):
        # 缺失维度不应以 0.5 参与聚合：全部观测值都是 0.2 时，S 仍应约等于 0.2。
        cfg = make_config()
        x = flat_features(
            0.2,
            missing=["impact", "tool_diversity", "dep_depth"],
            structural=["impact", "tool_diversity", "dep_depth"],
        )
        self.assertAlmostEqual(complexity_score(x, cfg.weights), 0.2, delta=0.02)


class TestTierDecision(unittest.TestCase):
    def test_hysteresis_blocks_upgrade_near_boundary(self):
        cfg = make_config()
        # S≈0.461：argmax 已是 MD，但真实切换点 b1≈0.456，未越过 b1+delta=0.486。
        decision = decide_tier(flat_features(0.46), cfg, prev_tier=Tier.EZ)
        self.assertEqual(decision.tier, Tier.EZ)
        self.assertIn("滞回", decision.reason)

    def test_hysteresis_allows_upgrade_beyond_delta(self):
        cfg = make_config()
        decision = decide_tier(flat_features(0.49), cfg, prev_tier=Tier.EZ)
        self.assertEqual(decision.tier, Tier.MD)

    def test_argmax_boundary_differs_from_theta(self):
        cfg = make_config()
        b1, b2 = decision_boundaries(cfg.theta1, cfg.theta2, cfg.alpha)
        # 真实切换点由三档概率的 argmax 决定：b1 右移、b2 左移，都不等于 theta。
        self.assertGreater(b1, cfg.theta1)
        self.assertLess(b2, cfg.theta2)
        self.assertLess(b1, b2)

    def test_low_confidence_upgrades_one_tier(self):
        cfg = make_config(tau=0.55)
        decision = decide_tier(flat_features(0.41), cfg)
        self.assertTrue(decision.upgraded)
        self.assertEqual(decision.tier, Tier.MD)

    def test_hard_override_wins(self):
        cfg = make_config()
        decision = decide_tier(flat_features(0.02), cfg, hard_override=Tier.HD)
        self.assertEqual(decision.tier, Tier.HD)
        self.assertEqual(decision.rule_override, "HD")

    def test_suggest_theta_follows_priors(self):
        scores = list(range(1000))
        theta1, theta2 = suggest_theta(scores)
        self.assertLess(theta1, theta2)
        self.assertAlmostEqual(theta1, 326.7, delta=2)
        self.assertAlmostEqual(theta2, 839.2, delta=2)


class TestFeatures(unittest.TestCase):
    def test_reference_flow_changes_features(self):
        message = "按照文档把这个流程改一下"
        text_only = extract_features(message)

        flow = TaskFlow(
            id="f1",
            name="demo",
            steps=[
                Step(id="s1", name="读取", instruction="读取配置", checkpoint=True, constraints="只读"),
                Step(id="s2", name="修改", instruction="修改逻辑"),
                Step(id="s3", name="验证", instruction="运行测试", checkpoint=True),
            ],
        )
        plan = ExecutionPlan(
            flow_id="f1",
            schema_hash="sha256:demo",
            units=[ExecutionUnit(seq=1, type="step", ref="s1")],
        )
        with_ref = extract_features(message, flow=flow, plan=plan)

        self.assertLess(with_ref.uncertainty, text_only.uncertainty)
        self.assertGreater(with_ref.complexity, text_only.complexity)
        self.assertGreater(with_ref.acceptance, text_only.acceptance)
        self.assertNotIn("dep_depth", with_ref.missing)
        for vector in (text_only, with_ref):
            self.assertIn("impact", vector.missing)
            self.assertIn("tool_diversity", vector.missing)
            self.assertIn("impact", vector.structural_missing)
            self.assertIn("tool_diversity", vector.structural_missing)
            for value in vector.as_dict().values():
                self.assertGreaterEqual(value, 0.02)
                self.assertLessEqual(value, 0.98)


class TestShadow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "shadow.jsonl"
        self.env = mock.patch.dict(
            os.environ,
            {
                "HARNESS_SCHEDULER_ENABLED": "1",
                "HARNESS_SCHEDULER_SHADOW": "1",
                "HARNESS_SCHEDULER_SHADOW_PATH": str(self.path),
                "HARNESS_SCHEDULER_PREVIEW_CHARS": "20",
            },
            clear=False,
        )
        self.env.start()
        reset_config()

    def tearDown(self):
        self.env.stop()
        reset_config()
        self.tmp.cleanup()

    def test_shadow_appends_record_without_raw_text(self):
        message = "帮我实现一个跨三个服务的登录改造，需要动数据库和发布上线"
        record = asyncio.run(shadow_evaluate(message, "u1", None))
        self.assertIsNotNone(record)
        self.assertEqual(record.mode, "dynamic")
        self.assertTrue(self.path.exists())

        rows = [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]["message_preview"]), 20)
        self.assertEqual(len(rows[0]["message_sha256"]), 64)
        self.assertNotIn(message, json.dumps(rows[0], ensure_ascii=False))

        summary = summarize_shadow(self.path)
        self.assertEqual(summary["records"], 1)
        self.assertIn("raw_tier_counts", summary)
        self.assertIn("ez_blocked", summary)
        # 纯概率分档与最终档位都要落盘，安全规则不能吞掉原始分布。
        self.assertIn("raw_tier", rows[0])
        self.assertIn(rows[0]["raw_tier"], {"EZ", "MD", "HD"})

    def test_shadow_disabled_returns_none(self):
        with mock.patch.dict(os.environ, {"HARNESS_SCHEDULER_ENABLED": "0"}, clear=False):
            reset_config()
            self.assertIsNone(asyncio.run(shadow_evaluate("hi", "u1", None)))
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
