<script setup lang="ts">
// 我的任务流：卡片式列表（内置 + 我的），分页 + 按最近更新排序
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { CopyDocument, Delete, Plus, SetUp } from '@element-plus/icons-vue'
import TopNav from '@/components/console/TopNav.vue'
import FlowMiniMap from '@/components/taskflow/FlowMiniMap.vue'
import { forkTaskFlow, type FlowListItem, type StepDef } from '@/api/taskflow'
import { useTaskflowStore } from '@/stores/taskflow'

const store = useTaskflowStore()
const router = useRouter()

function stepsOf(id: string): StepDef[] {
  return store.schemas[id]?.steps ?? []
}

const forkingId = ref('')
const deletingId = ref('')

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
        <el-button type="primary" :icon="Plus" @click="router.push('/tasks/new')">
          新建任务流
        </el-button>
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
          <div class="card-top">
            <div class="thumb">
              <FlowMiniMap v-if="stepsOf(item.id).length" :steps="stepsOf(item.id)" compact />
              <span v-else class="thumb-empty">加载结构…</span>
            </div>

            <div class="card-main">
              <div class="card-title-row">
                <strong>{{ item.name }}</strong>
              </div>
              <div class="tag-line">
                <el-tag size="small" effect="plain" :type="item.source === 'builtin' ? 'primary' : 'info'">
                  {{ item.source === 'builtin' ? '系统内置' : '我的' }}
                </el-tag>
                <el-tag
                  size="small"
                  effect="plain"
                  :type="item.status === 'published' ? 'success' : 'warning'"
                >
                  {{ item.status === 'published' ? '已发布' : '草稿' }}
                </el-tag>
              </div>
              <p class="card-desc">{{ item.search_doc?.description || '暂无描述' }}</p>
              <span class="card-meta">更新于 {{ formatTime(item.updated_at) }}</span>
            </div>
          </div>

          <div class="card-actions">
            <el-button
              v-if="item.source === 'builtin'"
              size="small"
              :icon="CopyDocument"
              :loading="forkingId === item.id"
              @click.stop="fork(item)"
            >
              复制为我的任务流
            </el-button>
            <template v-else>
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
            </template>
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

.card-top {
  display: flex;
  gap: 12px;
  min-height: 84px;
}

.thumb {
  flex: none;
  width: 62px;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  overflow: hidden;
  border-right: 1px solid var(--line);
}

.thumb-empty {
  font-size: 9.5px;
  color: var(--text-3);
  writing-mode: vertical-rl;
  padding: 4px 0;
}

.card-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.card-title-row strong {
  font-size: 14px;
  letter-spacing: -0.01em;
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
