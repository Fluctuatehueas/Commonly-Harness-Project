// 技能管理接口：列表、详情、启停、新增、上传、下载、删除
import { API_BASE } from './client'

export interface SkillItem {
  skill_id: string
  name: string
  title: string
  description: string
  target: string
  enabled: boolean
  protected: boolean
  file_count: number
  size_bytes: number
  updated_at: string
  path: string
}

export interface SkillList {
  items: SkillItem[]
  total: number
  page: number
  page_size: number
  pages: number
}

export interface SkillContent {
  skill: SkillItem
  content: string
  truncated: boolean
}

export interface SkillStorage {
  skills_root: string
  main_root: string
  disabled_root: string
  max_package_mb: number
}

export interface SkillQuery {
  page?: number
  page_size?: number
  keyword?: string
  enabled?: boolean
  target?: string
}

export interface SkillInstallJob {
  job_id: string
  kind: 'url' | 'zip'
  label: string
  status: 'queued' | 'running' | 'done' | 'error'
  stage: string
  progress: number
  message: string
  error: string
  skill: SkillItem | null
  created_at: string
  updated_at: string
}

export interface StartInstallResult {
  job_id: string
  job: SkillInstallJob
}

async function readError(res: Response, fallback: string): Promise<string> {
  const data = (await res.json().catch(() => ({}))) as { detail?: unknown }
  if (typeof data.detail === 'string' && data.detail) return data.detail
  if (data.detail && typeof data.detail === 'object') {
    const value = data.detail as { message?: string }
    if (value.message) return value.message
  }
  return `${fallback}: HTTP ${res.status}`
}

export async function fetchSkills(query: SkillQuery = {}): Promise<SkillList> {
  const params = new URLSearchParams()
  params.set('page', String(query.page ?? 1))
  params.set('page_size', String(query.page_size ?? 12))
  if (query.keyword) params.set('keyword', query.keyword)
  if (query.enabled !== undefined) params.set('enabled', String(query.enabled))
  if (query.target) params.set('target', query.target)

  const res = await fetch(`${API_BASE}/skills?${params.toString()}`, {
    cache: 'no-store',
    signal: AbortSignal.timeout(8000),
  })
  if (!res.ok) throw new Error(await readError(res, '读取技能列表失败'))
  return (await res.json()) as SkillList
}

export async function fetchSkillStorage(): Promise<SkillStorage> {
  const res = await fetch(`${API_BASE}/skills/storage`, {
    cache: 'no-store',
    signal: AbortSignal.timeout(5000),
  })
  if (!res.ok) throw new Error(await readError(res, '读取技能库位置失败'))
  return (await res.json()) as SkillStorage
}

export async function fetchSkillContent(
  target: string,
  name: string,
): Promise<SkillContent> {
  const params = new URLSearchParams({ target, name })
  const res = await fetch(`${API_BASE}/skills/content?${params.toString()}`, {
    cache: 'no-store',
    signal: AbortSignal.timeout(8000),
  })
  if (!res.ok) throw new Error(await readError(res, '读取技能详情失败'))
  return (await res.json()) as SkillContent
}

export async function toggleSkill(
  target: string,
  name: string,
  enabled: boolean,
): Promise<SkillItem> {
  const res = await fetch(`${API_BASE}/skills/toggle`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ target, name, enabled }),
  })
  if (!res.ok) throw new Error(await readError(res, '切换技能状态失败'))
  return (await res.json()) as SkillItem
}

export async function deleteSkill(target: string, name: string): Promise<void> {
  const params = new URLSearchParams({ target, name })
  const res = await fetch(`${API_BASE}/skills/item?${params.toString()}`, {
    method: 'DELETE',
  })
  if (!res.ok) throw new Error(await readError(res, '删除技能失败'))
}

export interface CreateSkillPayload {
  name: string
  description: string
  content: string
}

export async function createSkill(payload: CreateSkillPayload): Promise<SkillItem> {
  const res = await fetch(`${API_BASE}/skills/create`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) throw new Error(await readError(res, '创建技能失败'))
  return (await res.json()) as SkillItem
}

export async function downloadSkillFromUrl(url: string): Promise<StartInstallResult> {
  const res = await fetch(`${API_BASE}/skills/download-url`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url }),
  })
  if (!res.ok) throw new Error(await readError(res, '下载技能失败'))
  return (await res.json()) as StartInstallResult
}

export async function uploadSkillZip(file: File): Promise<StartInstallResult> {
  const form = new FormData()
  form.append('file', file)
  form.append('target', 'main')
  const res = await fetch(`${API_BASE}/skills/upload`, {
    method: 'POST',
    body: form,
  })
  if (!res.ok) throw new Error(await readError(res, '上传技能包失败'))
  return (await res.json()) as StartInstallResult
}

export async function fetchSkillJob(jobId: string): Promise<SkillInstallJob> {
  const res = await fetch(`${API_BASE}/skills/jobs/${encodeURIComponent(jobId)}`, {
    cache: 'no-store',
  })
  if (!res.ok) throw new Error(await readError(res, '读取安装进度失败'))
  return (await res.json()) as SkillInstallJob
}

export async function fetchSkillJobs(): Promise<SkillInstallJob[]> {
  const res = await fetch(`${API_BASE}/skills/jobs`, { cache: 'no-store' })
  if (!res.ok) throw new Error(await readError(res, '读取安装任务失败'))
  const data = (await res.json()) as { jobs: SkillInstallJob[] }
  return data.jobs
}

export async function revealSkill(target: string, name: string): Promise<void> {
  const res = await fetch(`${API_BASE}/skills/reveal`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ target, name }),
  })
  if (!res.ok) throw new Error(await readError(res, '打开目录失败'))
}
