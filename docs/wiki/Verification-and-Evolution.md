# Verification and Evolution

Two loops that share the same trajectory data:

- **Verification** checks individual outputs and final answers, then records evidence.
- **Evolution** turns failures and feedback into experience, candidates, and controlled releases.

Both are observable-only by default. They do not rewrite the user's answer unless a blocking condition is explicitly configured.

## 1. Verification points

| Point | What is checked | Typical checks |
| :-- | :-- | :-- |
| `v1_tool` | A single tool result | Empty output, tool error, truncation, empty retrieval |
| `v2_subagent` | A subagent result | Usable conclusion, unsupported claims of writes |
| `v3_final` | The final answer | Non-empty, plan coverage, optional schema or numeric assertions |

Code:

| File | Responsibility |
| :-- | :-- |
| `src/agent/verification/l0.py` | Deterministic L0 checks |
| `src/agent/verification/rubric.py` | L1 judge prompt and structured parsing |
| `src/agent/verification/runner.py` | Level selection, execution, evidence recording |
| `src/agent/verification/evidence.py` | Evidence model |
| `src/agent/verification/budget.py` | Judge call and sampling budget |
| `src/agent/verification/types.py` | Points, levels, issues |

## 2. Levels

| Level | Method | Cost | When |
| :-- | :-- | :-- | :-- |
| `L0` | Deterministic rules | Zero | Every configured point |
| `L1` | LLM judge with a rubric | Model call | V3 by risk; V2 by sample rate |
| `L2` | Verification subagent deep review | Higher | Enabled and risk tier at or above threshold, or blocking issues |

Configuration:

| Variable | Default | Meaning |
| :-- | :-- | :-- |
| `HARNESS_VERIFICATION_ENABLED` | `true` | Master switch |
| `HARNESS_VERIFICATION_POINTS` | v1/v2/v3 | Enabled points |
| `HARNESS_VERIFICATION_ASYNC` | `true` | Run after the answer is released |
| `HARNESS_VERIFICATION_MAX_LEVEL` | `l2` | Highest level allowed |
| `HARNESS_VERIFICATION_L1_SAMPLE_RATE` | `0.1` | V2 sampling rate |
| `HARNESS_VERIFICATION_MAX_JUDGE_CALLS` | `3` | Judge call budget |
| `HARNESS_VERIFICATION_MAX_DELEGATIONS` | `1` | Verification subagent budget |
| `HARNESS_VERIFICATION_L2_ENABLED` | `true` | Allow L2 |
| `HARNESS_VERIFICATION_L2_RISK_TIER` | `t1` | Minimum risk tier for automatic L2 |
| `HARNESS_VERIFICATION_BLOCKING_SCORE` | `0.6` | Score below which an issue becomes blocking |
| `HARNESS_VERIFICATION_MAX_EVIDENCE` | `20` | Evidence cap per run |

Design rules:

- L0 always runs before any model-based check. A passing judge must not hide a deterministic failure.
- Verification is asynchronous by default so it does not add latency to the response.
- Blocks are explicit and bounded; an unbounded judge loop is worse than a missed check.

## 3. Evidence

Evidence records what was checked, at which point, with which level, and what was found. It is stored with the trajectory and used by the evolution loop.

Good evidence answers:

- Which tool or subagent produced the output?
- What was missing or inconsistent?
- Was the check deterministic or model-based?
- What was the confidence and score?

Without evidence, "verification" becomes a boolean nobody can audit.

## 4. Evolution control plane

Code: `src/agent/evolution/`

| File | Responsibility |
| :-- | :-- |
| `attribution.py` | Failure attribution by actor and observer |
| `control_plane.py` | Candidate proposal, approval, version records |
| `eval_sets.py` | Eval set and training data export |
| `experience.py` | Experience extraction, admission, utility, decay, pruning |
| `judge.py` | LLM judging helpers |
| `read_view.py` | Read models for the console |
| `redaction.py` | Redaction before storage |
| `releases.py` | Release records and rollback |
| `reward.py` | Reward signals |
| `tool_health.py` | Tool health tracking |
| `trace_governor.py` | Trace volume and retention control |
| `verifiers.py` | Verification helpers |
| `versioning.py` | Version metadata |

Attribution layers:

```text
memory | experience | harness | tool_schema | tool_router | skill | model_weight | noop
```

The actor and an independent observer attribute the same failure. When they disagree, the case is marked high-uncertainty instead of being auto-adopted.

## 5. Experience lifecycle

1. A run fails or receives negative feedback.
2. Attribution proposes a layer.
3. An experience record is extracted with a trigger and a rule.
4. Admission checks utility, duplication, and safety.
5. Approved experience is injected into later runs by `ExperienceInjectionMiddleware`.
6. Utility decays; low-utility or expired entries are pruned.

Defaults:

