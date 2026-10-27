// 对话测试：SSE 流式状态机 + 演示模式
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { API_BASE } from '@/api/client'
import type { ContextFile } from '@/api/context'

export type ToolCategory = 'tool' | 'skill' | 'delegation'

export interface MessageTokens {
  input: number
  output: number
  total: number
  rounds?: number
}

export interface ChatMsg {
  id: string
  kind: 'user' | 'assistant' | 'tool' | 'interrupt' | 'system'
  content?: string
  attachments?: ContextFile[]
  streaming?: boolean
  tokens?: MessageTokens
  toolCallId?: string
  toolName?: string
  category?: ToolCategory
  args?: string
  result?: string
  status?: 'calling' | 'done' | 'error'
  source?: string
  subagentName?: string
  interruptId?: string
  interruptType?: string
  payload?: Record<string, unknown>
  resolved?: 'answered' | 'approved' | 'rejected'
}

interface SseEvent {
  type: string
  [key: string]: unknown
}

const SKILL_TOOLS = new Set([
  'download_skill',
  'assign_skill',
  'list_subagent_skills',
  'delete_subagent_skill',
  'update_subagent_skill',
])
const DELEGATION_TOOLS = new Set(['task', 'start_async_task'])

let seq = 0
const uid = (prefix: string) => `${prefix}-${Date.now()}-${seq++}`

function categoryOf(name: string): ToolCategory {
  if (SKILL_TOOLS.has(name)) return 'skill'
  if (DELEGATION_TOOLS.has(name)) return 'delegation'
  return 'tool'
}

export { categoryOf }

