<script setup lang="ts">
// 输入框上方的后台任务栏：默认收起，统一展示调度运行与远程异步子 Agent。
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import AppIcon from '@/components/console/AppIcon.vue'
import {
  cancelSchedulerRun,
  fetchSchedulerRuns,
  type SchedulerRunSummary,
} from '@/api/scheduler'
import {
  cancelAsyncTask,
  fetchAsyncTasks,
  type AsyncTaskSummary,
} from '@/api/asyncTasks'
import { useChatStore } from '@/stores/chat'

const chat = useChatStore()

const open = ref(false)
const runs = ref<SchedulerRunSummary[]>([])
const asyncTasks = ref<AsyncTaskSummary[]>([])
// `${kind}:${id}` -> 本地临时状态：接口返回前先显示「终止中」，避免重复点击。
const overrides = ref<Record<string, string>>({})
const error = ref('')

let timer: number | undefined
let requestSeq = 0

// created/unknown 不代表真正在跑：纯计划或远端暂不可达都不应进入任务栏。
const ACTIVE_STATUSES = new Set(['running', 'paused', 'cancelling'])
const TERMINAL_STATUSES = new Set(['done', 'failed', 'cancelled', 'interrupted'])

const STATUS_LABEL: Record<string, string> = {
  created: '排队中',
  running: '执行中',
  paused: '待介入',
  cancelling: '终止中',
  cancelled: '已终止',
  interrupted: '已中断',
  done: '已完成',
  failed: '失败',
  unknown: '状态未知',
}

interface BackgroundTask {
  key: string
  kind: 'scheduler' | 'async'
  id: string
  title: string
  status: string
  meta: string
}

/** 远端 run 状态归一化到任务栏使用的状态集合。 */
function mapAsyncStatus(status: string): string {
  switch (status) {
    case 'pending':
    case 'running':
      return 'running'
    case 'success':
      return 'done'
    case 'error':
    case 'timeout':
      return 'failed'
    case 'cancelled':
      return 'cancelled'
    case 'interrupted':
      return 'interrupted'
    default:
      return 'unknown'
  }
}

function schedulerMeta(run: SchedulerRunSummary, status: string): string {
  const parts = [STATUS_LABEL[status] || status]
  if (run.item_count) parts.push(`${run.done}/${run.item_count}`)
  if (run.failed) parts.push(`失败 ${run.failed}`)
  return parts.join(' · ')
}

const tasks = computed<BackgroundTask[]>(() => {
  const schedulerRows = runs.value.map((run) => {
    const key = `scheduler:${run.run_id}`
    const status = overrides.value[key] || run.status
    return {
      key,
      kind: 'scheduler' as const,
      id: run.run_id,
      title: run.goal || run.run_id,
      status,
      meta: schedulerMeta(run, status),
    }
  })
  const asyncRows = asyncTasks.value.map((task) => {
    const key = `async:${task.task_id}`
    const status = overrides.value[key] || mapAsyncStatus(task.status)
    return {
      key,
      kind: 'async' as const,
      id: task.task_id,
      title: task.agent_name
        ? `异步子任务 · ${task.agent_name}`
        : `异步子任务 ${task.task_id.slice(0, 8)}`,
      status,
      meta: `异步子任务 · ${STATUS_LABEL[status] || status}`,
    }
  })
  return [...schedulerRows, ...asyncRows]
})

const activeTasks = computed(() =>
  tasks.value.filter((task) => ACTIVE_STATUSES.has(task.status)),
)

const summaryText = computed(() => {
  const count = activeTasks.value.length
  if (!count) return ''
  const paused = activeTasks.value.filter((task) => task.status === 'paused').length
  return paused
    ? `${count} 个后台任务（${paused} 个待介入）`
    : `${count} 个后台任务正在运行`
})

async function load() {
  const tid = chat.threadId
  if (!tid) {
    runs.value = []
    asyncTasks.value = []
    return
  }
  const seq = ++requestSeq
  const [schedulerResult, asyncResult] = await Promise.allSettled([
    fetchSchedulerRuns(tid),
    fetchAsyncTasks(tid),
  ])
  if (seq !== requestSeq) return
  if (schedulerResult.status === 'fulfilled') runs.value = schedulerResult.value
  if (asyncResult.status === 'fulfilled') asyncTasks.value = asyncResult.value

  // 任务已终态或已不在列表中时，清掉本地覆盖，避免一直显示「终止中」。
  // cancelled 只用于当前这一轮乐观隐藏；下一次轮询以服务端状态为准，
  // 防止远端取消失败时任务被永久藏起来。
  const next = { ...overrides.value }
  for (const [key, status] of Object.entries(next)) {
    if (status === 'cancelled') delete next[key]
  }
  const currentKeys = new Set<string>()
  for (const run of runs.value) {
    const key = `scheduler:${run.run_id}`
    currentKeys.add(key)
    if (TERMINAL_STATUSES.has(run.status)) delete next[key]
  }
  for (const task of asyncTasks.value) {
    const key = `async:${task.task_id}`
    currentKeys.add(key)
    if (TERMINAL_STATUSES.has(mapAsyncStatus(task.status))) delete next[key]
  }
  for (const key of Object.keys(next)) {
    if (!currentKeys.has(key)) delete next[key]
  }
  overrides.value = next
  error.value = ''
}

