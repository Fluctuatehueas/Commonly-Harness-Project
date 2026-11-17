// 后台调度事件流：调度工具返回后本轮聊天 SSE 关闭，进度改由独立接口推送。
import { API_BASE } from '@/api/client'
import { USER_ID } from '@/stores/sessions'

export interface SchedulerEventSubscription {
  close: () => void
}

export interface SchedulerRunSummary {
  run_id: string
  goal: string
  status: string
  tier: string
  mode: string
  plan_version: number
  item_count: number
  done: number
  failed: number
  summary: string
  created_at?: string
  updated_at?: string
}

/** 读取当前会话最近的调度运行，供输入框上方的后台任务栏展示。 */
export async function fetchSchedulerRuns(threadId: string): Promise<SchedulerRunSummary[]> {
  const response = await fetch(
    `${API_BASE}/scheduler/runs?user_id=${encodeURIComponent(USER_ID)}&thread_id=${encodeURIComponent(threadId)}`,
    { signal: AbortSignal.timeout(6000) },
  )
  if (!response.ok) throw new Error(`GET /scheduler/runs 失败: ${response.status}`)
  const data = (await response.json()) as { runs?: SchedulerRunSummary[] }
  return Array.isArray(data.runs) ? data.runs : []
}

/** 终止一个后台调度；接口返回后任务会在下一次事件中落成 cancelled 终态。 */
export async function cancelSchedulerRun(runId: string): Promise<void> {
  const response = await fetch(
    `${API_BASE}/scheduler/runs/${encodeURIComponent(runId)}/cancel?user_id=${encodeURIComponent(USER_ID)}`,
    { method: 'POST', signal: AbortSignal.timeout(8000) },
  )
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new Error(
      (data as { detail?: string }).detail || `终止后台任务失败: ${response.status}`,
    )
  }
}

/**
 * 订阅一次调度运行的进度。
 *
 * - 后端会先补一份数据库快照，再回放历史事件，断线重连不会丢进度；
 * - 收到 run_finished 后主动关闭，避免 EventSource 在服务端正常断开后继续重连。
 */
export function subscribeSchedulerRun(
  runId: string,
  onEvent: (event: Record<string, unknown>) => void,
  onClosed?: () => void,
): SchedulerEventSubscription {
  const url = `${API_BASE}/scheduler/runs/${encodeURIComponent(runId)}/events?user_id=${encodeURIComponent(USER_ID)}`
  const source = new EventSource(url)
  let closed = false

  const close = () => {
    if (closed) return
    closed = true
    source.close()
    onClosed?.()
  }

  source.onmessage = (message) => {
    try {
      const event = JSON.parse(message.data) as Record<string, unknown>
      onEvent(event)
      if (String(event.event || '') === 'run_finished') close()
    } catch {
      // 单条事件解析失败不打断订阅。
    }
  }
  // 网络抖动交给 EventSource 自动重连；终态由 run_finished 主动关闭。
  source.onerror = () => undefined

  return { close }
}
