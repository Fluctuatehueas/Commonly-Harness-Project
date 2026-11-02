<script setup lang="ts">
// 技能管理：搜索 + 列表 + 右侧可拖拽抽屉 + 三种新增方式
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import TopNav from '@/components/console/TopNav.vue'
import AppIcon from '@/components/console/AppIcon.vue'
import UiSwitch from '@/components/console/UiSwitch.vue'
import MarkdownView from '@/components/chat/MarkdownView.vue'
import {
  createSkill,
  deleteSkill,
  downloadSkillFromUrl,
  fetchSkillContent,
  fetchSkillJob,
  fetchSkillJobs,
  fetchSkillStorage,
  fetchSkills,
  revealSkill,
  toggleSkill,
  uploadSkillZip,
  type SkillInstallJob,
  type SkillItem,
  type SkillStorage,
} from '@/api/skills'

const PAGE_SIZE = 12

const items = ref<SkillItem[]>([])
const total = ref(0)
const page = ref(1)
const pages = ref(1)
const keyword = ref('')
const enabledFilter = ref<'all' | 'on' | 'off'>('all')
const loading = ref(false)
const error = ref('')

const drawerOpen = ref(false)
const active = ref<SkillItem | null>(null)
const markdown = ref('')
const contentLoading = ref(false)
const drawerWidth = ref(480)
const resizing = ref(false)

const addOpen = ref(false)
const addTab = ref<'url' | 'zip' | 'manual'>('url')
const urlInput = ref('')
const zipFile = ref<File | null>(null)
const zipInput = ref<HTMLInputElement | null>(null)
const dragOver = ref(false)
const form = reactive({ name: '', description: '', content: '' })
const submitting = ref(false)
const addError = ref('')
const storage = ref<SkillStorage | null>(null)

const installRoot = computed(
  () => storage.value?.main_root || '技能库/main',
)
const maxPackageMb = computed(() => storage.value?.max_package_mb || 32)

const deleting = ref<SkillItem | null>(null)
const deletingBusy = ref(false)
const togglingId = ref('')

const toast = ref('')
const toastType = ref<'ok' | 'err'>('ok')
let toastTimer: number | undefined
let searchTimer: number | undefined

// 安装任务卡：Dialog 关闭后仍在页面底部显示进度。
const jobs = ref<SkillInstallJob[]>([])
let jobTimer: number | undefined
let jobLoadPending = false

const stageLabel: Record<string, string> = {
  queued: '排队中',
  downloading: '下载技能包',
  validating: '校验技能元信息',
  extracting: '解压技能包',
  installing: '安装技能',
  refreshing: '刷新 Agent 技能',
  done: '安装完成',
  error: '安装失败',
}

function flash(message: string, type: 'ok' | 'err' = 'ok') {
  toast.value = message
  toastType.value = type
  if (toastTimer) window.clearTimeout(toastTimer)
  toastTimer = window.setTimeout(() => (toast.value = ''), 3000)
}

