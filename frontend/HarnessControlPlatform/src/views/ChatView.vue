<script setup lang="ts">
// 首页：对话测试（顶栏菜单 + 左侧会话栏 + SSE 流式 + 文件预览）
import { computed, nextTick, onBeforeUnmount, onMounted, provide, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { InputInstance } from 'element-plus'
import {
  ArrowDown,
  Check,
  Close,
  CopyDocument,
  Document,
  Flag,
  Link,
  Paperclip,
  Picture,
  Plus,
  Promotion,
  VideoPause,
} from '@element-plus/icons-vue'
import TopNav from '@/components/console/TopNav.vue'
import AppIcon from '@/components/console/AppIcon.vue'
import type { IconName } from '@/components/console/icons'
import InterruptCard from '@/components/chat/InterruptCard.vue'
import CompactionBanner from '@/components/chat/CompactionBanner.vue'
import SessionPanel from '@/components/chat/SessionPanel.vue'
import FilePanel from '@/components/chat/FilePanel.vue'
import MarkdownView from '@/components/chat/MarkdownView.vue'
import ProducedFilesCard from '@/components/chat/ProducedFilesCard.vue'
import TrajectoryTree from '@/components/chat/TrajectoryTree.vue'
import {
  TRAJECTORY_REGISTRY,
  type TrajectoryRegistry,
} from '@/components/chat/trajectoryTypes'
import ExecutionCard from '@/components/taskflow/ExecutionCard.vue'
import SchedulerPlanCard from '@/components/scheduler/SchedulerPlanCard.vue'
import BackgroundTasksBar from '@/components/scheduler/BackgroundTasksBar.vue'
import RoundLimitToast from '@/components/chat/RoundLimitToast.vue'
import HoloTooltip from '@/components/console/HoloTooltip.vue'
import { API_BASE } from '@/api/client'
import {
  deleteContextFile,
  fetchContextFormats,
  importContextUrl,
  resolveContextFiles,
  uploadContextFile,
  type ContextFile,
  type ContextFormats,
} from '@/api/context'
import { fetchTrajectory } from '@/api/trajectory'
import {
  categoryOf,
  useChatStore,
  type ChatMsg,
  type TerminateReason,
} from '@/stores/chat'
import { USER_ID, useSessionStore } from '@/stores/sessions'
import { useDisplayStore } from '@/stores/display'
import { useFileStore } from '@/stores/files'
import { useSettingsStore } from '@/stores/settings'
import { useAgentStore } from '@/stores/agents'
import { avatarGradient, avatarInitial } from '@/components/myagent/avatar'
import { formatContext } from '@/stores/models'
import { fetchSandboxStatus, fetchFeedbackSettings, fetchGlobalModelSettings, type GlobalModelSettings } from '@/api/settings'
import { withdrawLatestMessage, fetchSessionUsage, type SessionUsage } from '@/api/chat'

const chat = useChatStore()
const display = useDisplayStore()
const sessions = useSessionStore()
const files = useFileStore()
const settings = useSettingsStore()
const agents = useAgentStore()

// 轨迹节点索引：TrajectoryNode 挂载时登记，顶部粘性折叠条据此定位并代理收起。
const trajectoryRegistry: TrajectoryRegistry = new Map()
provide(TRAJECTORY_REGISTRY, trajectoryRegistry)

// Agent 下拉项：补上头像与所属组的组标（队长组带小旗帜）。
// 组信息以 /agent-groups 为准，/agents 列表不返回组籍。
const agentOptions = computed(() =>
  agents.options.map((opt) => ({
    ...opt,
    avatar: agents.byId(opt.value)?.avatar || '',
    groups: agents.groupsOf(opt.value).map((group) => ({
      group_id: group.group_id,
      name: group.name,
      role: group.leader_agent_id === opt.value ? ('leader' as const) : ('member' as const),
    })),
  })),
)

// 当前 Agent 可用的组；只有组长组能作为会话入口（组员由组长委派）。
const groupOptions = computed(() =>
  agents.groupsOf(chat.selectedAgentId).map((group) => {
    const isLeader = group.leader_agent_id === chat.selectedAgentId
    return {
      value: group.group_id,
      name: group.name,
      role: isLeader ? '组长' : '组员',
      isLeader,
      label: `${group.name}${isLeader ? ' · 组长' : ' · 组员'}`,
    }
  }),
)

// 换 Agent 后只清理失效的组选择；不自动选组，新会话默认以独立 Agent 执行。
function syncGroupForAgent() {
  // 组列表还没加载完时不做判断，否则会把会话绑定的组误清空。
  if (!agents.groupsLoaded) return
  const available = groupOptions.value
  if (
    chat.selectedGroupId &&
    !available.some((item) => item.value === chat.selectedGroupId)
  ) {
    chat.selectedGroupId = ''
  }
}

watch(
  () => chat.selectedAgentId,
  () => syncGroupForAgent(),
)

// 组列表异步到达后补一次校验：会话绑定的组可能属于别的 Agent。
watch(
  () => agents.groupsLoaded,
  (loaded) => {
    if (loaded) syncGroupForAgent()
  },
)

// 未配置沙箱时在对话页顶部提示本机执行的安全风险。
const sandboxNotice = ref('')
const sandboxBannerClosed = ref(false)
const integrityNotice = ref('')
const integrityBannerClosed = ref(false)
// 反馈总开关；关闭时隐藏赞同/不赞同/重试，仅保留复制。
const feedbackEnabled = ref(true)

const draft = ref('')
const scroller = ref<HTMLElement | null>(null)
const inputEl = ref<InputInstance | null>(null)
const showFiles = ref(false)

/** 消息泡文件卡片入口：打开右侧抽屉并定位到该文件 */
function openSessionFile(file: { path: string; origin: 'project' | 'sandbox' }) {
  files.requestOpen(file.path, file.origin)
  showFiles.value = true
}

/** 当前会话最近一轮的有用产出，只渲染在最后一条回答下方 */
const turnArtifacts = computed(() => files.artifactsFor(chat.threadId))
const fileRefreshKey = ref(0)
// null=检测中 true=在线 false=离线（离线时提示后端不可达）
const backendOnline = ref<boolean | null>(null)
// 用户离开底部后不再强制滚动，改为提供「回到底部」按钮
const stickToBottom = ref(true)
const hasOverflow = ref(false)
// 右侧轮次导航：展开态、当前高亮项；刻度与列表共用同一份数据
const navActive = ref(0)
const navOpen = ref(false)
const navList = ref<HTMLElement | null>(null)

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

// ---------- 未发送草稿持久化 ----------
// 草稿按 thread_id 存进 localStorage：切换会话和重启程序后都能恢复。
// 尚未创建会话时的输入统一存到 new 键，首次启动时再回填。
const DRAFT_PREFIX = 'harness.chat.draft.'
const NEW_DRAFT_KEY = 'new'
const LAST_THREAD_KEY = 'harness.chat.lastThread'
const DRAFT_SAVE_DELAY = 400

interface DraftRecord {
  text: string
  files: ContextFile[]
}

let draftTimer: number | undefined
// 恢复/清空输入期间暂停落盘，避免把中间态（空输入）覆盖成草稿。
let draftLocked = false

function isContextFile(value: unknown): value is ContextFile {
  if (!value || typeof value !== 'object') return false
  const item = value as Partial<ContextFile>
  return (
    typeof item.id === 'string' &&
    typeof item.name === 'string' &&
    typeof item.kind === 'string'
  )
}

function draftKey(threadId: string | null): string {
  return `${DRAFT_PREFIX}${threadId || NEW_DRAFT_KEY}`
}

function readDraft(threadId: string | null): DraftRecord | null {
  try {
    const raw = localStorage.getItem(draftKey(threadId))
    if (!raw) return null
    const parsed = JSON.parse(raw) as { text?: unknown; files?: unknown }
    const text = typeof parsed.text === 'string' ? parsed.text : ''
    const files = Array.isArray(parsed.files) ? parsed.files.filter(isContextFile) : []
    if (!text.trim() && files.length === 0) return null
    return { text, files }
  } catch {
    return null
  }
}

function writeDraft(threadId: string | null, text: string, files: ContextFile[]): void {
  try {
    if (!text.trim() && files.length === 0) {
      localStorage.removeItem(draftKey(threadId))
      return
    }
    localStorage.setItem(draftKey(threadId), JSON.stringify({ text, files }))
  } catch {
    // 本地存储不可用时草稿只在当前页面内保留
  }
}

function clearDraft(threadId: string | null): void {
  try {
    localStorage.removeItem(draftKey(threadId))
  } catch {
    // 忽略存储异常
  }
}

function persistDraftNow(): void {
  if (draftLocked) return
  // 离线演示没有真实会话，草稿统一存到“未归属”槽，下次启动仍可回填。
  const threadId = chat.demoMode ? null : chat.threadId
  writeDraft(threadId, draft.value, [...pendingFiles.value])
}

function readLastThread(): string | null {
  try {
    return localStorage.getItem(LAST_THREAD_KEY)
  } catch {
    return null
  }
}

/** 清空输入框与待发附件，只释放本地预览，不删除服务端文件。 */
function resetInput(): void {
  for (const url of Object.values(previews.value)) URL.revokeObjectURL(url)
  draft.value = ''
  pendingFiles.value = []
  previews.value = {}
  uploadError.value = ''
}

/** 恢复指定会话的草稿；附件按 ID 校验，失效的只提示、不阻塞恢复。 */
async function restoreDraft(threadId: string | null): Promise<void> {
  draftLocked = true
  try {
    const record = readDraft(threadId)
    draft.value = record?.text ?? ''
    pendingFiles.value = []
    previews.value = {}
    uploadError.value = ''
    if (record && record.files.length) {
      try {
        const checked = await resolveContextFiles(
          record.files.map((file) => file.id),
          USER_ID,
        )
        pendingFiles.value = checked.files
        const expired = record.files.filter((file) => checked.missing.includes(file.id))
        if (expired.length) {
          uploadError.value = `以下附件已过期，未恢复：${expired
            .map((file) => file.name)
            .join('、')}`
        }
        if (checked.missing.length) writeDraft(threadId, draft.value, checked.files)
      } catch {
        // 后端暂不可用：先展示本地记录，发送时再由后端校验
        pendingFiles.value = record.files
      }
    }
  } finally {
    draftLocked = false
  }
}

/** 新建会话前把当前输入存到原会话；附件保留在服务端供以后恢复。 */
function stashCurrentDraft(): void {
  if (draftTimer) window.clearTimeout(draftTimer)
  // 输入为空时不要写盘：否则会把“未归属”草稿或已恢复的草稿误删。
  if (!draft.value.trim() && pendingFiles.value.length === 0) return
  persistDraftNow()
}

/** 新建会话后清理不属于任何会话的草稿及其附件，避免服务端残留。 */
function discardUnownedDraft(): void {
  const orphan = readDraft(null)
  orphan?.files.forEach((file) => void deleteContextFile(file.id, USER_ID))
  clearDraft(null)
}

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
  void agents.load()
  void agents.loadGroups()
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
    // 进入对话页时做一次自动归档检查，不在后台常驻计时器。
    await sessions.autoArchive()
    // 归档超过保留天数的会话在这里真正删除（保留天数与归档配置一致）。
    const purged = await sessions.autoPurge()
    if (purged > 0) {
      ElMessage.info(`已自动清除 ${purged} 个超过保留期的归档会话`)
    }
    // 启动恢复需要先拿到会话列表，才能校验上次的会话是否仍然存在。
    await sessions.refresh()
    void sessions.refreshArchived()
    formats.value = await fetchContextFormats()
    void settings.load()
    void loadGlobalModel()
    void fetchFeedbackSettings()
      .then((config) => {
        feedbackEnabled.value = config.enabled
      })
      .catch(() => undefined)
    void fetchSandboxStatus()
      .then((status) => {
        if (!status.configured) sandboxNotice.value = status.notice
      })
      .catch(() => undefined)
  }
  // 优先恢复“从工作台最近对话进入”的会话，其次恢复上次关闭程序时的会话。
  if (!chat.threadId) {
    const target = sessions.activeThreadId || readLastThread()
    const known = target && sessions.sessions.some((item) => item.thread_id === target)
    // 会话列表加载失败（为空）时仍尝试恢复上次会话，历史接口可能依然可用。
    if (backendOnline.value && target && (known || sessions.sessions.length === 0)) {
      await selectSession(target)
    } else {
      // 没有可恢复的会话时，回填尚未创建会话的草稿。
      await restoreDraft(null)
    }
  } else if (!chat.demoMode) {
    // 同一页面重新挂载时（例如从设置页返回），沿用当前会话并恢复草稿。
    await selectSession(chat.threadId)
  } else {
    await restoreDraft(null)
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
    ElMessage.warning('链接需要以 http:// 或 https:// 开头')
    return
  }
  if (pendingFiles.value.length >= formats.value.max_files) {
    ElMessage.warning(`一次最多上传 ${formats.value.max_files} 个文件`)
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
    ElMessage.error((error as Error).message)
  } finally {
    urlLoading.value = false
  }
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
      ElMessage.warning(`一次最多上传 ${formats.value.max_files} 个文件`)
      break
    }
    if (file.size > limit) {
      ElMessage.warning(`${file.name} 超过 ${formats.value.max_file_mb} MB`)
      continue
    }
    const ext = `.${(file.name.split('.').pop() || '').toLowerCase()}`
    if (allowed.length && !allowed.includes(ext)) {
      ElMessage.warning(`不支持的文件类型 ${ext}`)
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
      ElMessage.error((error as Error).message)
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
  feedback?: 'like' | 'dislike' | null
  created_at?: string
}

