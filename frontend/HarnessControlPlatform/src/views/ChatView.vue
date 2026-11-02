<script setup lang="ts">
// 首页：对话测试（顶栏菜单 + 左侧会话栏 + SSE 流式 + 文件预览）
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import TopNav from '@/components/console/TopNav.vue'
import AppIcon from '@/components/console/AppIcon.vue'
import UiSwitch from '@/components/console/UiSwitch.vue'
import InterruptCard from '@/components/chat/InterruptCard.vue'
import SessionPanel from '@/components/chat/SessionPanel.vue'
import FilePanel from '@/components/chat/FilePanel.vue'
import MarkdownView from '@/components/chat/MarkdownView.vue'
import TrajectoryTree from '@/components/chat/TrajectoryTree.vue'
import RoundLimitToast from '@/components/chat/RoundLimitToast.vue'
import { API_BASE } from '@/api/client'
import {
  deleteContextFile,
  fetchContextFormats,
  importContextUrl,
  uploadContextFile,
  type ContextFile,
  type ContextFormats,
} from '@/api/context'
import { fetchTrajectory } from '@/api/trajectory'
import { categoryOf, useChatStore, type ChatMsg } from '@/stores/chat'
import { USER_ID, useSessionStore } from '@/stores/sessions'
import { useDisplayStore } from '@/stores/display'
import { useFileStore } from '@/stores/files'
import { useSettingsStore } from '@/stores/settings'
import { fetchSandboxStatus } from '@/api/settings'

const chat = useChatStore()
const display = useDisplayStore()
const sessions = useSessionStore()
const files = useFileStore()
const settings = useSettingsStore()

// 未配置沙箱时在对话页顶部提示本机执行的安全风险。
const sandboxNotice = ref('')
const sandboxBannerClosed = ref(false)
const integrityNotice = ref('')
const integrityBannerClosed = ref(false)

const draft = ref('')
const scroller = ref<HTMLElement | null>(null)
const inputEl = ref<HTMLTextAreaElement | null>(null)
const showFiles = ref(false)
const fileRefreshKey = ref(0)
// null=检测中 true=在线 false=离线（离线时提示后端不可达）
const backendOnline = ref<boolean | null>(null)
// 用户离开底部后不再强制滚动，改为提供「回到底部」按钮
const stickToBottom = ref(true)
const hasOverflow = ref(false)
const TEXTAREA_MAX_HEIGHT = 168

// ---------- 上下文附件 ----------
const formats = ref<ContextFormats>({
  accept: '',
  max_files: 6,
  max_file_mb: 20,
  groups: {},
})
const pendingFiles = ref<ContextFile[]>([])
const previews = ref<Record<string, string>>({})
const uploading = ref(false)
const uploadError = ref('')
const fileInput = ref<HTMLInputElement | null>(null)
// 附件来源下拉：文件 / 图片 / 网络链接
const attachMenu = ref(false)
const fileAccept = ref('')
const urlMode = ref(false)
const urlDraft = ref('')
const urlLoading = ref(false)

const SUGGESTIONS = [
  '介绍一下 Harness 架构的六层设计',
  '帮我把“你好”写入 /notes.md 文件',
  '查看当前可用的技能',
  '演示一次人工介入流程',
]

/** 后端由 Electron 主进程拉起时会有启动延迟：短轮询等待，避免误判离线。 */
async function fetchHealthWithRetry(attempts = 20): Promise<Response | null> {
  for (let index = 0; index < attempts; index += 1) {
    try {
      const res = await fetch(`${API_BASE}/health`, { signal: AbortSignal.timeout(2500) })
      if (res.ok) return res
    } catch {
      // 后端仍在启动，继续重试
    }
    await new Promise((resolveTimer) => setTimeout(resolveTimer, 400))
  }
  return null
}

onMounted(async () => {
  const res = await fetchHealthWithRetry()
  backendOnline.value = Boolean(res)
  if (res) {
    const health = (await res.json().catch(() => null)) as {
      low_integrity?: boolean
      integrity_hint?: string
    } | null
    if (health?.low_integrity) {
      integrityNotice.value =
        health.integrity_hint ||
        '后端进程完整性过低，无法写入普通目录；请由 Electron 主进程或普通终端启动后端。'
    }
  }
  if (backendOnline.value) {
    chat.demoMode = false
    void sessions.refresh()
    formats.value = await fetchContextFormats()
    void settings.load()
    void fetchSandboxStatus()
      .then((status) => {
        if (!status.configured) sandboxNotice.value = status.notice
      })
      .catch(() => undefined)
  }
  // 从工作台“最近对话”进入时，恢复选中的会话
  if (!chat.threadId && sessions.activeThreadId) {
    void selectSession(sessions.activeThreadId)
  }
  // 已经处于某个会话时，先记录文件基线，便于下一轮标记新增文件。
  if (sessions.activeThreadId) void files.prime(sessions.activeThreadId)
})

// ---------- 上传交互 ----------
// 下拉里的三种来源分别对应不同的 accept，避免选择框里出现无关文件
function attachAccept(source: 'file' | 'image'): string {
  const groups = formats.value.groups || {}
  if (source === 'image') {
    return (groups.image || []).join(',') || '.png,.jpg,.jpeg,.webp,.gif'
  }
  const exts = [...(groups.document || []), ...(groups.text || [])]
  return exts.join(',') || formats.value.accept
}

async function pickAttach(source: 'file' | 'image') {
  if (uploading.value || chat.sending) return
  attachMenu.value = false
  fileAccept.value = attachAccept(source)
  // 浏览器要求点击必须发生在用户手势里，等 input 的 accept 更新后再触发
  await nextTick()
  fileInput.value?.click()
}

function openUrlMode() {
  attachMenu.value = false
  uploadError.value = ''
  urlMode.value = true
}

async function submitUrl() {
  const url = urlDraft.value.trim()
  if (!url || urlLoading.value) return
  if (!/^https?:\/\//i.test(url)) {
    uploadError.value = '链接需要以 http:// 或 https:// 开头'
    return
  }
  if (pendingFiles.value.length >= formats.value.max_files) {
    uploadError.value = `一次最多上传 ${formats.value.max_files} 个文件`
    return
  }
  uploadError.value = ''
  urlLoading.value = true
  try {
    const record = await importContextUrl(url, USER_ID)
    pendingFiles.value.push(record)
    urlDraft.value = ''
    urlMode.value = false
  } catch (error) {
    uploadError.value = (error as Error).message
  } finally {
    urlLoading.value = false
  }
}

