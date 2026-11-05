<script setup lang="ts">
// 对话左侧栏：动作区 + 项目分组 + 对话分组（无卡片、发丝线分区、可折叠）
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import AppIcon from '@/components/console/AppIcon.vue'
import SessionRow from './SessionRow.vue'
import { useSessionStore, type SessionItem } from '@/stores/sessions'
import { useWorkspaceStore } from '@/stores/workspace'
import { pickDirectory, projectNameFromPath, revealProject, type ProjectMode } from '@/api/workspace'
import {
  fetchMcpIntegrations,
  probeMcpServer,
  type McpServerItem,
} from '@/api/mcp'

const sessions = useSessionStore()
const workspace = useWorkspaceStore()

const emit = defineEmits<{
  select: [threadId: string]
  create: []
  remove: [threadId: string]
  rename: [threadId: string, title: string]
  pin: [threadId: string, pinned: boolean]
  'select-project': [projectId: string]
  'create-integration': [serverId: string]
  'open-archived': [threadId: string]
  'archived-removed': [threadId: string]
}>()

// ---------- 折叠状态（localStorage 记忆） ----------
const GROUPS_KEY = 'harness.sidebar.groups'
const EXPANDED_KEY = 'harness.sidebar.projects'

function readGroups(): { integrations: boolean; projects: boolean; sessions: boolean } {
  try {
    const raw = localStorage.getItem(GROUPS_KEY)
    if (raw) {
      const value = JSON.parse(raw) as {
        integrations?: boolean
        projects?: boolean
        sessions?: boolean
      }
      return {
        integrations: value.integrations !== false,
        projects: value.projects !== false,
        sessions: value.sessions !== false,
      }
    }
  } catch {
    /* 忽略损坏的本地数据 */
  }
  return { integrations: true, projects: true, sessions: true }
}

function readExpanded(): string[] {
  try {
    const raw = localStorage.getItem(EXPANDED_KEY)
    if (raw) {
      const value = JSON.parse(raw) as unknown
      if (Array.isArray(value)) return value.filter((id): id is string => typeof id === 'string')
    }
  } catch {
    /* 忽略损坏的本地数据 */
  }
  return []
}

const open = reactive(readGroups())
const expanded = ref<string[]>(readExpanded())
const projectMenu = ref<string | null>(null)
const archiveOpen = ref(false)
const notice = ref('')
let noticeTimer: number | undefined

// ---------- 集成的项目（用户自有后端 MCP） ----------
const integrations = ref<McpServerItem[]>([])
const selectedIntegration = ref('')
const integrationLoading = ref(false)
const integrationError = ref('')
const connectingIntegration = ref('')
let integrationPoll: number | undefined

watch(
  open,
  (value) => {
    try {
      localStorage.setItem(GROUPS_KEY, JSON.stringify(value))
    } catch {
      /* 本地存储不可用时忽略 */
    }
  },
  { deep: true },
)

watch(
  expanded,
  (value) => {
    try {
      localStorage.setItem(EXPANDED_KEY, JSON.stringify(value))
    } catch {
      /* 本地存储不可用时忽略 */
    }
  },
  { deep: true },
)

function toggleGroup(key: 'integrations' | 'projects' | 'sessions') {
  open[key] = !open[key]
}

/** 新建会话前先展开对话组，保证新会话立刻可见。 */
function requestCreate() {
  open.sessions = true
  emit('create')
}

function isExpanded(projectId: string) {
  return expanded.value.includes(projectId)
}

function toggleProject(projectId: string) {
  expanded.value = isExpanded(projectId)
    ? expanded.value.filter((id) => id !== projectId)
    : [...expanded.value, projectId]
}

/** 点击项目行：有对话就打开最近一条，没有对话只展开项目组。 */
function openProjectGroup(projectId: string) {
  const list = projectSessions(projectId)
  if (list.length === 0) {
    if (!isExpanded(projectId)) toggleProject(projectId)
    return
  }
  if (!isExpanded(projectId)) expanded.value = [...expanded.value, projectId]
  emit('select-project', projectId)
}

// ---------- 分组数据 ----------
// 默认工作区在运行时仍然存在，但按需求在侧边栏隐藏。
const visibleProjects = computed(() =>
  workspace.sorted.filter((project) => !project.is_default),
)
const projectIds = computed(() => new Set(visibleProjects.value.map((project) => project.id)))

const ungrouped = computed(() =>
  sessions.sessions.filter(
    (item) =>
      !item.integration_server_id &&
      (!item.project_id || !projectIds.value.has(item.project_id)),
  ),
)

function projectSessions(projectId: string) {
  return sessions.sessions.filter((item) => item.project_id === projectId)
}

function integrationSessions(serverId: string) {
  return sessions.sessions.filter(
    (item) => item.integration_server_id === serverId,
  )
}

function integrationConnected(item: McpServerItem) {
  return item.state === 'Connected' || item.state === 'Degraded'
}