/** 历史消息自带 created_at，转成毫秒供气泡底部显示时间。 */
function messageTime(value?: string): number | undefined {
  const parsed = Date.parse(value || '')
  return Number.isFinite(parsed) ? parsed : undefined
}

/** 气泡底部时间只显示时:分，完整时间放在 title 里。 */
function clockOf(value?: number): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function timeTitle(value?: number): string {
  return value ? new Date(value).toLocaleString() : ''
}

async function selectSession(threadId: string) {
  // 先把当前输入存回原会话，再切换；附件继续留在服务端等待恢复。
  stashCurrentDraft()
  draftLocked = true
  resetInput()
  sessions.activeThreadId = threadId
  chat.activateThread(threadId)
  // 会话绑定的 Agent 回填到选择器，避免跨会话串用员工。
  const bound = sessions.sessions.find((item) => item.thread_id === threadId)
  chat.selectedAgentId = bound?.agent_id || agents.defaultAgentId || 'default'
  // 组随会话恢复；该会话没绑定过组就保持「无组」，不自动推断。
  chat.selectedGroupId = bound?.group_id || ''
  syncGroupForAgent()
  // 记录切换前的文件基线，下一轮结束时才能准确判断“本轮新增”。
  void files.prime(threadId)
  // 后台仍在运行的会话：保留实时缓冲，不能用 checkpoint 历史覆盖
  try {
    if (!chat.isThreadSending(threadId)) {
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
          spent?: number
          rounds?: number
          context_window?: number
          context_used?: number
        }
      }
      const msgs: ChatMsg[] = data.messages.map((m, i) => {
        const at = messageTime(m.created_at)
        if (m.role === 'user') return { id: `h-u-${i}`, kind: 'user', content: m.content, at }
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
            spent: data.session_usage.spent,
            rounds: data.session_usage.rounds,
            contextWindow: data.session_usage.context_window,
            contextUsed: data.session_usage.context_used,
          }
        : null
      chat.loadHistory(msgs, threadId, usage)
      void hydrateTurnDurations(threadId, msgs)
      stickToBottom.value = true
      void scrollBottom()
    }
  } catch {
    chat.activateThread(threadId)
  } finally {
    await restoreDraft(threadId)
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
  const previousThread = chat.threadId
  // 新建会话前保留原会话草稿；新建后输入框从空白开始。
  stashCurrentDraft()
  draftLocked = true
  resetInput()
  // 新会话默认不选组：组只在用户显式选择后才写回该会话。
  chat.selectedGroupId = ''
  const id = await sessions.createSession(
    undefined,
    undefined,
    chat.selectedAgentId || agents.defaultAgentId,
    undefined,
  )
  if (!id) {
    sessions.activeThreadId = null
    chat.newChat()
    draftLocked = false
    return
  }
  sessions.activeThreadId = id
  chat.activateThread(id)
  void files.prime(id)
  if (!previousThread) discardUnownedDraft()
  draftLocked = false
  // 新建后直接进入该会话的聊天界面，并把焦点放回输入框。
  stickToBottom.value = true
  await nextTick()
  void scrollBottom()
  inputEl.value?.focus()
}

