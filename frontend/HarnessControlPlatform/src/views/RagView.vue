<script setup lang="ts">
// 知识库：RAG Provider 注册、检索测试、知识灌入与管理
// Harness 只保存 Provider 配置，不保存知识原文、切片与向量。
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import TopNav from '@/components/console/TopNav.vue'
import AppIcon from '@/components/console/AppIcon.vue'
import UiSwitch from '@/components/console/UiSwitch.vue'
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

const toast = ref('')
const toastType = ref<'ok' | 'err'>('ok')
let toastTimer: number | undefined

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
const drawerTab = ref<'overview' | 'knowledge' | 'retrieve'>('overview')
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
const topK = ref(5)
const retrieving = ref(false)
const retrieveError = ref('')
const retrieveResult = ref<{ chunks: RagChunk[]; errors: { provider_id: string; message: string }[]; note?: string } | null>(null)

const deletes = ref<RagProviderItem | null>(null)
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
  top_k_max: 20,
  max_chars_per_chunk: 2000,
  timeout_ms: 30000,
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

function flash(message: string, type: 'ok' | 'err' = 'ok') {
  toast.value = message
  toastType.value = type
  if (toastTimer) window.clearTimeout(toastTimer)
  toastTimer = window.setTimeout(() => (toast.value = ''), 3200)
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

async function confirmDelete() {
  const item = deletes.value
  if (!item || deleting.value) return
  deleting.value = true
  try {
    await deleteRagProvider(item.provider_id)
    if (selected.value?.provider_id === item.provider_id) closeDrawer()
    deletes.value = null
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

async function switchTab(tab: 'overview' | 'knowledge' | 'retrieve') {
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
  if (toastTimer) window.clearTimeout(toastTimer)
  if (polling.value) window.clearInterval(polling.value)
})
</script>

<template>
  <div class="app-shell">
    <TopNav />

    <div class="page">
      <section class="runtime-strip">
        <div class="runtime-title">
          <span class="title-icon"><AppIcon name="book" :size="15" /></span>
          <div>
            <strong>知识库 / RAG</strong>
            <small>Provider 注册 · 统一检索 · 知识灌入与管理</small>
          </div>
        </div>
        <div class="runtime-facts">
          <span><b>{{ providers.length }}</b> 个 Provider</span>
          <span><b>{{ readyCount }}</b> 就绪</span>
          <button class="icon-btn" type="button" title="刷新" @click="load">
            <AppIcon name="timer" :size="13" />
          </button>
        </div>
      </section>

      <section class="toolbar">
        <div class="search-box">
          <AppIcon name="search" :size="13" />
          <input v-model="keyword" type="text" placeholder="搜索 Provider、协议或知识域…" />
        </div>
        <button class="ghost-btn" type="button" @click="openDiscover">
          <AppIcon name="server" :size="13" /> 从 MCP 发现
        </button>
        <button class="add-btn" type="button" @click="openCreate">
          <AppIcon name="plus" :size="14" /> 新增 Provider
        </button>
      </section>

      <p v-if="error" class="error-note">{{ error }}</p>

      <div v-if="loading && providers.length === 0" class="panel loading">
        <i class="spinner"></i> 正在读取 RAG Provider…
      </div>

      <ul v-else-if="filtered.length" class="provider-list">
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
              <span class="state-label">{{ stateMeta(item.state).label }}</span>
              <span class="protocol-chip">{{ item.protocol.toUpperCase() }}</span>
              <span v-if="item.integration_server_id" class="int-chip">
                集成 {{ item.integration_server_id }}
              </span>
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
            <button
              class="mini-btn"
              type="button"
              :disabled="probingId === item.provider_id || !item.enabled"
              @click="probe(item)"
            >
              <AppIcon name="play" :size="11" />
              {{ probingId === item.provider_id ? '检测中' : '健康检查' }}
            </button>
            <UiSwitch
              :model-value="item.enabled"
              :disabled="togglingId === item.provider_id"
              @update:model-value="(value) => toggleEnabled(item, value)"
            />
            <button class="icon-btn" type="button" title="详情" @click="openDrawer(item)">
              <AppIcon name="chevronRight" :size="12" />
            </button>
          </div>
        </li>
      </ul>

      <div v-else class="panel empty">
        <div class="empty-orb"><AppIcon name="book" :size="24" /></div>
        <h3>还没有 RAG Provider</h3>
        <p>
          接入一个外部 RAG/知识库后，Agent 会通过 knowledge_search 工具检索知识并引用来源。
          Harness 不保存知识原文与向量。
        </p>
        <button class="add-btn" type="button" @click="openCreate">
          <AppIcon name="plus" :size="14" /> 新增 Provider
        </button>
      </div>
    </div>

    <!-- 详情抽屉 -->
    <Transition name="drawer">
      <aside v-if="drawerOpen && selected" class="drawer">
        <header class="drawer-head">
          <span class="drawer-state" :class="stateMeta(selected.state).tone"></span>
          <div class="drawer-title">
            <code>{{ selected.provider_id }}</code>
            <small>{{ selected.name }} · {{ stateMeta(selected.state).label }}</small>
          </div>
          <button class="icon-btn" type="button" title="关闭" @click="closeDrawer">
            <AppIcon name="close" :size="13" />
          </button>
        </header>

        <div class="drawer-actions">
          <button type="button" :disabled="probingId === selected.provider_id" @click="probe(selected)">
            <AppIcon name="play" :size="12" /> 健康检查
          </button>
          <button type="button" @click="openEdit(selected)">
            <AppIcon name="edit" :size="12" /> 编辑
          </button>
          <button class="danger" type="button" @click="deletes = selected">
            <AppIcon name="trash" :size="12" /> 删除
          </button>
        </div>

        <nav class="drawer-tabs">
          <button type="button" :class="{ active: drawerTab === 'overview' }" @click="switchTab('overview')">概览</button>
          <button type="button" :class="{ active: drawerTab === 'knowledge' }" @click="switchTab('knowledge')">知识管理</button>
          <button type="button" :class="{ active: drawerTab === 'retrieve' }" @click="switchTab('retrieve')">检索测试</button>
        </nav>

        <div class="drawer-body">
          <template v-if="drawerTab === 'overview'">
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
          </template>

          <template v-else-if="drawerTab === 'knowledge'">
            <div class="tools-head">
              <span>{{ documents.length }} 个文档</span>
              <button type="button" :disabled="docsLoading" @click="loadKnowledge">
                <AppIcon name="timer" :size="11" /> 刷新
              </button>
            </div>
            <div v-if="stats" class="stats-box">
              <pre>{{ JSON.stringify(stats, null, 2) }}</pre>
            </div>

            <div class="ingest-box">
              <label>
                <span>文档标题（纯文本灌入时使用）</span>
                <input v-model="ingestTitle" type="text" placeholder="例如：合同管理办法" />
              </label>
              <label>
                <span>灌入内容（纯文本或 JSON 文档数组）</span>
                <textarea
                  v-model="ingestText"
                  rows="6"
                  spellcheck="false"
                  placeholder='纯文本，或 [{"title":"...","content":"..."}]'
                ></textarea>
              </label>
              <div class="ingest-actions">
                <button class="primary" type="button" :disabled="ingestBusy" @click="submitIngest">
                  {{ ingestBusy ? '提交中…' : '提交灌入' }}
                </button>
              </div>
              <p v-if="ingestNotice" class="notice-line">{{ ingestNotice }}</p>
            </div>

            <p v-if="docsError" class="error-note">{{ docsError }}</p>
            <div v-if="docsLoading" class="drawer-empty">正在读取文档…</div>
            <div v-else-if="documents.length === 0" class="drawer-empty">
              该 Provider 未返回文档列表，或知识库为空。
            </div>
            <ul v-else class="doc-list">
              <li v-for="(doc, index) in documents" :key="documentId(doc) || index">
                <span class="doc-title">{{ doc.title || doc.name || documentId(doc) || '未命名文档' }}</span>
                <span class="doc-meta">{{ doc.status || '' }}</span>
                <button
                  type="button"
                  class="danger-text"
                  :disabled="deletingDocId === documentId(doc)"
                  @click="removeDocument(doc)"
                >
                  删除
                </button>
              </li>
            </ul>
          </template>

          <template v-else>
            <div class="retrieve-box">
              <label>
                <span>检索问题</span>
                <textarea v-model="query" rows="3" placeholder="例如：合同续签流程是什么"></textarea>
              </label>
              <label class="inline">
                <span>top_k</span>
                <input v-model.number="topK" type="number" min="1" max="20" />
              </label>
              <button class="primary" type="button" :disabled="retrieving" @click="runRetrieve">
                {{ retrieving ? '检索中…' : '检索' }}
              </button>
              <p v-if="retrieveError" class="error-note">{{ retrieveError }}</p>
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
          </template>
        </div>
      </aside>
    </Transition>

    <!-- 新增/编辑 -->
    <Transition name="fade">
      <div v-if="formOpen" class="modal-mask" @click.self="formOpen = false">
        <div class="dialog wide">
          <header class="dialog-head">
            <strong>{{ editingId ? `编辑 ${editingId}` : '新增 RAG Provider' }}</strong>
            <button class="icon-btn" type="button" @click="formOpen = false">
              <AppIcon name="close" :size="13" />
            </button>
          </header>
          <div class="dialog-body">
            <div class="form-grid">
              <label>
                <span>provider_id</span>
                <input v-model="form.provider_id" type="text" :disabled="Boolean(editingId)" placeholder="rag_crm_knowledge" />
              </label>
              <label>
                <span>展示名</span>
                <input v-model="form.name" type="text" placeholder="CRM 知识库" />
              </label>
              <label class="full">
                <span>描述</span>
                <input v-model="form.description" type="text" placeholder="知识域、负责人或用途" />
              </label>
              <label>
                <span>协议</span>
                <select v-model="form.protocol">
                  <option value="http">HTTP / OpenAPI</option>
                  <option value="mcp">MCP（已接入的 MCP 服务）</option>
                </select>
              </label>
              <label v-if="form.protocol === 'http'">
                <span>endpoint</span>
                <input v-model="form.endpoint" type="text" placeholder="https://rag.example.com/api" />
              </label>
              <label v-else>
                <span>MCP 服务</span>
                <select v-model="form.mcp_server_id">
                  <option value="">选择已接入的 MCP 服务</option>
                  <option v-for="server in mcpServers" :key="server.server_id" :value="server.server_id">
                    {{ server.server_id }}
                  </option>
                </select>
              </label>
              <label>
                <span>鉴权方式</span>
                <select v-model="form.auth_type">
                  <option value="none">无需鉴权</option>
                  <option value="bearer">Bearer Token</option>
                  <option value="api_key">API Key</option>
                </select>
              </label>
              <label v-if="form.auth_type !== 'none'">
                <span>密钥环境变量</span>
                <input v-model="form.secret_ref" type="text" placeholder="RAG_TOKEN" />
              </label>
              <label>
                <span>知识域（逗号分隔）</span>
                <input v-model="form.domains" type="text" placeholder="contract, crm" />
              </label>
              <label>
                <span>top_k 上限</span>
                <input v-model.number="form.top_k_max" type="number" min="1" max="50" />
              </label>
              <label>
                <span>单片段字符上限</span>
                <input v-model.number="form.max_chars_per_chunk" type="number" min="100" max="8000" />
              </label>
              <label>
                <span>超时（ms）</span>
                <input v-model.number="form.timeout_ms" type="number" min="1000" max="300000" />
              </label>
              <label v-if="form.protocol === 'mcp'">
                <span>归属集成后端</span>
                <input v-model="form.integration_server_id" type="text" placeholder="可空，默认等于 MCP 服务" />
              </label>
            </div>

            <div class="cap-block">
              <span>能力</span>
              <div class="cap-chips">
                <button
                  v-for="cap in CAPABILITIES"
                  :key="cap.key"
                  type="button"
                  class="cap-chip"
                  :class="{ on: form.capabilities.includes(cap.key), fixed: cap.key === 'retrieve' }"
                  @click="toggleCapability(cap.key)"
                >
                  {{ cap.label }}
                </button>
              </div>
            </div>

            <div class="form-switches">
              <span class="switch-row">
                <span>启用</span>
                <UiSwitch v-model="form.enabled" />
              </span>
            </div>
            <p v-if="formError" class="form-error">{{ formError }}</p>
          </div>
          <footer class="dialog-foot">
            <button class="ghost-btn" type="button" :disabled="saving" @click="formOpen = false">取消</button>
            <button class="primary" type="button" :disabled="saving" @click="submitForm">
              {{ saving ? '保存中…' : '保存' }}
            </button>
          </footer>
        </div>
      </div>
    </Transition>

    <!-- 从 MCP 发现 -->
    <Transition name="fade">
      <div v-if="discoverOpen" class="modal-mask" @click.self="discoverOpen = false">
        <div class="dialog small">
          <header class="dialog-head">
            <strong>从 MCP 后端发现 RAG</strong>
            <button class="icon-btn" type="button" @click="discoverOpen = false">
              <AppIcon name="close" :size="13" />
            </button>
          </header>
          <div class="dialog-body">
            <p class="import-hint">
              选择一个已接入的 MCP 服务，Harness 会调用它暴露的发现工具读取 RAG manifest，
              并注册为 Provider。manifest 需包含 provider_id、capabilities、knowledge_domains 等字段。
            </p>
            <div class="form-grid">
              <label class="full">
                <span>MCP 服务</span>
                <select v-model="discoveryForm.mcp_server_id">
                  <option value="">选择 MCP 服务</option>
                  <option v-for="server in mcpServers" :key="server.server_id" :value="server.server_id">
                    {{ server.server_id }}
                  </option>
                </select>
              </label>
              <label class="full">
                <span>发现工具</span>
                <input v-model="discoveryForm.discovery_tool" type="text" placeholder="rag_manifest" />
              </label>
            </div>
            <div class="form-switches">
              <span class="switch-row">
                <span>发现后立即注册</span>
                <UiSwitch v-model="discoveryForm.auto_register" />
              </span>
            </div>
            <p v-if="discoverError" class="form-error">{{ discoverError }}</p>
          </div>
          <footer class="dialog-foot">
            <button class="ghost-btn" type="button" :disabled="discovering" @click="discoverOpen = false">取消</button>
            <button class="primary" type="button" :disabled="discovering" @click="submitDiscover">
              {{ discovering ? '发现中…' : '开始发现' }}
            </button>
          </footer>
        </div>
      </div>
    </Transition>

    <!-- 删除确认 -->
    <Transition name="fade">
      <div v-if="deletes" class="modal-mask" @click.self="deletes = null">
        <div class="dialog small">
          <header class="dialog-head">
            <strong>删除 RAG Provider</strong>
            <button class="icon-btn" type="button" @click="deletes = null">
              <AppIcon name="close" :size="13" />
            </button>
          </header>
          <div class="dialog-body">
            <p class="confirm-text">
              确定删除 <b>{{ deletes.provider_id }}</b> 吗？只删除 Harness 侧的注册配置，
              不影响后端知识库。
            </p>
          </div>
          <footer class="dialog-foot">
            <button class="ghost-btn" type="button" :disabled="deleting" @click="deletes = null">取消</button>
            <button class="danger-btn" type="button" :disabled="deleting" @click="confirmDelete">
              {{ deleting ? '删除中…' : '确认删除' }}
            </button>
          </footer>
        </div>
      </div>
    </Transition>

    <Transition name="fade">
      <div v-if="toast" class="toast" :class="toastType">{{ toast }}</div>
    </Transition>
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
.title-icon { color: var(--accent-cyan); }
.runtime-title strong { display: block; font-size: 15px; }
.runtime-title small { font-size: 11px; color: var(--text-3); }
.runtime-facts { display: flex; align-items: center; gap: 16px; font-size: 11.5px; color: var(--text-3); }
.runtime-facts b { margin-right: 3px; font-size: 15px; color: var(--text-1); }

.toolbar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.search-box {
  display: flex; align-items: center; gap: 7px; flex: 1; min-width: 220px; max-width: 420px;
  padding: 7px 10px; color: var(--text-3); border: 1px solid var(--line-strong);
  border-radius: var(--radius-sm); background: var(--bg-deep);
}
.search-box input {
  flex: 1; min-width: 0; font-size: 12.5px; font-family: inherit; color: var(--text-1);
  border: none; background: transparent; outline: none;
}
.add-btn {
  display: inline-flex; align-items: center; gap: 6px; padding: 7px 13px;
  font-size: 12px; color: #fff; border: none; border-radius: var(--radius-sm); background: var(--accent);
}
.ghost-btn {
  display: inline-flex; align-items: center; gap: 6px; padding: 7px 12px; font-size: 12px;
  color: var(--text-2); border: 1px solid var(--line); border-radius: var(--radius-sm); background: transparent;
}
.ghost-btn:hover { color: var(--text-1); border-color: var(--line-strong); }

.error-note {
  margin: 0; padding: 8px 10px; font-size: 12px; color: var(--amber);
  border: 1px solid rgba(242, 176, 60, 0.3); border-radius: var(--radius-sm);
}
.loading, .empty {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  min-height: 280px; gap: 10px; color: var(--text-3); font-size: 12px; text-align: center;
}
.loading { flex-direction: row; }
.spinner {
  width: 16px; height: 16px; border: 2px solid transparent;
  border-top-color: var(--accent-cyan); border-radius: 50%; animation: spin 0.75s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
.empty-orb {
  display: grid; place-items: center; width: 44px; height: 44px; border-radius: 50%;
  color: var(--accent-cyan); border: 1px solid var(--line-strong);
}
.empty h3 { margin: 0; font-size: 16px; color: var(--text-1); }
.empty p { max-width: 520px; margin: 0; font-size: 11.5px; line-height: 1.7; }

.provider-list { list-style: none; margin: 0; padding: 0; border-top: 1px solid var(--line); }
.provider-row {
  --state-color: #6f86b3;
  display: grid; grid-template-columns: 18px minmax(0, 1fr) 110px 220px; align-items: center;
  gap: 12px; padding: 10px 14px 10px 0; border-bottom: 1px solid var(--line); cursor: pointer;
}
.provider-row.ready { --state-color: #37d0c4; }
.provider-row.degraded { --state-color: #f2b03c; }
.provider-row.down { --state-color: #e2605e; }
.provider-row.disabled { --state-color: #4b5b7a; }
.provider-row:hover { background: var(--bg-hover); }
.state-rail { align-self: stretch; display: grid; place-items: center; border-left: 2px solid var(--state-color); }
.state-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--state-color); box-shadow: 0 0 9px var(--state-color); }
.provider-main { min-width: 0; display: flex; flex-direction: column; gap: 3px; }
.provider-head { display: flex; align-items: center; gap: 9px; flex-wrap: wrap; }
.provider-head code { font-family: 'Cascadia Code', Consolas, monospace; font-size: 13px; color: var(--text-1); }
.state-label { font-size: 10.5px; color: var(--state-color); }
.protocol-chip {
  padding: 1px 6px; font-size: 9px; color: var(--text-2); border: 1px solid var(--line); border-radius: var(--radius-sm);
}
.int-chip {
  padding: 1px 6px; font-size: 9px; color: var(--accent-cyan);
  border: 1px solid rgba(55, 208, 196, 0.35); border-radius: var(--radius-sm);
}
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
.mini-btn {
  display: inline-flex; align-items: center; gap: 5px; padding: 4px 9px; font-size: 10.5px;
  color: var(--text-2); border: 1px solid var(--line); border-radius: var(--radius-sm); background: transparent;
}
.mini-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.icon-btn {
  display: grid; place-items: center; width: 28px; height: 28px; color: var(--text-3);
  border: 1px solid var(--line); border-radius: var(--radius-sm); background: transparent;
}

/* Drawer */
.drawer {
  position: fixed; top: var(--topbar-h); right: 0; bottom: 0; z-index: 45; width: 600px;
  display: flex; flex-direction: column; border-left: 1px solid var(--line-strong);
  background: var(--bg-elevated); box-shadow: -12px 0 28px rgba(2, 6, 14, 0.42);
}
.drawer-head { display: flex; align-items: center; gap: 10px; padding: 14px 16px; border-bottom: 1px solid var(--line); }
.drawer-state { width: 9px; height: 9px; border-radius: 50%; background: #6f86b3; }
.drawer-state.ready { background: #37d0c4; }
.drawer-state.degraded { background: #f2b03c; }
.drawer-state.down { background: #e2605e; }
.drawer-state.disabled { background: #4b5b7a; }
.drawer-title { flex: 1; min-width: 0; }
.drawer-title code { display: block; font-family: 'Cascadia Code', Consolas, monospace; font-size: 14px; color: var(--text-1); }
.drawer-title small { display: block; margin-top: 2px; font-size: 10.5px; color: var(--text-3); }
.drawer-actions { display: flex; gap: 8px; flex-wrap: wrap; padding: 10px 16px; }
.drawer-actions button {
  display: inline-flex; align-items: center; gap: 5px; padding: 5px 10px; font-size: 11px;
  color: var(--text-2); border: 1px solid var(--line); border-radius: var(--radius-sm); background: transparent;
}
.drawer-actions button.danger { color: var(--danger); border-color: rgba(226, 96, 94, 0.38); }
.drawer-tabs { display: flex; gap: 4px; padding: 0 16px 8px; border-bottom: 1px solid var(--line); }
.drawer-tabs button {
  padding: 6px 12px; font-size: 11.5px; color: var(--text-3); border: 1px solid transparent;
  border-radius: var(--radius-sm); background: transparent;
}
.drawer-tabs button.active {
  color: var(--accent-cyan); border-color: rgba(55, 208, 196, 0.35); background: rgba(55, 208, 196, 0.07);
}
.drawer-body { flex: 1; min-height: 0; overflow-y: auto; padding: 12px 16px 24px; }
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
  margin-bottom: 12px; padding: 9px 10px; border: 1px solid rgba(226, 96, 94, 0.35); border-radius: var(--radius-sm);
}
.error-box strong { font-size: 11.5px; color: var(--danger); }
.error-box p { margin: 4px 0; font-size: 11px; color: var(--text-2); }

.tools-head {
  display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 9px;
  font-size: 10.5px; color: var(--text-3);
}
.tools-head button {
  display: inline-flex; align-items: center; gap: 4px; padding: 4px 9px; font-size: 10px;
  color: var(--text-2); border: 1px solid var(--line); border-radius: var(--radius-sm); background: transparent;
}
.stats-box pre {
  margin: 0 0 10px; max-height: 160px; overflow: auto; padding: 8px 10px;
  font-family: var(--font-mono); font-size: 10px; line-height: 1.5; color: var(--text-2);
  border: 1px solid var(--line); border-radius: var(--radius-sm); background: var(--bg-deep);
}
.ingest-box { display: flex; flex-direction: column; gap: 8px; margin-bottom: 12px; }
.ingest-box label { display: flex; flex-direction: column; gap: 4px; font-size: 10.5px; color: var(--text-3); }
.ingest-box input, .ingest-box textarea {
  width: 100%; padding: 8px 10px; font-size: 11.5px; font-family: var(--font-mono); color: var(--text-1);
  border: 1px solid var(--line-strong); border-radius: var(--radius-sm); background: var(--bg-deep);
  resize: vertical; outline: none;
}
.ingest-actions { display: flex; justify-content: flex-end; }
.primary {
  padding: 6px 14px; font-size: 11.5px; color: #fff; border: none;
  border-radius: var(--radius-sm); background: var(--accent);
}
.notice-line { margin: 0; font-size: 10.5px; line-height: 1.6; color: var(--accent-cyan); }
.doc-list { list-style: none; margin: 0; padding: 0; }
.doc-list li {
  display: flex; align-items: center; gap: 8px; padding: 7px 4px; font-size: 11px;
  color: var(--text-2); border-bottom: 1px solid var(--line);
}
.doc-title { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.doc-meta { font-size: 9.5px; color: var(--text-3); }
.danger-text { padding: 2px 6px; font-size: 10px; color: var(--danger); background: transparent; border: 0; }

.retrieve-box { display: flex; flex-direction: column; gap: 8px; }
.retrieve-box label { display: flex; flex-direction: column; gap: 4px; font-size: 10.5px; color: var(--text-3); }
.retrieve-box label.inline { flex-direction: row; align-items: center; gap: 8px; }
.retrieve-box textarea, .retrieve-box input {
  padding: 8px 10px; font-size: 11.5px; color: var(--text-1); border: 1px solid var(--line-strong);
  border-radius: var(--radius-sm); background: var(--bg-deep); outline: none; resize: vertical;
}
.retrieve-box label.inline input { width: 80px; }
.retrieve-box button { align-self: flex-end; }
.retrieve-result { margin-top: 12px; }
.chunk-list { list-style: none; margin: 8px 0 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.chunk-list li { padding: 8px 10px; border: 1px solid var(--line); border-radius: var(--radius-sm); }
.chunk-list header { display: flex; justify-content: space-between; gap: 8px; }
.chunk-list code { font-family: 'Cascadia Code', Consolas, monospace; font-size: 10px; color: var(--accent-cyan); }
.chunk-list header span { font-size: 10px; color: var(--text-3); }
.chunk-list p { margin: 6px 0; font-size: 11px; line-height: 1.65; color: var(--text-2); }
.chunk-list small { font-size: 9.5px; color: var(--text-3); word-break: break-all; }

/* Dialogs */
.modal-mask {
  position: fixed; inset: 0; z-index: 60; display: grid; place-items: center; padding: 24px;
  background: rgba(2, 7, 18, 0.62); backdrop-filter: blur(4px);
}
.dialog {
  width: min(760px, 100%); max-height: calc(100vh - 80px); display: flex; flex-direction: column;
  border: 1px solid var(--line-strong); border-radius: var(--radius-lg); background: var(--bg-panel);
  box-shadow: 0 16px 40px rgba(2, 6, 14, 0.5);
}
.dialog.small { width: min(520px, 100%); }
.dialog.wide { width: min(840px, 100%); }
.dialog-head { display: flex; align-items: center; justify-content: space-between; padding: 14px 16px; border-bottom: 1px solid var(--line); }
.dialog-body { flex: 1; min-height: 0; overflow-y: auto; padding: 14px 16px; }
.dialog-foot { display: flex; justify-content: flex-end; gap: 10px; padding: 12px 16px; border-top: 1px solid var(--line); }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.form-grid label { display: flex; flex-direction: column; gap: 5px; font-size: 11.5px; color: var(--text-2); }
.form-grid label.full { grid-column: 1 / -1; }
.form-grid input, .form-grid select {
  width: 100%; padding: 8px 10px; font-size: 12px; font-family: inherit; color: var(--text-1);
  border: 1px solid var(--line-strong); border-radius: var(--radius-sm); background: var(--bg-deep); outline: none;
}
.cap-block { margin-top: 12px; font-size: 11.5px; color: var(--text-2); }
.cap-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
.cap-chip {
  padding: 4px 10px; font-size: 10.5px; color: var(--text-3); border: 1px solid var(--line);
  border-radius: var(--radius-sm); background: transparent;
}
.cap-chip.on { color: var(--accent-cyan); border-color: rgba(55, 208, 196, 0.35); background: rgba(55, 208, 196, 0.07); }
.cap-chip.fixed { opacity: 0.8; cursor: default; }
.form-switches { display: flex; gap: 24px; flex-wrap: wrap; padding: 12px 0 4px; }
.switch-row { display: inline-flex; align-items: center; gap: 9px; font-size: 11.5px; color: var(--text-2); }
.import-hint { margin: 0 0 10px; font-size: 11.5px; line-height: 1.65; color: var(--text-3); }
.form-error { margin: 8px 0 0; font-size: 11.5px; color: var(--danger); }
.confirm-text { margin: 0; font-size: 12.5px; line-height: 1.7; color: var(--text-2); }
.ghost-btn { color: var(--text-2); border: 1px solid var(--line-strong); background: transparent; padding: 7px 14px; border-radius: var(--radius-sm); }
.danger-btn { color: #fff; background: var(--danger); border: none; padding: 7px 14px; border-radius: var(--radius-sm); }

.toast {
  position: fixed; left: 50%; bottom: 28px; z-index: 80; transform: translateX(-50%);
  padding: 9px 16px; font-size: 12px; color: var(--text-1);
  border: 1px solid rgba(55, 208, 196, 0.35); border-radius: var(--radius-sm); background: var(--bg-elevated);
}
.toast.err { color: var(--danger); border-color: rgba(226, 96, 94, 0.45); }

.drawer-enter-active, .drawer-leave-active { transition: transform 0.26s cubic-bezier(0.22, 1, 0.36, 1), opacity 0.2s; }
.drawer-enter-from, .drawer-leave-to { transform: translateX(24px); opacity: 0; }
.fade-enter-active, .fade-leave-active { transition: opacity 0.2s ease; }
.fade-enter-from, .fade-leave-to { opacity: 0; }

@media (max-width: 1100px) {
  .provider-row { grid-template-columns: 18px minmax(0, 1fr) 120px; }
  .provider-actions { grid-column: 2 / -1; justify-content: flex-start; padding-top: 6px; }
  .provider-metrics { justify-content: flex-start; }
  .form-grid { grid-template-columns: 1fr; }
}
@media (max-width: 760px) {
  .provider-row { grid-template-columns: 18px minmax(0, 1fr); }
  .provider-metrics { grid-column: 2; }
  .drawer { width: 100%; }
}
</style>