function integrationHint(item: McpServerItem) {
  if (!item.enabled) return '集成项目已停用'
  if (!integrationConnected(item)) return '未连接：先连接后端 MCP 才能操作会话'
  return `${item.description || '用户自有后端'} · ${item.tool_count} 个工具`
}

async function loadIntegrations() {
  integrationLoading.value = true
  try {
    const data = await fetchMcpIntegrations()
    integrations.value = data.items
    integrationError.value = ''
    // 选中的后端被删除或改名后，清空选择，避免操作落到不存在的项目。
    if (
      selectedIntegration.value &&
      !data.items.some((item) => item.server_id === selectedIntegration.value)
    ) {
      selectedIntegration.value = ''
    }
  } catch (err) {
    integrationError.value = (err as Error).message
  } finally {
    integrationLoading.value = false
  }
}

async function connectIntegration(item: McpServerItem) {
  if (connectingIntegration.value) return
  connectingIntegration.value = item.server_id
  try {
    const result = await probeMcpServer(item.server_id)
    const detail = result.server
    const index = integrations.value.findIndex(
      (entry) => entry.server_id === item.server_id,
    )
    if (index >= 0) integrations.value[index] = detail
    if (detail.state === 'Connected' || detail.state === 'Degraded') {
      open.integrations = true
      selectedIntegration.value = item.server_id
      flash(`${item.server_id} 已连接，可以新建会话`)
    } else {
      flash(`${item.server_id} 连接失败：${detail.error || detail.state}`)
    }
  } catch (err) {
    flash((err as Error).message)
  } finally {
    connectingIntegration.value = ''
  }
}

/** 点击集成项目：选中的后端才展示其会话；未连接时禁止操作。 */
function selectIntegration(item: McpServerItem) {
  if (!integrationConnected(item)) return
  selectedIntegration.value =
    selectedIntegration.value === item.server_id ? '' : item.server_id
  if (selectedIntegration.value) open.integrations = true
}

function createIntegrationSession(item: McpServerItem) {
  if (!integrationConnected(item)) return
  selectedIntegration.value = item.server_id
  open.integrations = true
  emit('create-integration', item.server_id)
}

// ---------- 动作 ----------
function flash(message: string) {
  notice.value = message
  if (noticeTimer) window.clearTimeout(noticeTimer)
  noticeTimer = window.setTimeout(() => (notice.value = ''), 2400)
}

// ---------- 页内弹窗（Electron 不支持 window.prompt） ----------
interface DialogState {
  kind: 'input' | 'confirm' | 'alert'
  title: string
  message: string
  value: string
  danger: boolean
}

const dialog = ref<DialogState | null>(null)
const dialogInput = ref<HTMLInputElement | null>(null)
let dialogResolve: ((value: string | boolean | null) => void) | null = null

function openDialog(state: DialogState, resultOnClose: string | boolean | null) {
  dialog.value = state
  void nextTick(() => dialogInput.value?.focus())
  return new Promise<string | boolean | null>((resolve) => {
    dialogResolve = (value) => resolve(value === null ? resultOnClose : value)
  })
}

function closeDialog(result: string | boolean | null) {
  const resolve = dialogResolve
  dialogResolve = null
  dialog.value = null
  resolve?.(result)
}

function submitDialog() {
  if (!dialog.value) return
  if (dialog.value.kind === 'input') {
    const value = dialog.value.value.trim()
    closeDialog(value || null)
    return
  }
  closeDialog(true)
}

/** 提醒类弹窗只能点确定关闭；输入/确认类允许点遮罩取消。 */
function cancelDialog() {
  if (!dialog.value || dialog.value.kind === 'alert') return
  closeDialog(dialog.value.kind === 'input' ? null : false)
}

async function askInput(title: string, defaultValue = '', message = '') {
  const result = await openDialog(
    { kind: 'input', title, message, value: defaultValue, danger: false },
    null,
  )
  return typeof result === 'string' ? result : null
}

async function askConfirm(title: string, message: string, danger = false) {
  const result = await openDialog(
    { kind: 'confirm', title, message, value: '', danger },
    false,
  )
  return result === true
}

async function askAlert(title: string, message: string) {
  await openDialog({ kind: 'alert', title, message, value: '', danger: false }, true)
}

