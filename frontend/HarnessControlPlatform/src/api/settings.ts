// 后端运行参数：思考模式 / 联网搜索 / 沙箱配置，写入 .env 后热更新
import { API_BASE } from './client'

export interface ModelSettings {
  model_name: string
  base_url: string
  has_api_key: boolean
  thinking_enabled: boolean
  web_search_enabled: boolean
}

export type ModelSettingsPatch = Partial<
  Pick<ModelSettings, 'thinking_enabled' | 'web_search_enabled'>
>

export interface SandboxSettings {
  configured: boolean
  mode: 'sandbox' | 'local'
  host: string
  port: number
  has_api_key: boolean
  image: string
  default_timeout_seconds: number
  sandbox_timeout_seconds: number
  prewarm_enabled: boolean
  use_server_proxy: boolean
  notice: string
}

export interface RuntimeProbe {
  command: string
  label: string
  available: boolean
  path: string
  version: string
  min_version: string
  satisfied: boolean
  download: string
  hint: string
  message?: string
}

export interface LocalEnvironment {
  platform: string
  runnable: number
  items: RuntimeProbe[]
}

export interface StoragePaths {
  user_data_dir: string
  preferred_dir: string
  fallback: boolean
  error: string
  workspace_root: string
  mcp_root: string
  mcp_config: string
  skills_root: string
}

export interface SandboxSettingsResponse {
  sandbox: SandboxSettings
  status: { configured: boolean; mode: 'sandbox' | 'local'; notice: string }
  local_env: LocalEnvironment
  paths: StoragePaths
}

export interface SandboxSettingsPatch {
  host?: string
  port?: number
  api_key?: string
  clear_api_key?: boolean
  image?: string
  default_timeout_seconds?: number
  sandbox_timeout_seconds?: number
  prewarm_enabled?: boolean
  use_server_proxy?: boolean
}

export interface SandboxCheckResult {
  reachable: boolean
  host: string
  port: number
  message: string
}

export async function fetchModelSettings(): Promise<ModelSettings> {
  const res = await fetch(`${API_BASE}/settings/model`, {
    signal: AbortSignal.timeout(4000),
  })
  if (!res.ok) throw new Error(`读取设置失败: HTTP ${res.status}`)
  return (await res.json()) as ModelSettings
}

export async function updateModelSettings(
  patch: ModelSettingsPatch,
): Promise<ModelSettings> {
  const res = await fetch(`${API_BASE}/settings/model`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
  const data = (await res.json().catch(() => ({}))) as {
    detail?: string
    current?: ModelSettings
  }
  if (!res.ok) throw new Error(data.detail || `保存设置失败: HTTP ${res.status}`)
  if (!data.current) throw new Error('后端未返回最新设置')
  return data.current
}

export async function fetchSandboxSettings(): Promise<SandboxSettingsResponse> {
  const res = await fetch(`${API_BASE}/settings/sandbox`, {
    cache: 'no-store',
    signal: AbortSignal.timeout(15000),
  })
  if (!res.ok) throw new Error(`读取沙箱设置失败: HTTP ${res.status}`)
  return (await res.json()) as SandboxSettingsResponse
}

export async function updateSandboxSettings(
  patch: SandboxSettingsPatch,
): Promise<SandboxSettingsResponse> {
  const res = await fetch(`${API_BASE}/settings/sandbox`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
  const data = (await res.json().catch(() => ({}))) as {
    detail?: string
    sandbox?: SandboxSettings
    status?: SandboxSettingsResponse['status']
  }
  if (!res.ok) {
    throw new Error(
      typeof data.detail === 'string' ? data.detail : `保存沙箱设置失败: HTTP ${res.status}`,
    )
  }
  return fetchSandboxSettings()
}

export async function checkSandboxPort(
  host?: string,
  port?: number,
): Promise<SandboxCheckResult> {
  const query = new URLSearchParams()
  if (host) query.set('host', host)
  if (port) query.set('port', String(port))
  const suffix = query.toString() ? `?${query.toString()}` : ''
  const res = await fetch(`${API_BASE}/settings/sandbox/check${suffix}`, {
    method: 'POST',
  })
  if (!res.ok) throw new Error(`检测沙箱服务失败: HTTP ${res.status}`)
  return (await res.json()) as SandboxCheckResult
}

export async function fetchSandboxStatus(): Promise<SandboxSettingsResponse['status']> {
  const res = await fetch(`${API_BASE}/settings/sandbox/status`, {
    cache: 'no-store',
    signal: AbortSignal.timeout(5000),
  })
  if (!res.ok) throw new Error(`读取执行模式失败: HTTP ${res.status}`)
  return (await res.json()) as SandboxSettingsResponse['status']
}

