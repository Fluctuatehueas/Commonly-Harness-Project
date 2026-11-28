﻿﻿﻿<script setup lang="ts">
// 悬浮助手面板：独立不透明窗口，系统级拖拽与缩放交给 Electron。
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ArrowUpBold,
  Document,
  Plus,
} from '@element-plus/icons-vue'
import CommandComposer from '@/components/chat/CommandComposer.vue'
import CommandTerminal from '@/components/chat/CommandTerminal.vue'
import BindingBar, { type BindingChip } from '@/components/chat/BindingBar.vue'
import { parseInput } from '@/commands/parser'
import type { ParsedInput } from '@/types/commands'
import { useCommandRunner } from '@/commands/runner'
import { useCommandsStore } from '@/stores/commands'
import AppIcon from '@/components/console/AppIcon.vue'
import SessionPanel from '@/components/chat/SessionPanel.vue'
import FilePanel from '@/components/chat/FilePanel.vue'
import MarkdownView from '@/components/chat/MarkdownView.vue'
import MediaViewer from '@/components/chat/MediaViewer.vue'
import InterruptCard from '@/components/chat/InterruptCard.vue'
import CollapsibleText from '@/components/chat/CollapsibleText.vue'
import FileTypeIcon from '@/components/chat/FileTypeIcon.vue'
import ProducedFilesCard from '@/components/chat/ProducedFilesCard.vue'
import TrajectoryTree from '@/components/chat/TrajectoryTree.vue'
import CurrentAgentPicker from '@/components/chat/CurrentAgentPicker.vue'
import { mapHistoryMessages, type HistoryPayload } from '@/components/chat/history'
import { useChatStore, type ChatMsg } from '@/stores/chat'
import { USER_ID, useSessionStore } from '@/stores/sessions'
import { useDisplayStore } from '@/stores/display'
import { useFileStore } from '@/stores/files'
import { useSettingsStore } from '@/stores/settings'
import { useAgentStore } from '@/stores/agents'
import { avatarGradient, avatarInitial } from '@/components/myagent/avatar'
import { API_BASE } from '@/api/client'
import { withdrawLatestMessage } from '@/api/chat'
import { fetchFeedbackSettings } from '@/api/settings'
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

// 每轮答复者：优先用该轮发送时绑定的 Agent，旧数据回落到当前选择。
// 这样同一会话里先 A 后 B 时，第一轮仍然显示 A 的头像与名称。
function turnAgent(turn: TurnView): {
  id: string
  name: string
  role: string
  initial: string
  gradient: string
} {
  const id = turn.user?.agentId || chat.selectedAgentId || 'default'
  const profile = agents.byId(id)
  const name = profile?.name || '默认智能体'
  return {
    id,
    name,
    role: profile?.role_title || '',
    initial: avatarInitial(name, profile?.avatar || ''),
    gradient: avatarGradient(id),
  }
}

const LAST_THREAD_KEY = 'harness.chat.lastThread'

function collapsePanel(): void {
  sessionDrawer.value = false
  fileDrawer.value = false
  modesOpen.value = false
  desktop?.collapseAssistant()
}