function targetLabel(target: string): string {
  if (target === 'main') return '主智能体'
  const name = target.split('/').pop() || target
  return `子智能体 · ${name}`
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

function formatTime(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString('zh-CN', { hour12: false })
}

let loadSeq = 0

async function load() {
  const seq = ++loadSeq
  loading.value = true
  error.value = ''
  try {
    const data = await fetchSkills({
      page: page.value,
      page_size: PAGE_SIZE,
      keyword: keyword.value.trim(),
      enabled:
        enabledFilter.value === 'all'
          ? undefined
          : enabledFilter.value === 'on',
    })
    if (seq !== loadSeq) return
    items.value = data.items
    total.value = data.total
    pages.value = data.pages
    page.value = data.page
  } catch (err) {
    if (seq !== loadSeq) return
    error.value = (err as Error).message
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

watch(keyword, () => {
  if (searchTimer) window.clearTimeout(searchTimer)
  searchTimer = window.setTimeout(() => {
    page.value = 1
    void load()
  }, 300)
})

watch(enabledFilter, () => {
  page.value = 1
  void load()
})

async function openDrawer(item: SkillItem) {
  active.value = item
  drawerOpen.value = true
  contentLoading.value = true
  markdown.value = ''
  try {
    const data = await fetchSkillContent(item.target, item.name)
    active.value = data.skill
    markdown.value = data.content
    if (data.truncated) flash('SKILL.md 过大，已截断显示预览', 'err')
  } catch (err) {
    flash((err as Error).message, 'err')
  } finally {
    contentLoading.value = false
  }
}

function closeDrawer() {
  drawerOpen.value = false
  active.value = null
  markdown.value = ''
}

async function onToggle(item: SkillItem, value: boolean) {
  if (item.protected) {
    flash('skill-management 是受保护的管理技能，不能停用', 'err')
    return
  }
  if (togglingId.value) return
  togglingId.value = item.skill_id
  try {
    const updated = await toggleSkill(item.target, item.name, value)
    Object.assign(item, updated)
    if (active.value && active.value.skill_id === item.skill_id) {
      Object.assign(active.value, updated)
    }
    flash(value ? '技能已启用，下一轮对话生效' : '技能已停用，下一轮对话不再加载')
  } catch (err) {
    flash((err as Error).message, 'err')
  } finally {
    togglingId.value = ''
  }
}

async function reveal(item: SkillItem) {
  try {
    await revealSkill(item.target, item.name)
    flash('已在服务器资源管理器中打开技能目录')
  } catch (err) {
    flash((err as Error).message, 'err')
  }
}

function askDelete(item: SkillItem) {
  if (item.protected) {
    flash('受保护的管理技能不能删除', 'err')
    return
  }
  deleting.value = item
}

async function confirmDelete() {
  const item = deleting.value
  if (!item || deletingBusy.value) return
  deletingBusy.value = true
  try {
    await deleteSkill(item.target, item.name)
    if (active.value?.skill_id === item.skill_id) closeDrawer()
    deleting.value = null
    flash(`已删除技能 ${item.name}`)
    if (items.value.length === 1 && page.value > 1) page.value--
    await load()
  } catch (err) {
    flash((err as Error).message, 'err')
  } finally {
    deletingBusy.value = false
  }
}

// ---------- 新增 ----------
function resetAdd() {
  addTab.value = 'url'
  urlInput.value = ''
  zipFile.value = null
  form.name = ''
  form.description = ''
  form.content = ''
  addError.value = ''
}

function openAdd() {
  resetAdd()
  addOpen.value = true
}

function closeAdd() {
  if (submitting.value) return
  addOpen.value = false
}

function pickZip() {
  zipInput.value?.click()
}

function acceptZip(file: File | undefined | null) {
  if (!file) return
  if (!file.name.toLowerCase().endsWith('.zip')) {
    addError.value = '只支持 .zip 技能包'
    return
  }
  addError.value = ''
  zipFile.value = file
}

function onZipInput(event: Event) {
  const input = event.target as HTMLInputElement
  acceptZip(input.files?.[0])
  input.value = ''
}

function onDrop(event: DragEvent) {
  dragOver.value = false
  acceptZip(event.dataTransfer?.files?.[0])
}

const canSubmit = computed(() => {
  if (addTab.value === 'url') return /^https?:\/\//i.test(urlInput.value.trim())
  if (addTab.value === 'zip') return Boolean(zipFile.value)
  return Boolean(form.name.trim() && form.description.trim())
})

// ---------- 安装任务卡 ----------
function upsertJobs(next: SkillInstallJob[]) {
  const map = new Map(jobs.value.map((job) => [job.job_id, job]))
  for (const job of next) map.set(job.job_id, job)
  jobs.value = Array.from(map.values()).sort((a, b) =>
    a.created_at.localeCompare(b.created_at),
  )
}

function removeJob(jobId: string) {
  jobs.value = jobs.value.filter((job) => job.job_id !== jobId)
}

function scheduleJobPoll(delay = 1000) {
  if (jobTimer) window.clearTimeout(jobTimer)
  jobTimer = window.setTimeout(() => void pollJobs(), delay)
}

async function pollJobs() {
  const running = jobs.value.filter(
    (job) => job.status === 'queued' || job.status === 'running',
  )
  if (!running.length) return
  let finished = false
  const results = await Promise.all(
    running.map(async (job) => {
      try {
        return await fetchSkillJob(job.job_id)
      } catch {
        return null
      }
    }),
  )
  for (const job of results) {
    if (!job) continue
    if (job.status === 'done') {
      finished = true
      flash(`技能 ${job.skill?.title || job.label} 安装完成`)
      // 成功的卡片展示 8 秒后自动收起，失败卡片保留给用户查看。
      window.setTimeout(() => removeJob(job.job_id), 8000)
    } else if (job.status === 'error') {
      finished = true
      flash(job.error || '技能安装失败', 'err')
    }
    upsertJobs([job])
  }
  if (finished) {
    await load()
  }
  if (
    jobs.value.some(
      (job) => job.status === 'queued' || job.status === 'running',
    )
  ) {
    scheduleJobPoll(1200)
  }
}

function trackJob(job: SkillInstallJob) {
  upsertJobs([job])
  scheduleJobPoll(600)
}

function startPendingJob(kind: 'url' | 'zip', label: string): string {
  const pendingId = `pending-${Date.now()}`
  const now = new Date().toISOString()
  upsertJobs([
    {
      job_id: pendingId,
      kind,
      label,
      status: 'running',
      stage: kind === 'zip' ? 'extracting' : 'downloading',
      progress: 8,
      message: kind === 'zip' ? '正在上传技能包…' : '正在创建下载任务…',
      error: '',
      skill: null,
      created_at: now,
      updated_at: now,
    },
  ])
  return pendingId
}

async function submitAdd() {
  if (!canSubmit.value || submitting.value) return
  submitting.value = true
  addError.value = ''

  // URL / ZIP 安装：确认后立即关闭 Dialog，进度改由底部任务卡展示。
  if (addTab.value === 'url') {
    const url = urlInput.value.trim()
    addOpen.value = false
    const pendingId = startPendingJob('url', url)
    try {
      const result = await downloadSkillFromUrl(url)
      removeJob(pendingId)
      trackJob(result.job)
      keyword.value = ''
      enabledFilter.value = 'all'
      page.value = 1
    } catch (err) {
      removeJob(pendingId)
      flash((err as Error).message, 'err')
    } finally {
      submitting.value = false
    }
    return
  }

  if (addTab.value === 'zip') {
    const file = zipFile.value
    if (!file) {
      submitting.value = false
      return
    }
    addOpen.value = false
    const pendingId = startPendingJob('zip', file.name)
    try {
      const result = await uploadSkillZip(file)
      removeJob(pendingId)
      trackJob(result.job)
      keyword.value = ''
      enabledFilter.value = 'all'
      page.value = 1
    } catch (err) {
      removeJob(pendingId)
      flash((err as Error).message, 'err')
    } finally {
      submitting.value = false
    }
    return
  }

  // 手动创建仍是同步操作，成功后再关闭 Dialog。
  try {
    const added = await createSkill({
      name: form.name.trim(),
      description: form.description.trim(),
      content: form.content,
    })
    flash('技能已创建')
    addOpen.value = false
    keyword.value = ''
    enabledFilter.value = 'all'
    page.value = 1
    await load()
    await openDrawer(added)
  } catch (err) {
    addError.value = (err as Error).message
  } finally {
    submitting.value = false
  }
}

// ---------- 抽屉拖拽 ----------
let resizeStartX = 0
let resizeStartWidth = 0

function onResizeMove(event: MouseEvent) {
  drawerWidth.value = Math.min(
    760,
    Math.max(320, resizeStartWidth + (resizeStartX - event.clientX)),
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
  void fetchSkillStorage()
    .then((value) => (storage.value = value))
    .catch(() => undefined)
  // 页面重新打开时恢复仍在运行的安装任务进度卡。
  void fetchSkillJobs()
    .then((list) => {
      const running = list.filter(
        (job) => job.status === 'queued' || job.status === 'running',
      )
      if (running.length) {
        upsertJobs(running)
        scheduleJobPoll(800)
      }
    })
    .catch(() => undefined)
})

onBeforeUnmount(() => {
  if (toastTimer) window.clearTimeout(toastTimer)
  if (searchTimer) window.clearTimeout(searchTimer)
  if (jobTimer) window.clearTimeout(jobTimer)
  stopResize()
})
</script>

<template>
  <div class="app-shell">
    <TopNav />

    <div class="page">
      <section class="panel head">
        <div class="head-title">
          <span class="title-icon"><AppIcon name="sparkles" :size="15" /></span>
          <div>
            <strong>技能管理</strong>
            <small>共 {{ total }} 个技能 · 支持 URL / ZIP / 手动创建</small>
          </div>
        </div>

        <div class="head-actions">
          <div class="search-box">
            <AppIcon name="search" :size="13" />
            <input v-model="keyword" type="text" placeholder="搜索技能名称或描述…" />
            <button v-if="keyword" type="button" title="清空" @click="keyword = ''">
              <AppIcon name="close" :size="10" />
            </button>
          </div>
          <select v-model="enabledFilter" class="state-select">
            <option value="all">全部状态</option>
            <option value="on">使用中</option>
            <option value="off">已停用</option>
          </select>
          <button class="add-btn" type="button" @click="openAdd">
            <AppIcon name="plus" :size="14" /> 新增技能
          </button>
        </div>
      </section>

      <p v-if="error" class="error-note">{{ error }}</p>

      <div v-if="loading && items.length === 0" class="panel loading">
        <i class="spinner"></i> 正在读取技能库…
      </div>

      <ul v-else-if="items.length" class="skill-list">
        <li
          v-for="item in items"
          :key="item.skill_id"
          class="skill-card"
          :class="{ disabled: !item.enabled }"
        >
          <span class="skill-icon"><AppIcon name="book" :size="20" /></span>

          <div class="skill-main">
            <div class="skill-title-row">
              <strong>{{ item.title }}</strong>
              <code>{{ item.name }}</code>
              <span class="target-chip">{{ targetLabel(item.target) }}</span>
              <span v-if="!item.enabled" class="state-chip off">已停用</span>
              <span v-if="item.protected" class="state-chip lock">受保护</span>
            </div>
            <p class="skill-desc">{{ item.description || '暂无描述' }}</p>
            <div class="skill-meta">
              <span>{{ item.file_count }} 个文件</span>
              <em>·</em>
              <span>{{ formatSize(item.size_bytes) }}</span>
              <em>·</em>
              <span>更新于 {{ formatTime(item.updated_at) }}</span>
            </div>
          </div>

          <div class="skill-actions">
            <UiSwitch
              :model-value="item.enabled"
              :disabled="item.protected || togglingId === item.skill_id"
              @update:model-value="(value) => onToggle(item, value)"
            />
            <button class="gear" type="button" title="查看技能详情" @click="openDrawer(item)">
              <AppIcon name="gear" :size="15" />
            </button>
          </div>
        </li>
      </ul>

      <div v-else class="panel empty">
        <div class="empty-orb"><AppIcon name="sparkles" :size="24" /></div>
        <h3>没有找到技能</h3>
        <p>调整搜索条件，或点击「新增技能」从 URL / ZIP / 手动创建。</p>
      </div>

      <footer v-if="pages > 1" class="pager">
        <button type="button" :disabled="page <= 1" @click="page--; load()">上一页</button>
        <span>第 {{ page }} / {{ pages }} 页</span>
        <button type="button" :disabled="page >= pages" @click="page++; load()">下一页</button>
      </footer>
    </div>

    <!-- 右侧技能详情抽屉：宽度可拖拽 -->
    <Transition name="drawer">
      <aside
        v-if="drawerOpen && active"
        class="drawer"
        :class="{ resizing }"
        :style="{ width: `${drawerWidth}px` }"
      >
        <div class="resizer" title="拖动调整宽度" @mousedown="startResize"></div>

        <header class="drawer-head">
          <span class="skill-icon"><AppIcon name="book" :size="18" /></span>
          <div class="drawer-title">
            <strong>{{ active.title }}</strong>
            <small><code>{{ active.name }}</code> · {{ targetLabel(active.target) }}</small>
          </div>
          <button class="icon-btn" type="button" title="关闭" @click="closeDrawer">
            <AppIcon name="close" :size="13" />
          </button>
        </header>

        <div class="drawer-actions">
          <button type="button" @click="reveal(active)">
            <AppIcon name="folder" :size="12" /> 打开目录
          </button>
          <button
            type="button"
            :disabled="active.protected || togglingId === active.skill_id"
            @click="onToggle(active, !active.enabled)"
          >
            <AppIcon :name="active.enabled ? 'stop' : 'play'" :size="12" />
            {{ active.enabled ? '停用' : '启用' }}
          </button>
          <button
            class="danger"
            type="button"
            :disabled="active.protected"
            @click="askDelete(active)"
          >
            <AppIcon name="trash" :size="12" /> 删除
          </button>
        </div>

        <div class="drawer-meta">
          <span>{{ active.file_count }} 个文件</span>
          <em>·</em>
          <span>{{ formatSize(active.size_bytes) }}</span>
          <em>·</em>
          <span>{{ formatTime(active.updated_at) }}</span>
        </div>

        <div class="drawer-body">
          <div class="drawer-section">
            <span class="readonly-tag">SKILL.md · 只读</span>
          </div>
          <MarkdownView v-if="markdown" :content="markdown" />
          <p v-else-if="contentLoading" class="drawer-empty">正在读取技能描述…</p>
          <p v-else class="drawer-empty">没有可展示的描述内容</p>
        </div>
      </aside>
    </Transition>

    <!-- 新增技能 Dialog：三种方式分页展示 -->
    <Transition name="fade">
      <div v-if="addOpen" class="modal-mask" @click.self="closeAdd">
        <div class="dialog">
          <header class="dialog-head">
            <strong>新增技能</strong>
            <button class="icon-btn" type="button" @click="closeAdd">
              <AppIcon name="close" :size="13" />
            </button>
          </header>

          <div class="dialog-tabs">
            <button
              type="button"
              :class="{ active: addTab === 'url' }"
              @click="addTab = 'url'; addError = ''"
            >
              <AppIcon name="link" :size="13" /> URL 安装
            </button>
            <button
              type="button"
              :class="{ active: addTab === 'zip' }"
              @click="addTab = 'zip'; addError = ''"
            >
              <AppIcon name="upload" :size="13" /> ZIP 上传
            </button>
            <button
              type="button"
              :class="{ active: addTab === 'manual' }"
              @click="addTab = 'manual'; addError = ''"
            >
              <AppIcon name="book" :size="13" /> 手动编写
            </button>
          </div>

          <div class="dialog-body">
            <div v-if="addTab === 'url'" class="method">
              <p class="method-desc">
                支持 GitHub <code>tree</code> 技能目录链接，或可直接下载的 ZIP 直链。
                后台会下载、解压、校验 SKILL.md 并自动生成元信息。
              </p>
              <label>
                <span>技能链接</span>
                <input
                  v-model="urlInput"
                  type="url"
                  placeholder="https://github.com/owner/repo/tree/main/skills/xxx"
                />
              </label>

              <div class="method-notes">
                <h4>粘贴前请逐条确认</h4>
                <ol>
                  <li>
                    必须是<strong>可直接下载的 ZIP 文件链接</strong>，或
                    <strong>GitHub 仓库里的技能子目录链接</strong>（形如
                    <code>https://github.com/owner/repo/tree/main/skills/技能名</code>）。
                    网页文章、仓库主页、Release 页面、网盘分享页都会下载失败。
                  </li>
                  <li>
                    ZIP 解压后，根目录或唯一顶层目录必须包含
                    <code>SKILL.md</code>；其 YAML frontmatter 必须同时有
                    <code>name</code> 与 <code>description</code>，否则无法识别技能包。
                  </li>
                  <li>
                    安装位置：<code>{{ installRoot }}/&lt;技能名&gt;</code>。
                    技能名由链接或 SKILL.md 推断，推断成功后直接接入系统技能库。
                  </li>
                  <li>
                    如已存在同名技能，下载会失败；需先删除旧技能，或改用「ZIP 上传 / 手动编写」。
                  </li>
                  <li>
                    包体上限 {{ maxPackageMb }} MB；后台会拒绝路径穿越、符号链接和超限文件树。
                  </li>
                  <li>
                    下载成功后列表会自动刷新，下一轮对话立即生效，无需重启后端。
                  </li>
                </ol>
                <p class="note-example">
                  示例直链：<code>https://example.com/skills/report-writer.zip</code>
                </p>
              </div>
            </div>

            <div v-else-if="addTab === 'zip'" class="method">
              <p class="method-desc">
                拖入或选择一个 ZIP 技能包，后台会校验压缩包安全性，解压到技能库并识别 SKILL.md。
              </p>
              <div
                class="dropzone"
                :class="{ over: dragOver }"
                role="button"
                tabindex="0"
                @click="pickZip"
                @keydown.enter="pickZip"
                @dragover.prevent="dragOver = true"
                @dragleave="dragOver = false"
                @drop.prevent="onDrop"
              >
                <AppIcon name="upload" :size="26" />
                <strong>拖拽 ZIP 到这里，或点击选择文件</strong>
                <span>最大 {{ maxPackageMb }} MB · 自动解包并接入技能库</span>
              </div>
              <input ref="zipInput" class="hidden-input" type="file" accept=".zip" @change="onZipInput" />
              <p v-if="zipFile" class="picked-file">
                <AppIcon name="file" :size="12" />
                {{ zipFile.name }} · {{ formatSize(zipFile.size) }}
                <button type="button" @click="zipFile = null">移除</button>
              </p>
              <div class="method-notes compact">
                <p>
                  安装位置：<code>{{ installRoot }}/&lt;技能名&gt;</code>。
                  压缩包内必须包含 <code>SKILL.md</code>（含 name / description 的 frontmatter），
                  缺少时会被拒绝。
                </p>
              </div>
            </div>

            <div v-else class="method">
              <p class="method-desc">
                手写 SKILL.md 的 frontmatter 摘要与正文。名称为目录名，创建后可在详情抽屉中查看。
              </p>
              <label>
                <span>技能名称 <em>用于目录名，仅字母/数字/-/_</em></span>
                <input v-model="form.name" type="text" placeholder="例如 report-writer" maxlength="64" />
              </label>
              <label>
                <span>摘要描述 <em>SKILL.md frontmatter 的 description</em></span>
                <textarea v-model="form.description" rows="2" placeholder="一句话说明这个技能解决什么问题" maxlength="500"></textarea>
              </label>
              <label>
                <span>详细描述 <em>SKILL.md 正文，可写使用步骤与示例</em></span>
                <textarea v-model="form.content" rows="7" placeholder="## 使用场景&#10;&#10;## 操作步骤&#10;&#10;## 示例"></textarea>
              </label>
            </div>

            <p v-if="addError" class="form-error">{{ addError }}</p>
          </div>

          <footer class="dialog-foot">
            <button class="ghost-btn" type="button" :disabled="submitting" @click="closeAdd">取消</button>
            <button class="primary" type="button" :disabled="!canSubmit || submitting" @click="submitAdd">
              {{ submitting ? '处理中…' : addTab === 'manual' ? '创建技能' : '安装技能' }}
            </button>
          </footer>
        </div>
      </div>
    </Transition>

    <!-- 删除确认 -->
    <Transition name="fade">
      <div v-if="deleting" class="modal-mask" @click.self="deleting = null">
        <div class="dialog small">
          <header class="dialog-head">
            <strong>删除技能</strong>
            <button class="icon-btn" type="button" @click="deleting = null">
              <AppIcon name="close" :size="13" />
            </button>
          </header>
          <div class="dialog-body">
            <p class="confirm-text">
              确定要永久删除 <b>{{ deleting.title }}</b> 吗？
              该操作会直接从磁盘移除
              <code>{{ deleting.path }}</code>，无法恢复。
            </p>
          </div>
          <footer class="dialog-foot">
            <button class="ghost-btn" type="button" :disabled="deletingBusy" @click="deleting = null">取消</button>
            <button class="danger-btn" type="button" :disabled="deletingBusy" @click="confirmDelete">
              {{ deletingBusy ? '删除中…' : '确认删除' }}
            </button>
          </footer>
        </div>
      </div>
    </Transition>

    <!-- 安装进度卡：Dialog 关闭后从页面底部向上堆叠 -->
    <div v-if="jobs.length" class="job-dock">
      <TransitionGroup name="job-rise">
        <article
          v-for="job in jobs"
          :key="job.job_id"
          class="job-card"
          :class="job.status"
        >
          <header>
            <span class="job-icon">
              <AppIcon
                :name="job.status === 'error' ? 'close' : job.status === 'done' ? 'check' : 'sparkles'"
                :size="13"
              />
            </span>
            <div class="job-title">
              <strong>{{ job.kind === 'zip' ? 'ZIP 技能包' : 'URL 技能安装' }}</strong>
              <small>{{ job.label }}</small>
            </div>
            <button class="job-close" type="button" title="收起" @click="removeJob(job.job_id)">
              <AppIcon name="close" :size="10" />
            </button>
          </header>
          <p class="job-message">
            {{ job.status === 'error' ? job.error : job.message }}
          </p>
          <div class="job-progress">
            <i :style="{ width: `${Math.max(4, job.progress)}%` }"></i>
          </div>
          <footer>
            <span>{{ stageLabel[job.stage] || job.stage }}</span>
            <em>{{ job.progress }}%</em>
          </footer>
        </article>
      </TransitionGroup>
    </div>

    <Transition name="fade">
      <div v-if="toast" class="toast" :class="toastType">{{ toast }}</div>
    </Transition>
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

.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding: 12px 16px;
  flex-wrap: wrap;
}

.head-title {
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

.head-title strong {
  display: block;
  font-size: 15px;
}

.head-title small {
  font-size: 11px;
  color: var(--text-3);
}

.head-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.search-box {
  display: flex;
  align-items: center;
  gap: 7px;
  width: 270px;
  padding: 7px 10px;
  color: var(--text-3);
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-sm);
  background: var(--bg-deep);
}

.search-box input {
  flex: 1;
  min-width: 0;
  font-size: 12px;
  font-family: inherit;
  color: var(--text-1);
  border: none;
  background: transparent;
  outline: none;
}

.search-box button {
  display: grid;
  place-items: center;
  width: 18px;
  height: 18px;
  color: var(--text-3);
  border: none;
  border-radius: 4px;
  background: transparent;
}

.state-select {
  padding: 7px 10px;
  font-size: 11.5px;
  font-family: inherit;
  color: var(--text-2);
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-sm);
  background: var(--bg-deep);
  outline: none;
}

.add-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 13px;
  font-size: 12px;
  color: #fff;
  border: none;
  border-radius: var(--radius-sm);
  background: var(--accent);
}

