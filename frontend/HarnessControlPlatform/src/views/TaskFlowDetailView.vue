<script setup lang="ts">
// 任务流详情：上部全局配置，下部左简图右步骤配置。
// 保存 / 发布 / 复制内置流；422 校验失败按 step_id 在简图上标红。
// 新增步骤有三个入口：简图工具栏、简图末尾 + 节点、右键菜单 —— 空态也能建流程。
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ArrowLeft,
  Clock,
  CopyDocument,
  Delete,
  Plus,
  Promotion,
  Refresh,
  Upload,
} from '@element-plus/icons-vue'
import TopNav from '@/components/console/TopNav.vue'
import FlowMiniMap from '@/components/taskflow/FlowMiniMap.vue'
import type { NodeMenuPayload } from '@/components/taskflow/types'
import FlowGlobalPanel from '@/components/taskflow/FlowGlobalPanel.vue'
import StepConfigPanel from '@/components/taskflow/StepConfigPanel.vue'
import {
  FlowValidationError,
  type ExecutionDetail,
  type ExecutionItem,
  type FlowDetail,
  type FlowIssue,
  type FlowSource,
  type FlowStatus,
  type StepDef,
  type StepTrace,
  type TaskFlowSchema,
} from '@/api/taskflow'
import { useTaskflowStore } from '@/stores/taskflow'
import { fetchSkills } from '@/api/skills'
import { fetchMcpServers } from '@/api/mcp'

const route = useRoute()
const router = useRouter()
const store = useTaskflowStore()

const isNew = computed(() => route.params.id === 'new')
const flowId = computed(() => String(route.params.id || ''))

const meta = reactive({
  source: 'user' as FlowSource,
  status: 'draft' as FlowStatus,
  updatedAt: '',
})

// 步骤 id 生成：会话内递增，避免与已有 id 冲突
// 注意：blankFlow() 在下方 draft 初始化时即调用，本变量必须先于 draft 声明（let 无提升）
let stepSeq = 0

const draft = reactive<TaskFlowSchema>(blankFlow())
const loading = ref(false)
const saving = ref(false)
const publishing = ref(false)
const forking = ref(false)
const issues = ref<FlowIssue[]>([])
const selectedId = ref('')

const skillOptions = ref<{ name: string; label: string }[]>([])
const mcpOptions = ref<{ id: string; label: string }[]>([])

function genStepId(): string {
  stepSeq += 1
  return `s_${Date.now().toString(36)}_${stepSeq}`
}

function blankStep(): StepDef {
  return {
    id: genStepId(),
    name: '',
    instruction: '',
    skills: 'auto',
    executor: 'auto',
    constraints: '',
    checkpoint: false,
  }
}

function blankFlow(): TaskFlowSchema {
  return {
    schema_version: '1.0',
    name: '',
    description: '',
    trigger_examples: [],
    mcp_services: [],
    steps: [],
  }
}

const readonly = computed(() => meta.source === 'builtin')

// 422 错误：按 step_id 聚合到节点，step_id 为空的作为全局错误展示
const stepErrors = computed(() => {
  const map: Record<string, string> = {}
  for (const issue of issues.value) {
    if (!issue.step_id) continue
    map[issue.step_id] = map[issue.step_id]
      ? `${map[issue.step_id]}；${issue.message}`
      : issue.message
  }
  return map
})
const globalIssues = computed(() => issues.value.filter((issue) => !issue.step_id))

interface LocatedStep {
  step: StepDef
  list: StepDef[]
  index: number
  label: string
  /** 显示的步骤序号，如 2 / 2.1 */
  indexLabel: string
}

function findStep(id: string): LocatedStep | null {
  for (let i = 0; i < draft.steps.length; i++) {
    const step = draft.steps[i] as StepDef
    if (step.id === id) {
      return { step, list: draft.steps, index: i, label: `主流 步骤 ${i + 1}`, indexLabel: String(i + 1) }
    }
    for (const branch of step.parallel || []) {
      for (let j = 0; j < branch.steps.length; j++) {
        const sub = branch.steps[j] as StepDef
        if (sub.id === id) {
          return {
            step: sub,
            list: branch.steps,
            index: j,
            label: `子流 ${i + 1}.${j + 1} · ${branch.name}`,
            indexLabel: `${i + 1}.${j + 1}`,
          }
        }
      }
    }
  }
  return null
}

