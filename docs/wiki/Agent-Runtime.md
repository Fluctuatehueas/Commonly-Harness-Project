# Agent Runtime

The runtime is assembled in `src/agent/main_agent.py`. That file is the composition root: it chooses the model, backend routes, tools, subagents, middleware order, and system-prompt policies. Almost every runtime behavior can be traced back to a decision made there.

## 1. Entry point

```python
async def create_main_agent(
    config, *, store, checkpointer, sandbox_backend,
    integration_server_id=None, agent_profile=None,
    agent_context=None, scheduler_enabled=True,
) -> Any
```

Inputs:

| Input | Meaning |
| :-- | :-- |
| `config.configurable.user_id` | Memory and sandbox isolation scope |
| `store` | PostgreSQL-backed long-term memory |
| `checkpointer` | LangGraph state persistence keyed by `thread_id` |
| `sandbox_backend` | User-level stable sandbox proxy; `None` means a no-sandbox conversation-only mode |
| `integration_server_id` | Binds the session to one customer MCP backend |
| `agent_profile` | MyAgent profile when no explicit agent context is supplied |
| `agent_context` | Normal or team context; team mode decides visible members and group prompts/models |

`src/api/agent_loader.py` caches the resulting graph per `user_id`. Different sessions of the same user share the graph and sandbox proxy but use different `thread_id` checkpoints.

## 2. Model selection

Model resolution is three-level inheritance:

```text
Agent profile  →  Agent group  →  User default
```

`context.resolve_model(model_settings(user_id))` returns the effective model. Two model objects are built:

- `model` — role `main`
- `summary_model` — role `summary`, used by compaction, memory updates, and evolution helpers

The effective context limit comes from the model settings or `context_limit_for(user_id)`. Model web search and thinking flags are read when the graph is built, so changing them triggers a graph rebuild via the settings API.

## 3. Backend routes

```text
CompositeBackend
├── /memories/  → StoreBackend (namespace = user_id)
├── /artifacts/ → StateBackend
├── /skills/    → ReadOnlyFilesystemBackend (only when no sandbox)
└── default     → ProjectWorkspaceBackend(sandbox_backend)
```

Consequences:

- Memory survives process restarts and sandbox loss.
- Large tool results can be offloaded to `/artifacts/` without polluting the project directory.
- Project files are readable and writable, but **commands always go to the sandbox**.
- Without a sandbox, skills are mounted read-only and execution tools are disabled or rejected.

## 4. Tools on the main agent

The main agent deliberately keeps only general-purpose capabilities. It does not receive every tool in the repository.

| Group | Source | Notes |
| :-- | :-- | :-- |
| MCP tools | `get_mcp_runtime(user_id).tools_for_main_agent(...)` | Lazy discovery at graph build time; no connections at process startup |
| Async subagent submission | `create_async_sandbox_tools` | Returns a task id immediately; only when a sandbox exists |
| RAG tools | `create_rag_tools()` | Always registered; empty result when no provider is configured |
| Basic tools | `get_basic_tools()` | Time, calculation, JSON helpers |
| Host tools | `build_host_tools()` | Disabled by default; each call pauses for HITL approval |
| Planning | `create_planning_tools()` | `create_work_plan` for parallel delegation waves |
| Scheduler | `create_scheduler_tools(...)` | Only when scheduling execution is enabled |
| Task flow | `create_task_flow_tools()` | Step reporting for task-flow runs |
| Build | `create_build_tools(sandbox_backend)` | Compile/test locally when runtime exists, otherwise in the sandbox |
| HITL | `request_additional_info`, `request_user_choice` | Interrupt-based clarification |
| Documents | `read_word_document` | Word document reader |
| Workspace | `publish_to_project`, `run_in_sandbox` | Artifact publishing and isolated execution |
| Skills | `create_skill_management_tools(...)` | Uses the stable sandbox proxy so it survives replacement |

`_ensure_unique_tool_names(main_tools)` fails graph construction on duplicate tool names. This is intentional: duplicate names make model tool selection ambiguous.

## 5. Middleware order

Middleware order is semantic. `before_*` hooks run in declaration order; `after_*` hooks run in reverse order. The order below mirrors `create_deep_agent`.

