<script setup lang="ts">
// 我的任务流：卡片式列表（内置 + 我的），分页 + 按最近更新排序
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { CopyDocument, Delete, Plus, SetUp, Upload } from '@element-plus/icons-vue'
import TopNav from '@/components/console/TopNav.vue'
import {
  createTaskFlow,
  FlowValidationError,
  forkTaskFlow,
  type FlowListItem,
  type StepDef,
  type TaskFlowSchema,
} from '@/api/taskflow'
import { useTaskflowStore } from '@/stores/taskflow'

const store = useTaskflowStore()
const router = useRouter()

const forkingId = ref('')
const deletingId = ref('')
// 正在切换启用状态的流：同一时间只允许一个，避免连点造成状态回跳。
const togglingId = ref('')
const importing = ref(false)
const importInput = ref<HTMLInputElement | null>(null)

const pages = computed(() => Math.max(1, Math.ceil(store.total / 12)))

function formatTime(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString('zh-CN', { hour12: false })
}

function openDetail(item: FlowListItem) {
  router.push(`/tasks/${item.id}`)
}

async function fork(item: FlowListItem) {
  if (forkingId.value) return
  forkingId.value = item.id
  try {
    const res = await forkTaskFlow(item.id)
    ElMessage.success(`已复制「${item.name}」为我的任务流`)
    router.push(`/tasks/${res.id}`)
  } catch (err) {
    ElMessage.error((err as Error).message)
  } finally {
    forkingId.value = ''
  }
}

async function toggleEnabled(item: FlowListItem, enabled: boolean) {
  if (togglingId.value) return
  togglingId.value = item.id
  try {
    await store.setEnabled(item.id, enabled)
    ElMessage.success(
      enabled
        ? `已启用「${item.name}」`
        : `已停用「${item.name}」，Agent 不会再自动命中`,
    )
  } catch (err) {
    ElMessage.error((err as Error).message)
  } finally {
    togglingId.value = ''
  }
}

async function remove(item: FlowListItem) {
  try {
    await ElMessageBox.confirm(
      `确定删除任务流「${item.name}」？该操作不可恢复。`,
      '删除任务流',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  deletingId.value = item.id
  try {
    await store.remove(item.id)
    ElMessage.success(`已删除「${item.name}」`)
    // 当前页删空时回退一页
    const target = store.items.length <= 1 && store.page > 1 ? store.page - 1 : store.page
    await store.loadList(target)
  } catch (err) {
    ElMessage.error((err as Error).message)
  } finally {
    deletingId.value = ''
  }
}

function onPageChange(page: number) {
  void store.loadList(page)
}

// ---------- 导入 JSON ----------
function triggerImport() {
  importInput.value?.click()
}

async function onImportFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  // 允许连续选择同一个文件
  input.value = ''
  if (!file || importing.value) return

  importing.value = true
  try {
    const parsed = JSON.parse(await file.text()) as unknown
    if (!parsed || typeof parsed !== 'object') {
      throw new Error('JSON 结构不正确：需要包含 name 和 steps 数组')
    }
    // 兼容三种常见结构：裸 schema、{ schema }、{ payload }
    const root = parsed as Record<string, unknown>
    const candidate = (root.schema ?? root.payload ?? root) as Record<string, unknown>
    const steps = candidate.steps
    if (
      typeof candidate.name !== 'string' ||
      !candidate.name.trim() ||
      !Array.isArray(steps)
    ) {
      throw new Error('JSON 结构不正确：需要包含 name 和 steps 数组')
    }

    // 导入视为新建草稿：去掉 id/source/status，避免覆盖已有任务流
    const payload: TaskFlowSchema = {
      schema_version:
        typeof candidate.schema_version === 'string' ? candidate.schema_version : '1.0',
      name: candidate.name.trim(),
      description: typeof candidate.description === 'string' ? candidate.description : '',
      trigger_examples: Array.isArray(candidate.trigger_examples)
        ? (candidate.trigger_examples as string[])
        : [],
      mcp_services: Array.isArray(candidate.mcp_services)
        ? (candidate.mcp_services as string[])
        : [],
      steps: steps as StepDef[],
      source: 'user',
    }

    const res = await createTaskFlow(payload)
    ElMessage.success(`已导入「${payload.name}」，当前为草稿，发布后才会命中`)
    router.push(`/tasks/${res.id}`)
  } catch (err) {
    if (err instanceof FlowValidationError) {
      const first = err.issues
        .slice(0, 3)
        .map((issue) => issue.message)
        .join('；')
      ElMessage.error(`导入校验未通过（${err.issues.length} 项）：${first}`)
    } else if (err instanceof SyntaxError) {
      ElMessage.error('JSON 解析失败，请检查文件格式')
    } else {
      ElMessage.error((err as Error).message)
    }
  } finally {
    importing.value = false
  }
}

onMounted(() => void store.loadList(1))
</script>

<template>
  <div class="app-shell">
    <TopNav />

    <div class="page">
      <!-- 页头 -->
      <header class="page-head">
        <div class="head-title">
          <span class="title-icon"><el-icon><SetUp /></el-icon></span>
          <div>
            <strong>我的任务流</strong>
            <small>共 {{ store.total }} 条 · 定义流程，Agent 命中后按统一方法执行</small>
          </div>
        </div>
        <div class="head-actions">
          <el-button :icon="Upload" :loading="importing" @click="triggerImport">
            导入 JSON
          </el-button>
          <el-button type="primary" :icon="Plus" @click="router.push('/tasks/new')">
            新建任务流
          </el-button>
        </div>
        <input
          ref="importInput"
          class="hidden-input"
          type="file"
          accept=".json,application/json"
          @change="onImportFile"
        />
      </header>

      <el-alert
        v-if="store.listError"
        :title="store.listError"
        type="error"
        :closable="false"
        show-icon
      />

      <!-- 加载骨架 -->
      <div v-if="store.listLoading && !store.items.length" class="flow-grid">
        <div v-for="i in 6" :key="i" class="flow-card skeleton-card">
          <el-skeleton animated :rows="3" />
        </div>
      </div>

      <!-- 卡片列表 -->
      <div v-else-if="store.items.length" class="flow-grid">
        <article
          v-for="item in store.items"
          :key="item.id"
          class="flow-card"
          @click="openDetail(item)"
        >
          <div class="card-main">
            <div class="card-title-row">
              <strong>{{ item.name }}</strong>
              <!-- 启用开关只对已发布流生效：草稿本来就不参与命中 -->
              <span v-if="item.status === 'published'" class="enable-switch" @click.stop>
                <el-switch
                  :model-value="item.enabled"
                  size="small"
                  :loading="togglingId === item.id"
                  :disabled="togglingId === item.id"
                  @update:model-value="
                    (value: string | number | boolean) => toggleEnabled(item, value === true)
                  "
                />
              </span>
              <el-tooltip v-else content="发布后才能启用" placement="top">
                <span class="enable-switch" @click.stop>
                  <el-switch :model-value="false" size="small" disabled />
                </span>
              </el-tooltip>
            </div>
            <div class="tag-line">
              <el-tag size="small" effect="plain" :type="item.source === 'builtin' ? 'primary' : 'info'">
                {{ item.source === 'builtin' ? '系统内置' : '我的' }}
              </el-tag>
              <el-tag v-if="item.status !== 'published'" size="small" effect="plain" type="warning">
                草稿
              </el-tag>
              <el-tag v-else-if="item.enabled" size="small" effect="plain" type="success">
                已启用
              </el-tag>
              <el-tag v-else size="small" effect="plain" type="info">已停用</el-tag>
            </div>
            <p class="card-desc">{{ item.search_doc?.description || '暂无描述' }}</p>
            <span class="card-meta">更新于 {{ formatTime(item.updated_at) }}</span>
          </div>

          <div class="card-actions">
            <el-button
              v-if="item.source === 'builtin'"
              size="small"
              :icon="CopyDocument"
              :loading="forkingId === item.id"
              @click.stop="fork(item)"
            >
              复制
            </el-button>
            <el-button size="small" type="primary" plain @click.stop="openDetail(item)">
              编辑
            </el-button>
            <el-button
              size="small"
              type="danger"
              :icon="Delete"
              :loading="deletingId === item.id"
              @click.stop="remove(item)"
            >
              删除
            </el-button>
          </div>
        </article>
      </div>

      <!-- 空态 -->
      <div v-else class="empty-wrap">
        <el-empty description="还没有任务流">
          <el-button type="primary" :icon="Plus" @click="router.push('/tasks/new')">
            新建第一条任务流
          </el-button>
        </el-empty>
      </div>

      <footer v-if="pages > 1" class="pager">
        <el-pagination
          layout="prev, pager, next"
          :current-page="store.page"
          :page-count="pages"
          background
          @current-change="onPageChange"
        />
      </footer>
    </div>
  </div>
</template>

<style scoped>
.app-shell {
  min-height: 100vh;
}

.page {
  display: flex;
  flex-direction: column;
  gap: 14px;
  width: 100%;
  padding: 14px 18px 28px;
}

/* ---------- 页头 ---------- */
.page-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  flex-wrap: wrap;
  padding: 4px 2px 12px;
  border-bottom: 1px solid var(--line);
}

