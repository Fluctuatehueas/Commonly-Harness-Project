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
    AcceptanceCheck,
    AcceptanceItem,
    FactRecord,
    PlanItem,
    PlanItemInput,
    PlanMethodInput,
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
from scheduler.replanner import (  # noqa: E402
    promote_pending,
    reorder,
    revise,
    revision_targets,
    subtree,
    switch_method,
)
from scheduler.runner import (  # noqa: E402
    ItemOutcome,
    SchedulerRunner,
    _upsert_fact,
    render_facts,
)
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


    def test_method_without_items_is_rejected(self):
        phases = [
            PlanPhaseInput(
                id="P0",
                title="A",
                methods=[PlanMethodInput(id="P0.m1", items=[])],
            )
        ]
        with self.assertRaises(PlanError):
            build_plan(
                run_id="r",
                goal="g",
                tier=Tier.HD,
                phases=phases,
                cfg=make_cfg(),
            )

    def test_phase_methods_materialize_first(self):
        phases = [
            PlanPhaseInput(
                id="P0",
                title="实现",
                methods=[
                    PlanMethodInput(
                        id="P0.m1", title="方案一", items=[item_input("p0.1")]
                    ),
                    PlanMethodInput(
                        id="P0.m2", title="方案二", items=[item_input("p0.2")]
                    ),
                ],
            )
        ]
        plan = build_plan(
            run_id="r",
            goal="g",
            tier=Tier.HD,
            phases=phases,
            cfg=make_cfg(),
        )

        phase = plan.phases[0]
        self.assertEqual(phase.active_method_id, "P0.m1")
        self.assertEqual(phase.tried_methods, ["P0.m1"])
        self.assertEqual([item.id for item in phase.items], ["p0.1"])
        self.assertEqual(len(phase.methods), 2)


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


    def test_reorder_moves_item_to_wave_front(self):
        plan = SchedulePlan(
            run_id="r",
            goal="g",
            mode="dynamic",
            tier=Tier.HD,
            phases=[
                PlanPhase(
                    id="P0",
                    title="A",
                    items=[
                        PlanItem(id="a", phase_id="P0", title="a"),
                        PlanItem(id="b", phase_id="P0", title="b"),
                    ],
                )
            ],
            waves=[["a", "b"]],
        )
        updated, changed, _ = reorder(plan, "b")
        self.assertEqual(updated.waves, [["b", "a"]])
        self.assertEqual(changed, ["b", "a"])
        self.assertEqual(updated.plan_version, 2)

        same, none_changed, _ = reorder(plan)
        self.assertEqual(none_changed, [])
        self.assertEqual(same.plan_version, 1)


    def test_switch_method_keeps_done_leaves(self):
        plan = build_plan(
            run_id="r",
            goal="g",
            tier=Tier.HD,
            phases=[
                PlanPhaseInput(
                    id="P0",
                    title="A",
                    methods=[
                        PlanMethodInput(
                            id="P0.m1",
                            items=[item_input("a"), item_input("b")],
                        ),
                        PlanMethodInput(id="P0.m2", items=[item_input("c")]),
                    ],
                )
            ],
            cfg=make_cfg(),
        )
        plan.phases[0].items[0].status = "done"

        updated, changed, _, ok = switch_method(plan, "P0")

        self.assertTrue(ok)
        self.assertEqual([item.id for item in updated.phases[0].items], ["a", "c"])
        self.assertEqual(updated.phases[0].items[0].status, "done")
        self.assertEqual(changed, ["c"])
        self.assertEqual(updated.phases[0].active_method_id, "P0.m2")
        self.assertEqual(updated.plan_version, 2)

    def test_switch_method_exhausted_returns_false(self):
        plan = build_plan(
            run_id="r",
            goal="g",
            tier=Tier.HD,
            phases=[
                PlanPhaseInput(
                    id="P0",
                    title="A",
                    methods=[
                        PlanMethodInput(id="P0.m1", items=[item_input("a")]),
                        PlanMethodInput(id="P0.m2", items=[item_input("b")]),
                    ],
                )
            ],
            cfg=make_cfg(),
        )
        plan.phases[0].tried_methods = ["P0.m1", "P0.m2"]

        _, _, _, ok = switch_method(plan, "P0")
        self.assertFalse(ok)

    def test_revise_reopens_done_item_and_downstream(self):
        plan = self._plan()
        for item in plan.phases[0].items:
            item.status = "done"

        updated, changed, _ = revise(plan, "b")

        statuses = {item.id: item.status for item in updated.phases[0].items}
        self.assertEqual(statuses, {"a": "done", "b": "pending", "c": "pending"})
        self.assertEqual(sorted(changed), ["b", "c"])
        self.assertEqual(updated.plan_version, plan.plan_version + 1)

    def test_revision_targets_skip_pending(self):
        plan = self._plan()
        plan.phases[0].items[0].status = "done"
        plan.phases[0].items[1].status = "done"
        self.assertEqual(revision_targets(plan, "b"), ["b"])


