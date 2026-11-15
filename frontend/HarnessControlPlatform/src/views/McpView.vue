<script setup lang="ts">
// MCP 服务管理：懒连接状态板 + 粘贴厂商 JSON 导入 + 悬停详情卡 + 可拖拽抽屉
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ArrowRight,
  Close,
  Delete,
  Edit,
  InfoFilled,
  Lock,
  Monitor,
  Plus,
  Refresh,
  Search,
  VideoPlay,
} from '@element-plus/icons-vue'
import TopNav from '@/components/console/TopNav.vue'
import HoverDetailCard from '@/components/console/HoverDetailCard.vue'
import HoloTooltip from '@/components/console/HoloTooltip.vue'
import {
  deleteMcpServer,
  fetchMcpConfig,
  fetchMcpLogs,
  fetchMcpRuntime,
  fetchMcpServer,
  fetchMcpServers,
  fetchMcpTools,
  importMcpJson,
  preflightMcpJson,
  probeMcpServer,
  restartMcpServer,
  setMcpEnabled,
  testMcpTool,
  type McpConfigInfo,
  type McpLogEntry,
  type McpPreflightResult,
  type McpRuntimeInfo,
  type McpServerDetail,
  type McpServerItem,
  type McpState,
  type McpTool,
} from '@/api/mcp'

const PAGE_SIZE = 20

const STATE_META: Record<
  McpState,
  { label: string; tone: string; hint: string }
> = {
  Dormant: { label: '未连接', tone: 'dormant', hint: '首次使用时连接' },
  Disabled: { label: '已停用', tone: 'disabled', hint: '不会连接，也不会暴露工具' },
  Starting: { label: '连接中', tone: 'starting', hint: '正在建立会话' },
  Connected: { label: '已连接', tone: 'connected', hint: '工具可用，连接池复用中' },
  Degraded: { label: '降级', tone: 'degraded', hint: '可用但延迟或健康检查异常' },
  Pending: { label: '重试中', tone: 'pending', hint: '瞬时失败，按退避策略重试' },
  NeedsAuth: { label: '需授权', tone: 'auth', hint: '凭据失效，不会自动重试' },
  Failed: { label: '失败', tone: 'failed', hint: '配置或不可恢复错误' },
  Stopping: { label: '清理中', tone: 'stopping', hint: '正在释放连接' },
}

const items = ref<McpServerItem[]>([])
const total = ref(0)
const page = ref(1)
const pages = ref(1)
const keyword = ref('')
const stateFilter = ref('')
const loading = ref(false)
const error = ref('')
const configError = ref('')
const runtime = ref<McpRuntimeInfo | null>(null)

const drawerOpen = ref(false)
const drawerTab = ref<'overview' | 'tools' | 'logs'>('overview')
const selected = ref<McpServerDetail | null>(null)
const tools = ref<McpTool[]>([])
const logs = ref<McpLogEntry[]>([])
const drawerLoading = ref(false)
const toolLoading = ref(false)
const logsLoading = ref(false)
const drawerWidth = ref(520)
const resizing = ref(false)

const probeId = ref('')
const togglingId = ref('')
const restartingId = ref('')

const activeTool = ref<McpTool | null>(null)
const testArgs = ref('{}')
const testResult = ref('')
const testRunning = ref(false)
const testError = ref('')

// ---------- 导入 JSON ----------
const addOpen = ref(false)
const editingId = ref('')
const jsonText = ref('')
const importEnabled = ref(true)
// 自定义 MCP：这是用户自己的后端项目，会出现在聊天侧栏【集成的项目】。
const importIntegration = ref(false)
const importing = ref(false)
const addError = ref('')
const preflightLoading = ref(false)
const preflight = ref<McpPreflightResult | null>(null)
const forceImport = ref(false)
const configInfo = ref<McpConfigInfo | null>(null)
let preflightTimer: number | undefined
let preflightSeq = 0

const SAMPLE_JSON = `{
  "mcpServers": {
    "weather": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-weather"],
      "env": { "WEATHER_API_KEY": "\${WEATHER_API_KEY}" }
    },
    "remote-docs": {
      "type": "streamable_http",
      "url": "https://mcp.example.com/mcp",
      "headers": { "Authorization": "Bearer \${DOCS_TOKEN}" }
    }
  }
}`

// ---------- 悬停详情卡 ----------
const hover = ref<{ item: McpServerItem; x: number; y: number } | null>(null)
const hoverTools = ref<McpTool[]>([])
const hoverLoading = ref(false)
const hoverCache = new Map<string, McpTool[]>()
let hoverTimer: number | undefined
let hoverSeq = 0

const deleting = ref<McpServerItem | null>(null)
const deletingBusy = ref(false)

let searchTimer: number | undefined
let pollTimer: number | undefined
let loadSeq = 0

const connectedCount = computed(
  () =>
    items.value.filter(
      (item) => item.state === 'Connected' || item.state === 'Degraded',
    ).length,
)
const poolSize = computed(() => runtime.value?.pool.size ?? 0)
const isEditing = computed(() => Boolean(editingId.value))

function stateMeta(state: McpState) {
  return STATE_META[state] || STATE_META.Failed
}

// 状态徽标：把业务状态映射到 el-tag 的语义色
type TagType = 'primary' | 'success' | 'warning' | 'info' | 'danger'

function stateTagType(state: McpState): TagType {
  if (state === 'Connected') return 'success'
  if (state === 'Starting' || state === 'Degraded' || state === 'Pending') {
    return 'warning'
  }
  if (state === 'Failed' || state === 'NeedsAuth') return 'danger'
  return 'info'
}

function transportLabel(transport: string): string {
  if (transport === 'streamable_http' || transport === 'http') return 'HTTP'
  if (transport === 'sse') return 'SSE'
  return 'stdio'
}

function sourceLabel(source: string): string {
  if (source === 'live_discovery') return '实时发现'
  if (source === 'lkg_cache') return '上次缓存'
  return '无'
}

function flash(message: string, type: 'ok' | 'err' = 'ok') {
  if (type === 'err') ElMessage.error(message)
  else ElMessage.success(message)
}

function formatTime(iso?: string | null): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString('zh-CN', { hour12: false })
}