.error-note {
  margin: 0;
  padding: 8px 10px;
  font-size: 12px;
  color: var(--amber);
  border: 1px solid rgba(242, 176, 60, 0.3);
  border-radius: var(--radius-sm);
  background: transparent;
}

.loading,
.empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 320px;
  gap: 10px;
  color: var(--text-3);
  font-size: 12px;
  text-align: center;
}

.loading {
  flex-direction: row;
}

.spinner {
  width: 16px;
  height: 16px;
  border: 2px solid transparent;
  border-top-color: var(--accent-cyan);
  border-radius: 50%;
  animation: spin 0.75s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.empty-orb {
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  border-radius: 50%;
  color: var(--accent-cyan);
  border: 1px solid var(--line-strong);
  background: transparent;
}

.empty h3 {
  margin: 0;
  font-size: 16px;
  color: var(--text-1);
}

.empty p {
  margin: 0;
  font-size: 11.5px;
}

.skill-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

/* 单列技能行：标题 / 描述 / 开关 + 详情按钮 */
.skill-card {
  display: flex;
  align-items: center;
  gap: 13px;
  padding: 12px 4px;
  border: 0;
  border-top: 1px solid var(--line);
  border-radius: 0;
  background: transparent;
  transition: background 0.16s, border-color 0.16s;
}

.skill-card:hover {
  background: var(--bg-hover);
}

.skill-card.disabled {
  opacity: 0.62;
}

.skill-icon {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  flex: none;
  border-radius: var(--radius-sm);
  color: var(--accent-cyan);
  border: 1px solid var(--line);
  background: transparent;
}

.skill-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.skill-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.skill-title-row strong {
  font-size: 13.5px;
  color: var(--text-1);
}

.skill-title-row code {
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 10.5px;
  color: var(--text-3);
}

.target-chip {
  padding: 1px 6px;
  font-size: 9.5px;
  color: var(--accent-cyan);
  border: 1px solid rgba(55, 208, 196, 0.28);
  border-radius: var(--radius-sm);
}

.state-chip {
  padding: 1px 6px;
  font-size: 9.5px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--line);
  color: var(--text-3);
}

