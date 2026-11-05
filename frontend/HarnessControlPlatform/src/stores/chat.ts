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
  // 上下文窗口由后端返回，前端不再维护常量
  contextWindow?: number
  contextUsed?: number
}

export interface ChatMsg {
  id: string
  kind: 'user' | 'assistant' | 'reasoning' | 'subagent' | 'tool' | 'interrupt' | 'system'
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
  // 子 Agent 的推理/工具挂在委派节点下，按此 ID 组成轨迹树
  parentToolCallId?: string
  // 到达时间，用于轨迹树按时间戳排序与展示
  at?: number
  interruptId?: string
  interruptType?: string
  payload?: Record<string, unknown>
  resolved?: 'answered' | 'approved' | 'rejected'
  // 用户在 HITL 卡片里提交内容的简短摘要，卡片收起后用于轨迹树展示
  resolution?: string
}

interface SseEvent {
  type: string
  [key: string]: unknown
}

export interface CompactionState {
  active: boolean
  compactionId: string
  phase: 'compacting' | 'evaluating' | 'exiting'
  startedAt: number
  usedTokens: number
  limit: number
  window: number
  pct: number
  reason: string
  degraded?: boolean
  error?: string
  // 已收到压缩结束事件，等待最短展示时长后再退场
  exiting?: boolean
  // 进入“评估”阶段的时间，用于保证该阶段有最短展示时长
  evaluatingAt?: number
}

