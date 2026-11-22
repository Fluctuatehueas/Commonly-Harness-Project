# Scheduling and Task Flows

Two related but distinct mechanisms:

- **Scheduler** — decides how much planning a task needs, builds a phased plan, executes waves, scores checkpoints, and replans.
- **Task flow** — a reusable, versioned workflow definition that can be compiled, published, forked, and executed.

Neither replaces the normal agent loop. They are opt-in tools for work that benefits from explicit structure.

## 1. Scheduler tiers

Code: `src/scheduler/`

| File | Responsibility |
| :-- | :-- |
| `tiering.py` | Compute features and decide EZ / MD / HD |
| `features.py` | Feature extraction |
| `planner.py` | Build the two-level plan |
| `runner.py` | Execute waves and checkpoints |
| `checkpoint.py` | Score progress |
| `replanner.py` | Reorder / subtree / full replanning |
| `store.py` | Persist runs, items, events |
| `events.py` | Event stream |
| `shadow.py` | Shadow evaluation records |
| `config.py` | Thresholds and execution parameters |

Tiers:

| Tier | Meaning | Planning style |
| :-- | :-- | :-- |
| `EZ` | Simple, single-step | No scheduler plan |
| `MD` | Medium, multi-stage | Provide the first two phases; later phases expand at checkpoints |
| `HD` | Hard, cross-file, high impact | Provide the full phase table |

Tiering uses seven normalized features: `complexity`, `uncertainty`, `impact`, `scale`, `dep_depth`, `tool_diversity`, `acceptance`. Missing features are recorded explicitly; the model does not silently invent them.

Shadow mode writes only hashes, lengths, and a short preview to `.harness/scheduler_shadow.jsonl`; it never stores full user text and never changes execution.

## 2. Planning model

A plan is a two-level table:

```text
Phase P0
  ├─ item A: depends_on [], read [src/a], write [src/a]
  └─ item B: depends_on [A], read [src/a], write [src/b]
Phase P1
  └─ item C: depends_on [B], ...
```

Each item declares:

- Owner (`main` or a subagent name)
- Dependencies
- Read and write paths
- Completion criteria

The planner returns **waves**: items in the same wave have no dependency and no write conflict, so they can run concurrently.

## 3. Execution parameters

Defaults in `scheduler/config.py`:

| Parameter | Default | Meaning |
| :-- | :-- | :-- |
| `max_items` | `32` | Maximum planned items |
| `max_parallel` | `1` | Concurrent item execution |
| `max_replan` | `3` | Replan budget |
| `max_attempts` | `2` | Attempts per item |
| `node_timeout_sec` | `900` | Per-item timeout |
| `checkpoint_every` | `3` | Checkpoint interval |
| `p_replan` | `0.70` | Score at or above this triggers replan |
| `p_reorder` | `0.45` | Score between this and `p_replan` allows reordering only |
| `wrapup_ratio` | `0.80` | Stop expanding new phases at this context pressure |
| `phase_replan_limit` | `1` | Replans per phase |
| `replan_min_nodes` | `2` | Minimum completed nodes between replans |

Switches:

- `HARNESS_SCHEDULER_ENABLED` — enables the scheduler subsystem
- `HARNESS_SCHEDULER_EXEC` — enables execution tools; when false, planning may exist but execution tools are not registered

## 4. Checkpoints and replanning

At each checkpoint the runner scores progress and chooses:

| Score range | Action |
| :-- | :-- |
| `< p_reorder` | Continue |
| `p_reorder .. p_replan` | Reorder remaining work only |
| `>= p_replan` | Replan |

Replan scopes:

- `reorder` — same items, different order
- `subtree` — replace the remaining dependent subtree
- `full` — rebuild the plan

Replanning is throttled by `phase_replan_limit` and `replan_min_nodes` to avoid thrash. Completed items are never redone by the scheduler; the prompt explicitly forbids it.

## 5. Scheduler API