async function load() {
  const seq = ++loadSeq
  loading.value = true
  error.value = ''
  try {
    const data = await fetchMcpServers({
      page: page.value,
      page_size: PAGE_SIZE,
      keyword: keyword.value.trim(),
      state: stateFilter.value,
    })
    if (seq !== loadSeq) return
    items.value = data.items
    total.value = data.total
    pages.value = data.pages
    page.value = data.page
    configError.value = data.config_error
    runtime.value = {
      initialized: true,
      config_error: data.config_error,
      pool: data.pool,
      server_count: data.total,
      main_servers: runtime.value?.main_servers || [],
    }
    void loadRuntime()
  } catch (err) {
    if (seq !== loadSeq) return
    error.value = (err as Error).message
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

async function loadRuntime() {
  try {
    runtime.value = await fetchMcpRuntime()
  } catch {
    /* runtime 概览失败不影响列表 */
  }
}

watch(keyword, () => {
  if (searchTimer) window.clearTimeout(searchTimer)
  searchTimer = window.setTimeout(() => {
    page.value = 1
    void load()
  }, 300)
})

watch(stateFilter, () => {
  page.value = 1
  void load()
})

// ---------- 详情抽屉 ----------
async function openDrawer(item: McpServerItem) {
  drawerOpen.value = true
  drawerTab.value = 'overview'
  activeTool.value = null
  testResult.value = ''
  testError.value = ''
  selected.value = { ...item, tools: [], pool_stats: runtime.value?.pool || {
    size: 0, hits: 0, misses: 0, evictions: 0, idle_ttl_ms: 0, max_pool_size: 0,
  } }
  drawerLoading.value = true
  try {
    selected.value = await fetchMcpServer(item.server_id)
    tools.value = selected.value.tools || []
    void loadLogs()
  } catch (err) {
    flash((err as Error).message, 'err')
  } finally {
    drawerLoading.value = false
  }
}

async function refreshSelected(serverId?: string) {
  const targetId = serverId || selected.value?.server_id
  if (!targetId) return
  try {
    const detail = await fetchMcpServer(targetId)
    selected.value = detail
    tools.value = detail.tools || []
    const index = items.value.findIndex((item) => item.server_id === targetId)
    if (index >= 0) items.value[index] = detail
  } catch (err) {
    flash((err as Error).message, 'err')
  }
  void loadRuntime()
}

function closeDrawer() {
  drawerOpen.value = false
  selected.value = null
  tools.value = []
  logs.value = []
  activeTool.value = null
}

async function probeServer(item: McpServerItem | McpServerDetail) {
  if (probeId.value) return
  probeId.value = item.server_id
  try {
    const result = await probeMcpServer(item.server_id)
    const detail = result.server
    const index = items.value.findIndex((entry) => entry.server_id === item.server_id)
    if (index >= 0) items.value[index] = detail
    if (selected.value?.server_id === item.server_id) {
      selected.value = detail
      tools.value = detail.tools || []
    }
    if (detail.state === 'Connected' || detail.state === 'Degraded') {
      flash(`${item.server_id} 已连接，发现 ${detail.tool_count} 个工具`)
    } else {
      flash(`${item.server_id} 连接失败：${detail.error || detail.state}`, 'err')
    }
  } catch (err) {
    flash((err as Error).message, 'err')
  } finally {
    probeId.value = ''
    void loadRuntime()
  }
}

async function toggleServer(item: McpServerItem | McpServerDetail, enabled: boolean) {
  if (togglingId.value) return
  togglingId.value = item.server_id
  try {
    const detail = await setMcpEnabled(item.server_id, enabled, false)
    const index = items.value.findIndex((entry) => entry.server_id === item.server_id)
    if (index >= 0) items.value[index] = detail
    if (selected.value?.server_id === item.server_id) {
      selected.value = detail
      tools.value = detail.tools || []
    }
    flash(
      enabled
        ? `${item.server_id} 已启用，首次使用时才会连接`
        : `${item.server_id} 已停用，工具已移除`,
    )
  } catch (err) {
    flash((err as Error).message, 'err')
  } finally {
    togglingId.value = ''
    void load()
  }
}

async function restartServer(serverId: string) {
  if (restartingId.value) return
  restartingId.value = serverId
  try {
    await restartMcpServer(serverId)
    await refreshSelected(serverId)
    flash('已释放旧连接，下一次调用将按需重连')
  } catch (err) {
    flash((err as Error).message, 'err')
  } finally {
    restartingId.value = ''
  }
}

// 删除确认：交给 ElMessageBox，确认后再走原有删除流程
async function askDelete(item: McpServerItem | McpServerDetail) {
  deleting.value = item
  try {
    await ElMessageBox.confirm(
      `确定删除 ${item.server_id} 吗？配置、连接和工具缓存都会被清理。`,
      '删除 MCP 服务',
      {
        type: 'warning',
        confirmButtonText: '确认删除',
        cancelButtonText: '取消',
      },
    )
  } catch {
    deleting.value = null
    return
  }
  await confirmDelete()
}

async function confirmDelete() {
  const item = deleting.value
  if (!item || deletingBusy.value) return
  deletingBusy.value = true
  try {
    await deleteMcpServer(item.server_id)
    if (selected.value?.server_id === item.server_id) closeDrawer()
    flash(`已删除 MCP 服务 ${item.server_id}`)
    if (items.value.length === 1 && page.value > 1) page.value--
    await load()
  } catch (err) {
    flash((err as Error).message, 'err')
  } finally {
    deletingBusy.value = false
    deleting.value = null
  }
}

// 分页与抽屉分段
function onPageChange(next: number) {
  page.value = next
  void load()
}

// el-radio-group 的 change 回调参数可能为 undefined，这里做空值兜底
function onDrawerTabChange(value: string | number | boolean | undefined) {
  void switchTab(value as 'overview' | 'tools' | 'logs')
}

// ---------- 工具与测试 ----------
async function switchTab(tab: 'overview' | 'tools' | 'logs') {
  drawerTab.value = tab
  if (!selected.value) return
  if (tab === 'tools' && tools.value.length === 0) {
    await loadTools(false)
  }
  if (tab === 'logs') {
    await loadLogs()
  }
}

async function loadTools(discover: boolean) {
  if (!selected.value) return
  toolLoading.value = true
  try {
    const data = await fetchMcpTools(selected.value.server_id, discover)
    tools.value = data.tools
    await refreshSelected(selected.value.server_id)
  } catch (err) {
    flash((err as Error).message, 'err')
  } finally {
    toolLoading.value = false
  }
}

async function loadLogs() {
  if (!selected.value) return
  logsLoading.value = true
  try {
    const data = await fetchMcpLogs(selected.value.server_id)
    logs.value = data.logs
  } catch (err) {
    flash((err as Error).message, 'err')
  } finally {
    logsLoading.value = false
  }
}

function openTool(tool: McpTool) {
  activeTool.value = activeTool.value?.remote_name === tool.remote_name ? null : tool
  testArgs.value = '{}'
  testResult.value = ''
  testError.value = ''
}

async function runToolTest() {
  if (!selected.value || !activeTool.value || testRunning.value) return
  let args: Record<string, unknown> = {}
  try {
    args = JSON.parse(testArgs.value || '{}') as Record<string, unknown>
    if (Array.isArray(args) || args === null || typeof args !== 'object') {
      throw new Error('参数必须是 JSON 对象')
    }
  } catch (err) {
    testError.value = `JSON 解析失败：${(err as Error).message}`
    return
  }
  testRunning.value = true
  testError.value = ''
  testResult.value = ''
  try {
    const data = await testMcpTool(
      selected.value.server_id,
      activeTool.value.remote_name,
      args,
    )
    testResult.value = data.text
    await refreshSelected(selected.value.server_id)
  } catch (err) {
    testError.value = (err as Error).message
  } finally {
    testRunning.value = false
  }
}

// ---------- 新增 / 编辑（粘贴 JSON） ----------
function openCreate() {
  editingId.value = ''
  jsonText.value = ''
  preflight.value = null
  addError.value = ''
  importEnabled.value = true
  importIntegration.value = false
  forceImport.value = false
  addOpen.value = true
}

function buildVendorJson(detail: McpServerDetail): string {
  const connection = detail.connection || {}
  const entry: Record<string, unknown> = {}
  if (detail.transport === 'stdio') {
    entry.command = connection.command || ''
    entry.args = connection.args || []
    if (connection.env && Object.keys(connection.env).length > 0) {
      entry.env = connection.env
    }
    if (connection.cwd) entry.cwd = connection.cwd
  } else {
    entry.type = detail.transport
    entry.url = connection.url || ''
    if (connection.headers && Object.keys(connection.headers).length > 0) {
      entry.headers = connection.headers
    }
  }
  if (detail.description) entry.description = detail.description
  return JSON.stringify({ mcpServers: { [detail.server_id]: entry } }, null, 2)
}

async function openEdit(item: McpServerItem | McpServerDetail) {
  let detail: McpServerDetail | null = null
  if ('tools' in item) {
    detail = item
  } else {
    try {
      detail = await fetchMcpServer(item.server_id)
    } catch (err) {
      flash((err as Error).message, 'err')
      return
    }
  }
  if (!detail) return
  editingId.value = detail.server_id
  jsonText.value = buildVendorJson(detail)
  preflight.value = null
  addError.value = ''
  importEnabled.value = detail.enabled
  importIntegration.value = detail.integration
  forceImport.value = false
  addOpen.value = true
  schedulePreflight(200)
}

function formatJson() {
  try {
    jsonText.value = JSON.stringify(JSON.parse(jsonText.value), null, 2)
    addError.value = ''
    schedulePreflight(200)
  } catch (err) {
    addError.value = `JSON 格式不合法：${(err as Error).message}`
  }
}

function fillSample() {
  jsonText.value = SAMPLE_JSON
  addError.value = ''
  schedulePreflight(300)
}

function schedulePreflight(delay = 600) {
  if (preflightTimer) window.clearTimeout(preflightTimer)
  preflightTimer = window.setTimeout(() => void runPreflight(), delay)
}

async function runPreflight() {
  const text = jsonText.value.trim()
  if (!text) {
    preflight.value = null
    return
  }
  const seq = ++preflightSeq
  preflightLoading.value = true
  addError.value = ''
  try {
    const result = await preflightMcpJson(text)
    if (seq !== preflightSeq) return
    preflight.value = result
    if (result.invalid.length > 0) {
      addError.value = result.invalid.map((item) => `${item.server_id}: ${item.error}`).join('；')
    }
  } catch (err) {
    if (seq !== preflightSeq) return
    preflight.value = null
    addError.value = (err as Error).message
  } finally {
    if (seq === preflightSeq) preflightLoading.value = false
  }
}

const canImport = computed(() => {
  if (importing.value || preflightLoading.value) return false
  if (!jsonText.value.trim() || !preflight.value) return false
  if (preflight.value.invalid.length > 0) return false
  return preflight.value.ready || forceImport.value
})

// 状态卡片只展示子进程原始输出，连接/状态事件留给「日志」页看全量
const recentSubprocessLogs = computed(() =>
  logs.value.filter((entry) => entry.event === 'mcp.log').slice(-4),
)

async function submitImport() {
  if (!canImport.value) return
  importing.value = true
  addError.value = ''
  try {
    const result = await importMcpJson(
      jsonText.value,
      importEnabled.value,
      importIntegration.value,
    )
    addOpen.value = false
    jsonText.value = ''
    preflight.value = null
    keyword.value = ''
    stateFilter.value = ''
    page.value = 1
    await load()
    flash(`已导入 ${result.imported.length} 个 MCP 服务配置`)
    const first = result.servers[0]
    if (first) await openDrawer(first)
  } catch (err) {
    addError.value = (err as Error).message
  } finally {
    importing.value = false
  }
}

// ---------- 悬停详情卡 ----------
function hoverMeta(item: McpServerItem): { label: string; value: string }[] {
  return [
    { label: '传输', value: transportLabel(item.transport) },
    { label: '命名空间', value: item.namespace },
    { label: '工具', value: String(item.tool_count) },
    { label: '延迟', value: item.latency_ms == null ? '—' : `${item.latency_ms} ms` },
    { label: '来源', value: sourceLabel(item.tool_source) },
    { label: '最近使用', value: formatTime(item.last_used_at) },
  ]
}

function hoverTags(item: McpServerItem): string[] {
  const tags = [stateMeta(item.state).label, transportLabel(item.transport)]
  if (item.integration) tags.push('集成项目')
  if (!item.enabled) tags.push('已停用')
  if (item.criticality === 'required') tags.push('关键服务')
  return tags
}

async function showHover(item: McpServerItem, event: MouseEvent) {
  if (hoverTimer) window.clearTimeout(hoverTimer)
  const x = event.clientX
  const y = event.clientY
  hoverTimer = window.setTimeout(() => {
    hover.value = { item, x, y }
    const cached = hoverCache.get(item.server_id)
    if (cached) {
      hoverTools.value = cached
      hoverLoading.value = false
      return
    }
    hoverTools.value = []
    hoverLoading.value = true
    const seq = ++hoverSeq
    void fetchMcpTools(item.server_id, false)
      .then((data) => {
        hoverCache.set(item.server_id, data.tools)
        if (seq === hoverSeq && hover.value?.item.server_id === item.server_id) {
          hoverTools.value = data.tools
        }
      })
      .catch(() => undefined)
      .finally(() => {
        if (seq === hoverSeq) hoverLoading.value = false
      })
  }, 240)
}

function hideHover() {
  if (hoverTimer) window.clearTimeout(hoverTimer)
  hover.value = null
}

const hoverStyle = computed(() => {
  if (!hover.value) return {}
  const cardWidth = 420
  const cardHeight = 340
  const left = Math.min(hover.value.x + 18, window.innerWidth - cardWidth - 16)
  const top = Math.min(hover.value.y + 14, window.innerHeight - cardHeight - 16)
  return {
    left: `${Math.max(12, left)}px`,
    top: `${Math.max(12, top)}px`,
  }
})

// ---------- 抽屉拖拽 ----------
let resizeStartX = 0
let resizeStartWidth = 0

function onResizeMove(event: MouseEvent) {
  drawerWidth.value = Math.min(
    820,
    Math.max(380, resizeStartWidth + (resizeStartX - event.clientX)),
  )
}

function stopResize() {
  resizing.value = false
  window.removeEventListener('mousemove', onResizeMove)
  window.removeEventListener('mouseup', stopResize)
}

function startResize(event: MouseEvent) {
  event.preventDefault()
  resizing.value = true
  resizeStartX = event.clientX
  resizeStartWidth = drawerWidth.value
  window.addEventListener('mousemove', onResizeMove)
  window.addEventListener('mouseup', stopResize)
}

onMounted(() => {
  void load()
  void loadRuntime()
  void fetchMcpConfig()
    .then((value) => (configInfo.value = value))
    .catch(() => undefined)
  // 状态轮询只刷新列表，不会触发任何 MCP 连接。
  pollTimer = window.setInterval(() => {
    if (!loading.value && !addOpen.value && !deleting.value) {
      void load()
      if (selected.value && !drawerLoading.value) {
        void refreshSelected(selected.value.server_id)
      }
    }
  }, 5000)
})

onBeforeUnmount(() => {
  if (searchTimer) window.clearTimeout(searchTimer)
  if (pollTimer) window.clearInterval(pollTimer)
  if (hoverTimer) window.clearTimeout(hoverTimer)
  if (preflightTimer) window.clearTimeout(preflightTimer)
  stopResize()
})
</script>

<template>
  <div class="app-shell">
    <TopNav />

    <div class="page">
      <section class="runtime-strip">
        <div class="runtime-title">
          <span class="title-icon"><el-icon><Monitor /></el-icon></span>
          <div>
            <strong>MCP 服务</strong>
            <small>客户端编排 · 启动零连接 · 首次使用时建立会话</small>
            <small v-if="configInfo" class="config-path">
              统一配置：<code>{{ configInfo.config_path }}</code>
            </small>
          </div>
        </div>
        <div class="runtime-facts">
          <span><b>{{ total }}</b> 个服务</span>
          <span><b>{{ connectedCount }}</b> 已连接</span>
          <span><b>{{ poolSize }}</b> 连接池</span>
          <el-tag size="small" effect="plain" type="primary">懒连接</el-tag>
          <el-button
            size="small"
            :icon="Refresh"
            circle
            title="刷新状态"
            @click="load"
          />
        </div>
      </section>

      <section class="toolbar">
        <el-input
          v-model="keyword"
          class="search-input"
          placeholder="搜索服务名、描述或命名空间…"
          :prefix-icon="Search"
          clearable
        />
        <el-select v-model="stateFilter" class="state-select" placeholder="全部状态">
          <el-option label="全部状态" value="" />
          <el-option label="未连接" value="Dormant" />
          <el-option label="连接中" value="Starting" />
          <el-option label="已连接" value="Connected" />
          <el-option label="降级" value="Degraded" />
          <el-option label="重试中" value="Pending" />
          <el-option label="需授权" value="NeedsAuth" />
          <el-option label="失败" value="Failed" />
          <el-option label="清理中" value="Stopping" />
          <el-option label="已停用" value="Disabled" />
        </el-select>
        <el-button type="primary" :icon="Plus" @click="openCreate">新增服务</el-button>
      </section>

      <p v-if="configError" class="config-error">
        {{ configError }}
      </p>
      <p v-if="error" class="error-note">{{ error }}</p>

      <div v-if="loading && items.length === 0" class="loading-block">
        <el-skeleton animated :rows="6" />
      </div>

      <ul v-else-if="items.length" v-loading="loading" class="server-list">
        <li
          v-for="item in items"
          :key="item.server_id"
          class="server-row"
          :class="stateMeta(item.state).tone"
          @mouseenter="(event) => showHover(item, event)"
          @mouseleave="hideHover"
          @click="openDrawer(item)"
        >
          <span class="state-rail">
            <i class="state-dot"></i>
          </span>

          <div class="server-main">
            <div class="server-head">
              <code class="server-id">{{ item.server_id }}</code>
              <el-tag size="small" effect="plain" :type="stateTagType(item.state)">
                {{ stateMeta(item.state).label }}
              </el-tag>
              <el-tag v-if="item.integration" size="small" effect="plain" type="primary">
                集成
              </el-tag>
              <el-tag
                v-if="item.criticality === 'required'"
                size="small"
                effect="plain"
                type="warning"
              >
                关键
              </el-tag>
              <el-tag v-if="!item.enabled" size="small" effect="plain" type="info">
                已停用
              </el-tag>
            </div>
            <p class="server-desc">{{ item.description || '未填写描述' }}</p>
            <div class="server-tags">
              <span>{{ transportLabel(item.transport) }}</span>
              <em>/</em>
              <span>ns {{ item.namespace }}</span>
              <em>/</em>
              <span>{{ stateMeta(item.state).hint }}</span>
              <em v-if="item.retry_at">/</em>
              <span v-if="item.retry_at">重试 {{ formatTime(item.retry_at) }}</span>
            </div>
          </div>

          <div class="server-metrics">
            <div class="metric">
              <strong>{{ item.tool_count }}</strong>
              <small>工具</small>
            </div>
            <div class="metric">
              <strong>{{ item.latency_ms ?? '—' }}</strong>
              <small>ms</small>
            </div>
            <div class="metric source">
              <strong>{{ sourceLabel(item.tool_source) }}</strong>
              <small>来源</small>
            </div>
          </div>

          <div class="server-actions" @click.stop>
            <el-button
              size="small"
              :icon="VideoPlay"
              :loading="probeId === item.server_id"
              :disabled="probeId === item.server_id || !item.enabled"
              @click="probeServer(item)"
            >
              {{ probeId === item.server_id ? '连接中' : '测试连接' }}
            </el-button>
            <el-switch
              :model-value="item.enabled"
              :disabled="togglingId === item.server_id"
              @change="(v: string | number | boolean) => toggleServer(item, Boolean(v))"
            />
            <el-button
              size="small"
              :icon="ArrowRight"
              circle
              title="详情"
              @click="openDrawer(item)"
            />
          </div>
        </li>
      </ul>

      <el-empty v-else class="empty-block" description="还没有 MCP 服务">
        <p class="empty-tip">
          新增一个服务后，它会先保持未连接；第一次调用或点击测试连接时才建立会话。
        </p>
        <el-button type="primary" :icon="Plus" @click="openCreate">新增服务</el-button>
      </el-empty>

      <footer v-if="pages > 1" class="pager">
        <el-pagination
          layout="prev, pager, next"
          :current-page="page"
          :page-count="pages"
          background
          @current-change="onPageChange"
        />
        <span>共 {{ total }} 个</span>
      </footer>
    </div>

    <!-- 详情抽屉：右侧覆盖式面板，宽度可拖拽 -->
    <el-drawer
      v-model="drawerOpen"
      class="mcp-drawer"
      direction="rtl"
      :size="`${drawerWidth}px`"
      :with-header="false"
      :modal="false"
      modal-penetrable
      :lock-scroll="false"
      :style="{ top: 'var(--topbar-h)', height: 'calc(100vh - var(--topbar-h))' }"
      @closed="closeDrawer"
    >
      <div v-if="selected" class="drawer-panel" :class="{ resizing }">
        <div class="resizer" title="拖动调整宽度" @mousedown="startResize"></div>

        <header class="drawer-head">
          <span class="drawer-state" :class="stateMeta(selected.state).tone"></span>
          <div class="drawer-title">
            <code>{{ selected.server_id }}</code>
            <small>{{ stateMeta(selected.state).label }} · {{ stateMeta(selected.state).hint }}</small>
          </div>
          <el-button size="small" :icon="Close" circle title="关闭" @click="closeDrawer" />
        </header>

        <div class="drawer-actions">
          <el-button
            size="small"
            :icon="VideoPlay"
            :loading="probeId === selected.server_id"
            :disabled="probeId === selected.server_id || !selected.enabled"
            @click="probeServer(selected)"
          >
            {{ probeId === selected.server_id ? '连接中…' : '测试连接' }}
          </el-button>
          <el-button
            size="small"
            :icon="Refresh"
            :disabled="restartingId === selected.server_id"
            @click="restartServer(selected.server_id)"
          >
            重启
          </el-button>
          <el-button size="small" :icon="Edit" @click="openEdit(selected)">编辑</el-button>
          <el-button size="small" type="danger" :icon="Delete" @click="askDelete(selected)">
            删除
          </el-button>
        </div>

        <div class="drawer-body">
          <div v-if="drawerLoading" class="drawer-empty">正在读取详情…</div>

          <template v-else>
            <el-radio-group
              :model-value="drawerTab"
              class="drawer-tabs"
              @change="onDrawerTabChange"
            >
              <el-radio-button value="overview">概览</el-radio-button>
              <el-radio-button value="tools">
                工具 {{ selected?.tool_count }}
              </el-radio-button>
              <el-radio-button value="logs">日志</el-radio-button>
            </el-radio-group>

            <div v-if="drawerTab === 'overview'" class="drawer-pane">
              <div class="drawer-switchbar">
                <span>
                  <strong>{{ selected.enabled ? '已启用配置' : '已停用' }}</strong>
                  <small>
                    {{ selected.enabled ? '首次使用时连接，不占用启动时间' : '不会连接，也不会暴露工具' }}
                  </small>
                </span>
                <el-switch
                  :model-value="selected.enabled"
                  :disabled="togglingId === selected.server_id"
                  @change="(v: string | number | boolean) => selected && toggleServer(selected, Boolean(v))"
                />
              </div>

              <div v-if="selected.error" class="error-box">
                <strong>{{ selected.error_code || '连接错误' }}</strong>
                <p>{{ selected.error }}</p>
                <span v-if="selected.error_category">分类：{{ selected.error_category }}</span>
              </div>

              <section v-if="recentSubprocessLogs.length" class="detail-block">
                <h4>
                  最近子进程日志
                  <el-button type="primary" link size="small" @click="switchTab('logs')">
                    查看全部
                  </el-button>
                </h4>
                <ul class="log-mini">
                  <li
                    v-for="(entry, index) in recentSubprocessLogs"
                    :key="`${entry.ts}-${index}`"
                    :class="entry.level"
                  >
                    <span>{{ formatTime(entry.ts) }}</span>
                    <p>{{ entry.message }}</p>
                  </li>
                </ul>
              </section>

              <section class="detail-block">
                <h4>连接</h4>
                <dl class="kv-grid">
                  <dt>transport</dt><dd>{{ selected.transport }}</dd>
                  <dt>namespace</dt><dd>{{ selected.namespace }}</dd>
                  <dt>类型</dt><dd>{{ selected.integration ? '集成项目（自有后端 MCP）' : '通用 MCP' }}</dd>
                  <dt>criticality</dt><dd>{{ selected.criticality }}</dd>
                  <dt>preconnect</dt><dd>{{ selected.preconnect ? '是' : '否（懒连接）' }}</dd>
                  <dt>pool key</dt><dd class="mono">{{ selected.pool_key }}</dd>
                  <dt>启用</dt><dd>{{ selected.enabled ? '是' : '否' }}</dd>
                </dl>
              </section>

              <section class="detail-block">
                <h4>连接参数</h4>
                <dl class="kv-grid">
                  <template v-if="selected.transport === 'stdio'">
                    <dt>command</dt><dd class="mono">{{ selected.connection.command || '—' }}</dd>
                    <dt>args</dt><dd class="mono">{{ (selected.connection.args || []).join(' ') || '—' }}</dd>
                    <dt>cwd</dt><dd class="mono">{{ selected.connection.cwd || '—' }}</dd>
                    <dt>env</dt><dd class="mono">{{ Object.keys(selected.connection.env || {}).join(', ') || '—' }}</dd>
                  </template>
                  <template v-else>
                    <dt>url</dt><dd class="mono">{{ selected.connection.url || '—' }}</dd>
                    <dt>headers</dt><dd class="mono">{{ Object.keys(selected.connection.headers || {}).join(', ') || '—' }}</dd>
                  </template>
                  <dt>连接超时</dt><dd>{{ selected.connect_timeout_ms }} ms</dd>
                  <dt>调用超时</dt><dd>{{ selected.call_timeout_ms }} ms</dd>
                </dl>
              </section>

              <section class="detail-block">
                <h4>运行状态</h4>
                <dl class="kv-grid">
                  <dt>工具数</dt><dd>{{ selected.tool_count }}</dd>
                  <dt>工具来源</dt><dd>{{ sourceLabel(selected.tool_source) }}</dd>
                  <dt>最近连接</dt><dd>{{ formatTime(selected.connected_at) }}</dd>
                  <dt>最近使用</dt><dd>{{ formatTime(selected.last_used_at) }}</dd>
                  <dt>重试次数</dt><dd>{{ selected.retry_count }}</dd>
                  <dt>epoch</dt><dd>{{ selected.epoch }}</dd>
                </dl>
              </section>

              <section class="detail-block">
                <h4>工具过滤</h4>
                <p class="filter-line">allow：{{ selected.allow_tools.join(', ') || '全部' }}</p>
                <p class="filter-line">deny：{{ selected.deny_tools.join(', ') || '无' }}</p>
              </section>
            </div>

            <div v-else-if="drawerTab === 'tools'" class="drawer-pane">
              <div class="tools-head">
                <span>{{ selected.tool_count }} 个工具 · {{ sourceLabel(selected.tool_source) }}</span>
                <el-button
                  size="small"
                  :icon="Refresh"
                  :loading="toolLoading"
                  @click="loadTools(true)"
                >
                  {{ toolLoading ? '发现中…' : '重新发现' }}
                </el-button>
              </div>

              <div v-if="toolLoading && tools.length === 0" class="drawer-empty">正在发现工具…</div>
              <div v-else-if="tools.length === 0" class="drawer-empty">
                还没有工具缓存。点击「重新发现」会建立一次懒连接并拉取工具。
              </div>

              <ul v-else class="tool-list">
                <li
                  v-for="tool in tools"
                  :key="tool.remote_name"
                  class="tool-item"
                  :class="{ expanded: activeTool?.remote_name === tool.remote_name }"
                >
                  <el-button text class="tool-head" @click="openTool(tool)">
                    <code>{{ tool.exposed_name }}</code>
                    <span class="tool-remote">{{ tool.remote_name }}</span>
                    <el-tag v-if="!tool.allowlisted" size="small" type="danger" effect="plain">
                      已拦截
                    </el-tag>
                    <el-icon
                      class="chev"
                      :class="{ open: activeTool?.remote_name === tool.remote_name }"
                    >
                      <ArrowRight />
                    </el-icon>
                  </el-button>
                  <div v-if="activeTool?.remote_name === tool.remote_name" class="tool-body">
                    <p class="tool-desc">{{ tool.description || '未提供描述' }}</p>
                    <div class="schema-block">
                      <small>参数 Schema</small>
                      <pre>{{ JSON.stringify(tool.input_schema, null, 2) }}</pre>
                    </div>
                    <div class="test-block">
                      <small>测试调用参数（JSON）</small>
                      <el-input v-model="testArgs" type="textarea" :rows="4" />
                      <div class="test-actions">
                        <el-button
                          type="primary"
                          size="small"
                          :loading="testRunning"
                          @click="runToolTest"
                        >
                          {{ testRunning ? '调用中…' : '调用工具' }}
                        </el-button>
                      </div>
                      <p v-if="testError" class="form-error">{{ testError }}</p>
                      <pre v-if="testResult" class="test-result">{{ testResult }}</pre>
                    </div>
                  </div>
                </li>
              </ul>
            </div>

            <div v-else class="drawer-pane">
              <div class="tools-head">
                <span>最近 {{ logs.length }} 条事件</span>
                <el-button size="small" :icon="Refresh" :loading="logsLoading" @click="loadLogs">
                  刷新
                </el-button>
              </div>
              <div v-if="logsLoading" class="drawer-empty">正在读取日志…</div>
              <div v-else-if="logs.length === 0" class="drawer-empty">暂无日志</div>
              <ul v-else class="log-list">
                <li
                  v-for="(entry, index) in logs"
                  :key="`${entry.ts}-${index}`"
                  class="log-item"
                  :class="entry.level"
                >
                  <span class="log-time">{{ formatTime(entry.ts) }}</span>
                  <span class="log-event">{{ entry.event }}</span>
                  <p>{{ entry.message || JSON.stringify(entry) }}</p>
                </li>
              </ul>
            </div>
          </template>
        </div>
      </div>
    </el-drawer>

    <!-- 新增/编辑：粘贴厂商 JSON，确认后直接写入统一配置文件 -->
    <el-dialog
      v-model="addOpen"
      class="mcp-json-dialog"
      :title="isEditing ? `编辑 ${editingId}` : '新增 MCP 服务'"
      width="min(840px, 94vw)"
      destroy-on-close
    >
      <div class="dialog-body">
        <p class="import-hint">
          把 MCP 服务厂商提供的配置 JSON 直接粘贴到这里，支持
          <code>{"mcpServers": {...}}</code>、单个服务对象或服务映射。
          工具、提示词与资源由 MCP 服务自己上报，无需手填。
        </p>

        <el-input
          v-model="jsonText"
          class="json-input"
          type="textarea"
          :rows="14"
          placeholder='{ "mcpServers": { "weather": { "command": "npx", "args": ["-y", "@modelcontextprotocol/server-weather"] } } }'
          @input="schedulePreflight()"
        />

        <div class="json-actions">
          <el-button size="small" @click="fillSample">填入示例</el-button>
          <el-button size="small" @click="formatJson">格式化</el-button>
          <el-button size="small" :loading="preflightLoading" @click="runPreflight">
            {{ preflightLoading ? '检查中…' : '检查环境' }}
          </el-button>
        </div>

        <div v-if="preflight" class="preflight">
          <header>
            <span>环境预检</span>
            <el-tag size="small" effect="plain" :type="preflight.ready ? 'success' : 'warning'">
              {{ preflight.ready ? '可以导入' : '存在未满足的前置条件' }}
            </el-tag>
          </header>

          <div v-if="preflight.requires_api_key.length" class="api-key-hint">
            <el-icon><Lock /></el-icon>
            <span>
              这些网络 MCP 通常需要 API Key：
              <code>{{ preflight.requires_api_key.join(', ') }}</code>。
              请在 JSON 的 <code>headers</code> 中补充（如
              <code>Authorization: Bearer ${API_KEY}</code>）。
            </span>
          </div>

          <div v-if="preflight.npm_cache && !preflight.npm_cache.writable" class="cache-hint">
            <el-icon><InfoFilled /></el-icon>
            <span>
              系统 npm 缓存不可写（<code>{{ preflight.npm_cache.cache_dir || '未知' }}</code>），
              普通用户没有该目录权限。下面这些服务会自动改用项目内缓存
              <code>{{ preflight.npm_cache.workspace_root }}</code>，
              无需管理员权限，也不会改动你的全局 npm 配置。
            </span>
          </div>

          <ul class="pf-list">
            <li v-for="server in preflight.servers" :key="server.server_id">
              <div class="pf-row">
                <code>{{ server.server_id }}</code>
                <el-tag size="small" effect="plain" type="info">
                  {{ server.transport || '无效' }}
                </el-tag>
                <el-tag
                  class="pf-state"
                  size="small"
                  effect="plain"
                  :type="server.error || !server.ready ? 'warning' : 'success'"
                >
                  {{ server.error ? '配置无效' : server.ready ? '就绪' : '缺少运行环境' }}
                </el-tag>
              </div>
              <p v-if="server.error" class="pf-text bad">{{ server.error }}</p>
              <p v-if="server.hint" class="pf-text">{{ server.hint }}</p>
              <ul v-if="server.checks.length" class="pf-checks">
                <li v-for="check in server.checks" :key="check.command">
                  <span>{{ check.label }}</span>
                  <em>{{ check.available ? check.version || '已安装' : '未检测到' }}</em>
                  <span class="pf-version" :class="check.satisfied ? 'ok' : 'bad'">
                    {{ check.available ? (check.satisfied ? '满足' : `需要 ${check.min_version}+`) : '缺失' }}
                  </span>
                  <a :href="check.download" target="_blank" rel="noreferrer">下载</a>
                  <el-tooltip
                    placement="left"
                    :show-after="120"
                    popper-class="holo-tooltip"
                    effect="dark"
                  >
                    <template #content>
                      <div class="holo-tipcard" style="width: 300px">
                        <span class="holo-tipcard-corner tl"></span>
                        <span class="holo-tipcard-corner tr"></span>
                        <span class="holo-tipcard-corner bl"></span>
                        <span class="holo-tipcard-corner br"></span>
                        <header class="holo-tipcard-head">
                          <span class="holo-tipcard-scan"></span>
                          <strong>下载的是什么</strong>
                          <code>ENV // PACKAGE</code>
                        </header>
                        <p class="holo-tipcard-body">
                          这是 {{ check.download_target || '该运行环境的官方安装包' }}，不是这个 MCP
                          的工具或资源。工具、提示词与资源会在服务连通后由服务自己上报，不需要在这里下载。建议：{{
                            check.advice ||
                            (check.satisfied ? '当前已满足，不建议重复安装。' : '当前未满足，建议按链接安装。')
                          }}
                        </p>
                        <footer class="holo-tipcard-foot">
                          <span>HOLOGRAPHIC DRAWER BRIEF</span>
                          <span>悬停查看说明</span>
                        </footer>
                      </div>
                    </template>
                    <el-icon class="info-icon"><InfoFilled /></el-icon>
                  </el-tooltip>
                </li>
              </ul>
              <p v-if="server.cache_dir" class="pf-text pf-cache">
                npm 缓存：<code>{{ server.cache_dir }}</code>（项目内，独立于系统缓存）
              </p>
            </li>
          </ul>

          <el-checkbox
            v-if="!preflight.ready && preflight.invalid.length === 0"
            v-model="forceImport"
            class="force-row"
          >
            仍要保存（稍后自行安装缺失的运行环境）
          </el-checkbox>
        </div>

        <div class="form-switches">
          <span class="switch-row">
            <span>启用配置</span>
            <el-switch v-model="importEnabled" />
          </span>
          <span class="switch-row">
            <span>
              集成项目
              <small class="switch-hint">这是我自己接入的后端 MCP，会出现在聊天侧栏</small>
            </span>
            <el-switch v-model="importIntegration" />
          </span>
        </div>

        <p class="lazy-note">
          保存后默认保持未连接；只有点击「测试连接」或第一次被 Agent 调用时，才会建立会话。
          配置统一写入 <code>{{ configInfo?.config_path || 'mcp/servers.json' }}</code>。
        </p>
        <p v-if="addError" class="form-error">{{ addError }}</p>
      </div>

      <template #footer>
        <el-button :disabled="importing" @click="addOpen = false">取消</el-button>
        <el-button type="primary" :disabled="!canImport" :loading="importing" @click="submitImport">
          {{ importing ? '导入中…' : '确认导入' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 悬停详情卡：通用组件，后续插件市场可复用 -->
    <Teleport to="body">
      <div v-if="hover" class="mcp-hover" :style="hoverStyle">
        <HoverDetailCard
          :title="hover.item.server_id"
          :subtitle="hover.item.description || '未填写描述'"
          :tags="hoverTags(hover.item)"
          :meta="hoverMeta(hover.item)"
          :items="hoverTools.map((tool) => ({ name: tool.exposed_name, description: tool.description }))"
          :item-label="`可用工具 ${hover.item.tool_count}`"
          :loading="hoverLoading"
          empty-text="尚未发现工具；首次调用或测试连接后会自动拉取"
          :max-items="8"
        />
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.app-shell {
  min-height: 100vh;
}

.page {
  display: flex;
  flex-direction: column;
  gap: 12px;
  width: 100%;
  padding: 14px 18px 24px;
}

/* ---------- 运行时状态条 ---------- */
.runtime-strip {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding: 12px 2px;
  border: 0;
  border-top: 1px solid var(--line);
  border-radius: 0;
  background: transparent;
  flex-wrap: wrap;
}

.runtime-title {
  display: flex;
  align-items: center;
  gap: 10px;
}

.title-icon {
  display: grid;
  place-items: center;
  width: 22px;
  height: 22px;
  color: var(--accent-cyan);
  background: transparent;
}

.runtime-title strong {
  display: block;
  font-size: 15px;
}

.runtime-title small {
  font-size: 11px;
  color: var(--text-3);
}

.runtime-facts {
  display: flex;
  align-items: center;
  gap: 16px;
}

.runtime-facts span {
  font-size: 11.5px;
  color: var(--text-3);
}

.runtime-facts b {
  margin-right: 3px;
  font-size: 15px;
  color: var(--text-1);
  font-variant-numeric: tabular-nums;
}

.toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.search-input {
  width: 340px;
}

.state-select {
  width: 132px;
}

.config-error,
.error-note {
  margin: 0;
  padding: 8px 10px;
  font-size: 12px;
  color: var(--amber);
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-sm);
  background: transparent;
}

.loading-block {
  padding: 18px 0;
}

.empty-block {
  padding: 34px 0;
}

.empty-tip {
  max-width: 460px;
  margin: 0 auto 12px;
  font-size: 11.5px;
  line-height: 1.7;
  color: var(--text-3);
}

/* ---------- 服务列表：状态信号板 ---------- */
.server-list {
  position: relative;
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0;
  border-top: 1px solid var(--line);
}

.server-row {
  --state-color: var(--text-3);
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr) 150px 240px;
  align-items: center;
  gap: 12px;
  padding: 10px 14px 10px 0;
  border: 0;
  border-bottom: 1px solid var(--line);
  border-radius: 0;
  background: transparent;
  cursor: pointer;
  transition: border-color 0.16s, background 0.16s;
}

.server-row.connected,
.server-row.degraded,
.server-row.starting {
  --state-color: var(--accent-cyan);
}

.server-row.pending {
  --state-color: var(--amber);
}

.server-row.dormant {
  --state-color: var(--text-3);
}

.server-row.auth {
  --state-color: var(--accent);
}

.server-row.failed {
  --state-color: var(--danger);
}

.server-row.disabled,
.server-row.stopping {
  --state-color: var(--line-strong);
}

.server-row:hover {
  background: var(--bg-hover);
}

.state-rail {
  align-self: stretch;
  display: grid;
  place-items: center;
  border-left: 2px solid var(--state-color);
  border-radius: 0;
  background: transparent;
}

.state-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--state-color);
  box-shadow: 0 0 9px var(--state-color);
}

