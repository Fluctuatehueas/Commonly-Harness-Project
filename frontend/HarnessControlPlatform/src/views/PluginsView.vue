<script setup lang="ts">
// 插件市场：单页展示全部插件，卡片点开右侧可拖拽抽屉
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Compass, Search, Upload } from '@element-plus/icons-vue'
import TopNav from '@/components/console/TopNav.vue'
import PluginCard from '@/components/plugins/PluginCard.vue'
import PluginDetailDrawer from '@/components/plugins/PluginDetailDrawer.vue'
import PluginJobBar from '@/components/plugins/PluginJobBar.vue'
import {
  deletePlugin,
  fetchPluginCatalog,
  fetchPluginJobs,
  fetchPlugins,
  fetchPluginUpdates,
  importDshSkill,
  restartPlugin,
  startPluginInstall,
  togglePlugin,
  updatePlugin,
  uploadPluginZip,
} from '@/api/plugins'
import type {
  PluginCardModel,
  PluginInstalledItem,
  PluginJob,
  PluginMarketItem,
  PluginUpdateItem,
} from '@/types/plugins'

const CAPABILITIES = ['tool', 'resource', 'prompt', 'hook', 'ui', 'integration']
const SOURCES = [
  { value: '', label: '全部来源' },
  { value: 'builtin', label: '系统内置' },
  { value: 'user', label: '用户安装' },
  { value: 'community', label: '社区' },
  { value: 'private', label: '私有市场' },
  { value: 'public', label: '公共市场' },
  { value: 'local', label: '本地目录' },
  { value: 'dsh', label: 'DSH 转译' },
  { value: 'remote', label: '远程服务' },
]

const catalog = ref<PluginMarketItem[]>([])
const installed = ref<PluginInstalledItem[]>([])
const updates = ref<PluginUpdateItem[]>([])
const loading = ref(false)
const error = ref('')
const catalogStale = ref(false)
const catalogFetchedAt = ref('')

const keyword = ref('')
const capability = ref('')
const source = ref('')
const sort = ref<'popular' | 'latest' | 'name'>('popular')

// 前端分页：目录一次拉取后在本地过滤排序，再按页渲染
const page = ref(1)
const pageSize = ref(10)

const drawerOpen = ref(false)
const active = ref<PluginCardModel | null>(null)

const activeJobs = ref<PluginJob[]>([])
const jobsClosed = ref(false)
let jobTimer: number | undefined

const uploadInput = ref<HTMLInputElement | null>(null)
const busyId = ref('')

const updateMap = computed(() => {
  const map = new Map<string, PluginUpdateItem>()
  for (const item of updates.value) map.set(item.plugin_id, item)
  return map
})

const installedCount = computed(() => installed.value.length)
const updateCount = computed(() => updates.value.length)

function isUpdate(item: PluginCardModel): boolean {
  if (!item.installed) return false
  return updateMap.value.has(item.plugin_id)
}

function mergedCards(): PluginCardModel[] {
  const map = new Map<string, PluginCardModel>()
  for (const market of catalog.value) {
    map.set(market.plugin_id, { ...market })
  }
  for (const row of installed.value) {
    const patch: Partial<PluginCardModel> = {
      installed: true,
      installed_version: row.version,
      enabled: row.enabled,
      builtin: row.builtin,
      deletable: row.deletable,
      status: row.status,
      has_ui: row.has_ui,
      ui_slots: row.ui_slots,
      health: row.health,
      tool_count: row.tool_count,
      path: row.path,
      error: row.error,
      versions: row.versions,
      author: row.author || { name: 'Local' },
      tag: row.tag || [],
      license: row.license || '',
    }
    const existing = map.get(row.plugin_id)
    if (existing) {
      Object.assign(existing, patch)
      continue
    }
    map.set(row.plugin_id, {
      plugin_id: row.plugin_id,
      name: row.name,
      version: row.version,
      description: row.description,
      source: row.source,
      capabilities: row.capabilities,
      permissions: row.permissions,
      runtime: row.runtime,
      transport: row.transport,
      protocolVersion: row.protocolVersion,
      ui: { slots: row.ui_slots },
      versions: row.versions,
      installable: false,
      ...patch,
    })
  }
  return [...map.values()]
}

