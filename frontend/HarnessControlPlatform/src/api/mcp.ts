// MCP 服务管理接口
import { API_BASE } from './client'

export type McpState =
  | 'Dormant'
  | 'Disabled'
  | 'Starting'
  | 'Connected'
  | 'Degraded'
  | 'Pending'
  | 'NeedsAuth'
  | 'Failed'
  | 'Stopping'

export interface McpConnection {
  transport?: string
  command?: string
  args?: string[]
  env?: Record<string, string>
  cwd?: string
  url?: string
  headers?: Record<string, string>
  [key: string]: unknown
}

export interface McpServerItem {
  server_id: string
  description: string
  transport: string
  connection: McpConnection
  enabled: boolean
  preconnect: boolean
  /** 用户自有后端项目封装成的 MCP（集成模式）。 */
  integration: boolean
  criticality: 'required' | 'optional'
  namespace: string
  allow_tools: string[]
  deny_tools: string[]
  connect_timeout_ms: number
  call_timeout_ms: number
  tags: string[]
  has_secret: boolean
  state: McpState
  error: string
  error_code: string
  error_category: string
  retry_at: string | null
  retry_count: number
  epoch: number
  connected_at: string | null
  last_used_at: string | null
  latency_ms: number | null
  tool_count: number
  tool_source: 'none' | 'lkg_cache' | 'live_discovery'
  pool_reused: boolean
  pool_key: string
  connected: boolean
  conflicts?: Record<string, string>
}

export interface McpTool {
  server_id: string
  remote_name: string
  exposed_name: string
  description: string
  input_schema: Record<string, unknown>
  allowlisted: boolean
  source: string
}

export interface McpPoolStats {
  size: number
  hits: number
  misses: number
  evictions: number
  idle_ttl_ms: number
  max_pool_size: number
}

export interface McpServerDetail extends McpServerItem {
  tools: McpTool[]
  pool_stats: McpPoolStats
}

export interface McpServerList {
  items: McpServerItem[]
  total: number
  page: number
  page_size: number
  pages: number
  config_error: string
  pool: McpPoolStats
}

export interface McpRuntimeInfo {
  initialized: boolean
  config_error: string
  pool: McpPoolStats
  server_count: number
  main_servers: string[]
}

export interface McpLogEntry {
  ts: string
  event: string
  server_id: string
  level: string
  message?: string
  [key: string]: unknown
}

export interface McpServerPayload {
  server_id: string
  description: string
  transport: string
  command?: string | null
  args: string[]
  env: Record<string, string>
  cwd?: string | null
  url?: string | null
  headers: Record<string, string>
  enabled: boolean
  preconnect: boolean
  integration: boolean
  criticality: 'required' | 'optional'
  namespace: string
  allow_tools: string[]
  deny_tools: string[]
  connect_timeout_ms: number
  call_timeout_ms: number
  tags: string[]
}

export interface McpConfigInfo {
  format: 'json'
  config_path: string
  root: string
  config_error: string
}

export interface McpRuntimeProbe {
  command: string
  label: string
  available: boolean
  path: string
  version: string
  min_version: string
  satisfied: boolean
  download: string
  /** 下载链接实际会下载什么，用于 Info 提示 */
  download_target?: string
  hint: string
  /** 是否建议下载的说明 */
  advice?: string
  message?: string
}

export interface McpNpmCacheInfo {
  available: boolean
  command: string
  /** 系统 npm 缓存目录 */
  cache_dir: string
  writable: boolean
  error: string
  /** MCP 服务运行态根目录（项目内 .mcp） */
  workspace_root: string
}

export interface McpPreflightServer {
  server_id: string
  transport: 'stdio' | 'network' | ''
  command?: string
  ready: boolean
  requires_api_key?: boolean
  error?: string
  checks: McpRuntimeProbe[]
  hint: string
  /** npx / npm 型服务实际使用的项目内缓存目录 */
  cache_dir?: string
}

export interface McpPreflightResult {
  servers: McpPreflightServer[]
  invalid: McpPreflightServer[]
  ready: boolean
  requires_api_key: string[]
  npm_cache?: McpNpmCacheInfo | null
}

export interface McpImportResult {
  ok: boolean
  imported: string[]
  failed: { server_id: string; error: string }[]
  servers: McpServerDetail[]
  config_path: string
}

