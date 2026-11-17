// 远程异步子 Agent 任务：与调度运行一起进入输入框上方的后台任务栏。
import { API_BASE } from '@/api/client'
import { USER_ID } from '@/stores/sessions'

export interface AsyncTaskSummary {
  task_id: string
  agent_name: string
  status: string
  run_id: string
  created_at?: string
  done: boolean
  delivered: boolean
  error?: string | null
}

/** 列出当前会话的远程异步子任务；终态结果由后端幂等写回会话。 */
export async function fetchAsyncTasks(threadId: string): Promise<AsyncTaskSummary[]> {
  const response = await fetch(
    `${API_BASE}/async-tasks?user_id=${encodeURIComponent(USER_ID)}&thread_id=${encodeURIComponent(threadId)}`,
    { signal: AbortSignal.timeout(6000) },
  )
  if (!response.ok) throw new Error(`GET /async-tasks 失败: ${response.status}`)
  const data = (await response.json()) as { tasks?: AsyncTaskSummary[] }
  return Array.isArray(data.tasks) ? data.tasks : []
}

/** 终止远程异步子任务的当前 run。 */
export async function cancelAsyncTask(taskId: string): Promise<void> {
  const response = await fetch(
    `${API_BASE}/async-tasks/${encodeURIComponent(taskId)}/cancel?user_id=${encodeURIComponent(USER_ID)}`,
    { method: 'POST', signal: AbortSignal.timeout(8000) },
  )
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new Error(
      (data as { detail?: string }).detail || `终止异步任务失败: ${response.status}`,
    )
  }
}
