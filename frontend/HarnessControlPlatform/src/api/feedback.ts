import { API_BASE } from './client'

export type FeedbackAction = 'like' | 'dislike' | 'none'

export interface FeedbackPayload {
  user_id: string
  thread_id: string
  message_id: string
  action: FeedbackAction
  user_excerpt?: string
  answer_excerpt?: string
  meta?: Record<string, unknown>
}

export interface FeedbackResult {
  message_id: string
  action: string
}

/** 写入一条消息反馈；失败时抛出可展示的错误。 */
export async function submitFeedback(payload: FeedbackPayload): Promise<FeedbackResult> {
  const res = await fetch(`${API_BASE}/feedback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}))
    const message = (detail as { detail?: string }).detail || `HTTP ${res.status}`
    throw new Error(message)
  }
  return (await res.json()) as FeedbackResult
}