async function createProject(mode: ProjectMode) {
  // 新建项目 = 选择一个父目录 + 输入新目录名；选择已有目录 = 直接附着。
  const base = await pickDirectory()
  if (!base) return

  let path = base
  let name = projectNameFromPath(base)
  if (mode === 'create') {
    const input = await askInput(
      '新建项目',
      '新项目',
      '将在所选目录下创建同名子目录',
    )
    const cleaned = (input || '').trim()
    if (!cleaned) return
    if (/[\\/:*?"<>|]/.test(cleaned)) {
      flash('项目名称不能包含 \\ / : * ? " < > |')
      return
    }
    const separator = base.includes('\\') ? '\\' : '/'
    path = `${base.replace(/[\\/]+$/, '')}${separator}${cleaned}`
    name = cleaned
  }

  let project = await workspace.create(mode, path, name, false)
  // 目录已带标记但被其他项目登记时，允许用户显式接管。
  if (!project && workspace.error.includes('已被另一个项目登记')) {
    if (await askConfirm('接管项目目录', `目录已被另一个项目登记：\n${path}\n\n是否接管该目录？`)) {
      project = await workspace.create(mode, path, name, true)
    }
  }
  if (!project) {
    await askAlert('项目创建失败', workspace.error || '未知错误')
    return
  }
  open.projects = true
  if (!isExpanded(project.id)) expanded.value = [...expanded.value, project.id]
  flash(mode === 'create' ? `已创建项目 ${project.name}` : `已添加项目 ${project.name}`)
}



async function renameProject(projectId: string) {
  const current = workspace.projects.find((project) => project.id === projectId)
  if (!current) return
  const next = await askInput('重命名项目', current.name)
  if (!next || !next.trim()) return
  projectMenu.value = null
  if (!(await workspace.update(projectId, { name: next.trim() }))) {
    flash(workspace.error || '重命名失败')
  }
}

async function toggleProjectPin(projectId: string) {
  const current = workspace.projects.find((project) => project.id === projectId)
  if (!current) return
  projectMenu.value = null
  if (!(await workspace.update(projectId, { pinned: !current.pinned }))) {
    flash(workspace.error || '更新置顶失败')
  }
}

async function repairProject(projectId: string) {
  projectMenu.value = null
  if (await workspace.validate(projectId, true)) {
    flash('项目标记已校验并修复')
  } else {
    flash(workspace.error || '项目修复失败')
  }
}

async function openProject(path: string) {
  projectMenu.value = null
  if (!(await revealProject(path))) {
    flash(path)
  }
}

async function removeProject(projectId: string, isDefault: boolean) {
  if (isDefault) {
    flash('默认项目不可删除')
    return
  }
  if (!(await askConfirm('移出项目', '仅从列表移除项目登记，不会删除磁盘目录。是否继续？', true))) return
  projectMenu.value = null
  if (await workspace.remove(projectId)) {
    flash('已移出项目（磁盘目录未删除）')
  } else {
    flash(workspace.error || '移出项目失败')
  }
}

async function copyProjectPath(projectId: string) {
  const path = workspace.projects.find((project) => project.id === projectId)?.path
  if (!path) return
  try {
    await navigator.clipboard.writeText(path)
    flash('项目路径已复制')
  } catch {
    flash(path)
  }
}

async function createSessionInProject(projectId: string) {
  projectMenu.value = null
  const id = await sessions.createSession(projectId)
  if (!id) {
    flash('新建项目会话失败')
    return
  }
  if (!isExpanded(projectId)) {
    open.projects = true
    expanded.value = [...expanded.value, projectId]
  }
  emit('select', id)
}

// ---------- 归档会话 ----------
function fmtArchiveTime(value: string | null): string {
  if (!value) return '未知时间'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '未知时间'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

/** 归档会话如果属于本机项目，定位到对应磁盘目录；数据库会话不展示路径。 */
function archivedProject(item: SessionItem) {
  if (!item.project_id) return null
  return workspace.projects.find((project) => project.id === item.project_id) ?? null
}

function archivedLocation(item: SessionItem): string {
  const project = archivedProject(item)
  if (project) return project.path
  if (item.integration_server_id) return `集成项目 · ${item.integration_server_id}`
  return ''
}

async function openArchivedLocation(item: SessionItem) {
  const project = archivedProject(item)
  if (!project) return
  if (!(await revealProject(project.path))) flash(project.path)
}

function openArchiveDialog() {
  archiveOpen.value = true
  void sessions.refreshArchived()
}

function closeArchiveDialog() {
  archiveOpen.value = false
}

async function archiveSession(threadId: string) {
  // 运行中或等待人工介入的会话先不归档，避免归档后仍有 Agent 写入。
  if (sessions.runningThreads.has(threadId) || sessions.waitingThreads.has(threadId)) {
    flash('会话正在运行或等待人工介入，结束后再归档')
    return
  }
  const ok = await askConfirm(
    '归档会话',
    '归档后该会话会从侧边栏隐藏，并且不再被 Agent 读取；可随时在「归档会话」中恢复。是否继续？',
  )
  if (!ok) return
  if (await sessions.setArchived(threadId, true)) {
    flash('已归档，可在底部「归档会话」中恢复')
  } else {
    flash('归档失败')
  }
}

async function restoreArchived(threadId: string) {
  if (await sessions.setArchived(threadId, false)) {
    flash('已恢复会话')
  } else {
    flash('恢复失败')
  }
}

async function removeArchived(threadId: string) {
  if (!(await askConfirm('永久删除归档会话', '将永久删除该会话的记录和日志，且无法恢复。是否继续？', true))) {
    return
  }
  if (await sessions.removeSession(threadId)) {
    // 若删除的是正在只读查看的归档会话，通知对话区清理本地状态。
    emit('archived-removed', threadId)
    flash('已永久删除归档会话')
  } else {
    flash('删除失败')
  }
}

function viewArchived(threadId: string) {
  archiveOpen.value = false
  emit('open-archived', threadId)
}

// 当前会话变化时，自动展开对话组；属于项目时同时展开该项目，
// 避免选中的会话被折叠隐藏。
watch(
  () => sessions.activeThreadId,
  (threadId) => {
    if (!threadId) return
    open.sessions = true
    const item = sessions.sessions.find((session) => session.thread_id === threadId)
    if (!item?.project_id) return
    open.projects = true
    if (!isExpanded(item.project_id)) {
      expanded.value = [...expanded.value, item.project_id]
    }
  },
)

onMounted(() => {
  void workspace.refresh()
  void loadIntegrations()
  void sessions.refreshArchived()
  // 连接状态来自 MCP 运行时；这里只刷新列表，不触发任何连接。
  integrationPoll = window.setInterval(() => {
    void loadIntegrations()
  }, 10000)
})

onBeforeUnmount(() => {
  if (integrationPoll) window.clearInterval(integrationPoll)
})
</script>

<template>
  <aside class="session-panel">
    <div class="sp-actions side-stack">
      <button class="side-action primary" type="button" @click="requestCreate">
        <AppIcon name="plus" :size="14" />
        <span>新建会话</span>
      </button>
      <button class="side-action" type="button" @click="createProject('create')">
        <AppIcon name="folder" :size="14" />
        <span>新建项目</span>
      </button>
    </div>

    <div class="sp-scroll">
      <!-- 集成的项目：用户自有后端 MCP；未连接时禁止操作 -->
      <section class="group">
        <header class="group-head" @click="toggleGroup('integrations')">
          <span class="chev" :class="{ open: open.integrations }">
            <AppIcon name="chevronRight" :size="11" />
          </span>
          <span class="group-label">集成的项目</span>
          <span class="count mono">{{ integrations.length }}</span>
        </header>

        <div v-show="open.integrations" class="group-body">
          <div
            v-for="item in integrations"
            :key="item.server_id"
            class="integration-block"
          >
            <div
              class="integration-row"
              :class="{
                on: integrationConnected(item) && item.enabled,
                selected: selectedIntegration === item.server_id,
                disabled: !integrationConnected(item) || !item.enabled,
              }"
              role="button"
              :tabindex="integrationConnected(item) && item.enabled ? 0 : -1"
              @click="selectIntegration(item)"
              @keydown.enter="selectIntegration(item)"
            >
              <span
                class="int-signal"
                :class="{ on: integrationConnected(item) && item.enabled }"
                :title="integrationConnected(item) ? '后端 MCP 已连接' : '后端 MCP 未连接'"
              >
                <i></i>
              </span>
              <span class="i-main">
                <span class="i-name">{{ item.server_id }}</span>
                <span class="i-hint">{{ integrationHint(item) }}</span>
              </span>
              <span
                v-if="!integrationConnected(item) && item.enabled"
                class="i-connect"
                @click.stop="connectIntegration(item)"
              >
                {{ connectingIntegration === item.server_id ? '连接中…' : '连接' }}
              </span>
              <span
                v-else-if="integrationConnected(item) && item.enabled"
                class="i-new"
                title="在该后端新建会话"
                @click.stop="createIntegrationSession(item)"
              >
                <AppIcon name="chatPlus" :size="12" />
              </span>
              <span
                class="p-chev"
                :class="{ open: selectedIntegration === item.server_id }"
                @click.stop="selectIntegration(item)"
              >
                <AppIcon name="chevronRight" :size="10" />
              </span>
            </div>

            <div
              v-if="selectedIntegration === item.server_id && integrationConnected(item)"
              class="integration-sessions"
            >
              <SessionRow
                v-for="session in integrationSessions(item.server_id)"
                :key="session.thread_id"
                :session="session"
                :active="sessions.activeThreadId === session.thread_id"
                :running="sessions.runningThreads.has(session.thread_id)"
                :waiting="sessions.waitingThreads.has(session.thread_id)"
                indent
                @select="emit('select', $event)"
                @rename="(threadId, title) => emit('rename', threadId, title)"
                @remove="emit('remove', $event)"
                @pin="(threadId, pinned) => emit('pin', threadId, pinned)"
                @archive="archiveSession"
              />
              <p v-if="integrationSessions(item.server_id).length === 0" class="empty">
                暂无会话，点击右侧按钮新建
              </p>
            </div>
          </div>

          <p v-if="integrationError" class="empty">{{ integrationError }}</p>
          <p v-else-if="integrationLoading && integrations.length === 0" class="empty">加载中…</p>
          <p v-else-if="integrations.length === 0" class="empty">
            暂无集成项目。在 MCP 服务页新增服务时打开「集成项目」即可。
          </p>
        </div>
      </section>

      <!-- 项目分组 -->
      <section class="group">
        <header class="group-head" @click="toggleGroup('projects')">
          <span class="chev" :class="{ open: open.projects }">
            <AppIcon name="chevronRight" :size="11" />
          </span>
          <span class="group-label">项目</span>
          <span class="count mono">{{ visibleProjects.length }}</span>
        </header>

        <div v-show="open.projects" class="group-body">
          <div v-for="project in visibleProjects" :key="project.id" class="project-block">
            <div class="project-row" role="button" tabindex="0" @click="openProjectGroup(project.id)" @keydown.enter="openProjectGroup(project.id)">
              <AppIcon name="folder" :size="12" />
              <span class="p-name">{{ project.name }}</span>
              <span
                v-if="project.status !== 'active'"
                class="p-status"
                :class="project.status"
                :title="`标记状态：${project.marker_state}`"
              >
                {{ project.status === 'unavailable' ? '目录丢失' : '需修复' }}
              </span>
              <span class="p-hover">
                <button
                  class="mini"
                  type="button"
                  title="项目操作"
                  @click.stop="projectMenu = project.id"
                >…</button>
              </span>
              <span class="p-chev" :class="{ open: isExpanded(project.id) }" @click.stop="toggleProject(project.id)">
                <AppIcon name="chevronRight" :size="10" />
              </span>
            </div>

            <div v-if="isExpanded(project.id)" class="project-sessions">
              <SessionRow
                v-for="item in projectSessions(project.id)"
                :key="item.thread_id"
                :session="item"
                :active="sessions.activeThreadId === item.thread_id"
                :running="sessions.runningThreads.has(item.thread_id)"
                :waiting="sessions.waitingThreads.has(item.thread_id)"
                indent
                @select="emit('select', $event)"
                @rename="(threadId, title) => emit('rename', threadId, title)"
                @remove="emit('remove', $event)"
                @pin="(threadId, pinned) => emit('pin', threadId, pinned)"
                @archive="archiveSession"
              />
              <p v-if="projectSessions(project.id).length === 0" class="empty">暂无对话</p>
            </div>

            <div v-if="projectMenu === project.id" class="menu-mask" @click.stop="projectMenu = null"></div>
            <div v-if="projectMenu === project.id" class="p-menu" @click.stop>
              <button type="button" @click="createSessionInProject(project.id)">
                <AppIcon name="chatPlus" :size="11" /><span>在该项目下新建对话</span>
              </button>
              <button type="button" @click="renameProject(project.id)">
                <AppIcon name="edit" :size="11" /><span>重命名</span>
              </button>
              <button type="button" @click="toggleProjectPin(project.id)">
                <AppIcon :name="project.pinned ? 'unpin' : 'pin'" :size="11" />
                <span>{{ project.pinned ? '取消置顶' : '置顶' }}</span>
              </button>
              <button type="button" @click="repairProject(project.id)">
                <AppIcon name="check" :size="11" /><span>重新校验 / 修复标记</span>
              </button>
              <button type="button" @click="openProject(project.path)">
                <AppIcon name="folder" :size="11" /><span>在资源管理器中打开</span>
              </button>
              <button type="button" @click="copyProjectPath(project.id); projectMenu = null">
                <AppIcon name="copy" :size="11" /><span>复制目录路径</span>
              </button>
              <button
                v-if="!project.is_default"
                type="button"
                class="danger"
                @click="removeProject(project.id, project.is_default)"
              >
                <AppIcon name="trash" :size="11" /><span>移出项目</span>
              </button>
              <p class="p-path mono">{{ project.path }}</p>
            </div>
          </div>

          <p v-if="workspace.error && visibleProjects.length === 0" class="empty">
            {{ workspace.error }}
          </p>
        </div>
      </section>

      <!-- 对话分组：未归属项目的会话 -->
      <section class="group">
        <header class="group-head" @click="toggleGroup('sessions')">
          <span class="chev" :class="{ open: open.sessions }">
            <AppIcon name="chevronRight" :size="11" />
          </span>
          <span class="group-label">对话</span>
          <span class="count mono">{{ ungrouped.length }}</span>
        </header>

        <div v-show="open.sessions" class="group-body">
          <SessionRow
            v-for="item in ungrouped"
            :key="item.thread_id"
            :session="item"
            :active="sessions.activeThreadId === item.thread_id"
            :running="sessions.runningThreads.has(item.thread_id)"
            :waiting="sessions.waitingThreads.has(item.thread_id)"
            @select="emit('select', $event)"
            @rename="(threadId, title) => emit('rename', threadId, title)"
            @remove="emit('remove', $event)"
            @pin="(threadId, pinned) => emit('pin', threadId, pinned)"
            @archive="archiveSession"
          />
          <p v-if="sessions.offline" class="empty">后端离线，会话列表不可用</p>
          <p v-else-if="sessions.loading && sessions.sessions.length === 0" class="empty">加载中…</p>
          <p v-else-if="ungrouped.length === 0" class="empty">
            {{ workspace.projects.length ? '暂无未分组对话' : '还没有对话，发一条消息开始' }}
          </p>
        </div>
      </section>
    </div>

    <button class="archive-entry" type="button" @click="openArchiveDialog">
      <AppIcon name="archive" :size="14" />
      <span>归档会话</span>
      <span v-if="sessions.archivedSessions.length" class="arc-badge mono">
        {{ sessions.archivedSessions.length }}
      </span>
    </button>

    <p v-if="notice" class="sp-notice mono">{{ notice }}</p>

    <!-- 归档会话管理：查看 / 恢复 / 永久删除 -->
    <div v-if="archiveOpen" class="archive-mask" @click.self="closeArchiveDialog">
      <div class="archive-box" role="dialog" aria-modal="true">
        <header class="archive-head">
          <div class="archive-heading">
            <strong>归档会话</strong>
            <p>归档会话不会出现在侧边栏，也不会被 Agent 读取。</p>
          </div>
          <button class="archive-close" type="button" title="关闭" @click="closeArchiveDialog">
            <AppIcon name="close" :size="12" />
          </button>
        </header>

        <div class="archive-list">
          <div
            v-for="item in sessions.archivedSessions"
            :key="item.thread_id"
            class="archive-item"
          >
            <div class="arc-main">
              <div class="arc-title-row">
                <AppIcon name="archive" :size="11" />
                <strong>{{ item.title || '新会话' }}</strong>
                <span class="arc-count mono">{{ item.message_count }} 条</span>
              </div>
              <div class="arc-meta mono">
                <span>归档于 {{ fmtArchiveTime(item.archived_at) }}</span>
                <button
                  v-if="archivedProject(item)"
                  class="arc-path"
                  type="button"
                  :title="archivedLocation(item)"
                  @click="openArchivedLocation(item)"
                >
                  · {{ archivedLocation(item) }}
                </button>
                <span v-else-if="archivedLocation(item)">· {{ archivedLocation(item) }}</span>
              </div>
            </div>
            <div class="arc-actions">
              <button type="button" @click="viewArchived(item.thread_id)">查看</button>
              <button type="button" @click="restoreArchived(item.thread_id)">恢复</button>
              <button class="danger" type="button" @click="removeArchived(item.thread_id)">
                删除
              </button>
            </div>
          </div>

          <p v-if="sessions.archivedLoading && sessions.archivedSessions.length === 0" class="empty">
            加载中…
          </p>
          <p v-else-if="sessions.archivedSessions.length === 0" class="empty">
            暂无归档会话。会话行的「更多」菜单里可以选择归档。
          </p>
        </div>
      </div>
    </div>

    <!-- 页内弹窗：Electron 渲染进程不支持 window.prompt / confirm -->
    <div v-if="dialog" class="dialog-mask" @click.self="cancelDialog">
      <div class="dialog-box" role="dialog" aria-modal="true">
        <strong class="dialog-title">{{ dialog.title }}</strong>
        <p v-if="dialog.message" class="dialog-msg">{{ dialog.message }}</p>
        <input
          v-if="dialog.kind === 'input'"
          ref="dialogInput"
          v-model="dialog.value"
          class="dialog-input mono"
          type="text"
          @keydown.enter="submitDialog"
          @keydown.esc="closeDialog(null)"
        />
        <div class="dialog-actions">
          <button v-if="dialog.kind !== 'alert'" type="button" class="dlg-btn" @click="cancelDialog">
            取消
          </button>
          <button type="button" class="dlg-btn primary" :class="{ danger: dialog.danger }" @click="submitDialog">
            确定
          </button>
        </div>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.session-panel {
  width: var(--sidebar-w);
  flex: none;
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: transparent;
  border-right: 1px solid var(--line);
  overflow: hidden;
}

/* 顶部动作区：垂直排列的入口 */
.sp-actions {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px 10px;
  border-bottom: 1px solid var(--line);
}

.side-action {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  height: 34px;
  padding: 0 11px;
  font-size: 12px;
  text-align: left;
  color: var(--text-2);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: transparent;
}

.side-action:hover {
  color: var(--text-1);
  border-color: var(--line-strong);
  background: rgba(91, 140, 255, 0.06);
}

.side-action :deep(svg) {
  color: var(--text-3);
}

.side-action.primary {
  color: var(--text-1);
  border-color: rgba(91, 140, 255, 0.3);
  background: rgba(91, 140, 255, 0.08);
}

.side-action.primary :deep(svg) {
  color: var(--accent);
}

.side-action.primary:hover {
  border-color: var(--accent);
  background: rgba(91, 140, 255, 0.14);
}

/* 滚动区 */
.sp-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 4px 0 12px;
}

