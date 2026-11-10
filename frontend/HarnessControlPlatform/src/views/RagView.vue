<script setup lang="ts">
// 知识库：RAG Provider 注册、检索测试、知识灌入与管理
// Harness 只保存 Provider 配置，不保存知识原文、切片与向量。
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ArrowRight,
  Connection,
  Delete,
  Edit,
  Plus,
  Reading,
  Refresh,
  Search,
  VideoPlay,
} from '@element-plus/icons-vue'
import TopNav from '@/components/console/TopNav.vue'
import { fetchMcpServers, type McpServerItem } from '@/api/mcp'
import {
  createRagProvider,
  deleteRagDocument,
  deleteRagProvider,
  discoverRagProvider,
  fetchRagDocuments,
  fetchRagProvider,
  fetchRagProviders,
  fetchRagStats,
  ingestRag,
  probeRagProvider,
  retrieveRag,
  setRagEnabled,
  updateRagProvider,
  type RagChunk,
  type RagDocumentItem,
  type RagProviderItem,
  type RagProviderPayload,
  type RagState,
} from '@/api/rag'

const STATE_META: Record<RagState, { label: string; tone: string; hint: string }> = {
  Unknown: { label: '未检测', tone: 'unknown', hint: '尚未做健康检查' },
  Ready: { label: '就绪', tone: 'ready', hint: '检索与知识接口可用' },
  Degraded: { label: '降级', tone: 'degraded', hint: '部分能力异常' },
  Unavailable: { label: '不可用', tone: 'down', hint: '后端服务不可达' },
  Disabled: { label: '已停用', tone: 'disabled', hint: '不会参与检索' },
}

const providers = ref<RagProviderItem[]>([])
const loading = ref(false)
const error = ref('')
const keyword = ref('')
const polling = ref<number | undefined>(undefined)
const mcpServers = ref<McpServerItem[]>([])

const editingId = ref('')
const formOpen = ref(false)
const saving = ref(false)
const formError = ref('')

const discoverOpen = ref(false)
const discovering = ref(false)
const discoverError = ref('')
const discoveryForm = reactive({
  mcp_server_id: '',
  discovery_tool: 'rag_manifest',
  auto_register: true,
})

const drawerOpen = ref(false)
const drawerTab = ref<string | number>('overview')
const selected = ref<RagProviderItem | null>(null)
const probingId = ref('')
const togglingId = ref('')

const documents = ref<RagDocumentItem[]>([])
const docsLoading = ref(false)
const docsError = ref('')
const stats = ref<Record<string, unknown> | null>(null)
const ingestTitle = ref('')
const ingestText = ref('')
const ingestBusy = ref(false)
const ingestNotice = ref('')
const deletingDocId = ref('')

const query = ref('')
const topK = ref<number | undefined>(5)
const retrieving = ref(false)
const retrieveError = ref('')
const retrieveResult = ref<{ chunks: RagChunk[]; errors: { provider_id: string; message: string }[]; note?: string } | null>(null)

const deleting = ref(false)

const form = reactive({
  provider_id: '',
  name: '',
  description: '',
  protocol: 'http' as 'http' | 'mcp',
  endpoint: '',
  mcp_server_id: '',
  auth_type: 'none',
  secret_ref: '',
  capabilities: ['retrieve', 'health'],
  domains: '',
  citation_format: 'chunk',
  top_k_max: 20 as number | undefined,
  max_chars_per_chunk: 2000 as number | undefined,
  timeout_ms: 30000 as number | undefined,
  enabled: true,
  integration_server_id: '',
})

const CAPABILITIES = [
  { key: 'retrieve', label: '检索' },
  { key: 'ingest', label: '知识灌入' },
  { key: 'list_documents', label: '文档列表' },
  { key: 'delete_document', label: '删除文档' },
  { key: 'stats', label: '知识统计' },
  { key: 'health', label: '健康检查' },
]

const filtered = computed(() => {
  const needle = keyword.value.trim().toLowerCase()
  if (!needle) return providers.value
  return providers.value.filter((item) =>
    [item.provider_id, item.name, item.description, item.protocol, item.knowledge_domains.join(' ')]
      .join(' ')
      .toLowerCase()
      .includes(needle),
  )
})

const readyCount = computed(
  () => providers.value.filter((item) => item.state === 'Ready').length,
)

function stateMeta(state: RagState) {
  return STATE_META[state] || STATE_META.Unknown
}

// 状态对应的 Element Plus 标签色
function stateTagType(state: RagState): 'primary' | 'success' | 'info' | 'warning' | 'danger' {
  if (state === 'Ready') return 'success'
  if (state === 'Degraded') return 'warning'
  if (state === 'Unavailable') return 'danger'
  return 'info'
}

function flash(message: string, type: 'ok' | 'err' = 'ok') {
  if (type === 'err') ElMessage.error(message)
  else ElMessage.success(message)
}