.state-chip.off {
  color: var(--amber);
  border-color: rgba(242, 176, 60, 0.35);
}

.state-chip.lock {
  color: #a99bff;
  border-color: rgba(143, 123, 255, 0.35);
}

.skill-desc {
  margin: 0;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--text-2);
}

.skill-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  font-size: 9.5px;
  color: var(--text-3);
}

.skill-meta em {
  font-style: normal;
  opacity: 0.6;
}

.skill-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: none;
}

.gear {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  color: var(--text-3);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: transparent;
  transition: color 0.16s, border-color 0.16s, background 0.16s;
}

.gear:hover {
  color: var(--accent-cyan);
  border-color: var(--line-strong);
  background: var(--bg-hover);
}

.pager {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 4px 0 8px;
  font-size: 11.5px;
  color: var(--text-3);
}

.pager button {
  padding: 5px 12px;
  font-size: 11px;
  color: var(--text-2);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: transparent;
}

.pager button:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

/* ---------- 右侧抽屉 ---------- */
.drawer {
  position: fixed;
  top: var(--topbar-h);
  right: 0;
  bottom: 0;
  z-index: 45;
  display: flex;
  flex-direction: column;
  min-width: 320px;
  border-left: 1px solid var(--line-strong);
  background: var(--bg-elevated);
  box-shadow: -12px 0 28px rgba(2, 6, 14, 0.42);
}