.group + .group {
  margin-top: 4px;
  border-top: 1px solid var(--line);
}

.group-head {
  position: relative;
  display: flex;
  align-items: center;
  gap: 5px;
  height: 30px;
  padding: 0 10px;
  font-size: 11.5px;
  color: var(--text-3);
  cursor: pointer;
  user-select: none;
  transition: color 0.15s ease;
}

.group-head:hover {
  color: var(--text-2);
}

.chev {
  display: grid;
  place-items: center;
  transition: transform 0.18s ease;
  color: var(--text-3);
}

.chev.open {
  transform: rotate(90deg);
}

.count {
  font-size: 10px;
  color: var(--text-3);
}

.group-tool {
  display: grid;
  place-items: center;
  width: 20px;
  height: 20px;
  margin-left: auto;
  padding: 0;
  color: var(--text-3);
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
}

.group-tool:hover {
  color: var(--accent-cyan);
  background: var(--bg-hover);
}

.group-body {
  padding: 2px 4px 8px;
}

/* 集成的项目行 */
.integration-block {
  position: relative;
}

.integration-row {
  display: flex;
  align-items: center;
  gap: 7px;
  min-height: 38px;
  padding: 3px 8px;
  color: var(--text-2);
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}

.integration-row:hover {
  color: var(--text-1);
  background: var(--bg-hover);
}

