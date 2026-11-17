"""ATDP 读取视图的单元测试。运行：python -m unittest discover -s tests -v"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.evolution.read_view import (  # noqa: E402
    TRUST_ACCEPTED,
    TRUST_CORRECTED,
    TRUST_REJECTED,
    TRUST_UNKNOWN,
    aggregate_rewards,
    apply_atdp_view,
    build_run_summaries,
    resolve_run_feedback,
)


def make_event(event_id, run_id, event_type, start, end, atdp=None):
    return {
        "id": event_id,
        "run_id": run_id,
        "type": event_type,
        "start": start,
        "end": end,
        "atdp": atdp
        if atdp is not None
        else {
            "r_t": {"value": None, "source": "pending", "evidence_ref": ""},
            "y_t": {"returned": True, "trust": TRUST_UNKNOWN, "status": "done"},
        },
    }


class AggregateRewardsTest(unittest.TestCase):
    def test_sums_by_event_and_ignores_run_level(self):
        records = [
            {"event_id": "e1", "value": 0.2, "source": "tool_outcome", "created_at": "2026-11-17T10:00:00+00:00"},
            {"event_id": "e1", "value": -0.1, "source": "verification", "created_at": "2026-11-17T10:01:00+00:00"},
            {"event_id": "run1:run", "value": -0.2, "source": "run_signal", "created_at": "2026-11-17T10:02:00+00:00"},
        ]
        buckets = aggregate_rewards(records)
        self.assertEqual(set(buckets), {"e1"})
        self.assertAlmostEqual(buckets["e1"]["value"], 0.1)
        self.assertEqual(buckets["e1"]["count"], 2)
        self.assertEqual(buckets["e1"]["sources"], ["tool_outcome", "verification"])
        self.assertEqual(buckets["e1"]["latest_at"], "2026-11-17T10:01:00+00:00")


class ResolveRunFeedbackTest(unittest.TestCase):
    def setUp(self):
        self.events = [
            make_event("e1", "run1", "model", "2026-11-17T10:00:00+00:00", "2026-11-17T10:05:00+00:00"),
            make_event("e2", "run2", "model", "2026-11-17T10:10:00+00:00", "2026-11-17T10:15:00+00:00"),
        ]

    def test_maps_feedback_to_latest_finished_run(self):
        feedback = [
            {
                "message_id": "m1",
                "action": "dislike",
                "created_at": "2026-11-17T10:06:00+00:00",
                "updated_at": "2026-11-17T10:06:00+00:00",
            }
        ]
        resolved = resolve_run_feedback(self.events, feedback)
        self.assertEqual(resolved["run1"]["trust"], TRUST_REJECTED)
        self.assertEqual(resolved["run1"]["action"], "dislike")

    def test_retry_becomes_corrected(self):
        feedback = [
            {
                "message_id": "m2",
                "action": "retry",
                "created_at": "2026-11-17T10:16:00+00:00",
                "updated_at": "2026-11-17T10:16:00+00:00",
            }
        ]
        resolved = resolve_run_feedback(self.events, feedback)
        self.assertEqual(resolved["run2"]["trust"], TRUST_CORRECTED)

    def test_out_of_window_is_not_guessed(self):
        feedback = [
            {
                "message_id": "m3",
                "action": "like",
                "created_at": "2026-11-17T12:00:00+00:00",
                "updated_at": "2026-11-17T12:00:00+00:00",
            }
        ]
        self.assertEqual(resolve_run_feedback(self.events, feedback), {})

    def test_explicit_run_id_wins(self):
        feedback = [
            {
                "message_id": "m4",
                "action": "like",
                "created_at": "2026-11-17T10:16:00+00:00",
                "updated_at": "2026-11-17T10:16:00+00:00",
                "meta": {"run_id": "run1"},
            }
        ]
        resolved = resolve_run_feedback(self.events, feedback)
        self.assertEqual(resolved["run1"]["trust"], TRUST_ACCEPTED)

    def test_latest_feedback_overrides(self):
        feedback = [
            {
                "message_id": "m5",
                "action": "dislike",
                "created_at": "2026-11-17T10:06:00+00:00",
                "updated_at": "2026-11-17T10:06:00+00:00",
            },
            {
                "message_id": "m5",
                "action": "like",
                "created_at": "2026-11-17T10:07:00+00:00",
                "updated_at": "2026-11-17T10:07:00+00:00",
            },
        ]
        resolved = resolve_run_feedback(self.events, feedback)
        self.assertEqual(resolved["run1"]["trust"], TRUST_ACCEPTED)


class ApplyViewTest(unittest.TestCase):
    def test_sets_r_t_and_terminal_trust_only(self):
        events = [
            make_event("e1", "run1", "model", "2026-11-17T10:00:00+00:00", "2026-11-17T10:01:00+00:00"),
            make_event("e2", "run1", "tool", "2026-11-17T10:02:00+00:00", "2026-11-17T10:03:00+00:00"),
            make_event("e3", "run1", "model", "2026-11-17T10:04:00+00:00", "2026-11-17T10:05:00+00:00"),
        ]
        rewards = {"e2": {"value": 0.2, "count": 1, "sources": ["tool_outcome"], "latest_at": "2026-11-17T10:03:00+00:00"}}
        run_trust = {"run1": {"trust": TRUST_REJECTED, "action": "dislike", "message_id": "m1", "at": "2026-11-17T10:06:00+00:00"}}
        apply_atdp_view(events, rewards, run_trust)

        self.assertTrue(events[1]["atdp"]["r_t"]["resolved"])
        self.assertAlmostEqual(events[1]["atdp"]["r_t"]["value"], 0.2)
        self.assertFalse(events[0]["atdp"]["r_t"]["resolved"])

        # 只有最后一条模型事件承接 run 级反馈。
        self.assertEqual(events[2]["atdp"]["y_t"]["trust"], TRUST_REJECTED)
        self.assertEqual(events[2]["atdp"]["y_t"]["feedback_message_id"], "m1")
        self.assertEqual(events[0]["atdp"]["y_t"]["trust"], TRUST_UNKNOWN)
        self.assertEqual(events[1]["atdp"]["y_t"]["trust"], TRUST_UNKNOWN)

    def test_skips_events_without_atdp(self):
        events = [{"id": "e1", "run_id": "run1", "type": "model", "start": "", "end": "", "atdp": None}]
        apply_atdp_view(events, {}, {"run1": {"trust": TRUST_ACCEPTED, "action": "like", "message_id": "m", "at": ""}})
        self.assertIsNone(events[0]["atdp"])


class BuildSummariesTest(unittest.TestCase):
    def test_sums_run_rewards_across_sources(self):
        rewards = [
            {"execution_id": "run1", "value": 0.2, "source": "tool_outcome"},
            {"execution_id": "run1", "value": -0.2, "source": "run_signal"},
            {"execution_id": "run2", "value": 0.1, "source": "tool_outcome"},
        ]
        run_trust = {"run1": {"trust": TRUST_REJECTED, "action": "dislike", "message_id": "m1", "at": ""}}
        summaries = build_run_summaries(rewards, run_trust)

        self.assertAlmostEqual(summaries["run1"]["r_t"]["value"], 0.0)
        self.assertEqual(summaries["run1"]["r_t"]["count"], 2)
        self.assertEqual(summaries["run1"]["y_t"]["trust"], TRUST_REJECTED)
        self.assertAlmostEqual(summaries["run2"]["r_t"]["value"], 0.1)
        self.assertNotIn("y_t", summaries["run2"])


if __name__ == "__main__":
    unittest.main()