const cards = computed(() => {
  let list = mergedCards()
  const needle = keyword.value.trim().toLowerCase()
  if (needle) {
    list = list.filter((item) =>
      [item.plugin_id, item.name, item.description, ...(item.tag || [])]
        .join(' ')
        .toLowerCase()
        .includes(needle),
    )
  }
  if (capability.value) {
    list = list.filter((item) => (item.capabilities || []).includes(capability.value))
  }
  if (source.value) {
    list = list.filter((item) => String(item.source || '') === source.value)
  }
  if (sort.value === 'latest') {
    list.sort((a, b) => String(b.version).localeCompare(String(a.version)))
  } else if (sort.value === 'name') {
    list.sort((a, b) => a.name.localeCompare(b.name, 'zh-CN'))
  } else {
    list.sort(
      (a, b) =>
        Number(b.downloadCount || b.like || 0) -
        Number(a.downloadCount || a.like || 0),
    )
  }
  return list
})

const totalCards = computed(() => cards.value.length)
const pagedCards = computed(() => {
  const start = (page.value - 1) * pageSize.value
  return cards.value.slice(start, start + pageSize.value)
})

watch([keyword, capability, source, sort, pageSize], () => {
  page.value = 1
})

watch(totalCards, (value) => {
  const maxPage = Math.max(Math.ceil(value / pageSize.value), 1)
  if (page.value > maxPage) page.value = maxPage
})

function flash(message: string, type: 'ok' | 'err' = 'ok') {
  if (type === 'err') ElMessage.error(message)
  else ElMessage.success(message)
}

function notifyChanged() {
  window.dispatchEvent(new Event('harness:plugins-changed'))
}

function formatTime(iso: string): string {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString('zh-CN', { hour12: false })
}

async function loadCatalog(force = false) {
  const first = await fetchPluginCatalog({ page: 1, page_size: 100, refresh: force })
  if (first.pages <= 1) return first
  // 目录可能超过单页上限：把剩余页一并取回，再交给本地分页
  const rest = await Promise.all(
    Array.from({ length: first.pages - 1 }, (_, index) =>
      fetchPluginCatalog({ page: index + 2, page_size: 100 }),
    ),
  )
  return { ...first, items: [first, ...rest].flatMap((page) => page.items) }
}

async function loadAll(force = false) {
  loading.value = true
  error.value = ''
  const [catalogResult, installedResult, updatesResult] = await Promise.allSettled([
    loadCatalog(force),
    fetchPlugins(),
    fetchPluginUpdates(),
  ])
  if (catalogResult.status === 'fulfilled') {
    catalog.value = catalogResult.value.items
    catalogStale.value = catalogResult.value.stale
    catalogFetchedAt.value = catalogResult.value.fetched_at || ''
  } else {
    error.value = (catalogResult.reason as Error).message
  }
  installed.value =
    installedResult.status === 'fulfilled' ? installedResult.value.items : []
  updates.value =
    updatesResult.status === 'fulfilled' ? updatesResult.value.items : []
  loading.value = false
}

async function discover() {
  await loadAll(true)
  if (!error.value) flash('插件目录已刷新')
}

async function loadJobs() {
  try {
    activeJobs.value = await fetchPluginJobs()
  } catch {
    // 任务轮询失败不打断页面。
  }
}

function ensureJobPolling() {
  if (jobTimer) return
  jobTimer = window.setInterval(async () => {
    await loadJobs()
    const running = activeJobs.value.some(
      (job) => job.status === 'queued' || job.status === 'running',
    )
    if (running) return
    if (jobTimer) window.clearInterval(jobTimer)
    jobTimer = undefined
    await loadAll()
    notifyChanged()
  }, 1200)
}

function afterStartJob(job: PluginJob | null | undefined) {
  jobsClosed.value = false
  if (job) {
    activeJobs.value = [job, ...activeJobs.value.filter((item) => item.job_id !== job.job_id)]
  }
  void loadJobs()
  ensureJobPolling()
}

function openDetail(item: PluginCardModel) {
  active.value = item
  drawerOpen.value = true
}

