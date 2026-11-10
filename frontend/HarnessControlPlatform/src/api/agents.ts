// MyAgent 档案接口：按当前演示用户读取、招募、编辑与删除主 Agent。
import { API_BASE } from '@/api/client'

export interface AgentModelConfig {
  model_name?: string
  base_url?: string
  api_key?: string
  has_api_key?: boolean
  thinking_enabled?: boolean
  context_limit?: number
}

export interface AgentProfile {
  id: string
  name: string
  role_title: string
  description: string
  avatar: string
  system_prompt: string
  model: AgentModelConfig
  mcp_server_ids: string[]
  enabled: boolean
  is_default: boolean
  created_at: string
  updated_at: string
}

export interface AgentPayload {
  name: string
  role_title?: string
  description?: string
  avatar?: string
  system_prompt?: string
  model?: AgentModelConfig
  mcp_server_ids?: string[]
  enabled?: boolean
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    ...init,
  })
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}))
    throw new Error((detail as { detail?: string }).detail || `HTTP ${res.status}`)
  }
  return (await res.json()) as T
}

export async function fetchAgents(): Promise<{
  items: AgentProfile[]
  default_agent_id: string
}> {
  return request('/agents')
}

export async function createAgent(payload: AgentPayload): Promise<AgentProfile> {
  return request('/agents', { method: 'POST', body: JSON.stringify(payload) })
}

export async function updateAgent(
  agentId: string,
  payload: Partial<AgentPayload>,
): Promise<AgentProfile> {
  return request(`/agents/${encodeURIComponent(agentId)}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export async function deleteAgent(agentId: string): Promise<{ ok: boolean }> {
  return request(`/agents/${encodeURIComponent(agentId)}`, { method: 'DELETE' })
}
