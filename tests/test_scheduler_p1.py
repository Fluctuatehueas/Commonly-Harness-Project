"""P1 计划器 / 检查点 / 重规划 / 执行器单测。

运行：python -m unittest discover -s tests -p "test_scheduler_p1.py" -v
不依赖 PostgreSQL 与模型：执行器与存储全部用假实现注入。
"""

import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from scheduler.checkpoint import score_checkpoint  # noqa: E402
from scheduler.config import TierConfig  # noqa: E402
from scheduler.models import (  # noqa: E402
    PlanItem,
    PlanItemInput,
    PlanPhase,
    PlanPhaseInput,
    RunState,
    SchedulePlan,
    Tier,
)
from scheduler.planner import (  # noqa: E402
    PlanError,
    build_plan,
    build_reference_plan,
)
from scheduler.replanner import promote_pending, subtree  # noqa: E402
from scheduler.runner import ItemOutcome, SchedulerRunner  # noqa: E402
from task_flow.models import Step, Subflow, TaskFlow  # noqa: E402


def make_cfg(**overrides) -> TierConfig:
    base = dict(
        max_items=32,
        max_parallel=1,
        max_attempts=2,
        max_replan=3,
        node_timeout_sec=2.0,
        checkpoint_every=100,
        p_replan=0.70,
        p_reorder=0.45,
    )
    base.update(overrides)
    return TierConfig(**base)


def item_input(item_id: str, **overrides) -> PlanItemInput:
    base = dict(id=item_id, title=item_id)
    base.update(overrides)
    return PlanItemInput(**base)


def three_phase_input() -> list[PlanPhaseInput]:
    return [
        PlanPhaseInput(id="P0", title="调研", items=[item_input("p0.1")]),
        PlanPhaseInput(id="P1", title="实现", items=[item_input("p1.1")]),
        PlanPhaseInput(id="P2", title="验收", items=[item_input("p2.1")]),
    ]


class PlannerTest(unittest.TestCase):
    def test_md_only_expands_two_phases(self):
        plan = build_plan(
            run_id="r1",
            goal="g",
            tier=Tier.MD,
            phases=three_phase_input(),
            cfg=make_cfg(),
        )
        self.assertEqual([p.id for p in plan.phases], ["P0", "P1"])
        self.assertEqual([p.id for p in plan.pending_phases], ["P2"])
        self.assertEqual(plan.waves, [["p0.1"], ["p1.1"]])

    def test_hd_expands_all_phases(self):
        plan = build_plan(
            run_id="r2",
            goal="g",
            tier=Tier.HD,
            phases=three_phase_input(),
            cfg=make_cfg(),
        )
        self.assertEqual(len(plan.phases), 3)
        self.assertEqual(plan.pending_phases, [])

    def test_ez_is_rejected(self):
        with self.assertRaises(PlanError):
            build_plan(
                run_id="r3",
                goal="g",
                tier=Tier.EZ,
                phases=three_phase_input(),
                cfg=make_cfg(),
            )

    def test_same_wave_write_conflict_is_serialized(self):
        phases = [
            PlanPhaseInput(
                id="P0",
                title="并发写",
                items=[
                    item_input("a", writes=["src/app.py"]),
                    item_input("b", writes=["src/app.py"]),
                ],
            )
        ]
        plan = build_plan(
            run_id="r4", goal="g", tier=Tier.HD, phases=phases, cfg=make_cfg(max_parallel=4)
        )
        self.assertEqual(len(plan.waves), 2)
        self.assertTrue(plan.conflicts)
        self.assertIn("a", plan.phases[0].items[1].depends_on)

    def test_cycle_is_rejected(self):
        phases = [
            PlanPhaseInput(
                id="P0",
                title="环",
                items=[
                    item_input("a", depends_on=["b"]),
                    item_input("b", depends_on=["a"]),
                ],
            )
        ]
        with self.assertRaises(PlanError):
            build_plan(run_id="r5", goal="g", tier=Tier.HD, phases=phases, cfg=make_cfg())

    def test_too_many_items(self):
        phases = [
            PlanPhaseInput(
                id="P0",
                title="超限",
                items=[item_input(f"i{index}") for index in range(5)],
            )
        ]
        with self.assertRaises(PlanError):
            build_plan(
                run_id="r6", goal="g", tier=Tier.HD, phases=phases, cfg=make_cfg(max_items=4)
            )

    def test_reference_plan_maps_flow(self):
        flow = TaskFlow(
            id="tf_demo",
            name="演示流",
            steps=[
                Step(id="s1", name="读需求", instruction="读取需求"),
                Step(
                    id="s2",
                    name="并行实现",
                    instruction="分两路实现",
                    parallel=[
                        Subflow(name="前端", steps=[Step(id="s2.1", name="页面")]),
                        Subflow(name="后端", steps=[Step(id="s2.2", name="接口")]),
                    ],
                ),
            ],
        )
        plan = build_reference_plan(run_id="r7", flow=flow, cfg=make_cfg(), tier=Tier.HD)
        ids = {item.id for phase in plan.phases for item in phase.items}
        self.assertEqual(ids, {"s1", "s2", "s2.1", "s2.2"})
        self.assertEqual(plan.mode, "reference")