.server-row.starting .state-dot,
.server-row.pending .state-dot {
  animation: state-pulse 1.4s ease-in-out infinite;
}

@keyframes state-pulse {
  0%, 100% { opacity: 0.45; }
  50% { opacity: 1; }
}

.server-main {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.server-head {
  display: flex;
  align-items: center;
  gap: 9px;
  flex-wrap: wrap;
}

.server-id {
  font-family: 'Cascadia Code', 'JetBrains Mono', Consolas, monospace;
  font-size: 13px;
  color: var(--text-1);
}

.switch-hint {
  display: block;
  margin-top: 2px;
  font-size: 9.5px;
  color: var(--text-3);
}

.server-desc {
  margin: 0;
  overflow: hidden;
  font-size: 11.5px;
  line-height: 1.55;
  color: var(--text-2);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.server-tags {
  display: flex;
  align-items: center;
  gap: 5px;
  flex-wrap: wrap;
  font-size: 9.5px;
  color: var(--text-3);
}

.server-tags em {
  font-style: normal;
  opacity: 0.5;
}

.server-metrics {
  display: flex;
  gap: 14px;
  justify-content: flex-end;
}

.metric {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  min-width: 44px;
}

.metric strong {
  font-size: 15px;
  font-variant-numeric: tabular-nums;
  color: var(--text-1);
}

.metric small {
  font-size: 9px;
  color: var(--text-3);
}

.metric.source strong {
  font-size: 10.5px;
  font-weight: 500;
  color: var(--text-2);
}

.server-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 9px;
}

.pager {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 6px 0 8px;
  font-size: 11.5px;
  color: var(--text-3);
}

/* ---------- 详情抽屉 ---------- */
/* el-drawer 内部的自定义面板：头部固定，内容区滚动 */
.drawer-panel {
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100%;
  border-left: 1px solid var(--line-strong);
  background: var(--bg-elevated);
}

.drawer-panel.resizing {
  user-select: none;
}

.resizer {
  position: absolute;
  top: 0;
  left: 0;
  bottom: 0;
  z-index: 1;
  width: 6px;
  cursor: col-resize;
}

.resizer::after {
  content: '';
  position: absolute;
  top: 50%;
  left: 2px;
  width: 2px;
  height: 46px;
  margin-top: -23px;
  border-radius: 2px;
  background: var(--line-strong);
  opacity: 0;
  transition: opacity 0.15s;
}

.resizer:hover::after,
.drawer-panel.resizing .resizer::after {
  opacity: 1;
}

.drawer-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--line);
}

