// 对话测试：按会话隔离的 SSE 状态机 + 演示模式
// 关键点：切换会话不 abort 正在运行的流，每个 thread 有独立消息缓冲区。
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

interface ThreadChat {
  messages: ChatMsg[]
  sending: boolean
  thinking: boolean
  pendingInterrupt: ChatMsg | null
  controller: AbortController | null
  sessionTokens: MessageTokens | null
}

const SKILL_TOOLS = new Set([
  'download_skill',
  'assign_skill',
  'list_subagent_skills',
  'delete_subagent_skill',
  'update_subagent_skill',
])
const DELEGATION_TOOLS = new Set(['task', 'start_async_task'])
const EMPTY_MESSAGES: ChatMsg[] = []

let seq = 0
const uid = (prefix: string) => `${prefix}-${Date.now()}-${seq++}`

function categoryOf(name: string): ToolCategory {
  if (SKILL_TOOLS.has(name)) return 'skill'
  if (DELEGATION_TOOLS.has(name)) return 'delegation'
  return 'tool'
}

export { categoryOf }

export const useChatStore = defineStore('chat', () => {
  // threadId -> 独立会话状态。SSE 事件始终写入发起请求的那个 thread。
  const threads = ref<Record<string, ThreadChat>>({})
  const threadId = ref<string | null>(null)
  const demoMode = ref(true)
  const lastError = ref('')
  const roundLimit = ref(50)
  // 每完成一轮自增，供侧边栏刷新标题/条数
  const turnVersion = ref(0)

  let pendingDemoIndex = 0

  function createThread(): ThreadChat {
    return {
      messages: [],
      sending: false,
      thinking: false,
      pendingInterrupt: null,
      controller: null,
      sessionTokens: null,
    }
  }

  function ensureThread(tid: string): ThreadChat {
    if (!threads.value[tid]) {
      threads.value[tid] = createThread()
    }
    // 必须返回响应式代理，而不是刚创建的原始对象：
    // 直接改原始对象的 sending 不会触发 runningThreadIds 等 computed。
    return threads.value[tid]!
  }

  function activateThread(tid: string | null) {
    if (tid) ensureThread(tid)
    threadId.value = tid
  }

  function releaseThread(tid: string) {
    const state = threads.value[tid]
    state?.controller?.abort()
    delete threads.value[tid]
    if (threadId.value === tid) threadId.value = null
  }

  function isThreadSending(tid: string) {
    return Boolean(threads.value[tid]?.sending)
  }

  const current = computed(() =>
    threadId.value ? threads.value[threadId.value] : undefined,
  )
  const messages = computed(() => current.value?.messages ?? EMPTY_MESSAGES)
  const sending = computed(() => current.value?.sending ?? false)
  const thinking = computed(() => current.value?.thinking ?? false)
  const pendingInterrupt = computed(() => current.value?.pendingInterrupt ?? null)
  const sessionTokens = computed(() => current.value?.sessionTokens ?? null)
  const isEmpty = computed(() => messages.value.length === 0)

  // 后台仍在运行的会话集合，供侧边栏动画使用
  const runningThreadIds = computed(() =>
    Object.entries(threads.value)
      .filter(([, state]) => state.sending)
      .map(([tid]) => tid),
  )
  const waitingThreadIds = computed(() =>
    Object.entries(threads.value)
      .filter(([, state]) => Boolean(state.pendingInterrupt && !state.pendingInterrupt.resolved))
      .map(([tid]) => tid),
  )

  function findTool(state: ThreadChat, toolCallId: string) {
    return state.messages.find(
      (m) => m.kind === 'tool' && m.toolCallId === toolCallId,
    )
  }

  function migrateThread(from: string, to: string) {
    if (!from || !to || from === to) return
    const state = threads.value[from]
    if (!state) return
    threads.value[to] = state
    delete threads.value[from]
    if (threadId.value === from) threadId.value = to
  }

  // 单个 SSE 事件 → 指定 thread 的消息列表变更
  function handleEvent(evt: SseEvent, state: ThreadChat, key: string) {
    lastError.value = ''
    switch (evt.type) {
      case 'token': {
        state.thinking = false
        const last = state.messages[state.messages.length - 1]
        if (last && last.kind === 'assistant' && last.streaming) {
          last.content = (last.content || '') + String(evt.content || '')
        } else {
          state.messages.push({
            id: uid('a'),
            kind: 'assistant',
            content: String(evt.content || ''),
            streaming: true,
          })
        }
        break
      }
      case 'tool_start': {
        state.thinking = false
        state.messages.push({
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
        const tool = findTool(state, String(evt.tool_call_id))
        if (tool) tool.args = (tool.args || '') + String(evt.args || '')
        break
      }
      case 'tool_result': {
        const tool = findTool(state, String(evt.tool_call_id))
        if (tool) {
          tool.result = String(evt.text || '')
          tool.status = evt.status === 'error' ? 'error' : 'done'
          if (evt.subagent_name) tool.subagentName = String(evt.subagent_name)
        }
        break
      }
      case 'tool_end': {
        const tool = findTool(state, String(evt.tool_call_id))
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
        state.messages.push(msg)
        state.pendingInterrupt = msg
        break
      }
      case 'done': {
        if (typeof evt.round_limit === 'number' && evt.round_limit > 0) {
          roundLimit.value = evt.round_limit
        }
        const realId = evt.thread_id ? String(evt.thread_id) : ''
        if (realId) migrateThread(key, realId)

        const usage = evt.usage as Partial<MessageTokens> | undefined
        if (usage && typeof usage.total === 'number' && usage.total > 0) {
          for (let i = state.messages.length - 1; i >= 0; i--) {
            const msg = state.messages[i]
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
          state.sessionTokens = {
            input: session.input || 0,
            output: session.output || 0,
            total: session.total,
            rounds: session.rounds || 0,
          }
        }
        for (const message of state.messages) {
          if (message.streaming) message.streaming = false
        }
        state.sending = false
        state.thinking = false
        state.controller = null
        if (!evt.interrupted) state.pendingInterrupt = null
        turnVersion.value++
        break
      }
      case 'error': {
        lastError.value = String(evt.message || '未知错误')
        state.messages.push({
          id: uid('s'),
          kind: 'system',
          content: `⚠ ${lastError.value}`,
        })
        state.sending = false
        state.thinking = false
        state.controller = null
        break
      }
    }
  }

  function isAbortError(error: unknown) {
    return error instanceof DOMException && error.name === 'AbortError'
  }

  // 解析 SSE 流：data: {...}\n\n
  async function consumeStream(res: Response, state: ThreadChat, key: string) {
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
              handleEvent(JSON.parse(line.slice(6)) as SseEvent, state, key)
            } catch {
              /* 忽略非 JSON 帧 */
            }
          }
        }
        index = buffer.indexOf('\n\n')
      }
    }
  }

  async function streamPost(path: string, body: unknown, state: ThreadChat, key: string) {
    state.controller = new AbortController()
    try {
      const res = await fetch(`${API_BASE}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: state.controller.signal,
      })
      await consumeStream(res, state, key)
    } catch (error) {
      if (isAbortError(error)) return
      handleEvent(
        { type: 'error', message: friendlyError(error as Error) },
        state,
        key,
      )
    }
  }

  // 网络层错误转成人话提示，其余原样展示
  function friendlyError(error: Error): string {
    if (error instanceof TypeError) {
      return `无法连接后端 ${API_BASE}：请确认 FastAPI 已启动（python main.py），或开启演示模式`
    }
    return error.message
  }

  function send(text: string, attachments: ContextFile[] = []) {
    if (sending.value) return
    if (!threadId.value) {
      // ChatView 正常会先建会话；演示模式或异常路径下用本地线程兜底
      if (!demoMode.value) {
        lastError.value = '会话尚未创建，请先新建会话'
        return
      }
      activateThread(`demo-${Date.now()}-${pendingDemoIndex++}`)
    }
    const key = threadId.value
    if (!key) return
    const state = ensureThread(key)
    const trimmed = text.trim()
    if (!trimmed && attachments.length === 0) return
    const message = trimmed || '请阅读我上传的附件，并简要说明内容。'
    state.messages.push({
      id: uid('u'),
      kind: 'user',
      content: trimmed,
      attachments,
    })
    state.sending = true
    state.thinking = true

    if (demoMode.value) {
      void runDemo(state, key, message)
      return
    }
    void streamPost(
      '/chat/stream',
      {
        message,
        thread_id: key,
        user_id: 'console_user',
        username: '控制台用户',
        attachments: attachments.map((item) => item.id),
      },
      state,
      key,
    )
  }

  // HITL 恢复：补充信息 / 审批决策
  function resume(
    interrupt: ChatMsg,
    payload: Record<string, unknown>,
    markAs: 'answered' | 'approved' | 'rejected',
  ) {
    const key = threadId.value
    const state = key ? threads.value[key] : undefined
    if (!key || !state || state.pendingInterrupt !== interrupt) return
    interrupt.resolved = markAs
    state.pendingInterrupt = null
    state.sending = true
    state.thinking = true
    if (demoMode.value) {
      void runDemoResume(state, key, markAs)
      return
    }
    void streamPost(
      `/chat/${key}/resume`,
      { resume: payload, user_id: 'console_user', username: '控制台用户' },
      state,
      key,
    )
  }

  // 只中止当前会话，不影响后台其他会话
  function stop() {
    const state = current.value
    if (!state) return
    state.controller?.abort()
    state.controller = null
    state.sending = false
    state.thinking = false
  }

  function newChat() {
    threadId.value = null
    lastError.value = ''
  }

  // 切换会话：已有实时缓冲时直接恢复；否则用历史覆盖
  function loadHistory(
    msgs: ChatMsg[],
    tid: string,
    sessionUsage?: MessageTokens | null,
  ) {
    const state = ensureThread(tid)
    if (!state.sending) {
      state.messages = msgs
      state.pendingInterrupt =
        msgs.find((m) => m.kind === 'interrupt' && !m.resolved) || null
      state.sessionTokens =
        sessionUsage && sessionUsage.total > 0 ? sessionUsage : null
    }
    threadId.value = tid
    lastError.value = ''
  }

  // ---------- 演示模式：脚本化 SSE 事件，覆盖全部卡片类型 ----------
  const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))
  let demoSessionTokens: MessageTokens = { input: 0, output: 0, total: 0, rounds: 0 }

  function demoDone(state: ThreadChat, key: string, interrupted: boolean, usage: MessageTokens) {
    // 与后端口径一致：input 取最近一轮，output 累加历轮生成。
    demoSessionTokens = {
      input: usage.input,
      output: demoSessionTokens.output + usage.output,
      total: usage.input + demoSessionTokens.output + usage.output,
      rounds: (demoSessionTokens.rounds || 0) + 1,
    }
    handleEvent(
      {
        type: 'done',
        thread_id: key,
        user_id: 'console_user',
        username: '控制台用户',
        content: '',
        interrupted,
        usage,
        session_usage: { ...demoSessionTokens },
      },
      state,
      key,
    )
  }

  async function typeOut(state: ThreadChat, text: string) {
    state.messages.push({ id: uid('a'), kind: 'assistant', content: '', streaming: true })
    const msg = state.messages[state.messages.length - 1]!
    for (const ch of text) {
      msg.content = (msg.content ?? '') + ch
      await sleep(28)
    }
    msg.streaming = false
  }

  async function runDemo(state: ThreadChat, key: string, userText: string) {
    await sleep(400)
    state.thinking = false
    await typeOut(state, `收到「${userText.slice(0, 18)}」。我先读取你的偏好文件，再检索资料。`)
    handleEvent({ type: 'tool_start', tool_call_id: 'call-1', tool_name: 'read_file', source: 'main' }, state, key)
    await sleep(300)
    handleEvent({ type: 'tool_args', tool_call_id: 'call-1', args: '{"path": "/memories/console_user/preferences.md"}', source: 'main' }, state, key)
    await sleep(600)
    handleEvent({ type: 'tool_result', tool_call_id: 'call-1', tool_name: 'read_file', status: 'done', text: 'preferred:\n  language: zh-CN\n  output_style: concise\nrecent_queries:\n  - 控制台演示', source: 'main' }, state, key)
    handleEvent({ type: 'tool_end', tool_call_id: 'call-1', status: 'done', source: 'main' }, state, key)

    handleEvent({ type: 'tool_start', tool_call_id: 'call-2', tool_name: 'web_search', source: 'main' }, state, key)
    await sleep(250)
    handleEvent({ type: 'tool_args', tool_call_id: 'call-2', args: '{"query": "Harness 多智能体架构 最佳实践"}', source: 'main' }, state, key)
    await sleep(900)
    handleEvent({ type: 'tool_result', tool_call_id: 'call-2', tool_name: 'web_search', status: 'done', text: '1. 编排层评估（LangGraph 状态图）…\n2. 上下文工程：卸载/摘要/隔离…\n3. HITL 以 checkpoint 落地…', source: 'main' }, state, key)
    handleEvent({ type: 'tool_end', tool_call_id: 'call-2', status: 'done', source: 'main' }, state, key)

    handleEvent({ type: 'tool_start', tool_call_id: 'call-3', tool_name: 'assign_skill', source: 'main' }, state, key)
    await sleep(250)
    handleEvent({ type: 'tool_args', tool_call_id: 'call-3', args: '{"skill_name": "report-writer", "subagent_name": "main"}', source: 'main' }, state, key)
    await sleep(700)
    handleEvent({ type: 'tool_result', tool_call_id: 'call-3', tool_name: 'assign_skill', status: 'done', text: '{"ok": true, "skill_name": "report-writer", "target": "main", "path": "/skills/main/report-writer"}', source: 'main' }, state, key)
    handleEvent({ type: 'tool_end', tool_call_id: 'call-3', status: 'done', source: 'main' }, state, key)

    handleEvent({ type: 'tool_start', tool_call_id: 'call-4', tool_name: 'task', source: 'main', subagent_name: 'research_agent' }, state, key)
    await sleep(250)
    handleEvent({ type: 'tool_args', tool_call_id: 'call-4', args: '{"subagent_type": "research_agent", "description": "汇总检索结果并给出结构化结论"}', source: 'main' }, state, key)
    await sleep(900)
    handleEvent({ type: 'tool_result', tool_call_id: 'call-4', tool_name: 'task', status: 'done', text: '子 Agent 报告：三条核心结论已生成，建议以 Markdown 输出。', source: 'research_agent' }, state, key)
    handleEvent({ type: 'tool_end', tool_call_id: 'call-4', status: 'done', source: 'main' }, state, key)

    await typeOut(state, '整理完成，共 3 条核心结论。输出前需要确认一件事：')
    handleEvent(
      {
        type: 'interrupt',
        thread_id: key,
        interrupt_id: 'int-demo',
        interrupt_type: 'information_request',
        information_needed: '请确认输出格式：Markdown 还是表格？',
        context: '已生成初稿，等待格式确认后交付。',
      },
      state,
      key,
    )
    demoDone(state, key, true, { input: 6056, output: 42, total: 6098 })
  }

  async function runDemoResume(
    state: ThreadChat,
    key: string,
    markAs: 'answered' | 'approved' | 'rejected',
  ) {
    await sleep(500)
    state.thinking = false
    if (markAs === 'rejected') {
      await typeOut(state, '好的，已取消本次交付。需要调整时随时告诉我。')
    } else {
      await typeOut(state, '已按 Markdown 输出最终报告：')
      state.messages.push({
        id: uid('a'),
        kind: 'assistant',
        content:
          '# Harness 多智能体实践结论\n\n1. **先可观测再优化**：没有 trace 与评估集，任何改动都是盲改。\n2. **上下文分层**：卸载 → 摘要 → 主动压缩，三层共用同一状态。\n3. **HITL 落 checkpoint**：等待人 = 可恢复，而不是阻塞。',
      })
    }
    demoDone(state, key, false, { input: 6122, output: 58, total: 6180 })
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
    turnVersion,
    runningThreadIds,
    waitingThreadIds,
    activateThread,
    releaseThread,
    isThreadSending,
    send,
    resume,
    stop,
    newChat,
    loadHistory,
  }
})