class CheckpointTest(unittest.TestCase):
    def test_irreversible_requires_hitl(self):
        decision = score_checkpoint(
            tier=Tier.MD, irreversible=True, cfg=make_cfg()
        )
        self.assertEqual(decision.action, "hitl")

    def test_global_new_requirement_forces_full_replan(self):
        decision = score_checkpoint(
            tier=Tier.MD, global_scope=True, cfg=make_cfg()
        )
        self.assertEqual(decision.action, "replan")
        self.assertEqual(decision.scope, "full")

    def test_high_risk_replans(self):
        decision = score_checkpoint(
            tier=Tier.HD,
            impact="high",
            uncertainty="high",
            user_delta=True,
            budget_ratio=0.8,
            cfg=make_cfg(),
        )
        self.assertEqual(decision.action, "replan")
        self.assertEqual(decision.scope, "subtree")

    def test_low_risk_continues(self):
        decision = score_checkpoint(
            tier=Tier.EZ,
            impact="low",
            uncertainty="low",
            budget_ratio=0.0,
            cfg=make_cfg(),
        )
        self.assertEqual(decision.action, "continue")


class ReplannerTest(unittest.TestCase):
    def _plan(self) -> SchedulePlan:
        phases = [
            PlanPhase(
                id="P0",
                title="A",
                items=[
                    PlanItem(id="a", phase_id="P0", title="a", status="done"),
                    PlanItem(id="b", phase_id="P0", title="b", depends_on=["a"]),
                    PlanItem(id="c", phase_id="P0", title="c", depends_on=["b"]),
                ],
            )
        ]
        return SchedulePlan(
            run_id="r",
            goal="g",
            mode="dynamic",
            tier=Tier.HD,
            phases=phases,
            waves=[["a"], ["b"], ["c"]],
        )

    def test_subtree_resets_failed_and_downstream_but_keeps_done(self):
        plan = self._plan()
        plan.phases[0].items[1].status = "failed"
        plan.phases[0].items[2].status = "running"
        updated, changed, _ = subtree(plan, "b")
        statuses = {item.id: item.status for item in updated.phases[0].items}
        self.assertEqual(statuses["a"], "done")
        self.assertEqual(statuses["b"], "pending")
        self.assertEqual(statuses["c"], "pending")
        self.assertEqual(sorted(changed), ["b", "c"])
        self.assertEqual(updated.plan_version, plan.plan_version + 1)

    def test_promote_pending_expands_next_phase(self):
        plan = build_plan(
            run_id="r8",
            goal="g",
            tier=Tier.MD,
            phases=three_phase_input(),
            cfg=make_cfg(),
        )
        for item in plan.phases[0].items + plan.phases[1].items:
            item.status = "done"
        updated, changed, _ = promote_pending(plan)
        self.assertEqual([p.id for p in updated.phases], ["P0", "P1", "P2"])
        self.assertEqual(changed, ["p2.1"])
        self.assertEqual(updated.pending_phases, [])