.integration-row.selected {
  color: var(--text-1);
  background: rgba(55, 208, 196, 0.08);
}

.integration-row.disabled {
  cursor: not-allowed;
  opacity: 0.62;
}

.integration-row.disabled:hover {
  color: var(--text-2);
  background: transparent;
}

.int-signal {
  display: grid;
  place-items: center;
  width: 14px;
  height: 14px;
  flex: none;
}

.int-signal i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #4b5b7a;
  border: 1px solid rgba(255, 255, 255, 0.12);
}

.int-signal.on i {
  background: var(--accent-cyan);
  box-shadow: 0 0 9px var(--accent-cyan);
}

.i-main {
  display: flex;
  flex-direction: column;
  gap: 1px;
  flex: 1;
  min-width: 0;
}

.i-name {
  overflow: hidden;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.i-hint {
  overflow: hidden;
  font-size: 9.5px;
  color: var(--text-3);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.i-connect {
  flex: none;
  padding: 2px 7px;
  font-size: 9.5px;
  color: var(--accent-cyan);
  border: 1px solid rgba(55, 208, 196, 0.34);
  border-radius: var(--radius-sm);
}

.i-connect:hover {
  background: rgba(55, 208, 196, 0.1);
}

.i-new {
  display: grid;
  place-items: center;
  width: 20px;
  height: 20px;
  flex: none;
  color: var(--text-3);
  border-radius: var(--radius-sm);
}

.i-new:hover {
  color: var(--accent-cyan);
  background: var(--bg-hover);
}

.integration-sessions {
  padding-bottom: 2px;
}

/* 项目行 */
.project-block {
  position: relative;
}

.project-row {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 8px;
  color: var(--text-2);
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}

.project-row:hover {
  color: var(--text-1);
  background: var(--bg-hover);
}

.project-row :deep(svg) {
  color: var(--text-3);
}

.p-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.p-status {
  flex: none;
  padding: 1px 5px;
  font-size: 9px;
  border: 1px solid rgba(255, 182, 72, 0.4);
  border-radius: 999px;
  color: var(--amber);
  background: rgba(255, 182, 72, 0.08);
}

.p-status.unavailable {
  color: var(--danger);
  border-color: rgba(255, 96, 96, 0.4);
  background: rgba(255, 96, 96, 0.08);
}

.p-hover {
  opacity: 0;
  transition: opacity 0.15s ease;
}

.project-row:hover .p-hover {
  opacity: 1;
}

.mini {
  width: 20px;
  height: 20px;
  padding: 0;
  font-size: 12px;
  line-height: 1;
  color: var(--text-3);
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
}

.mini:hover {
  color: var(--accent-cyan);
  background: var(--bg-hover);
}

.p-chev {
  display: grid;
  place-items: center;
  color: var(--text-3);
  transition: transform 0.18s ease;
}

.p-chev.open {
  transform: rotate(90deg);
}

.project-sessions {
  padding-bottom: 2px;
}

.empty {
  margin: 0;
  padding: 6px 10px 8px;
  font-size: 10.5px;
  line-height: 1.6;
  color: var(--text-3);
}

/* 项目操作菜单 */
.menu-mask {
  position: fixed;
  inset: 0;
  z-index: 35;
}

.p-menu {
  position: absolute;
  top: 30px;
  right: 6px;
  z-index: 36;
  display: flex;
  flex-direction: column;
  gap: 1px;
  width: 196px;
  padding: 4px;
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-md);
  background: var(--bg-elevated);
  box-shadow: 0 8px 24px rgba(2, 6, 14, 0.5);
}

.p-menu button {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 6px 8px;
  font-size: 11px;
  text-align: left;
  color: var(--text-2);
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
}

.p-menu button:hover {
  color: var(--text-1);
  background: var(--bg-hover);
}

.p-menu button :deep(svg) {
  color: var(--accent-cyan);
  flex: none;
}

.p-menu button.danger {
  color: var(--danger);
}

.p-menu button.danger :deep(svg) {
  color: var(--danger);
}

.p-path {
  margin: 2px 6px 4px;
  overflow: hidden;
  font-size: 9.5px;
  color: var(--text-3);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sp-notice {
  margin: 0;
  padding: 6px 10px;
  font-size: 9.5px;
  color: var(--accent-cyan);
  border-top: 1px solid var(--line);
  background: transparent;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 侧边栏底部的「归档会话」入口 */
.archive-entry {
  display: flex;
  align-items: center;
  gap: 7px;
  flex: none;
  width: calc(100% - 20px);
  margin: 6px 10px 8px;
  padding: 6px 10px;
  font-size: 11px;
  text-align: left;
  color: var(--text-2);
  border: 1px dashed var(--line-strong);
  border-radius: var(--radius-sm);
  background: transparent;
}

.archive-entry:hover {
  color: var(--text-1);
  border-color: var(--accent);
  background: rgba(91, 140, 255, 0.06);
}

.archive-entry :deep(svg) {
  color: var(--accent-cyan);
}

.arc-badge {
  margin-left: auto;
  padding: 0 6px;
  font-size: 9.5px;
  line-height: 16px;
  color: var(--accent-cyan);
  border-radius: 999px;
  background: rgba(55, 208, 196, 0.12);
}

/* 归档会话管理弹窗 */
.archive-mask {
  position: fixed;
  inset: 0;
  z-index: 58;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(2, 6, 14, 0.55);
  backdrop-filter: blur(3px);
}

.archive-box {
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: min(520px, 100%);
  max-height: min(520px, 82vh);
  padding: 14px;
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-md);
  background: var(--bg-elevated);
  box-shadow: 0 16px 40px rgba(2, 6, 14, 0.55);
}

.archive-head {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}

.archive-heading {
  flex: 1;
  min-width: 0;
}

.archive-heading strong {
  font-size: 13px;
  color: var(--text-1);
}

.archive-heading p {
  margin: 2px 0 0;
  font-size: 10.5px;
  color: var(--text-3);
}

.archive-close {
  display: grid;
  place-items: center;
  width: 22px;
  height: 22px;
  flex: none;
  padding: 0;
  color: var(--text-3);
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
}

.archive-close:hover {
  color: var(--text-1);
  background: var(--bg-hover);
}

.archive-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-height: 0;
  overflow-y: auto;
}

.archive-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: rgba(255, 255, 255, 0.02);
}