function formatTime(iso?: string | null) {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString('zh-CN', { hour12: false })
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await fetchRagProviders()
    providers.value = data.items
  } catch (err) {
    error.value = (err as Error).message
  } finally {
    loading.value = false
  }
}

async function loadMcpServers() {
  try {
    const data = await fetchMcpServers({ page: 1, page_size: 100 })
    mcpServers.value = data.items
  } catch {
    mcpServers.value = []
  }
}

function resetForm() {
  editingId.value = ''
  form.provider_id = ''
  form.name = ''
  form.description = ''
  form.protocol = 'http'
  form.endpoint = ''
  form.mcp_server_id = ''
  form.auth_type = 'none'
  form.secret_ref = ''
  form.capabilities = ['retrieve', 'health']
  form.domains = ''
  form.citation_format = 'chunk'
  form.top_k_max = 20
  form.max_chars_per_chunk = 2000
  form.timeout_ms = 30000
  form.enabled = true
  form.integration_server_id = ''
}

function openCreate() {
  resetForm()
  formError.value = ''
  formOpen.value = true
}

function openEdit(item: RagProviderItem) {
  editingId.value = item.provider_id
  form.provider_id = item.provider_id
  form.name = item.name
  form.description = item.description
  form.protocol = item.protocol
  form.endpoint = item.endpoint
  form.mcp_server_id = item.mcp_server_id
  form.auth_type = String((item.auth as { type?: string }).type || 'none')
  form.secret_ref = String((item.auth as { secret_ref?: string }).secret_ref || '')
  form.capabilities = [...item.capabilities]
  form.domains = item.knowledge_domains.join(', ')
  form.citation_format = item.citation_format
  form.top_k_max = Number(item.limits?.top_k_max || 20)
  form.max_chars_per_chunk = Number(item.limits?.max_chars_per_chunk || 2000)
  form.timeout_ms = item.timeout_ms
  form.enabled = item.enabled
  form.integration_server_id = item.integration_server_id
  formError.value = ''
  formOpen.value = true
}

function toggleCapability(key: string) {
  if (key === 'retrieve') return
  const index = form.capabilities.indexOf(key)
  if (index >= 0) form.capabilities.splice(index, 1)
  else form.capabilities.push(key)
}

function buildPayload(): RagProviderPayload {
  const auth =
    form.auth_type === 'none'
      ? {}
      : { type: form.auth_type, secret_ref: form.secret_ref.trim() }
  return {
    provider_id: form.provider_id.trim(),
    name: form.name.trim() || form.provider_id.trim(),
    description: form.description.trim(),
    protocol: form.protocol,
    endpoint: form.endpoint.trim(),
    mcp_server_id: form.mcp_server_id.trim(),
    auth,
    capabilities: [...form.capabilities, 'retrieve'].filter(
      (item, index, list) => list.indexOf(item) === index,
    ),
    knowledge_domains: form.domains
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean),
    citation_format: form.citation_format,
    limits: {
      top_k_max: Number(form.top_k_max) || 20,
      max_chars_per_chunk: Number(form.max_chars_per_chunk) || 2000,
    },
    tools: {},
    timeout_ms: Number(form.timeout_ms) || 30000,
    enabled: form.enabled,
    integration_server_id: form.integration_server_id.trim(),
  }
}

async function submitForm() {
  if (saving.value) return
  if (!form.provider_id.trim()) {
    formError.value = 'provider_id 不能为空'
    return
  }
  formError.value = ''
  saving.value = true
  try {
    const payload = buildPayload()
    if (editingId.value) await updateRagProvider(editingId.value, payload)
    else await createRagProvider(payload)
    formOpen.value = false
    flash(editingId.value ? 'Provider 已更新' : 'Provider 已创建')
    await load()
  } catch (err) {
    formError.value = (err as Error).message
  } finally {
    saving.value = false
  }
}

async function probe(item: RagProviderItem) {
  if (probingId.value) return
  probingId.value = item.provider_id
  try {
    const detail = await probeRagProvider(item.provider_id)
    providers.value = providers.value.map((entry) =>
      entry.provider_id === item.provider_id ? detail : entry,
    )
    if (selected.value?.provider_id === item.provider_id) selected.value = detail
    if (detail.state === 'Ready') flash(`${item.provider_id} 健康检查通过`)
    else flash(`${item.provider_id} 不可用：${detail.last_error || detail.state}`, 'err')
  } catch (err) {
    flash((err as Error).message, 'err')
  } finally {
    probingId.value = ''
  }
}

async function toggleEnabled(item: RagProviderItem, enabled: boolean) {
  if (togglingId.value) return
  togglingId.value = item.provider_id
  try {
    const detail = await setRagEnabled(item.provider_id, enabled)
    providers.value = providers.value.map((entry) =>
      entry.provider_id === item.provider_id ? detail : entry,
    )
    if (selected.value?.provider_id === item.provider_id) selected.value = detail
    flash(enabled ? 'Provider 已启用' : 'Provider 已停用')
  } catch (err) {
    flash((err as Error).message, 'err')
  } finally {
    togglingId.value = ''
  }
}

