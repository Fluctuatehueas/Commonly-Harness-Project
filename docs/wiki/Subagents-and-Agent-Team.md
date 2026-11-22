# Subagents and Agent Team

There are two ways to split work:

1. **Subagents** — declared in YAML, invoked through the `task` tool, each with its own prompt, tools, model, and skills.
2. **Agent team** — a group of agents with a leader, member roster, group prompt, group model, and visibility rules.

Both use the same underlying subagent mechanism; the team mode adds assembly and delegation policy.

## 1. Sync subagents

Configuration files live in `src/agent/subagents/configs/`. The loader is `src/agent/subagents/loader.py`.

Required keys:

| Key | Meaning |
| :-- | :-- |
| `name` | Unique subagent name; used by `task` |
| `description` | When to use it; this is what the main agent reads |
| `system_prompt` | Role and boundaries |
| `tools` | Tools that must exist, or construction fails |

Optional keys:

| Key | Meaning |
| :-- | :-- |
| `optional_tools` | Bind if available, skip if not |
| `mcp_servers` | MCP servers whose tools this subagent may use |
| `model` | `main` or `vision` alias |
| `skills` | Skill directories visible to the subagent |
| `middleware` | Additional middleware |
| `interrupt_on` | Tool-level HITL triggers |
| `permissions` | Filesystem permissions converted to `FilesystemPermission` |
| `deny_tools` | Tool-level denylist, used for read-only subagents |

A minimal example:

```yaml
name: researcher

description: |
  Research subagent for external facts and source comparison.
  Use for parallel searches and evidence gathering.
  Do not use for code changes or command execution.

tools:
  - get_current_time
  - format_json
  - calculate

optional_tools:
  - search_web
  - fetch_web
  - read_word_document

system_prompt: |
  You are a researcher. Return verifiable facts with sources.
  If a fact cannot be verified, say so. Do not modify code or run commands.
```

Rules the loader enforces:

- `tools` and `optional_tools` cannot overlap.
- `mcp_servers` must be a de-duplicated string list.
- Declared mandatory tools must be available; optional tools never block construction.
- YAML is read as `utf-8-sig` to tolerate a Windows BOM.

## 2. Built-in subagents

| Name | Purpose |
| :-- | :-- |
| `document_reader` | Read and extract documents |
| `intent_parsing` | Normalize ambiguous user intent |
| `parallel_executor` | Execute independent work items concurrently |
| `web_researcher` | Web search and source gathering |
| `vision_analyst` | Image understanding |
| `skill_curator` | Skill discovery and curation |
| `researcher` | General research with evidence |
| `executor` | Code and file changes |
| `reviewer` | Review and critique |
| `verifier_score` | Score a result |
| `verifier_critic` | Critique a result |
| `verification_expert` | Verification specialist used by the scheduler |
| `scheduler_evaluator` | Evaluate scheduler checkpoints |
| `replan_advisor` | Advise on replanning scope |

The list is assembled in `SYNC_SUBAGENT_CONFIG_FILES` in `src/agent/main_agent.py`.

## 3. Middleware automatically added

Every sync subagent receives, in this order:

1. `ModelConcurrencyMiddleware` at the front
2. `TimeAwarenessMiddleware(stamp_missing_human=True)`
3. `SubagentProgressMiddleware(name)`

The concurrency middleware is outermost so the subagent reserves a model slot before making a request, avoiding a cache-write race with identical prefixes. Progress middleware makes concurrent delegation observable per `run_id`.

## 4. Async subagents

Async subagents run as a separate service through the Agent Protocol. They are declared in `langgraph.json`:

```json
{
  "graphs": {
    "async_subagent": "./src/agent/subagents/async_entry.py:async_subagent_graph"
  }
}
```

Key points:

- The async graph is **not embedded** in the main graph.
- The main agent gets a submission tool that returns a `task_id` immediately.
- Results are polled through `/async-tasks`.
- `HARNESS_ASYNC_AGENT_PROTOCOL_URL` points to the service.
- Async tools exist only when a sandbox is available, because they operate on sandbox state.