async function readError(res: Response, fallback: string): Promise<Error> {
  const data = (await res.json().catch(() => ({}))) as {
    detail?: unknown
  }
  const detail = data.detail
  if (typeof detail === 'string' && detail) return new Error(detail)
  if (detail && typeof detail === 'object') {
    const value = detail as { message?: string; code?: string }
    return new Error(value.message || value.code || fallback)
  }
  return new Error(`${fallback}: HTTP ${res.status}`)
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    cache: 'no-store',
    ...init,
  })
  if (!res.ok) throw await readError(res, `请求失败 ${path}`)
  return (await res.json()) as T
}

export function fetchMcpRuntime(): Promise<McpRuntimeInfo> {
  return request('/mcp/runtime')
}

export function fetchMcpServers(params: {
  page?: number
  page_size?: number
  keyword?: string
  state?: string
  enabled?: boolean
  integration?: boolean
} = {}): Promise<McpServerList> {
  const query = new URLSearchParams()
  query.set('page', String(params.page ?? 1))
  query.set('page_size', String(params.page_size ?? 20))
  if (params.keyword) query.set('keyword', params.keyword)
  if (params.state) query.set('state', params.state)
  if (params.enabled !== undefined) query.set('enabled', String(params.enabled))
  if (params.integration !== undefined) {
    query.set('integration', String(params.integration))
  }
  return request(`/mcp/servers?${query.toString()}`)
}

/** 聊天侧栏【集成的项目】：只返回用户自有后端 MCP，不触发连接。 */
export function fetchMcpIntegrations(): Promise<{
  items: McpServerItem[]
  total: number
}> {
  return request('/mcp/integrations')
}

export function fetchMcpServer(serverId: string): Promise<McpServerDetail> {
  return request(`/mcp/servers/${encodeURIComponent(serverId)}`)
}

export function createMcpServer(payload: McpServerPayload): Promise<McpServerDetail> {
  return request('/mcp/servers', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function updateMcpServer(
  serverId: string,
  payload: McpServerPayload,
): Promise<McpServerDetail> {
  return request(`/mcp/servers/${encodeURIComponent(serverId)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function deleteMcpServer(serverId: string): Promise<{ ok: boolean }> {
  return request(`/mcp/servers/${encodeURIComponent(serverId)}`, {
    method: 'DELETE',
  })
}

export function setMcpEnabled(
  serverId: string,
  enabled: boolean,
  discover = false,
): Promise<McpServerDetail> {
  return request(`/mcp/servers/${encodeURIComponent(serverId)}/enable`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled, discover }),
  })
}

export function probeMcpServer(
  serverId: string,
): Promise<{ probe: Record<string, unknown>; server: McpServerDetail }> {
  return request(`/mcp/servers/${encodeURIComponent(serverId)}/probe`, {
    method: 'POST',
  })
}

export function restartMcpServer(serverId: string): Promise<McpServerDetail> {
  return request(`/mcp/servers/${encodeURIComponent(serverId)}/restart`, {
    method: 'POST',
  })
}

export function fetchMcpTools(
  serverId: string,
  discover = false,
): Promise<{
  server_id: string
  state: McpState
  tools: McpTool[]
  conflicts: Record<string, string>
}> {
  return request(
    `/mcp/servers/${encodeURIComponent(serverId)}/tools?discover=${discover}`,
  )
}

export function testMcpTool(
  serverId: string,
  toolName: string,
  args: Record<string, unknown>,
): Promise<{ server_id: string; tool: string; state: McpState; text: string }> {
  return request(
    `/mcp/servers/${encodeURIComponent(serverId)}/tools/${encodeURIComponent(toolName)}/test`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ arguments: args }),
    },
  )
}

export function fetchMcpLogs(
  serverId: string,
  limit = 200,
): Promise<{ server_id: string; logs: McpLogEntry[] }> {
  return request(
    `/mcp/servers/${encodeURIComponent(serverId)}/logs?limit=${limit}`,
  )
}

export function fetchMcpConfig(): Promise<McpConfigInfo> {
  return request('/mcp/config')
}

export function preflightMcpJson(jsonText: string): Promise<McpPreflightResult> {
  return request('/mcp/preflight', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ json_text: jsonText }),
  })
}

export function importMcpJson(
  jsonText: string,
  enabled = true,
  integration = false,
): Promise<McpImportResult> {
  return request('/mcp/import', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ json_text: jsonText, enabled, integration }),
  })
}