class FakeStore:
    def __init__(self) -> None:
        self.states = []
        self.plans = []
        self.checkpoints = []

    async def update_run(self, state):
        self.states.append(state.model_copy(deep=True))

    async def save_plan(self, plan):
        self.plans.append(plan.model_copy(deep=True))

    async def add_checkpoint(self, **kwargs):
        self.checkpoints.append(kwargs)


class RunnerTest(unittest.TestCase):
    def _plan(self, *, phases=None, tier=Tier.HD) -> SchedulePlan:
        phases = phases or [
            PlanPhase(
                id="P0",
                title="A",
                items=[
                    PlanItem(id="a", phase_id="P0", title="a"),
                    PlanItem(id="b", phase_id="P0", title="b", depends_on=["a"]),
                    PlanItem(id="c", phase_id="P0", title="c", depends_on=["b"]),
                ],
            )
        ]
        return SchedulePlan(
            run_id="run_test",
            goal="g",
            mode="dynamic",
            tier=tier,
            phases=phases,
            waves=[["a"], ["b"], ["c"]],
        )

    def _state(self) -> RunState:
        return RunState(run_id="run_test", user_id="u", goal="g", tier=Tier.HD)

    def test_runs_in_dependency_order_and_persists(self):
        order = []

        async def executor(item, state):
            order.append(item.id)
            return ItemOutcome(status="done", summary=f"{item.id} ok")

        store = FakeStore()
        runner = SchedulerRunner(cfg=make_cfg(), executor=executor, store=store)
        result = asyncio.run(runner.run(self._plan(), self._state()))

        self.assertTrue(result.ok)
        self.assertEqual(order, ["a", "b", "c"])
        self.assertEqual(result.done, ["a", "b", "c"])
        self.assertTrue(store.states)
        self.assertTrue(store.plans)

    def test_retry_then_success(self):
        attempts = {"a": 0}

        async def executor(item, state):
            attempts[item.id] = attempts.get(item.id, 0) + 1
            if item.id == "a" and attempts[item.id] == 1:
                raise RuntimeError("第一次失败")
            return ItemOutcome(status="done", summary="ok")

        runner = SchedulerRunner(cfg=make_cfg(), executor=executor)
        result = asyncio.run(runner.run(self._plan(), self._state()))
        self.assertTrue(result.ok)
        self.assertEqual(attempts["a"], 2)

    def test_failed_dependency_skips_downstream(self):
        async def executor(item, state):
            if item.id == "a":
                return ItemOutcome(status="failed", error="boom")
            return ItemOutcome(status="done")

        runner = SchedulerRunner(cfg=make_cfg(), executor=executor)
        result = asyncio.run(runner.run(self._plan(), self._state()))
        self.assertFalse(result.ok)
        self.assertIn("a", result.failed)
        self.assertIn("b", result.skipped)
        self.assertIn("c", result.skipped)

    def test_promote_pending_executes_next_phase(self):
        phases = [
            PlanPhase(id="P0", title="A", items=[PlanItem(id="a", phase_id="P0", title="a")])
        ]
        plan = SchedulePlan(
            run_id="run_test",
            goal="g",
            mode="dynamic",
            tier=Tier.MD,
            phases=phases,
            pending_phases=[
                PlanPhase(
                    id="P1", title="B", items=[PlanItem(id="b", phase_id="P1", title="b")]
                )
            ],
            waves=[["a"]],
        )

        async def executor(item, state):
            return ItemOutcome(status="done")

        runner = SchedulerRunner(cfg=make_cfg(), executor=executor)
        result = asyncio.run(runner.run(plan, self._state()))
        self.assertTrue(result.ok)
        self.assertEqual(sorted(result.done), ["a", "b"])
        self.assertEqual(result.replans, 1)


if __name__ == "__main__":
    unittest.main()
