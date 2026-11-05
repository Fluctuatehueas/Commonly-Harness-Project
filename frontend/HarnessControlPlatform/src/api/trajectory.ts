// 轨迹接口：读取某会话按 run 聚合的过程记录
import { API_BASE } from './client'

export type TrajectoryEventType =
  | 'run'
  | 'model'
  | 'tool'
  | 'skill'
  | 'delegation'
  | 'hitl'
  | 'compaction'

export interface TrajectoryTokens {
  input?: number
  output?: number
  total?: number
}

export interface TrajectoryContext {
  used_tokens?: number
  limit?: number
  pct?: number
}

export interface TrajectoryCompactionTrigger {
  type?: string
  used_tokens?: number
  limit?: number
  pct?: number
  signals?: Record<string, number | boolean | string>
}

export interface TrajectoryCompactionResult {
  before_tokens?: number
  after_tokens?: number
  saved_tokens?: number
  summary_tokens?: number
  summary_preview?: string
  history_file?: string
  anchors_total?: number
}

export interface TrajectoryCompactionEvaluation {
  status?: string
  fidelity?: number
  timing?: string
  anchors_total?: number
  anchors_kept?: number
  retrieval_cost?: { reads?: number; tokens?: number }
  reason?: string
}

export interface TrajectoryCompactionBudget {
  window?: number
  reserve?: number
  usable?: number
  soft?: number
  hard?: number
  keep?: number
  soft_pct?: number
  hard_pct?: number
}

export interface TrajectoryEvent {
  id: string
  run_id: string
  type: TrajectoryEventType
  label: string
  status: 'running' | 'done' | 'error' | 'interrupted' | string
  start: string
  end?: string | null
  duration_ms?: number | null
  context?: TrajectoryContext
  preview?: string
  result?: string
  args?: string
  tokens?: TrajectoryTokens
  tool_name?: string
  tool_call_id?: string
  subagent_name?: string
  source?: string
  summary?: Record<string, number>
  tool_names?: string[]
  // 压缩事件附加字段
  compaction_id?: string
  budget?: TrajectoryCompactionBudget
  trigger?: TrajectoryCompactionTrigger
  scope?: {
    cutoff_index?: number
    messages_before?: number
    messages_after?: number
    layers?: string[]
  }
  compaction_result?: TrajectoryCompactionResult
  evaluation?: TrajectoryCompactionEvaluation
  integrity?: {
    algo?: string
    hash?: string
    signature?: string
    signed?: boolean
    verified?: boolean
  }
}

export interface TrajectorySummary {
  model_calls: number
  tool_calls: number
  skill_calls: number
  delegations: number
  hitl: number
  compact_count: number
  tokens: TrajectoryTokens
  peak_context_tokens: number
  min_context_tokens: number
  tokens_saved: number
  duration_ms: number
  event_count: number
}

export interface TrajectoryRun {
  run_id: string
  started_at: string
  ended_at: string | null
  status: 'running' | 'done' | 'interrupted' | string
  events: TrajectoryEvent[]
  summary: TrajectorySummary
}

export interface TrajectoryResponse {
  thread_id: string
  runs: TrajectoryRun[]
  latest_run_id: string | null
  event_count: number
  generated_at: string
}

export async function fetchTrajectory(
  threadId: string,
  userId: string,
): Promise<TrajectoryResponse> {
  const res = await fetch(
    `${API_BASE}/trajectory/${encodeURIComponent(threadId)}?user_id=${encodeURIComponent(userId)}`,
    { signal: AbortSignal.timeout(10000) },
  )
  if (!res.ok) {
    const detail = (await res.json().catch(() => ({}))) as { detail?: string }
    throw new Error(detail.detail || `轨迹读取失败: HTTP ${res.status}`)
  }
  return (await res.json()) as TrajectoryResponse
}