/** 刷新窗口：重新挂载页面即会重新请求后端，拿到最新会话与配置。 */
function refreshPanel(): void {
  window.location.reload()
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
  // 恢复该会话上次使用的 Agent：本地选择优先，其次后端绑定，最后才回落默认。
  chat.selectedAgentId =
    sessions.lastAgent(threadId) || bound?.agent_id || agents.defaultAgentId || 'default'
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

// 记住该会话最后选择的 Agent：即使还没发消息，切走再切回也保持同一个。
watch(
  () => chat.selectedAgentId,
  (agentId) => {
    if (agentId && chat.threadId) sessions.rememberAgent(chat.threadId, agentId)
  },
)

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
  void composerEl.value?.focus()
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

// 图片附件缩略图预览：复用全屏 MediaViewer。
const mediaOpen = ref(false)
const mediaSrc = ref('')
const mediaTitle = ref('')
const mediaFilename = ref('')

function openImagePreview(src: string, name: string) {
  if (!src) return
  mediaSrc.value = src
  mediaTitle.value = name
  mediaFilename.value = name
  mediaOpen.value = true
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

// 剪贴板图片的 MIME → 扩展名；后端只接受这几种图片格式。
const PASTE_IMAGE_EXT: Record<string, string> = {
  'image/png': '.png',
  'image/jpeg': '.jpg',
  'image/jpg': '.jpg',
  'image/webp': '.webp',
  'image/gif': '.gif',
}

/** 粘贴板里的图片自动转成附件；只含图片时阻止默认粘贴行为。 */
function onClipboardPaste(event: ClipboardEvent): void {
  if (archivedActive.value) return
  const data = event.clipboardData
  if (!data) return
  const items = Array.from(data.items || [])
  const images = items
    .filter((item) => item.kind === 'file' && item.type.startsWith('image/'))
    .map((item) => item.getAsFile())
    .filter((file): file is File => Boolean(file))
  if (!images.length) return
  // 同时带文本时保留文本粘贴，图片额外作为附件加入。
  const hasText = items.some((item) => item.kind === 'string' && item.type === 'text/plain')
  if (!hasText) event.preventDefault()
  if (uploading.value || chat.sending || chat.compacting) return
  const files = images.map((image, index) => {
    const mime = image.type || 'image/png'
    const ext = PASTE_IMAGE_EXT[mime] || '.png'
    return new File([image], `粘贴图片-${Date.now()}-${index}${ext}`, { type: mime })
  })
  void addFiles(files)
}

// 拖拽整个助手窗体即可追加附件。
let dragDepth = 0
const dragging = ref(false)

function onDragEnter(): void {
  if (archivedActive.value) return
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
  if (archivedActive.value) return
  const dropped = Array.from(event.dataTransfer?.files || [])
  await addFiles(dropped)
}

// ---------- 发送 ----------
const draft = ref('')
const modesOpen = ref(false)
const composerEl = ref<InstanceType<typeof CommandComposer> | null>(null)
const commands = useCommandsStore()
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
  updateNavActive()
}

async function submit(): Promise<void> {
  if (archivedActive.value) return
  if (chat.sending || chat.compacting) return
  const source = draft.value
  if (!source.trim() && pendingFiles.value.length === 0) return
  if (backendOnline.value === false) {
    ElMessage.error('后端离线，暂时无法发送')
    return
  }
  if (!commands.catalog.length) await commands.loadCatalog()
  const parsed: ParsedInput = commands.modeEnabled
    ? parseInput(source, commands.commandNames)
    : { commands: [], rest: source }
  if (parsed.error) {
    commands.showTerminal({
      ok: false,
      command: 'parse',
      kind: 'text',
      title: '命令解析失败',
      text: parsed.error,
      columns: [],
      rows: [],
      warnings: [],
      meta: {},
    })
    return
  }
  const hasCommands = parsed.commands.length > 0
  const text = hasCommands ? parsed.rest : source
  if (hasCommands) {
    draft.value = ''
    composerEl.value?.clear()
    const outcome = await runner.run(parsed.commands)
    if (outcome === 'stop') {
      composerEl.value?.setValue(draft.value)
      return
    }
    if (!text.trim() && pendingFiles.value.length === 0) return
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
  const attachments = [...pendingFiles.value]
  draft.value = ''
  composerEl.value?.clear()
  pendingFiles.value = []
  uploadError.value = ''
  const binding = chat.threadId ? commands.requestBinding(chat.threadId) : undefined
  chat.send(text, attachments, binding)
  if (chat.threadId) commands.clearBinding(chat.threadId)
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

// ---------- 命令系统 ----------
const bindingChips = computed<BindingChip[]>(() => {
  if (!chat.threadId) return []
  const binding = commands.bindingFor(chat.threadId)
  const chips: BindingChip[] = []
  if (binding.agentName || binding.agentId) {
    chips.push({ key: 'agent', label: 'Agent', value: binding.agentName || binding.agentId })
  }
  if (binding.groupName || binding.groupId) {
    chips.push({ key: 'group', label: '组', value: binding.groupName || binding.groupId })
  }
  if (binding.flowName || binding.flowId) {
    chips.push({ key: 'taskflow', label: '任务流', value: binding.flowName || binding.flowId })
  }
  binding.skills.forEach((value) => chips.push({ key: `skill:${value}`, label: '技能', value }))
  binding.mcp_ids.forEach((value) => chips.push({ key: `mcp:${value}`, label: 'MCP', value }))
  binding.plugin_ids.forEach((value) => chips.push({ key: `plugin:${value}`, label: '插件', value }))
  return chips
})

function removeBinding(key: string) {
  const threadId = chat.threadId
  if (!threadId) return
  const binding = commands.bindingFor(threadId)
  if (key === 'agent') commands.patchBinding(threadId, { agentId: '', agentName: '' })
  else if (key === 'group') commands.patchBinding(threadId, { groupId: '', groupName: '' })
  else if (key === 'taskflow') commands.patchBinding(threadId, { flowId: '', flowName: '' })
  else if (key.startsWith('skill:')) {
    commands.patchBinding(threadId, { skills: binding.skills.filter((item) => item !== key.slice(6)) })
  } else if (key.startsWith('mcp:')) {
    commands.patchBinding(threadId, { mcp_ids: binding.mcp_ids.filter((item) => item !== key.slice(4)) })
  } else if (key.startsWith('plugin:')) {
    commands.patchBinding(threadId, {
      plugin_ids: binding.plugin_ids.filter((item) => item !== key.slice(7)),
    })
  }
}

function lastAssistantMessage(): ChatMsg | null {
  return (
    [...chat.messages].reverse().find((item) => item.kind === 'assistant' && item.content) ||
    null
  )
}

function exportSession(format: 'md' | 'json') {
  const threadId = chat.threadId
  if (!threadId) {
    ElMessage.warning('当前没有会话')
    return
  }
  const rows = chat.messages.filter((item) => item.kind === 'user' || item.kind === 'assistant')
  const content =
    format === 'json'
      ? JSON.stringify(
          rows.map((item) => ({
            role: item.kind,
            content: item.content || '',
            at: item.at || null,
          })),
          null,
          2,
        )
      : rows
          .map((item) => `${item.kind === 'user' ? '用户' : '助手'}：${item.content || ''}`)
          .join('\n\n')
  const blob = new Blob([content], {
    type: `${format === 'json' ? 'application/json' : 'text/markdown'};charset=utf-8`,
  })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `session-${threadId.slice(0, 8)}.${format}`
  link.click()
  URL.revokeObjectURL(url)
  ElMessage.success('已导出当前会话')
}

const runner = useCommandRunner({
  deleteSession: async (threadId: string) => {
    await sessions.removeSession(threadId)
    if (chat.threadId === threadId) chat.newChat()
  },
  withdraw: async () => {
    const turn = turns.value[turns.value.length - 1]
    if (!turn || !canWithdraw(turn, turns.value.length - 1)) return false
    await withdrawLatest(turn, turns.value.length - 1)
    return true
  },
  newSession: async () => {
    const id = await sessions.createSession(
      undefined,
      undefined,
      chat.selectedAgentId || agents.defaultAgentId,
      undefined,
    )
    if (!id) return
    sessions.activeThreadId = id
    chat.activateThread(id)
    void files.prime(id)
  },
  clearView: () => chat.clearView(),
  rename: (title: string) =>
    chat.threadId ? sessions.renameSession(chat.threadId, title) : Promise.resolve(false),
  pin: (pinned: boolean) =>
    chat.threadId ? sessions.setPinned(chat.threadId, pinned) : Promise.resolve(false),
  archive: async () => {
    const threadId = chat.threadId
    if (!threadId) return false
    const ok = await sessions.setArchived(threadId, true)
    if (ok) chat.newChat()
    return ok
  },
  selectAgent: (name: string, team?: string) => {
    const target =
      name === 'default'
        ? agents.defaultAgentId
        : agents.items.find((item) => item.id === name)?.id ||
          agents.items.find((item) => item.name === name)?.id
    if (!target) return false
    chat.selectedAgentId = target
    if (team) {
      const group =
        agents.groups.find((item) => item.group_id === team) ||
        agents.groups.find((item) => item.name === team)
      chat.selectedGroupId = group?.group_id || ''
    } else {
      chat.selectedGroupId = ''
    }
    return true
  },
  copyLastAnswer: async () => {
    const message = lastAssistantMessage()
    if (!message) return false
    await copyMessage(message)
    return true
  },
  exportSession,
  showResult: (payload) => {
    commands.showTerminal(payload)
  },
})

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

// 归档会话只读：仍可回看聊天记录，但不再向 Agent 发送消息（与对话页一致）。
const archivedActive = computed(() =>
  sessions.isArchived(chat.threadId || sessions.activeThreadId),
)

/** 当前只读的归档会话恢复回侧边栏，输入区随之恢复。 */
async function restoreActiveSession(): Promise<void> {
  const threadId = chat.threadId
  if (!threadId) return
  await sessions.setArchived(threadId, false)
  ElMessage.success('已恢复会话')
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

// ---------- 右侧轮次导航 ----------
// 只为包含用户消息的轮次生成刻度：收起是一列刻度，悬停展开为该轮提问。
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

const navActive = ref(0)
const navOpen = ref(false)
const navList = ref<HTMLElement | null>(null)

function navAnchor(key: string): string {
  return `data-turn-id="${String(key).replace(/"/g, '')}"`
}

/** 高亮当前视口顶部所在的轮次；与刻度共用一个索引空间。 */
function updateNavActive(): void {
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
    // 顶部留 40px 容差：轮次刚滚到视口上沿时就算已切入该轮。
    if (node.getBoundingClientRect().top - base <= 40) active = index
  }
  navActive.value = active
}

/** 跳转到第 index 个轮次；保持用户阅读位置，不触发自动跟随到底部。 */
function jumpToTurn(index: number): void {
  const el = scroller.value
  const item = turnNav.value[index]
  if (!el || !item) return
  const node = el.querySelector<HTMLElement>(`[${navAnchor(item.key)}]`)
  if (!node) return
  const top =
    node.getBoundingClientRect().top - el.getBoundingClientRect().top + el.scrollTop - 10
  stickToBottom.value = false
  navActive.value = index
  el.scrollTo({ top: Math.max(0, top), behavior: 'smooth' })
}

/** 指针停在刻度上滚轮时，把滚动转交给消息区；展开态下让面板自己滚。 */
function onNavWheel(event: WheelEvent): void {
  if (navOpen.value) return
  const el = scroller.value
  if (!el) return
  event.preventDefault()
  el.scrollTop += event.deltaY
}

/** 展开后把当前轮次滚进面板可视区，避免长会话里看不到高亮项。 */
async function onNavOpenChange(open: boolean): Promise<void> {
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

// ---------- 回答底部：时间 / 复制 / 赞同 / 不赞同 / 重试（与对话页一致） ----------
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

// 反馈总开关；关闭时隐藏赞同/不赞同/重试，仅保留复制。
const feedbackEnabled = ref(true)
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
    await openThread(threadId)
    const withdrawn = result.content || turn.user.content || ''
    // 撤回内容放回输入框；若原本还有未发送草稿，保留在后面。
    const restored = draft.value.trim()
    draft.value = restored ? `${withdrawn}\n\n${restored}` : withdrawn
    await nextTick()
    composerEl.value?.focus()
    void sessions.refresh()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '撤回失败')
  }
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

// 运行中 / 等待介入 标识按 thread 同步到会话栏（与对话页一致）
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

// ---------- 生命周期 ----------
onMounted(async () => {
  chat.demoMode = false
  void agents.load()
  void agents.loadGroups()
  backendOnline.value = await probeHealth()
  if (backendOnline.value) {
    await sessions.refresh()
    void sessions.refreshArchived()
    formats.value = await fetchContextFormats()
    void settings.load()
    void fetchFeedbackSettings()
      .then((config) => {
        feedbackEnabled.value = config.enabled
      })
      .catch(() => undefined)
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

        <button class="as-icon" type="button" title="刷新窗口，重新拉取后端数据" @click="refreshPanel">
          <AppIcon name="refresh" :size="14" />
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
          :class="{
            'with-pins': Boolean(activeFlow || activeScheduler),
            'with-nav': turnNav.length > 1,
          }"
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

          <template v-for="(turn, turnIndex) in turns" :key="turn.key">
            <div v-if="turn.user" class="as-row user" :data-turn-id="turn.key">
              <div class="user-col">
                <div class="bubble user-bubble">
                  <div v-if="turn.user.attachments?.length" class="bubble-atts">
                    <template v-for="att in turn.user.attachments" :key="att.id">
                      <button
                        v-if="att.kind === 'image' && attachmentSrc(att)"
                        class="att-image"
                        type="button"
                        :title="`查看 ${att.name}`"
                        @click="openImagePreview(attachmentSrc(att), att.name)"
                      >
                        <img :src="attachmentSrc(att)" :alt="att.name" />
                      </button>
                      <span v-else class="bubble-att">
                        <FileTypeIcon :ext="att.ext" :name="att.name" :size="18" />
                        <span class="att-name">{{ att.name }}</span>
                      </span>
                    </template>
                  </div>
                  <CollapsibleText v-if="turn.user.content" :text="turn.user.content" />
                </div>
                <div v-if="turn.user.content || canWithdraw(turn, turnIndex)" class="msg-meta user">
                  <span
                    v-if="clockOf(turn.user.at)"
                    class="msg-time"
                    :title="timeTitle(turn.user.at)"
                  >
                    {{ clockOf(turn.user.at) }}
                  </span>
                  <button
                    v-if="turn.user.content"
                    class="meta-icon"
                    :class="{ active: copiedId === turn.user.id }"
                    :title="copiedId === turn.user.id ? '已复制' : '复制'"
                    :aria-label="copiedId === turn.user.id ? '已复制' : '复制'"
                    @click="copyMessage(turn.user)"
                  >
                    <AppIcon :name="copiedId === turn.user.id ? 'check' : 'copy'" :size="13" />
                  </button>
                  <button
                    v-if="canWithdraw(turn, turnIndex)"
                    class="meta-icon"
                    aria-label="撤回"
                    title="撤回这条消息"
                    @click="withdrawLatest(turn, turnIndex)"
                  >
                    <AppIcon name="undo" :size="13" />
                  </button>
                </div>
              </div>
            </div>

            <!-- 本轮答复者：头像 + 智能体名称单独一行，轨迹与回答都在其下 -->
            <div
              v-if="turn.final || turn.process.length || turn.live || turn.interrupt"
              class="as-row agent-row"
            >
              <span
                class="agent-avatar"
                :style="{ background: turnAgent(turn).gradient }"
              >
                {{ turnAgent(turn).initial }}
              </span>
              <strong class="agent-name">{{ turnAgent(turn).name }}</strong>
              <span v-if="turnAgent(turn).role" class="agent-role">{{ turnAgent(turn).role }}</span>
            </div>

            <div v-if="turn.process.length || turn.live" class="as-row tree-row">
              <TrajectoryTree
                :items="turn.process"
                :live="turn.live"
                :duration-ms="turnDuration(turn)"
              />
            </div>

            <div v-if="turn.final" class="as-row ai">
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
                <div v-if="turn.final.content && !turn.final.streaming" class="msg-meta">
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
                    <AppIcon :name="copiedId === turn.final.id ? 'check' : 'copy'" :size="13" />
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
                    <AppIcon name="thumbUp" :size="13" />
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
                    <AppIcon name="thumbDown" :size="13" />
                  </button>
                  <button
                    v-if="canRetry(turn, turnIndex)"
                    class="meta-icon"
                    aria-label="重新回答"
                    title="重新回答"
                    @click="handleRetry(turn)"
                  >
                    <AppIcon name="refresh" :size="13" />
                  </button>
                </div>
              </div>
            </div>

            <div v-if="turn.terminated" class="as-row terminate-row">
              <span>{{ terminateText(turn.terminated) }}</span>
            </div>

            <div v-if="turn.interrupt" class="as-row">
              <InterruptCard :msg="turn.interrupt" :readonly="archivedActive" />
            </div>

            <div v-for="note in turn.notes" :key="note.id" class="as-row note-row">
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
      </div>

      <footer v-if="!archivedActive" class="composer">
        <p v-if="uploadError" class="upload-error">{{ uploadError }}</p>

        <BindingBar
          v-if="bindingChips.length"
          :items="bindingChips"
          @remove="removeBinding"
        />

        <div class="composer-shell" @paste="onClipboardPaste">
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

          <CommandComposer
            ref="composerEl"
            v-model="draft"
            :disabled="chat.sending || chat.compacting"
            placeholder="输入消息，/ 唤起命令；Enter 发送"
            @submit="submit"
          />

          <div class="composer-tools">
            <button
              class="composer-tool"
              type="button"
              :disabled="uploading || chat.sending"
              title="添加文件 / 图片"
              @click="pickFiles"
            >
              <span v-if="uploading" class="attach-spin"></span>
              <el-icon v-else><Plus /></el-icon>
            </button>
            <input
              ref="fileInput"
              class="file-input"
              type="file"
              multiple
              :accept="fileAccept || formats.accept"
              @change="onFilesPicked"
            />

            <div class="composer-pop-wrap">
              <button
                class="composer-pill"
                :class="{ open: modesOpen, on: settings.thinking || settings.webSearch }"
                type="button"
                title="思考模式 / 联网搜索"
                @click="modesOpen = !modesOpen"
              >
                <AppIcon name="sliders" :size="12" />
                <span>工具</span>
              </button>
              <div v-if="modesOpen" class="composer-pop-mask" @click="modesOpen = false"></div>
              <Transition name="pop">
                <div v-if="modesOpen" class="composer-pop">
                  <label class="composer-pop-row">
                    <span>思考模式</span>
                    <el-switch
                      :model-value="settings.thinking"
                      size="small"
                      :disabled="chat.sending || settings.saving"
                      @update:model-value="(v: string | number | boolean) => toggleThinking(v === true)"
                    />
                  </label>
                  <label class="composer-pop-row">
                    <span>联网搜索</span>
                    <el-switch
                      :model-value="settings.webSearch"
                      size="small"
                      :disabled="chat.sending || settings.saving"
                      @update:model-value="(v: string | number | boolean) => toggleWebSearch(v === true)"
                    />
                  </label>
                </div>
              </Transition>
            </div>

            <!-- 智能体 / 组选择：收进「当前智能体」面板 -->
            <CurrentAgentPicker :disabled="archivedActive" />

            <span class="composer-spacer"></span>
            <span class="composer-hint">Enter 发送</span>
            <button
              v-if="chat.sending"
              class="composer-send is-stop"
              type="button"
              title="中止"
              @click="chat.stop()"
            >
              <span class="stop-square"></span>
            </button>
            <button
              v-else
              class="composer-send"
              type="button"
              title="发送"
              :disabled="(!draft.trim() && pendingFiles.length === 0) || uploading || chat.compacting"
              @click="submit"
            >
              <el-icon><ArrowUpBold /></el-icon>
            </button>
          </div>
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

      <!-- 归档会话：输入区替换为只读提示，避免误以为还能继续对话 -->
      <div v-else class="archived-readonly">
        <span class="ar-icon"><AppIcon name="archive" :size="14" /></span>
        <span class="ar-text">该会话已归档，仅支持查看聊天记录，不会再被 Agent 读取。</span>
        <el-button class="ar-restore" size="small" @click="restoreActiveSession">
          恢复会话
        </el-button>
      </div>

      <div v-if="dragging" class="drag-mask">
        <span class="drag-icon"><Document :size="22" /></span>
        <strong>松开以添加附件</strong>
        <small>支持图片与文档，可直接拖入整个助手窗口</small>
      </div>
    </section>

    <MediaViewer
      v-model="mediaOpen"
      kind="image"
      :src="mediaSrc"
      :title="mediaTitle"
      :filename="mediaFilename"
    />

    <CommandTerminal variant="panel" />
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

/* 右侧出现轮次导航时，给气泡留出安全距离，避免与刻度重叠 */
.as-messages.with-nav {
  padding-right: 26px;
}

/* 右侧轮次导航：收起时是一列紧凑刻度，悬停向左展开为可读列表 */
.turn-nav {
  position: absolute;
  top: 50%;
  right: 5px;
  transform: translateY(-50%);
  display: flex;
  flex-direction: column;
  justify-content: center;
  width: 14px;
  max-height: calc(100% - 16px);
  z-index: 11;
  overflow: hidden;
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  transition:
    width 0.16s ease,
    background 0.16s ease,
    border-color 0.16s ease;
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
  width: 14px;
  height: 11px;
  padding: 0;
  border: 0;
  background: transparent;
  cursor: pointer;
}

.turn-nav-bar {
  flex: none;
  display: block;
  width: 8px;
  height: 2px;
  border-radius: 1px;
  background: rgba(126, 154, 198, 0.34);
  transition:
    width 0.15s ease,
    background 0.15s ease;
}

.turn-nav-row:hover .turn-nav-bar {
  width: 12px;
  background: var(--text-2);
}

.turn-nav-row.active .turn-nav-bar {
  width: 12px;
  background: var(--accent-cyan);
  box-shadow: 0 0 6px rgba(55, 208, 196, 0.6);
}

.turn-nav-label {
  display: none;
  min-width: 0;
  overflow: hidden;
  font-size: 11.5px;
  line-height: 1.5;
  color: var(--text-2);
  text-align: right;
  white-space: nowrap;
  text-overflow: ellipsis;
}

/* 展开态：以刻度组为中心向左撑开，覆盖在消息区右侧 */
.turn-nav.open {
  right: 4px;
  width: 240px;
  padding: 7px 6px;
  border-color: rgba(55, 208, 196, 0.32);
  background: rgba(4, 16, 26, 0.97);
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.5);
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
  background: rgba(126, 154, 198, 0.3);
}

.turn-nav.open .turn-nav-row {
  width: 100%;
  height: auto;
  min-height: 26px;
  gap: 10px;
  padding: 4px 6px;
  border-radius: var(--radius-sm);
}

.turn-nav.open .turn-nav-row:hover {
  background: rgba(55, 208, 196, 0.1);
}

.turn-nav.open .turn-nav-label {
  display: block;
  text-align: left;
}

.turn-nav.open .turn-nav-row:hover .turn-nav-label {
  color: var(--text-1);
}

.turn-nav.open .turn-nav-row.active .turn-nav-label {
  color: var(--accent-cyan);
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

/* 用户气泡 + 下方复制栏的纵向容器 */
.user-col {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  max-width: 88%;
  min-width: 0;
}

.user-col .bubble {
  max-width: 100%;
}

.msg-meta.user {
  justify-content: flex-end;
  gap: 2px;
  margin-top: 2px;
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
  align-items: flex-end;
  gap: 6px;
  margin-bottom: 5px;
}

/* 图片附件：Claude 式圆角缩略图，点击打开全屏预览 */
.att-image {
  display: inline-flex;
  flex: none;
  max-width: 140px;
  max-height: 140px;
  padding: 0;
  overflow: hidden;
  cursor: zoom-in;
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 9px;
  background: rgba(255, 255, 255, 0.04);
  transition: border-color 0.15s ease, filter 0.15s ease;
}

.att-image img {
  display: block;
  width: auto;
  height: auto;
  max-width: 140px;
  max-height: 140px;
  object-fit: cover;
}

.att-image:hover {
  border-color: rgba(255, 255, 255, 0.3);
  filter: brightness(1.06);
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

.bubble-att .att-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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

/* 本轮答复者标识：头像 + 名称 + 角色，独占一行 */
.as-row.agent-row {
  align-items: center;
  gap: 7px;
  padding-left: 2px;
}

.agent-avatar {
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  flex: none;
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
  color: #041018;
  border-radius: 50%;
}

.agent-name {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-1);
}

.agent-role {
  font-size: 10px;
  color: var(--text-3);
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

/* 回答气泡底部：时间 + 复制 / 赞同 / 不赞同 / 重试 */
.msg-meta {
  display: flex;
  align-items: center;
  gap: 2px;
  margin-top: -2px;
}

.msg-time {
  margin-right: 4px;
  font-size: 10px;
  line-height: 20px;
  color: var(--text-3);
  font-variant-numeric: tabular-nums;
  user-select: none;
}

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
  background: rgba(4, 12, 22, 0.6);
}

/* 归档会话的只读底栏：替代输入框，避免误以为还能继续对话 */
.archived-readonly {
  position: relative;
  z-index: 5;
  flex: none;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px;
  border-top: 1px solid rgba(255, 182, 72, 0.16);
  background: rgba(255, 182, 72, 0.05);
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
  font-size: 10.5px;
  line-height: 1.5;
  color: var(--text-2);
}

.archived-readonly .ar-restore.el-button {
  flex: none;
  height: 26px;
  padding: 0 10px;
  font-size: 11px;
  color: var(--accent-cyan);
  border-color: rgba(55, 208, 196, 0.36);
  background: transparent;
}

.archived-readonly .ar-restore.el-button:hover {
  color: var(--accent-cyan);
  border-color: var(--accent-cyan);
  background: rgba(55, 208, 196, 0.1);
}

.pending-row {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  padding: 0 2px 6px;
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

