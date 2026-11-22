# Troubleshooting and FAQ

Start with evidence, not guesses. The fastest diagnostic path is:

1. `GET /health`
2. `GET /settings/readiness`
3. The first error in `logs/`
4. `GET /trajectory/{thread_id}` for the failing turn

## 1. Startup

| Symptom | Likely cause | Fix |
| :-- | :-- | :-- |
| `error while attempting to bind on address` | Port in use | Change `HARNESS_API_PORT` or stop the occupying process |
| "user data directory is not writable" | Default data directory lacks permission | Set `HARNESS_USER_DATA_DIR` to a writable path |
| Backend exits immediately | Missing import, bad `.env`, or database failure | Read the first traceback in the log |
| `psycopg` event loop error on Windows | Proactor loop used | Ensure `main.py` runs the selector policy before importing `api.chat` |
| Tables missing | Database unreachable at first use | Check `DB_HOST` / `DB_USER` / `DB_PASSWORD` / `DB_NAME` |

## 2. Database

| Symptom | Likely cause | Fix |
| :-- | :-- | :-- |
| Readiness reports database failure | Host, port, or credentials wrong | Test with `psql` from the backend host |
| Connection refused | PostgreSQL not running or bound to localhost only | Start it; check `listen_addresses` |
| Authentication failed | Password or user mismatch | Update `.env`; restart |
| Disk full | Trajectory and checkpoints grow | Clean old sessions; monitor disk |
| Slow history | Large checkpoint or missing index | Inspect query plans; archive old sessions |

## 3. Model

| Symptom | Likely cause | Fix |
| :-- | :-- | :-- |
| "LLM not configured" | Empty `DEEPSEEK_API_KEY` | Fill it and save via settings, or restart |
| `DEEPSEEK_API_KEY not configured` at chat time | Key missing at call time | Update `.env` or `PUT /settings/model` |
| 401 / 403 from model | Invalid key or base URL | Verify key and `DEEPSEEK_BASE_URL` |
| Rate limit errors | Concurrency or quota | Lower `HARNESS_MODEL_REQUESTS_PER_SECOND`; add retries |
| Timeouts | Endpoint slow or thinking mode expensive | Increase timeout; disable thinking for the case |
| Model ignores tools | Tool descriptions or schema unclear | Rewrite descriptions; tighten `inputSchema` |

## 4. Sandbox

| Symptom | Likely cause | Fix |
| :-- | :-- | :-- |
| Sandbox creation fails | Server down, key mismatch, clock skew | Check server, key, and time |
| Connection timeout (not 401) | Firewall or security group blocks the port | Open `OPEN_SANDBOX_PORT`; verify `0.0.0.0` listening |
| 401 from sandbox | `api_key` differs from `.env` | Align `sandbox.toml` and `.env` |
| Command times out | Command too long or default timeout too low | Chunk work; raise timeout deliberately |
| Files disappear later | Sandbox TTL expired | Publish artifacts before TTL |
| Sandbox dies mid-run | Instance crash or host resource pressure | Proxy replacement should recover; retry the step |
| `run_in_sandbox` rejected | No sandbox configured | Check readiness and sandbox settings |

Diagnostics:

```bash
ss -lntp | grep 18083
curl http://<sandbox-host>:18083/health
```

```powershell
Test-NetConnection <sandbox-host> -Port 18083
```

## 5. MCP

| Symptom | Likely cause | Fix |
| :-- | :-- | :-- |
| Tools not visible | Server disabled, wrong scope, or discovery failed | `GET /mcp/servers/{id}/tools`; probe; check logs |
| Probe succeeds but calls fail | Tool schema or auth issue | Test one tool via `/tools/{tool}/test` |
| One server breaks others | Unhandled server error | Check runtime isolation; restart the server |
| Model never uses a tool | Description too vague | State purpose, when to use, and side effects |
| Wrong tool selected | Overlapping descriptions | Merge tools or disambiguate descriptions |
| Integration tools leak across tenants | Wrong `integration_server_id` binding | Verify session binding and scoping |
| Tool result too large | No truncation | Truncate or offload to `/artifacts/` |

## 6. Context and compaction

| Symptom | Likely cause | Fix |
| :-- | :-- | :-- |
| Context overflow errors | Tool outputs too large | Truncate, offload, reduce attachments |
| Compaction never runs | `MIN_ROUNDS` not reached or switch off | Check `HARNESS_CONTEXT_COMPACT_*` |
| Compaction runs too often | Trigger too low | Raise `TRIGGER`/`HARD`; increase `KEEP` |
| Summary loses task state | Anchors missing | Anchor task state and constraints |
| Latency spikes at compaction | Summary call slow | Lower timeout? No—verify model latency; reduce context |
| Answers degrade after compaction | Aggressive summarization | Raise `KEEP`; verify with eval set |

Compaction is a trade-off. More compression saves tokens but loses facts. Validate against an eval set, not by feel.

## 7. Delegation

| Symptom | Likely cause | Fix |
| :-- | :-- | :-- |
| Leader does everything itself | Task not in member scope, or team not active | Inspect `agent_graph_built` log; check member list and `task_tool` |
| Subagent not available | YAML failed validation or name duplicate | Read the construction error |
| Subagent ignores its tools | Tools not bound or optional | Check `tools` vs `optional_tools`; verify runtime availability |
| Parallel work runs serially | Multiple `task` calls were not in one message | Adjust the plan/prompt; verify waves |
| Async task never finishes | Agent Protocol service down or task failed | `GET /async-tasks/{task_id}`; check service |

