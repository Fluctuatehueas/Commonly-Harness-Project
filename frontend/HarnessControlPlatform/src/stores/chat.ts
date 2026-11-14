// 对话测试：按会话隔离的 SSE 状态机 + 演示模式
// 关键点：切换会话不 abort 正在运行的流，每个 thread 有独立消息缓冲区。
import { defineStore, acceptHMRUpdate } from 'pinia'
import { computed, ref } from 'vue'
import { API_BASE } from '@/api/client'
import { submitFeedback } from '@/api/feedback'
import { USER_ID } from '@/stores/sessions'
import { currentUser } from '@/api/identity'
import type { ContextFile } from '@/api/context'
import type { ExecutionStepView, StepTrace } from '@/api/taskflow'

export type ToolCategory = 'tool' | 'skill' | 'delegation'

export interface MessageTokens {
  input: number
  output: number
  total: number
  // 真实计费口径：历轮 input + output 之和；撤回消息不回退
  spent?: number
  rounds?: number
  // 上下文窗口由后端返回，前端不再维护常量
  contextWindow?: number
  contextUsed?: number
}

// 任务规划卡片里的单个条目：由 scheduler_progress.planned 的 plan_items 快照渲染。
export interface SchedulerPlanItem {
  id: string
  title: string
  phaseId: string
  phaseTitle?: string
  status: 'pending' | 'running' | 'done' | 'failed' | 'skipped' | 'needs_hitl'
  detail?: string
}

export interface ChatMsg {
  id: string
  kind:
    | 'user'
    | 'assistant'
    | 'reasoning'
    | 'subagent'
    | 'tool'
    | 'interrupt'
    | 'system'
    | 'taskflow'
    | 'scheduler'
    // 仅前端渲染的流程标记：经验命中 / 任务流命中，不写入轨迹存储
    | 'marker'
    // 本轮终止原因，仅本地展示，不写入历史
    | 'terminated'
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
  // ---------- 任务流执行卡片 ----------
  flowId?: string
  flowName?: string
  flowStatus?: 'running' | 'done' | 'failed' | 'aborted'
  flowSteps?: ExecutionStepView[]
  flowTraces?: StepTrace[]
  flowCurrentStep?: string
  // ---------- 任务规划卡片 ----------
  schedRunId?: string
  schedGoal?: string
  schedTier?: string
  schedMode?: string
  schedStatus?: 'running' | 'done' | 'failed' | 'paused'
  schedPlanVersion?: number
  schedItems?: SchedulerPlanItem[]
  schedSummary?: string
  schedReplans?: number
  schedNote?: string
  // 用户对这条最终回答的最新反馈；刷新后由历史接口回填
  feedback?: 'like' | 'dislike' | null
  // ---------- 仅渲染的流程标记 ----------
  markerType?: 'experience' | 'taskflow'
  markerItems?: { id: string; name: string; rule?: string }[]
  // ---------- 终止原因（仅本地，不落历史） ----------
  termReason?: TerminateReason
  termDetail?: string
}

export type TerminateReason = 'user' | 'network' | 'server' | 'limit'

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
  // 重试时暂存被替换的旧回答，失败且未产生新内容时用于回滚
  retryBackup: {
    messageId: string
    content: string
    index: number
    tokens?: MessageTokens
  } | null
  // 当前是否处于覆盖式重试中
  retryPending: boolean
  // 重试后是否已经收到新正文；没有则失败时恢复旧回答
  retryReceived: boolean
  // 用户消息 ID -> 连续重试次数（仅本地，用于防滥用提示）
  retryCount: Record<string, number>
  // 本轮是否已收到 done / error 终止事件，用于识别流被意外关闭
  terminalSeen: boolean
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

