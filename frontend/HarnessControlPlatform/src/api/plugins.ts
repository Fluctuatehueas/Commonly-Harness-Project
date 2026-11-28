// 插件市场与插件管理接口
import { API_BASE } from './client'
import type {
  PluginAuditItem,
  PluginCatalogPage,
  PluginHealth,
  PluginInstallPayload,
  PluginInstalledItem,
  PluginJob,
  PluginMarketItem,
  PluginStartInstallResult,
  PluginUpdateItem,
} from '@/types/plugins'

async function readError(res: Response, fallback: string): Promise<string> {
  const data = (await res.json().catch(() => ({}))) as { detail?: unknown }
  if (typeof data.detail === 'string' && data.detail) return data.detail
  if (data.detail && typeof data.detail === 'object') {
    const value = data.detail as { message?: string; code?: string }
    if (value.message) return value.message
    if (value.code) return `${fallback}（${value.code}）`
  }
  return `${fallback}: HTTP ${res.status}`
}

async function getJson<T>(path: string, fallback: string, timeout = 10000): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    cache: 'no-store',
    signal: AbortSignal.timeout(timeout),
  })
  if (!res.ok) throw new Error(await readError(res, fallback))
  return (await res.json()) as T
}

async function sendJson<T>(
  path: string,
  method: 'POST' | 'PUT' | 'DELETE',
  body: unknown,
  fallback: string,
): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) throw new Error(await readError(res, fallback))
  return (await res.json()) as T
}

export interface PluginCatalogQuery {
  keyword?: string
  capability?: string
  source?: string
  sort?: 'popular' | 'latest' | 'name'
  page?: number
  page_size?: number
  refresh?: boolean
}

export async function fetchPluginCatalog(
  query: PluginCatalogQuery = {},
): Promise<PluginCatalogPage> {
  const params = new URLSearchParams()
  params.set('page', String(query.page ?? 1))
  params.set('page_size', String(query.page_size ?? 12))
  if (query.keyword) params.set('keyword', query.keyword)
  if (query.capability) params.set('capability', query.capability)
  if (query.source) params.set('source', query.source)
  if (query.sort) params.set('sort', query.sort)
  if (query.refresh) params.set('refresh', 'true')
  return getJson<PluginCatalogPage>(
    `/plugins/market/catalog?${params.toString()}`,
    '读取插件市场失败',
    15000,
  )
}

export async function fetchPluginMarketDetail(pluginId: string): Promise<{
  market: PluginMarketItem
  installed: boolean
  installed_version: string
  enabled: boolean
  health?: PluginHealth | null
}> {
  return getJson(
    `/plugins/market/${encodeURIComponent(pluginId)}`,
    '读取插件详情失败',
  )
}

export interface PluginListQuery {
  keyword?: string
  enabled?: boolean
  source?: string
}

export async function fetchPlugins(
  query: PluginListQuery = {},
): Promise<{ items: PluginInstalledItem[]; total: number }> {
  const params = new URLSearchParams()
  if (query.keyword) params.set('keyword', query.keyword)
  if (query.enabled !== undefined) params.set('enabled', String(query.enabled))
  if (query.source) params.set('source', query.source)
  const suffix = params.toString()
  return getJson(
    `/plugins${suffix ? `?${suffix}` : ''}`,
    '读取已安装插件失败',
  )
}

export async function fetchPluginDetail(pluginId: string): Promise<PluginInstalledItem> {
  return getJson(`/plugins/${encodeURIComponent(pluginId)}`, '读取插件详情失败')
}

export async function fetchPluginUpdates(): Promise<{
  items: PluginUpdateItem[]
  total: number
}> {
  return getJson('/plugins/updates', '读取可更新插件失败', 15000)
}

export async function startPluginInstall(
  payload: PluginInstallPayload,
): Promise<PluginStartInstallResult> {
  return sendJson(
    '/plugins/install',
    'POST',
    payload,
    '启动插件安装失败',
  )
}

export async function uploadPluginZip(
  file: File,
  autoEnable = true,
): Promise<PluginStartInstallResult> {
  const form = new FormData()
  form.append('file', file)
  form.append('auto_enable', String(autoEnable))
  const res = await fetch(`${API_BASE}/plugins/install/upload`, {
    method: 'POST',
    body: form,
  })
  if (!res.ok) throw new Error(await readError(res, '上传插件包失败'))
  return (await res.json()) as PluginStartInstallResult
}

export async function fetchPluginJobs(): Promise<PluginJob[]> {
  const data = await getJson<{ jobs: PluginJob[] }>('/plugins/jobs', '读取安装任务失败')
  return data.jobs
}

export async function fetchPluginJob(jobId: string): Promise<PluginJob> {
  return getJson(`/plugins/jobs/${encodeURIComponent(jobId)}`, '读取安装进度失败')
}

export async function togglePlugin(
  pluginId: string,
  enabled: boolean,
): Promise<{ plugin: PluginInstalledItem }> {
  return sendJson(
    `/plugins/${encodeURIComponent(pluginId)}/toggle`,
    'POST',
    { enabled },
    enabled ? '启用插件失败' : '停用插件失败',
  )
}

export async function restartPlugin(pluginId: string): Promise<PluginHealth> {
  return sendJson(
    `/plugins/${encodeURIComponent(pluginId)}/restart`,
    'POST',
    undefined,
    '重启插件失败',
  )
}

export async function deletePlugin(pluginId: string): Promise<void> {
  await sendJson(
    `/plugins/${encodeURIComponent(pluginId)}`,
    'DELETE',
    undefined,
    '卸载插件失败',
  )
}

export async function updatePlugin(
  pluginId: string,
  version = '',
): Promise<PluginStartInstallResult> {
  return sendJson(
    `/plugins/${encodeURIComponent(pluginId)}/update`,
    'POST',
    { version },
    '更新插件失败',
  )
}

export async function fetchPluginHealth(pluginId: string): Promise<PluginHealth> {
  return getJson(`/plugins/${encodeURIComponent(pluginId)}/health`, '读取插件健康状态失败')
}

export async function fetchPluginAudit(
  pluginId = '',
  limit = 100,
): Promise<PluginAuditItem[]> {
  const params = new URLSearchParams({ limit: String(limit) })
  if (pluginId) params.set('plugin_id', pluginId)
  const data = await getJson<{ items: PluginAuditItem[] }>(
    `/plugins/audit?${params.toString()}`,
    '读取插件审计失败',
  )
  return data.items
}

// DSH 条目转译为本地技能
export async function importDshSkill(
  pluginId: string,
  version = '',
): Promise<PluginStartInstallResult> {
  return sendJson(
    '/plugins/dsh/import',
    'POST',
    { plugin_id: pluginId, version },
    '启动 DSH 转译失败',
  )
}

export function pluginUiUrl(pluginId: string, entry: string): string {
  const clean = entry.replace(/^\/+/, '')
  return `${API_BASE}/plugins/${encodeURIComponent(pluginId)}/ui/${clean}`
}