.drawer.resizing {
  user-select: none;
}

.resizer {
  position: absolute;
  top: 0;
  left: -4px;
  bottom: 0;
  width: 8px;
  cursor: col-resize;
}

.resizer::after {
  content: '';
  position: absolute;
  top: 50%;
  left: 3px;
  width: 2px;
  height: 46px;
  margin-top: -23px;
  border-radius: 2px;
  background: var(--line-strong);
  opacity: 0;
  transition: opacity 0.15s;
}

.resizer:hover::after,
.drawer.resizing .resizer::after {
  opacity: 1;
}

.drawer-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--line);
}

.drawer-title {
  flex: 1;
  min-width: 0;
}

.drawer-title strong {
  display: block;
  font-size: 14px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.drawer-title small {
  display: block;
  margin-top: 2px;
  font-size: 10.5px;
  color: var(--text-3);
}

.drawer-title small code {
  font-family: 'Cascadia Code', Consolas, monospace;
  color: var(--text-2);
}

.icon-btn {
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  flex: none;
  color: var(--text-3);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: transparent;
}

.icon-btn:hover {
  color: var(--text-1);
  border-color: var(--line-strong);
}

.drawer-actions {
  display: flex;
  gap: 8px;
  padding: 10px 16px;
  flex-wrap: wrap;
}

.drawer-actions button {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 5px 10px;
  font-size: 11px;
  color: var(--text-2);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: transparent;
}

.drawer-actions button:hover:not(:disabled) {
  color: var(--text-1);
  border-color: var(--line-strong);
}

.drawer-actions button:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.drawer-actions .danger {
  color: var(--danger);
  border-color: rgba(226, 96, 94, 0.38);
}

.drawer-actions .danger:hover:not(:disabled) {
  background: rgba(226, 96, 94, 0.1);
}

.drawer-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  padding: 0 16px 10px;
  font-size: 10px;
  color: var(--text-3);
  border-bottom: 1px solid var(--line);
}