.drawer-state {
  width: 9px;
  height: 9px;
  flex: none;
  border-radius: 50%;
  background: var(--text-3);
  box-shadow: 0 0 10px currentColor;
}

.drawer-state.connected,
.drawer-state.starting {
  background: var(--accent-cyan);
}

.drawer-state.pending,
.drawer-state.degraded {
  background: var(--amber);
}

.drawer-state.dormant {
  background: var(--text-3);
}

.drawer-state.auth {
  background: var(--accent);
}

.drawer-state.failed {
  background: var(--danger);
}

.drawer-state.disabled,
.drawer-state.stopping {
  background: var(--line-strong);
}

.drawer-title {
  flex: 1;
  min-width: 0;
}

.drawer-title code {
  display: block;
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 14px;
  color: var(--text-1);
}

.drawer-title small {
  display: block;
  margin-top: 2px;
  font-size: 10.5px;
  color: var(--text-3);
}

.drawer-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  padding: 10px 16px;
}

.drawer-tabs {
  display: flex;
  width: 100%;
  margin-bottom: 12px;
}

.drawer-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 12px 16px 24px;
}

.drawer-pane {
  padding-top: 2px;
}

.drawer-switchbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
  padding: 9px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: transparent;
}

.drawer-switchbar strong {
  display: block;
  font-size: 11.5px;
  color: var(--text-1);
}