interface ThreadChat {
  messages: ChatMsg[]
  sending: boolean
  thinking: boolean
  pendingInterrupt: ChatMsg | null
  controller: AbortController | null
  sessionTokens: MessageTokens | null
  // 用户消息 ID -> 本轮耗时（后端 done.duration_ms）
  turnDurations: Record<string, number>
  // 当前进行中的用户轮次，用于回填耗时
  activeTurnKey: string | null
  // 当前压缩状态（临时提示，不落历史）
  compaction: CompactionState | null
  compactionCount: number
  // 压缩期间到达的正文 token，结束后按序回放
  compactionBuffer: string
  // 压缩动画的定时器（阶段最短时长与退场）
  compactionTimers: number[]
  // 压缩进度看门狗：长时间没有心跳则提示连接可能已断开
  compactionWatchdog: number | null
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

// 压缩动画时长：保证“压缩 → 评估 → 退场”三步都看得见。
const COMPACTION_MIN_STAGE_MS = 700
const COMPACTION_EXIT_MS = 340

/** 压缩阶段最短展示时间，避免摘要太快导致动画一闪而过。 */
function stageWait(startedAt: number, minMs = COMPACTION_MIN_STAGE_MS): number {
  return Math.max(minMs - (Date.now() - startedAt), 0)
}

function removeCompactionNotes(state: ThreadChat) {
  state.messages = state.messages.filter((message) => !message.id.startsWith('cp-note-'))
}

function clearCompactionTimers(state: ThreadChat) {
  for (const timer of state.compactionTimers) window.clearTimeout(timer)
  state.compactionTimers = []
}

function scheduleCompaction(state: ThreadChat, delay: number, run: () => void) {
  const timer = window.setTimeout(() => {
    state.compactionTimers = state.compactionTimers.filter((item) => item !== timer)
    run()
  }, delay)
  state.compactionTimers = [...state.compactionTimers, timer]
}

const COMPACTION_STALE_MS = 20000

/** 压缩期间每收到一次进度就重置看门狗；超时说明连接可能已断。 */
function armCompactionWatchdog(state: ThreadChat) {
  if (state.compactionWatchdog) window.clearTimeout(state.compactionWatchdog)
  state.compactionWatchdog = window.setTimeout(() => {
    if (state.compaction?.active && !state.compaction.exiting) {
      state.compaction = {
        ...state.compaction,
        error: '压缩进度长时间无响应，连接可能已断开',
      }
      const note = state.messages.find(
        (message) => message.id === `cp-note-${state.compaction?.compactionId}`,
      )
      if (note) note.content = '压缩进度长时间无响应，请刷新后重试'
    }
  }, COMPACTION_STALE_MS)
}

function clearCompactionWatchdog(state: ThreadChat) {
  if (state.compactionWatchdog) window.clearTimeout(state.compactionWatchdog)
  state.compactionWatchdog = null
}

/** 压缩结束后更新会话上下文占用。 */
function applyCompactionUsage(state: ThreadChat, evt: SseEvent) {
  const after = Number(evt.after_tokens || 0)
  const rawWindow = Number(evt.window || evt.limit || 0)
  if (!state.sessionTokens || after <= 0) return
  state.sessionTokens.contextUsed = after
  if (rawWindow > 0) state.sessionTokens.contextWindow = rawWindow
}

/** 压缩结束后把缓冲的正文回放到当前气泡。 */
function flushCompactionBuffer(state: ThreadChat) {
  const text = state.compactionBuffer
  state.compactionBuffer = ''
  if (!text) return
  const last = [...state.messages].reverse().find((message) => message.kind === 'assistant')
  if (last && last.streaming) {
    last.content = `${last.content || ''}${text}`
    return
  }
  state.messages.push({
    id: uid('a'),
    kind: 'assistant',
    content: text,
    streaming: true,
    at: Date.now(),
  })
}

let seq = 0
const uid = (prefix: string) => `${prefix}-${Date.now()}-${seq++}`

function categoryOf(name: string): ToolCategory {
  if (SKILL_TOOLS.has(name)) return 'skill'
  if (DELEGATION_TOOLS.has(name)) return 'delegation'
  return 'tool'
}

export { categoryOf }

// HITL 提交内容压成一行，卡片收起后仍能在轨迹树里看到用户回答了什么。
function summarizeResume(payload: Record<string, unknown>): string {
  const text = typeof payload.text === 'string' ? payload.text.trim() : ''
  if (text) return text.slice(0, 120)

  const decision = typeof payload.decision === 'string' ? payload.decision : ''
  if (decision) {
    const reason = typeof payload.message === 'string' ? payload.message.trim() : ''
    if (decision === 'reject') return `拒绝${reason ? `：${reason}` : ''}`.slice(0, 120)
    if (decision === 'approve_thread') return '批准（仅本会话允许）'
    return '批准（仅本次）'
  }

  const decisions = payload.decisions as
    | { type?: string; message?: string }[]
    | undefined
  if (Array.isArray(decisions)) {
    return decisions
      .map((item) =>
        item.type === 'reject'
          ? `拒绝${item.message ? `：${item.message}` : ''}`
          : '批准',
      )
      .join('、')
      .slice(0, 120)
  }

  const parts: string[] = []
  const values = payload.values as Record<string, unknown> | undefined
  if (values && typeof values === 'object') {
    for (const [key, value] of Object.entries(values)) {
      if (value === '' || value === null || value === undefined || value === false) continue
      parts.push(`${key}=${Array.isArray(value) ? value.join('/') : String(value)}`)
    }
  }
  const selected = payload.selected as string[] | undefined
  if (Array.isArray(selected) && selected.length) parts.push(`选项=${selected.join('/')}`)
  const freeText = typeof payload.free_text === 'string' ? payload.free_text.trim() : ''
  if (freeText) parts.push(`补充=${freeText}`)
  if (payload.cancelled) parts.push('已取消')
  return parts.join('；').slice(0, 160)
}

export const useChatStore = defineStore('chat', () => {
  // threadId -> 独立会话状态。SSE 事件始终写入发起请求的那个 thread。
  const threads = ref<Record<string, ThreadChat>>({})
  const threadId = ref<string | null>(null)
  const demoMode = ref(true)
  const lastError = ref('')
  const roundLimit = ref(50)
  // 每完成一轮自增，供侧边栏刷新标题/条数
  const turnVersion = ref(0)
  // 最近完成一轮的 thread_id；后台会话完成时也能定位到正确文件域
  const lastTurnThreadId = ref<string | null>(null)

  let pendingDemoIndex = 0

  function createThread(): ThreadChat {
    return {
      messages: [],
      sending: false,
      thinking: false,
      pendingInterrupt: null,
      controller: null,
      sessionTokens: null,
      turnDurations: {},
      activeTurnKey: null,
      compaction: null,
      compactionCount: 0,
      compactionBuffer: '',
      compactionTimers: [],
      compactionWatchdog: null,
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
  const turnDurations = computed(() => current.value?.turnDurations ?? {})
  const compaction = computed(() => current.value?.compaction ?? null)
  const compacting = computed(
    () => Boolean(current.value?.compaction?.active && current.value.compaction.phase !== 'exiting'),
  )
  const compactionCount = computed(() => current.value?.compactionCount ?? 0)
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

    // 同一段流式内容在相邻 chunk 中增量拼接；切换来源或父节点时新建一条。
    function appendDelta(kind: 'assistant' | 'reasoning' | 'subagent') {
      state.thinking = false
      const source = String(evt.source || 'main')
      const parent = evt.parent_tool_call_id
        ? String(evt.parent_tool_call_id)
        : undefined
      const text = String(evt.content || '')
      // 压缩期间不往气泡里追加内容，先缓冲；退场动画开始时统一回放。
      if (state.compaction?.active && !state.compaction.exiting && kind === 'assistant') {
        state.compactionBuffer += text
        return
      }
      const last = state.messages[state.messages.length - 1]
      if (
        last &&
        last.kind === kind &&
        last.streaming &&
        last.source === source &&
        last.parentToolCallId === parent
      ) {
        last.content = (last.content || '') + text
        return
      }
      state.messages.push({
        id: uid(kind === 'assistant' ? 'a' : kind === 'reasoning' ? 'r' : 's'),
        kind,
        content: text,
        streaming: true,
        source,
        parentToolCallId: parent,
        at: Date.now(),
      })
    }

    switch (evt.type) {
      case 'token':
        appendDelta('assistant')
        break
      case 'reasoning':
        appendDelta('reasoning')
        break
      case 'subagent_token':
        appendDelta('subagent')
        break
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
          parentToolCallId: evt.parent_tool_call_id
            ? String(evt.parent_tool_call_id)
            : undefined,
          at: Date.now(),
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
          at: Date.now(),
        }
        state.messages.push(msg)
        state.pendingInterrupt = msg
        break
      }
      case 'compaction_start': {
        const compactionId = String(evt.compaction_id || uid('cp'))
        const used = Number(evt.used_tokens || 0)
        const limit = Number(evt.limit || 0)
        const windowSize = Number(evt.window || limit || 0)
        const pct = Number(evt.pct || 0)
        clearCompactionTimers(state)
        state.thinking = true
        state.compaction = {
          active: true,
          compactionId,
          phase: 'compacting',
          startedAt: Date.now(),
          usedTokens: used,
          limit,
          window: windowSize,
          pct,
          reason: String(evt.trigger_type || 'soft'),
        }
        // 聊天区只放一条临时提示，任务结束后由 compaction_end 移除，不写历史。
        state.messages.push({
          id: `cp-note-${compactionId}`,
          kind: 'system',
          content: `正在压缩与评估上下文 · 已用 ${used.toLocaleString('zh-CN')} / ${limit.toLocaleString('zh-CN')}（${Math.round(pct * 100)}%）`,
          at: Date.now(),
        })
        armCompactionWatchdog(state)
        break
      }
      case 'compaction_progress': {
        if (!state.compaction || state.compaction.exiting) break
        armCompactionWatchdog(state)
        const next = String(evt.phase || '')
        if (next !== 'verify' || state.compaction.phase === 'evaluating') break
        // 进入“评估”阶段前保证“压缩”阶段至少展示一段时间。
        scheduleCompaction(state, stageWait(state.compaction.startedAt), () => {
          if (!state.compaction || state.compaction.exiting) return
          state.compaction = {
            ...state.compaction,
            phase: 'evaluating',
            evaluatingAt: Date.now(),
          }
        })
        break
      }
      case 'compaction_end': {
        clearCompactionWatchdog(state)
        const current = state.compaction
        if (!current) {
          // 页面未处于压缩态：只更新占用统计。
          applyCompactionUsage(state, evt)
          break
        }
        const degraded = Boolean(evt.degraded)
        const note = state.messages.find(
          (message) => message.id === `cp-note-${current.compactionId}`,
        )
        if (degraded && note) note.content = '上下文压缩失败，已使用兜底策略继续'

        const beginExit = () => {
          if (!state.compaction || state.compaction.compactionId !== current.compactionId) {
            return
          }
          // 退场动画开始时立即恢复被中断的正文输出。
          state.compaction.exiting = true
          state.compaction.phase = 'exiting'
          if (note && !degraded) note.content = '上下文已压缩，继续执行'
          flushCompactionBuffer(state)
          applyCompactionUsage(state, evt)
          if (String(evt.status || 'done') !== 'skipped') state.compactionCount += 1
          scheduleCompaction(state, COMPACTION_EXIT_MS, () => {
            removeCompactionNotes(state)
            if (state.compaction?.compactionId === current.compactionId) {
              state.compaction = null
            }
          })
        }

        // 压缩阶段 + 评估阶段都必须至少各展示一次，再进入退场。
        const evaluatingAt = current.evaluatingAt ?? Date.now()
        if (current.phase === 'compacting') {
          // 用新对象替换，避免与退场定时器里的旧引用混淆。
          state.compaction = { ...current, phase: 'evaluating', evaluatingAt }
        }
        scheduleCompaction(state, stageWait(evaluatingAt), beginExit)
        break
      }
      case 'compaction_error': {
        if (state.compaction) {
          state.compaction.error = String(evt.message || '压缩失败')
          const note = state.messages.find(
            (message) => message.id === `cp-note-${state.compaction?.compactionId}`,
          )
          if (note) note.content = '上下文压缩失败，已使用兜底策略继续'
        }
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
        const session = evt.session_usage as
          | (Partial<MessageTokens> & {
              context_window?: number
              context_used?: number
            })
          | undefined
        if (session && typeof session.total === 'number') {
          state.sessionTokens = {
            input: session.input || 0,
            output: session.output || 0,
            total: session.total,
            rounds: session.rounds || 0,
            contextWindow: session.context_window || 0,
            contextUsed: session.context_used ?? session.input ?? 0,
          }
        }
        // 后端以服务端处理时间计算本轮耗时，折叠后只展示这一个数字。
        if (typeof evt.duration_ms === 'number' && state.activeTurnKey) {
          state.turnDurations[state.activeTurnKey] = evt.duration_ms
        }
        for (const message of state.messages) {
          if (message.streaming) message.streaming = false
        }
        // 压缩提示属于临时的运行状态，结束后不留在聊天记录里。
        if (state.compaction?.exiting) {
          // 退场动画进行中：交给定时器收尾，避免动画被截断。
          scheduleCompaction(state, COMPACTION_EXIT_MS, () => {
            removeCompactionNotes(state)
            state.compaction = null
          })
        } else {
          clearCompactionTimers(state)
          removeCompactionNotes(state)
          state.compaction = null
        }
        state.compactionBuffer = ''
        state.sending = false
        state.thinking = false
        state.controller = null
        state.activeTurnKey = null
        if (!evt.interrupted) state.pendingInterrupt = null
        lastTurnThreadId.value = realId || key
        turnVersion.value++
        break
      }
      case 'error': {
        lastError.value = String(evt.message || '未知错误')
        state.messages.push({
          id: uid('s'),
          kind: 'system',
          content: `⚠ ${lastError.value}`,
          at: Date.now(),
        })
        for (const message of state.messages) {
          if (message.streaming) message.streaming = false
        }
        removeCompactionNotes(state)
        clearCompactionTimers(state)
        clearCompactionWatchdog(state)
        state.compaction = null
        state.compactionBuffer = ''
        state.sending = false
        state.thinking = false
        state.controller = null
        state.activeTurnKey = null
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
    const userMsg: ChatMsg = {
      id: uid('u'),
      kind: 'user',
      content: trimmed,
      attachments,
      at: Date.now(),
    }
    state.messages.push(userMsg)
    state.activeTurnKey = userMsg.id
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
    interrupt.resolution = summarizeResume(payload)
    state.pendingInterrupt = null
    state.sending = true
    state.thinking = true
    // HITL 恢复仍属于同一用户轮次，耗时回填到该轮。
    const lastUser = [...state.messages]
      .reverse()
      .find((message) => message.kind === 'user')
    state.activeTurnKey = lastUser?.id || state.activeTurnKey
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
    clearCompactionTimers(state)
    clearCompactionWatchdog(state)
    removeCompactionNotes(state)
    state.compaction = null
    state.compactionBuffer = ''
    state.sending = false
    state.thinking = false
    state.activeTurnKey = null
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
      clearCompactionTimers(state)
      clearCompactionWatchdog(state)
      state.compaction = null
      state.compactionBuffer = ''
    }
    threadId.value = tid
    lastError.value = ''
  }

  // 历史会话的每轮耗时要靠轨迹接口回填（实时轮次已由 done 事件带回）。
  function setTurnDurations(tid: string, durations: Record<string, number>) {
    const state = threads.value[tid]
    if (!state) return
    state.turnDurations = { ...state.turnDurations, ...durations }
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
        session_usage: {
          ...demoSessionTokens,
          context_window: 128000,
          context_used: usage.input,
        },
        duration_ms: 2400,
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
    handleEvent(
      { type: 'reasoning', content: '先确认偏好文件与可用工具，再给出结论。', source: 'main' },
      state,
      key,
    )
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
    handleEvent(
      {
        type: 'reasoning',
        content: '检索结果里有三条可以合并的结论，我先按可信度排序。',
        source: 'research_agent',
        parent_tool_call_id: 'call-4',
      },
      state,
      key,
    )
    await sleep(500)
    handleEvent(
      {
        type: 'subagent_token',
        content: '已核对三条来源，正在整理最终摘要。',
        source: 'research_agent',
        parent_tool_call_id: 'call-4',
      },
      state,
      key,
    )
    await sleep(400)
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
    turnDurations,
    compaction,
    compacting,
    compactionCount,
    isEmpty,
    turnVersion,
    lastTurnThreadId,
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
    setTurnDurations,
  }
})