function clearPending() {
  for (const file of pendingFiles.value) {
    const url = previews.value[file.id]
    if (url) URL.revokeObjectURL(url)
    void deleteContextFile(file.id, USER_ID)
  }
  pendingFiles.value = []
  previews.value = {}
  uploadError.value = ''
}

async function onFilesPicked(event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files || [])
  input.value = ''
  uploadError.value = ''
  if (files.length === 0) return

  const limit = formats.value.max_file_mb * 1024 * 1024
  const allowed = formats.value.accept
    ? formats.value.accept.split(',').map((item) => item.trim()).filter(Boolean)
    : []

  for (const file of files) {
    if (pendingFiles.value.length >= formats.value.max_files) {
      uploadError.value = `一次最多上传 ${formats.value.max_files} 个文件`
      break
    }
    if (file.size > limit) {
      uploadError.value = `${file.name} 超过 ${formats.value.max_file_mb} MB`
      continue
    }
    const ext = `.${(file.name.split('.').pop() || '').toLowerCase()}`
    if (allowed.length && !allowed.includes(ext)) {
      uploadError.value = `不支持的文件类型 ${ext}`
      continue
    }
    uploading.value = true
    try {
      const record = await uploadContextFile(file, USER_ID)
      pendingFiles.value.push(record)
      if (record.kind === 'image') {
        previews.value[record.id] = URL.createObjectURL(file)
      }
    } catch (error) {
      uploadError.value = (error as Error).message
    } finally {
      uploading.value = false
    }
  }
}

async function removePending(id: string) {
  pendingFiles.value = pendingFiles.value.filter((file) => file.id !== id)
  const url = previews.value[id]
  if (url) {
    URL.revokeObjectURL(url)
    delete previews.value[id]
  }
  await deleteContextFile(id, USER_ID)
}

// ---------- 会话切换 ----------
interface HistoryMessage {
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
}

async function selectSession(threadId: string) {
  clearPending()
  sessions.activeThreadId = threadId
  chat.activateThread(threadId)
  // 记录切换前的文件基线，下一轮结束时才能准确判断“本轮新增”。
  void files.prime(threadId)
  // 后台仍在运行的会话：保留实时缓冲，不能用 checkpoint 历史覆盖
  if (chat.isThreadSending(threadId)) return
  try {
    const res = await fetch(
      `${API_BASE}/history/${threadId}/messages?user_id=${USER_ID}`,
    )
    if (!res.ok) throw new Error(String(res.status))
    const data = (await res.json()) as {
      messages: HistoryMessage[]
      interrupt: Record<string, unknown> | null
      session_usage?: {
        input: number
        output: number
        total: number
        rounds?: number
        context_window?: number
        context_used?: number
      }
    }
    const msgs: ChatMsg[] = data.messages.map((m, i) => {
      if (m.role === 'user') return { id: `h-u-${i}`, kind: 'user', content: m.content }
      if (m.role === 'reasoning') {
        return {
          id: m.id || `h-r-${i}`,
          kind: 'reasoning',
          content: m.content,
          source: m.source || 'main',
        }
      }
      if (m.role === 'assistant') {
        return { id: m.id || `h-a-${i}`, kind: 'assistant', content: m.content, tokens: m.tokens }
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
        status: m.tool_status === 'error' ? 'error' : m.tool_status === 'calling' ? 'calling' : 'done',
        source: m.source,
        subagentName: m.role === 'delegation' && m.source && m.source !== 'main' ? m.source : undefined,
      }
    })
    if (data.interrupt) {
      const it = data.interrupt as { interrupt_id?: string; interrupt_type?: string }
      msgs.push({
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
          rounds: data.session_usage.rounds,
          contextWindow: data.session_usage.context_window,
          contextUsed: data.session_usage.context_used,
        }
      : null
    chat.loadHistory(msgs, threadId, usage)
    void hydrateTurnDurations(threadId, msgs)
    stickToBottom.value = true
    void scrollBottom()
  } catch {
    chat.activateThread(threadId)
  }
}

// 历史消息不带耗时：从轨迹接口把每个 run 的 duration 回填到对应轮次。
async function hydrateTurnDurations(threadId: string, msgs: ChatMsg[]) {
  try {
    const data = await fetchTrajectory(threadId, USER_ID)
    const userIds = msgs.filter((m) => m.kind === 'user').map((m) => m.id)
    if (!userIds.length || !data.runs.length) return
    const offset = Math.max(userIds.length - data.runs.length, 0)
    const durations: Record<string, number> = {}
    for (let i = 0; i < data.runs.length; i++) {
      const target = userIds[i + offset] || userIds[userIds.length - 1]
      const value = data.runs[i]?.summary?.duration_ms
      if (target && typeof value === 'number' && value > 0) {
        durations[target] = value
      }
    }
    chat.setTurnDurations(threadId, durations)
  } catch {
    // 轨迹接口不可用时不影响聊天历史展示
  }
}

async function createSession() {
  clearPending()
  const id = await sessions.createSession()
  if (!id) {
    sessions.activeThreadId = null
    chat.newChat()
    return
  }
  sessions.activeThreadId = id
  chat.activateThread(id)
  void files.prime(id)
}

/** 点击左侧项目条目：打开该项目下最近一条对话。 */
async function openProjectConversations(projectId: string) {
  const items = sessions.sessions
    .filter((item) => item.project_id === projectId)
    .slice()
    .sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at)))
  const latest = items[0]
  if (!latest) return
  await selectSession(latest.thread_id)
}

async function removeSession(threadId: string) {
  await sessions.removeSession(threadId)
  chat.releaseThread(threadId)
  files.forget(threadId)
  if (sessions.activeThreadId === threadId) {
    sessions.activeThreadId = null
  }
}

async function renameSession(threadId: string, title: string) {
  await sessions.renameSession(threadId, title)
}

async function togglePin(threadId: string, pinned: boolean) {
  await sessions.setPinned(threadId, pinned)
}

// 运行中 / 等待介入 标识按 thread 同步到会话栏
watch(
  () => chat.runningThreadIds,
  (ids) => {
    const next = new Set(ids)
    Array.from(sessions.runningThreads).forEach((tid) => {
      if (!next.has(tid)) sessions.markRunning(tid, false)
    })
    ids.forEach((tid) => sessions.markRunning(tid, true))
  },
  { immediate: true },
)