| # | Middleware | Responsibility |
| :-- | :-- | :-- |
| 1 | `ModelConcurrencyMiddleware` | Global model-call gate; limits concurrency so identical prefixes can reuse cache |
| 2 | `ContextInjectionMiddleware` | Injects uploaded context and attachments |
| 3 | `team_runtime_middleware(context)` | Team tool visibility and hard denial; empty for normal agents |
| 4 | `VisualizationMiddleware` | Output protocol for charts, diagrams, and images rendered by the frontend |
| 5 | `ExperienceInjectionMiddleware` | Injects approved evolution experience |
| 6 | `PdcGuardMiddleware(summary_model)` | PDCA guard behavior; disabled through `HARNESS_PDCA_ENABLED` |
| 7 | `ContextWindowMonitorMiddleWare` | Read-only context pressure logging |
| 8 | `TokenUsageMiddleware` | Accumulates final-answer token usage into the Store |
| 9 | `EvolutionMiddleware(summary_model)` | Post-run experience, attribution, and candidate orchestration |
| 10 | `TrajectoryMiddleware(context_limit=...)` | Persists run trajectory events |
| 11 | `VerificationPointMiddleware(...)` | Observes tool and delegation results (V1/V2) |
| 12 | `VerificationObserverMiddleware(...)` | Records verification evidence (V3) after the answer is released |
| 13 | `SandboxSkillsMiddleware(...)` | Pushes host skills into the sandbox before skill metadata is read |
| 14 | `build_agent_protection_middleware(...)` | Auto-summary, proactive compaction, model/tool run limits |
| 15 | `MemoryUpdateMiddleware(summary_model)` | Writes long-term user preferences during `after_agent` |
| 16 | `TimeAwarenessMiddleware` | Stamps message timestamps after compaction has rewritten the list |

Two ordering rules matter:

- Verification middleware must come after trajectory middleware so the current `run_id` exists.
- Time awareness must be innermost because compaction rewrites the message list, and timestamps must be applied to the final copy sent to the model.

## 6. Prompt policies

The system prompt is composed from a role card plus fixed policies. Role cards come from MyAgent profiles or team context; fixed policies are appended in `main_agent.py`:

| Policy block | Purpose |
| :-- | :-- |
| Web search policy | Whether and how to use search tools |
| Time policy | Call `get_current_time` instead of guessing |
| Integration policy | Restrict business calls to the bound integration backend |
| Execution policy | File vs sandbox rules, free vs project sessions, `publish_to_project`, host tools |
| Visualization policy | Self-contained HTML for charts; no large chart JSON in the reply |
| Parallel policy | `create_work_plan` then concurrent `task` calls in the same message |
| Team policy | For team leaders, delegation overrides "do it yourself" |
| Scheduler policy | When to call `scheduler_plan`; never redo completed items |
| Delegation instructions | Async subagent usage, when sandbox is enabled |

Do not move safety or environment constraints into a role card. They must stay after the role prompt so a custom style cannot bypass them.

## 7. Subagent loading

`_load_sync_subagents()` loads YAML declarations from `src/agent/subagents/configs/`:

`document_reader`, `intent_parsing`, `parallel_executor`, `web_researcher`, `vision_analyst`, `skill_curator`, `researcher`, `executor`, `reviewer`, `verifier_score`, `verifier_critic`, `verification_expert`, `scheduler_evaluator`, `replan_advisor`.

Each subagent gets:

- `ModelConcurrencyMiddleware` at the front
- `TimeAwarenessMiddleware(stamp_missing_human=True)`
- `SubagentProgressMiddleware(name)`

Permissions declared as YAML dictionaries are converted to `FilesystemPermission` objects before graph construction. YAML `optional_tools` means "bind if available, skip if not" and never blocks construction.

Team mode replaces the global subagent set with team members plus the group whitelist. Non-team mode injects all global YAML subagents.

## 8. No-sandbox mode

Passing `sandbox_backend=None` disables:

- Async subagent tools
- Skill synchronization and skill management tools
- Command execution
- Sandbox-backed build execution

It keeps:

- Chat and context injection
- Memory
- Project file read/write through the workspace backend
- Read-only skill mounting under `/skills/`

This mode is useful for pure conversation or for environments where no sandbox host exists yet.

## 9. Limits and tunables

| Variable | Default | Effect |
| :-- | :-- | :-- |
| `HARNESS_MAIN_AGENT_MODEL_RUN_LIMIT` | `0` (unlimited) | Main-agent model call cap |
| `HARNESS_MAIN_AGENT_TOOL_RUN_LIMIT` | `0` (unlimited) | Main-agent tool call cap |
| `HARNESS_SUBAGENT_MODEL_RUN_LIMIT` | `0` | Sync subagent model cap |
| `HARNESS_SUBAGENT_TOOL_RUN_LIMIT` | `0` | Sync subagent tool cap |
| `HARNESS_MODEL_REQUESTS_PER_SECOND` | `1` | Model request rate |
| `HARNESS_MODEL_MAX_RETRIES` | `5` | Model retry count |
| `HARNESS_RUNTIME_MAX_ATTEMPTS` | `3` | Runtime retry attempts |
| `HARNESS_HOST_ACCESS_ENABLED` | `false` | Enables host tools |

Set limits deliberately. An unlimited agent can loop; a too-tight limit truncates legitimate work. Verify the effect on a representative evaluation set rather than by feel.

## Next

- [[Context-Engineering]]
- [[Tools-and-MCP]]
- [[Subagents-and-Agent-Team]]