.arc-main {
  flex: 1;
  min-width: 0;
}

.arc-title-row {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.arc-title-row strong {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  font-size: 12px;
  font-weight: 500;
  color: var(--text-1);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.arc-title-row :deep(svg) {
  flex: none;
  color: var(--accent-cyan);
}

.arc-count {
  flex: none;
  font-size: 9.5px;
  color: var(--text-3);
}

.arc-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 3px;
  overflow: hidden;
  font-size: 9.5px;
  color: var(--text-3);
  white-space: nowrap;
}

.arc-meta > span {
  overflow: hidden;
  text-overflow: ellipsis;
}

.arc-path {
  flex: none;
  padding: 0;
  overflow: hidden;
  font-family: var(--font-mono);
  font-size: inherit;
  color: var(--accent-cyan);
  text-overflow: ellipsis;
  white-space: nowrap;
  border: 0;
  background: transparent;
}

.arc-path:hover {
  text-decoration: underline;
}

.arc-actions {
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex: none;
}

.arc-actions button {
  min-width: 52px;
  padding: 3px 8px;
  font-size: 10.5px;
  color: var(--text-2);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: transparent;
}

.arc-actions button:hover {
  color: var(--text-1);
  border-color: var(--line-strong);
  background: var(--bg-hover);
}

.arc-actions button.danger {
  color: var(--danger);
  border-color: rgba(255, 96, 96, 0.4);
}

.arc-actions button.danger:hover {
  background: rgba(255, 96, 96, 0.1);
}

.archive-list .empty {
  text-align: center;
}

/* 页内弹窗 */
.dialog-mask {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(2, 6, 14, 0.55);
  backdrop-filter: blur(3px);
}

.dialog-box {
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: min(360px, 100%);
  padding: 16px;
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-md);
  background: var(--bg-elevated);
  box-shadow: 0 16px 40px rgba(2, 6, 14, 0.55);
}

.dialog-title {
  font-size: 13px;
  color: var(--text-1);
}

.dialog-msg {
  margin: 0;
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--text-2);
  white-space: pre-wrap;
}

.dialog-input {
  width: 100%;
  height: 32px;
  padding: 0 9px;
  font-size: 12px;
  color: var(--text-1);
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-sm);
  background: rgba(255, 255, 255, 0.04);
  outline: none;
}

.dialog-input:focus {
  border-color: var(--accent);
}

.dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 2px;
}

.dlg-btn {
  min-width: 64px;
  height: 30px;
  padding: 0 12px;
  font-size: 12px;
  color: var(--text-2);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: transparent;
}

.dlg-btn:hover {
  color: var(--text-1);
  border-color: var(--line-strong);
}

.dlg-btn.primary {
  color: var(--text-1);
  border-color: rgba(91, 140, 255, 0.4);
  background: rgba(91, 140, 255, 0.12);
}

.dlg-btn.primary.danger {
  color: var(--danger);
  border-color: rgba(255, 96, 96, 0.4);
  background: rgba(255, 96, 96, 0.1);
}

@media (max-width: 900px) {
  .session-panel {
    width: 208px;
  }
}
</style>