watch(
  () => chat.waitingThreadIds,
  (ids) => {
    const next = new Set(ids)
    Array.from(sessions.waitingThreads).forEach((tid) => {
      if (!next.has(tid)) sessions.markWaiting(tid, false)
    })
    ids.forEach((tid) => sessions.markWaiting(tid, true))
  },
  { immediate: true },
)

// 任意会话完成一轮后刷新列表（标题/条数）与文件抽屉
watch(
  () => chat.turnVersion,
  () => {
    if (backendOnline.value) void sessions.refresh()
    const threadId = chat.lastTurnThreadId
    if (threadId) void files.markRound(threadId)
    fileRefreshKey.value++
  },
)

// 新建会话 / 后台线程迁移后同步高亮
watch(
  () => chat.threadId,
  (tid) => {
    if (tid && sessions.sessions.some((item) => item.thread_id === tid)) {
      sessions.activeThreadId = tid
    }
  },
)

// ---------- 消息渲染 ----------
function visible(msg: ChatMsg): boolean {
  const prefs = display.prefs
  if (msg.kind === 'reasoning') return prefs.showReasoning
  if (msg.kind === 'subagent') return prefs.showDelegation
  if (msg.kind !== 'tool') return true
  if (msg.category === 'skill' && !prefs.showSkills) return false
  if (msg.category === 'delegation') {
    const asyncTask = msg.toolName === 'start_async_task'
    if (asyncTask && !prefs.showAsyncTasks) return false
    if (!prefs.showDelegation) return false
  }
  if (msg.category === 'tool' && !prefs.showToolCalls) return false
  return true
}

const shown = computed(() => chat.messages.filter(visible))

// ---------- 按「用户轮次」分组 ----------
// 每轮：用户消息 + 轨迹树（推理/工具/技能/委派/中间话术）+ 最终回答 + 中断卡片。
// 轨迹永远在正式回答之上；最后一轮在执行中时保持展开，结束后自动折叠。
interface TurnView {
  key: string
  user: ChatMsg | null
  process: ChatMsg[]
  final: ChatMsg | null
  interrupt: ChatMsg | null
  notes: ChatMsg[]
  live: boolean
}

const turns = computed<TurnView[]>(() => {
  const result: TurnView[] = []
  let current: TurnView | null = null

  const flush = () => {
    if (!current) return
    // 只有「本轮最后一条且带内容」的助手消息才算正式回答，其余都是过程轨迹。
    const last = current.process[current.process.length - 1]
    if (last && last.kind === 'assistant' && last.content) {
      current.final = last
      current.process = current.process.slice(0, -1)
    }
    result.push(current)
    current = null
  }

  for (const msg of shown.value) {
    if (msg.kind === 'user') {
      flush()
      current = {
        key: msg.id,
        user: msg,
        process: [],
        final: null,
        interrupt: null,
        notes: [],
        live: false,
      }
      continue
    }
    if (!current) {
      current = {
        key: `pre-${msg.id}`,
        user: null,
        process: [],
        final: null,
        interrupt: null,
        notes: [],
        live: false,
      }
    }
    if (msg.kind === 'interrupt') {
      if (msg.resolved) {
        // 已处理的 HITL 不再常驻聊天区：收进本轮轨迹树，只保留一行结论。
        current.process.push({
          id: `${msg.id}-resolved`,
          kind: 'interrupt',
          content: interruptSummary(msg),
          interruptType: msg.interruptType,
          resolved: msg.resolved,
          status: 'done',
          at: msg.at,
        })
      } else {
        current.interrupt = msg
      }
    } else if (msg.kind === 'system') {
      current.notes.push(msg)
    } else {
      current.process.push(msg)
    }
  }
  flush()

  const lastTurn = result[result.length - 1]
  if (lastTurn && chat.sending) lastTurn.live = true
  return result
})

function turnDuration(turn: TurnView): number | undefined {
  if (!turn.user) return undefined
  return chat.turnDurations[turn.user.id]
}

// 已回复的 HITL 在轨迹树里显示成一条可展开的结论行。
function interruptSummary(msg: ChatMsg): string {
  const payload = msg.payload || {}
  const hitl = payload.hitl as { title?: string } | undefined
  const fallback =
    String(payload.information_needed || '').trim().slice(0, 80) ||
    (msg.interruptType === 'hitl_approval' ? '审批请求' : '补充信息请求')
  const status =
    msg.resolved === 'approved' ? '已批准' : msg.resolved === 'rejected' ? '已拒绝' : '已回复'
  const detail = msg.resolution ? `：${msg.resolution}` : ''
  return `${hitl?.title || fallback}（${status}）${detail}`
}

const backendStatusLabel = computed(
  () => (backendOnline.value === null ? '检测后端…' : backendOnline.value ? '后端在线' : '后端离线'),
)
const backendStatusClass = computed(() =>
  backendOnline.value === null ? 'checking' : backendOnline.value ? 'online' : 'offline',
)

async function scrollBottom() {
  await nextTick()
  const el = scroller.value
  if (!el) return
  el.scrollTop = el.scrollHeight
  stickToBottom.value = true
  updateOverflow()
}

function updateOverflow() {
  const el = scroller.value
  hasOverflow.value = Boolean(el && el.scrollHeight - el.clientHeight > 24)
}

// 用户一旦向上滚动，就停止自动跟随；是否回到底部由按钮决定。
function onMessagesScroll() {
  const el = scroller.value
  if (!el) return
  updateOverflow()
  const distance = el.scrollHeight - el.scrollTop - el.clientHeight
  stickToBottom.value = distance < 48
}

function jumpToBottom() {
  void scrollBottom()
}