.drawer-switchbar small {
  display: block;
  margin-top: 2px;
  font-size: 10px;
  color: var(--text-3);
}

.drawer-empty {
  padding: 22px 0;
  font-size: 11.5px;
  color: var(--text-3);
}

.error-box {
  margin-bottom: 12px;
  padding: 9px 10px;
  border: 1px solid rgba(226, 96, 94, 0.35);
  border-radius: var(--radius-sm);
  background: transparent;
}

.error-box strong {
  font-size: 11.5px;
  color: var(--danger);
}

.error-box p {
  margin: 4px 0;
  font-size: 11px;
  color: var(--text-2);
}

.error-box span {
  font-size: 9.5px;
  color: var(--text-3);
}

.detail-block {
  margin-bottom: 16px;
}

.detail-block h4 {
  margin: 0 0 7px;
  font-size: 10.5px;
  font-weight: 500;
  color: var(--text-3);
}

.log-mini {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.log-list {
  gap: 0;
  border-top: 1px solid var(--line);
}

.log-mini li {
  padding: 6px 8px;
  border: 0;
  border-top: 1px solid var(--line);
  border-radius: 0;
  background: transparent;
}

.log-mini li.warning {
  border-color: rgba(242, 176, 60, 0.3);
}

.log-mini li.error {
  border-color: rgba(226, 96, 94, 0.32);
}

.log-mini span {
  font-size: 9px;
  color: var(--text-3);
  font-variant-numeric: tabular-nums;
}

.log-mini p {
  margin: 3px 0 0;
  font-size: 10.5px;
  line-height: 1.55;
  color: var(--text-2);
  word-break: break-all;
}

.kv-grid {
  display: grid;
  grid-template-columns: 92px minmax(0, 1fr);
  gap: 5px 10px;
  margin: 0;
  padding: 9px 10px;
  font-size: 10.5px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: transparent;
}

.kv-grid dt {
  color: var(--text-3);
}

.kv-grid dd {
  margin: 0;
  overflow-wrap: anywhere;
  color: var(--text-2);
}

.kv-grid dd.mono {
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 10px;
}

.filter-line {
  margin: 0 0 4px;
  font-size: 10.5px;
  color: var(--text-2);
  word-break: break-all;
}

.tools-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 9px;
  font-size: 10.5px;
  color: var(--text-3);
}