Use async subagents for long-running work that should not block the conversation. Use sync subagents for work the next step depends on.

## 5. Delegation policy

The main prompt contains a parallel policy:

1. Call `create_work_plan(goal, tasks)` for multi-step or independent work.
2. Execute each returned wave by issuing **multiple `task` calls in the same message**.
3. Respect `depends_on`; move write conflicts to different waves.
4. Do not repeat work a subagent is already doing.
5. Include goal, inputs, expected output, and prohibitions in every delegation.

A single `task` call waits for that subagent to finish. Parallelism therefore comes from multiple calls in one message, not from issuing calls sequentially.

## 6. Agent team

Team mode is selected through the agent context:

- `AgentMode.TEAM`
- `TeamAgentContext`
- `build_team_subagents(...)`
- `team_runtime_middleware(context)`

What changes in team mode:

| Area | Behavior |
| :-- | :-- |
| Subagent set | Team members plus the group whitelist; other global YAML is not injected |
| Team tools | `create_team_tools(context, deps)` adds recruitment and delegation tools |
| Prompt | A delegation policy overrides "do it yourself" for member-owned work |
| Visibility | Members outside the group are hidden and hard-denied |
| Models | Agent → group → user default inheritance |

The leader is expected to split work by member responsibility, delegate, and integrate results. It should only do work that no member covers.

Team assembly includes a log line:

```text
agent_graph_built agent=... mode=team group=... subagents=[...] task_tool=True
```

This is the first place to look when delegation fails: an empty member list or `task_tool=False` explains most "the leader did everything itself" symptoms.

## 7. Agent profiles and MyAgent

A MyAgent profile defines:

- Prompt / role card
- Model selection
- MCP server bindings
- Team membership and group affiliation

Profiles are stored in PostgreSQL and exposed through:

| Method | Path |
| :-- | :-- |
| `GET` | `/agents` |
| `GET` | `/agents/{agent_id}` |
| `POST` | `/agents` |
| `PUT` | `/agents/{agent_id}` |
| `DELETE` | `/agents/{agent_id}` |
| `GET` | `/agents/{agent_id}/groups` |
| `GET` | `/agent-groups` |
| `GET` | `/agent-groups/{group_id}` |
| `POST` | `/agent-groups` |
| `PUT` | `/agent-groups/{group_id}` |
| `DELETE` | `/agent-groups/{group_id}` |
| `POST` | `/agent-groups/{group_id}/members` |
| `DELETE` | `/agent-groups/{group_id}/members/{agent_id}` |
| `PUT` | `/agent-groups/{group_id}/leader` |
| `DELETE` | `/agent-groups/{group_id}/leader` |

## 8. Choosing a decomposition

| Situation | Use |
| :-- | :-- |
| One small task | Do it in the main agent |
| Several independent research directions | Multiple `researcher` sync instances in one message |
| Long-running independent job | Async subagent |
| Specialist with a narrow tool set | Dedicated YAML subagent |
| Persistent role with its own model and MCP bindings | MyAgent profile |
| Multi-role workflows with a leader | Agent team |

Do not create a subagent for a one-time operation. Every subagent adds prompt tokens, tool schema tokens, and a coordination failure mode.

## 9. Checklist for a new subagent

1. Is there a real boundary, or is this just a step?
2. Does the description state both when to use and when not to use it?
3. Are tools minimal? Prefer `optional_tools` for capabilities that may be absent.
4. Does it need write access, or can it be read-only with `deny_tools`?
5. Does it need a different model? If not, leave `model` unset.
6. Is the name unique across all declared subagents?
7. Add it to `SYNC_SUBAGENT_CONFIG_FILES` if it should be globally available.
8. Test it with a deterministic prompt before exposing it to users.

## Next

- [[Tools-and-MCP]] for tool contracts
- [[Scheduling-and-Task-Flows]] for work that needs checkpoints
- [[Agent-Runtime]] for middleware order
