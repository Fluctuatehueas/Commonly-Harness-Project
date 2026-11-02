// 项目工作区接口：项目列表、创建/附着、校验、重命名、移出
import { API_BASE } from './client'
import { USER_ID } from '@/stores/sessions'

export type ProjectStatus = 'active' | 'untrusted' | 'unavailable'
export type ProjectMode = 'create' | 'attach'

export interface WorkspaceProject {
  id: string
  name: string
  path: string
  pinned: boolean
  status: ProjectStatus
  marker_state: 'ok' | 'missing' | 'invalid' | 'unsupported' | 'conflict'
  conversation_count: number
  created_at: string | null
  updated_at: string | null
  last_opened_at: string | null
  is_default: boolean
}

interface HarnessWorkspaceBridge {
  pickDirectory?: () => Promise<string | null>
  revealInFolder?: (path: string) => Promise<boolean>
  showItemInFolder?: (path: string) => Promise<boolean>
}

function workspaceBridge(): HarnessWorkspaceBridge | undefined {
  return (window as unknown as { harnessWorkspace?: HarnessWorkspaceBridge }).harnessWorkspace
}

function errorMessage(detail: unknown, fallback: string): string {
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object' && 'message' in detail) {
    return String((detail as { message?: string }).message || fallback)
  }
  return fallback
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    cache: 'no-store',
    signal: AbortSignal.timeout(8000),
    ...init,
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    throw new Error(errorMessage((data as { detail?: unknown }).detail, `HTTP ${res.status}`))
  }
  return data as T
}

export async function fetchWorkspaceProjects(): Promise<WorkspaceProject[]> {
  const data = await request<{ projects?: WorkspaceProject[] }>(
    `/projects?user_id=${encodeURIComponent(USER_ID)}`,
  )
  return (data.projects || []).map((item) => ({
    ...item,
    pinned: Boolean(item.pinned),
    conversation_count: Number(item.conversation_count || 0),
  }))
}

export async function createWorkspaceProject(options: {
  mode: ProjectMode
  path: string
  name?: string
  adopt?: boolean
}): Promise<WorkspaceProject> {
  return request<WorkspaceProject>(
    `/projects?user_id=${encodeURIComponent(USER_ID)}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        mode: options.mode,
        path: options.path,
        name: options.name || '',
        adopt: Boolean(options.adopt),
      }),
    },
  )
}

export async function updateWorkspaceProject(
  projectId: string,
  patch: { name?: string; pinned?: boolean },
): Promise<WorkspaceProject> {
  return request<WorkspaceProject>(
    `/projects/${projectId}?user_id=${encodeURIComponent(USER_ID)}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    },
  )
}

export async function deleteWorkspaceProject(projectId: string): Promise<void> {
  await request<{ ok: boolean }>(
    `/projects/${projectId}?user_id=${encodeURIComponent(USER_ID)}`,
    { method: 'DELETE' },
  )
}

export async function validateWorkspaceProject(
  projectId: string,
  repair = true,
): Promise<WorkspaceProject> {
  return request<WorkspaceProject>(
    `/projects/${projectId}/validate?user_id=${encodeURIComponent(USER_ID)}&repair=${repair ? 'true' : 'false'}`,
    { method: 'POST' },
  )
}

export interface WorkspacePathCheck {
  ok: boolean
  path?: string
  marker_state?: string
  message?: string
  code?: string
  project_id?: string | null
}

export async function checkWorkspacePath(path: string): Promise<WorkspacePathCheck> {
  return request<WorkspacePathCheck>('/projects/validate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  })
}

/** 用目录名作为项目默认名称。 */
export function projectNameFromPath(path: string): string {
  const cleaned = path.replace(/[\\/]+$/, '')
  const parts = cleaned.split(/[\\/]/)
  return parts[parts.length - 1] || cleaned || '未命名项目'
}

/**
 * 选择目录：Electron 走原生对话框；浏览器退化为手动输入路径。
 * 用户取消时返回 null，不抛出异常。
 */
export async function pickDirectory(): Promise<string | null> {
  const bridge = workspaceBridge()
  if (bridge?.pickDirectory) {
    try {
      const selected = await bridge.pickDirectory()
      return selected || null
    } catch {
      return null
    }
  }
  const input = window.prompt('输入项目目录的绝对路径', '')
  const value = (input || '').trim()
  return value || null
}

/** 在系统资源管理器中打开已登记项目目录；非 Electron 环境返回 false。 */
export async function revealProject(path: string): Promise<boolean> {
  const bridge = workspaceBridge()
  if (!bridge?.revealInFolder) return false
  try {
    return await bridge.revealInFolder(path)
  } catch {
    return false
  }
}

/** 在资源管理器中定位并选中一个文件；非 Electron 环境返回 false。 */
export async function revealFile(path: string): Promise<boolean> {
  const bridge = workspaceBridge()
  if (bridge?.showItemInFolder) {
    try {
      return await bridge.showItemInFolder(path)
    } catch {
      return false
    }
  }
  if (bridge?.revealInFolder) {
    try {
      return await bridge.revealInFolder(path)
    } catch {
      return false
    }
  }
  return false
}

/** 把项目根与文件相对路径拼成宿主绝对路径（允许 Windows 反斜杠）。 */
export function absoluteWorkspacePath(workspace: string, path: string): string {
  const root = workspace.replace(/[\\/]+$/, '')
  if (!root) return path
  if (/^[A-Za-z]:[\\/]/.test(path)) return path
  return `${root}${path.startsWith('/') ? path : `/${path}`}`
}
