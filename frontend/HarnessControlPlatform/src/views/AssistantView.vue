<script setup lang="ts">
// 悬浮助手面板：独立不透明窗口，系统级拖拽与缩放交给 Electron。
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { InputInstance } from 'element-plus'
import {
  Document,
  Paperclip,
  Promotion,
  VideoPause,
} from '@element-plus/icons-vue'
import AppIcon from '@/components/console/AppIcon.vue'
import SessionPanel from '@/components/chat/SessionPanel.vue'
import FilePanel from '@/components/chat/FilePanel.vue'
import MarkdownView from '@/components/chat/MarkdownView.vue'
import InterruptCard from '@/components/chat/InterruptCard.vue'
import FileTypeIcon from '@/components/chat/FileTypeIcon.vue'
import ProducedFilesCard from '@/components/chat/ProducedFilesCard.vue'
import TrajectoryTree from '@/components/chat/TrajectoryTree.vue'
import { mapHistoryMessages, type HistoryPayload } from '@/components/chat/history'
import { useChatStore, type ChatMsg } from '@/stores/chat'
import { USER_ID, useSessionStore } from '@/stores/sessions'
import { useDisplayStore } from '@/stores/display'
import { useFileStore } from '@/stores/files'
import { useSettingsStore } from '@/stores/settings'
import { useAgentStore } from '@/stores/agents'
import { API_BASE } from '@/api/client'
import {
  deleteContextFile,
  fetchContextFormats,
  uploadContextFile,
  type ContextFile,
  type ContextFormats,
} from '@/api/context'

document.title = 'Harness 悬浮助手'

const desktop = window.harnessDesktop
const chat = useChatStore()
const sessions = useSessionStore()
const display = useDisplayStore()
const files = useFileStore()
const settings = useSettingsStore()
const agents = useAgentStore()

const LAST_THREAD_KEY = 'harness.chat.lastThread'

function collapsePanel(): void {
  sessionDrawer.value = false
  fileDrawer.value = false
  modesOpen.value = false
  desktop?.collapseAssistant()
}

// ---------- 抽屉 ----------
const sessionDrawer = ref(false)
const fileDrawer = ref(false)

function closeDrawers(): void {
  sessionDrawer.value = false
  fileDrawer.value = false
}

// ---------- 会话 ----------
const backendOnline = ref<boolean | null>(null)
const currentSession = computed(() =>
  sessions.sessions.find((item) => item.thread_id === chat.threadId),
)
const title = computed(() =>
  currentSession.value?.title || (chat.threadId ? '未命名会话' : '新对话'),
)
const fileRefreshKey = ref(0)

async function probeHealth(attempts = 14): Promise<boolean> {
  for (let index = 0; index < attempts; index += 1) {
    try {
      const res = await fetch(`${API_BASE}/health`, { signal: AbortSignal.timeout(2000) })
      if (res.ok) return true
    } catch {
      // 后端仍在启动
    }
    await new Promise((resolveTimer) => setTimeout(resolveTimer, 400))
  }
  return false
}

async function openThread(threadId: string): Promise<void> {
  if (!threadId) return
  closeDrawers()
  sessions.activeThreadId = threadId
  chat.activateThread(threadId)
  const bound = sessions.sessions.find((item) => item.thread_id === threadId)
  chat.selectedAgentId = bound?.agent_id || agents.defaultAgentId || 'default'
  chat.selectedGroupId = bound?.group_id || ''
  void files.prime(threadId)
  void files.loadArtifacts(threadId)
  try {
    if (!chat.isThreadSending(threadId)) {
      const res = await fetch(
        `${API_BASE}/history/${threadId}/messages?user_id=${USER_ID}`,
      )
      if (!res.ok) throw new Error(String(res.status))
      const data = (await res.json()) as HistoryPayload
      const { messages, usage } = mapHistoryMessages(data)
      chat.loadHistory(messages, threadId, usage)
    }
  } catch {
    chat.activateThread(threadId)
  }
  try {
    localStorage.setItem(LAST_THREAD_KEY, threadId)
  } catch {
    // 忽略存储异常
  }
  await nextTick()
  scrollBottom()
}

async function createSession(): Promise<void> {
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
  closeDrawers()
  sessions.activeThreadId = id
  chat.activateThread(id)
  void files.prime(id)
  try {
    localStorage.setItem(LAST_THREAD_KEY, id)
  } catch {
    // 忽略存储异常
  }
  await nextTick()
  scrollBottom()
  void inputEl.value?.focus()
}

async function createIntegrationSession(serverId: string): Promise<void> {
  const id = await sessions.createSession(null, serverId)
  if (!id) return
  await openThread(id)
}

async function openProjectConversations(projectId: string): Promise<void> {
  const latest = sessions.sessions
    .filter((item) => item.project_id === projectId)
    .slice()
    .sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at)))[0]
  if (latest) await openThread(latest.thread_id)
}

async function renameSession(threadId: string, value: string): Promise<void> {
  await sessions.renameSession(threadId, value)
}

async function renameCurrent(): Promise<void> {
  if (!chat.threadId) return
  try {
    const result = await ElMessageBox.prompt('输入新的会话标题', '重命名会话', {
      inputValue: title.value,
      confirmButtonText: '保存',
      cancelButtonText: '取消',
      inputValidator: (value) => Boolean(value?.trim()) || '标题不能为空',
    })
    await sessions.renameSession(chat.threadId, result.value.trim())
  } catch {
    // 用户取消
  }
}