async function installItem(item: PluginCardModel) {
  if (item.installable === false || item.unsupported) return
  try {
    const result = await startPluginInstall({
      source: 'market',
      plugin_id: item.plugin_id,
      version: item.version,
    })
    afterStartJob(result.job)
    drawerOpen.value = false
    flash(`已开始安装 ${item.name}`)
  } catch (err) {
    flash((err as Error).message, 'err')
  }
}

function pickUpload() {
  uploadInput.value?.click()
}

async function importDshItem(item: PluginCardModel) {
  try {
    const result = await importDshSkill(item.plugin_id, item.version)
    afterStartJob(result.job)
    drawerOpen.value = false
    flash(`已开始转译 ${item.name}，完成后可在技能页查看`)
  } catch (err) {
    flash((err as Error).message, 'err')
  }
}

async function onUploadChange(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  if (!file.name.toLowerCase().endsWith('.zip')) {
    flash('只支持 ZIP 插件包', 'err')
    return
  }
  try {
    const result = await uploadPluginZip(file)
    afterStartJob(result.job)
    flash(`已开始安装 ${file.name}`)
  } catch (err) {
    flash((err as Error).message, 'err')
  }
}

function syncInstalled(row: PluginInstalledItem) {
  const index = installed.value.findIndex((item) => item.plugin_id === row.plugin_id)
  if (index >= 0) installed.value[index] = row
  else installed.value.push(row)
  if (active.value?.plugin_id === row.plugin_id) {
    Object.assign(active.value, {
      installed: true,
      installed_version: row.version,
      enabled: row.enabled,
      builtin: row.builtin,
      deletable: row.deletable,
      status: row.status,
      has_ui: row.has_ui,
      ui_slots: row.ui_slots,
      health: row.health,
      tool_count: row.tool_count,
      path: row.path,
      error: row.error,
    })
  }
}

async function onToggle(item: PluginCardModel, enabled: boolean) {
  if (busyId.value) return
  busyId.value = item.plugin_id
  try {
    const result = await togglePlugin(item.plugin_id, enabled)
    syncInstalled(result.plugin)
    flash(enabled ? '插件已启用，下一轮对话生效' : '插件已停用')
    notifyChanged()
  } catch (err) {
    flash((err as Error).message, 'err')
  } finally {
    busyId.value = ''
  }
}

async function onRestart(item: PluginCardModel) {
  if (busyId.value) return
  busyId.value = item.plugin_id
  try {
    const health = await restartPlugin(item.plugin_id)
    item.health = health
    flash(`插件已重启（${health.state}）`)
  } catch (err) {
    flash((err as Error).message, 'err')
  } finally {
    busyId.value = ''
  }
}