.tool-list,
.log-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.tool-item {
  border: 0;
  border-top: 1px solid var(--line);
  border-radius: 0;
  background: transparent;
  overflow: hidden;
}

.tool-item.expanded {
  background: var(--bg-hover);
}

.tool-head {
  width: 100%;
  height: auto;
  padding: 8px 10px;
  color: var(--text-1);
  text-align: left;
  background: transparent;
}

/* el-button 会把默认插槽包一层 span，这里让工具头恢复单行布局 */
.tool-head > span {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}

.tool-head code {
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 11.5px;
}

.tool-remote {
  font-size: 9.5px;
  color: var(--text-3);
}

.chev {
  margin-left: auto;
  color: var(--text-3);
  transition: transform 0.16s;
}

.chev.open {
  transform: rotate(90deg);
}

.tool-body {
  padding: 0 10px 11px;
  border-top: 1px solid var(--line);
}

.tool-desc {
  margin: 8px 0 0;
  font-size: 11px;
  line-height: 1.55;
  color: var(--text-2);
}

.schema-block,
.test-block {
  margin-top: 8px;
}

.schema-block small,
.test-block small {
  display: block;
  margin-bottom: 4px;
  font-size: 9.5px;
  color: var(--text-3);
}

.schema-block pre,
.test-result {
  margin: 0;
  max-height: 180px;
  overflow: auto;
  padding: 7px 9px;
  font-family: var(--font-mono);
  font-size: 10px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--text-2);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--bg-deep);
}