function schedule() {
  if (timer) window.clearTimeout(timer)
  timer = window.setTimeout(async () => {
    await load()
    schedule()
  }, 5000)
}

async function cancel(task: BackgroundTask) {
  if (task.status === 'cancelling') return
  overrides.value = { ...overrides.value, [task.key]: 'cancelling' }
  error.value = ''
  try {
    if (task.kind === 'scheduler') {
      await cancelSchedulerRun(task.id)
      chat.markSchedulerRunStatus(task.id, 'cancelled')
    } else {
      await cancelAsyncTask(task.id)
    }
    overrides.value = { ...overrides.value, [task.key]: 'cancelled' }
    // 给后端/远端一点时间落终态，再以服务端状态为准刷新。
    window.setTimeout(() => void load(), 2500)
  } catch (err) {
    const next = { ...overrides.value }
    delete next[task.key]
    overrides.value = next
    error.value = err instanceof Error ? err.message : '终止后台任务失败'
  }
}

watch(
  () => chat.threadId,
  () => {
    runs.value = []
    asyncTasks.value = []
    overrides.value = {}
    error.value = ''
    void load()
  },
)

onMounted(() => {
  void load()
  schedule()
})

onBeforeUnmount(() => {
  if (timer) window.clearTimeout(timer)
})
</script>

<template>
  <section v-if="activeTasks.length" class="bg-tasks" :class="{ open }">
    <button class="bg-head" type="button" @click="open = !open">
      <span class="bg-icon"><AppIcon name="terminal" :size="13" /></span>
      <span class="bg-label">{{ summaryText }}</span>
      <span class="bg-count">{{ activeTasks.length }}</span>
      <AppIcon
        class="bg-chevron"
        :class="{ rotated: open }"
        name="chevronRight"
        :size="12"
      />
    </button>

    <Transition name="bg-expand">
      <ul v-if="open" class="bg-list">
        <li v-for="task in activeTasks" :key="task.key" class="bg-item">
          <div class="bg-main">
            <span class="bg-goal" :title="task.title">{{ task.title }}</span>
            <span class="bg-meta">{{ task.meta }}</span>
          </div>
          <button
            class="bg-cancel"
            type="button"
            :title="task.status === 'cancelling' ? '正在终止' : '终止后台任务'"
            :disabled="task.status === 'cancelling'"
            @click.stop="cancel(task)"
          >
            <AppIcon :name="task.status === 'cancelling' ? 'clock' : 'close'" :size="12" />
          </button>
        </li>
      </ul>
    </Transition>

    <p v-if="error" class="bg-error">{{ error }}</p>
  </section>
</template>

<style scoped>
.bg-tasks {
  flex: none;
  overflow: hidden;
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-md);
  background: rgba(18, 22, 30, 0.72);
}

.bg-head {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 7px 10px;
  border: none;
  background: transparent;
  color: var(--text-2);
  font-size: 11px;
  text-align: left;
  cursor: pointer;
}

.bg-head:hover {
  background: rgba(255, 255, 255, 0.035);
}

.bg-icon {
  display: grid;
  place-items: center;
  flex: none;
  color: var(--accent-cyan);
}

.bg-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.bg-count {
  flex: none;
  min-width: 16px;
  padding: 0 5px;
  border-radius: 8px;
  background: rgba(55, 208, 196, 0.12);
  color: var(--accent-cyan);
  font-size: 10px;
  text-align: center;
}

.bg-chevron {
  flex: none;
  color: var(--text-3);
  transition: transform 0.18s ease;
}

.bg-chevron.rotated {
  transform: rotate(90deg);
}

.bg-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-height: 160px;
  margin: 0;
  padding: 0 10px 8px;
  overflow: auto;
  border-top: 1px solid var(--line);
  list-style: none;
}

.bg-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 2px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.03);
}

.bg-item:last-child {
  border-bottom: none;
}

.bg-main {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}

.bg-goal {
  overflow: hidden;
  color: var(--text-1);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.bg-meta {
  color: var(--text-3);
  font-size: 9.5px;
}

.bg-cancel {
  display: grid;
  place-items: center;
  width: 20px;
  height: 20px;
  flex: none;
  border: none;
  border-radius: 5px;
  background: transparent;
  color: var(--text-3);
  cursor: pointer;
}

.bg-cancel:hover:not(:disabled) {
  color: var(--danger);
  background: rgba(226, 96, 94, 0.12);
}

.bg-cancel:disabled {
  opacity: 0.55;
  cursor: default;
}

.bg-error {
  margin: 0;
  padding: 0 10px 7px;
  color: var(--danger);
  font-size: 10px;
}

.bg-expand-enter-active,
.bg-expand-leave-active {
  transition: opacity 0.15s ease;
}

.bg-expand-enter-from,
.bg-expand-leave-to {
  opacity: 0;
}
</style>
