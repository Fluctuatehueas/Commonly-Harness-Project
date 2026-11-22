# Context Engineering

Context is the most expensive and least stable resource in an agent runtime. This project treats it as an engineering problem with explicit budgets, thresholds, and evaluation.

Code lives in:

- `src/agent/context/` — anchors, budget, evaluator, autotune
- `src/agent/middlewares/context_injection.py`
- `src/agent/middlewares/context_window_monitor.py`
- `src/agent/middlewares/context_compaction.py`
- `src/api/context.py` — attachment upload and resolution
- `src/api/settings.py` — context settings

## 1. Inputs that consume context

| Input | Source | Control |
| :-- | :-- | :-- |
| System prompt + policies | Role card and `main_agent.py` | Static, but keep it small |
| Conversation history | Checkpoint | Compaction |
| Uploaded attachments | `POST /context/upload` | Size and count limits |
| URL imports | `POST /context/url` | Character limit |
| Tool results | Tool calls | Offloading to `/artifacts/`, truncation |
| Skills | Skill metadata and progressive disclosure | Only metadata is loaded first |
| Memory and experience | Store injection | Bounded excerpts |

## 2. Attachments

Endpoints:

| Method | Path | Purpose |
| :-- | :-- | :-- |
| `GET` | `/context/formats` | Supported formats |
| `POST` | `/context/upload` | Upload a file |
| `POST` | `/context/url` | Import from a URL |
| `DELETE` | `/context/{file_id}` | Delete an uploaded file |
| `POST` | `/context/resolve` | Resolve attachments for a request |

Limits from `.env`:

| Variable | Typical value | Meaning |
| :-- | :-- | :-- |
| `HARNESS_CONTEXT_MAX_MB` | `20` | Per-file size |
| `HARNESS_CONTEXT_MAX_FILES` | `6` | Files per request |
| `HARNESS_CONTEXT_MAX_CHARS` | `30000` | Extracted text per request |

Supported formats are declared in `src/api/context.py` (`SUPPORTED_FORMATS`). Extraction uses `python-docx`, `pypdf`, and text readers. The design intent is to extract text and structure, not to keep binary blobs in the prompt.

## 3. Context window monitoring

`ContextWindowMonitorMiddleWare` is read-only. It logs context pressure and emits metrics used by the trajectory and the frontend. It does not rewrite messages.

The effective limit comes from:

```text
model.context_limit  →  CONTEXT_LIMIT_FOR_USER  →  HARNESS_MODEL_CONTEXT_LIMIT
```

Warning threshold is `HARNESS_CONTEXT_ALERT_THRESHOLD` (default `0.70`). Crossing it is a signal to compact or to reduce tool output, not a reason to silently drop messages.

## 4. Compaction pipeline

Compaction is implemented by `build_agent_protection_middleware(...)`, which composes:

1. Automatic summarization of older turns
2. Proactive compaction at thresholds
3. Model and tool run limits

Thresholds:

| Variable | Default | Meaning |
| :-- | :-- | :-- |
| `HARNESS_CONTEXT_COMPACT_ENABLED` | `true` | Master switch |
| `HARNESS_CONTEXT_COMPACT_TRIGGER` | `0.70` | Soft trigger |
| `HARNESS_CONTEXT_COMPACT_HARD` | `0.88` | Hard trigger |
| `HARNESS_CONTEXT_COMPACT_KEEP` | `0.25`–`0.30` | Fraction of recent context to keep |
| `HARNESS_CONTEXT_COMPACT_MIN_ROUNDS` | `6` | Minimum rounds before compacting |
| `HARNESS_CONTEXT_COMPACT_COOLDOWN` | `3` | Rounds to wait after a compaction |
| `HARNESS_CONTEXT_COMPACT_MAX_DEFER` | `3` | Maximum deferrals before forcing |
| `HARNESS_CONTEXT_COMPACT_TIMEOUT` | `60` | Summary call timeout (seconds) |
| `HARNESS_CONTEXT_COMPACT_HEARTBEAT` | `5` | Progress heartbeat interval |

Design rules:

- Never compact before `MIN_ROUNDS`; short conversations should not be summarized.
- Prefer deferral over compaction when pressure is borderline, but cap deferrals.
- Keep recent rounds verbatim; summarize the older prefix.
- A compaction timeout must degrade gracefully, not lose the run.

## 5. Compaction evaluation

`CONTEXT_COMPACT_EVAL_ENABLED` turns on pre/post compaction evaluation:

| Variable | Default | Meaning |
| :-- | :-- | :-- |
| `HARNESS_CONTEXT_COMPACT_EVAL_ENABLED` | `true` | Evaluate compaction quality |
| `HARNESS_CONTEXT_EVAL_JUDGE` | `false` | Use an LLM judge |
| `HARNESS_CONTEXT_AUTOTUNE_ENABLED` | `true` | Online threshold tuning |
| `HARNESS_CONTEXT_AUTOTUNE_INTERVAL` | `20` | Tuning interval |
| `HARNESS_COMPACTION_SIGNING_KEY` | empty | Sign compaction artifacts for integrity |

`src/agent/context/evaluator.py` computes compaction quality; `src/agent/context/autotune.py` adjusts thresholds within safe bounds. Autotune is deliberately conservative: it changes thresholds, not the compaction algorithm, and it can be disabled.

If an LLM judge is enabled, judge cost is part of the runtime budget. Keep `EVAL_JUDGE=false` in high-throughput development loops.

## 6. Anchors and budget

`src/agent/context/anchors.py` marks stable content that should survive compaction (task state, constraints, user instructions). `budget.py` computes token budgets for sections.

The practical rule: anchors are for facts the agent must not forget, not for "important-looking" prose. Over-anchoring defeats compaction.

## 7. Tool output discipline

Tool results are the usual cause of context blow-up. The runtime handles them in three ways:

1. **Offload** large results to `/artifacts/` and keep a reference in context.
2. **Truncate** at a configured maximum, marking truncation.
3. **Summarize** via the summary model when a result is needed but too large.

The system prompt also instructs the model to write large datasets and charts to a self-contained HTML file instead of pasting JSON into the reply. This is a context decision, not only a UX decision.

## 8. Settings API

| Method | Path | Purpose |
| :-- | :-- | :-- |
| `GET` | `/settings/context` | Read current context settings |
| `PUT` | `/settings/context` | Update thresholds |

Updates are persisted and applied on the next graph build or run, depending on the field. See [[API-Reference]].

## 9. How to validate a context change

Do not tune thresholds by feel. Use this loop:

1. Build or reuse a small eval set of representative conversations (long tool outputs, multi-turn tasks, attachment-heavy requests).
2. Record baseline: answer quality, turns completed, token usage, compaction count, compaction latency.
3. Change one parameter.
4. Re-run the eval set and compare. Look for regressions in task completion before celebrating token savings.
5. Keep the change only if quality is flat or better and cost or latency improves.

Without step 1, "it feels better" is not evidence.

## Next

- [[Sessions-Memory-and-Workspace]] for what is stored where
- [[Verification-and-Evolution]] for evaluation infrastructure
- [[Troubleshooting-FAQ]] for compaction symptoms