/** 重试失败且尚未产生新回答时，把旧回答放回原位。 */
function restoreRetryBackup(state: ThreadChat) {
  const backup = state.retryBackup
  if (!backup) return
  if (!state.retryReceived) {
    const index = Math.min(Math.max(backup.index, 0), state.messages.length)
    state.messages.splice(index, 0, {
      id: backup.messageId,
      kind: 'assistant',
      content: backup.content,
      tokens: backup.tokens,
      source: 'main',
    })
  }
  state.retryBackup = null
  state.retryPending = false
  state.retryReceived = false
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

  const decisions = payload.decisions as { type?: string; message?: string }[] | undefined
  if (Array.isArray(decisions)) {
    return decisions
      .map((item) =>
        item.type === 'reject' ? `拒绝${item.message ? `：${item.message}` : ''}` : '批准',
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
  // threadId -> 最近一次未完成任务流的卡片快照。
  // 历史重新拉取（撤回）会丢掉本地卡片，这里用于把未完成的进度卡挂回去。
  const flowSnapshots = ref<Record<string, ChatMsg>>({})
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
      retryBackup: null,
      retryPending: false,
      retryReceived: false,
      retryCount: {},
      terminalSeen: false,
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

  const current = computed(() => (threadId.value ? threads.value[threadId.value] : undefined))
  const messages = computed(() => current.value?.messages ?? EMPTY_MESSAGES)
  const sending = computed(() => current.value?.sending ?? false)
  const thinking = computed(() => current.value?.thinking ?? false)
  const pendingInterrupt = computed(() => current.value?.pendingInterrupt ?? null)
  const sessionTokens = computed(() => current.value?.sessionTokens ?? null)
  const turnDurations = computed(() => current.value?.turnDurations ?? {})
  const compaction = computed(() => current.value?.compaction ?? null)
  const compacting = computed(() =>
    Boolean(current.value?.compaction?.active && current.value.compaction.phase !== 'exiting'),
  )
  const compactionCount = computed(() => current.value?.compactionCount ?? 0)
  const isEmpty = computed(() => messages.value.length === 0)
  // 用户选择的 MyAgent；切换会话时保持，默认 Agent 始终可选。
  const selectedAgentId = ref('default')
  // 用户选择的生效组；空表示按独立 Agent 执行。
  const selectedGroupId = ref('')

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
    return state.messages.find((m) => m.kind === 'tool' && m.toolCallId === toolCallId)
  }

  /**
   * 把仅渲染的流程标记插到本轮过程的最前面。
   *
   * 经验注入发生在首次模型调用前，应排在推理 / 工具之前；即使 SSE
   * 兜底事件到得较晚，也不能追加到最终回答之后，否则会破坏轮次分组。
   */
  function insertTurnMarker(state: ThreadChat, marker: ChatMsg) {
    let lastUser = -1
    for (let i = state.messages.length - 1; i >= 0; i -= 1) {
      if (state.messages[i]?.kind === 'user') {
        lastUser = i
        break
      }
    }
    // 标记发生在本轮首次模型调用前，时间戳对齐到用户消息之后，
    // 保证轨迹树按时间排序时仍排在本轮过程最前面。
    const userMessage = lastUser >= 0 ? state.messages[lastUser] : undefined
    marker.at = (userMessage?.at || Date.now()) + 1
    let index = lastUser + 1
    while (index < state.messages.length && state.messages[index]?.kind === 'marker') {
      index += 1
    }
    state.messages.splice(index, 0, marker)
  }

  // 任务流执行卡片：同一条流在一次回复中只保留一张卡片。
  function findTaskFlowMsg(state: ThreadChat): ChatMsg | undefined {
    for (let i = state.messages.length - 1; i >= 0; i--) {
      const msg = state.messages[i]
      if (msg?.kind === 'taskflow') return msg
    }
    return undefined
  }

  // 任务规划卡片：同一次运行只保留一张卡片；中断后恢复执行时复用它。
  function findSchedulerMsg(state: ThreadChat, runId = ''): ChatMsg | undefined {
    for (let i = state.messages.length - 1; i >= 0; i--) {
      const msg = state.messages[i]
      if (msg?.kind !== 'scheduler') continue
      if (runId && msg.schedRunId && msg.schedRunId !== runId) continue
      return msg
    }
    return undefined
  }

  function parseSchedulerItems(raw: unknown): SchedulerPlanItem[] {
    if (!Array.isArray(raw)) return []
    const allowed = new Set(['pending', 'running', 'done', 'failed', 'skipped', 'needs_hitl'])
    return raw.map((item) => {
      const record = item as {
        id?: string
        title?: string
        phase_id?: string
        phase_title?: string
        status?: string
        summary?: string
        error?: string
      }
      const status = String(record.status || 'pending')
      return {
        id: String(record.id || ''),
        title: String(record.title || record.id || '条目'),
        phaseId: String(record.phase_id || ''),
        phaseTitle: record.phase_title ? String(record.phase_title) : undefined,
        status: (allowed.has(status) ? status : 'pending') as SchedulerPlanItem['status'],
        detail: record.summary || record.error || undefined,
      }
    })
  }

  /** 记录/清理任务流快照：整条流走完后删除，保证卡片不再出现。 */
  function rememberFlowSnapshot(key: string, card: ChatMsg | undefined) {
    if (!card || card.kind !== 'taskflow') return
    const steps = (card.flowSteps || []).map((step) => ({ ...step }))
    const finished =
      steps.length > 0 &&
      steps.every((step) => step.status === 'done' || step.status === 'degraded')
    if (finished) {
      delete flowSnapshots.value[key]
      return
    }
    flowSnapshots.value[key] = {
      ...card,
      id: uid('tf'),
      flowStatus: 'running',
      flowCurrentStep: '',
      flowSteps: steps,
      flowTraces: (card.flowTraces || []).slice(),
      at: Date.now(),
    }
  }

  /** 撤回导致历史被重新拉取后，把未完成任务流的进度卡挂回消息流。 */
  function restoreFlowCard(tid: string) {
    const snapshot = flowSnapshots.value[tid]
    const state = threads.value[tid]
    if (!snapshot || !state) return
    if (
      state.messages.some(
        (message) => message.kind === 'taskflow' && message.flowStatus === 'running',
      )
    ) {
      return
    }
    state.messages.push({
      ...snapshot,
      id: uid('tf'),
      flowSteps: (snapshot.flowSteps || []).map((step) => ({ ...step })),
    })
  }

  function migrateThread(from: string, to: string) {
    if (!from || !to || from === to) return
    const state = threads.value[from]
    if (!state) return
    threads.value[to] = state
    delete threads.value[from]
    if (threadId.value === from) threadId.value = to
  }

  /**
   * 在当前轮末尾记录终止原因，供 AI 气泡下方状态条渲染。
   *
   * 同一轮只保留一条；重复调用只更新原因与详情，避免多次收尾产生多条提示。
   */
  function markTerminated(
    state: ThreadChat,
    reason: TerminateReason,
    detail = '',
  ) {
    // 所有流式节点都停下，否则中止后气泡会一直挂着光标。
    for (const message of state.messages) {
      if (message.streaming) message.streaming = false
    }
    let lastUser = -1
    for (let index = state.messages.length - 1; index >= 0; index -= 1) {
      if (state.messages[index]?.kind === 'user') {
        lastUser = index
        break
      }
    }
    const existing = state.messages
      .slice(lastUser + 1)
      .find((message) => message.kind === 'terminated')
    if (existing) {
      existing.termReason = reason
      existing.termDetail = detail
      existing.at = Date.now()
      return
    }
    state.messages.push({
      id: uid('t'),
      kind: 'terminated',
      termReason: reason,
      termDetail: detail,
      at: Date.now(),
    })
  }

  // 单个 SSE 事件 → 指定 thread 的消息列表变更
  function handleEvent(evt: SseEvent, state: ThreadChat, key: string) {
    lastError.value = ''

    // 同一段流式内容在相邻 chunk 中增量拼接；切换来源或父节点时新建一条。
    function appendDelta(kind: 'assistant' | 'reasoning' | 'subagent') {
      state.thinking = false
      if (kind === 'assistant' && state.retryPending) state.retryReceived = true
      const source = String(evt.source || 'main')
      const parent = evt.parent_tool_call_id ? String(evt.parent_tool_call_id) : undefined
      const text = String(evt.content || '')
      // 压缩期间不往气泡里追加内容，先缓冲；退场动画开始时统一回放。
      if (state.compaction?.active && !state.compaction.exiting && kind === 'assistant') {
        state.compactionBuffer += text
        return
      }
      // 子 Agent 的文本流会被推理 / 工具事件打断；按（来源 + 父委派）
      // 合并成一条完整输出，避免出现「松江区」「6,608 人」这种碎片节点。
      if (kind === 'subagent') {
        const merged = [...state.messages]
          .reverse()
          .find(
            (message) =>
              message.kind === 'subagent' &&
              message.source === source &&
              message.parentToolCallId === parent &&
              message.streaming,
          )
        if (merged) {
          merged.content = (merged.content || '') + text
          return
        }
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
      case 'retry_start': {
        // 后端已从旧回答之前的分叉点重新生成；本地若已主动移除则无需重复。
        const replaced = String(evt.replaced_message_id || '')
        state.thinking = true
        state.sending = true
        if (replaced && !state.retryPending) {
          const index = state.messages.findIndex(
            (message) => message.kind === 'assistant' && message.id === replaced,
          )
          if (index >= 0) {
            const previous = state.messages[index]!
            state.retryBackup = {
              messageId: previous.id,
              content: previous.content || '',
              index,
              tokens: previous.tokens,
            }
            state.messages.splice(index, 1)
          }
        }
        state.retryPending = true
        state.retryReceived = false
        break
      }
      case 'pdc_revision': {
        // 自检未通过，后端开始带反馈重做：移除上一版仍在流式的助手草稿。
        for (let index = state.messages.length - 1; index >= 0; index -= 1) {
          const message = state.messages[index]!
          if (message.kind !== 'assistant') break
          if (!message.streaming) break
          state.messages.splice(index, 1)
        }
        state.thinking = true
        break
      }
      case 'runtime_notice': {
        // 运行时自愈提示：只在轨迹内留一行说明，不结束本轮，也不当作回答。
        // 后端重试会从 checkpoint 重放，先前已流式的草稿必须丢弃，避免拼出两段回答。
        for (let index = state.messages.length - 1; index >= 0; index -= 1) {
          const message = state.messages[index]!
          if (message.kind !== 'assistant') break
          if (!message.streaming) break
          state.messages.splice(index, 1)
        }
        state.thinking = true
        state.messages.push({
          id: uid('s'),
          kind: 'system',
          content: String(evt.message || '内部异常，正在重试'),
          at: Date.now(),
        })
        break
      }
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
          parentToolCallId: evt.parent_tool_call_id ? String(evt.parent_tool_call_id) : undefined,
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
      case 'subagent_progress': {
        // 子 Agent 进度：每个 run_id 一条独立进度节点，并发实例不会互相覆盖。
        const runId = String(evt.run_id || '')
        if (!runId) break
        const childId = `subagent-progress-${runId}`
        const subagent = String(evt.subagent || 'subagent')
        let child = state.messages.find((m) => m.id === childId)
        if (!child) {
          const boundParents = new Set<string>(
            state.messages
              .filter((m) => m.id.startsWith('subagent-progress-'))
              .map((m) => m.parentToolCallId)
              .filter((value): value is string => Boolean(value)),
          )
          // 按启动顺序把实例绑定到同类型、仍在执行且尚未绑定进度的委派节点。
          const parent =
            state.messages.find(
              (m) =>
                m.kind === 'tool' &&
                m.category === 'delegation' &&
                m.subagentName === subagent &&
                m.toolCallId &&
                m.status === 'calling' &&
                !boundParents.has(m.toolCallId),
            ) ||
            state.messages.find(
              (m) =>
                m.kind === 'tool' &&
                m.category === 'delegation' &&
                m.toolCallId &&
                m.status === 'calling' &&
                !boundParents.has(m.toolCallId),
            )
          child = {
            id: childId,
            kind: 'tool',
            toolName: '进度',
            category: 'tool',
            source: subagent,
            parentToolCallId: parent?.toolCallId,
            args: '',
            result: '',
            status: 'done',
            at: Date.now(),
          }
          state.messages.push(child)
        }
        const phase = String(evt.event || '')
        const detail = String(evt.message || '')
        const label =
          phase === 'started'
            ? '开始处理'
            : phase === 'tool_start'
              ? `调用 ${String(evt.tool || '工具')}`
              : phase === 'tool_end'
                ? `${String(evt.tool || '工具')} ${evt.status === 'error' ? '失败' : '完成'}`
                : '完成'
        child.toolName = `进度 · ${subagent}`
        child.result = detail ? `${label}：${detail}` : label
        child.status = evt.status === 'error' ? 'error' : 'done'
        child.at = Date.now()
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
      case 'context_marker': {
        // 经验命中插入为一条仅前端渲染的流程节点，按到达时间参与轨迹排序；
        // 不写消息历史、不产生轨迹存储记录。
        if (String(evt.marker_type || '') === 'experience') {
          const rawItems = Array.isArray(evt.items) ? evt.items : []
          const items = rawItems
            .map((item) => {
              const record = item as { id?: string; name?: string; rule?: string }
              return {
                id: String(record.id || ''),
                name: String(record.name || record.id || '经验'),
                rule: record.rule ? String(record.rule) : undefined,
              }
            })
            .filter((item) => item.id || item.name)
          if (items.length) {
            insertTurnMarker(state, {
              id: uid('mk'),
              kind: 'marker',
              markerType: 'experience',
              markerItems: items,
              content: items.map((item) => `【根据${item.name}经验】`).join(''),
              at: Date.now(),
            })
          }
        }
        break
      }
      case 'task_flow_start': {
        state.thinking = false
        const flowName = String(evt.flow_name || '任务流')
        // 同一条流在同一会话里只留一次命中标记：卡片可能反复出现，
        // 但轨迹里重复刷屏没有信息量。
        const markerText = `【使用${flowName}任务流执行该任务】`
        const marked = state.messages.some(
          (message) =>
            message.kind === 'marker' &&
            message.markerType === 'taskflow' &&
            message.content === markerText,
        )
        if (!marked) {
          insertTurnMarker(state, {
            id: uid('mk'),
            kind: 'marker',
            markerType: 'taskflow',
            content: markerText,
            at: Date.now(),
          })
        }
        const rawSteps = Array.isArray(evt.steps) ? evt.steps : []
        state.messages.push({
          id: uid('tf'),
          kind: 'taskflow',
          flowId: String(evt.flow_id || ''),
          flowName: String(evt.flow_name || '任务流'),
          flowStatus: 'running',
          flowSteps: rawSteps.map((item) => {
            const step = item as {
              id?: string
              name?: string
              branch?: string | null
              status?: string
            }
            // 续跑/中断恢复时后端会带上已有进度；未知状态一律按等待处理。
            const raw = String(step.status || 'pending')
            const status: ExecutionStepView['status'] =
              raw === 'running' || raw === 'done' || raw === 'failed' || raw === 'degraded'
                ? raw
                : 'pending'
            return {
              id: String(step.id || ''),
              name: String(step.name || step.id || '步骤'),
              branch: step.branch ?? null,
              status,
              output: '',
            }
          }),
          flowTraces: [],
          flowCurrentStep: '',
          at: Date.now(),
        })
        break
      }
      case 'task_flow_progress': {
        state.thinking = false
        const card = findTaskFlowMsg(state)
        if (!card) break
        const status = String(evt.status || '')
        const stepId = String(evt.step_id || '')
        const attempt = Number(evt.attempts || 1)
        const step = card.flowSteps?.find((item) => item.id === stepId)
        const delta = String(evt.delta || '')
        if (step) {
          if (status === 'step_started') {
            // 重试会从头重放增量，先清空上一轮已渲染的产出。
            step.status = 'running'
            step.output = ''
            card.flowCurrentStep = step.name
          } else if (status === 'step_retry') {
            step.status = 'running'
            step.output = ''
          } else if (status === 'step_token') {
            step.status = 'running'
            step.output = `${step.output || ''}${delta}`
          } else if (status === 'step_done') {
            step.status = 'done'
          } else if (status === 'step_degraded') {
            step.status = 'degraded'
          } else if (status === 'step_failed') {
            step.status = 'failed'
          }
          if (status === 'step_started' && attempt > 1) card.flowCurrentStep = step.name
        }
        card.flowStatus = 'running'
        break
      }
      case 'task_flow_end': {
        const card = findTaskFlowMsg(state)
        if (!card) break
        const status = String(evt.status || 'done')
        card.flowStatus = status === 'failed' || status === 'aborted' ? status : 'done'
        card.flowTraces = Array.isArray(evt.step_traces) ? (evt.step_traces as StepTrace[]) : []
        if (card.flowStatus !== 'done') {
          for (const step of card.flowSteps || []) {
            if (step.status === 'running') step.status = 'failed'
          }
        }
        card.flowCurrentStep = ''
        rememberFlowSnapshot(key, card)
        break
      }
      case 'scheduler_progress': {
        // 任务规划卡片：planned 创建，item_* 更新条目状态，run_finished 归档。
        // 恢复执行（run_started / plan_revised）会把已归档卡片重新置为 running。
        const phase = String(evt.event || '')
        const runId = String(evt.run_id || '')
        if (!runId) break
        state.thinking = false
        let card = findSchedulerMsg(state, runId)
        const planItems = parseSchedulerItems(evt.plan_items)

        if (!card) {
          if (!['planned', 'run_started', 'plan_revised'].includes(phase)) break
          card = {
            id: uid('sc'),
            kind: 'scheduler',
            schedRunId: runId,
            schedGoal: String(evt.goal || ''),
            schedTier: String(evt.tier || ''),
            schedMode: String(evt.mode || ''),
            schedPlanVersion: Number(evt.plan_version || 1),
            schedItems: planItems,
            schedStatus: 'running',
            at: Date.now(),
          }
          state.messages.push(card)
          break
        }

        card.schedRunId = card.schedRunId || runId
        if (evt.goal) card.schedGoal = String(evt.goal)
        if (evt.tier) card.schedTier = String(evt.tier)
        if (evt.mode) card.schedMode = String(evt.mode)
        if (planItems.length) card.schedItems = planItems

        if (phase === 'planned' || phase === 'run_started' || phase === 'plan_revised') {
          card.schedStatus = 'running'
          card.at = Date.now()
          if (evt.plan_version) card.schedPlanVersion = Number(evt.plan_version)
          if (evt.reason) card.schedNote = String(evt.reason)
        } else if (phase === 'item_started') {
          const item = card.schedItems?.find((entry) => entry.id === String(evt.item_id || ''))
          if (item) item.status = 'running'
          card.schedStatus = 'running'
          card.at = Date.now()
        } else if (phase === 'item_done') {
          const item = card.schedItems?.find((entry) => entry.id === String(evt.item_id || ''))
          if (item) item.status = 'done'
        } else if (phase === 'item_failed') {
          const item = card.schedItems?.find((entry) => entry.id === String(evt.item_id || ''))
          if (item) {
            item.status = 'failed'
            item.detail = String(evt.error || '')
          }
        } else if (phase === 'item_skipped') {
          const item = card.schedItems?.find((entry) => entry.id === String(evt.item_id || ''))
          if (item) {
            item.status = 'skipped'
            item.detail = String(evt.reason || '')
          }
        } else if (phase === 'item_needs_hitl') {
          const item = card.schedItems?.find((entry) => entry.id === String(evt.item_id || ''))
          if (item) {
            item.status = 'needs_hitl'
            item.detail = String(evt.reason || '')
          }
          card.schedStatus = 'paused'
          card.at = Date.now()
        } else if (phase === 'run_finished') {
          const status = String(evt.status || 'done')
          card.schedStatus =
            status === 'failed' ? 'failed' : status === 'paused' ? 'paused' : 'done'
          card.schedSummary = String(evt.summary || '')
          card.schedReplans = Number(evt.replans || 0)
          // 完成后归档：时间戳后移，落到本轮轨迹的最底部。
          card.at = Date.now()
        }
        break
      }
      case 'done': {
        state.terminalSeen = true
        if (typeof evt.round_limit === 'number') {
          // 0 表示后端不做调用次数限制，前端也不再展示上限提示。
          roundLimit.value = evt.round_limit
        }
        // 只在气泡还没有正文时用 done.content 兜底（例如只收到子 Agent 输出）。
        // 已有流式正文时绝不覆盖：正文已通过自检放行，覆盖会表现为「回答被撤回重写」。
        const doneContent = typeof evt.content === 'string' ? evt.content : ''
        // 达到模型调用上限时，框架会把一条固定英文提示作为最终回答，据此标记。
        const limitHit = /^Model call limits exceeded/i.test(doneContent)
        if (doneContent) {
          const lastAssistant = [...state.messages]
            .reverse()
            .find((message) => message.kind === 'assistant')
          // 只在本轮气泡（仍在流式或还没有正文）上兜底，不碰历史回答。
          if (lastAssistant && (lastAssistant.streaming || !lastAssistant.content)) {
            if (!lastAssistant.content) lastAssistant.content = doneContent
            lastAssistant.streaming = false
          } else {
            state.messages.push({
              id: uid('a'),
              kind: 'assistant',
              content: doneContent,
              source: 'main',
              at: Date.now(),
            })
          }
        }
        const realId = evt.thread_id ? String(evt.thread_id) : ''
        if (realId) migrateThread(key, realId)

        // 流式期间使用的气泡 ID 是临时的；done 把它归一到 checkpoint 中的
        // 稳定消息 ID，反馈刷新后才能重新关联到同一条回答。
        const stableId =
          (typeof evt.final_message_id === 'string' && evt.final_message_id) ||
          (typeof (evt.usage as { message_id?: unknown } | undefined)?.message_id === 'string'
            ? String((evt.usage as { message_id?: string }).message_id)
            : '')
        if (stableId) {
          const lastAssistant = [...state.messages]
            .reverse()
            .find((message) => message.kind === 'assistant' && message.content)
          if (lastAssistant && lastAssistant.id !== stableId) {
            lastAssistant.id = stableId
          }
        }
        // 一轮结束后停止所有过程节点的流式脉冲；子 Agent 输出已合并成整段。
        for (const message of state.messages) {
          if (message.streaming && message.kind !== 'assistant') {
            message.streaming = false
          }
        }
        state.retryBackup = null
        state.retryPending = false
        state.retryReceived = false

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
            spent: session.spent,
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
        // HITL 中断不算异常终止，不写终止原因；只有真正超限才标记。
        if (limitHit && !evt.interrupted) markTerminated(state, 'limit')
        const flowCard = findTaskFlowMsg(state)
        if (flowCard && flowCard.flowStatus === 'running') flowCard.flowStatus = 'done'
        // 本轮结束卡片即收起；未走完的流留一份快照，撤回后可重新挂出。
        rememberFlowSnapshot(key, flowCard)
        const schedCard = findSchedulerMsg(state)
        if (schedCard && schedCard.schedStatus === 'running') {
          schedCard.schedStatus = 'done'
          schedCard.at = Date.now()
        }
        if (!evt.interrupted) state.pendingInterrupt = null
        lastTurnThreadId.value = realId || key
        turnVersion.value++
        break
      }
      case 'error': {
        state.terminalSeen = true
        lastError.value = String(evt.message || '未知错误')
        // 后端返回的 run_id 是排障锚点：一并显示，用户反馈时可直接提供。
        const runRef = evt.run_id ? `（run_id: ${String(evt.run_id)}）` : ''
        // 重试在产生任何新正文前失败：恢复旧回答，避免界面变空。
        restoreRetryBackup(state)
        // 网络层异常由前端标记为 network，其余按服务端错误展示。
        markTerminated(
          state,
          evt.network === true ? 'network' : 'server',
          `${lastError.value}${runRef}`,
        )
        removeCompactionNotes(state)
        clearCompactionTimers(state)
        clearCompactionWatchdog(state)
        state.compaction = null
        state.compactionBuffer = ''
        state.sending = false
        state.thinking = false
        state.controller = null
        state.activeTurnKey = null
        const flowCard = findTaskFlowMsg(state)
        if (flowCard && flowCard.flowStatus === 'running') flowCard.flowStatus = 'failed'
        rememberFlowSnapshot(key, flowCard)
        const schedCard = findSchedulerMsg(state)
        if (schedCard && schedCard.schedStatus === 'running') {
          schedCard.schedStatus = 'failed'
          schedCard.at = Date.now()
        }
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
    state.terminalSeen = false
    try {
      const res = await fetch(`${API_BASE}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: state.controller.signal,
      })
      await consumeStream(res, state, key)
      // 流被对端关闭却始终没收到 done / error：按连接中断收尾。
      if (!state.terminalSeen) {
        handleEvent(
          { type: 'error', message: '连接中断，回答未完成', network: true },
          state,
          key,
        )
      }
    } catch (error) {
      if (isAbortError(error)) return
      // TypeError 通常来自 fetch/reader 的网络层错误，其余按服务端错误处理。
      handleEvent(
        {
          type: 'error',
          message: friendlyError(error as Error),
          network: error instanceof TypeError,
        },
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
        user_id: USER_ID,
        username: currentUser().name,
        agent_id: selectedAgentId.value,
        // 显式传空串表示「本轮不组队」：后端据此把会话绑定清空，
        // 下次切回该会话时不会误恢复上一个组。
        group_id: selectedGroupId.value,
        attachments: attachments.map((item) => item.id),
      },
      state,
      key,
    )
  }

  /** 赞同 / 不赞同：乐观更新，失败回滚。 */
  async function sendFeedback(
    message: ChatMsg,
    action: 'like' | 'dislike',
    options: { userId: string; userExcerpt?: string; tools?: string[] },
  ) {
    const key = threadId.value
    if (!key || message.kind !== 'assistant') return
    const previous = message.feedback
    const next = previous === action ? undefined : action
    message.feedback = next
    try {
      await submitFeedback({
        user_id: options.userId,
        thread_id: key,
        message_id: message.id,
        action: next ?? 'none',
        user_excerpt: options.userExcerpt || '',
        answer_excerpt: (message.content || '').slice(0, 800),
        meta: {
          tools: options.tools || [],
          answer_chars: (message.content || '').length,
        },
      })
    } catch (error) {
      message.feedback = previous
      throw error
    }
  }

  /** 覆盖式重试最后一条最终回答；本地先移除旧气泡，失败时回滚。 */
  function retryLast(message: ChatMsg): boolean {
    const key = threadId.value
    const state = key ? threads.value[key] : undefined
    if (!key || !state || state.sending || demoMode.value) return false
    if (message.kind !== 'assistant') return false
    const index = state.messages.findIndex(
      (item) => item.kind === 'assistant' && item.id === message.id,
    )
    if (index < 0) return false
    state.retryBackup = {
      messageId: message.id,
      content: message.content || '',
      index,
      tokens: message.tokens,
    }
    state.messages.splice(index, 1)
    state.retryPending = true
    state.retryReceived = false
    state.sending = true
    state.thinking = true
    const lastUser = [...state.messages].reverse().find((item) => item.kind === 'user')
    if (lastUser) {
      state.retryCount[lastUser.id] = (state.retryCount[lastUser.id] || 0) + 1
    }
    void streamPost(
      `/chat/${key}/retry`,
      {
        user_id: USER_ID,
        username: currentUser().name,
        message_id: message.id,
      },
      state,
      key,
    )
    return true
  }

  /** 连续重试次数，供界面提示用户补充具体要求。 */
  function retryCountFor(turnKey: string): number {
    return current.value?.retryCount[turnKey] || 0
  }

  /** 追加一条本地系统提示（不进入后端历史）。 */
  function addNote(text: string) {
    const key = threadId.value
    const state = key ? threads.value[key] : undefined
    if (!state) return
    state.messages.push({
      id: uid('s'),
      kind: 'system',
      content: text,
      at: Date.now(),
    })
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
    const lastUser = [...state.messages].reverse().find((message) => message.kind === 'user')
    state.activeTurnKey = lastUser?.id || state.activeTurnKey
    if (demoMode.value) {
      void runDemoResume(state, key, markAs)
      return
    }
    void streamPost(
      `/chat/${key}/resume`,
      { resume: payload, user_id: USER_ID, username: currentUser().name },
      state,
      key,
    )
  }

  // 只中止当前会话，不影响后台其他会话
  function stop() {
    const state = current.value
    if (!state) return
    // 只有确实在跑的一轮才写终止原因，避免空闲时点停止留下误提示。
    const running = state.sending
    state.controller?.abort()
    state.controller = null
    // 手动终止已经收尾，避免 abort 后流关闭检测再补一条「连接中断」。
    state.terminalSeen = true
    clearCompactionTimers(state)
    clearCompactionWatchdog(state)
    removeCompactionNotes(state)
    state.compaction = null
    state.compactionBuffer = ''
    restoreRetryBackup(state)
    if (running) markTerminated(state, 'user')
    state.sending = false
    state.thinking = false
    state.activeTurnKey = null
  }

  function newChat() {
    threadId.value = null
    lastError.value = ''
  }

  // 切换会话：已有实时缓冲时直接恢复；否则用历史覆盖
  function loadHistory(msgs: ChatMsg[], tid: string, sessionUsage?: MessageTokens | null) {
    const state = ensureThread(tid)
    if (!state.sending) {
      state.messages = msgs
      state.pendingInterrupt = msgs.find((m) => m.kind === 'interrupt' && !m.resolved) || null
      state.sessionTokens = sessionUsage && sessionUsage.total > 0 ? sessionUsage : null
      clearCompactionTimers(state)
      clearCompactionWatchdog(state)
      state.compaction = null
      state.compactionBuffer = ''
      state.retryBackup = null
      state.retryPending = false
      state.retryReceived = false
      state.retryCount = {}
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
        user_id: USER_ID,
        username: currentUser().name,
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
    handleEvent(
      { type: 'tool_start', tool_call_id: 'call-1', tool_name: 'read_file', source: 'main' },
      state,
      key,
    )
    await sleep(300)
    handleEvent(
      {
        type: 'tool_args',
        tool_call_id: 'call-1',
        args: `{"path": "/memories/${USER_ID}/preferences.md"}`,
        source: 'main',
      },
      state,
      key,
    )
    await sleep(600)
    handleEvent(
      {
        type: 'tool_result',
        tool_call_id: 'call-1',
        tool_name: 'read_file',
        status: 'done',
        text: 'preferred:\n  language: zh-CN\n  output_style: concise\nrecent_queries:\n  - 控制台演示',
        source: 'main',
      },
      state,
      key,
    )
    handleEvent(
      { type: 'tool_end', tool_call_id: 'call-1', status: 'done', source: 'main' },
      state,
      key,
    )

    handleEvent(
      { type: 'tool_start', tool_call_id: 'call-2', tool_name: 'web_search', source: 'main' },
      state,
      key,
    )
    await sleep(250)
    handleEvent(
      {
        type: 'tool_args',
        tool_call_id: 'call-2',
        args: '{"query": "Harness 多智能体架构 最佳实践"}',
        source: 'main',
      },
      state,
      key,
    )
    await sleep(900)
    handleEvent(
      {
        type: 'tool_result',
        tool_call_id: 'call-2',
        tool_name: 'web_search',
        status: 'done',
        text: '1. 编排层评估（LangGraph 状态图）…\n2. 上下文工程：卸载/摘要/隔离…\n3. HITL 以 checkpoint 落地…',
        source: 'main',
      },
      state,
      key,
    )
    handleEvent(
      { type: 'tool_end', tool_call_id: 'call-2', status: 'done', source: 'main' },
      state,
      key,
    )

    handleEvent(
      { type: 'tool_start', tool_call_id: 'call-3', tool_name: 'assign_skill', source: 'main' },
      state,
      key,
    )
    await sleep(250)
    handleEvent(
      {
        type: 'tool_args',
        tool_call_id: 'call-3',
        args: '{"skill_name": "report-writer", "subagent_name": "main"}',
        source: 'main',
      },
      state,
      key,
    )
    await sleep(700)
    handleEvent(
      {
        type: 'tool_result',
        tool_call_id: 'call-3',
        tool_name: 'assign_skill',
        status: 'done',
        text: '{"ok": true, "skill_name": "report-writer", "target": "main", "path": "/skills/main/report-writer"}',
        source: 'main',
      },
      state,
      key,
    )
    handleEvent(
      { type: 'tool_end', tool_call_id: 'call-3', status: 'done', source: 'main' },
      state,
      key,
    )

    handleEvent(
      {
        type: 'tool_start',
        tool_call_id: 'call-4',
        tool_name: 'task',
        source: 'main',
        subagent_name: 'research_agent',
      },
      state,
      key,
    )
    await sleep(250)
    handleEvent(
      {
        type: 'tool_args',
        tool_call_id: 'call-4',
        args: '{"subagent_type": "research_agent", "description": "汇总检索结果并给出结构化结论"}',
        source: 'main',
      },
      state,
      key,
    )
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
    handleEvent(
      {
        type: 'tool_result',
        tool_call_id: 'call-4',
        tool_name: 'task',
        status: 'done',
        text: '子 Agent 报告：三条核心结论已生成，建议以 Markdown 输出。',
        source: 'research_agent',
      },
      state,
      key,
    )
    handleEvent(
      { type: 'tool_end', tool_call_id: 'call-4', status: 'done', source: 'main' },
      state,
      key,
    )

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
    selectedAgentId,
    selectedGroupId,
    turnVersion,
    lastTurnThreadId,
    runningThreadIds,
    waitingThreadIds,
    activateThread,
    releaseThread,
    isThreadSending,
    restoreFlowCard,
    send,
    resume,
    stop,
    newChat,
    loadHistory,
    setTurnDurations,
    sendFeedback,
    retryLast,
    retryCountFor,
    addNote,
  }
})

// setup store 新增 action 时，让 Vite HMR 替换定义，避免旧实例缺少新方法。
if (import.meta.hot) {
  import.meta.hot.accept(acceptHMRUpdate(useChatStore, import.meta.hot))
}