.drawer-meta em {
  font-style: normal;
  opacity: 0.6;
}

.drawer-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 12px 16px 24px;
}

.drawer-section {
  margin-bottom: 6px;
}

.readonly-tag {
  padding: 1px 7px;
  font-size: 9.5px;
  color: var(--accent-cyan);
  border: 1px solid rgba(55, 208, 196, 0.3);
  border-radius: var(--radius-sm);
}

.drawer-empty {
  font-size: 11.5px;
  color: var(--text-3);
}

/* ---------- Dialog ---------- */
.modal-mask {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(2, 7, 18, 0.62);
  backdrop-filter: blur(4px);
}

.dialog {
  width: min(680px, 100%);
  max-height: calc(100vh - 80px);
  display: flex;
  flex-direction: column;
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-lg);
  background: var(--bg-panel);
  box-shadow: 0 16px 40px rgba(2, 6, 14, 0.5);
}

.dialog.small {
  width: min(460px, 100%);
}

.dialog-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px;
  border-bottom: 1px solid var(--line);
}

.dialog-tabs {
  display: flex;
  gap: 4px;
  padding: 10px 14px 0;
}

.dialog-tabs button {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 7px 12px;
  font-size: 11.5px;
  color: var(--text-3);
  border: 1px solid transparent;
  border-bottom: none;
  border-radius: var(--radius-sm) var(--radius-sm) 0 0;
  background: transparent;
}