// 删除确认：统一走 ElMessageBox
async function askDelete(item: RagProviderItem) {
  if (deleting.value) return
  try {
    await ElMessageBox.confirm(
      `确定删除 ${item.provider_id} 吗？只删除 Harness 侧的注册配置，不影响后端知识库。`,
      '删除 RAG Provider',
      { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  deleting.value = true
  try {
    await deleteRagProvider(item.provider_id)
    if (selected.value?.provider_id === item.provider_id) closeDrawer()
    flash(`已删除 ${item.provider_id}`)
    await load()
  } catch (err) {
    flash((err as Error).message, 'err')
  } finally {
    deleting.value = false
  }
}

// ---------- 详情抽屉 ----------
async function openDrawer(item: RagProviderItem) {
  drawerOpen.value = true
  drawerTab.value = 'overview'
  selected.value = item
  documents.value = []
  stats.value = null
  retrieveResult.value = null
  retrieveError.value = ''
  try {
    const detail = await fetchRagProvider(item.provider_id)
    selected.value = detail
  } catch (err) {
    flash((err as Error).message, 'err')
  }
}

function closeDrawer() {
  drawerOpen.value = false
  selected.value = null
  documents.value = []
  stats.value = null
  retrieveResult.value = null
}

async function switchTab(tab: string | number) {
  drawerTab.value = tab
  if (!selected.value) return
  if (tab === 'knowledge') await loadKnowledge()
}

async function loadKnowledge() {
  if (!selected.value) return
  docsLoading.value = true
  docsError.value = ''
  try {
    const [list, stat] = await Promise.all([
      fetchRagDocuments(selected.value.provider_id),
      fetchRagStats(selected.value.provider_id).catch(() => null),
    ])
    documents.value = list.documents || []
    stats.value = stat
  } catch (err) {
    docsError.value = (err as Error).message
  } finally {
    docsLoading.value = false
  }
}

function documentId(item: RagDocumentItem): string {
  return String(item.document_id || item.id || '')
}

async function submitIngest() {
  if (!selected.value || ingestBusy.value) return
  const text = ingestText.value.trim()
  if (!text) {
    ingestNotice.value = '请输入要灌入的知识内容或 JSON'
    return
  }
  ingestBusy.value = true
  ingestNotice.value = ''
  try {
    let docs: Record<string, unknown>[]
    if (text.startsWith('[') || text.startsWith('{')) {
      const parsed = JSON.parse(text) as unknown
      if (Array.isArray(parsed)) docs = parsed as Record<string, unknown>[]
      else if (parsed && typeof parsed === 'object') {
        const wrapper = parsed as { documents?: unknown }
        docs = Array.isArray(wrapper.documents)
          ? (wrapper.documents as Record<string, unknown>[])
          : [parsed as Record<string, unknown>]
      } else docs = [{ content: text }]
    } else {
      docs = [{ title: ingestTitle.value.trim() || '未命名文档', content: text }]
    }
    const result = await ingestRag(selected.value.provider_id, docs)
    ingestNotice.value = `已提交 ${docs.length} 条文档：${JSON.stringify(result).slice(0, 120)}`
    ingestText.value = ''
    await loadKnowledge()
  } catch (err) {
    ingestNotice.value = (err as Error).message
  } finally {
    ingestBusy.value = false
  }
}

async function removeDocument(item: RagDocumentItem) {
  if (!selected.value) return
  const id = documentId(item)
  if (!id || deletingDocId.value) return
  deletingDocId.value = id
  try {
    await deleteRagDocument(selected.value.provider_id, id)
    flash('文档已删除')
    await loadKnowledge()
  } catch (err) {
    flash((err as Error).message, 'err')
  } finally {
    deletingDocId.value = ''
  }
}

async function runRetrieve() {
  if (!selected.value || retrieving.value) return
  const text = query.value.trim()
  if (!text) {
    retrieveError.value = '请输入检索问题'
    return
  }
  retrieving.value = true
  retrieveError.value = ''
  retrieveResult.value = null
  try {
    retrieveResult.value = await retrieveRag({
      query: text,
      top_k: Number(topK.value) || 5,
      provider_ids: [selected.value.provider_id],
    })
  } catch (err) {
    retrieveError.value = (err as Error).message
  } finally {
    retrieving.value = false
  }
}

// ---------- 从 MCP 发现 ----------
function openDiscover() {
  discoveryForm.mcp_server_id = ''
  discoveryForm.discovery_tool = 'rag_manifest'
  discoveryForm.auto_register = true
  discoverError.value = ''
  discoverOpen.value = true
  void loadMcpServers()
}

async function submitDiscover() {
  if (!discoveryForm.mcp_server_id) {
    discoverError.value = '请选择要发现 RAG 的 MCP 服务'
    return
  }
  discovering.value = true
  discoverError.value = ''
  try {
    const result = await discoverRagProvider({
      mcp_server_id: discoveryForm.mcp_server_id,
      discovery_tool: discoveryForm.discovery_tool,
      auto_register: discoveryForm.auto_register,
    })
    discoverOpen.value = false
    flash(
      `发现完成：${Array.isArray(result.discovered) ? result.discovered.length : 0} 个 Provider`,
    )
    await load()
  } catch (err) {
    discoverError.value = (err as Error).message
  } finally {
    discovering.value = false
  }
}

onMounted(() => {
  void load()
  polling.value = window.setInterval(() => {
    if (!formOpen.value && !drawerOpen.value) void load()
  }, 15000)
})

onBeforeUnmount(() => {
  if (polling.value) window.clearInterval(polling.value)
})
</script>

<template>
  <div class="app-shell">
    <TopNav />

    <div class="page">
      <section class="runtime-strip">
        <div class="runtime-title">
          <span class="title-icon"><el-icon><Reading /></el-icon></span>
          <div>
            <strong>知识库 / RAG</strong>
            <small>Provider 注册 · 统一检索 · 知识灌入与管理</small>
          </div>
        </div>
        <div class="runtime-facts">
          <span><b>{{ providers.length }}</b> 个 Provider</span>
          <span><b>{{ readyCount }}</b> 就绪</span>
          <el-button size="small" :icon="Refresh" circle title="刷新" @click="load" />
        </div>
      </section>

      <section class="toolbar">
        <el-input
          v-model="keyword"
          class="search-input"
          placeholder="搜索 Provider、协议或知识域…"
          :prefix-icon="Search"
          clearable
        />
        <el-button :icon="Connection" @click="openDiscover">从 MCP 发现</el-button>
        <el-button type="primary" :icon="Plus" @click="openCreate">新增 Provider</el-button>
      </section>

      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />

      <div v-loading="loading && providers.length === 0" class="provider-wrap">
        <ul v-if="filtered.length" class="provider-list">
          <li
            v-for="item in filtered"
            :key="item.provider_id"
            class="provider-row"
            :class="stateMeta(item.state).tone"
            @click="openDrawer(item)"
          >
            <span class="state-rail"><i class="state-dot"></i></span>
            <div class="provider-main">
              <div class="provider-head">
                <code>{{ item.provider_id }}</code>
                <el-tag size="small" effect="plain" :type="stateTagType(item.state)">
                  {{ stateMeta(item.state).label }}
                </el-tag>
                <el-tag size="small" effect="plain" type="info">
                  {{ item.protocol.toUpperCase() }}
                </el-tag>
                <el-tag
                  v-if="item.integration_server_id"
                  size="small"
                  effect="plain"
                  type="primary"
                >
                  集成 {{ item.integration_server_id }}
                </el-tag>
              </div>
              <p class="provider-desc">{{ item.description || item.name }}</p>
              <div class="provider-tags">
                <span>{{ stateMeta(item.state).hint }}</span>
                <em v-if="item.knowledge_domains.length">/</em>
                <span v-if="item.knowledge_domains.length">
                  {{ item.knowledge_domains.join(' · ') }}
                </span>
                <em v-if="item.last_checked_at">/</em>
                <span v-if="item.last_checked_at">检查 {{ formatTime(item.last_checked_at) }}</span>
              </div>
            </div>
            <div class="provider-metrics">
              <div class="metric">
                <strong>{{ item.document_count ?? '—' }}</strong>
                <small>文档</small>
              </div>
              <div class="metric">
                <strong>{{ item.latency_ms ?? '—' }}</strong>
                <small>ms</small>
              </div>
            </div>
            <div class="provider-actions" @click.stop>
              <el-button
                size="small"
                :icon="VideoPlay"
                :loading="probingId === item.provider_id"
                :disabled="!item.enabled"
                @click="probe(item)"
              >
                {{ probingId === item.provider_id ? '检测中' : '健康检查' }}
              </el-button>
              <el-switch
                :model-value="item.enabled"
                :loading="togglingId === item.provider_id"
                @update:model-value="(v: string | number | boolean) => toggleEnabled(item, Boolean(v))"
              />
              <el-button size="small" :icon="ArrowRight" circle title="详情" @click="openDrawer(item)" />
            </div>
          </li>
        </ul>

        <el-empty v-else-if="!loading" description="还没有 RAG Provider">
          <p class="empty-hint">
            接入一个外部 RAG/知识库后，Agent 会通过 knowledge_search 工具检索知识并引用来源。
            Harness 不保存知识原文与向量。
          </p>
          <el-button type="primary" :icon="Plus" @click="openCreate">新增 Provider</el-button>
        </el-empty>
      </div>
    </div>

    <!-- 详情抽屉 -->
    <el-drawer
      v-model="drawerOpen"
      class="rag-drawer"
      size="600px"
      @closed="closeDrawer"
    >
      <template #header>
        <div v-if="selected" class="drawer-head">
          <span class="drawer-state" :class="stateMeta(selected.state).tone"></span>
          <div class="drawer-title">
            <code>{{ selected.provider_id }}</code>
            <small>{{ selected.name }} · {{ stateMeta(selected.state).label }}</small>
          </div>
        </div>
      </template>

      <div v-if="selected" class="drawer-body">
        <div class="drawer-actions">
          <el-button
            size="small"
            :icon="VideoPlay"
            :loading="probingId === selected.provider_id"
            @click="probe(selected)"
          >
            健康检查
          </el-button>
          <el-button size="small" :icon="Edit" @click="openEdit(selected)">编辑</el-button>
          <el-button
            size="small"
            type="danger"
            :icon="Delete"
            :loading="deleting"
            @click="askDelete(selected)"
          >
            删除
          </el-button>
        </div>

        <el-tabs v-model="drawerTab" class="drawer-tabs" @tab-change="switchTab">
          <el-tab-pane label="概览" name="overview">
            <div v-if="selected">
              <div v-if="selected.last_error" class="error-box">
                <strong>最近错误</strong>
                <p>{{ selected.last_error }}</p>
              </div>
              <section class="detail-block">
                <h4>连接</h4>
                <dl class="kv-grid">
                  <dt>协议</dt><dd>{{ selected.protocol }}</dd>
                  <dt>endpoint</dt><dd class="mono">{{ selected.endpoint || '—' }}</dd>
                  <dt>MCP 服务</dt><dd class="mono">{{ selected.mcp_server_id || '—' }}</dd>
                  <dt>集成后端</dt><dd class="mono">{{ selected.integration_server_id || '—' }}</dd>
                  <dt>来源</dt><dd>{{ selected.source === 'mcp_discovery' ? 'MCP 发现' : '手动配置' }}</dd>
                  <dt>超时</dt><dd>{{ selected.timeout_ms }} ms</dd>
                  <dt>启用</dt><dd>{{ selected.enabled ? '是' : '否' }}</dd>
                </dl>
              </section>
              <section class="detail-block">
                <h4>能力</h4>
                <p class="filter-line">{{ selected.capabilities.join(', ') }}</p>
                <h4>知识域</h4>
                <p class="filter-line">{{ selected.knowledge_domains.join(', ') || '未限制' }}</p>
                <h4>限制</h4>
                <p class="filter-line">
                  top_k ≤ {{ selected.limits?.top_k_max ?? '—' }} · 单片段 ≤
                  {{ selected.limits?.max_chars_per_chunk ?? '—' }} 字符
                </p>
              </section>
            </div>
          </el-tab-pane>

          <el-tab-pane label="知识管理" name="knowledge">
            <div class="tools-head">
              <span>{{ documents.length }} 个文档</span>
              <el-button size="small" :icon="Refresh" :loading="docsLoading" @click="loadKnowledge">
                刷新
              </el-button>
            </div>
            <div v-if="stats" class="stats-box">
              <pre>{{ JSON.stringify(stats, null, 2) }}</pre>
            </div>

            <div class="ingest-box">
              <el-form label-position="top">
                <el-form-item label="文档标题（纯文本灌入时使用）">
                  <el-input v-model="ingestTitle" placeholder="例如：合同管理办法" />
                </el-form-item>
                <el-form-item label="灌入内容（纯文本或 JSON 文档数组）">
                  <el-input
                    v-model="ingestText"
                    type="textarea"
                    :rows="6"
                    spellcheck="false"
                    placeholder='纯文本，或 [{"title":"...","content":"..."}]'
                  />
                </el-form-item>
              </el-form>
              <div class="ingest-actions">
                <el-button type="primary" :loading="ingestBusy" @click="submitIngest">
                  {{ ingestBusy ? '提交中…' : '提交灌入' }}
                </el-button>
              </div>
              <p v-if="ingestNotice" class="notice-line">{{ ingestNotice }}</p>
            </div>

            <el-alert v-if="docsError" :title="docsError" type="error" :closable="false" show-icon />
            <div v-if="docsLoading" class="drawer-empty">正在读取文档…</div>
            <div v-else-if="documents.length === 0" class="drawer-empty">
              该 Provider 未返回文档列表，或知识库为空。
            </div>
            <ul v-else class="doc-list">
              <li v-for="(doc, index) in documents" :key="documentId(doc) || index">
                <span class="doc-title">{{ doc.title || doc.name || documentId(doc) || '未命名文档' }}</span>
                <el-tag v-if="doc.status" size="small" effect="plain" type="info">{{ doc.status }}</el-tag>
                <el-button
                  type="danger"
                  text
                  size="small"
                  :loading="deletingDocId === documentId(doc)"
                  @click="removeDocument(doc)"
                >
                  删除
                </el-button>
              </li>
            </ul>
          </el-tab-pane>

          <el-tab-pane label="检索测试" name="retrieve">
            <div class="retrieve-box">
              <el-form label-position="top">
                <el-form-item label="检索问题">
                  <el-input
                    v-model="query"
                    type="textarea"
                    :rows="3"
                    placeholder="例如：合同续签流程是什么"
                  />
                </el-form-item>
                <el-form-item label="top_k">
                  <el-input-number v-model="topK" :min="1" :max="20" controls-position="right" />
                </el-form-item>
              </el-form>
              <el-button type="primary" :loading="retrieving" @click="runRetrieve">
                {{ retrieving ? '检索中…' : '检索' }}
              </el-button>
              <el-alert
                v-if="retrieveError"
                :title="retrieveError"
                type="error"
                :closable="false"
                show-icon
              />
            </div>

            <div v-if="retrieveResult" class="retrieve-result">
              <p class="notice-line">
                命中 {{ retrieveResult.chunks.length }} 个片段
                <span v-if="retrieveResult.note"> · {{ retrieveResult.note }}</span>
              </p>
              <ul class="chunk-list">
                <li v-for="chunk in retrieveResult.chunks" :key="chunk.citation_id">
                  <header>
                    <code>{{ chunk.citation_id }}</code>
                    <span>{{ chunk.score ?? '—' }}</span>
                  </header>
                  <p>{{ chunk.content }}</p>
                  <small>{{ JSON.stringify(chunk.source) }}</small>
                </li>
              </ul>
            </div>
          </el-tab-pane>
        </el-tabs>
      </div>
    </el-drawer>

    <!-- 新增/编辑 -->
    <el-dialog
      v-model="formOpen"
      width="840px"
      :title="editingId ? `编辑 ${editingId}` : '新增 RAG Provider'"
      append-to-body
      class="rag-dialog"
    >
      <el-form label-position="top" class="form-grid">
        <el-form-item label="provider_id">
          <el-input
            v-model="form.provider_id"
            :disabled="Boolean(editingId)"
            placeholder="rag_crm_knowledge"
          />
        </el-form-item>
        <el-form-item label="展示名">
          <el-input v-model="form.name" placeholder="CRM 知识库" />
        </el-form-item>
        <el-form-item label="描述" class="full">
          <el-input v-model="form.description" placeholder="知识域、负责人或用途" />
        </el-form-item>
        <el-form-item label="协议">
          <el-select v-model="form.protocol" class="fill">
            <el-option label="HTTP / OpenAPI" value="http" />
            <el-option label="MCP（已接入的 MCP 服务）" value="mcp" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="form.protocol === 'http'" label="endpoint">
          <el-input v-model="form.endpoint" placeholder="https://rag.example.com/api" />
        </el-form-item>
        <el-form-item v-else label="MCP 服务">
          <el-select v-model="form.mcp_server_id" class="fill" placeholder="选择已接入的 MCP 服务">
            <el-option
              v-for="server in mcpServers"
              :key="server.server_id"
              :label="server.server_id"
              :value="server.server_id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="鉴权方式">
          <el-select v-model="form.auth_type" class="fill">
            <el-option label="无需鉴权" value="none" />
            <el-option label="Bearer Token" value="bearer" />
            <el-option label="API Key" value="api_key" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="form.auth_type !== 'none'" label="密钥环境变量">
          <el-input v-model="form.secret_ref" placeholder="RAG_TOKEN" />
        </el-form-item>
        <el-form-item label="知识域（逗号分隔）">
          <el-input v-model="form.domains" placeholder="contract, crm" />
        </el-form-item>
        <el-form-item label="top_k 上限">
          <el-input-number
            v-model="form.top_k_max"
            :min="1"
            :max="50"
            controls-position="right"
            class="fill"
          />
        </el-form-item>
        <el-form-item label="单片段字符上限">
          <el-input-number
            v-model="form.max_chars_per_chunk"
            :min="100"
            :max="8000"
            controls-position="right"
            class="fill"
          />
        </el-form-item>
        <el-form-item label="超时（ms）">
          <el-input-number
            v-model="form.timeout_ms"
            :min="1000"
            :max="300000"
            controls-position="right"
            class="fill"
          />
        </el-form-item>
        <el-form-item v-if="form.protocol === 'mcp'" label="归属集成后端">
          <el-input v-model="form.integration_server_id" placeholder="可空，默认等于 MCP 服务" />
        </el-form-item>
      </el-form>

      <div class="cap-block">
        <span>能力</span>
        <div class="cap-chips">
          <el-check-tag
            v-for="cap in CAPABILITIES"
            :key="cap.key"
            :checked="form.capabilities.includes(cap.key)"
            :class="{ fixed: cap.key === 'retrieve' }"
            @change="toggleCapability(cap.key)"
          >
            {{ cap.label }}
          </el-check-tag>
        </div>
      </div>

      <div class="form-switches">
        <span class="switch-row">
          <span>启用</span>
          <el-switch
            :model-value="form.enabled"
            @update:model-value="(v: string | number | boolean) => (form.enabled = Boolean(v))"
          />
        </span>
      </div>
      <el-alert v-if="formError" :title="formError" type="error" :closable="false" show-icon />
      <template #footer>
        <el-button :disabled="saving" @click="formOpen = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitForm">
          {{ saving ? '保存中…' : '保存' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 从 MCP 发现 -->
    <el-dialog v-model="discoverOpen" width="520px" title="从 MCP 后端发现 RAG" append-to-body>
      <p class="import-hint">
        选择一个已接入的 MCP 服务，Harness 会调用它暴露的发现工具读取 RAG manifest，
        并注册为 Provider。manifest 需包含 provider_id、capabilities、knowledge_domains 等字段。
      </p>
      <el-form label-position="top">
        <el-form-item label="MCP 服务">
          <el-select v-model="discoveryForm.mcp_server_id" class="fill" placeholder="选择 MCP 服务">
            <el-option
              v-for="server in mcpServers"
              :key="server.server_id"
              :label="server.server_id"
              :value="server.server_id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="发现工具">
          <el-input v-model="discoveryForm.discovery_tool" placeholder="rag_manifest" />
        </el-form-item>
      </el-form>
      <div class="form-switches">
        <span class="switch-row">
          <span>发现后立即注册</span>
          <el-switch
            :model-value="discoveryForm.auto_register"
            @update:model-value="
              (v: string | number | boolean) => (discoveryForm.auto_register = Boolean(v))
            "
          />
        </span>
      </div>
      <el-alert v-if="discoverError" :title="discoverError" type="error" :closable="false" show-icon />
      <template #footer>
        <el-button :disabled="discovering" @click="discoverOpen = false">取消</el-button>
        <el-button type="primary" :loading="discovering" @click="submitDiscover">
          {{ discovering ? '发现中…' : '开始发现' }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.app-shell { min-height: 100vh; }
.page { display: flex; flex-direction: column; gap: 12px; padding: 14px 18px 24px; }

.runtime-strip {
  display: flex; align-items: center; justify-content: space-between; gap: 14px;
  padding: 12px 2px; border-top: 1px solid var(--line); flex-wrap: wrap;
}
.runtime-title { display: flex; align-items: center; gap: 10px; }
.title-icon { display: inline-flex; color: var(--accent-cyan); }
.runtime-title strong { display: block; font-size: 15px; }
.runtime-title small { font-size: 11px; color: var(--text-3); }
.runtime-facts { display: flex; align-items: center; gap: 16px; font-size: 11.5px; color: var(--text-3); }
.runtime-facts b { margin-right: 3px; font-size: 15px; color: var(--text-1); }

.toolbar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.search-input { flex: 1; min-width: 220px; max-width: 420px; }

.provider-wrap { min-height: 220px; }
.provider-list { list-style: none; margin: 0; padding: 0; border-top: 1px solid var(--line); }
.provider-row {
  --state-color: var(--text-3);
  display: grid; grid-template-columns: 18px minmax(0, 1fr) 110px 260px; align-items: center;
  gap: 12px; padding: 10px 14px 10px 0; border-bottom: 1px solid var(--line); cursor: pointer;
}
.provider-row.ready { --state-color: var(--green); }
.provider-row.degraded { --state-color: var(--amber); }
.provider-row.down { --state-color: var(--danger); }
.provider-row.disabled { --state-color: var(--text-3); }
.provider-row:hover { background: var(--bg-hover); }
.state-rail { align-self: stretch; display: grid; place-items: center; border-left: 2px solid var(--state-color); }
.state-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--state-color); box-shadow: 0 0 9px var(--state-color); }
.provider-main { min-width: 0; display: flex; flex-direction: column; gap: 3px; }
.provider-head { display: flex; align-items: center; gap: 9px; flex-wrap: wrap; }
.provider-head code { font-family: 'Cascadia Code', Consolas, monospace; font-size: 13px; color: var(--text-1); }
.provider-desc {
  margin: 0; overflow: hidden; font-size: 11.5px; color: var(--text-2);
  text-overflow: ellipsis; white-space: nowrap;
}
.provider-tags { display: flex; gap: 5px; flex-wrap: wrap; font-size: 9.5px; color: var(--text-3); }
.provider-tags em { font-style: normal; opacity: 0.5; }
.provider-metrics { display: flex; gap: 14px; justify-content: flex-end; }
.metric { display: flex; flex-direction: column; align-items: flex-end; min-width: 44px; }
.metric strong { font-size: 15px; color: var(--text-1); font-variant-numeric: tabular-nums; }
.metric small { font-size: 9px; color: var(--text-3); }
.provider-actions { display: flex; align-items: center; justify-content: flex-end; gap: 9px; }

.empty-hint {
  max-width: 520px; margin: 0 0 14px; font-size: 11.5px; line-height: 1.7; color: var(--text-3);
}

/* 详情抽屉 */
.drawer-head { display: flex; align-items: center; gap: 10px; width: 100%; }
.drawer-state { width: 9px; height: 9px; border-radius: 50%; background: var(--text-3); flex: none; }
.drawer-state.ready { background: var(--green); }
.drawer-state.degraded { background: var(--amber); }
.drawer-state.down { background: var(--danger); }
.drawer-state.disabled { background: var(--text-3); }
.drawer-title { flex: 1; min-width: 0; }
.drawer-title code { display: block; font-family: 'Cascadia Code', Consolas, monospace; font-size: 14px; color: var(--text-1); }
.drawer-title small { display: block; margin-top: 2px; font-size: 10.5px; color: var(--text-3); }
.drawer-body { min-height: 100%; padding: 0 0 24px; }
.drawer-actions { display: flex; gap: 8px; flex-wrap: wrap; padding: 10px 16px; }
.drawer-tabs { padding: 0 16px; }
.drawer-tabs :deep(.el-tabs__header) { margin-bottom: 12px; }
.drawer-tabs :deep(.el-tabs__content) { overflow: visible; }
.drawer-empty { padding: 22px 0; font-size: 11.5px; color: var(--text-3); }
.detail-block { margin-bottom: 16px; }
.detail-block h4 { margin: 0 0 7px; font-size: 10.5px; font-weight: 500; color: var(--text-3); }
.kv-grid {
  display: grid; grid-template-columns: 92px minmax(0, 1fr); gap: 5px 10px; margin: 0; padding: 9px 10px;
  font-size: 10.5px; border: 1px solid var(--line); border-radius: var(--radius-sm);
}
.kv-grid dt { color: var(--text-3); }
.kv-grid dd { margin: 0; overflow-wrap: anywhere; color: var(--text-2); }
.kv-grid dd.mono { font-family: 'Cascadia Code', Consolas, monospace; font-size: 10px; }
.filter-line { margin: 0 0 6px; font-size: 10.5px; color: var(--text-2); }
.error-box {
  margin-bottom: 12px; padding: 9px 10px; border: 1px solid var(--line-strong); border-radius: var(--radius-sm);
}
.error-box strong { font-size: 11.5px; color: var(--danger); }
.error-box p { margin: 4px 0; font-size: 11px; color: var(--text-2); }

.tools-head {
  display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 9px;
  font-size: 10.5px; color: var(--text-3);
}
.stats-box pre {
  margin: 0 0 10px; max-height: 160px; overflow: auto; padding: 8px 10px;
  font-family: var(--font-mono); font-size: 10px; line-height: 1.5; color: var(--text-2);
  border: 1px solid var(--line); border-radius: var(--radius-sm); background: var(--bg-deep);
}
.ingest-box { display: flex; flex-direction: column; gap: 8px; margin-bottom: 12px; }
.ingest-box :deep(.el-form-item) { margin-bottom: 10px; }
.ingest-actions { display: flex; justify-content: flex-end; }
.notice-line { margin: 0; font-size: 10.5px; line-height: 1.6; color: var(--accent-cyan); }
.doc-list { list-style: none; margin: 0; padding: 0; }
.doc-list li {
  display: flex; align-items: center; gap: 8px; padding: 7px 4px; font-size: 11px;
  color: var(--text-2); border-bottom: 1px solid var(--line);
}
.doc-title { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.retrieve-box { display: flex; flex-direction: column; gap: 8px; }
.retrieve-box :deep(.el-form-item) { margin-bottom: 10px; }
.retrieve-box > .el-button { align-self: flex-end; }
.retrieve-result { margin-top: 12px; }
.chunk-list { list-style: none; margin: 8px 0 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.chunk-list li { padding: 8px 10px; border: 1px solid var(--line); border-radius: var(--radius-sm); }
.chunk-list header { display: flex; justify-content: space-between; gap: 8px; }
.chunk-list code { font-family: 'Cascadia Code', Consolas, monospace; font-size: 10px; color: var(--accent-cyan); }
.chunk-list header span { font-size: 10px; color: var(--text-3); }
.chunk-list p { margin: 6px 0; font-size: 11px; line-height: 1.65; color: var(--text-2); }
.chunk-list small { font-size: 9.5px; color: var(--text-3); word-break: break-all; }

/* 表单 */
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0 16px; }
.form-grid .full { grid-column: 1 / -1; }
.fill { width: 100%; }
.cap-block { margin-top: 4px; font-size: 11.5px; color: var(--text-2); }
.cap-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
.cap-chips .fixed { opacity: 0.75; cursor: default; }
.form-switches { display: flex; gap: 24px; flex-wrap: wrap; padding: 12px 0 4px; }
.switch-row { display: inline-flex; align-items: center; gap: 9px; font-size: 11.5px; color: var(--text-2); }
.import-hint { margin: 0 0 12px; font-size: 11.5px; line-height: 1.65; color: var(--text-3); }

@media (max-width: 1100px) {
  .provider-row { grid-template-columns: 18px minmax(0, 1fr) 130px; }
  .provider-metrics { display: none; }
  .form-grid { grid-template-columns: 1fr; }
}
@media (max-width: 760px) {
  .provider-row { grid-template-columns: 18px minmax(0, 1fr); }
  .provider-actions { grid-column: 2 / -1; justify-content: flex-start; padding-top: 6px; }
  :deep(.rag-drawer) { width: 100% !important; }
}
</style>
