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
  pinned: boolean
  /** 归属项目；未归属时为 null（后端未就绪时前端兜底为 null）。 */
  project_id: string | null
  /** 绑定的集成 MCP（用户自有后端）；普通会话为 null。 */
  integration_server_id: string | null
  /** 是否已归档：归档后从侧边栏隐藏，仅可在归档管理中查看/恢复/删除。 */
  archived: boolean
  archived_at: string | null
}

export const USER_ID = 'console_user'

export const useSessionStore = defineStore('sessions', () => {
  const sessions = ref<SessionItem[]>([])
  const archivedSessions = ref<SessionItem[]>([])
  const activeThreadId = ref<string | null>(null)
  // 正在执行任务的会话（客户端跟踪：流式进行中 / 等待人工介入）
  const runningThreads = ref<Set<string>>(new Set())
  const waitingThreads = ref<Set<string>>(new Set())
  const loading = ref(false)
  const archivedLoading = ref(false)
  const offline = ref(false)

  function normalize(item: SessionItem): SessionItem {
    return {
      ...item,
      pinned: Boolean(item.pinned),
      project_id: item.project_id ?? null,
      integration_server_id: item.integration_server_id ?? null,
      archived: Boolean(item.archived),
      archived_at: item.archived_at ?? null,
    }
  }

  // 置顶优先，组内按最后消息时间倒序
  function sortItems(items: SessionItem[]): SessionItem[] {
    return [...items].sort((a, b) => {
      if (a.pinned !== b.pinned) return a.pinned ? -1 : 1
      return String(b.updated_at).localeCompare(String(a.updated_at))
    })
  }

  function sortSessions() {
    sessions.value = sortItems(sessions.value)
  }

  async function refresh() {
    loading.value = true
    try {
      const res = await fetch(`${API_BASE}/history?user_id=${USER_ID}`, {
        signal: AbortSignal.timeout(6000),
      })
      if (!res.ok) throw new Error(String(res.status))
      const data = (await res.json()) as { sessions: SessionItem[] }
      sessions.value = sortItems(data.sessions.map(normalize))
      offline.value = false
    } catch {
      offline.value = true
    } finally {
      loading.value = false
    }
  }

  /** 读取已归档会话列表，供归档管理弹窗使用。 */
  async function refreshArchived() {
    archivedLoading.value = true
    try {
      const res = await fetch(
        `${API_BASE}/history?user_id=${USER_ID}&archived=true`,
        { signal: AbortSignal.timeout(6000) },
      )
      if (!res.ok) throw new Error(String(res.status))
      const data = (await res.json()) as { sessions: SessionItem[] }
      archivedSessions.value = sortItems(data.sessions.map(normalize))
    } catch {
      /* 离线时保留上一次结果 */
    } finally {
      archivedLoading.value = false
    }
  }

  /** 进入对话页时执行一次自动归档检查，返回本次归档数量。 */
  async function autoArchive(): Promise<number> {
    try {
      const res = await fetch(
        `${API_BASE}/history/auto-archive?user_id=${USER_ID}`,
        { method: 'POST', signal: AbortSignal.timeout(6000) },
      )
      if (!res.ok) return 0
      const data = (await res.json()) as { archived?: number }
      return Number(data.archived || 0)
    } catch {
      return 0
    }
  }

  function isArchived(threadId: string | null | undefined): boolean {
    if (!threadId) return false
    return archivedSessions.value.some((item) => item.thread_id === threadId)
  }

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

  async function createSession(
    projectId?: string | null,
    integrationServerId?: string | null,
  ): Promise<string | null> {
    try {
      const query = new URLSearchParams({ user_id: USER_ID })
      if (projectId) query.set('project_id', projectId)
      if (integrationServerId) {
        query.set('integration_server_id', integrationServerId)
      }
      const res = await fetch(`${API_BASE}/history?${query.toString()}`, { method: 'POST' })
      if (!res.ok) return null
      const session = normalize((await res.json()) as SessionItem)
      sessions.value.unshift(session)
      sortSessions()
      return session.thread_id
    } catch {
      return null
    }
  }

  async function patchSession(
    threadId: string,
    body: { title?: string; pinned?: boolean },
  ): Promise<boolean> {
    // 现有后端在 /history 上支持 title/pinned；项目归属按新契约在 /sessions 上提供。
    // 先走现有路径，失败再尝试新路径，两者都不可用时返回 false（界面保留原值）。
    const endpoints = [
      `${API_BASE}/history/${threadId}?user_id=${USER_ID}`,
      `${API_BASE}/sessions/${threadId}?user_id=${USER_ID}`,
    ]
    for (const endpoint of endpoints) {
      try {
        const res = await fetch(endpoint, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        })
        if (!res.ok) continue
        const updated = (await res.json()) as Partial<SessionItem>
        const index = sessions.value.findIndex((item) => item.thread_id === threadId)
        if (index >= 0) {
          sessions.value[index] = {
            ...sessions.value[index]!,
            ...updated,
            pinned: Boolean(updated.pinned ?? sessions.value[index]!.pinned),
            project_id:
              updated.project_id === undefined
                ? sessions.value[index]!.project_id
                : updated.project_id,
          }
        }
        sortSessions()
        return true
      } catch {
        // 继续尝试下一个端点
      }
    }
    return false
  }

  async function renameSession(threadId: string, title: string): Promise<boolean> {
    const value = title.trim()
    if (!value) return false
    return patchSession(threadId, { title: value })
  }

  async function setPinned(threadId: string, pinned: boolean): Promise<boolean> {
    return patchSession(threadId, { pinned })
  }

  /** 归档 / 恢复会话：成功后在后端两个列表之间移动该条记录。 */
  async function setArchived(threadId: string, archived: boolean): Promise<boolean> {
    try {
      const res = await fetch(
        `${API_BASE}/history/${threadId}?user_id=${USER_ID}`,
        {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ archived }),
        },
      )
      if (!res.ok) return false
      const updated = normalize((await res.json()) as SessionItem)
      sessions.value = sessions.value.filter((item) => item.thread_id !== threadId)
      archivedSessions.value = archivedSessions.value.filter(
        (item) => item.thread_id !== threadId,
      )
      if (updated.archived) {
        archivedSessions.value = sortItems([updated, ...archivedSessions.value])
      } else {
        sessions.value = sortItems([updated, ...sessions.value])
      }
      return true
    } catch {
      return false
    }
  }

  async function removeSession(threadId: string): Promise<boolean> {
    try {
      const res = await fetch(
        `${API_BASE}/history/${threadId}?user_id=${USER_ID}`,
        { method: 'DELETE' },
      )
      if (!res.ok) return false
      sessions.value = sessions.value.filter((s) => s.thread_id !== threadId)
      archivedSessions.value = archivedSessions.value.filter(
        (s) => s.thread_id !== threadId,
      )
      if (activeThreadId.value === threadId) activeThreadId.value = null
      return true
    } catch {
      /* 离线时静默 */
      return false
    }
  }

  return {
    sessions,
    archivedSessions,
    activeThreadId,
    runningThreads,
    waitingThreads,
    loading,
    archivedLoading,
    offline,
    refresh,
    refreshArchived,
    autoArchive,
    createSession,
    removeSession,
    renameSession,
    setPinned,
    setArchived,
    isArchived,
    markRunning,
    markWaiting,
  }
})