class VerdictParsingTest(unittest.TestCase):
    def test_parse_plain_json(self):
        from agent.tools.scheduler_tools import _parse_verdict

        verdict, evidence = _parse_verdict(
            '{"verdict": "passed", "reason": "ok", "evidence": ["a"]}'
        )
        self.assertEqual(verdict, "passed")
        self.assertEqual(evidence, ["a"])

    def test_parse_fenced_json(self):
        from agent.tools.scheduler_tools import _parse_verdict

        verdict, _ = _parse_verdict('```json\n{"verdict": "failed"}\n```')
        self.assertEqual(verdict, "failed")

    def test_parse_garbage_stays_unverified(self):
        from agent.tools.scheduler_tools import _parse_verdict

        verdict, evidence = _parse_verdict("模型没有按要求输出")
        self.assertEqual(verdict, "unverified")
        self.assertEqual(evidence, [])


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


class BrokenStore(FakeStore):
    """模拟落库失败，验证降级会被上报而不是静默吞掉。"""

    async def save_plan(self, plan):
        raise RuntimeError("db down")


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


    def test_irreversible_waits_for_approval(self):
        calls: list[str] = []

        async def executor(item, state):
            calls.append(item.id)
            return ItemOutcome(status="done")

        plan = self._plan(
            phases=[
                PlanPhase(
                    id="P0",
                    title="A",
                    items=[
                        PlanItem(id="a", phase_id="P0", title="a", irreversible=True)
                    ],
                )
            ]
        )
        plan.waves = [["a"]]
        state = self._state()
        result = asyncio.run(
            SchedulerRunner(cfg=make_cfg(), executor=executor).run(plan, state)
        )

        self.assertEqual(calls, [])
        self.assertEqual(state.status, "paused")
        self.assertEqual(plan.phases[0].items[0].status, "needs_hitl")
        self.assertFalse(result.ok)

    def test_approved_irreversible_executes(self):
        calls: list[str] = []

        async def executor(item, state):
            calls.append(item.id)
            return ItemOutcome(status="done", summary="ok")

        plan = self._plan(
            phases=[
                PlanPhase(
                    id="P0",
                    title="A",
                    items=[
                        PlanItem(
                            id="a",
                            phase_id="P0",
                            title="a",
                            irreversible=True,
                            approved=True,
                        )
                    ],
                )
            ]
        )
        plan.waves = [["a"]]
        result = asyncio.run(
            SchedulerRunner(cfg=make_cfg(), executor=executor).run(plan, self._state())
        )

        self.assertTrue(result.ok)
        self.assertEqual(calls, ["a"])

    def test_resume_resets_running_items(self):
        plan = self._plan()
        items = plan.phases[0].items
        items[0].status = "running"  # 模拟中断时正在执行
        items[1].status = "done"
        order: list[str] = []

        async def executor(item, state):
            order.append(item.id)
            return ItemOutcome(status="done")

        result = asyncio.run(
            SchedulerRunner(cfg=make_cfg(), executor=executor).run(
                plan, self._state()
            )
        )

        self.assertIn("a", result.done)
        self.assertNotIn("a", result.skipped)
        self.assertEqual(order, ["a", "c"])

    def test_context_ratio_written_to_d5(self):
        async def executor(item, state):
            return ItemOutcome(status="done", context_ratio=0.42)

        state = self._state()
        asyncio.run(
            SchedulerRunner(cfg=make_cfg(), executor=executor).run(
                self._plan(
                    phases=[
                        PlanPhase(
                            id="P0",
                            title="A",
                            items=[PlanItem(id="a", phase_id="P0", title="a")],
                        )
                    ]
                ),
                state,
            )
        )
        self.assertAlmostEqual(state.delta.d5_budget_ratio, 0.42)

    def test_d4_triggers_full_replan_and_is_consumed(self):
        async def executor(item, state):
            return ItemOutcome(status="done", summary="ok")

        plan = self._plan(
            phases=[
                PlanPhase(
                    id="P0",
                    title="A",
                    items=[
                        PlanItem(id="a", phase_id="P0", title="a", checkpoint=True),
                        PlanItem(
                            id="b", phase_id="P0", title="b", depends_on=["a"]
                        ),
                    ],
                )
            ]
        )
        plan.waves = [["a"], ["b"]]
        state = self._state()
        state.delta.d3_manual_intervention = True
        state.delta.d4_new_requirements = True

        result = asyncio.run(
            SchedulerRunner(
                cfg=make_cfg(replan_min_nodes=0), executor=executor
            ).run(plan, state)
        )

        self.assertEqual(result.replans, 1)
        self.assertGreater(result.plan_version, 1)
        self.assertFalse(state.delta.d4_new_requirements)
        self.assertTrue(result.ok)

    def test_mid_band_reorder_does_not_bump_version(self):
        async def executor(item, state):
            return ItemOutcome(status="done")

        plan = self._plan(
            phases=[
                PlanPhase(
                    id="P0",
                    title="A",
                    items=[
                        PlanItem(
                            id="a",
                            phase_id="P0",
                            title="a",
                            checkpoint=True,
                            impact="mid",
                            uncertainty="mid",
                        )
                    ],
                )
            ]
        )
        plan.waves = [["a"]]

        result = asyncio.run(
            SchedulerRunner(cfg=make_cfg(), executor=executor).run(
                plan, self._state()
            )
        )

        self.assertEqual(result.replans, 0)
        self.assertEqual(result.plan_version, 1)

    def test_facts_recorded_on_done(self):
        async def executor(item, state):
            return ItemOutcome(status="done", summary=f"{item.id} 产出")

        plan = self._plan(
            phases=[
                PlanPhase(
                    id="P0",
                    title="A",
                    items=[
                        PlanItem(
                            id="a",
                            phase_id="P0",
                            title="a",
                            acceptance="测试通过",
                            writes=["src/a.py"],
                        )
                    ],
                )
            ]
        )
        plan.waves = [["a"]]
        state = self._state()

        asyncio.run(
            SchedulerRunner(cfg=make_cfg(), executor=executor).run(plan, state)
        )

        self.assertEqual(len(state.facts), 1)
        fact = state.facts[0]
        self.assertEqual(fact.item_id, "a")
        self.assertEqual(fact.goal, "a")
        self.assertEqual(fact.output_ref, "run_test:a")
        self.assertEqual(fact.output_digest, "a 产出")
        self.assertEqual([entry.text for entry in fact.acceptance], ["测试通过"])
        self.assertEqual(fact.verdict, "unverified")
        self.assertEqual(fact.evidence, ["src/a.py"])
        self.assertFalse(fact.revised)
        # 运行结束后 state.summary 是统计摘要；事实视图由 facts 渲染
        self.assertIn("『a』a 产出", render_facts(state.facts))

    def test_render_facts_includes_acceptance_and_verdict(self):
        facts = [
            FactRecord(
                item_id="p0.1",
                output_digest="完成",
                acceptance="编译通过",
            )
        ]
        text = render_facts(facts)
        self.assertIn("『p0.1』完成", text)
        self.assertIn("验收：编译通过", text)
        self.assertIn("unverified", text)

    def test_upsert_fact_marks_revised_on_repeat(self):
        state = self._state()
        item = PlanItem(
            id="a",
            phase_id="P0",
            title="a",
            status="done",
            finished_at="2026-01-01T00:00:00+00:00",
        )
        _upsert_fact(state, item, ItemOutcome(status="done", summary="第一次"))
        _upsert_fact(state, item, ItemOutcome(status="done", summary="第二次"))

        self.assertEqual(len(state.facts), 2)
        self.assertEqual([fact.revised for fact in state.facts], [False, True])

    def test_carry_done_prefers_facts(self):
        from agent.tools.scheduler_tools import _carry_done

        old = SchedulePlan(
            run_id="r",
            goal="g",
            mode="dynamic",
            tier=Tier.HD,
            phases=[
                PlanPhase(
                    id="P0",
                    title="A",
                    items=[
                        PlanItem(
                            id="a",
                            phase_id="P0",
                            title="a",
                            status="done",
                            summary="旧摘要",
                        )
                    ],
                )
            ],
        )
        new = SchedulePlan(
            run_id="r",
            goal="g",
            mode="dynamic",
            tier=Tier.HD,
            phases=[
                PlanPhase(
                    id="P0",
                    title="A",
                    items=[PlanItem(id="a", phase_id="P0", title="a")],
                )
            ],
        )
        facts = [
            FactRecord(
                item_id="a",
                output_digest="新事实",
                finished_at="2026-01-01T00:00:00+00:00",
            )
        ]

        _carry_done(old, new, facts)

        item = new.phases[0].items[0]
        self.assertEqual(item.status, "done")
        self.assertEqual(item.summary, "新事实")

    def test_wrapup_skips_pending_phases(self):
        async def executor(item, state):
            return ItemOutcome(status="done")

        plan = SchedulePlan(
            run_id="run_test",
            goal="g",
            mode="dynamic",
            tier=Tier.MD,
            phases=[
                PlanPhase(
                    id="P0",
                    title="A",
                    items=[PlanItem(id="a", phase_id="P0", title="a")],
                )
            ],
            pending_phases=[
                PlanPhase(
                    id="P1",
                    title="B",
                    items=[PlanItem(id="b", phase_id="P1", title="b")],
                )
            ],
            waves=[["a"]],
        )
        state = self._state()
        state.delta.d5_budget_ratio = 0.9

        result = asyncio.run(
            SchedulerRunner(cfg=make_cfg(), executor=executor).run(plan, state)
        )

        self.assertEqual(result.replans, 0)
        self.assertEqual([phase.id for phase in plan.phases], ["P0"])
        self.assertTrue(plan.pending_phases)


    def _method_plan(self) -> SchedulePlan:
        return build_plan(
            run_id="run_test",
            goal="g",
            tier=Tier.HD,
            phases=[
                PlanPhaseInput(
                    id="P0",
                    title="A",
                    methods=[
                        PlanMethodInput(id="P0.m1", items=[item_input("a")]),
                        PlanMethodInput(id="P0.m2", items=[item_input("b")]),
                    ],
                )
            ],
            cfg=make_cfg(),
        )

    def test_failure_switches_to_next_method(self):
        plan = self._method_plan()

        async def executor(item, state):
            if item.id == "a":
                return ItemOutcome(status="failed", error="boom")
            return ItemOutcome(status="done", summary="ok")

        result = asyncio.run(
            SchedulerRunner(cfg=make_cfg(), executor=executor).run(
                plan, self._state()
            )
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.replans, 1)
        self.assertEqual(result.plan_version, 2)
        self.assertEqual(result.done, ["b"])

    def test_all_methods_failed_pauses(self):
        plan = self._method_plan()

        async def executor(item, state):
            return ItemOutcome(status="failed", error="boom")

        state = self._state()
        result = asyncio.run(
            SchedulerRunner(cfg=make_cfg(), executor=executor).run(plan, state)
        )

        self.assertEqual(result.status, "paused")
        self.assertEqual(state.status, "paused")
        self.assertFalse(result.ok)

    def test_throttle_blocks_checkpoint_replan(self):
        plan = self._plan(
            phases=[
                PlanPhase(
                    id="P0",
                    title="A",
                    items=[
                        PlanItem(
                            id="a",
                            phase_id="P0",
                            title="a",
                            checkpoint=True,
                            impact="high",
                            uncertainty="high",
                        ),
                        PlanItem(
                            id="b", phase_id="P0", title="b", depends_on=["a"]
                        ),
                    ],
                )
            ]
        )
        plan.waves = [["a"], ["b"]]

        async def executor(item, state):
            return ItemOutcome(status="done")

        result = asyncio.run(
            SchedulerRunner(cfg=make_cfg(), executor=executor).run(
                plan, self._state()
            )
        )

        self.assertEqual(result.replans, 0)
        self.assertEqual(result.plan_version, 1)

    def test_verification_failure_reopens_item(self):
        calls = {"n": 0}
        runs = {"n": 0}

        async def executor(item, state):
            runs["n"] += 1
            return ItemOutcome(status="done", summary=f"第{runs['n']}次产出")

        async def verifier(item, state, outcome):
            calls["n"] += 1
            if calls["n"] == 1:
                return "failed", ["验收条件未满足"]
            return "passed", ["已满足"]

        plan = self._plan(
            phases=[
                PlanPhase(
                    id="P0",
                    title="A",
                    items=[
                        PlanItem(
                            id="a",
                            phase_id="P0",
                            title="a",
                            acceptance="必须输出 ok",
                        )
                    ],
                )
            ]
        )
        plan.waves = [["a"]]
        state = self._state()

        events: list[dict] = []
        result = asyncio.run(
            SchedulerRunner(
                cfg=make_cfg(),
                executor=executor,
                verifier=verifier,
                emit=events.append,
            ).run(plan, state)
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.replans, 1)
        self.assertEqual(runs["n"], 2)
        self.assertEqual(len(state.facts), 2)
        self.assertEqual(state.facts[0].verdict, "failed")
        self.assertEqual(state.facts[1].verdict, "passed")
        self.assertTrue(state.facts[1].revised)
        self.assertEqual(result.done, ["a"])

        # 事实级快照：重开前保留旧摘要与旧事实
        self.assertEqual(len(state.revisions), 1)
        revision = state.revisions[0]
        self.assertEqual(revision.item_id, "a")
        self.assertEqual(revision.previous_summary, "第1次产出")
        self.assertEqual(revision.snapshot.verdict, "failed")
        self.assertTrue(
            any(event.get("event") == "revision_snapshot" for event in events)
        )


    def test_verifier_error_surfaces_warning(self):
        async def executor(item, state):
            return ItemOutcome(status="done", summary="产出")

        async def verifier(item, state, outcome):
            raise RuntimeError("verifier boom")

        events: list[dict] = []
        plan = self._plan(
            phases=[
                PlanPhase(
                    id="P0",
                    title="A",
                    items=[
                        PlanItem(
                            id="a",
                            phase_id="P0",
                            title="a",
                            acceptance="必须输出 ok",
                        )
                    ],
                )
            ]
        )
        plan.waves = [["a"]]
        state = self._state()

        result = asyncio.run(
            SchedulerRunner(
                cfg=make_cfg(),
                executor=executor,
                verifier=verifier,
                emit=events.append,
            ).run(plan, state)
        )

        self.assertTrue(result.ok)
        self.assertTrue(any("verify_failed" in item for item in state.warnings))
        self.assertTrue(
            any(event.get("event") == "scheduler_error" for event in events)
        )
        self.assertEqual(state.facts[0].verdict, "unverified")

    def test_persist_failure_surfaces_warning(self):
        async def executor(item, state):
            return ItemOutcome(status="done")

        events: list[dict] = []
        plan = self._plan()
        state = self._state()

        result = asyncio.run(
            SchedulerRunner(
                cfg=make_cfg(),
                executor=executor,
                store=BrokenStore(),
                emit=events.append,
            ).run(plan, state)
        )

        self.assertTrue(result.ok)
        self.assertTrue(any("persist_plan" in item for item in state.warnings))
        self.assertTrue(
            any(event.get("event") == "scheduler_error" for event in events)
        )
        self.assertTrue(any("persist_plan" in item for item in result.warnings))


    def test_checkpoint_event_uses_measured_dimensions(self):
        items = [PlanItem(id="a", phase_id="P0", title="a", checkpoint=True)]
        items += [
            PlanItem(
                id=f"n{index}",
                phase_id="P0",
                title=f"n{index}",
                depends_on=["a"] if index == 0 else [f"n{index - 1}"],
            )
            for index in range(5)
        ]
        plan = SchedulePlan(
            run_id="run_test",
            goal="g",
            mode="dynamic",
            tier=Tier.HD,
            phases=[PlanPhase(id="P0", title="A", items=items)],
            waves=[["a"]] + [[f"n{index}"] for index in range(5)],
        )

        async def executor(item, state):
            return ItemOutcome(status="done")

        events: list[dict] = []
        asyncio.run(
            SchedulerRunner(
                cfg=make_cfg(), executor=executor, emit=events.append
            ).run(
                plan,
                RunState(run_id="run_test", user_id="u", goal="g", tier=Tier.HD),
            )
        )

        checkpoints = [
            event for event in events if event.get("event") == "checkpoint"
        ]
        self.assertTrue(checkpoints)
        self.assertEqual(checkpoints[0]["impact"], "high")
        self.assertEqual(checkpoints[0]["uncertainty"], "mid")