.test-block :deep(.el-textarea__inner) {
  font-family: var(--font-mono);
  font-size: 11px;
}

.test-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 6px;
}

.test-result {
  margin-top: 8px;
  color: var(--accent-cyan);
  max-height: 240px;
}

.log-item {
  padding: 7px 9px;
  border: 0;
  border-bottom: 1px solid var(--line);
  border-radius: 0;
  background: transparent;
}

.log-item.warning {
  border-color: rgba(242, 176, 60, 0.3);
}

.log-item.error {
  border-color: rgba(226, 96, 94, 0.3);
}

.log-time {
  font-size: 9px;
  color: var(--text-3);
  font-variant-numeric: tabular-nums;
}

.log-event {
  margin-left: 8px;
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 9.5px;
  color: var(--accent-cyan);
}

.log-item p {
  margin: 3px 0 0;
  font-size: 10.5px;
  line-height: 1.5;
  color: var(--text-2);
}

/* ---------- Dialog ---------- */
.form-switches {
  display: flex;
  gap: 24px;
  flex-wrap: wrap;
  padding: 4px 0 8px;
}

.switch-row {
  display: inline-flex;
  align-items: center;
  gap: 9px;
  font-size: 11.5px;
  color: var(--text-2);
}

.lazy-note {
  margin: 6px 0 0;
  padding: 9px 0 0;
  font-size: 10.5px;
  line-height: 1.6;
  color: var(--text-3);
  border: 0;
  border-top: 1px solid var(--line);
  border-radius: 0;
  background: transparent;
}