const selected = computed(() => (selectedId.value ? findStep(selectedId.value) : null))

function normalizeStep(raw: Partial<StepDef>): StepDef {
  return {
    id: raw.id || genStepId(),
    name: raw.name || '',
    instruction: raw.instruction || '',
    skills: raw.skills === undefined ? 'auto' : raw.skills,
    executor: raw.executor || 'auto',
    constraints: raw.constraints || '',
    checkpoint: !!raw.checkpoint,
    ...(raw.example ? { example: raw.example } : {}),
    ...(raw.parallel?.length
      ? {
          parallel: raw.parallel.map((branch) => ({
            name: branch.name,
            steps: branch.steps.map((sub) => normalizeStep(sub)),
          })),
        }
      : {}),
  }
}

function applyDetail(row: FlowDetail) {
  const schema = row.schema
  draft.schema_version = schema.schema_version || '1.0'
  draft.name = schema.name || row.name || ''
  draft.description = schema.description || ''
  draft.trigger_examples = [...(schema.trigger_examples || [])]
  draft.mcp_services = [...(schema.mcp_services || [])]
  draft.steps = (schema.steps || []).map((step) => normalizeStep(step))
  meta.source = row.source
  meta.status = row.status
  meta.updatedAt = row.updated_at
  selectedId.value = draft.steps[0]?.id || ''
}

async function load() {
  issues.value = []
  menu.value = null
  execDetail.value = {}
  execExpanded.value = []
  if (isNew.value) {
    Object.assign(draft, blankFlow())
    meta.source = 'user'
    meta.status = 'draft'
    meta.updatedAt = ''
    selectedId.value = ''
    return
  }
  loading.value = true
  try {
    const row = await store.loadDetail(flowId.value).then(() => store.current)
    if (!row) {
      ElMessage.error(store.detailError || '任务流不存在')
      return
    }
    applyDetail(row)
    void loadExecutions()
  } catch (err) {
    ElMessage.error((err as Error).message)
  } finally {
    loading.value = false
  }
}

// ---------- 执行记录 ----------
const execDetail = ref<Record<string, ExecutionDetail | 'loading'>>({})
const execExpanded = ref<string[]>([])

async function loadExecutions() {
  if (isNew.value) return
  try {
    await store.loadExecutions(flowId.value)
  } catch (err) {
    ElMessage.error((err as Error).message)
  }
}

async function loadExecutionDetail(id: string) {
  const cached = execDetail.value[id]
  if (cached && cached !== 'loading') return
  execDetail.value = { ...execDetail.value, [id]: 'loading' }
  try {
    const detail = await store.loadExecution(id)
    execDetail.value = { ...execDetail.value, [id]: detail }
  } catch (err) {
    const next = { ...execDetail.value }
    delete next[id]
    execDetail.value = next
    ElMessage.error((err as Error).message)
  }
}

// el-collapse 展开时按需拉取 StepTrace，列表接口不带执行详情。
function onExecChange(value: unknown) {
  const ids = (Array.isArray(value) ? value : [value]).map((item) => String(item))
  execExpanded.value = ids
  for (const id of ids) void loadExecutionDetail(id)
}

function detailOf(id: string): ExecutionDetail | null {
  const value = execDetail.value[id]
  return value && value !== 'loading' ? value : null
}

function tracesOf(id: string): StepTrace[] {
  return detailOf(id)?.step_traces ?? []
}

function traceName(stepId: string): string {
  return findStep(stepId)?.step.name || stepId
}

const execStatusLabel: Record<string, string> = {
  running: '执行中',
  done: '已完成',
  failed: '失败',
  aborted: '已中止',
}

function execStatusType(status: string): 'success' | 'danger' | 'warning' | 'info' {
  if (status === 'done') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'aborted') return 'warning'
  return 'info'
}

const traceStatusLabel: Record<string, string> = {
  done: '完成',
  failed: '失败',
  degraded: '兜底',
  skipped: '跳过',
}

function traceStatusType(status: string): 'success' | 'danger' | 'warning' | 'info' {
  if (status === 'done') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'degraded') return 'warning'
  return 'info'
}

