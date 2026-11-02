// 会话文件：列表 / 预览 / 下载（右侧文件抽屉）
// new 标记按“轮次”计算：每轮结束后与上一轮基线对比，只保留本轮新增文件。
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { API_BASE } from '@/api/client'
import { USER_ID } from './sessions'

export type FileOrigin = 'project' | 'sandbox'

export interface FileMeta {
  path: string
  size: number
  modified_at: string | null
  origin: FileOrigin
}

interface FileListPayload {
  files?: FileMeta[]
  source?: string
  workspace?: string
  sandbox_root?: string
  project_id?: string | null
}

function fileKey(file: FileMeta): string {
  return `${file.origin}:${file.path}`
}

export const useFileStore = defineStore('files', () => {
  const files = ref<FileMeta[]>([])
  const source = ref('')
  const workspace = ref('')
  const sandboxRoot = ref('')
  const projectId = ref<string | null>(null)
  const loading = ref(false)
  const current = ref<{ path: string; content: string; origin: FileOrigin } | null>(null)
  const error = ref('')
  // threadId -> 本轮新增文件 key（origin:path）。每轮结束后整体替换。
  const newKeys = ref<Record<string, Set<string>>>({})

  let activeThread: string | null = null
  // 上一轮结束时的文件快照；用于判断“本轮新增”。
  const baselineByThread: Record<string, Set<string>> = {}
  // 切换会话时正在拉取基线；轮次结束需要等它完成，避免把整批旧文件误判为 new。
  const primePromises: Record<string, Promise<void>> = {}

  async function fetchList(threadId: string): Promise<FileListPayload> {
    const res = await fetch(
      `${API_BASE}/threads/${threadId}/files?user_id=${encodeURIComponent(USER_ID)}`,
    )
    if (!res.ok) {
      const detail = (await res.json().catch(() => ({})) as { detail?: string }).detail
      throw new Error(detail || String(res.status))
    }
    return (await res.json()) as FileListPayload
  }

  function apply(payload: FileListPayload) {
    files.value = payload.files || []
    source.value = payload.source || 'sandbox'
    workspace.value = payload.workspace || ''
    sandboxRoot.value = payload.sandbox_root || ''
    projectId.value = payload.project_id || null
  }

  /** 打开会话时先记录基线，后续轮次才知道哪些是新增。 */
  function prime(threadId: string | null): Promise<void> {
    if (!threadId || baselineByThread[threadId]) return Promise.resolve()
    const pending = primePromises[threadId]
    if (pending) return pending
    const task = (async () => {
      try {
        const payload = await fetchList(threadId)
        if (!baselineByThread[threadId]) {
          baselineByThread[threadId] = new Set((payload.files || []).map(fileKey))
        }
      } catch {
        // 后端离线时忽略；下一次 list/markRound 会再尝试。
      } finally {
        delete primePromises[threadId]
      }
    })()
    primePromises[threadId] = task
    return task
  }

  async function list(threadId: string | null) {
    activeThread = threadId
    if (!threadId) {
      files.value = []
      source.value = ''
      workspace.value = ''
      sandboxRoot.value = ''
      projectId.value = null
      return
    }
    loading.value = true
    error.value = ''
    try {
      const payload = await fetchList(threadId)
      apply(payload)
    } catch (err) {
      error.value = (err as Error).message
      files.value = []
    } finally {
      loading.value = false
    }
  }

  /** 一轮对话结束后刷新文件，并把本轮新增标为 new；上一轮 new 自动清除。 */
  async function markRound(threadId: string | null) {
    if (!threadId) return
    const pending = primePromises[threadId]
    if (pending) await pending
    try {
      const payload = await fetchList(threadId)
      const currentKeys = new Set((payload.files || []).map(fileKey))
      const previous = baselineByThread[threadId] ?? new Set<string>()
      const nextNew = new Set<string>()
      for (const key of currentKeys) {
        if (!previous.has(key)) nextNew.add(key)
      }
      baselineByThread[threadId] = currentKeys
      newKeys.value = { ...newKeys.value, [threadId]: nextNew }
      if (activeThread === threadId) apply(payload)
    } catch {
      // 文件刷新失败不影响对话主流程
    }
  }

  function isNew(threadId: string | null, file: FileMeta): boolean {
    if (!threadId) return false
    return Boolean(newKeys.value[threadId]?.has(fileKey(file)))
  }

  /** 删除会话时清理该 thread 的基线与 new 状态。 */
  function forget(threadId: string) {
    delete baselineByThread[threadId]
    delete primePromises[threadId]
    if (threadId in newKeys.value) {
      const next = { ...newKeys.value }
      delete next[threadId]
      newKeys.value = next
    }
  }

  async function open(threadId: string, path: string, origin: FileOrigin = 'sandbox') {
    error.value = ''
    try {
      const res = await fetch(
        `${API_BASE}/threads/${threadId}/files/content?user_id=${encodeURIComponent(USER_ID)}&path=${encodeURIComponent(path)}&source=${origin}`,
      )
      if (!res.ok) {
        const detail = (await res.json().catch(() => ({})) as { detail?: string }).detail
        throw new Error(detail || String(res.status))
      }
      const data = await res.json()
      current.value = { path: data.path, content: data.content, origin }
    } catch (err) {
      error.value = (err as Error).message
    }
  }

  async function download(threadId: string, path: string, origin: FileOrigin = 'sandbox') {
    error.value = ''
    try {
      const res = await fetch(
        `${API_BASE}/threads/${threadId}/files/download?user_id=${encodeURIComponent(USER_ID)}&path=${encodeURIComponent(path)}&source=${origin}`,
      )
      if (!res.ok) {
        const detail = (await res.json().catch(() => ({})) as { detail?: string }).detail
        throw new Error(detail || String(res.status))
      }
      saveBlob(path.split('/').pop() || 'download', await res.blob())
    } catch (err) {
      error.value = (err as Error).message
    }
  }

  function closePreview() {
    current.value = null
  }

  return {
    files,
    source,
    workspace,
    sandboxRoot,
    projectId,
    loading,
    current,
    error,
    newKeys,
    list,
    prime,
    markRound,
    isNew,
    forget,
    open,
    download,
    closePreview,
  }
})

export function saveBlob(filename: string, blob: Blob) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}
