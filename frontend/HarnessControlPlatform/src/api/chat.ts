import { API_BASE } from './client'

export interface SessionUsage {
  input: number
  output: number
  total: number
  spent?: number
  rounds?: number
  context_window?: number
  context_used?: number
}

export interface WithdrawResult {
  thread_id: string
  content: string
}

/** 补拉会话累计用量；用于旧前端状态缺少 spent 时校正统计。 */
export async function fetchSessionUsage(
  threadId: string,
  userId: string,
): Promise<SessionUsage | null> {
  const query = `user_id=${encodeURIComponent(userId)}`
  const usageRes = await fetch(
    `${API_BASE}/history/${threadId}/usage?${query}`,
  )
  if (usageRes.ok) {
    return (await usageRes.json()) as SessionUsage
  }
  // 兼容尚未重启的旧后端：从历史消息响应里取 session_usage。
  const messagesRes = await fetch(
    `${API_BASE}/history/${threadId}/messages?${query}`,
  )
  if (!messagesRes.ok) return null
  const data = (await messagesRes.json()) as { session_usage?: SessionUsage }
  return data.session_usage ?? null
}

/** 撤回会话中最新一条用户消息；后端始终以 head 中的最新用户消息为准。 */
export async function withdrawLatestMessage(
  threadId: string,
  userId: string,
  username: string,
): Promise<WithdrawResult> {
  const res = await fetch(`${API_BASE}/chat/${threadId}/withdraw`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId, username }),
  })
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}))
    const message = (detail as { detail?: string }).detail || `HTTP ${res.status}`
    throw new Error(message)
  }
  return (await res.json()) as WithdrawResult
}
