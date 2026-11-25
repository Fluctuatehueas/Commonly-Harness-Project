// 历史消息转换：主对话页与悬浮助手共用，保证两处对同一份后端数据的分组一致。
import { categoryOf, type ChatMsg, type MessageTokens } from '@/stores/chat'
import type { ContextFile } from '@/api/context'

export interface HistoryMessage {
  id: string
  role: string
  content?: string
  tool_name?: string
  tool_status?: string
  tool_call_id?: string
  text?: string
  args?: string
  source?: string
  tokens?: { input: number; output: number; total: number }
  feedback?: 'like' | 'dislike' | null
  created_at?: string
  // 用户消息随消息发送的附件元信息；旧会话没有该字段。
  attachments?: ContextFile[]
  // 本轮应答的 Agent id；旧会话没有该字段，前端回落到当前选择。
  agent_id?: string
}

export interface HistorySessionUsage {
  input: number
  output: number
  total: number
  spent?: number
  rounds?: number
  context_window?: number
  context_used?: number
}

export interface HistoryPayload {
  messages: HistoryMessage[]
  interrupt: Record<string, unknown> | null
  session_usage?: HistorySessionUsage
}

/** 历史消息自带 created_at，转成毫秒供气泡底部显示时间。 */
function messageTime(value?: string): number | undefined {
  const parsed = Date.parse(value || '')
  return Number.isFinite(parsed) ? parsed : undefined
}

/** 把 /history/{id}/messages 的响应转成聊天 store 的消息列表。 */
export function mapHistoryMessages(data: HistoryPayload): {
  messages: ChatMsg[]
  usage: MessageTokens | null
} {
  const messages: ChatMsg[] = data.messages.map((m, i) => {
    const at = messageTime(m.created_at)
    if (m.role === 'user') {
      return {
        id: `h-u-${i}`,
        kind: 'user',
        content: m.content,
        attachments: m.attachments,
        agentId: m.agent_id,
        at,
      }
    }
    if (m.role === 'reasoning') {
      return {
        id: m.id || `h-r-${i}`,
        kind: 'reasoning',
        content: m.content,
        source: m.source || 'main',
        at,
      }
    }
    if (m.role === 'assistant') {
      return {
        id: m.id || `h-a-${i}`,
        kind: 'assistant',
        content: m.content,
        tokens: m.tokens,
        feedback: m.feedback,
        at,
      }
    }
    const name = m.tool_name || 'tool'
    return {
      id: `h-t-${i}`,
      kind: 'tool',
      toolCallId: m.tool_call_id || m.id || `h-t-${i}`,
      toolName: name,
      category: categoryOf(name),
      args: m.args || '',
      result: m.text || '',
      status:
        m.tool_status === 'error'
          ? 'error'
          : m.tool_status === 'calling'
            ? 'calling'
            : 'done',
      source: m.source,
      subagentName:
        m.role === 'delegation' && m.source && m.source !== 'main'
          ? m.source
          : undefined,
    }
  })

  if (data.interrupt) {
    const it = data.interrupt as { interrupt_id?: string; interrupt_type?: string }
    messages.push({
      id: `h-i-${Date.now()}`,
      kind: 'interrupt',
      interruptId: it.interrupt_id,
      interruptType: it.interrupt_type || 'unknown',
      payload: data.interrupt,
    })
  }

  const usage = data.session_usage
    ? {
        input: data.session_usage.input,
        output: data.session_usage.output,
        total: data.session_usage.total,
        spent: data.session_usage.spent,
        rounds: data.session_usage.rounds,
        contextWindow: data.session_usage.context_window,
        contextUsed: data.session_usage.context_used,
      }
    : null

  return { messages, usage }
}
