// AgentTeam 组接口：组 CRUD、成员与组长操作。
import { API_BASE } from '@/api/client'

export interface AgentGroupModel {
  model_name?: string
  base_url?: string
  api_key?: string
  has_api_key?: boolean
  thinking_enabled?: boolean
  context_limit?: number
}

export interface AgentGroupMember {
  agent_id: string
  role: 'leader' | 'member'
  duty: string
  joined_at?: string
}

export interface AgentGroup {
  group_id: string
  name: string
  summary: string
  leader_agent_id: string
  visibility: 'private' | 'discoverable'
  extra_subagent_names: string[]
  model: AgentGroupModel
  members: AgentGroupMember[]
  created_at?: string
  updated_at?: string
}

export interface AgentGroupPayload {
  name: string
  summary?: string
  leader_agent_id?: string
  visibility?: 'private' | 'discoverable'
  members?: { agent_id: string; duty?: string }[]
  extra_subagent_names?: string[]
  model?: AgentGroupModel
}

interface ValidationIssue {
  loc?: (string | number)[]
  msg?: string
}

/** FastAPI 的 detail 可能是字符串，也可能是参数校验错误数组，这里统一转成可读文本。 */
function detailToMessage(detail: unknown, fallback: string): string {
  if (typeof detail === 'string' && detail.trim()) return detail
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => {
        if (typeof item === 'string') return item
        if (item && typeof item === 'object') {
          const issue = item as ValidationIssue
          const field = (issue.loc || [])
            .filter((part) => part !== 'body' && part !== 'query' && part !== 'path')
            .join('.')
          const message = issue.msg || '参数不合法'
          return field ? `${field}: ${message}` : message
        }
        return String(item)
      })
      .filter(Boolean)
    if (messages.length) return messages.join('；')
  }
  if (detail && typeof detail === 'object') {
    const message = (detail as { message?: unknown }).message
    if (typeof message === 'string' && message) return message
    return JSON.stringify(detail)
  }
  return fallback
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(
      detailToMessage((body as { detail?: unknown }).detail, `HTTP ${res.status}`),
    )
  }
  return (await res.json()) as T
}

export async function fetchAgentGroups(): Promise<{ items: AgentGroup[] }> {
  return request('/agent-groups')
}

export async function createAgentGroup(payload: AgentGroupPayload): Promise<AgentGroup> {
  return request('/agent-groups', { method: 'POST', body: JSON.stringify(payload) })
}

export async function updateAgentGroup(
  groupId: string,
  payload: Partial<AgentGroupPayload>,
): Promise<AgentGroup> {
  return request(`/agent-groups/${encodeURIComponent(groupId)}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export async function deleteAgentGroup(groupId: string): Promise<{ ok: boolean }> {
  return request(`/agent-groups/${encodeURIComponent(groupId)}`, { method: 'DELETE' })
}

export async function addAgentGroupMember(
  groupId: string,
  agentId: string,
  duty = '',
): Promise<AgentGroup> {
  return request(`/agent-groups/${encodeURIComponent(groupId)}/members`, {
    method: 'POST',
    body: JSON.stringify({ agent_id: agentId, duty }),
  })
}

export async function removeAgentGroupMember(
  groupId: string,
  agentId: string,
): Promise<AgentGroup> {
  return request(
    `/agent-groups/${encodeURIComponent(groupId)}/members/${encodeURIComponent(agentId)}`,
    { method: 'DELETE' },
  )
}

export async function transferAgentGroupLeader(
  groupId: string,
  agentId: string,
): Promise<AgentGroup> {
  return request(`/agent-groups/${encodeURIComponent(groupId)}/leader`, {
    method: 'PUT',
    body: JSON.stringify({ agent_id: agentId }),
  })
}

export async function clearAgentGroupLeader(groupId: string): Promise<AgentGroup> {
  return request(`/agent-groups/${encodeURIComponent(groupId)}/leader`, {
    method: 'DELETE',
  })
}