.head-title {
  display: flex;
  align-items: center;
  gap: 10px;
}

.title-icon {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  font-size: 15px;
  color: var(--accent-cyan);
  border: 1px solid rgba(55, 208, 196, 0.28);
  border-radius: var(--radius-sm);
  background: rgba(55, 208, 196, 0.06);
}

.head-title strong {
  display: block;
  font-size: 16px;
  letter-spacing: -0.015em;
}

.head-title small {
  font-size: 10.5px;
  color: var(--text-3);
}

.head-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.hidden-input {
  display: none;
}

/* ---------- 卡片网格 ---------- */
.flow-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 12px;
}

.flow-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: rgba(11, 18, 32, 0.5);
  cursor: pointer;
  transition:
    border-color 0.18s ease,
    background 0.18s ease;
}

.flow-card:hover {
  border-color: var(--line-strong);
  background: rgba(16, 26, 44, 0.6);
}

.skeleton-card {
  cursor: default;
}

.card-main {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.card-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.card-title-row strong {
  font-size: 14px;
  letter-spacing: -0.01em;
}

/* 开关只切换启用状态，不触发卡片打开 */
.enable-switch {
  display: inline-flex;
  flex: none;
}

.tag-line {
  display: flex;
  align-items: center;
  gap: 6px;
}

.card-desc {
  margin: 0;
  font-size: 11.5px;
  line-height: 1.55;
  color: var(--text-2);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.card-meta {
  font-size: 10px;
  color: var(--text-3);
}

.card-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  /* 统一贴到卡片右下角：同行卡片等高，按钮不再随描述行数上下浮动 */
  margin-top: auto;
  padding-top: 10px;
  border-top: 1px solid var(--line);
}

/* ---------- 空态与分页 ---------- */
.empty-wrap {
  display: grid;
  place-items: center;
  padding: 48px 0;
  border-top: 1px solid var(--line);
}

.pager {
  display: flex;
  justify-content: center;
  padding: 6px 0;
}
</style>