class CheckpointMeasurementTest(unittest.TestCase):
    def _items(self, count: int) -> list[PlanItem]:
        return [
            PlanItem(
                id=f"n{index}",
                phase_id="P0",
                title=f"n{index}",
                depends_on=[f"n{index - 1}"] if index else [],
            )
            for index in range(count)
        ]

    def test_impact_counts_pending_descendants(self):
        from scheduler.checkpoint import measure_impact

        items = self._items(4)  # n0 -> n1 -> n2 -> n3
        self.assertEqual(measure_impact(items[0], items), "mid")
        self.assertEqual(measure_impact(items[-1], items), "low")

    def test_impact_high_with_many_descendants(self):
        from scheduler.checkpoint import measure_impact

        items = self._items(6)  # n0 有 5 个未完成下游
        self.assertEqual(measure_impact(items[0], items), "high")

    def test_uncertainty_from_attempts_and_verdict(self):
        from scheduler.checkpoint import measure_uncertainty

        item = PlanItem(id="a", phase_id="P0", title="a", status="done")
        self.assertEqual(measure_uncertainty(item, []), "mid")

        passed = [FactRecord(item_id="a", verdict="passed")]
        self.assertEqual(measure_uncertainty(item, passed), "low")

        retried = item.model_copy(update={"attempts": 2})
        self.assertEqual(measure_uncertainty(retried, passed, max_attempts=2), "high")

        failed = item.model_copy(update={"status": "failed"})
        self.assertEqual(measure_uncertainty(failed, [], max_attempts=2), "high")


