// 轨迹接口：读取某会话按 run 聚合的过程记录
import { API_BASE } from './client'

export type TrajectoryEventType =
  | 'run'
  | 'model'
  | 'tool'
  | 'skill'
  | 'delegation'
  | 'hitl'

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
}

export interface TrajectorySummary {
  model_calls: number
  tool_calls: number
  skill_calls: number
  delegations: number
  hitl: number
  tokens: TrajectoryTokens
  peak_context_tokens: number
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