.form-error {
  margin: 8px 0 0;
  font-size: 11.5px;
  color: var(--danger);
}

/* ---------- JSON 导入 ---------- */
.config-path {
  display: block;
  margin-top: 2px;
  font-size: 9.5px;
  color: var(--text-3);
}

.config-path code {
  font-family: 'Cascadia Code', Consolas, monospace;
  color: var(--text-2);
  word-break: break-all;
}

.import-hint {
  margin: 0 0 10px;
  font-size: 11.5px;
  line-height: 1.65;
  color: var(--text-3);
}

.import-hint code {
  font-family: 'Cascadia Code', Consolas, monospace;
  color: var(--accent-cyan);
}

.json-input :deep(.el-textarea__inner) {
  min-height: 220px;
  max-height: 42vh;
  font-family: var(--font-mono);
  font-size: 11.5px;
  line-height: 1.6;
}

.json-actions {
  display: flex;
  gap: 8px;
  margin-top: 8px;
  flex-wrap: wrap;
}

.preflight {
  margin-top: 12px;
  padding: 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: transparent;
}

.preflight header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 11px;
  color: var(--text-2);
}

.api-key-hint {
  display: flex;
  gap: 7px;
  margin-top: 9px;
  padding: 8px 10px;
  font-size: 10.5px;
  line-height: 1.6;
  color: var(--amber);
  border: 1px solid rgba(242, 176, 60, 0.3);
  border-radius: var(--radius-sm);
  background: transparent;
}

.api-key-hint code {
  font-family: 'Cascadia Code', Consolas, monospace;
  color: var(--amber);
  word-break: break-all;
}

.pf-list {
  list-style: none;
  margin: 9px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.pf-list > li {
  padding: 8px 10px;
  border-radius: 0;
  background: transparent;
  border-top: 1px solid var(--line);
}

.pf-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.pf-row code {
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 11px;
  color: var(--text-1);
}

.pf-state {
  margin-left: auto;
}

.pf-text {
  margin: 6px 0 0;
  font-size: 10.5px;
  line-height: 1.6;
  color: var(--text-3);
}

.pf-text.bad {
  color: var(--danger);
}

.pf-checks {
  list-style: none;
  margin: 6px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.pf-checks li {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 10.5px;
  color: var(--text-2);
}

.pf-checks em {
  font-style: normal;
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 10px;
  color: var(--text-3);
}

.pf-version {
  margin-left: auto;
  font-size: 9.5px;
}

.pf-version.ok {
  color: var(--green);
}

.pf-version.bad {
  color: var(--amber);
}

.pf-checks a {
  font-size: 10px;
  color: var(--accent-cyan);
  text-decoration: none;
}

.pf-checks a:hover {
  text-decoration: underline;
}

.cache-hint {
  display: flex;
  gap: 7px;
  margin-top: 9px;
  padding: 8px 10px;
  font-size: 10.5px;
  line-height: 1.6;
  color: var(--accent-cyan);
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-sm);
  background: transparent;
}

.cache-hint code {
  font-family: 'Cascadia Code', Consolas, monospace;
  color: var(--accent-cyan);
  word-break: break-all;
}

.pf-cache code {
  font-family: 'Cascadia Code', Consolas, monospace;
  word-break: break-all;
}

/* 说明图标：与 FilePanel 一致的全息 Tooltip 触发元素 */
.info-icon {
  flex: none;
  font-size: 12px;
  color: var(--text-3);
  cursor: help;
  transition: color 0.15s ease;
}

.info-icon:hover {
  color: var(--accent-cyan);
}

.force-row {
  margin-top: 9px;
  font-size: 10.5px;
  color: var(--text-3);
}

/* ---------- 悬停详情卡 ---------- */
.mcp-hover {
  position: fixed;
  z-index: 90;
  width: min(420px, calc(100vw - 32px));
  pointer-events: none;
  animation: hover-in 0.18s ease-out;
}

@keyframes hover-in {
  from {
    opacity: 0;
    transform: translateY(6px);
  }

  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@media (max-width: 1100px) {
  .server-row {
    grid-template-columns: 18px minmax(0, 1fr) 120px;
  }

  .server-actions {
    grid-column: 2 / -1;
    justify-content: flex-start;
    padding-top: 6px;
  }

  .server-metrics {
    justify-content: flex-start;
  }
}

@media (max-width: 760px) {
  .server-row {
    grid-template-columns: 18px minmax(0, 1fr);
  }

  .server-metrics {
    grid-column: 2;
  }

  .search-input {
    width: 100%;
  }

  .resizer {
    display: none;
  }
}
</style>

<style>
/* el-drawer / el-dialog 会传送到 body，这里用独立类名做局部布局微调 */
.mcp-drawer .el-drawer__body {
  padding: 0;
  overflow: hidden;
}

.mcp-json-dialog .el-dialog__body {
  max-height: 62vh;
  overflow-y: auto;
}
</style>
