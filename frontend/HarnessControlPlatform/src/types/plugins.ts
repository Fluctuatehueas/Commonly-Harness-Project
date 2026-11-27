// 插件市场与已安装插件的前端类型定义

export type PluginSource =
  | 'builtin'
  | 'user'
  | 'community'
  | 'private'
  | 'local'
  | 'public'
  | 'dsh'
  | 'remote'
  | 'project'

export interface PluginAuthor {
  name: string
  homepage?: string
}

export interface PluginUISlot {
  slot: string
  title?: string
  entry: string
}

export interface PluginDist {
  url?: string
  sha256?: string
  size?: number
}

export interface PluginMarketItem {
  plugin_id: string
  name: string
  version: string
  description?: string
  summary?: string
  author?: PluginAuthor
  license?: string
  tag?: string[]
  source: PluginSource | string
  capabilities?: string[]
  permissions?: string[]
  runtime?: { kind: string; language?: string; command?: string; args?: string[] }
  transport?: { kind: string; url?: string }
  protocolVersion?: string
  ui?: { slots?: PluginUISlot[] }
  harnessVersion?: { min?: string; max?: string }
  dist?: PluginDist
  versions?: string[]
  like?: number
  downloadCount?: number
  installable?: boolean
  unsupported?: boolean
  import_kind?: string
  unsupported_reason?: string
  repository?: string
  installed?: boolean
  installed_version?: string
  enabled?: boolean
}

export interface PluginCatalogPage {
  items: PluginMarketItem[]
  total: number
  page: number
  page_size: number
  pages: number
  stale: boolean
  fetched_at?: string
  sources?: Record<string, PluginSourceState>
}

export interface PluginSourceState {
  url: string
  ok: boolean
  stale: boolean
  count: number
  error?: string
  fetched_at?: string
}

export interface PluginInstalledItem {
  plugin_id: string
  name: string
  version: string
  description: string
  source: PluginSource | string
  enabled: boolean
  status: string
  capabilities: string[]
  permissions: string[]
  runtime: { kind: string; language?: string; command?: string; args?: string[] }
  transport: { kind: string; url?: string }
  protocolVersion: string
  path: string
  builtin: boolean
  deletable: boolean
  has_ui: boolean
  ui_slots: PluginUISlot[]
  versions: string[]
  tool_count: number
  error?: string
  installed_at?: string
  health?: PluginHealth
  capabilities_detail?: Record<string, unknown>
}

export interface PluginHealth {
  plugin_id: string
  state: string
  latency_ms?: number
  error?: string
  enabled?: boolean
}

export interface PluginCardModel extends PluginMarketItem {
  builtin?: boolean
  deletable?: boolean
  status?: string
  has_ui?: boolean
  ui_slots?: PluginUISlot[]
  health?: PluginHealth
  tool_count?: number
  path?: string
  error?: string
  installed_at?: string
}

export interface PluginJob {
  job_id: string
  kind: string
  label: string
  plugin_id?: string
  status: 'queued' | 'running' | 'done' | 'error'
  stage: string
  progress: number
  message: string
  error: string
  result?: PluginInstalledItem | null
  created_at: string
  updated_at: string
}

export interface PluginUpdateItem {
  plugin_id: string
  name: string
  current_version: string
  latest_version: string
  dist?: PluginDist
}

export interface PluginAuditItem {
  id: number
  plugin_id: string
  version?: string
  action: string
  result: string
  detail?: Record<string, unknown>
  created_at: string
}

export interface PluginInstallPayload {
  source: 'market' | 'url' | 'remote'
  plugin_id?: string
  version?: string
  url?: string
  sha256?: string
  manifest?: Record<string, unknown>
  auto_enable?: boolean
}

export interface PluginStartInstallResult {
  job_id: string
  job: PluginJob | null
}