async function onUninstall(item: PluginCardModel) {
  try {
    await ElMessageBox.confirm(
      `确定卸载「${item.name}」吗？版本目录会被删除，data/ 与审计记录保留。`,
      '卸载插件',
      { type: 'warning', confirmButtonText: '卸载', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await deletePlugin(item.plugin_id)
    drawerOpen.value = false
    flash(`已卸载 ${item.name}`)
    await loadAll()
    notifyChanged()
  } catch (err) {
    flash((err as Error).message, 'err')
  }
}

async function onUpdate(item: PluginCardModel) {
  const update = updateMap.value.get(item.plugin_id)
  const version = update?.latest_version || item.version
  try {
    const result = await updatePlugin(item.plugin_id, version)
    afterStartJob(result.job)
    flash(`已开始更新 ${item.name}`)
  } catch (err) {
    flash((err as Error).message, 'err')
  }
}

onMounted(() => {
  void loadAll()
  void loadJobs()
  ensureJobPolling()
})

onBeforeUnmount(() => {
  if (jobTimer) window.clearInterval(jobTimer)
})
</script>

<template>
  <div class="app-shell">
    <TopNav />

    <div class="page">
      <section class="runtime-strip">
        <div class="runtime-title">
          <span class="title-icon"><el-icon><Compass /></el-icon></span>
          <div>
            <strong>插件市场</strong>
            <small>外设管理 · 安装、启停、更新与权限审计</small>
            <small v-if="catalogFetchedAt" class="config-path">
              目录更新：<code>{{ formatTime(catalogFetchedAt) }}</code>
            </small>
          </div>
        </div>
        <div class="runtime-facts">
          <span><b>{{ cards.length }}</b> 个插件</span>
          <span><b>{{ installedCount }}</b> 已安装</span>
          <span><b>{{ updateCount }}</b> 可更新</span>
          <el-tag v-if="catalogStale" size="small" effect="plain" type="warning">
            缓存快照
          </el-tag>
        </div>
      </section>

      <section class="toolbar">
        <el-input
          v-model="keyword"
          class="search-input"
          placeholder="搜索插件名称、描述或标签…"
          :prefix-icon="Search"
          clearable
        />
        <el-select v-model="capability" class="toolbar-select" placeholder="能力类型">
          <el-option label="全部能力" value="" />
          <el-option v-for="cap in CAPABILITIES" :key="cap" :label="cap" :value="cap" />
        </el-select>
        <el-select v-model="source" class="toolbar-select" placeholder="来源">
          <el-option
            v-for="item in SOURCES"
            :key="item.value"
            :label="item.label"
            :value="item.value"
          />
        </el-select>
        <el-select v-model="sort" class="toolbar-select" placeholder="排序">
          <el-option label="热门" value="popular" />
          <el-option label="最新" value="latest" />
          <el-option label="名称" value="name" />
        </el-select>
        <el-button type="primary" :icon="Compass" :loading="loading" @click="discover">
          发现
        </el-button>
        <el-button :icon="Upload" @click="pickUpload">上传插件包</el-button>
        <input
          ref="uploadInput"
          type="file"
          accept=".zip"
          class="hidden-input"
          @change="onUploadChange"
        />
      </section>

      <p v-if="error" class="error-note">{{ error }}</p>

      <div v-if="loading && cards.length === 0" class="loading-block">
        <el-skeleton animated :rows="6" />
      </div>

      <template v-else-if="cards.length">
        <div v-loading="loading" class="grid">
          <PluginCard
            v-for="item in pagedCards"
            :key="item.plugin_id"
            :item="item"
            :installed="Boolean(item.installed)"
            :update-available="isUpdate(item)"
            @detail="openDetail"
            @install="installItem"
            @import-skill="importDshItem"
            @toggle="onToggle"
            @restart="onRestart"
            @uninstall="onUninstall"
            @update="onUpdate"
          />
        </div>

        <div v-if="totalCards > pageSize" class="pager">
          <el-pagination
            v-model:current-page="page"
            v-model:page-size="pageSize"
            :page-sizes="[10, 20, 50]"
            :total="totalCards"
            layout="total, sizes, prev, pager, next, jumper"
            background
          />
        </div>
      </template>

      <el-empty v-else description="暂无可展示的插件" />
    </div>

    <PluginDetailDrawer
      v-model="drawerOpen"
      :item="active"
      :installed="Boolean(active?.installed)"
      :update-available="active ? isUpdate(active) : false"
      @install="installItem"
      @import-skill="importDshItem"
      @toggle="onToggle"
      @restart="onRestart"
      @uninstall="onUninstall"
      @update="onUpdate"
    />

    <PluginJobBar
      v-if="!jobsClosed && activeJobs.length"
      :jobs="activeJobs"
      @dismiss="jobsClosed = true"
    />
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
}
.runtime-title strong {
  display: block;
  font-size: 15px;
}
.runtime-title small {
  display: block;
  margin-top: 2px;
  font-size: 11px;
  color: var(--text-3);
}
.config-path code {
  font-family: var(--font-mono);
  color: var(--text-2);
}
.runtime-facts {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  color: var(--text-3);
  font-size: 11px;
}
.runtime-facts b {
  color: var(--text-1);
  font-weight: 600;
}
.toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.search-input {
  width: 320px;
}
.toolbar-select {
  width: 128px;
}
.hidden-input {
  display: none;
}
.error-note {
  margin: 0;
  padding: 8px 10px;
  font-size: 12px;
  color: var(--amber);
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-sm);
  background: rgba(242, 176, 60, 0.06);
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
}
.pager {
  display: flex;
  justify-content: center;
  padding: 4px 0 2px;
}
.loading-block {
  padding: 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--bg-panel);
}
</style>
