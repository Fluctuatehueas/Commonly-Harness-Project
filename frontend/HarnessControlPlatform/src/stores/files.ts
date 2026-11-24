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

// 临时文件 / 构建缓存 / 依赖目录：不计入「本轮产出」
const TEMP_DIRS = new Set([
  '__pycache__',
  'node_modules',
  'venv',
  'env',
  'dist',
  'build',
  'coverage',
  'target',
  '.tox',
  '.eggs',
  '.gradle',
  '.mvn',
  '.husky',
])
const TEMP_EXTS = new Set([
  '.pyc',
  '.pyo',
  '.log',
  '.tmp',
  '.temp',
  '.cache',
  '.swp',
  '.swo',
  '.map',
  '.lock',
  '.part',
  '.crdownload',
  '.class',
  '.o',
  '.obj',
  '.pdb',
  '.ilk',
  '.exp',
  '.tsbuildinfo',
])
const TEMP_FILES = new Set(['.ds_store', 'thumbs.db', 'desktop.ini'])

/** 过滤临时文件与缓存产物，只保留对用户有价值的文件。 */
export function isUsefulArtifact(file: FileMeta): boolean {
  const parts = file.path.split('/').filter(Boolean)
  if (!parts.length) return false
  const dirs = parts.slice(0, -1)
  // 点开头目录（.git/.venv/.commonharness 等）与已知构建目录一律排除
  if (dirs.some((dir) => dir.startsWith('.') || TEMP_DIRS.has(dir.toLowerCase()))) {
    return false
  }
  const name = parts[parts.length - 1]!
  const lower = name.toLowerCase()
  if (lower.startsWith('.') || TEMP_FILES.has(lower)) return false
  const dot = lower.lastIndexOf('.')
  return !(dot > 0 && TEMP_EXTS.has(lower.slice(dot)))
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
  // 外部请求定位文件（消息泡里点「在右侧打开」）：nonce 保证同一文件可重复触发
  const requested = ref<{ path: string; origin: FileOrigin; nonce: number } | null>(null)
  // threadId -> 本轮新增文件 key（origin:path）。每轮结束后整体替换。
  const newKeys = ref<Record<string, Set<string>>>({})
  // threadId -> (助手消息 ID -> 该轮产出文件)。按消息持久化，刷新后仍固定在该轮。
  const artifacts = ref<Record<string, Record<string, FileMeta[]>>>({})

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

  /** 一轮对话结束后刷新文件，并把本轮新增按「助手消息」归档并持久化。 */
  async function markRound(threadId: string | null, messageId = '') {
    if (!threadId) return
    const pending = primePromises[threadId]
    if (pending) await pending
    try {
      const payload = await fetchList(threadId)
      const currentKeys = new Set((payload.files || []).map(fileKey))
      // 没有基线时无法判断哪些是本轮新增，此时不产出文件卡片，避免把整目录当成产出
      const hadBaseline = Boolean(baselineByThread[threadId])
      const previous = baselineByThread[threadId] ?? new Set<string>()
      const nextNew = new Set<string>()
      for (const key of currentKeys) {
        if (!previous.has(key)) nextNew.add(key)
      }
      baselineByThread[threadId] = currentKeys
      newKeys.value = { ...newKeys.value, [threadId]: nextNew }
      // 同一轮可能因 HITL 恢复多次收尾，产出按消息合并而不是覆盖。
      const produced = hadBaseline
        ? (payload.files || []).filter(
            (file) => nextNew.has(fileKey(file)) && isUsefulArtifact(file),
          )
        : []
      if (messageId) {
        const merged = new Map<string, FileMeta>()
        const existing = artifacts.value[threadId]?.[messageId] || []
        for (const file of [...existing, ...produced]) merged.set(fileKey(file), file)
        const list = [...merged.values()]
        artifacts.value = {
          ...artifacts.value,
          [threadId]: { ...(artifacts.value[threadId] || {}), [messageId]: list },
        }
        if (list.length) void persistArtifacts(threadId, messageId, list)
      }
      if (activeThread === threadId) apply(payload)
    } catch {
      // 文件刷新失败不影响对话主流程；只清空本轮产出
      if (messageId) {
        artifacts.value = {
          ...artifacts.value,
          [threadId]: { ...(artifacts.value[threadId] || {}), [messageId]: [] },
        }
      }
    }
  }

  /** 把本轮产出写到后端，刷新或换设备后仍能按消息 ID 还原卡片。 */
  async function persistArtifacts(threadId: string, messageId: string, items: FileMeta[]) {
    try {
      await fetch(
        `${API_BASE}/threads/${threadId}/artifacts?user_id=${encodeURIComponent(USER_ID)}`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message_id: messageId, files: items }),
        },
      )
    } catch {
      // 持久化失败时本地仍然展示，不阻塞对话
    }
  }

  /** 打开会话时载入历史产出清单；只覆盖该 thread 的映射。 */
  async function loadArtifacts(threadId: string | null) {
    if (!threadId) return
    try {
      const res = await fetch(
        `${API_BASE}/threads/${threadId}/artifacts?user_id=${encodeURIComponent(USER_ID)}`,
      )
      if (!res.ok) return
      const data = (await res.json()) as {
        artifacts?: Record<string, FileMeta[]>
      }
      artifacts.value = { ...artifacts.value, [threadId]: data.artifacts || {} }
    } catch {
      // 后端离线时保持本地缓存
    }
  }

  /** 指定助手消息所在轮次的有用产出。 */
  function artifactsFor(threadId: string | null, messageId: string): FileMeta[] {
    if (!threadId || !messageId) return []
    return artifacts.value[threadId]?.[messageId] || []
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
    if (threadId in artifacts.value) {
      const next = { ...artifacts.value }
      delete next[threadId]
      artifacts.value = next
    }
  }

  async function open(threadId: string, path: string, origin: FileOrigin = 'sandbox') {
    error.value = ''
    // PDF 走 pdf.js 按二进制渲染，不做文本解码，避免后端 415。
    if (/\.pdf$/i.test(path)) {
      current.value = { path, content: '', origin }
      return
    }
    try {
      const content = await fetchFileContent(threadId, path, origin)
      current.value = { path, content, origin }
    } catch (err) {
      error.value = (err as Error).message
    }
  }

  /** 消息泡等外部入口请求抽屉定位到指定文件。 */
  function requestOpen(path: string, origin: FileOrigin = 'sandbox') {
    requested.value = { path, origin, nonce: Date.now() }
  }

  function clearRequested() {
    requested.value = null
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
    requested,
    newKeys,
    artifacts,
    list,
    prime,
    markRound,
    loadArtifacts,
    isNew,
    artifactsFor,
    forget,
    open,
    requestOpen,
    clearRequested,
    download,
    closePreview,
  }
})

/** 直接读取会话文件文本，不改变抽屉当前选中文件。 */
export async function fetchFileContent(
  threadId: string,
  path: string,
  origin: FileOrigin = 'sandbox',
): Promise<string> {
  const res = await fetch(
    `${API_BASE}/threads/${threadId}/files/content?user_id=${encodeURIComponent(USER_ID)}&path=${encodeURIComponent(path)}&source=${origin}`,
  )
  if (!res.ok) {
    const detail = (await res.json().catch(() => ({})) as { detail?: string }).detail
    throw new Error(detail || String(res.status))
  }
  const data = (await res.json()) as { content?: string }
  return String(data.content ?? '')
}

export function saveBlob(filename: string, blob: Blob) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}
