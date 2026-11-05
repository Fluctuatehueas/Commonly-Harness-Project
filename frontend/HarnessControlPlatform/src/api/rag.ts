// RAG Provider 管理接口：Harness 只做注册、检索编排与灌入转发。
import { API_BASE } from './client'

export type RagState = 'Unknown' | 'Ready' | 'Degraded' | 'Unavailable' | 'Disabled'

export interface RagProviderItem {
  provider_id: string
  name: string
  description: string
  protocol: 'http' | 'mcp'
  endpoint: string
  mcp_server_id: string
  auth: Record<string, unknown>
  capabilities: string[]
  knowledge_domains: string[]
  citation_format: string
  limits: Record<string, unknown>
  tools: Record<string, string>
  timeout_ms: number
  enabled: boolean
  source: string
  integration_server_id: string
  has_secret: boolean
  state: RagState
  ready: boolean
  last_error: string
  last_checked_at: string | null
  latency_ms: number | null
  document_count: number | null
}

export interface RagProviderPayload {
  provider_id: string
  name: string
  description: string
  protocol: 'http' | 'mcp'
  endpoint: string
  mcp_server_id: string
  auth: Record<string, unknown>
  capabilities: string[]
  knowledge_domains: string[]
  citation_format: string
  limits: Record<string, unknown>
  tools: Record<string, string>
  timeout_ms: number
  enabled: boolean
  integration_server_id: string
}

export interface RagChunk {
  citation_id: string
  content: string
  score?: number | null
  source: Record<string, unknown>
  metadata: Record<string, unknown>
  provider_id: string
  provider_name: string
}

export interface RagRetrieveResult {
  query: string
  top_k: number
  chunks: RagChunk[]
  providers: string[]
  errors: { provider_id: string; message: string }[]
  note?: string
}

export interface RagDocumentItem {
  document_id?: string
  id?: string
  title?: string
  name?: string
  status?: string
  updated_at?: string
  [key: string]: unknown
}

export interface RagDocumentList {
  provider_id: string
  documents: RagDocumentItem[]
  next_cursor?: string
  has_more?: boolean
  [key: string]: unknown
}

async function readError(res: Response, fallback: string): Promise<Error> {
  const data = (await res.json().catch(() => ({}))) as { detail?: unknown }
  const detail = data.detail
  if (typeof detail === 'string' && detail) return new Error(detail)
  if (detail && typeof detail === 'object') {
    const value = detail as { message?: string; code?: string }
    return new Error(value.message || value.code || fallback)
  }
  return new Error(`${fallback}: HTTP ${res.status}`)
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { cache: 'no-store', ...init })
  if (!res.ok) throw await readError(res, `请求失败 ${path}`)
  return (await res.json()) as T
}

export function fetchRagProviders(params: {
  protocol?: string
  enabled?: boolean
  integration_server_id?: string
} = {}): Promise<{ items: RagProviderItem[]; total: number }> {
  const query = new URLSearchParams()
  if (params.protocol) query.set('protocol', params.protocol)
  if (params.enabled !== undefined) query.set('enabled', String(params.enabled))
  if (params.integration_server_id) {
    query.set('integration_server_id', params.integration_server_id)
  }
  const suffix = query.toString()
  return request(`/rag/providers${suffix ? `?${suffix}` : ''}`)
}

export function fetchRagProvider(providerId: string): Promise<RagProviderItem> {
  return request(`/rag/providers/${encodeURIComponent(providerId)}`)
}

export function createRagProvider(payload: RagProviderPayload): Promise<RagProviderItem> {
  return request('/rag/providers', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function updateRagProvider(
  providerId: string,
  payload: RagProviderPayload,
): Promise<RagProviderItem> {
  return request(`/rag/providers/${encodeURIComponent(providerId)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function deleteRagProvider(providerId: string): Promise<{ ok: boolean }> {
  return request(`/rag/providers/${encodeURIComponent(providerId)}`, {
    method: 'DELETE',
  })
}

export function setRagEnabled(
  providerId: string,
  enabled: boolean,
): Promise<RagProviderItem> {
  return request(`/rag/providers/${encodeURIComponent(providerId)}/enable`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  })
}

export function probeRagProvider(providerId: string): Promise<RagProviderItem> {
  return request(`/rag/providers/${encodeURIComponent(providerId)}/probe`, {
    method: 'POST',
  })
}

export function retrieveRag(payload: {
  query: string
  top_k?: number
  provider_ids?: string[]
  filters?: Record<string, unknown>
}): Promise<RagRetrieveResult> {
  return request('/rag/retrieve', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function ingestRag(
  providerId: string,
  documents: Record<string, unknown>[],
  options: Record<string, unknown> = {},
): Promise<Record<string, unknown>> {
  return request(`/rag/providers/${encodeURIComponent(providerId)}/ingest`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ documents, options }),
  })
}

export function fetchRagDocuments(
  providerId: string,
  limit = 50,
  cursor = '',
): Promise<RagDocumentList> {
  const query = new URLSearchParams({ limit: String(limit) })
  if (cursor) query.set('cursor', cursor)
  return request(
    `/rag/providers/${encodeURIComponent(providerId)}/knowledge/documents?${query.toString()}`,
  )
}

export function deleteRagDocument(
  providerId: string,
  documentId: string,
): Promise<Record<string, unknown>> {
  return request(
    `/rag/providers/${encodeURIComponent(providerId)}/knowledge/documents/${encodeURIComponent(documentId)}`,
    { method: 'DELETE' },
  )
}

export function fetchRagStats(providerId: string): Promise<Record<string, unknown>> {
  return request(`/rag/providers/${encodeURIComponent(providerId)}/knowledge/stats`)
}

export function discoverRagProvider(payload: {
  mcp_server_id: string
  discovery_tool?: string
  auto_register?: boolean
}): Promise<Record<string, unknown>> {
  return request('/rag/discover', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}