## 8. Scheduler and task flows

| Symptom | Likely cause | Fix |
| :-- | :-- | :-- |
| Scheduler tools missing | `HARNESS_SCHEDULER_EXEC=false` or subsystem disabled | Check both flags |
| Run stays created | Execution not started or service restarted | Inspect `/scheduler/runs/{id}` and events |
| Repeated replanning | Threshold too low or plan too vague | Raise `p_replan`; improve item boundaries |
| Completed items redone | Prompt or replan scope wrong | Check replan scope and prompt policy |
| Flow never matches | Trigger examples weak or flow disabled | Add examples; publish and enable |
| Step marked degraded | Step completed with missing capability | Check tool availability and constraints |

## 9. Files and workspace

| Symptom | Likely cause | Fix |
| :-- | :-- | :-- |
| "File not found" in a free session | Absolute `/workspace/...` used instead of virtual path | Use virtual paths like `/notes.md` |
| File does not appear in the drawer | Written outside the session virtual root | Write under `/` |
| `publish_to_project` rejected | Free session or no project | Use a project session |
| Path validation fails | Root outside managed workspace | Register a project under the allowed root |
| Cannot execute project file | Host execution is forbidden | Stage files into the sandbox |
| `.commonharness` read/write denied | Reserved directory | Do not write into it |

## 10. Frontend

| Symptom | Likely cause | Fix |
| :-- | :-- | :-- |
| "Backend unreachable" | Backend down or wrong API base | Check `VITE_API_BASE_URL` and `/health` |
| CORS error | Origin missing from `HARNESS_CORS_ORIGINS` | Add the exact origin |
| SSE tokens arrive all at once | Proxy buffering | Disable buffering for `/chat/stream` |
| Blank page after deploy | SPA route fallback missing | Configure `try_files ... /index.html` |
| User sees another user's data | Identity header not isolated | Verify `X-User-Id` handling and gateway injection |
| Readiness modal loops | Missing LLM or sandbox configuration | Complete system settings; re-run readiness |

## 11. Sessions and recovery

| Symptom | Likely cause | Fix |
| :-- | :-- | :-- |
| Resume does nothing | No pending interrupt | Check the returned interrupt payload |
| Duplicate side effects after retry | Tool lacks idempotency | Add `idempotency_key` |
| History missing messages | Wrong `thread_id` or deleted session | Check session list; inspect checkpoint |
| Session mutex fails across workers | Multi-process deployment | Run a single process; design a distributed lock |
| Archived session not listed | Archive filter | Query archived sessions explicitly |

## 12. Verification and evolution

| Symptom | Likely cause | Fix |
| :-- | :-- | :-- |
| High judge cost | L1/L2 enabled broadly | Lower sample rate; restrict points |
| Blocking false positives | Threshold too strict | Raise `VERIFICATION_BLOCKING_SCORE`; inspect evidence |
| Experience pollutes answers | Bad admission or stale entries | Quarantine; lower TTL; review injection |
| Candidate never applies | Requires approval | Approve through the API/console |
| Metrics regress after release | Bad candidate | Roll back the release |

## 13. Where to look next

| Question | Answer |
| :-- | :-- |
| What exactly failed? | `/trajectory/{thread_id}`, first log error |
| Was it a tool or the model? | Tool call events and arguments |
| Was the context too long? | Context monitor logs and token usage |
| Was the sandbox involved? | Sandbox status and tool result |
| Was a policy violated? | Verification evidence and audit log |
| Was it a configuration change? | Settings endpoints and recent `.env` edits |

## 14. FAQ

**Can I run multiple Uvicorn workers?**
Not safely today. Session mutex and some caches are in-process.

**Can I use a local sandbox instead of a Linux host?**
The supported path is an OpenSandbox server. Windows and Docker Desktop require the server proxy. See [[Sandbox]].

**Does Harness store my business data?**
No. It stores session state, memory, trajectory, and files the user explicitly writes. Business data should stay in customer systems.

**Can I use OpenAI instead of DeepSeek?**
The model layer is built around a DeepSeek-compatible endpoint. Swapping providers requires verifying model construction, tool calling, and message formatting.

**Why does the model not use my new tool?**
Usually the description is too vague or the tool is not in the selected scope. Check the tool list in the trajectory, then rewrite the description.

**Why did compaction lose an important fact?**
The fact was not anchored. Anchor task state and constraints; increase `KEEP`; verify on an eval case.

**Is a task flow the same as a scheduler plan?**
No. A task flow is a reusable definition; a scheduler plan is generated for one run. See [[Scheduling-and-Task-Flows]].

## 15. When to stop debugging and roll back

Roll back when:

- A configuration change caused a widespread failure.
- Compaction or scheduling changes degrade answer quality across cases.
- A new MCP server destabilizes unrelated tools.
- A release candidate regresses eval metrics.

Restore the previous code and configuration, take a backup, then reproduce in a non-production environment before trying again.

## Next

- [[Getting-Started]] for a clean setup
- [[Deployment-and-Operations]] for production checks
- [[Development-Guide]] for debugging tools
