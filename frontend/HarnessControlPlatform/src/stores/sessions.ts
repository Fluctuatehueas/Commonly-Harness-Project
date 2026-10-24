// 会话列表：从后端 Store（/history）读取，支持切换与运行中标识
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { API_BASE } from '@/api/client'

export interface SessionItem {
  thread_id: string
  title: string
  created_at: string
  updated_at: string
  message_count: number
}

export const USER_ID = 'console_user'

export const useSessionStore = defineStore('sessions', () => {
  const sessions = ref<SessionItem[]>([])
  const activeThreadId = ref<string | null>(null)
  // 正在执行任务的会话（客户端跟踪：流式进行中 / 等待人工介入）
  const runningThreads = ref<Set<string>>(new Set())
  const waitingThreads = ref<Set<string>>(new Set())
  const loading = ref(false)
  const offline = ref(false)

  function markRunning(threadId: string | null, running: boolean) {
    if (!threadId) return
    const set = runningThreads.value
    if (running) set.add(threadId)
    else set.delete(threadId)
    runningThreads.value = new Set(set)
  }

  function markWaiting(threadId: string | null, waiting: boolean) {
    if (!threadId) return
    const set = waitingThreads.value
    if (waiting) set.add(threadId)
    else set.delete(threadId)
    waitingThreads.value = new Set(set)
  }

  async function refresh() {
    loading.value = true
    try {
      const res = await fetch(`${API_BASE}/history?user_id=${USER_ID}`, {
        signal: AbortSignal.timeout(6000),
      })
      if (!res.ok) throw new Error(String(res.status))
      const data = (await res.json()) as { sessions: SessionItem[] }
      sessions.value = data.sessions
      offline.value = false
    } catch {
      offline.value = true
    } finally {
      loading.value = false
    }
  }

  async function createSession(): Promise<string | null> {
    try {
      const res = await fetch(`${API_BASE}/history?user_id=${USER_ID}`, { method: 'POST' })
      if (!res.ok) return null
      const session = (await res.json()) as SessionItem
      sessions.value.unshift(session)
      return session.thread_id
    } catch {
      return null
    }
  }

  async function removeSession(threadId: string) {
    try {
      await fetch(`${API_BASE}/history/${threadId}?user_id=${USER_ID}`, { method: 'DELETE' })
      sessions.value = sessions.value.filter((s) => s.thread_id !== threadId)
      if (activeThreadId.value === threadId) activeThreadId.value = null
    } catch {
      /* 离线时静默 */
    }
  }

  return {
    sessions,
    activeThreadId,
    runningThreads,
    waitingThreads,
    loading,
    offline,
    refresh,
    createSession,
    removeSession,
    markRunning,
    markWaiting,
  }
})