// ---------- 输入框自适应高度 ----------
function autoGrow() {
  const el = inputEl.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, TEXTAREA_MAX_HEIGHT)}px`
}

watch(draft, () => {
  void nextTick(autoGrow)
})

// ---------- 后端运行参数（思考 / 联网搜索） ----------
const settingsDisabled = computed(
  () => chat.sending || settings.saving || settings.online === false,
)
const settingsHint = computed(() => {
  if (settings.online === false) return '后端离线，开关仅本地展示'
  if (settings.saving) return '正在写入后端…'
  if (settings.error) return settings.error
  return ''
})

function toggleThinking(value: boolean) {
  void settings.setThinking(value)
}

function toggleWebSearch(value: boolean) {
  void settings.setWebSearch(value)
}

// ---------- 上下文窗口圆环 ----------
const DONUT_CIRC = 2 * Math.PI * 15.5
const ctxWindow = computed(() => chat.sessionTokens?.contextWindow || 0)
const ctxUsed = computed(
  () => chat.sessionTokens?.contextUsed ?? chat.sessionTokens?.input ?? 0,
)
const ctxPct = computed(() =>
  ctxWindow.value > 0
    ? Math.min(100, Math.round((ctxUsed.value / ctxWindow.value) * 100))
    : 0,
)
const ctxTone = computed(() =>
  ctxPct.value >= 85 ? 'danger' : ctxPct.value >= 60 ? 'warn' : 'ok',
)
const ctxDash = computed(
  () => `${(ctxPct.value / 100) * DONUT_CIRC} ${DONUT_CIRC}`,
)

// ---------- 消息复制 ----------
const copiedId = ref('')
let copyTimer: number | undefined

async function copyMessage(msg: ChatMsg) {
  const text = msg.content || ''
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    // 非安全上下文（http://IP）没有 clipboard API，退回 execCommand
    const area = document.createElement('textarea')
    area.value = text
    document.body.appendChild(area)
    area.select()
    document.execCommand('copy')
    area.remove()
  }
  copiedId.value = msg.id
  if (copyTimer) window.clearTimeout(copyTimer)
  copyTimer = window.setTimeout(() => (copiedId.value = ''), 1600)
}

function formatExact(total: number): string {
  return total.toLocaleString('zh-CN')
}

// ---------- 会话轮次达上限提醒 ----------
const roundNotice = ref(false)
// 每个 thread 只提醒一次；关闭或新开会话后不再对旧会话重复弹出
const noticeShownFor = ref<string | null>(null)
let noticeTimer: number | undefined

const userRounds = computed(() => chat.messages.filter((m) => m.kind === 'user').length)

watch(
  () => [chat.sending, chat.threadId, userRounds.value] as const,
  ([sending, tid, rounds]) => {
    if (!tid) {
      roundNotice.value = false
      return
    }
    if (sending || noticeShownFor.value === tid) return
    if (rounds >= chat.roundLimit) {
      roundNotice.value = true
      noticeShownFor.value = tid
      // 15 秒后自动渐隐，用户也可以手动关闭或新开会话
      if (noticeTimer) window.clearTimeout(noticeTimer)
      noticeTimer = window.setTimeout(() => (roundNotice.value = false), 15000)
    }
  },
)

function dismissRoundNotice() {
  roundNotice.value = false
  if (noticeTimer) window.clearTimeout(noticeTimer)
}

function newChatFromNotice() {
  dismissRoundNotice()
  void createSession()
}

// 内容增长时只在用户仍贴着底部才自动滚动，避免打断向上翻阅
watch(
  () => [
    chat.messages.length,
    shown.value.reduce(
      (sum, msg) =>
        sum + (msg.content?.length || 0) + (msg.result?.length || 0) + (msg.args?.length || 0),
      0,
    ),
    chat.sending,
  ],
  () => {
    void nextTick(() => {
      updateOverflow()
      if (stickToBottom.value) void scrollBottom()
    })
  },
)

async function submit() {
  if (!draft.value.trim() && pendingFiles.value.length === 0) return
  // 没有会话时先建一个，确保 SSE 请求始终带着确定的 thread_id
  if (!chat.threadId && !chat.demoMode) {
    const id = await sessions.createSession()
    if (!id) {
      uploadError.value = '无法创建会话，请检查后端连接'
      return
    }
    sessions.activeThreadId = id
    chat.activateThread(id)
  }
  const files = pendingFiles.value
  chat.send(draft.value, files)
  draft.value = ''
  // 已发送的附件不再重复引用；预览 URL 保留给消息气泡显示。
  pendingFiles.value = []
  uploadError.value = ''
  stickToBottom.value = true
  void nextTick(() => {
    autoGrow()
    void scrollBottom()
  })
}
</script>

<template>
  <div class="app-shell">
    <TopNav>
      <template #actions>
        <span class="status-chip" :class="backendStatusClass">
          <i class="dot"></i>{{ backendStatusLabel }}
        </span>
      </template>
    </TopNav>

    <!-- 轮次达上限：顶部渐隐渐现提醒卡 -->
    <Transition name="toast">
      <div v-if="roundNotice" class="toast-slot">
        <RoundLimitToast
          :rounds="userRounds"
          :limit="chat.roundLimit"
          @new-chat="newChatFromNotice"
          @close="dismissRoundNotice"
        />
      </div>
    </Transition>

    <div class="chat-layout">
      <SessionPanel
        @select="selectSession"
        @create="createSession"
        @remove="removeSession"
        @rename="renameSession"
        @pin="togglePin"
        @select-project="openProjectConversations"
      />

      <section class="panel chat">
        <header class="chat-head">
          <div class="title">
            <span class="title-icon"><AppIcon name="message" :size="14" /></span>
            <div>
              <strong>对话</strong>
              <small>推理 / 工具轨迹树 · SSE 流式 · HITL 人工介入</small>
            </div>
          </div>
          <div class="controls">
            <span v-if="chat.threadId" class="chip">thread {{ chat.threadId.slice(0, 8) }}…</span>
            <button class="ghost" type="button" @click="showFiles = !showFiles">
              <AppIcon name="file" :size="13" /> 文件
            </button>
            <button class="ghost" type="button" @click="createSession()">
              <AppIcon name="plus" :size="13" /> 新会话
            </button>
          </div>
        </header>

        <div v-if="sandboxNotice && !sandboxBannerClosed" class="sandbox-warn">
          <AppIcon name="shield" :size="13" />
          <span>{{ sandboxNotice }}</span>
          <button type="button" title="关闭提示" @click="sandboxBannerClosed = true">
            <AppIcon name="close" :size="10" />
          </button>
        </div>

        <div v-if="integrityNotice && !integrityBannerClosed" class="sandbox-warn">
          <AppIcon name="shield" :size="13" />
          <span>{{ integrityNotice }}</span>
          <button type="button" title="关闭提示" @click="integrityBannerClosed = true">
            <AppIcon name="close" :size="10" />
          </button>
        </div>

        <div class="messages-wrap">
          <div ref="scroller" class="messages" @scroll.passive="onMessagesScroll">
            <div v-if="shown.length === 0" class="empty">
              <div class="empty-orb"><AppIcon name="sparkles" :size="26" /></div>
              <h3>Harness 对话测试台</h3>
              <p>发送一条消息，观察模型推理、工具调用、技能使用与人工介入的完整链路。</p>
              <div class="suggestions">
                <button v-for="s in SUGGESTIONS" :key="s" type="button" @click="chat.send(s)">{{ s }}</button>
              </div>
            </div>

            <template v-for="turn in turns" :key="turn.key">
              <div v-if="turn.user" class="row user">
                <div class="msg-col user">
                  <div class="bubble user-bubble">
                    <div v-if="turn.user.attachments?.length" class="bubble-atts">
                      <span v-for="att in turn.user.attachments" :key="att.id" class="bubble-att">
                        <img v-if="previews[att.id]" :src="previews[att.id]" alt="" />
                        <AppIcon v-else name="file" :size="11" />
                        <span>{{ att.name }}</span>
                      </span>
                    </div>
                    <span v-if="turn.user.content">{{ turn.user.content }}</span>
                  </div>
                  <div v-if="turn.user.content" class="msg-meta user">
                    <button
                      class="copy-mini"
                      type="button"
                      :class="{ copied: copiedId === turn.user.id }"
                      :title="copiedId === turn.user.id ? '已复制' : '复制文本'"
                      @click="copyMessage(turn.user)"
                    >
                      <AppIcon :name="copiedId === turn.user.id ? 'check' : 'copy'" :size="10" />
                      <span>{{ copiedId === turn.user.id ? '已复制' : '复制' }}</span>
                    </button>
                  </div>
                </div>
              </div>

              <!-- 轨迹永远在正式回答之上：树状展开，结束后自动折叠 -->
              <div v-if="turn.process.length || turn.live" class="row indent tree-row">
                <div class="tree-col">
                  <TrajectoryTree
                    :items="turn.process"
                    :live="turn.live"
                    :duration-ms="turnDuration(turn)"
                  />
                </div>
              </div>

              <div v-if="turn.final" class="row">
                <div class="avatar ai">AI</div>
                <div class="msg-col ai">
                  <div class="bubble ai-bubble">
                    <MarkdownView v-if="turn.final.content" :content="turn.final.content" />
                    <span v-if="turn.final.streaming" class="cursor"></span>
                  </div>
                  <div v-if="turn.final.content && !turn.final.streaming" class="msg-meta ai">
                    <button
                      class="copy-mini"
                      type="button"
                      :class="{ copied: copiedId === turn.final.id }"
                      :title="copiedId === turn.final.id ? '已复制' : '复制回答'"
                      @click="copyMessage(turn.final)"
                    >
                      <AppIcon :name="copiedId === turn.final.id ? 'check' : 'copy'" :size="10" />
                      <span>{{ copiedId === turn.final.id ? '已复制' : '复制' }}</span>
                    </button>
                  </div>
                </div>
              </div>

              <div v-if="turn.interrupt" class="row indent">
                <InterruptCard :msg="turn.interrupt" />
              </div>

              <div v-for="note in turn.notes" :key="note.id" class="row center">
                <span class="system-note">{{ note.content }}</span>
              </div>
            </template>
          </div>

          <!-- 用户向上翻阅时，不再强制滚到底，只在视口底部提供一个回底按钮 -->
          <Transition name="pop">
            <button
              v-if="hasOverflow && !stickToBottom"
              class="jump-bottom"
              type="button"
              title="回到最新消息"
              @click="jumpToBottom"
            >
              <AppIcon name="chevronRight" :size="12" />
              <span>回到底部</span>
            </button>
          </Transition>
        </div>

        <footer class="input-bar">
          <!-- 发送框顶部：快速切换后端的思考模式与联网搜索 -->
          <div class="input-head">
            <div class="mode-toggles">
              <span
                class="mode-toggle"
                :class="{ on: settings.thinking, disabled: settingsDisabled }"
                :title="'开启后模型返回推理过程，前端在轨迹树里流式展示'"
              >
                <AppIcon name="sparkles" :size="12" />
                <span>思考模式</span>
                <UiSwitch
                  :model-value="settings.thinking"
                  :disabled="settingsDisabled"
                  @update:model-value="toggleThinking"
                />
              </span>
              <span
                class="mode-toggle"
                :class="{ on: settings.webSearch, disabled: settingsDisabled }"
                :title="'开启后 Agent 可调用 search_web / fetch_web，关闭时后端不再绑定搜索工具'"
              >
                <AppIcon name="globe" :size="12" />
                <span>联网搜索</span>
                <UiSwitch
                  :model-value="settings.webSearch"
                  :disabled="settingsDisabled"
                  @update:model-value="toggleWebSearch"
                />
              </span>
            </div>
            <span v-if="settingsHint" class="settings-hint">{{ settingsHint }}</span>
          </div>

          <div v-if="pendingFiles.length" class="pending-row">
            <span v-for="att in pendingFiles" :key="att.id" class="pending-chip">
              <img v-if="previews[att.id]" :src="previews[att.id]" alt="" />
              <AppIcon v-else name="file" :size="12" />
              <span class="p-name">{{ att.name }}</span>
              <button type="button" title="移除" @click="removePending(att.id)">
                <AppIcon name="close" :size="10" />
              </button>
            </span>
          </div>
          <p v-if="uploadError" class="upload-error">{{ uploadError }}</p>

          <div v-if="urlMode" class="url-row">
            <AppIcon name="globe" :size="14" />
            <input
              v-model="urlDraft"
              type="url"
              placeholder="https://example.com/article"
              :disabled="urlLoading"
              @keydown.enter.prevent="submitUrl"
              @keydown.esc="urlMode = false"
            />
            <button
              class="url-ok"
              type="button"
              :disabled="urlLoading || !urlDraft.trim()"
              @click="submitUrl"
            >
              {{ urlLoading ? '抓取中…' : '导入' }}
            </button>
            <button class="url-cancel" type="button" title="取消" @click="urlMode = false">
              <AppIcon name="close" :size="11" />
            </button>
          </div>

          <div class="input-row">
            <div class="attach-wrap">
              <button
                class="attach"
                :class="{ active: attachMenu }"
                type="button"
                :disabled="uploading || chat.sending"
                title="添加上下文：文件 / 图片 / 网络链接"
                @click="attachMenu = !attachMenu"
              >
                <AppIcon name="paperclip" :size="17" />
                <span v-if="uploading" class="attach-spin"></span>
              </button>
              <Transition name="pop">
                <div v-if="attachMenu" class="attach-menu">
                  <button type="button" @click="pickAttach('file')">
                    <AppIcon name="file" :size="14" />
                    <span>文件</span>
                    <small>文档 / 文本</small>
                  </button>
                  <button type="button" @click="pickAttach('image')">
                    <AppIcon name="image" :size="14" />
                    <span>图片</span>
                    <small>随消息多模态发送</small>
                  </button>
                  <button type="button" @click="openUrlMode">
                    <AppIcon name="globe" :size="14" />
                    <span>网络链接</span>
                    <small>抓取网页正文</small>
                  </button>
                </div>
              </Transition>
            </div>
            <div v-if="attachMenu" class="menu-mask" @click="attachMenu = false"></div>
            <input
              ref="fileInput"
              class="file-input"
              type="file"
              multiple
              :accept="fileAccept || formats.accept"
              @change="onFilesPicked"
            />
            <textarea
              ref="inputEl"
              v-model="draft"
              rows="1"
              placeholder="输入消息，可附图片/文档/网页链接；Enter 发送 / Shift+Enter 换行"
              :disabled="chat.sending"
              @input="autoGrow"
              @keydown.enter.exact.prevent="submit"
            ></textarea>
            <button v-if="chat.sending" class="stop" type="button" title="中止" @click="chat.stop()">
              <AppIcon name="stop" :size="15" />
            </button>
            <button
              v-else
              class="send"
              type="button"
              :disabled="(!draft.trim() && pendingFiles.length === 0) || uploading"
              @click="submit"
            >
              <AppIcon name="send" :size="15" />
            </button>
          </div>

          <!-- 本会话统计 + 上下文窗口圆环 -->
          <div v-if="chat.sessionTokens" class="input-foot">
            <div
              class="usage-text"
              :title="`按最终回答统计：输出 ${chat.sessionTokens.output} tok（历轮生成之和）；当前上下文 ${formatExact(ctxUsed)} tok（最近一轮输入，含系统提示词/工具/技能/记忆）`"
            >
              <AppIcon name="cpu" :size="10" />
              <span>本会话输出</span>
              <b>{{ formatExact(chat.sessionTokens.output) }} tok</b>
              <em>·</em>
              <span>当前上下文</span>
              <b>{{ formatExact(ctxUsed) }} tok</b>
              <em v-if="chat.sessionTokens.rounds">· {{ chat.sessionTokens.rounds }} 轮</em>
            </div>

            <div
              v-if="ctxWindow > 0"
              class="ctx-donut"
              :class="ctxTone"
              :title="`上下文窗口占用 ${formatExact(ctxUsed)} / ${formatExact(ctxWindow)} tok（${ctxPct}%）`"
            >
              <svg viewBox="0 0 36 36" aria-hidden="true">
                <circle class="donut-track" cx="18" cy="18" r="15.5" />
                <circle
                  class="donut-bar"
                  cx="18"
                  cy="18"
                  r="15.5"
                  :stroke-dasharray="ctxDash"
                />
              </svg>
              <span class="donut-label">{{ ctxPct }}%</span>
            </div>
          </div>
        </footer>
      </section>

      <Transition name="slide">
        <FilePanel
          v-if="showFiles"
          :thread-id="chat.threadId"
          :refresh-key="fileRefreshKey"
          @close="showFiles = false"
        />
      </Transition>
    </div>
  </div>
</template>

<style scoped>
.app-shell {
  height: 100vh;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.chat-layout {
  flex: 1;
  display: flex;
  gap: 0;
  align-items: stretch;
  height: calc(100vh - var(--topbar-h));
  min-height: 0;
  padding: 0;
  min-width: 0;
  overflow: hidden;
}

.chat {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  min-width: 0;
  overflow: hidden;
  border-top: 0;
  background: transparent;
}

.chat-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding: 11px 18px;
  border-bottom: 1px solid var(--line);
  background: transparent;
}

.title {
  display: flex;
  align-items: center;
  gap: 9px;
}

.title-icon {
  display: grid;
  place-items: center;
  width: 22px;
  height: 22px;
  color: var(--accent-cyan);
  background: transparent;
}

.title strong {
  display: block;
  font-size: 14px;
  letter-spacing: -0.01em;
}

.title small {
  font-size: 10.5px;
  color: var(--text-3);
}

.controls {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: none;
}

.status-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex: none;
  padding: 3px 9px;
  font-size: 10.5px;
  white-space: nowrap;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-2);
}

.status-chip .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}

.status-chip.online {
  color: var(--green);
  border-color: rgba(61, 220, 132, 0.35);
}

.status-chip.online .dot {
  box-shadow: 0 0 6px var(--green);
}

.status-chip.offline {
  color: var(--amber);
  border-color: rgba(242, 176, 60, 0.35);
}

.status-chip.checking {
  color: var(--text-3);
}

.chip {
  flex: none;
  padding: 3px 8px;
  font-size: 10px;
  white-space: nowrap;
  color: var(--text-3);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: transparent;
}

.ghost {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  flex: none;
  padding: 5px 10px;
  font-size: 11.5px;
  white-space: nowrap;
  color: var(--text-2);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: transparent;
  transition: color 0.16s, border-color 0.16s, background 0.16s;
}

.ghost:hover {
  color: var(--text-1);
  border-color: var(--line-strong);
  background: var(--bg-hover);
}

.messages-wrap {
  position: relative;
  flex: 1;
  min-height: 0;
  display: flex;
}

/* 未配置沙箱时的本机执行安全提示 */
.sandbox-warn {
  display: flex;
  align-items: center;
  gap: 7px;
  margin: 0 16px 6px;
  padding: 6px 10px;
  font-size: 10.5px;
  line-height: 1.5;
  color: var(--amber);
  border: 1px solid rgba(242, 176, 60, 0.28);
  border-radius: var(--radius-sm);
  background: rgba(242, 176, 60, 0.06);
}

.sandbox-warn span {
  flex: 1;
  min-width: 0;
}

.sandbox-warn button {
  display: grid;
  place-items: center;
  width: 18px;
  height: 18px;
  flex: none;
  color: var(--text-3);
  border: none;
  border-radius: 4px;
  background: transparent;
}

.sandbox-warn button:hover {
  color: var(--text-1);
  background: rgba(255, 255, 255, 0.06);
}

.messages {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

/* 轨迹树容器：占满宽度，后面可跟「完整轨迹」入口 */
.tree-row {
  width: 100%;
}

.tree-col {
  display: flex;
  flex-direction: column;
  gap: 2px;
  width: 100%;
  min-width: 0;
}

/* 回到最新消息：固定在聊天视口底部，不阻塞阅读 */
.jump-bottom {
  position: absolute;
  left: 50%;
  bottom: 14px;
  transform: translateX(-50%);
  z-index: 12;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 5px 11px;
  font-size: 10.5px;
  color: var(--text-1);
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-sm);
  background: var(--bg-elevated);
}

.jump-bottom :deep(svg) {
  transform: rotate(90deg);
  color: var(--accent-cyan);
}

.jump-bottom:hover {
  border-color: var(--accent);
  color: #fff;
}

.empty {
  margin: auto;
  max-width: 460px;
  text-align: center;
}

.empty-orb {
  display: grid;
  place-items: center;
  width: 48px;
  height: 48px;
  margin: 0 auto 14px;
  color: var(--accent-cyan);
  background: transparent;
  border: 1px solid var(--line-strong);
  border-radius: 50%;
}

.empty h3 {
  margin: 0 0 6px;
  font-size: 16px;
}

.empty p {
  margin: 0 0 16px;
  font-size: 12px;
  color: var(--text-3);
}

.suggestions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 6px;
}

.suggestions button {
  padding: 6px 11px;
  font-size: 11.5px;
  color: var(--text-2);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: transparent;
  transition: color 0.16s, border-color 0.16s, background 0.16s;
}

.suggestions button:hover {
  color: var(--text-1);
  border-color: var(--accent);
  background: var(--bg-hover);
}

.row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  animation: fadein 0.3s ease;
}

@keyframes fadein {
  from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: none; }
}

.row.user {
  justify-content: flex-end;
}

.row.indent {
  padding-left: 40px;
}

.row.center {
  justify-content: center;
}

.avatar {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 600;
  flex: none;
}

.avatar.ai {
  color: var(--accent-cyan);
  border: 1px solid var(--line-strong);
  background: transparent;
}

.bubble {
  position: relative;
  max-width: 72%;
  padding: 9px 13px;
  font-size: 13px;
  line-height: 1.7;
  border-radius: var(--radius-md);
  white-space: pre-wrap;
  word-break: break-word;
}

/* 气泡 + 下方 meta 行的纵向容器 */
.msg-col {
  display: flex;
  flex-direction: column;
  max-width: 72%;
  min-width: 0;
}

.msg-col.user {
  align-items: flex-end;
}

.msg-col.ai {
  align-items: flex-start;
}

.msg-col .bubble {
  max-width: 100%;
}

/* 气泡正下方：复制按钮 / Token 消耗 */
.msg-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
}

.copy-mini {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 7px;
  font-size: 10px;
  color: var(--text-3);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: transparent;
  transition: color 0.16s, border-color 0.16s, background 0.16s;
}

.copy-mini:hover {
  color: var(--accent-cyan);
  border-color: var(--line-strong);
  background: var(--bg-hover);
}

.copy-mini.copied {
  color: var(--accent-cyan);
  border-color: rgba(55, 208, 196, 0.45);
}

/* 发送栏底部：左侧用量文本 + 右侧上下文圆环 */
.input-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 0 2px;
}

.usage-text {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  font-size: 10px;
  color: var(--text-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.usage-text :deep(svg) {
  color: var(--accent-cyan);
}

.usage-text b {
  font-weight: 600;
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  color: var(--text-1);
}

.usage-text em {
  font-style: normal;
  font-variant-numeric: tabular-nums;
  opacity: 0.8;
}

/* 上下文窗口占用：圆形进度条 */
.ctx-donut {
  position: relative;
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  flex: none;
}

.ctx-donut svg {
  width: 34px;
  height: 34px;
}

.donut-track {
  fill: none;
  stroke: rgba(96, 156, 255, 0.16);
  stroke-width: 3;
}

.donut-bar {
  fill: none;
  stroke: var(--accent-cyan);
  stroke-width: 3;
  stroke-linecap: round;
  stroke-dashoffset: 0;
  transform: rotate(-90deg);
  transform-origin: 50% 50%;
  transition: stroke-dasharray 0.35s ease, stroke 0.25s;
}

.ctx-donut.warn .donut-bar {
  stroke: var(--amber);
}

.ctx-donut.danger .donut-bar {
  stroke: #e2605e;
}

.donut-label {
  position: absolute;
  font-size: 9px;
  font-variant-numeric: tabular-nums;
  color: var(--text-2);
}

.ctx-donut.danger .donut-label {
  color: var(--danger);
}

.ai-bubble {
  border: 1px solid var(--line);
  background: rgba(8, 16, 30, 0.34);
  backdrop-filter: blur(6px);
  border-top-left-radius: 0;
  /* Markdown 渲染后由块级元素控制换行，不能再沿用 pre-wrap */
  white-space: normal;
}

.user-bubble {
  color: var(--text-1);
  background: rgba(91, 140, 255, 0.14);
  border: 1px solid rgba(91, 140, 255, 0.3);
  border-top-right-radius: 0;
}

.cursor {
  display: inline-block;
  width: 7px;
  height: 15px;
  margin-left: 2px;
  vertical-align: -2px;
  background: var(--accent-cyan);
  animation: blink 0.9s steps(1) infinite;
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}

.system-note {
  font-size: 11.5px;
  color: var(--text-3);
}

.input-bar {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 16px;
  border-top: 1px solid var(--line);
  background: transparent;
}

/* 发送框顶部：思考模式 / 联网搜索快速开关 */
.input-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  min-height: 26px;
}

.mode-toggles {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}

.mode-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11.5px;
  color: var(--text-3);
  white-space: nowrap;
  transition: color 0.18s;
}

.mode-toggle :deep(svg) {
  color: var(--text-3);
  transition: color 0.18s;
}

.mode-toggle.on,
.mode-toggle.on :deep(svg) {
  color: var(--accent-cyan);
}

.mode-toggle.disabled {
  opacity: 0.55;
}

.settings-hint {
  min-width: 0;
  overflow: hidden;
  font-size: 10px;
  color: var(--amber);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.input-row {
  display: flex;
  align-items: flex-end;
  gap: 8px;
}

/* 附件选择：原生 input 隐藏，按钮触发 */
.file-input {
  display: none;
}

.attach {
  position: relative;
  display: grid;
  place-items: center;
  width: 40px;
  height: 40px;
  flex: none;
  color: var(--text-2);
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-md);
  background: transparent;
  transition: color 0.16s, border-color 0.16s, background 0.16s;
}

/* 附件下拉容器：菜单向上弹出，避免遮挡输入框 */
.attach-wrap {
  position: relative;
  flex: none;
}

.attach.active {
  color: var(--accent-cyan);
  border-color: var(--accent);
  background: var(--bg-hover);
}

.attach-menu {
  position: absolute;
  bottom: calc(100% + 10px);
  left: 0;
  z-index: 41;
  display: flex;
  flex-direction: column;
  gap: 2px;
  width: 210px;
  padding: 6px;
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-md);
  background: var(--bg-elevated);
  box-shadow: 0 10px 28px rgba(2, 6, 14, 0.5);
}

.attach-menu button {
  display: grid;
  grid-template-columns: 18px 1fr;
  grid-template-rows: auto auto;
  column-gap: 8px;
  align-items: center;
  padding: 8px 10px;
  text-align: left;
  color: var(--text-2);
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  transition: color 0.15s, background 0.15s;
}

.attach-menu button:hover {
  color: var(--text-1);
  background: var(--bg-hover);
}

.attach-menu button svg,
.attach-menu button :deep(svg) {
  grid-row: 1 / span 2;
  color: var(--accent-cyan);
}

.attach-menu button span {
  font-size: 12.5px;
}

.attach-menu button small {
  font-size: 10.5px;
  color: var(--text-3);
}

/* 点击空白关闭下拉 */
.menu-mask {
  position: fixed;
  inset: 0;
  z-index: 40;
}

/* 网络链接输入行 */
.url-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 10px;
  color: var(--accent-cyan);
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-sm);
  background: rgba(4, 10, 20, 0.4);
}

.url-row input {
  flex: 1;
  min-width: 0;
  padding: 2px 4px;
  font-size: 12.5px;
  font-family: inherit;
  color: var(--text-1);
  border: none;
  background: transparent;
  outline: none;
}

.url-row input::placeholder {
  color: var(--text-3);
}

.url-ok {
  flex: none;
  padding: 4px 12px;
  font-size: 11.5px;
  color: #fff;
  border: none;
  border-radius: var(--radius-sm);
  background: var(--accent);
}

.url-ok:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.url-cancel {
  display: grid;
  place-items: center;
  width: 20px;
  height: 20px;
  flex: none;
  color: var(--text-3);
  border: none;
  border-radius: 5px;
  background: transparent;
}

.url-cancel:hover {
  color: #e2605e;
  background: rgba(226, 96, 94, 0.14);
}

.pop-enter-active,
.pop-leave-active {
  transition: all 0.16s ease;
}

.pop-enter-from,
.pop-leave-to {
  opacity: 0;
  transform: translateY(6px) scale(0.97);
}

.attach:hover:not(:disabled) {
  color: var(--accent-cyan);
  border-color: var(--accent);
  background: var(--bg-hover);
}

.attach:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.attach-spin {
  position: absolute;
  width: 34px;
  height: 34px;
  border: 2px solid transparent;
  border-top-color: var(--accent-cyan);
  border-radius: 50%;
  animation: spin 0.7s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.pending-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.pending-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 6px 3px 6px;
  font-size: 11px;
  color: var(--text-2);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: transparent;
}

.pending-chip img {
  width: 24px;
  height: 24px;
  object-fit: cover;
  border-radius: 4px;
}

.pending-chip .p-name {
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pending-chip button {
  display: grid;
  place-items: center;
  width: 16px;
  height: 16px;
  color: var(--text-3);
  border: none;
  border-radius: 4px;
  background: transparent;
}

.pending-chip button:hover {
  color: #e2605e;
  background: rgba(226, 96, 94, 0.14);
}

.upload-error {
  margin: 0;
  font-size: 11px;
  color: var(--amber);
}

.bubble-atts {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 6px;
}

.bubble-att {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 8px 3px 4px;
  font-size: 10.5px;
  border-radius: 7px;
  background: rgba(0, 0, 0, 0.18);
}

.bubble-att img {
  width: 26px;
  height: 26px;
  object-fit: cover;
  border-radius: 4px;
}

.input-row textarea {
  flex: 1;
  min-height: 40px;
  max-height: 168px;
  padding: 10px 14px;
  font-size: 13px;
  font-family: inherit;
  color: var(--text-1);
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-md);
  background: var(--bg-deep);
  resize: none;
  outline: none;
  overflow-y: auto;
  line-height: 1.6;
}

.input-row textarea:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 1px rgba(91, 140, 255, 0.22);
}

.send,
.stop {
  display: grid;
  place-items: center;
  width: 40px;
  height: 40px;
  border: none;
  border-radius: var(--radius-md);
  color: #fff;
  transition: filter 0.16s ease;
  flex: none;
}

.send {
  background: var(--accent);
}

.send:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.stop {
  background: var(--danger);
}

.slide-enter-active,
.slide-leave-active {
  transition: all 0.25s ease;
}

.slide-enter-from,
.slide-leave-to {
  opacity: 0;
  transform: translateX(16px);
}

/* 轮次提醒卡：固定顶部居中，渐显下浮 / 渐隐上收 */
.toast-slot {
  position: fixed;
  top: calc(var(--topbar-h) + 12px);
  left: 50%;
  transform: translateX(-50%);
  z-index: 80;
  width: min(720px, calc(100vw - 36px));
}

.toast-enter-active {
  transition: opacity 0.45s ease, transform 0.45s cubic-bezier(0.22, 1, 0.36, 1);
}

.toast-leave-active {
  transition: opacity 0.4s ease, transform 0.4s ease;
}

.toast-enter-from {
  opacity: 0;
  transform: translateX(-50%) translateY(-18px) scale(0.97);
}

.toast-leave-to {
  opacity: 0;
  transform: translateX(-50%) translateY(-10px);
}
</style>