/** 在集成的项目下新建会话：会话绑定该后端 MCP，工具范围由后端隔离。 */
async function createIntegrationSession(serverId: string) {
  const previousThread = chat.threadId
  stashCurrentDraft()
  draftLocked = true
  resetInput()
  const id = await sessions.createSession(null, serverId)
  draftLocked = false
  if (!id) return
  sessions.activeThreadId = id
  chat.activateThread(id)
  void files.prime(id)
  if (!previousThread) discardUnownedDraft()
  stickToBottom.value = true
  await nextTick()
  void scrollBottom()
  inputEl.value?.focus()
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

/** 清理某个会话在本地的前端状态（草稿、实时缓冲、文件域）。 */
function forgetSession(threadId: string) {
  // 删除正在编辑的会话时，连带放弃未发送内容和附件。
  if (chat.threadId === threadId) {
    if (draftTimer) window.clearTimeout(draftTimer)
    draftLocked = true
    for (const file of pendingFiles.value) void deleteContextFile(file.id, USER_ID)
    resetInput()
    draftLocked = false
  }
  // 已存草稿的附件也要一并清理，避免服务端残留无引用文件。
  const stored = readDraft(threadId)
  stored?.files.forEach((file) => void deleteContextFile(file.id, USER_ID))
  clearDraft(threadId)
  chat.releaseThread(threadId)
  files.forget(threadId)
  if (sessions.activeThreadId === threadId) {
    sessions.activeThreadId = null
  }
}

async function removeSession(threadId: string) {
  const title = sessions.sessions.find((item) => item.thread_id === threadId)?.title
  try {
    await ElMessageBox.confirm(
      `删除后，「${title || '该会话'}」的消息记录、轨迹与文件关联将一并移除，且无法恢复。`,
      '删除这个会话？',
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
  } catch {
    return
  }
  forgetSession(threadId)
  await sessions.removeSession(threadId)
}

async function renameSession(threadId: string, title: string) {
  await sessions.renameSession(threadId, title)
}

async function togglePin(threadId: string, pinned: boolean) {
  await sessions.setPinned(threadId, pinned)
}

/** 从归档管理弹窗打开只读会话。 */
async function openArchivedSession(threadId: string) {
  await selectSession(threadId)
}

/** 当前只读的归档会话恢复回侧边栏，输入框随之恢复。 */
async function restoreActiveSession() {
  const threadId = chat.threadId
  if (!threadId) return
  await sessions.setArchived(threadId, false)
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
  taskflow: ChatMsg | null
  // 执行中的任务规划卡固定在顶部；结束后的卡片归档到本轮轨迹底部
  scheduler: ChatMsg | null
  schedulers: ChatMsg[]
  final: ChatMsg | null
  interrupt: ChatMsg | null
  notes: ChatMsg[]
  // 本轮终止原因提示，固定在最终回答下方
  terminated: ChatMsg | null
  live: boolean
}

const turns = computed<TurnView[]>(() => {
  const result: TurnView[] = []
  let current: TurnView | null = null

  const flush = () => {
    if (!current) return
    // 自检修正会在同一轮产生第二版最终回答。两版回答之间没有工具调用时，
    // 前一条助手消息视为草稿，不展示，避免「答案先出现又被退回去」。
    if (current.process.length > 1) {
      const superseded = new Set<string>()
      let lastAssistantId: string | null = null
      let toolSince = false
      for (const msg of current.process) {
        if (msg.kind === 'tool' || msg.kind === 'interrupt') toolSince = true
        if (msg.kind === 'assistant' && msg.content) {
          if (lastAssistantId && !toolSince) superseded.add(lastAssistantId)
          lastAssistantId = msg.id
          toolSince = false
        }
      }
      if (superseded.size) {
        current.process = current.process.filter((msg) => !superseded.has(msg.id))
      }
    }
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
        taskflow: null,
        scheduler: null,
        schedulers: [],
        final: null,
        interrupt: null,
        notes: [],
        terminated: null,
        live: false,
      }
      continue
    }
    if (!current) {
      current = {
        key: `pre-${msg.id}`,
        user: null,
        process: [],
        taskflow: null,
        scheduler: null,
        schedulers: [],
        final: null,
        interrupt: null,
        notes: [],
        terminated: null,
        live: false,
      }
    }
    if (msg.kind === 'terminated') {
      current.terminated = msg
      continue
    }
    if (msg.kind === 'taskflow') {
      current.taskflow = msg
      continue
    }
    if (msg.kind === 'scheduler') {
      // 运行中固定在顶部；完成/中断后归档到本轮轨迹最底部。
      if (msg.schedStatus === 'running') current.scheduler = msg
      else current.schedulers.push(msg)
      continue
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

// ---------- 终止原因提示 ----------
const TERMINATE_TEXT: Record<TerminateReason, string> = {
  user: '你已手动终止本次回答',
  network: '连接中断，回答未完成',
  server: '服务端出错，回答已终止',
  limit: '达到模型调用上限，回答提前结束',
}

const TERMINATE_ICON: Record<TerminateReason, IconName> = {
  user: 'stop',
  network: 'globe',
  server: 'server',
  limit: 'timer',
}

function terminateText(msg: ChatMsg): string {
  const reason = msg.termReason || 'server'
  const base = TERMINATE_TEXT[reason]
  // 手动终止不需要附加细节；其余原因把具体错误并进同一行，避免再插一条提示。
  return msg.termDetail && reason !== 'user' ? `${base}：${msg.termDetail}` : base
}

function terminateIcon(msg: ChatMsg): IconName {
  return TERMINATE_ICON[msg.termReason || 'server']
}

// ---------- 任务流进度卡片 ----------
// 只认「最后一条任务流消息且仍在运行」：本轮一结束（完成/失败/中断）就收起，
// 撤回后由 store 的快照把未完成的卡片重新挂回来。
const activeFlow = computed<ChatMsg | null>(() => {
  const list = chat.messages
  for (let index = list.length - 1; index >= 0; index -= 1) {
    const message = list[index]
    if (message?.kind !== 'taskflow') continue
    return message.flowStatus === 'running' ? message : null
  }
  return null
})

// 任务规划卡片与任务流一致：只认最后一条规划消息且仍在执行。
// 中断/完成后自动离开顶部，归档进本轮轨迹；恢复执行时重新回到顶部。
const activeScheduler = computed<ChatMsg | null>(() => {
  const list = chat.messages
  for (let index = list.length - 1; index >= 0; index -= 1) {
    const message = list[index]
    if (message?.kind !== 'scheduler') continue
    return message.schedStatus === 'running' ? message : null
  }
  return null
})

// ---------- 右侧轮次导航 ----------
// 只给包含用户消息的轮次生成刻度：点击跳转，悬停预览该轮提问。
interface TurnNavItem {
  key: string
  label: string
}

const turnNav = computed<TurnNavItem[]>(() =>
  turns.value
    .filter((turn) => Boolean(turn.user))
    .map((turn) => ({
      key: turn.key,
      label: (turn.user?.content || '').replace(/\s+/g, ' ').trim() || '（附件消息）',
    })),
)

function navAnchor(key: string): string {
  return `data-turn-id="${String(key).replace(/"/g, '')}"`
}

/** 高亮当前视口顶部所在的轮次；与刻度共用一个索引空间。 */
function updateNavActive() {
  const el = scroller.value
  if (!el) return
  const items = turnNav.value
  if (!items.length) {
    navActive.value = 0
    return
  }
  const indexByKey = new Map(items.map((item, index) => [item.key, index]))
  const base = el.getBoundingClientRect().top
  let active = 0
  for (const node of el.querySelectorAll<HTMLElement>('[data-turn-id]')) {
    const index = indexByKey.get(node.dataset.turnId || '')
    if (index === undefined) continue
    // 顶部留 48px 容差：轮次标题刚滚到视口上沿时就算已切入该轮。
    if (node.getBoundingClientRect().top - base <= 48) active = index
  }
  navActive.value = active
}

/** 跳转到第 index 个轮次；保持用户阅读位置，不触发自动跟随到底部。 */
function jumpToTurn(index: number) {
  const el = scroller.value
  const item = turnNav.value[index]
  if (!el || !item) return
  const node = el.querySelector<HTMLElement>(`[${navAnchor(item.key)}]`)
  if (!node) return
  const top =
    node.getBoundingClientRect().top - el.getBoundingClientRect().top + el.scrollTop - 12
  stickToBottom.value = false
  navActive.value = index
  el.scrollTo({ top: Math.max(0, top), behavior: 'smooth' })
}

/** 指针停在导航条上滚轮时，把滚动转交给消息区；展开态下让面板自己滚。 */
function onNavWheel(event: WheelEvent) {
  if (navOpen.value) return
  const el = scroller.value
  if (!el) return
  event.preventDefault()
  el.scrollTop += event.deltaY
}

/** 展开后把当前轮次滚进面板可视区，避免长会话里看不到高亮项。 */
async function onNavOpenChange(open: boolean) {
  if (!open) return
  await nextTick()
  const list = navList.value
  if (!list) return
  const row = list.querySelector<HTMLElement>(`[data-nav-index="${navActive.value}"]`)
  if (!row) return
  const listRect = list.getBoundingClientRect()
  const rowRect = row.getBoundingClientRect()
  if (rowRect.top < listRect.top) list.scrollTop -= listRect.top - rowRect.top + 8
  else if (rowRect.bottom > listRect.bottom) list.scrollTop += rowRect.bottom - listRect.bottom + 8
}

watch(navOpen, (open) => void onNavOpenChange(open))

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

// 归档会话只读：仍可回看聊天记录，但不再向 Agent 发送消息。
const archivedActive = computed(() =>
  sessions.isArchived(chat.threadId || sessions.activeThreadId),
)

// ---------- 撤回最新用户消息 ----------
function canWithdraw(turn: TurnView, index: number): boolean {
  if (!turn.user || archivedActive.value || chat.sending || chat.demoMode) {
    return false
  }
  // 只能撤回最新一条；撤回后上一条自然成为最新，可继续逐条撤回。
  return index === turns.value.length - 1
}

async function withdrawLatest(turn: TurnView, index: number) {
  if (!canWithdraw(turn, index) || !turn.user) return
  try {
    await ElMessageBox.confirm(
      '撤回后，这条消息及其之后的 AI 回复会从会话中移除。已产生的 Token 计费和已写入磁盘或沙箱的文件不会回滚。',
      '撤回这条消息？',
      {
        confirmButtonText: '撤回',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
  } catch {
    return
  }
  try {
    const threadId = chat.threadId || sessions.activeThreadId
    if (!threadId) throw new Error('会话不存在')
    // 带上这一轮的原文：后端据此确认 head 的最新用户消息就是它，
    // 避免把没写入检查点的出错轮误撤成上一轮。
    const result = await withdrawLatestMessage(
      threadId,
      USER_ID,
      '控制台用户',
      turn.user.sentText ?? turn.user.content,
    )
    // 重新拉取历史，确保消息、中断与用量都以后端 head 为准。
    await selectSession(threadId)
    // 撤回后如果上一轮的任务流还没走完，把进度卡片重新挂回消息流。
    chat.restoreFlowCard(threadId)
    sessionUsageOverride.value = null
    void hydrateSessionUsage(threadId)
    const withdrawn = result.content || turn.user.content || ''
    // 撤回内容放回输入框；若原本还有未发送草稿，保留在后面。
    const restored = draft.value.trim()
    draft.value = restored ? `${withdrawn}\n\n${restored}` : withdrawn
    await nextTick()
    inputEl.value?.focus()
    void sessions.refresh()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '撤回失败')
  }
}

// ---------- 异常终止后重试 ----------
// 只对最后一轮生效：重试 = 用原提问（含附件）重新发起一轮，不撤回出错轮。
function canRetryTerminated(turn: TurnView, index: number): boolean {
  const term = turn.terminated
  if (!term || term.termReason === 'user') return false
  if (!turn.user || archivedActive.value || chat.sending || chat.demoMode) return false
  return index === turns.value.length - 1
}

function retryTerminated(turn: TurnView) {
  if (!turn.user || !canRetryTerminated(turn, turns.value.length - 1)) return
  chat.send(turn.user.content || '', turn.user.attachments || [])
  stickToBottom.value = true
  void scrollBottom()
}

// ---------- 会话 / 本轮 Token 统计 ----------
/** 兼容旧前端状态：本地缺少 spent 时，从后端补拉一次会话累计。 */
const sessionUsageOverride = ref<SessionUsage | null>(null)
const sessionUsage = computed(() => {
  const raw = sessionUsageOverride.value
  if (!raw) return chat.sessionTokens
  return {
    input: raw.input || 0,
    output: raw.output || 0,
    total: raw.total || 0,
    spent: raw.spent,
    rounds: raw.rounds || 0,
    contextWindow: raw.context_window || 0,
    contextUsed: raw.context_used ?? raw.input ?? 0,
  }
})

async function hydrateSessionUsage(threadId: string | null) {
  if (!threadId || chat.demoMode) return
  if (chat.sessionTokens?.spent != null) return
  try {
    const usage = await fetchSessionUsage(threadId, USER_ID)
    if (usage && typeof usage.spent === 'number') {
      sessionUsageOverride.value = usage
    }
  } catch {
    // 补拉失败时保留现有统计，不影响对话。
  }
}

watch(
  () => [chat.threadId, chat.turnVersion] as const,
  () => {
    // 每轮结束后以后端为准；旧 store 缺少 spent 时立即补拉。
    sessionUsageOverride.value = null
    void hydrateSessionUsage(chat.threadId)
  },
  { immediate: true },
)

/** 流式期间没有逐 token 用量，用字符数估算；done 后以真实 usage 校正。 */
function estimateTokens(text: string): number {
  if (!text) return 0
  let cjk = 0
  let other = 0
  for (const char of text) {
    const code = char.codePointAt(0) || 0
    const isCjk =
      (code >= 0x4e00 && code <= 0x9fff) ||
      (code >= 0x3000 && code <= 0x30ff) ||
      (code >= 0xff00 && code <= 0xffef)
    if (isCjk) cjk += 1
    else other += 1
  }
  return Math.ceil(cjk * 0.65 + other / 4)
}

const currentTurn = computed(() => turns.value[turns.value.length - 1] || null)
const currentTurnTokens = computed(() => currentTurn.value?.final?.tokens || null)
const streamingAnswerTokens = computed(() => {
  const final = currentTurn.value?.final
  if (!chat.sending || !final?.streaming) return 0
  return estimateTokens(final.content || '')
})
const turnEstimating = computed(
  () => Boolean(chat.sending && !currentTurnTokens.value),
)
// 本轮输入：完成后取真实 usage；流式中用上一轮上下文占用估算。
const turnInput = computed(() => {
  if (currentTurnTokens.value) return currentTurnTokens.value.input || 0
  return sessionUsage.value?.contextUsed || sessionUsage.value?.input || 0
})
// 本轮输出：流式中按字符估算，完成后取真实 usage。
const turnOutput = computed(() => {
  if (currentTurnTokens.value) return currentTurnTokens.value.output || 0
  return streamingAnswerTokens.value
})
// 本会话累计 = 历轮真实计费 + 本轮尚未落账的估算部分。
const sessionSpentMissing = computed(() => sessionUsage.value?.spent == null)
const sessionSpent = computed(() => {
  const base = sessionUsage.value?.spent ?? 0
  if (!chat.sending) return base
  const pendingInput = currentTurnTokens.value ? 0 : turnInput.value
  return base + pendingInput + streamingAnswerTokens.value
})
const usageEstimating = computed(() => Boolean(chat.sending))

async function scrollBottom() {
  await nextTick()
  const el = scroller.value
  if (!el) return
  el.scrollTop = el.scrollHeight
  stickToBottom.value = true
  updateOverflow()
  updateNavActive()
}

function updateOverflow() {
  const el = scroller.value
  hasOverflow.value = Boolean(el && el.scrollHeight - el.clientHeight > 24)
}

// 用户一旦向上滚动，就停止自动跟随；是否回到底部由按钮决定。
/** 视口顶部正在阅读的轨迹节点；滚动跨过节点边界时自动切换。 */
const pinnedTraj = ref<{ id: string; level: number; label: string; open: boolean } | null>(
  null,
)

let pinFrame = 0

function schedulePinnedTrajUpdate() {
  if (pinFrame) return
  pinFrame = requestAnimationFrame(() => {
    pinFrame = 0
    updatePinnedTrajectory()
  })
}

/**
 * 找出滚动视口内最顶部的轨迹节点行。
 * 子节点在 DOM 中紧随父节点，因此顺流行遍历后「最后一个越过顶部」的行
 * 就是当前正在看的节点块；父 / 子节点滚到顶部时会自然切换。
 */
function updatePinnedTrajectory() {
  const el = scroller.value
  if (!el) {
    pinnedTraj.value = null
    return
  }
  const top = el.getBoundingClientRect().top
  let currentId = ''
  for (const row of el.querySelectorAll<HTMLElement>('[data-traj-row]')) {
    const rect = row.getBoundingClientRect()
    if (rect.bottom <= top + 1) continue
    if (rect.top <= top + 2) currentId = row.dataset.trajRow || ''
  }
  const entry = currentId ? trajectoryRegistry.get(currentId) : undefined
  // 只粘可折叠节点：收起操作才有意义。
  if (!entry || !entry.collapsible()) {
    pinnedTraj.value = null
    return
  }
  pinnedTraj.value = {
    id: currentId,
    level: entry.level(),
    label: entry.label(),
    open: entry.open(),
  }
}

function togglePinnedTraj() {
  const target = pinnedTraj.value
  if (!target) return
  trajectoryRegistry.get(target.id)?.toggle()
  void nextTick(updatePinnedTrajectory)
}

function onMessagesScroll() {
  const el = scroller.value
  if (!el) return
  updateOverflow()
  const distance = el.scrollHeight - el.scrollTop - el.clientHeight
  stickToBottom.value = distance < 48
  updateNavActive()
  schedulePinnedTrajUpdate()
}

function jumpToBottom() {
  void scrollBottom()
}

// 输入或附件变化后延迟落盘，避免每次按键都写 localStorage。
watch(
  [draft, pendingFiles],
  () => {
    if (draftTimer) window.clearTimeout(draftTimer)
    draftTimer = window.setTimeout(persistDraftNow, DRAFT_SAVE_DELAY)
  },
  { deep: true },
)

// 轮次数量或内容变化后重算导航高亮，避免新增消息后刻度索引与视口错位。
watch(
  () => turnNav.value.length,
  () => {
    void nextTick(updateNavActive)
  },
)

// 会话切换或轨迹节点增删后刷新粘性折叠条，避免残留上一个会话的节点。
watch(
  () => [chat.threadId, chat.messages.length] as const,
  () => {
    pinnedTraj.value = null
    void nextTick(updatePinnedTrajectory)
  },
)

// 记住最后打开的会话，供下次启动恢复。
watch(
  () => sessions.activeThreadId,
  (threadId) => {
    try {
      if (threadId) localStorage.setItem(LAST_THREAD_KEY, threadId)
      else localStorage.removeItem(LAST_THREAD_KEY)
    } catch {
      // 忽略存储异常
    }
  },
)

/** 关闭程序/隐藏页面时立即落盘，避免防抖窗口内的输入丢失。 */
function flushDraft() {
  if (draftTimer) window.clearTimeout(draftTimer)
  persistDraftNow()
}

function onVisibilityChange() {
  if (document.visibilityState === 'hidden') flushDraft()
}

window.addEventListener('beforeunload', flushDraft)
document.addEventListener('visibilitychange', onVisibilityChange)

onBeforeUnmount(() => {
  flushDraft()
  window.removeEventListener('beforeunload', flushDraft)
  document.removeEventListener('visibilitychange', onVisibilityChange)
  if (pinFrame) cancelAnimationFrame(pinFrame)
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
// 当前生效模型：Agent 独立配置 > 组模型 > 系统全局模型，与后端 resolve_model 一致。
const globalModel = ref<GlobalModelSettings | null>(null)
const agentProfileModel = computed(
  () => agents.byId(chat.selectedAgentId)?.model || null,
)
const selectedGroup = computed(
  () => agents.groups.find((group) => group.group_id === chat.selectedGroupId) || null,
)
const groupProfileModel = computed(() => selectedGroup.value?.model || null)
const ctxModelName = computed(
  () =>
    agentProfileModel.value?.model_name ||
    groupProfileModel.value?.model_name ||
    globalModel.value?.model_name ||
    '',
)
const ctxModelSource = computed(() => {
  const agentModel = agentProfileModel.value
  if (agentModel?.model_name || agentModel?.context_limit) return 'Agent 独立配置'
  const groupModel = groupProfileModel.value
  if (groupModel?.model_name || groupModel?.context_limit) {
    return `组模型 · ${selectedGroup.value?.name || selectedGroup.value?.group_id || ''}`.trim()
  }
  return '系统全局'
})
const agentContextLimit = computed(() =>
  Number(
    agentProfileModel.value?.context_limit ||
      groupProfileModel.value?.context_limit ||
      globalModel.value?.context_limit ||
      0,
  ),
)
const ctxWindow = computed(() => {
  if (agentContextLimit.value > 0) return agentContextLimit.value
  return sessionUsage.value?.contextWindow || 0
})
const ctxUsed = computed(
  () => sessionUsage.value?.contextUsed ?? sessionUsage.value?.input ?? 0,
)
const ctxPct = computed(() =>
  ctxWindow.value > 0
    ? Math.min(100, Math.round((ctxUsed.value / ctxWindow.value) * 100))
    : 0,
)
// 阈值与后端软/硬水位对齐：0.70B / 0.88B 相对原始窗口约为 62% / 78%。
const ctxTone = computed(() =>
  ctxPct.value >= 78 ? 'danger' : ctxPct.value >= 62 ? 'warn' : 'ok',
)
const ctxDash = computed(
  () => `${(ctxPct.value / 100) * DONUT_CIRC} ${DONUT_CIRC}`,
)

async function loadGlobalModel() {
  try {
    globalModel.value = await fetchGlobalModelSettings()
  } catch {
    globalModel.value = null
  }
}

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

// ---------- 赞同 / 不赞同 / 重试 ----------
const feedbackBusy = ref<Record<string, boolean>>({})
const RETRY_HINT_LIMIT = 3
const retryHintShown = new Set<string>()

function toolsOf(turn: TurnView): string[] {
  return turn.process
    .filter((message) => message.kind === 'tool' && message.toolName)
    .map((message) => String(message.toolName))
    .filter((name, index, list) => list.indexOf(name) === index)
    .slice(0, 8)
}

async function toggleFeedback(turn: TurnView, action: 'like' | 'dislike') {
  const final = turn.final
  if (!final || final.streaming || archivedActive.value || chat.demoMode) return
  if (!feedbackEnabled.value) return
  if (feedbackBusy.value[final.id]) return
  feedbackBusy.value = { ...feedbackBusy.value, [final.id]: true }
  try {
    await chat.sendFeedback(final, action, {
      userId: USER_ID,
      userExcerpt: turn.user?.content || '',
      tools: toolsOf(turn),
    })
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '反馈提交失败')
  } finally {
    const next = { ...feedbackBusy.value }
    delete next[final.id]
    feedbackBusy.value = next
  }
}

function canRetry(turn: TurnView, index: number): boolean {
  if (!feedbackEnabled.value || archivedActive.value || chat.sending || chat.demoMode) return false
  if (!turn.final || turn.final.streaming || turn.interrupt) return false
  // 只允许最后一条最终回答：历史回答重试会丢弃其后的轮次。
  return index === turns.value.length - 1
}

function handleRetry(turn: TurnView) {
  const final = turn.final
  if (!final) return
  if (!chat.retryLast(final)) return
  const turnKey = turn.user?.id || ''
  if (
    turnKey &&
    chat.retryCountFor(turnKey) >= RETRY_HINT_LIMIT &&
    !retryHintShown.has(turnKey)
  ) {
    retryHintShown.add(turnKey)
    chat.addNote('如果还是不对，直接说希望我在哪方面调整：更短、更细、换个角度都可以。')
  }
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
    if (chat.roundLimit > 0 && rounds >= chat.roundLimit) {
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

/** 空状态快捷提问：复用 submit，保证建会话/绑定/清草稿逻辑一致。 */
function sendSuggestion(text: string) {
  draft.value = text
  void submit()
}

async function submit() {
  if (archivedActive.value) return
  // 流式期间输入框已禁用，这里再兜一层，避免重复 Enter 造成重复入队。
  if (chat.sending) return
  if (!draft.value.trim() && pendingFiles.value.length === 0) return
  const previousThread = chat.threadId
  // 没有会话时先建一个，确保 SSE 请求始终带着确定的 thread_id
  if (!chat.threadId && !chat.demoMode) {
    // 新会话默认不绑定组；Agent 跟随当前选择，避免首轮落到默认 Agent。
    const id = await sessions.createSession(
      undefined,
      undefined,
      chat.selectedAgentId || agents.defaultAgentId,
      undefined,
    )
    if (!id) {
      ElMessage.error('无法创建会话，请检查后端连接')
      return
    }
    sessions.activeThreadId = id
    chat.activateThread(id)
  }
  // 先快照再清空：清空必须发生在发送之前，任何后续异常都不能让草稿残留。
  const text = draft.value
  const files = [...pendingFiles.value]
  // 已发送的草稿立即作废；之前没有会话时，同时清掉“未归属”草稿。
  clearDraft(chat.threadId)
  if (!previousThread) clearDraft(null)
  draft.value = ''
  // 已发送的附件不再重复引用；预览 URL 保留给消息气泡显示。
  pendingFiles.value = []
  uploadError.value = ''
  chat.send(text, files)
  // 把本轮选择同步到本地会话索引，切走再切回时能恢复同一个 Agent / 组。
  // 这只是内存副本，失败不影响本轮发送（后端已持久化绑定）。
  try {
    if (chat.threadId) {
      sessions.bindRouting(chat.threadId, chat.selectedAgentId, chat.selectedGroupId)
    }
  } catch {
    // 忽略本地索引同步异常
  }
  stickToBottom.value = true
  void nextTick(() => {
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
          :limit="chat.roundLimit > 0 ? chat.roundLimit : 0"
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
        @create-integration="createIntegrationSession"
        @open-archived="openArchivedSession"
        @archived-removed="forgetSession"
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
            <el-select
              v-model="chat.selectedAgentId"
              class="agent-select holo-select holo-select--agent"
              popper-class="holo-popper"
              size="small"
              placeholder="选择 Agent"
              :disabled="archivedActive"
            >
              <el-option
                v-for="opt in agentOptions"
                :key="opt.value"
                :label="opt.label"
                :value="opt.value"
              >
                <div class="agent-option">
                  <span
                    class="agent-option-avatar"
                    :style="{ background: avatarGradient(opt.value) }"
                  >
                    {{ avatarInitial(opt.label, opt.avatar) }}
                  </span>
                  <span class="agent-option-main">
                    <strong>{{ opt.label }}</strong>
                    <small>{{ opt.role }}</small>
                  </span>
                  <span v-if="opt.groups.length" class="agent-option-groups">
                    <HoloTooltip
                      v-for="group in opt.groups"
                      :key="group.group_id"
                      :title="group.name"
                      :content="`角色：${group.role === 'leader' ? '组长' : '组员'}`"
                      placement="left"
                      :width="200"
                    >
                      <span
                        class="group-badge"
                        :style="{ background: avatarGradient(group.group_id) }"
                      >
                        {{ avatarInitial(group.name) }}
                        <i v-if="group.role === 'leader'" class="group-badge-flag">
                          <el-icon><Flag /></el-icon>
                        </i>
                      </span>
                    </HoloTooltip>
                  </span>
                </div>
              </el-option>
            </el-select>
            <el-select
              v-if="groupOptions.length"
              v-model="chat.selectedGroupId"
              class="agent-select holo-select holo-select--group"
              popper-class="holo-popper"
              size="small"
              placeholder="独立执行（不使用组）"
              :disabled="archivedActive"
            >
              <el-option label="独立执行（不使用组）" value="">
                <div class="agent-option">
                  <span class="agent-option-avatar agent-option-avatar--none">—</span>
                  <span class="agent-option-main">
                    <strong>独立执行</strong>
                    <small>不使用组</small>
                  </span>
                </div>
              </el-option>
              <el-option
                v-for="opt in groupOptions"
                :key="opt.value"
                :label="opt.label"
                :value="opt.value"
              >
                <div class="agent-option">
                  <HoloTooltip
                    :title="opt.name"
                    :content="`角色：${opt.role}`"
                    placement="left"
                    :width="200"
                  >
                    <span
                      class="group-badge group-badge--lg"
                      :style="{ background: avatarGradient(opt.value) }"
                    >
                      {{ avatarInitial(opt.name) }}
                      <i v-if="opt.isLeader" class="group-badge-flag">
                        <el-icon><Flag /></el-icon>
                      </i>
                    </span>
                  </HoloTooltip>
                  <span class="agent-option-main">
                    <strong>{{ opt.name }}</strong>
                    <small>{{ opt.role }}</small>
                  </span>
                </div>
              </el-option>
            </el-select>
            <span v-if="archivedActive" class="chip archived-chip">归档只读</span>
            <span v-if="chat.threadId" class="chip">thread {{ chat.threadId.slice(0, 8) }}…</span>
            <el-button size="small" :icon="Document" @click="showFiles = !showFiles">
              文件
            </el-button>
            <el-button size="small" :icon="Plus" @click="createSession()">
              新会话
            </el-button>
          </div>
        </header>

        <div v-if="sandboxNotice && !sandboxBannerClosed" class="sandbox-warn">
          <AppIcon name="shield" :size="13" />
          <span class="warn-text">{{ sandboxNotice }}</span>
          <el-button
            class="banner-close"
            :icon="Close"
            text
            circle
            size="small"
            title="关闭提示"
            @click="sandboxBannerClosed = true"
          />
        </div>

        <div v-if="integrityNotice && !integrityBannerClosed" class="sandbox-warn">
          <AppIcon name="shield" :size="13" />
          <span class="warn-text">{{ integrityNotice }}</span>
          <el-button
            class="banner-close"
            :icon="Close"
            text
            circle
            size="small"
            title="关闭提示"
            @click="integrityBannerClosed = true"
          />
        </div>

        <div class="messages-wrap">
          <!-- 任务规划：执行中固定在滚动区顶部，逐条流式展开；结束后归档进轨迹 -->
          <div v-if="activeScheduler" class="flow-dock">
            <SchedulerPlanCard
              :goal="activeScheduler.schedGoal || ''"
              :tier="activeScheduler.schedTier || ''"
              :mode="activeScheduler.schedMode || ''"
              :status="activeScheduler.schedStatus || 'running'"
              :items="activeScheduler.schedItems || []"
              :plan-version="activeScheduler.schedPlanVersion || 1"
              :summary="activeScheduler.schedSummary || ''"
              :note="activeScheduler.schedNote || ''"
              :checkpoint="activeScheduler.schedCheckpoint"
              :events="activeScheduler.schedEvents || []"
              :warnings="activeScheduler.schedWarnings || []"
              collapsible
            />
          </div>
          <!-- 任务流进度：固定在滚动区顶部，只显示当前运行中的那一条；本轮结束即消失 -->
          <div v-if="activeFlow" class="flow-dock">
            <ExecutionCard
              :flow-name="activeFlow.flowName || '任务流'"
              :status="activeFlow.flowStatus || 'running'"
              :current-step="activeFlow.flowCurrentStep || ''"
              :steps="activeFlow.flowSteps || []"
              :traces="activeFlow.flowTraces || []"
              collapsible
            />
          </div>
          <!-- 轨迹粘性折叠条：整条可点，箭头指示当前节点的展开状态 -->
          <button
            v-if="pinnedTraj"
            class="traj-pin"
            type="button"
            :style="{ paddingLeft: `${8 + Math.min(pinnedTraj.level - 1, 6) * 10}px` }"
            :title="pinnedTraj.open ? '收起该节点' : '展开该节点'"
            @click="togglePinnedTraj"
          >
            <span class="traj-pin-level">L{{ pinnedTraj.level }}</span>
            <span class="traj-pin-label">{{ pinnedTraj.label }}</span>
            <AppIcon
              name="chevronRight"
              :size="11"
              class="traj-pin-chev"
              :class="{ open: pinnedTraj.open }"
            />
          </button>
          <div
            ref="scroller"
            class="messages"
            :class="{ 'with-nav': turnNav.length > 1 }"
            @scroll.passive="onMessagesScroll"
          >
            <el-empty v-if="shown.length === 0" class="chat-empty">
              <template #image>
                <div class="empty-orb"><AppIcon name="sparkles" :size="26" /></div>
              </template>
              <template #description>
                <p class="empty-title">Harness 对话测试台</p>
                <p class="empty-desc">
                  发送一条消息，观察模型推理、工具调用、技能使用与人工介入的完整链路。
                </p>
              </template>
              <div class="suggestions">
                <el-button
                  v-for="s in SUGGESTIONS"
                  :key="s"
                  size="small"
                  @click="sendSuggestion(s)"
                >
                  {{ s }}
                </el-button>
              </div>
            </el-empty>

            <template v-for="(turn, turnIndex) in turns" :key="turn.key">
              <div v-if="turn.user" class="row user" :data-turn-id="turn.key">
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
                  <div
                    v-if="turn.user.content || canWithdraw(turn, turnIndex)"
                    class="msg-meta user"
                  >
                    <span
                      v-if="clockOf(turn.user.at)"
                      class="msg-time"
                      :title="timeTitle(turn.user.at)"
                    >
                      {{ clockOf(turn.user.at) }}
                    </span>
                    <el-button
                      v-if="turn.user.content"
                      class="copy-mini"
                      size="small"
                      text
                      :icon="copiedId === turn.user.id ? Check : CopyDocument"
                      :class="{ copied: copiedId === turn.user.id }"
                      :title="copiedId === turn.user.id ? '已复制' : '复制文本'"
                      @click="copyMessage(turn.user)"
                    >
                      {{ copiedId === turn.user.id ? '已复制' : '复制' }}
                    </el-button>
                    <button
                      v-if="canWithdraw(turn, turnIndex)"
                      class="meta-icon"
                      aria-label="撤回"
                      title="撤回这条消息"
                      @click="withdrawLatest(turn, turnIndex)"
                    >
                      <AppIcon name="undo" :size="14" />
                    </button>
                  </div>
                </div>
              </div>

              <!-- 任务流执行卡片已移到滚动区顶部常驻，这里不再内联渲染 -->

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

              <!-- 已归档的任务规划卡：固定在本轮轨迹最底部，恢复执行时回到顶部 -->
              <div
                v-for="card in turn.schedulers"
                :key="card.id"
                class="row indent tree-row"
              >
                <div class="tree-col">
                  <SchedulerPlanCard
                    :goal="card.schedGoal || ''"
                    :tier="card.schedTier || ''"
                    :mode="card.schedMode || ''"
                    :status="card.schedStatus || 'done'"
                    :items="card.schedItems || []"
                    :plan-version="card.schedPlanVersion || 1"
                    :summary="card.schedSummary || ''"
                    :note="card.schedNote || ''"
                    :checkpoint="card.schedCheckpoint"
                    :events="card.schedEvents || []"
                    :warnings="card.schedWarnings || []"
                    archived
                  />
                </div>
              </div>

              <div v-if="turn.final" class="row">
                <div class="avatar ai">AI</div>
                <div class="msg-col ai">
                  <div class="bubble ai-bubble">
                    <MarkdownView
                      v-if="turn.final.content"
                      :content="turn.final.content"
                      :thread-id="chat.threadId"
                      :streaming="Boolean(turn.final.streaming)"
                      @open-file="openSessionFile"
                    />
                    <span v-if="turn.final.streaming" class="cursor"></span>
                  </div>
                  <!-- 本轮产出：只渲染在最新一轮回答下方 -->
                  <ProducedFilesCard
                    v-if="
                      turnIndex === turns.length - 1 &&
                      !turn.final.streaming &&
                      turnArtifacts.length
                    "
                    :files="turnArtifacts"
                    @open-file="openSessionFile"
                  />
                  <div v-if="turn.final.content && !turn.final.streaming" class="msg-meta ai">
                    <span
                      v-if="clockOf(turn.final.at)"
                      class="msg-time"
                      :title="timeTitle(turn.final.at)"
                    >
                      {{ clockOf(turn.final.at) }}
                    </span>
                    <button
                      class="meta-icon"
                      :class="{ active: copiedId === turn.final.id }"
                      :title="copiedId === turn.final.id ? '已复制' : '复制'"
                      :aria-label="copiedId === turn.final.id ? '已复制' : '复制'"
                      @click="copyMessage(turn.final)"
                    >
                      <AppIcon :name="copiedId === turn.final.id ? 'check' : 'copy'" :size="14" />
                    </button>
                    <button
                      v-if="feedbackEnabled"
                      class="meta-icon"
                      :class="{ active: turn.final.feedback === 'like' }"
                      :disabled="archivedActive || chat.demoMode || feedbackBusy[turn.final.id]"
                      :aria-pressed="turn.final.feedback === 'like'"
                      aria-label="赞同"
                      title="赞同"
                      @click="toggleFeedback(turn, 'like')"
                    >
                      <AppIcon name="thumbUp" :size="14" />
                    </button>
                    <button
                      v-if="feedbackEnabled"
                      class="meta-icon"
                      :class="{ active: turn.final.feedback === 'dislike', dislike: true }"
                      :disabled="archivedActive || chat.demoMode || feedbackBusy[turn.final.id]"
                      :aria-pressed="turn.final.feedback === 'dislike'"
                      aria-label="不赞同"
                      title="不赞同"
                      @click="toggleFeedback(turn, 'dislike')"
                    >
                      <AppIcon name="thumbDown" :size="14" />
                    </button>
                    <button
                      v-if="canRetry(turn, turnIndex)"
                      class="meta-icon"
                      aria-label="重新回答"
                      title="重新回答"
                      @click="handleRetry(turn)"
                    >
                      <AppIcon name="refresh" :size="14" />
                    </button>
                  </div>
                </div>
              </div>

              <!-- 终止原因：贴在 AI 回答最底部的一行状态条 -->
              <div v-if="turn.terminated" class="row terminate-row">
                <div class="msg-col ai">
                  <div class="terminate-line">
                    <div class="terminate-bar" :class="turn.terminated.termReason || 'server'">
                      <AppIcon :name="terminateIcon(turn.terminated)" :size="12" />
                      <span>{{ terminateText(turn.terminated) }}</span>
                    </div>
                    <button
                      v-if="canRetryTerminated(turn, turnIndex)"
                      class="terminate-retry"
                      aria-label="重试这一轮"
                      title="重试这一轮"
                      @click="retryTerminated(turn)"
                    >
                      <AppIcon name="refresh" :size="13" />
                    </button>
                  </div>
                </div>
              </div>

              <div v-if="turn.interrupt" class="row indent">
                <InterruptCard :msg="turn.interrupt" :readonly="archivedActive" />
              </div>

              <div v-for="note in turn.notes" :key="note.id" class="row center">
                <span class="system-note">{{ note.content }}</span>
              </div>
            </template>
          </div>

          <!-- 右侧轮次导航：收起时是一列刻度，悬停展开为可读的轮次列表 -->
          <div
            v-if="turnNav.length > 1"
            class="turn-nav"
            :class="{ open: navOpen }"
            @mouseenter="navOpen = true"
            @mouseleave="navOpen = false"
            @wheel="onNavWheel"
          >
            <div ref="navList" class="turn-nav-list">
              <button
                v-for="(item, i) in turnNav"
                :key="item.key"
                type="button"
                class="turn-nav-row"
                :class="{ active: navActive === i }"
                :data-nav-index="i"
                :aria-label="`第 ${i + 1} 轮：${item.label}`"
                :aria-current="navActive === i ? 'true' : undefined"
                @click="jumpToTurn(i)"
              >
                <span class="turn-nav-label">{{ item.label }}</span>
                <span class="turn-nav-bar"></span>
              </button>
            </div>
          </div>

          <!-- 用户向上翻阅时，不再强制滚到底，只在视口底部提供一个回底按钮 -->
          <Transition name="pop">
            <el-button
              v-if="hasOverflow && !stickToBottom"
              class="jump-bottom"
              size="small"
              :icon="ArrowDown"
              title="回到最新消息"
              @click="jumpToBottom"
            >
              回到底部
            </el-button>
          </Transition>
        </div>

        <!-- 正常会话显示输入框；归档会话改为只读提示，明确区分两种状态 -->
        <footer v-if="!archivedActive" class="input-bar">
          <!-- 后台任务栏：默认收起，展开后可查看并终止当前会话的后台调度 -->
          <BackgroundTasksBar />

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
                <el-switch
                  :model-value="settings.thinking"
                  size="small"
                  :disabled="settingsDisabled"
                  @update:model-value="(v: string | number | boolean) => toggleThinking(v === true)"
                />
              </span>
              <span
                class="mode-toggle"
                :class="{ on: settings.webSearch, disabled: settingsDisabled }"
                :title="'开启后 Agent 可调用 search_web / fetch_web，关闭时后端不再绑定搜索工具'"
              >
                <AppIcon name="globe" :size="12" />
                <span>联网搜索</span>
                <el-switch
                  :model-value="settings.webSearch"
                  size="small"
                  :disabled="settingsDisabled"
                  @update:model-value="(v: string | number | boolean) => toggleWebSearch(v === true)"
                />
              </span>
            </div>
            <span v-if="settingsHint" class="settings-hint">{{ settingsHint }}</span>
          </div>

          <CompactionBanner :state="chat.compaction" />

          <div v-if="pendingFiles.length" class="pending-row">
            <span v-for="att in pendingFiles" :key="att.id" class="pending-chip">
              <img v-if="previews[att.id]" :src="previews[att.id]" alt="" />
              <AppIcon v-else name="file" :size="12" />
              <span class="p-name">{{ att.name }}</span>
              <el-button
                class="chip-close"
                :icon="Close"
                text
                circle
                size="small"
                title="移除"
                @click="removePending(att.id)"
              />
            </span>
          </div>
          <p v-if="uploadError" class="upload-error">{{ uploadError }}</p>

          <div v-if="urlMode" class="url-row">
            <el-icon class="url-icon"><Link /></el-icon>
            <el-input
              v-model="urlDraft"
              class="url-input"
              size="small"
              placeholder="https://example.com/article"
              :disabled="urlLoading"
              @keydown.enter.prevent="submitUrl"
              @keydown.esc="urlMode = false"
            />
            <el-button
              type="primary"
              size="small"
              :disabled="urlLoading || !urlDraft.trim()"
              @click="submitUrl"
            >
              {{ urlLoading ? '抓取中…' : '导入' }}
            </el-button>
            <el-button
              :icon="Close"
              text
              circle
              size="small"
              title="取消"
              @click="urlMode = false"
            />
          </div>

          <div class="input-row">
            <div class="attach-wrap">
              <el-button
                class="attach"
                :class="{ active: attachMenu }"
                size="large"
                circle
                :disabled="uploading || chat.sending || chat.compacting"
                title="添加上下文：文件 / 图片 / 网络链接"
                @click="attachMenu = !attachMenu"
              >
                <span v-if="uploading" class="attach-spin"></span>
                <el-icon v-else><Paperclip /></el-icon>
              </el-button>
              <Transition name="pop">
                <div v-if="attachMenu" class="attach-menu">
                  <button class="attach-item" type="button" @click="pickAttach('file')">
                    <span class="ai-icon"><el-icon><Document /></el-icon></span>
                    <span class="ai-text">
                      <span class="ai-label">文件</span>
                      <small>文档 / 文本</small>
                    </span>
                  </button>
                  <button class="attach-item" type="button" @click="pickAttach('image')">
                    <span class="ai-icon"><el-icon><Picture /></el-icon></span>
                    <span class="ai-text">
                      <span class="ai-label">图片</span>
                      <small>随消息多模态发送</small>
                    </span>
                  </button>
                  <button class="attach-item" type="button" @click="openUrlMode">
                    <span class="ai-icon"><el-icon><Link /></el-icon></span>
                    <span class="ai-text">
                      <span class="ai-label">网络链接</span>
                      <small>抓取网页正文</small>
                    </span>
                  </button>
                </div>
              </Transition>
            </div>
            <div v-if="attachMenu" class="menu-mask" @click="attachMenu = false"></div>
            <!-- 原生文件选择框无法用组件替代，继续隐藏后由按钮触发 -->
            <input
              ref="fileInput"
              class="file-input"
              type="file"
              multiple
              :accept="fileAccept || formats.accept"
              @change="onFilesPicked"
            />
            <el-input
              ref="inputEl"
              v-model="draft"
              class="chat-input"
              type="textarea"
              :autosize="{ minRows: 1, maxRows: 6 }"
              resize="none"
              placeholder="输入消息，可附图片/文档/网页链接；Enter 发送 / Shift+Enter 换行"
              :disabled="chat.sending || chat.compacting"
              @keydown.enter.exact.prevent="submit"
            />
            <el-button
              v-if="chat.sending"
              class="stop"
              type="danger"
              :icon="VideoPause"
              size="large"
              circle
              title="中止"
              @click="chat.stop()"
            />
            <el-button
              v-else
              class="send"
              type="primary"
              :icon="Promotion"
              size="large"
              circle
              :disabled="(!draft.trim() && pendingFiles.length === 0) || uploading || chat.compacting"
              @click="submit"
            />
          </div>

          <!-- 本会话累计消耗 + 本轮输入/输出 + 上下文窗口圆环 -->
          <div v-if="sessionUsage" class="input-foot">
            <span class="usage-spacer" aria-hidden="true"></span>
            <div
              class="usage-text"
              :title="`本会话累计消耗 ${formatExact(sessionSpent)} tok（历轮 input + output，含已撤回轮次）；本轮输入 ${formatExact(turnInput)} tok，输出 ${formatExact(turnOutput)} tok。${usageEstimating ? '流式期间按上一轮上下文与输出字符估算，结束后按实际计费校正。' : ''}`"
            >
              <AppIcon name="cpu" :size="10" />
              <span>本会话消耗</span>
              <b v-if="sessionSpentMissing" title="后端未提供会话累计消耗，请重启后端">—</b>
              <b v-else>{{ usageEstimating ? '≈ ' : '' }}{{ formatExact(sessionSpent) }} tok</b>
              <em>·</em>
              <span>本轮</span>
              <span>输入</span>
              <b>{{ turnEstimating ? '≈ ' : '' }}{{ formatExact(turnInput) }} tok</b>
              <em>·</em>
              <span>输出</span>
              <b>{{ turnEstimating ? '≈ ' : '' }}{{ formatExact(turnOutput) }} tok</b>
              <em v-if="sessionUsage.rounds">· {{ sessionUsage.rounds }} 轮</em>
              <span v-if="chat.compactionCount" class="compact-count">
                已压缩 {{ chat.compactionCount }} 次
              </span>
            </div>

            <div class="ctx-wrap">
              <HoloTooltip
                v-if="ctxWindow > 0"
                title="上下文窗口"
                :code="ctxModelName || '未指定模型'"
                :content="`模型 ${ctxModelName || '未知'}（${ctxModelSource}）；上下文上限 ${formatExact(ctxWindow)} tok · ${formatContext(ctxWindow)}。已占用 ${formatExact(ctxUsed)} tok（${ctxPct}%）。`"
                :footer="
                  ctxTone === 'danger'
                    ? '接近上限，建议压缩'
                    : ctxTone === 'warn'
                      ? '注意用量，接近水位'
                      : '余量充足'
                "
                placement="top"
              >
                <div class="ctx-donut" :class="ctxTone">
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
              </HoloTooltip>
            </div>
          </div>
        </footer>

        <div v-else class="archived-readonly">
          <span class="ar-icon"><AppIcon name="archive" :size="14" /></span>
          <span class="ar-text">该会话已归档，仅支持查看聊天记录，不会再被 Agent 读取。</span>
          <el-button class="ar-restore" size="small" @click="restoreActiveSession">
            恢复会话
          </el-button>
        </div>
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

.agent-select {
  width: 168px;
}

/* ---------- 全息选择器 ----------
   与青色 HOLO ARCHIVE 面板区分：紫 / 品红棱镜描边、斜向扫描纹、
   缓慢掠过的高光带，以及单侧斜切的玻璃底。 */
.holo-select {
  --holo-a: rgba(167, 139, 250, 0.9);
  --holo-b: rgba(244, 114, 182, 0.7);
  --holo-c: rgba(96, 165, 250, 0.55);
}

.holo-select :deep(.el-select__wrapper) {
  position: relative;
  overflow: hidden;
  min-height: 30px;
  padding: 0 26px 0 11px;
  border: 1px solid transparent;
  border-radius: 7px 2px 7px 2px;
  background:
    linear-gradient(135deg, rgba(31, 18, 56, 0.9), rgba(11, 9, 26, 0.94)) padding-box,
    linear-gradient(118deg, var(--holo-a), var(--holo-b) 46%, var(--holo-c)) border-box;
  box-shadow:
    inset 0 0 18px rgba(167, 139, 250, 0.14),
    0 0 0 1px rgba(6, 5, 16, 0.55);
  backdrop-filter: blur(12px);
  transition: box-shadow 0.24s ease;
}

.holo-select :deep(.el-select__wrapper)::before {
  content: '';
  position: absolute;
  inset: 0;
  background: repeating-linear-gradient(
    116deg,
    rgba(226, 214, 255, 0.055) 0 1px,
    transparent 1px 6px
  );
  pointer-events: none;
}

.holo-select :deep(.el-select__wrapper)::after {
  content: '';
  position: absolute;
  top: -70%;
  left: -45%;
  width: 38%;
  height: 240%;
  background: linear-gradient(90deg, transparent, rgba(244, 114, 182, 0.26), transparent);
  transform: rotate(14deg);
  animation: holo-sheen 5.2s ease-in-out infinite;
  pointer-events: none;
}

@keyframes holo-sheen {
  0%,
  100% {
    left: -45%;
    opacity: 0;
  }
  18% {
    opacity: 1;
  }
  60% {
    left: 105%;
    opacity: 1;
  }
  78% {
    opacity: 0;
  }
}

.holo-select :deep(.el-select__wrapper.is-hovering),
.holo-select :deep(.el-select__wrapper.is-focused) {
  box-shadow:
    inset 0 0 26px rgba(244, 114, 182, 0.2),
    0 0 16px rgba(167, 139, 250, 0.34);
}

.holo-select :deep(.el-select__selection),
.holo-select :deep(.el-select__suffix) {
  position: relative;
  z-index: 1;
}

.holo-select :deep(.el-select__placeholder),
.holo-select :deep(.el-select__selected-item) {
  color: #ece6ff;
  font-size: 11.5px;
  letter-spacing: 0.02em;
}

.holo-select :deep(.el-select__placeholder.is-transparent) {
  color: rgba(196, 181, 253, 0.62);
}

.holo-select :deep(.el-select__caret) {
  color: #c4b5fd;
  transition: transform 0.24s ease;
}

.holo-select :deep(.el-select__wrapper.is-focused) .el-select__caret {
  transform: rotate(180deg);
}

/* 组选择器走暖调：琥珀 → 品红，和冷紫的 Agent 选择器区分。 */
.holo-select--group {
  --holo-a: rgba(251, 191, 36, 0.85);
  --holo-b: rgba(244, 114, 182, 0.8);
  --holo-c: rgba(167, 139, 250, 0.5);
}

.holo-select--group :deep(.el-select__wrapper) {
  background:
    linear-gradient(135deg, rgba(54, 28, 42, 0.9), rgba(14, 9, 24, 0.94)) padding-box,
    linear-gradient(118deg, var(--holo-a), var(--holo-b) 46%, var(--holo-c)) border-box;
  box-shadow:
    inset 0 0 18px rgba(251, 191, 36, 0.12),
    0 0 0 1px rgba(6, 5, 16, 0.55);
}

/* 下拉层 teleport 到 body，必须用全局选择器命中 popper-class。 */
:global(.holo-popper) {
  overflow: hidden;
  border: 1px solid rgba(167, 139, 250, 0.42);
  border-radius: 8px 2px 8px 2px;
  background:
    repeating-linear-gradient(
      116deg,
      rgba(226, 214, 255, 0.04) 0 1px,
      transparent 1px 6px
    ),
    linear-gradient(160deg, rgba(24, 15, 44, 0.98), rgba(9, 8, 20, 0.98));
  box-shadow:
    0 18px 46px rgba(0, 0, 0, 0.62),
    0 0 26px rgba(167, 139, 250, 0.2);
  backdrop-filter: blur(14px);
}

:global(.holo-popper .el-select-dropdown__item) {
  color: #cfc7ea;
  font-size: 11.5px;
}

:global(.holo-popper .el-select-dropdown__item.is-hovering) {
  background: linear-gradient(90deg, rgba(167, 139, 250, 0.16), rgba(244, 114, 182, 0.1));
  color: #f5efff;
}

:global(.holo-popper .el-select-dropdown__item.is-selected) {
  color: #f0abfc;
  font-weight: 500;
}

:global(.holo-popper .el-popper__arrow::before) {
  border-color: rgba(167, 139, 250, 0.42);
  background: rgba(24, 15, 44, 0.98);
}

/* Agent / 组下拉项：头像 + 名称 + 所属组组标（队长组右下角带小旗帜） */
.agent-option {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  min-height: 34px;
  line-height: 1.2;
}

.agent-option-avatar {
  display: grid;
  place-items: center;
  flex: none;
  width: 22px;
  height: 22px;
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
  color: #041018;
  border-radius: 50%;
}

.agent-option-avatar--none {
  color: var(--text-3);
  background: transparent;
  border: 1px dashed var(--line);
}

.agent-option-main {
  display: flex;
  align-items: baseline;
  gap: 6px;
  flex: 1;
  min-width: 0;
}

.agent-option-main strong {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.agent-option-main small {
  flex: none;
  font-size: 10px;
  color: var(--text-3);
}

.agent-option-groups {
  display: flex;
  align-items: center;
  gap: 4px;
  flex: none;
}

.group-badge {
  position: relative;
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  font-size: 12px;
  font-weight: 700;
  line-height: 1;
  color: #041018;
  border-radius: 6px;
}

.group-badge--lg {
  width: 32px;
  height: 32px;
  font-size: 12px;
  border-radius: 7px;
}

.group-badge-flag {
  position: absolute;
  right: -3px;
  bottom: -3px;
  display: grid;
  place-items: center;
  width: 11px;
  height: 11px;
  font-size: 7px;
  font-style: normal;
  color: #f6c85f;
  border-radius: 50%;
  background: #0b1622;
}

/* Element Plus 给相邻按钮默认加重叠边距，这里统一交给 flex gap */
.controls .el-button + .el-button,
.url-row .el-button + .el-button {
  margin-left: 0;
}

.controls .el-button {
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

.chip.archived-chip {
  color: var(--amber);
  border-color: rgba(255, 182, 72, 0.4);
  background: rgba(255, 182, 72, 0.08);
}

.messages-wrap {
  position: relative;
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

/* 任务流进度常驻在滚动区顶部：不随消息滚动，折叠后只占一行左右的高度 */
.flow-dock {
  flex: none;
  padding: 10px 20px 0;
}

/* 轨迹粘性折叠条：整条点击切换，层级用 L1/L2 标记 + 缩进表达 */
.traj-pin {
  flex: none;
  display: flex;
  align-items: center;
  gap: 7px;
  margin: 6px 20px 0;
  padding: 5px 8px;
  font-family: inherit;
  font-size: 11.5px;
  color: var(--text-2);
  text-align: left;
  cursor: pointer;
  border: 1px solid var(--line);
  border-left: 2px solid rgba(53, 224, 255, 0.45);
  border-radius: 7px;
  background: rgba(9, 16, 29, 0.94);
  box-shadow: 0 8px 20px rgba(0, 0, 0, 0.32);
  transition: border-color 0.16s, background 0.16s;
}

.traj-pin:hover {
  border-color: rgba(53, 224, 255, 0.45);
  background: rgba(12, 22, 38, 0.96);
}

.traj-pin-level {
  flex: none;
  padding: 0 4px;
  font-family: var(--font-mono);
  font-size: 9px;
  line-height: 15px;
  color: var(--accent-cyan);
  border: 1px solid rgba(53, 224, 255, 0.32);
  border-radius: 4px;
}

.traj-pin-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.traj-pin-chev {
  flex: none;
  color: var(--text-3);
  transition: color 0.16s, transform 0.18s;
}

.traj-pin-chev.open {
  transform: rotate(90deg);
}

.traj-pin:hover .traj-pin-chev {
  color: var(--accent-cyan);
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

.sandbox-warn .warn-text {
  flex: 1;
  min-width: 0;
}

.sandbox-warn .banner-close.el-button {
  width: 18px;
  height: 18px;
  flex: none;
  padding: 0;
  color: var(--text-3);
}

.sandbox-warn .banner-close.el-button:hover {
  color: var(--text-1);
  background: var(--bg-hover);
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

/* 右侧出现轮次导航时，给气泡留出安全距离，避免与刻度重叠 */
.messages.with-nav {
  padding-right: 34px;
}

/* 右侧轮次导航：收起时是一组紧凑刻度，整体垂直居中；悬停向右浮出列表 */
.turn-nav {
  position: absolute;
  top: 50%;
  right: 15px;
  transform: translateY(-50%);
  display: flex;
  flex-direction: column;
  justify-content: center;
  width: 16px;
  max-height: calc(100% - 20px);
  z-index: 11;
  overflow: hidden;
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  transition: width 0.16s ease, background 0.16s ease, border-color 0.16s ease;
}

.turn-nav-list {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 4px;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  scrollbar-width: none;
}

.turn-nav-list::-webkit-scrollbar {
  display: none;
}

.turn-nav-row {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  width: 16px;
  height: 12px;
  padding: 0;
  border: 0;
  background: transparent;
  cursor: pointer;
}

.turn-nav-bar {
  flex: none;
  display: block;
  width: 9px;
  height: 2px;
  border-radius: 1px;
  background: var(--line-strong);
  transition: width 0.15s ease, background 0.15s ease;
}

.turn-nav-row:hover .turn-nav-bar {
  width: 13px;
  background: var(--text-2);
}

.turn-nav-row.active .turn-nav-bar {
  width: 13px;
  background: var(--accent);
}

.turn-nav-label {
  display: none;
  min-width: 0;
  overflow: hidden;
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-2);
  text-align: right;
  white-space: nowrap;
  text-overflow: ellipsis;
}

/* 展开态：面板以刻度组为中心向两侧撑开，覆盖在消息区右侧 */
.turn-nav.open {
  right: 8px;
  width: 268px;
  max-height: calc(100% - 20px);
  padding: 7px 5px 7px 6px;
  border-color: var(--line);
  background: var(--bg-elevated);
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.45);
}

.turn-nav.open .turn-nav-list {
  width: 100%;
  align-items: stretch;
  gap: 2px;
  scrollbar-width: thin;
}

.turn-nav.open .turn-nav-list::-webkit-scrollbar {
  display: block;
  width: 4px;
}

.turn-nav.open .turn-nav-list::-webkit-scrollbar-thumb {
  border-radius: 2px;
  background: var(--line-strong);
}

.turn-nav.open .turn-nav-row {
  width: 100%;
  height: auto;
  min-height: 28px;
  gap: 10px;
  padding: 4px 2px 4px 8px;
  border-radius: var(--radius-sm);
}

.turn-nav.open .turn-nav-row:hover {
  background: var(--bg-hover);
}

.turn-nav.open .turn-nav-label {
  display: block;
}

.turn-nav.open .turn-nav-row:hover .turn-nav-label {
  color: var(--text-1);
}

.turn-nav.open .turn-nav-row.active .turn-nav-label {
  color: var(--accent);
}

/* 轨迹树容器：占满宽度，后面可跟「完整轨迹」入口 */
.tree-row {
  width: 100%;
}

.flow-row {
  width: 100%;
  margin: 2px 0;
}

.tree-col {
  display: flex;
  flex-direction: column;
  gap: 2px;
  width: 100%;
  min-width: 0;
}

/* 回到最新消息：固定在聊天视口底部，不阻塞阅读 */
.jump-bottom.el-button {
  position: absolute;
  left: 50%;
  bottom: 14px;
  transform: translateX(-50%);
  z-index: 12;
  margin-left: 0;
  font-size: 10.5px;
  color: var(--text-1);
  border-color: var(--line-strong);
  background: var(--bg-elevated);
}

.jump-bottom.el-button:hover {
  border-color: var(--accent);
  color: var(--text-1);
  background: var(--bg-elevated);
}

.chat-empty {
  margin: auto;
  max-width: 460px;
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

.empty-title {
  margin: 0 0 6px;
  font-size: 16px;
  color: var(--text-1);
}

.empty-desc {
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

.suggestions :deep(.el-button + .el-button) {
  margin-left: 0;
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

/* 气泡底部的时间戳：只做弱提示，不和操作按钮抢注意力 */
.msg-time {
  font-size: 10px;
  line-height: 22px;
  color: var(--text-3);
  font-variant-numeric: tabular-nums;
  user-select: none;
}

.copy-mini.el-button {
  height: 22px;
  padding: 2px 7px;
  font-size: 10px;
  color: var(--text-3);
}

.copy-mini.el-button:hover {
  color: var(--accent-cyan);
  background: var(--bg-hover);
}

.copy-mini.el-button.copied {
  color: var(--accent-cyan);
}

/* 最终回答下方：复制 / 赞同 / 不赞同 / 重试 的纯图标按钮 */
.meta-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  padding: 0;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--text-3);
  cursor: pointer;
  transition: color 0.15s ease, background 0.15s ease, opacity 0.15s ease;
}

.meta-icon:hover:not(:disabled) {
  color: var(--accent-cyan);
  background: var(--bg-hover);
}

.meta-icon.active {
  color: var(--accent-cyan);
  background: color-mix(in srgb, var(--accent-cyan) 12%, transparent);
}

.meta-icon.dislike.active {
  color: var(--el-color-warning);
  background: color-mix(in srgb, var(--el-color-warning) 14%, transparent);
}

.meta-icon:disabled {
  opacity: 0.45;
  cursor: default;
}

/* 发送栏底部：中间用量文本，右侧上下文圆环 */
.input-foot {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 0 2px;
}

.usage-spacer {
  flex: 1;
  min-width: 0;
}

.ctx-wrap {
  flex: 1;
  display: flex;
  justify-content: flex-end;
  min-width: 0;
}

.usage-text {
  display: flex;
  align-items: center;
  justify-content: center;
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

.compact-count {
  margin-left: 6px;
  padding: 1px 6px;
  border-radius: 999px;
  border: 1px solid rgba(124, 140, 255, 0.35);
  color: #b8c4ff;
  font-size: 10px;
}

/* 上下文窗口占用：圆形进度条 */
.ctx-donut {
  position: relative;
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  flex: none;
  cursor: help;
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

/* 终止原因：与 AI 气泡左边缘对齐的一行状态条 */
.terminate-row {
  padding-left: 38px;
}

.terminate-line {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  max-width: 100%;
}

.terminate-bar {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex: 0 1 auto;
  min-width: 0;
  max-width: 100%;
  padding: 5px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--bg-hover);
  color: var(--text-2);
  font-size: 11.5px;
  line-height: 1.5;
  word-break: break-word;
  animation: fadein 0.2s ease;
}

/* 异常终止后的重试：红色/黄色状态条右侧的黄色图标按钮 */
.terminate-retry {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  width: 24px;
  height: 24px;
  padding: 0;
  border: 1px solid color-mix(in srgb, var(--amber) 45%, transparent);
  border-radius: var(--radius-sm);
  background: color-mix(in srgb, var(--amber) 12%, transparent);
  color: var(--amber);
  cursor: pointer;
  transition:
    background 0.15s ease,
    border-color 0.15s ease;
}

.terminate-retry:hover {
  border-color: var(--amber);
  background: color-mix(in srgb, var(--amber) 22%, transparent);
}

.terminate-bar.user {
  color: var(--text-3);
}

.terminate-bar.network,
.terminate-bar.limit {
  border-color: color-mix(in srgb, var(--amber) 35%, transparent);
  background: color-mix(in srgb, var(--amber) 7%, transparent);
  color: var(--amber);
}

.terminate-bar.server {
  border-color: color-mix(in srgb, var(--danger) 35%, transparent);
  background: color-mix(in srgb, var(--danger) 7%, transparent);
  color: var(--danger);
}

.input-bar {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 16px;
  border-top: 1px solid var(--line);
  background: transparent;
}

/* 归档会话的只读底栏：替代输入框，避免误以为还能继续对话 */
.archived-readonly {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 12px 16px;
  border-top: 1px solid var(--line);
  background: rgba(255, 182, 72, 0.04);
}

.archived-readonly .ar-icon {
  display: grid;
  place-items: center;
  flex: none;
  color: var(--amber);
}

.archived-readonly .ar-text {
  flex: 1;
  min-width: 0;
  font-size: 11.5px;
  color: var(--text-2);
}

.archived-readonly .ar-restore.el-button {
  flex: none;
  height: 28px;
  padding: 0 12px;
  font-size: 11.5px;
  color: var(--accent-cyan);
  border-color: rgba(55, 208, 196, 0.36);
}

.archived-readonly .ar-restore.el-button:hover {
  color: var(--accent-cyan);
  border-color: var(--accent-cyan);
  background: rgba(55, 208, 196, 0.1);
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

.attach.el-button {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  padding: 0;
  flex: none;
  margin-left: 0;
  font-size: 16px;
  color: var(--text-2);
  border-color: var(--line-strong);
}

/* 圆形按钮内部可能出现的空白插槽包裹层，统一居中 */
.attach.el-button > span {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  line-height: 1;
}

/* 附件下拉容器：菜单向上弹出，避免遮挡输入框 */
.attach-wrap {
  position: relative;
  flex: none;
}

.attach.el-button.active {
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

/* 下拉项：图标方块 + 两行文字，用 flex 布局，避免依赖 EP 按钮插槽结构 */
.attach-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 8px 9px;
  font-family: inherit;
  color: var(--text-2);
  text-align: left;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  cursor: pointer;
  transition: background 0.16s ease, color 0.16s ease;
}

.attach-item:hover {
  color: var(--text-1);
  background: var(--bg-hover);
}

.attach-item .ai-icon {
  display: grid;
  place-items: center;
  flex: none;
  width: 28px;
  height: 28px;
  font-size: 14px;
  color: var(--accent-cyan);
  border: 1px solid rgba(55, 208, 196, 0.22);
  border-radius: 6px;
  background: rgba(55, 208, 196, 0.07);
}

.attach-item .ai-text {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}

.attach-item .ai-label {
  font-size: 12.5px;
  line-height: 1.35;
}

.attach-item small {
  font-size: 10.5px;
  line-height: 1.35;
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

.url-row .url-icon {
  flex: none;
  font-size: 14px;
  color: var(--accent-cyan);
}

.url-row .url-input {
  flex: 1;
  min-width: 0;
}

.url-row :deep(.el-input__inner) {
  font-size: 12.5px;
}

.url-row :deep(.el-input__wrapper) {
  background: transparent;
  box-shadow: none;
  padding: 0 4px;
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
  gap: 7px;
  padding: 4px 6px 4px 7px;
  font-size: 11.5px;
  line-height: 1.3;
  color: var(--text-2);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: rgba(126, 154, 198, 0.05);
}

.pending-chip img {
  width: 22px;
  height: 22px;
  object-fit: cover;
  border-radius: 4px;
}

.pending-chip > .el-icon {
  color: var(--text-3);
}

.pending-chip .p-name {
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pending-chip .chip-close.el-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  margin-left: 0;
  padding: 0;
  font-size: 11px;
  color: var(--text-3);
}

.pending-chip .chip-close.el-button:hover {
  color: var(--danger);
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

.input-row .chat-input {
  flex: 1;
  min-width: 0;
}

.chat-input :deep(.el-textarea__inner) {
  min-height: 40px !important;
  padding: 10px 14px;
  font-size: 13px;
  font-family: inherit;
  line-height: 1.6;
  color: var(--text-1);
  border-radius: var(--radius-md);
  background: var(--bg-deep);
  resize: none;
}

.send.el-button,
.stop.el-button {
  width: 40px;
  height: 40px;
  flex: none;
  margin-left: 0;
}

.stop.el-button {
  border-color: var(--danger);
  background: var(--danger);
  color: #fff;
}

.stop.el-button:hover {
  border-color: var(--danger);
  background: rgba(226, 96, 94, 0.82);
  color: #fff;
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