function durationText(row: ExecutionItem): string {
  if (!row.finished_at) return '—'
  const start = new Date(row.created_at).getTime()
  const end = new Date(row.finished_at).getTime()
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return '—'
  return `${((end - start) / 1000).toFixed(1)}s`
}

function traceOutput(trace: StepTrace): string {
  return trace.output || trace.output_summary || ''
}

// 路由级前进 / 后退：参数变化时重新加载
watch(
  () => route.params.id,
  () => {
    if (route.name === 'taskflow-detail') void load()
  },
)

// ---------- 新增步骤（三个入口共用） ----------
function addStep() {
  if (readonly.value) return
  const step = blankStep()
  draft.steps.push(step)
  selectedId.value = step.id
  // 新增后把配置区滚到可见
  window.setTimeout(() => {
    document.querySelector('.step-pane')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }, 40)
}

// ---------- 右键菜单 ----------
const menu = ref<NodeMenuPayload | null>(null)

function onNodeMenu(payload: NodeMenuPayload) {
  if (readonly.value) return
  menu.value = payload
}

function closeMenu() {
  menu.value = null
}

async function menuAction(action: 'insert' | 'fork' | 'remove') {
  const target = menu.value
  if (!target || readonly.value) return
  menu.value = null

  const located = findStep(target.id)
  if (!located) return

  if (action === 'insert') {
    const step = blankStep()
    located.list.splice(located.index + 1, 0, step)
    selectedId.value = step.id
    return
  }

  if (action === 'fork') {
    // v1 嵌套层级 ≤ 2：只允许主流步骤插入并行子流
    if (target.branch !== null) return
    if (located.step.parallel?.length) {
      located.step.parallel.push({
        name: `分支 ${located.step.parallel.length + 1}`,
        steps: [blankStep()],
      })
    } else {
      // 首次插入只开一个分支，用户需要时再继续加分支
      located.step.parallel = [{ name: '分支一', steps: [blankStep()] }]
    }
    selectedId.value = located.step.id
    return
  }

  // remove：二次确认，避免误删
  try {
    await ElMessageBox.confirm(
      `确定删除步骤「${located.step.name || '未命名步骤'}」？`,
      '删除步骤',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  located.list.splice(located.index, 1)
  // 子分支删空后自动收起：分支和 fork 标记都不应继续留在图上
  pruneEmptyBranches()
  if (selectedId.value === target.id) selectedId.value = draft.steps[0]?.id || ''
}

/** 删除空分支；全部分支为空时移除 parallel，主节点的并行角标随之消失。 */
function pruneEmptyBranches() {
  for (const step of draft.steps) {
    if (!step.parallel?.length) continue
    step.parallel = step.parallel.filter((branch) => branch.steps.length > 0)
    if (!step.parallel.length) delete step.parallel
  }
}

/** 子分支末尾快速加步。 */
function addSubStep(payload: { stepId: string; branchIndex: number }) {
  if (readonly.value) return
  const located = findStep(payload.stepId)
  const branch = located?.step.parallel?.[payload.branchIndex]
  if (!branch) return
  const step = blankStep()
  branch.steps.push(step)
  selectedId.value = step.id
  window.setTimeout(() => {
    document.querySelector('.step-pane')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }, 40)
}

// ---------- 保存 / 发布 / 复制 ----------
function buildPayload(): TaskFlowSchema {
  const payload = JSON.parse(JSON.stringify(draft)) as TaskFlowSchema
  if (isNew.value) delete payload.id
  else payload.id = flowId.value
  payload.source = 'user'
  return payload
}

function handleValidation(err: FlowValidationError) {
  issues.value = err.issues
  const first = err.issues.find((issue) => issue.step_id)
  if (first?.step_id && findStep(first.step_id)) selectedId.value = first.step_id
  ElMessage.error(`校验未通过：${err.issues.length} 个问题已在图中标注`)
}

async function save(): Promise<boolean> {
  if (readonly.value || saving.value) return false
  saving.value = true
  issues.value = []
  try {
    const res = await store.save(buildPayload(), isNew.value ? undefined : flowId.value)
    meta.status = res.status
    meta.updatedAt = new Date().toISOString()
    if (isNew.value) {
      await router.replace(`/tasks/${res.id}`)
    }
    ElMessage.success('已保存')
    return true
  } catch (err) {
    if (err instanceof FlowValidationError) handleValidation(err)
    else ElMessage.error((err as Error).message)
    return false
  } finally {
    saving.value = false
  }
}

async function publish() {
  if (readonly.value || publishing.value) return
  if (!(await save())) return
  publishing.value = true
  issues.value = []
  try {
    const res = await store.publish(flowId.value)
    meta.status = res.status
    ElMessage.success('已发布，路由命中后将按该流程执行')
  } catch (err) {
    if (err instanceof FlowValidationError) handleValidation(err)
    else ElMessage.error((err as Error).message)
  } finally {
    publishing.value = false
  }
}

async function fork() {
  if (forking.value) return
  forking.value = true
  try {
    const res = await store.fork(flowId.value)
    ElMessage.success('已复制为我的任务流')
    router.push(`/tasks/${res.id}`)
  } catch (err) {
    ElMessage.error((err as Error).message)
  } finally {
    forking.value = false
  }
}

function formatTime(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString('zh-CN', { hour12: false })
}

onMounted(() => {
  void load()
  void fetchSkills({ page: 1, page_size: 100 })
    .then((data) => {
      skillOptions.value = data.items.map((item) => ({
        name: item.name,
        label: item.title || item.name,
      }))
    })
    .catch(() => undefined)
  void fetchMcpServers({ page: 1, page_size: 100 })
    .then((data) => {
      mcpOptions.value = data.items.map((item) => ({
        id: item.server_id,
        label: item.description || item.server_id,
      }))
    })
    .catch(() => undefined)
})
</script>

<template>
  <div class="app-shell">
    <TopNav />
      <div v-loading="loading" class="page">
      <!-- 页头：返回 + 标题 + 状态 + 操作 -->
      <header class="page-head">
        <div class="head-left">
          <el-button :icon="ArrowLeft" circle text @click="router.push('/tasks')" />
          <div class="head-title">
            <div class="title-line">
              <strong>{{ isNew ? '新建任务流' : draft.name || '未命名任务流' }}</strong>
              <el-tag size="small" effect="plain" :type="meta.source === 'builtin' ? 'primary' : 'info'">
                {{ meta.source === 'builtin' ? '内置' : '我的' }}
              </el-tag>
              <el-tag size="small" effect="plain" :type="meta.status === 'published' ? 'success' : 'warning'">
                {{ meta.status === 'published' ? '已发布' : '草稿' }}
              </el-tag>
            </div>
            <small class="head-meta">
              <span v-if="meta.updatedAt">更新于 {{ formatTime(meta.updatedAt) }}</span>
              <span v-else>尚未保存</span>
              <span class="dot-sep">·</span>
              <span>{{ draft.steps.length }} 个主流步骤</span>
            </small>
          </div>
        </div>

        <div class="head-actions">
          <template v-if="readonly">
            <el-button type="primary" :icon="CopyDocument" :loading="forking" @click="fork">
              复制为我的任务流
            </el-button>
          </template>
          <template v-else>
            <el-button :loading="saving" :disabled="publishing" @click="save">保存</el-button>
            <el-button
              type="primary"
              :icon="Upload"
              :loading="publishing"
              :disabled="saving"
              @click="publish"
            >
              {{ meta.status === 'published' ? '重新发布' : '发布' }}
            </el-button>
          </template>
        </div>
      </header>

      <!-- 全局校验错误 -->
      <div v-if="globalIssues.length" class="global-issues">
        <el-alert
          v-for="(issue, i) in globalIssues"
          :key="i"
          :title="`[${issue.pass_name}] ${issue.message}`"
          type="error"
          :closable="false"
          show-icon
        />
      </div>

      <!-- 上部：流程全局配置 -->
      <FlowGlobalPanel :flow="draft" :mcp-options="mcpOptions" :readonly="readonly" />

      <!-- 下部：左简图 + 右步骤配置 -->
      <div class="editor-row">
        <aside class="map-pane">
          <header class="pane-head">
            <div class="pane-title">
              <el-icon><Promotion /></el-icon>
              <strong>流程简图</strong>
              <small v-if="!readonly">单击选中 · 右键编辑</small>
            </div>
            <el-button
              v-if="!readonly"
              type="primary"
              size="small"
              :icon="Plus"
              @click="addStep"
            >
              新增步骤
            </el-button>
          </header>
          <div class="map-scroll">
            <FlowMiniMap
              :steps="draft.steps"
              :selected-id="selectedId"
              :errors="stepErrors"
              :readonly="readonly"
              @select="selectedId = $event"
              @menu="onNodeMenu"
              @add="addStep"
              @add-sub="addSubStep"
            />
          </div>
        </aside>

        <section class="step-pane">
          <StepConfigPanel
            :step="selected?.step ?? null"
            :title="selected?.label || ''"
            :step-index="selected?.indexLabel || ''"
            :skill-options="skillOptions"
            :readonly="readonly"
            :error="selectedId ? stepErrors[selectedId] || '' : ''"
          />
        </section>
      </div>

      <!-- 执行记录：列表只给摘要，展开时按需拉取 StepTrace 回放 -->
      <section v-if="!isNew" class="exec-pane">
        <header class="pane-head">
          <div class="pane-title">
            <el-icon><Clock /></el-icon>
            <strong>执行记录</strong>
            <small>共 {{ store.executionsTotal }} 条 · 展开查看步骤轨迹</small>
          </div>
          <el-button
            size="small"
            :icon="Refresh"
            :loading="store.executionsLoading"
            @click="loadExecutions"
          >
            刷新
          </el-button>
        </header>

        <el-empty
          v-if="!store.executions.length && !store.executionsLoading"
          description="还没有执行记录：在对话中命中该任务流后会自动记录"
          :image-size="56"
        />
        <el-collapse
          v-else
          v-model="execExpanded"
          class="exec-list"
          @change="onExecChange"
        >
          <el-collapse-item v-for="row in store.executions" :key="row.id" :name="row.id">
            <template #title>
              <div class="exec-row-title">
                <el-tag size="small" effect="plain" :type="execStatusType(row.status)">
                  {{ execStatusLabel[row.status] || row.status }}
                </el-tag>
                <span class="exec-time">{{ formatTime(row.created_at) }}</span>
                <span class="exec-meta">耗时 {{ durationText(row) }}</span>
                <span class="exec-meta">会话 {{ row.thread_id || '—' }}</span>
                <span v-if="row.entry_step" class="exec-meta">
                  入口 {{ traceName(row.entry_step) }}
                </span>
                <code class="exec-id">{{ row.id }}</code>
              </div>
            </template>

            <div v-if="!detailOf(row.id)" class="exec-loading">
              {{ execDetail[row.id] === 'loading' ? '加载执行详情…' : '展开后加载执行详情' }}
            </div>
            <div v-else-if="!tracesOf(row.id).length" class="exec-loading">该执行没有步骤记录</div>
            <div v-else class="trace-list">
              <article
                v-for="(trace, index) in tracesOf(row.id)"
                :key="`${trace.step_id}-${index}`"
                class="trace-card"
              >
                <header class="trace-head">
                  <el-tag size="small" effect="plain" :type="traceStatusType(trace.status)">
                    {{ traceStatusLabel[trace.status] || trace.status }}
                  </el-tag>
                  <strong>{{ traceName(trace.step_id) }}</strong>
                  <span v-if="trace.branch" class="exec-meta">分支 {{ trace.branch }}</span>
                  <span class="exec-meta">
                    尝试 {{ trace.attempts }} 次 · {{ (trace.duration_ms / 1000).toFixed(1) }}s
                  </span>
                  <span v-if="trace.state_source === 'user_provided'" class="exec-meta">
                    用户提供上游
                  </span>
                </header>
                <p v-if="trace.check_result" class="trace-check">校验：{{ trace.check_result }}</p>
                <pre v-if="traceOutput(trace)" class="trace-output">{{ traceOutput(trace) }}</pre>
                <p v-else class="exec-meta">无产出</p>
              </article>
            </div>
          </el-collapse-item>
        </el-collapse>
      </section>
      </div>

    <!-- 步骤右键菜单 -->
    <Transition name="fade">
      <div v-if="menu" class="menu-mask" @click="closeMenu" @contextmenu.prevent="closeMenu">
        <div class="ctx-menu" :style="{ left: `${menu.x}px`, top: `${menu.y}px` }" @click.stop>
          <button type="button" class="ctx-item" @click="menuAction('insert')">
            <el-icon><Plus /></el-icon>
            在下方插入步骤
          </button>
          <button
            type="button"
            class="ctx-item"
            :disabled="menu.branch !== null"
            :title="menu.branch !== null ? '子流内不支持再嵌套并行' : ''"
            @click="menuAction('fork')"
          >
            <el-icon><Promotion /></el-icon>
            插入并行子流
          </button>
          <div class="ctx-sep" />
          <button type="button" class="ctx-item danger" @click="menuAction('remove')">
            <el-icon><Delete /></el-icon>
            删除步骤
          </button>
        </div>
      </div>
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

.head-left {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.head-title {
  min-width: 0;
}

.title-line {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.title-line strong {
  font-size: 16px;
  letter-spacing: -0.015em;
}

.head-meta {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-top: 3px;
  font-size: 10.5px;
  color: var(--text-3);
}

.dot-sep {
  opacity: 0.5;
}

.head-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.global-issues {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

/* ---------- 下部布局 ---------- */
.editor-row {
  display: grid;
  grid-template-columns: minmax(260px, 320px) minmax(0, 1fr);
  gap: 14px;
  align-items: stretch;
}

.map-pane {
  display: flex;
  flex-direction: column;
  min-height: 460px;
  max-height: calc(100vh - var(--topbar-h) - 120px);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: rgba(11, 18, 32, 0.42);
}

.pane-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--line);
}

.pane-title {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--text-2);
}

.pane-title strong {
  font-size: 12.5px;
  color: var(--text-1);
}

.pane-title small {
  font-size: 10px;
  color: var(--text-3);
}

.map-scroll {
  flex: 1;
  min-height: 0;
  overflow: auto;
}

.step-pane {
  min-width: 0;
  display: flex;
  flex-direction: column;
}

/* ---------- 执行记录 ---------- */
.exec-pane {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: rgba(11, 18, 32, 0.42);
}

.exec-list {
  padding: 4px 14px 10px;
  border: none;
}

.exec-row-title {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  flex-wrap: wrap;
  font-size: 11.5px;
}

.exec-row-title strong {
  color: var(--text-1);
}

.exec-time {
  color: var(--text-2);
}

.exec-meta {
  font-size: 10.5px;
  color: var(--text-3);
}

.exec-id {
  margin-left: auto;
  font-size: 10px;
  color: var(--text-3);
}

.exec-loading {
  padding: 8px 4px;
  font-size: 11px;
  color: var(--text-3);
}

.trace-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 2px 0 4px;
}

.trace-card {
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: rgba(8, 14, 26, 0.5);
}

.trace-head {
  display: flex;
  align-items: center;
  gap: 9px;
  flex-wrap: wrap;
}

.trace-head strong {
  font-size: 11.5px;
  color: var(--text-1);
}

.trace-check {
  margin: 6px 0 0;
  font-size: 10.5px;
  color: var(--amber);
}

.trace-output {
  margin: 7px 0 0;
  padding: 8px 10px;
  max-height: 260px;
  overflow: auto;
  font-family: inherit;
  font-size: 10.8px;
  line-height: 1.6;
  color: var(--text-2);
  white-space: pre-wrap;
  word-break: break-word;
  border-left: 1px solid var(--line-strong);
  background: rgba(255, 255, 255, 0.02);
}

/* ---------- 右键菜单 ---------- */
.menu-mask {
  position: fixed;
  inset: 0;
  z-index: 90;
}

.ctx-menu {
  position: fixed;
  min-width: 176px;
  padding: 4px;
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-md);
  background: var(--bg-elevated);
  box-shadow: 0 14px 34px rgba(2, 6, 14, 0.6);
}

.ctx-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 10px;
  font-size: 12px;
  color: var(--text-2);
  text-align: left;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
}

.ctx-item:hover:not(:disabled) {
  color: var(--text-1);
  background: var(--bg-hover);
}

.ctx-item:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.ctx-item.danger {
  color: var(--danger);
}

.ctx-item.danger:hover {
  background: rgba(226, 96, 94, 0.1);
}

.ctx-sep {
  height: 1px;
  margin: 4px 6px;
  background: var(--line);
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.14s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

/* 只有窄屏（<860px）才降级为上下布局，常见笔记本宽度保持左图右配置 */
@media (max-width: 860px) {
  .editor-row {
    grid-template-columns: 1fr;
  }

  .map-pane {
    max-height: 380px;
  }
}
</style>