| Method | Path | Purpose |
| :-- | :-- | :-- |
| `GET` | `/scheduler/runs` | List runs |
| `POST` | `/scheduler/runs/{run_id}/cancel` | Cancel a run |
| `GET` | `/scheduler/runs/{run_id}/events` | Event stream |

`scheduler_plan` returns a `run_id` immediately and does not mean the task is finished. Progress arrives through the event stream; the terminal summary is written back to the session.

## 6. When to use the scheduler

Use it when:

- The task spans multiple stages or files
- Checkpoints and recovery matter
- The work may need replanning
- You need an auditable run record

Do not use it when:

- The task is a single step
- The plan is obvious and short
- Latency matters more than structure

Building a scheduler plan for a one-line request is pure overhead and increases failure surface.

## 7. Task flows

Code: `src/task_flow/`

| File | Responsibility |
| :-- | :-- |
| `models.py` | Schema v1, plan, execution records |
| `parser.py` | Parse flow definitions |
| `compiler.py` | Compile steps into an execution plan |
| `validators/` | Structural and safety validation |
| `router.py` | Match a request to a flow |
| `injection.py` | Inject flow instructions into the agent |
| `store.py` | Persist definitions and executions |
| `agent_bridge.py` | Bridge to the agent runtime |
| `indexer.py` | Flow indexing |
| `builtin/` | Built-in flows |

A flow contains:

- `schema_version`
- `id`, `name`, `description`
- `trigger_examples`
- `mcp_services`
- `steps`

A step contains:

| Field | Meaning |
| :-- | :-- |
| `id`, `name` | Identity |
| `instruction` | What to do |
| `skills` | `auto` or explicit skill names |
| `executor` | `auto`, `main`, `subagent:<name>`, or `agent:<id>` |
| `constraints` | Step constraints |
| `checkpoint` | Whether to pause or score here |
| `example` | Optional example |
| `parallel` | Subflows that fork |

Compilation turns steps and forks into `ExecutionUnit` records with a sequence number and a join policy (`all` for forks).

## 8. Task flow lifecycle

| Stage | Action |
| :-- | :-- |
| Draft | Create or import JSON |
| Validate | Structural and safety checks |
| Publish | Make available to routing |
| Enable / disable | Control whether it can be matched |
| Fork | Copy a built-in or existing flow |
| Execute | Run and record step traces |
| Replay | Re-run for comparison |

Execution statuses: `running`, `done`, `failed`, `aborted`. Step trace statuses: `done`, `failed`, `degraded`, `skipped`.

## 9. Task flow API

| Method | Path | Purpose |
| :-- | :-- | :-- |
| `GET` | `/task-flows` | List flows |
| `POST` | `/task-flows` | Create |
| `GET` | `/task-flows/{flow_id}` | Detail |
| `PUT` | `/task-flows/{flow_id}` | Update |
| `DELETE` | `/task-flows/{flow_id}` | Delete |
| `POST` | `/task-flows/{flow_id}/publish` | Publish |
| `PATCH` | `/task-flows/{flow_id}/enabled` | Enable or disable |
| `POST` | `/task-flows/{flow_id}/fork` | Fork |
| `GET` | `/task-flows/{flow_id}/executions` | List executions |
| `GET` | `/task-flows/executions/{execution_id}` | Execution detail |

## 10. Current limitations

- The scheduler is **not HTN**. It is a two-level task table with a dependency DAG, topological waves, and checkpoint scoring. There is no method layer and no `(S, R, D)` closed loop.
- `max_parallel` defaults to `1`; raising it requires verifying tool and sandbox concurrency behavior.
- Task flow execution is agent-mediated; steps are instructions, not deterministic function calls. Do not use a flow where a deterministic script is required.
- Flow matching depends on trigger examples and routing; it is not a formal intent classifier.

Design details and open items are in `docs/core_design/任务调度模块设计.md` and `docs/core_design/任务流模块设计.md`.

## Next

- [[Agent-Runtime]] for planning tools
- [[Subagents-and-Agent-Team]] for delegation
- [[Verification-and-Evolution]] for acceptance checks