class AcceptanceStructureTest(unittest.TestCase):
    def test_string_is_coerced_to_single_item(self):
        item = PlanItemInput(id="a", title="a", acceptance="编译通过")
        self.assertEqual([entry.text for entry in item.acceptance], ["编译通过"])

    def test_structured_check_survives_roundtrip(self):
        item = PlanItemInput(
            id="a",
            title="a",
            acceptance=[
                {
                    "text": "报告存在",
                    "check": {"kind": "file_exists", "target": "/workspace/out/report.md"},
                }
            ],
        )
        check = item.acceptance[0].check
        self.assertIsNotNone(check)
        self.assertEqual(check.kind, "file_exists")

    def test_legacy_fact_acceptance_still_loads(self):
        fact = FactRecord(item_id="a", acceptance="旧字符串")
        self.assertEqual([entry.text for entry in fact.acceptance], ["旧字符串"])


class AcceptanceCheckTest(unittest.TestCase):
    class _Response:
        def __init__(self, content=None, error=None):
            self.content = content
            self.error = error

    class _ExecuteResponse:
        def __init__(self, exit_code=0):
            self.exit_code = exit_code

    class _Backend:
        def __init__(self, files=None, exit_code=0):
            self.files = files or {}
            self.exit_code = exit_code
            self.executed: list[str] = []

        def download_files(self, paths):
            return [
                AcceptanceCheckTest._Response(
                    content=self.files.get(path).encode("utf-8")
                    if path in self.files
                    else None,
                    error=None if path in self.files else "file_not_found",
                )
                for path in paths
            ]

        def execute(self, command, timeout=None):
            self.executed.append(command)
            return AcceptanceCheckTest._ExecuteResponse(self.exit_code)

    def _run(self, check, backend):
        from agent.tools.scheduler_tools import _run_one_check

        return asyncio.run(_run_one_check(check, backend))

    def test_file_exists(self):
        backend = self._Backend({"/out/a.md": "x"})
        ok, _ = self._run(
            AcceptanceCheck(kind="file_exists", target="/out/a.md"), backend
        )
        self.assertTrue(ok)
        ok, _ = self._run(
            AcceptanceCheck(kind="file_exists", target="/out/missing.md"), backend
        )
        self.assertFalse(ok)

    def test_regex_and_field_equals(self):
        backend = self._Backend({"/out/s.json": '{"total": 42, "meta": {"ok": true}}'})
        ok, _ = self._run(
            AcceptanceCheck(kind="regex", target="/out/s.json", expect=r'"total":\s*42'),
            backend,
        )
        self.assertTrue(ok)
        ok, _ = self._run(
            AcceptanceCheck(
                kind="field_equals", target="/out/s.json", expect="meta.ok=True"
            ),
            backend,
        )
        self.assertTrue(ok)
        ok, _ = self._run(
            AcceptanceCheck(kind="field_equals", target="/out/s.json", expect="total=7"),
            backend,
        )
        self.assertFalse(ok)

    def test_command_exit0(self):
        ok, _ = self._run(
            AcceptanceCheck(kind="command_exit0", target="true"),
            self._Backend(exit_code=0),
        )
        self.assertTrue(ok)
        ok, _ = self._run(
            AcceptanceCheck(kind="command_exit0", target="false"),
            self._Backend(exit_code=1),
        )
        self.assertFalse(ok)

    def test_decide_by_checks(self):
        from agent.tools.scheduler_tools import _decide_by_checks

        checked = AcceptanceItem(
            text="文件存在",
            check=AcceptanceCheck(kind="file_exists", target="/out/a.md"),
        )
        text_only = AcceptanceItem(text="结论合理")
        self.assertEqual(_decide_by_checks([checked], [(True, "ok")]), "passed")
        self.assertEqual(_decide_by_checks([checked], [(False, "missing")]), "failed")
        self.assertIsNone(_decide_by_checks([checked, text_only], [(True, "ok")]))


if __name__ == "__main__":
    unittest.main()