.dialog-tabs button.active {
  color: var(--text-1);
  border-color: var(--line);
  background: var(--bg-hover);
}

.dialog-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 14px 16px;
}

.method {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.method-desc {
  margin: 0;
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--text-3);
}

.method-desc code {
  font-family: 'Cascadia Code', Consolas, monospace;
  color: var(--accent-cyan);
}

/* URL / ZIP 安装说明：把后端约束逐条写清楚 */
.method-notes {
  padding: 10px 12px;
  font-size: 10.5px;
  line-height: 1.7;
  color: var(--text-3);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: transparent;
}

.method-notes h4 {
  margin: 0 0 6px;
  font-size: 11px;
  color: var(--text-2);
}

.method-notes ol {
  margin: 0;
  padding-left: 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.method-notes strong {
  color: var(--text-2);
}

.method-notes code {
  font-family: 'Cascadia Code', Consolas, monospace;
  color: var(--accent-cyan);
  word-break: break-all;
}

.method-notes.compact p {
  margin: 0;
}

.note-example {
  margin: 8px 0 0;
}

.method label {
  display: flex;
  flex-direction: column;
  gap: 5px;
  font-size: 11.5px;
  color: var(--text-2);
}

.method label em {
  margin-left: 5px;
  font-style: normal;
  font-size: 10px;
  color: var(--text-3);
}

.method input,
.method textarea {
  width: 100%;
  padding: 9px 11px;
  font-size: 12.5px;
  font-family: inherit;
  color: var(--text-1);
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-sm);
  background: var(--bg-deep);
  resize: vertical;
  outline: none;
}

.method input:focus,
.method textarea:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 1px rgba(91, 140, 255, 0.22);
}

.dropzone {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 7px;
  min-height: 160px;
  padding: 18px;
  color: var(--text-3);
  border: 1px dashed var(--line-strong);
  border-radius: var(--radius-md);
  background: transparent;
  cursor: pointer;
  transition: color 0.16s, border-color 0.16s, background 0.16s;
}

.dropzone:hover,
.dropzone.over {
  color: var(--accent-cyan);
  border-color: rgba(55, 208, 196, 0.5);
  background: var(--bg-hover);
}