| Variable | Default | Meaning |
| :-- | :-- | :-- |
| `HARNESS_EVOLUTION_ENABLED` | `true` | Master switch |
| `HARNESS_EVOLUTION_MODE` | `auto_low_risk` | Automation policy |
| `HARNESS_EVOLUTION_EXPERIENCE` | `true` | Extract experience |
| `HARNESS_EVOLUTION_EXPERIENCE_INJECTION` | `true` | Inject experience |
| `HARNESS_EVOLUTION_EXPERIENCE_MAX_ITEMS` | `200` | Per-user cap |
| `HARNESS_EVOLUTION_EXPERIENCE_MIN_UTILITY` | `0.2` | Pruning threshold |
| `HARNESS_EVOLUTION_EXPERIENCE_TTL_DAYS` | `90` | Expiry |
| `HARNESS_EVOLUTION_EXPERIENCE_INJECT_LIMIT` | bounded | Per-run injection cap |
| `HARNESS_EVOLUTION_JUDGE` | `true` | LLM judging |
| `HARNESS_EVOLUTION_REDACTION` | `true` | Redact before storage |
| `HARNESS_EVOLUTION_REWARD` | `true` | Reward signals |
| `HARNESS_EVOLUTION_TOOL_HEALTH` | `true` | Tool health |
| `HARNESS_EVOLUTION_ATTRIBUTION` | `true` | Failure attribution |
| `HARNESS_EVOLUTION_CONTROL_PLANE` | `true` | Candidate and release control |
| `HARNESS_EVOLUTION_SHARING` | `true` | Cross-session sharing policy |
| `HARNESS_EVOLUTION_REPLAY_TIER` | `approx` | Replay fidelity |
| `HARNESS_EVOLUTION_KEEP_REASONING` | `false` | Do not retain raw reasoning by default |

## 6. Candidates and releases

```text
failure / feedback
   → attribution
   → experience record
   → intervention candidate
   → human approval (when required)
   → release record
   → rollback if metrics regress
```

A candidate has:

- Target layer
- Target object
- Change spec
- Evidence references
- Risk tier
- Whether human approval is required

Applying an experience candidate activates the experience. Applying a deeper candidate creates a release record. Rollback restores the previous version.

The repository does **not** train model weights. `eval_sets.py` exposes a `WeightUpdatePort` as an integration point for an external training service.

## 7. Eval sets and training data

- Eval sets store representative cases with expected properties.
- Training data export uses successful trajectories from the current skill generation; it does not mix across generations.
- Online failures should become new eval cases. This is the main defense against silent regressions.
- External tools such as DeepEval, Ragas, or lm-evaluation-harness are not wired yet; the internal eval set is the current mechanism.

## 8. API

| Method | Path | Purpose |
| :-- | :-- | :-- |
| `GET` | `/evolution/config` | Configuration |
| `GET` | `/evolution/experiences` | List experience |
| `GET` | `/evolution/experiences/{id}` | Experience detail |
| `POST` | `/evolution/experiences/{id}/approve` | Approve |
| `POST` | `/evolution/experiences/{id}/quarantine` | Quarantine |
| `DELETE` | `/evolution/experiences/{id}` | Delete |
| `GET` | `/evolution/tool-health` | Tool health |
| `POST` | `/evolution/rewards` | Record reward |
| `GET` | `/evolution/rewards` | List rewards |
| `GET` | `/evolution/candidates` | List candidates |
| `POST` | `/evolution/candidates/{id}/apply` | Apply candidate |
| `POST` | `/evolution/candidates/{id}/reject` | Reject candidate |
| `GET` | `/evolution/releases` | List releases |
| `POST` | `/evolution/releases/{id}/rollback` | Roll back |
| `GET` | `/evolution/eval-sets` | List eval sets |
| `POST` | `/evolution/eval-sets` | Add eval case |
| `GET` | `/evolution/training-data` | Export training data |
| `GET` | `/evolution/metrics` | Metrics |

## 9. Feedback loop

| Method | Path | Purpose |
| :-- | :-- | :-- |
| `POST` | `/feedback` | Submit like / dislike with excerpts |

`HARNESS_FEEDBACK_ENABLED` toggles the feature. Records are bounded by daily limits, excerpt lengths, retention days, and maximum record count. Negative feedback is one input to experience extraction; it is not proof by itself.

## 10. How to evaluate a harness change

1. Define the behavior under test: tool selection, task completion, citation quality, latency, token cost.
2. Build or reuse an eval set large enough to catch regressions.
3. Record baseline metrics and traces.
4. Change one thing.
5. Re-run and compare. Inspect traces for new failure modes, not just aggregate scores.
6. Publish only if quality is flat or better and the cost trade-off is acceptable.

"Feels better" is not an evaluation. A trace without a baseline is an anecdote.

## Next

- [[Context-Engineering]] for compaction evaluation
- [[Scheduling-and-Task-Flows]] for acceptance checks
- [[Development-Guide]] for testing conventions