export const useChatStore = defineStore('chat', () => {
  const messages = ref<ChatMsg[]>([])
  const threadId = ref<string | null>(null)
  const sending = ref(false)
  const thinking = ref(false) // 已发出请求但还没收到第一个 token
  const pendingInterrupt = ref<ChatMsg | null>(null)
  const demoMode = ref(true) // 后端未启动时也能完整预览 UI
  const lastError = ref('')
  // 会话轮次上限：由后端 done 事件下发（MAIN_AGENT_MODEL_RUN_LIMIT），默认 50
  const roundLimit = ref(50)
  // 本会话累计 Token 消耗：后端 TokenUsageMiddleware 持久化在 Store，done/历史接口下发
  const sessionTokens = ref<MessageTokens | null>(null)
  let ctrl: AbortController | null = null

  const isEmpty = computed(() => messages.value.length === 0)

  function push(msg: ChatMsg) {
    messages.value.push(msg)
  }

  function findTool(toolCallId: string) {
    return messages.value.find((m) => m.kind === 'tool' && m.toolCallId === toolCallId)
  }

  // 单个 SSE 事件 → 消息列表变更
  function handleEvent(evt: SseEvent) {
    lastError.value = ''
    switch (evt.type) {
      case 'token': {
        thinking.value = false
        const last = messages.value[messages.value.length - 1]
        if (last && last.kind === 'assistant' && last.streaming) {
          last.content = (last.content || '') + String(evt.content || '')
        } else {
          push({
            id: uid('a'),
            kind: 'assistant',
            content: String(evt.content || ''),
            streaming: true,
          })
        }
        break
      }
      case 'tool_start': {
        thinking.value = false
        push({
          id: uid('t'),
          kind: 'tool',
          toolCallId: String(evt.tool_call_id),
          toolName: String(evt.tool_name || 'tool'),
          category: categoryOf(String(evt.tool_name || '')),
          args: '',
          result: '',
          status: 'calling',
          source: String(evt.source || 'main'),
          subagentName: evt.subagent_name ? String(evt.subagent_name) : undefined,
        })
        break
      }
      case 'tool_args': {
        const tool = findTool(String(evt.tool_call_id))
        if (tool) tool.args = (tool.args || '') + String(evt.args || '')
        break
      }
      case 'tool_result': {
        const tool = findTool(String(evt.tool_call_id))
        if (tool) {
          tool.result = String(evt.text || '')
          tool.status = evt.status === 'error' ? 'error' : 'done'
          if (evt.subagent_name) tool.subagentName = String(evt.subagent_name)
        }
        break
      }
      case 'tool_end': {
        const tool = findTool(String(evt.tool_call_id))
        if (tool && tool.status === 'calling') tool.status = 'done'
        break
      }
      case 'interrupt': {
        const msg: ChatMsg = {
          id: uid('i'),
          kind: 'interrupt',
          interruptId: evt.interrupt_id ? String(evt.interrupt_id) : undefined,
          interruptType: String(evt.interrupt_type || 'unknown'),
          payload: evt as Record<string, unknown>,
        }
        push(msg)
        pendingInterrupt.value = msg
        break
      }
      case 'done': {
        if (evt.thread_id) threadId.value = String(evt.thread_id)
        if (typeof evt.round_limit === 'number' && evt.round_limit > 0) {
          roundLimit.value = evt.round_limit
        }
        // usage = 本轮最终回答的单次调用消耗，挂到最后一条正式回答上；
        // session_usage = 会话累计（Store 持久化，恢复历史时同样准确）。
        const usage = evt.usage as Partial<MessageTokens> | undefined
        if (usage && typeof usage.total === 'number' && usage.total > 0) {
          for (let i = messages.value.length - 1; i >= 0; i--) {
            const msg = messages.value[i]
            if (msg && msg.kind === 'assistant' && msg.content) {
              msg.tokens = {
                input: usage.input || 0,
                output: usage.output || 0,
                total: usage.total,
              }
              break
            }
          }
        }
        const session = evt.session_usage as Partial<MessageTokens> | undefined
        if (session && typeof session.total === 'number') {
          sessionTokens.value = {
            input: session.input || 0,
            output: session.output || 0,
            total: session.total,
            rounds: session.rounds || 0,
          }
        }
        for (const m of messages.value) if (m.streaming) m.streaming = false
        if (evt.interrupted) {
          // 中断卡片保持可操作
        } else {
          pendingInterrupt.value = null
        }
        sending.value = false
        thinking.value = false
        break
      }
      case 'error': {
        lastError.value = String(evt.message || '未知错误')
        push({ id: uid('s'), kind: 'system', content: `⚠ ${lastError.value}` })
        sending.value = false
        thinking.value = false
        break
      }
    }
  }

  // 解析 SSE 流：data: {...}\n\n
  async function consumeStream(res: Response) {
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}))
      throw new Error((detail as { detail?: string }).detail || `HTTP ${res.status}`)
    }
    const reader = res.body!.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let index = buffer.indexOf('\n\n')
      while (index >= 0) {
        const frame = buffer.slice(0, index)
        buffer = buffer.slice(index + 2)
        for (const line of frame.split('\n')) {
          if (line.startsWith('data: ')) {
            try {
              handleEvent(JSON.parse(line.slice(6)) as SseEvent)
            } catch {
              /* 忽略非 JSON 帧 */
            }
          }
        }
        index = buffer.indexOf('\n\n')
      }
    }
  }

  async function streamPost(path: string, body: unknown) {
    ctrl = new AbortController()
    const res = await fetch(`${API_BASE}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: ctrl.signal,
    })
    await consumeStream(res)
  }

  // 网络层错误转成人话提示，其余原样展示
  function friendlyError(error: Error): string {
    if (error instanceof TypeError) {
      return `无法连接后端 ${API_BASE}：请确认 FastAPI 已启动（python main.py），或开启演示模式`
    }
    return error.message
  }

  function send(text: string, attachments: ContextFile[] = []) {
    const trimmed = text.trim()
    if ((!trimmed && attachments.length === 0) || sending.value) return
    // 只有附件没有文字时给模型一个明确的默认指令，后端要求 message 非空。
    const message = trimmed || '请阅读我上传的附件，并简要说明内容。'
    push({ id: uid('u'), kind: 'user', content: trimmed, attachments })
    sending.value = true
    thinking.value = true
    if (demoMode.value) {
      void runDemo(message)
      return
    }
    void streamPost('/chat/stream', {
      message,
      thread_id: threadId.value,
      user_id: 'console_user',
      username: '控制台用户',
      attachments: attachments.map((item) => item.id),
    }).catch((error: Error) => {
      handleEvent({ type: 'error', message: friendlyError(error) })
    })
  }

  // HITL 恢复：补充信息 / 审批决策
  function resume(interrupt: ChatMsg, payload: Record<string, unknown>, markAs: 'answered' | 'approved' | 'rejected') {
    if (pendingInterrupt.value !== interrupt) return
    interrupt.resolved = markAs
    pendingInterrupt.value = null
    sending.value = true
    thinking.value = true
    if (demoMode.value) {
      void runDemoResume(markAs)
      return
    }
    void streamPost(`/chat/${threadId.value}/resume`, {
      resume: payload,
      user_id: 'console_user',
      username: '控制台用户',
    }).catch((error: Error) => {
      handleEvent({ type: 'error', message: friendlyError(error) })
    })
  }

  function stop() {
    ctrl?.abort()
    ctrl = null
    sending.value = false
    thinking.value = false
  }

  function newChat() {
    stop()
    messages.value = []
    threadId.value = null
    pendingInterrupt.value = null
    lastError.value = ''
    sessionTokens.value = null
    demoSessionTokens = { input: 0, output: 0, total: 0 }
  }

  // 切换会话：载入历史消息（由视图层从 /history 映射而来）
  function loadHistory(msgs: ChatMsg[], tid: string, sessionUsage?: MessageTokens | null) {
    stop()
    messages.value = msgs
    threadId.value = tid
    pendingInterrupt.value = msgs.find((m) => m.kind === 'interrupt' && !m.resolved) || null
    lastError.value = ''
    sessionTokens.value = sessionUsage && sessionUsage.total > 0 ? sessionUsage : null
  }

  // ---------- 演示模式：脚本化 SSE 事件，覆盖全部卡片类型 ----------
  const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))
  // 演示模式本地累计，模拟后端 Store 的会话统计
  let demoSessionTokens: MessageTokens = { input: 0, output: 0, total: 0, rounds: 0 }

  function demoDone(interrupted: boolean, usage: MessageTokens) {
    // 与后端口径一致：input 取最近一轮，output 累加历轮生成。
    demoSessionTokens = {
      input: usage.input,
      output: demoSessionTokens.output + usage.output,
      total: usage.input + demoSessionTokens.output + usage.output,
      rounds: (demoSessionTokens.rounds || 0) + 1,
    }
    handleEvent({
      type: 'done',
      thread_id: threadId.value || 'demo-thread',
      user_id: 'console_user',
      username: '控制台用户',
      content: '',
      interrupted,
      usage,
      session_usage: { ...demoSessionTokens },
    })
  }

  async function typeOut(text: string) {
    push({ id: uid('a'), kind: 'assistant', content: '', streaming: true })
    const msg = messages.value[messages.value.length - 1]!
    for (const ch of text) {
      msg.content = (msg.content ?? '') + ch
      await sleep(28)
    }
    msg.streaming = false
  }

  async function runDemo(userText: string) {
    await sleep(400)
    thinking.value = false
    await typeOut(`收到「${userText.slice(0, 18)}」。我先读取你的偏好文件，再检索资料。`)
    handleEvent({ type: 'tool_start', tool_call_id: 'call-1', tool_name: 'read_file', source: 'main' })
    await sleep(300)
    handleEvent({ type: 'tool_args', tool_call_id: 'call-1', args: '{"path": "/memories/console_user/preferences.md"}', source: 'main' })
    await sleep(600)
    handleEvent({ type: 'tool_result', tool_call_id: 'call-1', tool_name: 'read_file', status: 'done', text: 'preferred:\n  language: zh-CN\n  output_style: concise\nrecent_queries:\n  - 控制台演示', source: 'main' })
    handleEvent({ type: 'tool_end', tool_call_id: 'call-1', status: 'done', source: 'main' })

    handleEvent({ type: 'tool_start', tool_call_id: 'call-2', tool_name: 'web_search', source: 'main' })
    await sleep(250)
    handleEvent({ type: 'tool_args', tool_call_id: 'call-2', args: '{"query": "Harness 多智能体架构 最佳实践"}', source: 'main' })
    await sleep(900)
    handleEvent({ type: 'tool_result', tool_call_id: 'call-2', tool_name: 'web_search', status: 'done', text: '1. 编排层评估（LangGraph 状态图）…\n2. 上下文工程：卸载/摘要/隔离…\n3. HITL 以 checkpoint 落地…', source: 'main' })
    handleEvent({ type: 'tool_end', tool_call_id: 'call-2', status: 'done', source: 'main' })

    handleEvent({ type: 'tool_start', tool_call_id: 'call-3', tool_name: 'assign_skill', source: 'main' })
    await sleep(250)
    handleEvent({ type: 'tool_args', tool_call_id: 'call-3', args: '{"skill_name": "report-writer", "subagent_name": "main"}', source: 'main' })
    await sleep(700)
    handleEvent({ type: 'tool_result', tool_call_id: 'call-3', tool_name: 'assign_skill', status: 'done', text: '{"ok": true, "skill_name": "report-writer", "target": "main", "path": "/skills/main/report-writer"}', source: 'main' })
    handleEvent({ type: 'tool_end', tool_call_id: 'call-3', status: 'done', source: 'main' })

    handleEvent({ type: 'tool_start', tool_call_id: 'call-4', tool_name: 'task', source: 'main', subagent_name: 'research_agent' })
    await sleep(250)
    handleEvent({ type: 'tool_args', tool_call_id: 'call-4', args: '{"subagent_type": "research_agent", "description": "汇总检索结果并给出结构化结论"}', source: 'main' })
    await sleep(900)
    handleEvent({ type: 'tool_result', tool_call_id: 'call-4', tool_name: 'task', status: 'done', text: '子 Agent 报告：三条核心结论已生成，建议以 Markdown 输出。', source: 'research_agent' })
    handleEvent({ type: 'tool_end', tool_call_id: 'call-4', status: 'done', source: 'main' })

    await typeOut('整理完成，共 3 条核心结论。输出前需要确认一件事：')
    handleEvent({
      type: 'interrupt',
      thread_id: threadId.value || 'demo-thread',
      interrupt_id: 'int-demo',
      interrupt_type: 'information_request',
      information_needed: '请确认输出格式：Markdown 还是表格？',
      context: '已生成初稿，等待格式确认后交付。',
    })
    demoDone(true, { input: 6056, output: 42, total: 6098 })
  }

  async function runDemoResume(markAs: 'answered' | 'approved' | 'rejected') {
    await sleep(500)
    thinking.value = false
    if (markAs === 'rejected') {
      await typeOut('好的，已取消本次交付。需要调整时随时告诉我。')
    } else {
      await typeOut('已按 Markdown 输出最终报告：')
      push({
        id: uid('a'),
        kind: 'assistant',
        content:
          '# Harness 多智能体实践结论\n\n1. **先可观测再优化**：没有 trace 与评估集，任何改动都是盲改。\n2. **上下文分层**：卸载 → 摘要 → 主动压缩，三层共用同一状态。\n3. **HITL 落 checkpoint**：等待人 = 可恢复，而不是阻塞。',
      })
    }
    demoDone(false, { input: 6122, output: 58, total: 6180 })
  }

  return {
    messages,
    threadId,
    sending,
    thinking,
    pendingInterrupt,
    demoMode,
    lastError,
    roundLimit,
    sessionTokens,
    isEmpty,
    send,
    resume,
    stop,
    newChat,
    loadHistory,
  }
})