.dropzone strong {
  font-size: 12.5px;
  color: var(--text-2);
}

.dropzone span {
  font-size: 10.5px;
}

.hidden-input {
  display: none;
}

.picked-file {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  font-size: 11px;
  color: var(--text-2);
}

.picked-file button {
  margin-left: auto;
  padding: 3px 8px;
  font-size: 10px;
  color: var(--text-3);
  border: 1px solid var(--line);
  border-radius: 6px;
  background: transparent;
}

.form-error {
  margin: 10px 0 0;
  font-size: 11.5px;
  color: var(--danger);
}

.confirm-text {
  margin: 0;
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--text-2);
}

.confirm-text b {
  color: var(--text-1);
}

.confirm-text code {
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 11px;
  color: var(--amber);
  word-break: break-all;
}

.dialog-foot {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 12px 16px;
  border-top: 1px solid var(--line);
}

.dialog-foot button {
  padding: 7px 14px;
  font-size: 12px;
  border-radius: var(--radius-sm);
  border: none;
}

.ghost-btn {
  color: var(--text-2);
  border: 1px solid var(--line-strong) !important;
  background: transparent;
}

.ghost-btn:hover:not(:disabled) {
  color: var(--text-1);
  border-color: var(--accent) !important;
}

.primary {
  color: #fff;
  background: var(--accent);
}

.primary:disabled {
  opacity: 0.45;
  cursor: not-allowed;
  box-shadow: none;
}

.danger-btn {
  color: #fff;
  background: var(--danger);
}

.danger-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

/* ---------- 动效与提示 ---------- */
.drawer-enter-active,
.drawer-leave-active {
  transition: transform 0.26s cubic-bezier(0.22, 1, 0.36, 1), opacity 0.2s;
}

.drawer-enter-from,
.drawer-leave-to {
  transform: translateX(24px);
  opacity: 0;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

.toast {
  position: fixed;
  left: 50%;
  bottom: 28px;
  z-index: 80;
  transform: translateX(-50%);
  padding: 9px 16px;
  font-size: 12px;
  color: var(--text-1);
  border: 1px solid rgba(55, 208, 196, 0.35);
  border-radius: var(--radius-sm);
  background: var(--bg-elevated);
}

.toast.err {
  color: var(--danger);
  border-color: rgba(226, 96, 94, 0.45);
}

/* ---------- 底部安装进度卡 ---------- */
.job-dock {
  position: fixed;
  right: 24px;
  bottom: 20px;
  z-index: 70;
  width: min(380px, calc(100vw - 32px));
  display: flex;
  flex-direction: column-reverse;
  gap: 10px;
  pointer-events: none;
}

.job-card {
  pointer-events: auto;
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-md);
  background: var(--bg-elevated);
  box-shadow: 0 12px 28px rgba(2, 6, 14, 0.5);
}

.job-card.done {
  border-color: rgba(55, 208, 196, 0.35);
}

.job-card.error {
  border-color: rgba(226, 96, 94, 0.42);
}

.job-card header {
  display: flex;
  align-items: center;
  gap: 9px;
}

.job-icon {
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  flex: none;
  border-radius: var(--radius-sm);
  color: var(--accent-cyan);
  background: transparent;
}

.job-card.done .job-icon {
  color: var(--green);
  background: rgba(55, 201, 164, 0.12);
}

.job-card.error .job-icon {
  color: var(--danger);
  background: rgba(226, 96, 94, 0.12);
}

.job-title {
  flex: 1;
  min-width: 0;
}

.job-title strong {
  display: block;
  font-size: 12px;
  color: var(--text-1);
}

.job-title small {
  display: block;
  margin-top: 1px;
  font-size: 10px;
  color: var(--text-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.job-close {
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

.job-close:hover {
  color: var(--text-1);
  background: rgba(255, 255, 255, 0.06);
}

.job-message {
  margin: 0;
  font-size: 11px;
  line-height: 1.5;
  color: var(--text-2);
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.job-progress {
  height: 5px;
  overflow: hidden;
  border-radius: 2px;
  background: rgba(126, 154, 198, 0.14);
}

.job-progress i {
  display: block;
  height: 100%;
  border-radius: 2px;
  background: linear-gradient(90deg, var(--accent-cyan), var(--accent));
  transition: width 0.35s ease;
}

.job-card.done .job-progress i {
  background: var(--accent-cyan);
}

.job-card.error .job-progress i {
  background: var(--danger);
}

.job-card footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 10px;
  color: var(--text-3);
}

.job-card footer em {
  font-style: normal;
  color: var(--text-2);
}

/* 自底向上出现 */
.job-rise-enter-active,
.job-rise-leave-active,
.job-rise-move {
  transition: transform 0.3s cubic-bezier(0.22, 1, 0.36, 1), opacity 0.25s;
}

.job-rise-enter-from,
.job-rise-leave-to {
  transform: translateY(28px) scale(0.96);
  opacity: 0;
}

@media (max-width: 860px) {
  .head-actions {
    width: 100%;
  }

  .search-box {
    flex: 1;
    width: auto;
  }

  .drawer {
    width: 100% !important;
    top: var(--topbar-h);
  }

  .resizer {
    display: none;
  }
}
</style>