async function removeSession(threadId: string): Promise<void> {
  const sessionTitle = sessions.sessions.find((item) => item.thread_id === threadId)?.title
  try {
    await ElMessageBox.confirm(
      `删除后，「${sessionTitle || '该会话'}」的消息记录与轨迹将一并移除。`,
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
  await sessions.removeSession(threadId)
  if (chat.threadId === threadId) {
    chat.newChat()
    sessions.activeThreadId = null
  }
}

async function togglePin(threadId: string, pinned: boolean): Promise<void> {
  await sessions.setPinned(threadId, pinned)
}

// ---------- 附件 ----------
const pendingFiles = ref<ContextFile[]>([])
const previews = ref<Record<string, string>>({})
const uploading = ref(false)
const uploadError = ref('')
const formats = ref<ContextFormats>({
  accept: '',
  max_files: 6,
  max_file_mb: 20,
  groups: {},
})
const fileInput = ref<HTMLInputElement | null>(null)
const fileAccept = ref('')

function attachmentSrc(att: ContextFile): string {
  const local = previews.value[att.id]
  if (local) return local
  if (att.kind !== 'image') return ''
  return `${API_BASE}/context/${att.id}/raw?user_id=${encodeURIComponent(USER_ID)}`
}

function pickFiles(): void {
  if (uploading.value || chat.sending) return
  fileAccept.value = formats.value.accept || ''
  void nextTick(() => fileInput.value?.click())
}

async function addFiles(list: File[]): Promise<void> {
  if (!list.length) return
  uploadError.value = ''
  const limit = formats.value.max_file_mb * 1024 * 1024
  const allowed = formats.value.accept
    ? formats.value.accept.split(',').map((item) => item.trim()).filter(Boolean)
    : []
  for (const file of list) {
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

async function onFilesPicked(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const picked = Array.from(input.files || [])
  input.value = ''
  await addFiles(picked)
}

async function removePending(id: string): Promise<void> {
  pendingFiles.value = pendingFiles.value.filter((file) => file.id !== id)
  const url = previews.value[id]
  if (url) {
    URL.revokeObjectURL(url)
    delete previews.value[id]
  }
  await deleteContextFile(id, USER_ID)
}

// 拖拽整个助手窗体即可追加附件。
let dragDepth = 0
const dragging = ref(false)

function onDragEnter(): void {
  dragDepth += 1
  dragging.value = true
}

function onDragLeave(): void {
  dragDepth = Math.max(0, dragDepth - 1)
  if (dragDepth === 0) dragging.value = false
}

async function onDrop(event: DragEvent): Promise<void> {
  dragDepth = 0
  dragging.value = false
  const dropped = Array.from(event.dataTransfer?.files || [])
  await addFiles(dropped)
}

// ---------- 发送 ----------
const draft = ref('')
const modesOpen = ref(false)
const inputEl = ref<InputInstance | null>(null)
const scroller = ref<HTMLElement | null>(null)
const stickToBottom = ref(true)

function scrollBottom(): void {
  const el = scroller.value
  if (!el) return
  el.scrollTop = el.scrollHeight
}

function onScroll(): void {
  const el = scroller.value
  if (!el) return
  stickToBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 80
}

async function submit(): Promise<void> {
  if (chat.sending || chat.compacting) return
  if (!draft.value.trim() && pendingFiles.value.length === 0) return
  if (backendOnline.value === false) {
    ElMessage.error('后端离线，暂时无法发送')
    return
  }
  if (!chat.threadId) {
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
    void files.prime(id)
  }
  const text = draft.value
  const attachments = [...pendingFiles.value]
  draft.value = ''
  pendingFiles.value = []
  uploadError.value = ''
  chat.send(text, attachments)
  try {
    if (chat.threadId) {
      sessions.bindRouting(chat.threadId, chat.selectedAgentId, chat.selectedGroupId)
    }
  } catch {
    // 本地索引同步失败不影响发送
  }
  stickToBottom.value = true
  await nextTick()
  scrollBottom()
}

function toggleThinking(value: boolean): void {
  void settings.setThinking(value)
}

function toggleWebSearch(value: boolean): void {
  void settings.setWebSearch(value)
}

function sendSuggestion(text: string): void {
  draft.value = text
  void submit()
}

// ---------- 消息分组 ----------
interface TurnView {
  key: string
  user: ChatMsg | null
  process: ChatMsg[]
  final: ChatMsg | null
  interrupt: ChatMsg | null
  notes: ChatMsg[]
  terminated: ChatMsg | null
  live: boolean
}

function visible(msg: ChatMsg): boolean {
  const prefs = display.prefs
  if (msg.kind === 'reasoning') return prefs.showReasoning
  if (msg.kind === 'subagent') return prefs.showDelegation
  if (msg.kind !== 'tool') return true
  if (msg.category === 'skill' && !prefs.showSkills) return false
  if (msg.category === 'delegation') {
    if (msg.toolName === 'start_async_task' && !prefs.showAsyncTasks) return false
    if (!prefs.showDelegation) return false
  }
  if (msg.category === 'tool' && !prefs.showToolCalls) return false
  return true
}

const shown = computed(() => chat.messages.filter(visible))

const turns = computed<TurnView[]>(() => {
  const result: TurnView[] = []
  let current: TurnView | null = null

  const create = (key: string, user: ChatMsg | null): TurnView => ({
    key,
    user,
    process: [],
    final: null,
    interrupt: null,
    notes: [],
    terminated: null,
    live: false,
  })

  const flush = (): void => {
    if (!current) return
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
      current = create(msg.id, msg)
      continue
    }
    if (!current) current = create(`pre-${msg.id}`, null)
    if (msg.kind === 'terminated') {
      current.terminated = msg
      continue
    }
    // 任务流 / 任务规划不再占用消息流，统一收进左上角小图标。
    if (msg.kind === 'taskflow' || msg.kind === 'scheduler') continue
    if (msg.kind === 'interrupt') {
      if (msg.resolved) {
        current.process.push({
          id: `${msg.id}-resolved`,
          kind: 'interrupt',
          content: msg.content || msg.resolution || '人工介入已处理',
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
  return turn.user ? chat.turnDurations[turn.user.id] : undefined
}

function turnFiles(turn: TurnView) {
  return turn.final ? files.artifactsFor(chat.threadId, turn.final.id) : []
}

function openSessionFile(file: { path: string; origin: 'project' | 'sandbox' }): void {
  files.requestOpen(file.path, file.origin)
  fileDrawer.value = true
}

const TERMINATE_TEXT: Record<string, string> = {
  user: '你已手动终止本次回答',
  network: '连接中断，回答未完成',
  server: '服务端出错，回答已终止',
  limit: '达到模型调用上限，回答提前结束',
}

function terminateText(msg: ChatMsg): string {
  const base = TERMINATE_TEXT[msg.termReason || 'server'] || '回答已终止'
  return msg.termDetail && msg.termReason !== 'user' ? `${base}：${msg.termDetail}` : base
}

// ---------- 置顶卡片：左上角小图标 + 悬停展开 ----------
const activeFlow = computed<ChatMsg | null>(() => {
  for (let index = chat.messages.length - 1; index >= 0; index -= 1) {
    const message = chat.messages[index]
    if (message?.kind !== 'taskflow') continue
    return message.flowStatus === 'running' ? message : null
  }
  return null
})

const activeScheduler = computed<ChatMsg | null>(() => {
  for (let index = chat.messages.length - 1; index >= 0; index -= 1) {
    const message = chat.messages[index]
    if (message?.kind !== 'scheduler') continue
    return message.schedStatus === 'running' ? message : null
  }
  return null
})

const STEP_STATUS: Record<string, string> = {
  pending: '等待',
  running: '运行',
  done: '完成',
  failed: '失败',
  degraded: '兜底',
  skipped: '跳过',
  needs_hitl: '待介入',
}

function statusText(status: string): string {
  return STEP_STATUS[status] || status
}

// ---------- 用量 ----------
const usageLabel = computed(() => {
  const usage = chat.sessionTokens
  if (!usage) return ''
  const total = usage.spent ?? usage.total
  const rounds = usage.rounds || 0
  return `${total.toLocaleString()} tok${rounds ? ` · ${rounds} 轮` : ''}`
})
const ctxPct = computed(() => {
  const usage = chat.sessionTokens
  if (!usage?.contextWindow) return 0
  return Math.min(100, Math.round(((usage.contextUsed || 0) / usage.contextWindow) * 100))
})

// ---------- 运行中会话上报 ----------
function reportRunning(): void {
  if (!desktop) return
  const waiting = new Set(chat.waitingThreadIds)
  const ids = new Set<string>([...chat.runningThreadIds, ...waiting])
  desktop.reportRunningSessions(
    [...ids].map((threadId) => ({
      threadId,
      title: sessions.sessions.find((item) => item.thread_id === threadId)?.title || '新对话',
      waiting: waiting.has(threadId),
    })),
  )
}

watch(
  () => [chat.runningThreadIds, chat.waitingThreadIds, sessions.sessions] as const,
  () => reportRunning(),
  { immediate: true },
)

// 流式输出时消息条数不变但内容增长，用总字符数驱动自动贴底。
const contentTick = computed(() =>
  chat.messages.reduce((total, message) => total + (message.content?.length || 0), 0),
)

watch(
  () => [chat.threadId, chat.messages.length, contentTick.value] as const,
  () => {
    if (stickToBottom.value) void nextTick(scrollBottom)
    if (chat.threadId) {
      try {
        localStorage.setItem(LAST_THREAD_KEY, chat.threadId)
      } catch {
        // 忽略存储异常
      }
    }
  },
)

// 一轮结束后刷新会话标题与文件抽屉
watch(
  () => chat.turnVersion,
  () => {
    if (backendOnline.value) void sessions.refresh()
    fileRefreshKey.value += 1
  },
)

// ---------- 生命周期 ----------
onMounted(async () => {
  chat.demoMode = false
  void agents.load()
  backendOnline.value = await probeHealth()
  if (backendOnline.value) {
    await sessions.refresh()
    void sessions.refreshArchived()
    formats.value = await fetchContextFormats()
    void settings.load()
  }
  let target: string | null = null
  try {
    target = localStorage.getItem(LAST_THREAD_KEY)
  } catch {
    target = null
  }
  if (backendOnline.value && target) {
    const known =
      sessions.sessions.some((item) => item.thread_id === target) || sessions.sessions.length === 0
    if (known) await openThread(target)
  }
  reportRunning()
})

onBeforeUnmount(() => {
  desktop?.reportRunningSessions([])
  for (const url of Object.values(previews.value)) URL.revokeObjectURL(url)
})
</script>

<template>
  <div
    class="assistant-root"
    @dragenter.prevent="onDragEnter"
    @dragover.prevent
    @dragleave.prevent="onDragLeave"
    @drop.prevent="onDrop"
  >
    <section class="assistant-panel">
      <div class="panel-grid" aria-hidden="true"></div>

      <header class="as-head">
        <button
          class="as-icon"
          type="button"
          title="会话列表"
          :class="{ active: sessionDrawer }"
          @click="sessionDrawer = !sessionDrawer; fileDrawer = false"
        >
          <AppIcon name="list" :size="14" />
        </button>

        <div class="as-title">
          <strong :title="title">{{ title }}</strong>
          <button
            v-if="chat.threadId"
            class="rename-btn"
            type="button"
            title="重命名会话"
            @click="renameCurrent"
          >
            <AppIcon name="edit" :size="12" />
          </button>
        </div>

        <button
          class="as-icon"
          type="button"
          title="会话文件"
          :class="{ active: fileDrawer }"
          @click="fileDrawer = !fileDrawer; sessionDrawer = false"
        >
          <AppIcon name="folder" :size="14" />
        </button>
        <button class="as-icon" type="button" title="收起到桌面边缘" @click="collapsePanel">
          <AppIcon name="minimize" :size="13" />
        </button>
      </header>

      <div class="as-body">
        <!-- 左：会话列表抽屉；窄窗口下占满整个内容区 -->
        <Transition name="drawer-left">
          <aside v-if="sessionDrawer" class="drawer left">
            <header class="drawer-head">
              <span class="drawer-kicker mono">SESSIONS</span>
              <strong>会话</strong>
              <button class="as-icon" type="button" title="收起会话列表" @click="sessionDrawer = false">
                <AppIcon name="close" :size="12" />
              </button>
            </header>
            <div class="drawer-content">
              <SessionPanel
                @select="openThread"
                @create="createSession"
                @remove="removeSession"
                @rename="renameSession"
                @pin="togglePin"
                @select-project="openProjectConversations"
                @create-integration="createIntegrationSession"
                @open-archived="openThread"
                @archived-removed="() => undefined"
              />
            </div>
          </aside>
        </Transition>

        <!-- 右：文件抽屉 -->
        <Transition name="drawer-right">
          <aside v-if="fileDrawer" class="drawer right">
            <header class="drawer-head">
              <span class="drawer-kicker mono">FILES</span>
              <strong>会话文件</strong>
              <button class="as-icon" type="button" title="收起文件列表" @click="fileDrawer = false">
                <AppIcon name="close" :size="12" />
              </button>
            </header>
            <div class="drawer-content">
              <FilePanel
                :thread-id="chat.threadId"
                :refresh-key="fileRefreshKey"
                @close="fileDrawer = false"
              />
            </div>
          </aside>
        </Transition>

        <div
          class="as-messages"
          :class="{ 'with-pins': Boolean(activeFlow || activeScheduler) }"
          ref="scroller"
          @scroll.passive="onScroll"
        >
          <!-- 任务流 / 任务规划：只保留左上角小图标，悬停展开步骤摘要 -->
          <div v-if="activeFlow || activeScheduler" class="pin-stack">
            <div v-if="activeFlow" class="pin">
              <button class="pin-btn" type="button" title="任务流进行中">
                <AppIcon name="workflow" :size="13" />
              </button>
              <div class="pin-pop">
                <header class="pin-pop-head">
                  <span class="pin-pop-kicker mono">TASK FLOW</span>
                  <strong>{{ activeFlow.flowName || '任务流' }}</strong>
                </header>
                <ol class="pin-list">
                  <li v-for="(step, index) in activeFlow.flowSteps || []" :key="step.id">
                    <span class="pin-index mono">{{ index + 1 }}</span>
                    <span class="pin-name">{{ step.name }}</span>
                    <span class="pin-state" :class="step.status">{{ statusText(step.status) }}</span>
                  </li>
                  <li v-if="!(activeFlow.flowSteps || []).length" class="pin-empty">等待步骤下发…</li>
                </ol>
              </div>
            </div>

            <div v-if="activeScheduler" class="pin">
              <button class="pin-btn" type="button" title="任务规划进行中">
                <AppIcon name="sparkles" :size="13" />
              </button>
              <div class="pin-pop">
                <header class="pin-pop-head">
                  <span class="pin-pop-kicker mono">PLANNER</span>
                  <strong>{{ activeScheduler.schedGoal || '任务规划' }}</strong>
                </header>
                <ol class="pin-list">
                  <li
                    v-for="(item, index) in activeScheduler.schedItems || []"
                    :key="item.id"
                  >
                    <span class="pin-index mono">{{ index + 1 }}</span>
                    <span class="pin-name">{{ item.title }}</span>
                    <span class="pin-state" :class="item.status">{{ statusText(item.status) }}</span>
                  </li>
                  <li v-if="!(activeScheduler.schedItems || []).length" class="pin-empty">
                    等待计划下发…
                  </li>
                </ol>
              </div>
            </div>
          </div>

          <div v-if="!turns.length" class="as-empty">
            <span class="empty-orb"><AppIcon name="sparkles" :size="22" /></span>
            <p>悬浮助手已就绪</p>
            <small>直接输入问题，或拖入图片 / 文档开始一轮对话。</small>
          </div>

          <template v-for="turn in turns" :key="turn.key">
            <div v-if="turn.user" class="as-row user">
              <div class="bubble user-bubble">
                <div v-if="turn.user.attachments?.length" class="bubble-atts">
                  <span v-for="att in turn.user.attachments" :key="att.id" class="bubble-att">
                    <img
                      v-if="att.kind === 'image' && attachmentSrc(att)"
                      class="att-thumb"
                      :src="attachmentSrc(att)"
                      :alt="att.name"
                    />
                    <FileTypeIcon v-else :ext="att.ext" :name="att.name" :size="18" />
                    <span>{{ att.name }}</span>
                  </span>
                </div>
                <span v-if="turn.user.content">{{ turn.user.content }}</span>
              </div>
            </div>

            <div v-if="turn.process.length || turn.live" class="as-row tree-row">
              <TrajectoryTree
                :items="turn.process"
                :live="turn.live"
                :duration-ms="turnDuration(turn)"
              />
            </div>

            <div v-if="turn.final" class="as-row ai">
              <div class="ai-avatar">AI</div>
              <div class="ai-col">
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
                <ProducedFilesCard
                  v-if="!turn.final.streaming && turnFiles(turn).length"
                  :files="turnFiles(turn)"
                  @open-file="openSessionFile"
                />
              </div>
            </div>

            <div v-if="turn.terminated" class="as-row terminate-row">
              <span>{{ terminateText(turn.terminated) }}</span>
            </div>

            <div v-if="turn.interrupt" class="as-row">
              <InterruptCard :msg="turn.interrupt" />
            </div>

            <div v-for="note in turn.notes" :key="note.id" class="as-row note-row">
              <span class="system-note">{{ note.content }}</span>
            </div>
          </template>
        </div>
      </div>

      <footer class="composer">
        <div class="mode-row">
          <button
            class="mode-trigger"
            type="button"
            :class="{ open: modesOpen }"
            @click="modesOpen = !modesOpen"
          >
            <AppIcon name="sliders" :size="12" />
            <span>模式</span>
            <AppIcon name="chevronRight" :size="9" class="mode-chev" :class="{ open: modesOpen }" />
          </button>
          <Transition name="pop">
            <div v-if="modesOpen" class="mode-checks">
              <label class="check">
                <input
                  type="checkbox"
                  :checked="settings.thinking"
                  :disabled="chat.sending || settings.saving"
                  @change="toggleThinking(($event.target as HTMLInputElement).checked)"
                />
                <span>思考模式</span>
              </label>
              <label class="check">
                <input
                  type="checkbox"
                  :checked="settings.webSearch"
                  :disabled="chat.sending || settings.saving"
                  @change="toggleWebSearch(($event.target as HTMLInputElement).checked)"
                />
                <span>联网搜索</span>
              </label>
            </div>
          </Transition>
          <span class="composer-hint">Enter 发送 / Shift+Enter 换行</span>
        </div>

        <div v-if="pendingFiles.length" class="pending-row">
          <span v-for="att in pendingFiles" :key="att.id" class="pending-chip">
            <img
              v-if="att.kind === 'image' && attachmentSrc(att)"
              class="att-thumb"
              :src="attachmentSrc(att)"
              :alt="att.name"
            />
            <FileTypeIcon v-else :ext="att.ext" :name="att.name" :size="18" />
            <span class="p-name">{{ att.name }}</span>
            <button class="chip-close" type="button" title="移除" @click="removePending(att.id)">
              <AppIcon name="close" :size="10" />
            </button>
          </span>
        </div>
        <p v-if="uploadError" class="upload-error">{{ uploadError }}</p>

        <div class="input-row">
          <button
            class="attach-btn"
            type="button"
            :disabled="uploading || chat.sending"
            title="添加文件 / 图片"
            @click="pickFiles"
          >
            <span v-if="uploading" class="attach-spin"></span>
            <el-icon v-else><Paperclip /></el-icon>
          </button>
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
            :autosize="{ minRows: 1, maxRows: 5 }"
            resize="none"
            placeholder="输入消息，可附图片 / 文档；Enter 发送"
            :disabled="chat.sending || chat.compacting"
            @keydown.enter.exact.prevent="submit"
          />
          <el-button
            v-if="chat.sending"
            class="send holo-send-btn is-stop"
            :icon="VideoPause"
            size="large"
            title="中止"
            @click="chat.stop()"
          />
          <el-button
            v-else
            class="send holo-send-btn"
            :icon="Promotion"
            size="large"
            title="发送"
            :disabled="(!draft.trim() && pendingFiles.length === 0) || uploading || chat.compacting"
            @click="submit"
          />
        </div>

        <div class="composer-foot">
          <span v-if="usageLabel" class="usage">
            <AppIcon name="cpu" :size="9" />
            {{ usageLabel }}
          </span>
          <span v-if="ctxPct" class="ctx mono" :title="`上下文占用 ${ctxPct}%`">
            {{ ctxPct }}%
          </span>
        </div>
      </footer>

      <div v-if="dragging" class="drag-mask">
        <span class="drag-icon"><Document :size="22" /></span>
        <strong>松开以添加附件</strong>
        <small>支持图片与文档，可直接拖入整个助手窗口</small>
      </div>
    </section>
  </div>
</template>

<style scoped>
.assistant-root {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100vw;
  height: 100vh;
  overflow: hidden;
  font-family: var(--font-sans);
  color: var(--text-1);
}

/* ---------- 展开态面板 ---------- */
.assistant-panel {
  position: relative;
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  overflow: hidden;
  cursor: default;
  border: 1px solid rgba(55, 208, 196, 0.42);
  border-radius: 12px;
  background:
    repeating-linear-gradient(
      0deg,
      rgba(55, 208, 196, 0.028) 0,
      rgba(55, 208, 196, 0.028) 1px,
      transparent 1px,
      transparent 4px
    ),
    linear-gradient(145deg, rgba(6, 22, 34, 0.97), rgba(4, 10, 20, 0.96));
  box-shadow:
    0 0 0 1px rgba(55, 208, 196, 0.08),
    0 22px 60px rgba(0, 0, 0, 0.6),
    inset 0 0 50px rgba(55, 208, 196, 0.05);
}

.panel-grid {
  position: absolute;
  inset: 0;
  pointer-events: none;
  background-image:
    linear-gradient(rgba(55, 208, 196, 0.045) 1px, transparent 1px),
    linear-gradient(90deg, rgba(55, 208, 196, 0.045) 1px, transparent 1px);
  background-size: 30px 30px;
  mask-image: radial-gradient(circle at 50% 30%, #000 18%, transparent 78%);
}

.as-head {
  position: relative;
  z-index: 4;
  display: flex;
  align-items: center;
  gap: 6px;
  height: 44px;
  padding: 0 8px;
  border-bottom: 1px solid rgba(55, 208, 196, 0.22);
  background: rgba(4, 12, 22, 0.55);
  -webkit-app-region: drag;
}

.as-head .as-icon,
.as-head .rename-btn {
  -webkit-app-region: no-drag;
}

.as-icon {
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  flex: none;
  padding: 0;
  color: var(--text-2);
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
}

.as-icon:hover,
.as-icon.active {
  color: #dffaff;
  border-color: rgba(55, 208, 196, 0.38);
  background: rgba(55, 208, 196, 0.1);
}

.as-title {
  display: flex;
  align-items: center;
  gap: 5px;
  flex: 1;
  min-width: 0;
  justify-content: center;
}

.as-title strong {
  min-width: 0;
  overflow: hidden;
  font-size: 12px;
  font-weight: 600;
  color: #dffaff;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.rename-btn {
  display: grid;
  place-items: center;
  width: 20px;
  height: 20px;
  flex: none;
  padding: 0;
  color: var(--text-3);
  border: 0;
  border-radius: 4px;
  background: transparent;
}

.rename-btn:hover {
  color: var(--accent-cyan);
  background: rgba(55, 208, 196, 0.12);
}

.as-body {
  position: relative;
  flex: 1;
  min-height: 0;
}

/* ---------- 抽屉 ---------- */
.drawer {
  position: absolute;
  top: 0;
  bottom: 0;
  z-index: 30;
  display: flex;
  flex-direction: column;
  width: 100%;
  max-width: 340px;
  background:
    repeating-linear-gradient(
      0deg,
      rgba(55, 208, 196, 0.03) 0,
      rgba(55, 208, 196, 0.03) 1px,
      transparent 1px,
      transparent 4px
    ),
    linear-gradient(160deg, rgba(6, 24, 36, 0.99), rgba(4, 10, 20, 0.98));
  box-shadow: 0 0 40px rgba(0, 0, 0, 0.55);
}

.drawer.left {
  left: 0;
  border-right: 1px solid rgba(55, 208, 196, 0.32);
}

.drawer.right {
  right: 0;
  border-left: 1px solid rgba(55, 208, 196, 0.32);
}

/* 助手窗口很窄时，抽屉直接铺满内容区 */
@media (max-width: 480px) {
  .drawer {
    max-width: none;
  }
}

.drawer-head {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 40px;
  padding: 0 10px;
  flex: none;
  border-bottom: 1px solid rgba(55, 208, 196, 0.2);
}

.drawer-kicker {
  font-size: 8px;
  letter-spacing: 0.18em;
  color: rgba(55, 208, 196, 0.7);
}

.drawer-head strong {
  font-size: 12px;
  color: #dffaff;
}

.drawer-head .as-icon {
  margin-left: auto;
}

.drawer-content {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.drawer-content :deep(.session-panel) {
  width: 100% !important;
  height: 100%;
  border-right: 0;
  background: transparent;
}

.drawer-content :deep(.file-panel) {
  width: 100% !important;
  height: 100%;
  border-left: 0;
  background: transparent;
}

.drawer-content :deep(.file-panel .resizer) {
  display: none;
}

.drawer-left-enter-active,
.drawer-left-leave-active,
.drawer-right-enter-active,
.drawer-right-leave-active {
  transition: transform 0.24s cubic-bezier(0.22, 1, 0.36, 1), opacity 0.18s ease;
}

.drawer-left-enter-from,
.drawer-left-leave-to {
  opacity: 0;
  transform: translateX(-18px);
}

.drawer-right-enter-from,
.drawer-right-leave-to {
  opacity: 0;
  transform: translateX(18px);
}

/* ---------- 消息区 ---------- */
.as-messages {
  position: relative;
  height: 100%;
  padding: 12px 12px 10px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

/* 顶部有任务流 / 规划小图标时，给首条消息留出让位空间。 */
.as-messages.with-pins {
  padding-top: 46px;
}

.pin-stack {
  position: sticky;
  top: 0;
  z-index: 6;
  display: flex;
  align-items: flex-start;
  gap: 6px;
  align-self: flex-start;
  height: 0;
  overflow: visible;
}

.pin {
  position: relative;
}

.pin-btn {
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  padding: 0;
  color: var(--accent-cyan);
  border: 1px solid rgba(55, 208, 196, 0.45);
  border-radius: 7px;
  background: rgba(5, 20, 30, 0.92);
  box-shadow: 0 0 12px rgba(55, 208, 196, 0.18);
}

.pin-btn:hover {
  color: #dffaff;
  border-color: rgba(160, 240, 255, 0.8);
}

.pin-pop {
  position: absolute;
  top: 32px;
  left: 0;
  z-index: 20;
  display: none;
  width: 236px;
  padding: 10px 11px;
  border: 1px solid rgba(55, 208, 196, 0.4);
  border-radius: 8px;
  background: linear-gradient(150deg, rgba(6, 26, 38, 0.99), rgba(4, 10, 20, 0.98));
  box-shadow: 0 16px 40px rgba(0, 0, 0, 0.6);
  clip-path: polygon(
    0 8px,
    8px 0,
    calc(100% - 14px) 0,
    100% 14px,
    100% calc(100% - 8px),
    calc(100% - 8px) 100%,
    14px 100%,
    0 calc(100% - 14px)
  );
}

.pin:hover .pin-pop {
  display: block;
}

.pin-pop-head {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding-bottom: 6px;
  margin-bottom: 6px;
  border-bottom: 1px solid rgba(55, 208, 196, 0.18);
}

.pin-pop-kicker {
  font-size: 7.5px;
  letter-spacing: 0.18em;
  color: rgba(55, 208, 196, 0.7);
}

.pin-pop-head strong {
  font-size: 11.5px;
  font-weight: 600;
  color: #dffaff;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pin-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
  max-height: 220px;
  overflow-y: auto;
}

.pin-list li {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 10.5px;
  color: var(--text-2);
}

.pin-index {
  width: 16px;
  flex: none;
  color: var(--text-3);
  text-align: right;
}

.pin-name {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.pin-state {
  flex: none;
  font-size: 9px;
  color: var(--text-3);
}

.pin-state.running {
  color: var(--accent-cyan);
}
.pin-state.done {
  color: var(--green);
}
.pin-state.failed {
  color: var(--danger);
}
.pin-state.needs_hitl {
  color: var(--amber);
}

.pin-empty {
  font-size: 10px;
  color: var(--text-3);
}

.as-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  flex: 1;
  min-height: 140px;
  color: var(--text-3);
  text-align: center;
}

.as-empty .empty-orb {
  display: grid;
  place-items: center;
  width: 46px;
  height: 46px;
  margin-bottom: 2px;
  color: var(--accent-cyan);
  border: 1px solid rgba(55, 208, 196, 0.3);
  border-radius: 50%;
  background: rgba(55, 208, 196, 0.07);
  box-shadow: 0 0 24px rgba(55, 208, 196, 0.16);
}

.as-empty p {
  margin: 0;
  font-size: 12px;
  color: var(--text-1);
}

.as-empty small {
  font-size: 10px;
  color: var(--text-3);
}

.as-row {
  display: flex;
  gap: 8px;
  min-width: 0;
}

.as-row.user {
  justify-content: flex-end;
}

.bubble {
  max-width: 88%;
  padding: 8px 10px;
  border-radius: 9px;
  font-size: 12px;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
}

.user-bubble {
  color: #e8f2ff;
  border: 1px solid rgba(91, 140, 255, 0.32);
  background: linear-gradient(145deg, rgba(38, 72, 140, 0.55), rgba(18, 38, 78, 0.55));
}

.bubble-atts {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin-bottom: 5px;
}

.bubble-att {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 100%;
  padding: 2px 6px;
  font-size: 10px;
  color: var(--text-2);
  border: 1px solid rgba(126, 154, 198, 0.24);
  border-radius: 5px;
  background: rgba(6, 14, 26, 0.5);
}

.att-thumb {
  width: 22px;
  height: 22px;
  border-radius: 4px;
  object-fit: cover;
}

.as-row.tree-row {
  padding-left: 2px;
}

.as-row.ai {
  align-items: flex-start;
}

.ai-avatar {
  display: grid;
  place-items: center;
  width: 22px;
  height: 22px;
  margin-top: 2px;
  flex: none;
  font-family: var(--font-mono);
  font-size: 9px;
  color: #032a2c;
  border-radius: 50%;
  background: linear-gradient(135deg, #9ef5ea, #37d0c4);
  box-shadow: 0 0 12px rgba(55, 208, 196, 0.35);
}

.ai-col {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
  flex: 1;
}

.ai-bubble {
  max-width: 100%;
  padding: 6px 2px 2px;
  white-space: normal;
  border: 0;
  background: transparent;
}

.cursor {
  display: inline-block;
  width: 6px;
  height: 13px;
  margin-left: 2px;
  vertical-align: text-bottom;
  background: rgba(160, 240, 255, 0.8);
  animation: cursor-blink 1s steps(2) infinite;
}

@keyframes cursor-blink {
  50% {
    opacity: 0;
  }
}

.terminate-row {
  padding: 3px 8px;
  font-size: 10px;
  color: var(--amber);
  border-left: 2px solid rgba(242, 176, 60, 0.6);
  background: rgba(242, 176, 60, 0.06);
}

.note-row {
  justify-content: center;
}

.system-note {
  font-size: 10px;
  color: var(--text-3);
}

/* ---------- 输入区 ---------- */
.composer {
  position: relative;
  z-index: 5;
  flex: none;
  padding: 6px 10px 8px;
  border-top: 1px solid rgba(55, 208, 196, 0.22);
  background: rgba(4, 12, 22, 0.6);
}

.mode-row {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  padding-bottom: 6px;
  min-height: 24px;
}

.mode-trigger {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 7px;
  font-size: 10px;
  color: var(--text-2);
  border: 1px solid rgba(126, 154, 198, 0.24);
  border-radius: 999px;
  background: transparent;
}

.mode-trigger:hover,
.mode-trigger.open {
  color: #dffaff;
  border-color: rgba(55, 208, 196, 0.5);
  background: rgba(55, 208, 196, 0.08);
}

.mode-chev {
  transition: transform 0.18s ease;
}

.mode-chev.open {
  transform: rotate(90deg);
}

.mode-checks {
  display: flex;
  align-items: center;
  gap: 10px;
}

.check {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 10.5px;
  color: var(--text-2);
  cursor: pointer;
}

.check input {
  width: 12px;
  height: 12px;
  accent-color: var(--accent-cyan);
  cursor: pointer;
}

.composer-hint {
  margin-left: auto;
  font-size: 9px;
  color: var(--text-3);
}

.pending-row {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  padding-bottom: 6px;
}

.pending-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  max-width: 100%;
  padding: 2px 5px 2px 4px;
  font-size: 10px;
  color: var(--text-2);
  border: 1px solid rgba(55, 208, 196, 0.28);
  border-radius: 6px;
  background: rgba(55, 208, 196, 0.06);
}

.pending-chip .p-name {
  max-width: 150px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.chip-close {
  display: grid;
  place-items: center;
  width: 15px;
  height: 15px;
  padding: 0;
  color: var(--text-3);
  border: 0;
  border-radius: 50%;
  background: transparent;
}

.chip-close:hover {
  color: #fff;
  background: rgba(226, 96, 94, 0.4);
}

.upload-error {
  margin: 0 0 4px;
  font-size: 10px;
  color: var(--danger);
}

.input-row {
  display: flex;
  align-items: flex-end;
  gap: 6px;
}

.attach-btn {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  flex: none;
  padding: 0;
  color: var(--text-2);
  border: 1px solid rgba(126, 154, 198, 0.24);
  border-radius: 50%;
  background: rgba(6, 14, 26, 0.5);
}

.attach-btn:hover {
  color: #dffaff;
  border-color: rgba(55, 208, 196, 0.55);
}

.attach-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.attach-spin {
  width: 12px;
  height: 12px;
  border: 1.5px solid rgba(160, 240, 255, 0.25);
  border-top-color: var(--accent-cyan);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.file-input {
  display: none;
}

.chat-input {
  flex: 1;
}

.chat-input :deep(.el-textarea__inner) {
  min-height: 32px !important;
  padding: 6px 10px;
  font-size: 12px;
  line-height: 1.55;
  color: var(--text-1);
  border: 1px solid rgba(126, 154, 198, 0.2);
  border-radius: 16px;
  background: rgba(6, 14, 26, 0.62);
  box-shadow: none;
}

.chat-input :deep(.el-textarea__inner:focus) {
  border-color: rgba(55, 208, 196, 0.55);
  box-shadow: 0 0 0 1px rgba(55, 208, 196, 0.16);
}

.send {
  width: 36px;
  height: 36px;
  flex: none;
  margin-left: 0;
}

.composer-foot {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  min-height: 14px;
  padding-top: 4px;
}

.usage {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 9px;
  color: var(--text-3);
}

.ctx {
  font-size: 9px;
  color: var(--text-3);
}

.drag-mask {
  position: absolute;
  inset: 8px;
  z-index: 40;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  color: #dffaff;
  border: 1px dashed rgba(160, 240, 255, 0.7);
  border-radius: 10px;
  background: rgba(4, 16, 26, 0.72);
  backdrop-filter: blur(4px);
  pointer-events: none;
}

.drag-mask strong {
  font-size: 12px;
}

.drag-mask small {
  font-size: 10px;
  color: var(--text-2);
}

.drag-icon {
  display: grid;
  place-items: center;
  width: 40px;
  height: 40px;
  color: var(--accent-cyan);
  border: 1px solid rgba(55, 208, 196, 0.4);
  border-radius: 50%;
  background: rgba(55, 208, 196, 0.08);
}

.pop-enter-active,
.pop-leave-active {
  transition: opacity 0.16s ease, transform 0.16s ease;
}

.pop-enter-from,
.pop-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
