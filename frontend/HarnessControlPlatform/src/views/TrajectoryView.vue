<script setup lang="ts">
// 轨迹页：顶部甘特图 + 顺次日志列表 + 右侧详情
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import TopNav from '@/components/console/TopNav.vue'
import AppIcon from '@/components/console/AppIcon.vue'
import UiSwitch from '@/components/console/UiSwitch.vue'
import type { IconName } from '@/components/console/icons'
import {
  fetchTrajectory,
  type TrajectoryEvent,
  type TrajectoryRun,
} from '@/api/trajectory'
import { USER_ID, useSessionStore } from '@/stores/sessions'

const route = useRoute()
const sessions = useSessionStore()

const threadId = ref<string>('')
const runs = ref<TrajectoryRun[]>([])
const selectedRunId = ref<string>('')
const selectedEventId = ref<string>('')
const loading = ref(false)
const error = ref('')
const autoRefresh = ref(true)
let timer: number | undefined

// 甘特图按过程类别分行；颜色与对话页的轨迹条保持同一套语义
const LANES = [
  { key: 'run', label: 'Run', color: '#5c6f92', icon: 'workflow' },
  { key: 'model', label: '模型', color: '#5b8cff', icon: 'sparkles' },
  { key: 'tool', label: '工具', color: '#37d0c4', icon: 'wrench' },
  { key: 'skill', label: '技能', color: '#8f7bff', icon: 'layers' },
  { key: 'delegation', label: '委派', color: '#f2b03c', icon: 'users' },
  { key: 'hitl', label: '人工介入', color: '#e2605e', icon: 'shield' },
] as const

const TYPE_META: Record<string, { label: string; icon: IconName; color: string }> = {
  run: { label: 'Run', icon: 'workflow', color: '#5c6f92' },
  model: { label: '模型', icon: 'sparkles', color: '#5b8cff' },
  tool: { label: '工具', icon: 'wrench', color: '#37d0c4' },
  skill: { label: '技能', icon: 'layers', color: '#8f7bff' },
  delegation: { label: '委派', icon: 'users', color: '#f2b03c' },
  hitl: { label: '人工介入', icon: 'shield', color: '#e2605e' },
}

// selectedRunId 为空表示查看整个会话的全部轮次
const selectedRun = computed(
  () => runs.value.find((run) => run.run_id === selectedRunId.value) || null,
)

const activeRuns = computed<TrajectoryRun[]>(() => {
  if (!selectedRunId.value) return runs.value
  const run = selectedRun.value
  return run ? [run] : []
})

const events = computed(() => activeRuns.value.flatMap((run) => run.events))

const runNumber = computed(() => {
  const map = new Map<string, number>()
  runs.value.forEach((run, index) => map.set(run.run_id, index + 1))
  return map
})

const scopeStatus = computed(() => {
  if (!runs.value.length) return '暂无运行'
  if (selectedRunId.value) return selectedRun.value?.status || '暂无运行'
  if (runs.value.some((run) => run.status === 'running')) return 'running'
  return runs.value[runs.value.length - 1]?.status || 'done'
})

// 全部轮次时把每轮的汇总累加，单轮时直接复用该 run 的统计。
const summary = computed(() => {
  if (selectedRun.value) return selectedRun.value.summary
  const total = {
    model_calls: 0,
    tool_calls: 0,
    skill_calls: 0,
    delegations: 0,
    hitl: 0,
    tokens: { input: 0, output: 0, total: 0 },
    peak_context_tokens: 0,
    duration_ms: 0,
    event_count: 0,
  }
  for (const run of activeRuns.value) {
    total.model_calls += run.summary.model_calls
    total.tool_calls += run.summary.tool_calls
    total.skill_calls += run.summary.skill_calls
    total.delegations += run.summary.delegations
    total.hitl += run.summary.hitl
    total.tokens.input += run.summary.tokens.input || 0
    total.tokens.output += run.summary.tokens.output || 0
    total.tokens.total += run.summary.tokens.total || 0
    total.peak_context_tokens = Math.max(total.peak_context_tokens, run.summary.peak_context_tokens)
    total.duration_ms += run.summary.duration_ms || 0
    total.event_count += run.summary.event_count
  }
  return total
})

const selectedEvent = computed(
  () => events.value.find((event) => event.id === selectedEventId.value) || null,
)

// 过程日志按页展示，避免轨迹很多时整页被无限拉长
const LOG_PAGE_SIZE = 20
const logPage = ref(1)
const logPages = computed(() =>
  Math.max(1, Math.ceil(events.value.length / LOG_PAGE_SIZE)),
)
const pagedEvents = computed(() => {
  const start = (logPage.value - 1) * LOG_PAGE_SIZE
  return events.value.slice(start, start + LOG_PAGE_SIZE)
})

watch(
  () => events.value.length,
  () => {
    if (logPage.value > logPages.value) logPage.value = logPages.value
  },
)


const ganttRange = computed(() => {
  const starts = events.value
    .map((event) => Date.parse(event.start))
    .filter((value) => Number.isFinite(value))
  if (starts.length === 0) return { start: 0, end: 1, span: 1 }
  const ends = events.value
    .map((event) => (event.end ? Date.parse(event.end) : Date.parse(event.start) + 400))
    .filter((value) => Number.isFinite(value))
  const start = Math.min(...starts)
  const end = Math.max(...ends, start + 1000)
  return { start, end, span: end - start }
})

const ticks = computed(() =>
  Array.from({ length: 5 }, (_, index) => {
    const offset = (ganttRange.value.span * index) / 4
    return { offset: (index / 4) * 100, label: formatOffset(offset) }
  }),
)

function laneEvents(lane: string) {
  return events.value.filter((event) => event.type === lane)
}

function typeMeta(type: string) {
  return TYPE_META[type] || { label: type || '过程', icon: 'workflow', color: '#5b6b8c' }
}

function barStyle(event: TrajectoryEvent) {
  const start = Date.parse(event.start)
  const end = event.end ? Date.parse(event.end) : start + 400
  const range = ganttRange.value
  const left = Math.max(((start - range.start) / range.span) * 100, 0)
  const width = Math.max(((end - start) / range.span) * 100, 0.7)
  return {
    left: `${left}%`,
    width: `${width}%`,
    background: typeMeta(event.type).color,
  }
}

function formatOffset(ms: number) {
  if (ms < 1000) return `+${Math.round(ms)}ms`
  const seconds = ms / 1000
  if (seconds < 60) return `+${seconds.toFixed(seconds < 10 ? 1 : 0)}s`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `+${minutes}m${Math.round(seconds % 60)}s`
  const hours = Math.floor(minutes / 60)
  return `+${hours}h${minutes % 60}m`
}

function durationLabel(ms: number | null | undefined) {
  if (ms === null || ms === undefined) return '—'
  if (ms < 1000) return `${ms}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)}s`
  const minutes = Math.floor(ms / 60_000)
  if (minutes < 60) return `${minutes}m${Math.round((ms % 60_000) / 1000)}s`
  const hours = Math.floor(minutes / 60)
  return `${hours}h${minutes % 60}m`
}

function timeLabel(iso: string) {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

function statusLabel(status: string) {
  return (
    {
      running: '执行中',
      done: '完成',
      error: '失败',
      interrupted: '等待介入',
      answered: '已回复',
      approved: '已批准',
      rejected: '已拒绝',
    }[status] || status
  )
}

function barLabel(event: TrajectoryEvent) {
  if (event.type === 'run' && event.status === 'done') return '本轮 Run'
  return event.label
}

function eventTime(event: TrajectoryEvent) {
  if (event.type === 'run' && event.status === 'done' && event.end) return event.end
  return event.start
}

function selectRun(runId: string) {
  selectedRunId.value = runId
  logPage.value = 1
  const list = runId
    ? runs.value.find((item) => item.run_id === runId)?.events || []
    : events.value
  const last = [...list].reverse().find((event) => event.type !== 'run') || list[list.length - 1]
  selectedEventId.value = last?.id || ''
}

function onRunChange(event: Event) {
  selectRun((event.target as HTMLSelectElement).value)
}

function selectEvent(eventId: string) {
  selectedEventId.value = eventId
}

function eventRunLabel(event: TrajectoryEvent): string {
  const index = runNumber.value.get(event.run_id)
  return index ? `第${index}轮` : ''
}

async function load() {
  if (!threadId.value) {
    runs.value = []
    return
  }
  loading.value = true
  error.value = ''
  try {
    const data = await fetchTrajectory(threadId.value, USER_ID)
    runs.value = data.runs
    if (
      selectedRunId.value &&
      !data.runs.some((run) => run.run_id === selectedRunId.value)
    ) {
      // 选中的轮次已不存在（会话被清理）时回到整会话视图
      selectedRunId.value = ''
    }
    if (!selectedEventId.value || !events.value.some((event) => event.id === selectedEventId.value)) {
      selectRun(selectedRunId.value)
    }
  } catch (err) {
    error.value = (err as Error).message
  } finally {
    loading.value = false
  }
}

function startTimer() {
  stopTimer()
  if (!autoRefresh.value) return
  timer = window.setInterval(() => {
    if (activeRuns.value.some((run) => run.status === 'running')) void load()
  }, 4000)
}

function stopTimer() {
  if (timer) window.clearInterval(timer)
  timer = undefined
}

watch(autoRefresh, () => startTimer())
watch(
  () => route.query.thread,
  (value) => {
    const next = String(value || '')
    if (next && next !== threadId.value) threadId.value = next
  },
)
watch(threadId, () => {
  selectedRunId.value = ''
  selectedEventId.value = ''
  logPage.value = 1
  void load()
  startTimer()
})

onMounted(async () => {
  await sessions.refresh()
  const queryThread = String(route.query.thread || '')
  threadId.value =
    queryThread || sessions.activeThreadId || sessions.sessions[0]?.thread_id || ''
  startTimer()
})

onBeforeUnmount(stopTimer)
</script>

<template>
  <div class="app-shell">
    <TopNav>
      <template #actions>
        <span class="status-chip">
          <i class="dot"></i>{{ statusLabel(scopeStatus) }}
        </span>
        <span class="auto-toggle">
          <UiSwitch v-model="autoRefresh" />
          <em>自动刷新</em>
        </span>
      </template>
    </TopNav>

    <div class="page">
      <section class="panel head">
        <div class="head-title">
          <span class="title-icon"><AppIcon name="workflow" :size="15" /></span>
          <div>
            <strong>轨迹</strong>
            <small>模型决策 · 工具 / 技能 / 委派 · HITL · Token 与上下文</small>
          </div>
        </div>
        <div class="head-controls">
          <select v-model="threadId" class="session-select" :disabled="sessions.sessions.length === 0">
            <option value="">选择会话</option>
            <option v-for="item in sessions.sessions" :key="item.thread_id" :value="item.thread_id">
              {{ item.title }} · {{ item.message_count }} 条
            </option>
          </select>
          <select
            v-if="runs.length > 0"
            class="run-select"
            :value="selectedRunId"
            @change="onRunChange"
          >
            <option value="">全部轮次（整会话）</option>
            <option v-for="(run, index) in runs" :key="run.run_id" :value="run.run_id">
              第 {{ index + 1 }} 轮 · {{ timeLabel(run.started_at) }}
            </option>
          </select>
          <button class="ghost" type="button" :disabled="loading" @click="load()">
            <AppIcon name="timer" :size="13" /> 刷新
          </button>
        </div>
      </section>

      <p v-if="error" class="error-note">{{ error }}</p>

      <div v-if="!threadId" class="panel empty">
        <div class="empty-orb"><AppIcon name="workflow" :size="24" /></div>
        <h3>还没有可查看的会话</h3>
        <p>先在对话页发送一条消息，再回到这里查看执行轨迹。</p>
      </div>

      <div v-else-if="runs.length === 0" class="panel empty">
        <div class="empty-orb"><AppIcon name="workflow" :size="24" /></div>
        <h3>本会话还没有轨迹数据</h3>
        <p>轨迹由后端 TrajectoryMiddleware 记录；发一条消息后再刷新即可。</p>
        <p class="empty-hint">如果刚升级代码，旧会话不会补记历史过程。</p>
      </div>

      <template v-else>
        <section class="summary">
          <div class="stat">
            <small>{{ selectedRunId ? '总耗时' : '累计耗时' }}</small>
            <strong>{{ durationLabel(summary.duration_ms) }}</strong>
            <span v-if="!selectedRunId" class="stat-sub">{{ activeRuns.length }} 轮</span>
          </div>
          <div class="stat">
            <small>模型调用</small>
            <strong>{{ summary.model_calls }}</strong>
          </div>
          <div class="stat">
            <small>工具 / 技能</small>
            <strong>{{ summary.tool_calls }} / {{ summary.skill_calls }}</strong>
          </div>
          <div class="stat">
            <small>委派 / 介入</small>
            <strong>{{ summary.delegations }} / {{ summary.hitl }}</strong>
          </div>
          <div class="stat">
            <small>Token</small>
            <strong>{{ summary.tokens.total }}</strong>
            <span class="stat-sub">
              in {{ summary.tokens.input }} · out {{ summary.tokens.output }}
            </span>
          </div>
          <div class="stat">
            <small>上下文峰值</small>
            <strong>{{ summary.peak_context_tokens }}</strong>
            <span class="stat-sub">
              / {{ events[events.length - 1]?.context?.limit || 0 }} tokens
            </span>
          </div>
        </section>

        <div class="traj-layout">
          <div class="traj-main">
            <section class="panel gantt-panel">
             
              <div class="axis">
                <span class="axis-spacer"></span>
                <div class="axis-track">
                  <span v-for="tick in ticks" :key="tick.label" :style="{ left: `${tick.offset}%` }">
                    {{ tick.label }}
                  </span>
                </div>
              </div>
              <div class="lanes">
                <div v-for="lane in LANES" :key="lane.key" class="lane">
                  <div class="lane-label">
                    <i :style="{ background: lane.color }"></i>
                    <AppIcon :name="lane.icon" :size="12" />
                    <span>{{ lane.label }}</span>
                  </div>
                  <div class="lane-track">
                    <button
                      v-for="event in laneEvents(lane.key)"
                      :key="event.id"
                      class="gantt-bar"
                      type="button"
                      :class="{ selected: selectedEventId === event.id }"
                      :style="barStyle(event)"
                      :title="`${barLabel(event)} · ${durationLabel(event.duration_ms)}`"
                      @click="selectEvent(event.id)"
                    >
                      <span class="bar-label">{{ barLabel(event) }}</span>
                    </button>
                  </div>
                </div>
              </div>
            </section>

            <section class="panel log-panel">
              <header class="panel-head">
                <strong>过程日志</strong>
                <small>共 {{ events.length }} 条 · 按时间顺次排列，点击查看详情</small>
              </header>
              <div class="log-list">
                <button
                  v-for="event in pagedEvents"
                  :key="event.id"
                  class="log-item"
                  type="button"
                  :class="{ selected: selectedEventId === event.id }"
                  @click="selectEvent(event.id)"
                >
                  <div class="log-main">
                    <span class="log-dot" :style="{ background: typeMeta(event.type).color }"></span>
                    <span class="log-label">{{ event.label }}</span>
                    <span class="log-status" :class="event.status">{{ statusLabel(event.status) }}</span>
                  </div>
                  <div class="log-meta">
                    <span class="log-time">{{ timeLabel(eventTime(event)) }}</span>
                    <span class="log-run">{{ eventRunLabel(event) }}</span>
                    <span class="log-type">{{ typeMeta(event.type).label }}</span>
                    <span class="log-duration">耗时 {{ durationLabel(event.duration_ms) }}</span>
                    <span class="log-tokens" v-if="event.tokens?.total">{{ event.tokens.total }} tok</span>
                  </div>
                </button>
                <p v-if="events.length === 0" class="log-empty">当前范围没有过程记录</p>
              </div>
              <footer v-if="logPages > 1" class="log-pager">
                <button type="button" :disabled="logPage <= 1" @click="logPage--">上一页</button>
                <span>第 {{ logPage }} / {{ logPages }} 页</span>
                <button type="button" :disabled="logPage >= logPages" @click="logPage++">下一页</button>
              </footer>
            </section>
          </div>

          <aside class="panel detail">
            <template v-if="selectedEvent">
              <header class="detail-head">
                <span class="detail-icon" :style="{ color: typeMeta(selectedEvent.type).color }">
                  <AppIcon :name="typeMeta(selectedEvent.type).icon" :size="16" />
                </span>
                <div>
                  <strong>{{ selectedEvent.label }}</strong>
                  <small>
                    {{ typeMeta(selectedEvent.type).label }} ·
                    {{ timeLabel(selectedEvent.start) }}
                  </small>
                </div>
                <span class="detail-status" :class="selectedEvent.status">
                  {{ statusLabel(selectedEvent.status) }}
                </span>
              </header>

              <div class="detail-scroll">
                <div class="kv">
                  <div><small>开始</small><span>{{ timeLabel(selectedEvent.start) }}</span></div>
                  <div><small>耗时</small><span>{{ durationLabel(selectedEvent.duration_ms) }}</span></div>
                  <div v-if="selectedEvent.tool_name"><small>工具</small><span>{{ selectedEvent.tool_name }}</span></div>
                  <div v-if="selectedEvent.subagent_name"><small>子 Agent</small><span>{{ selectedEvent.subagent_name }}</span></div>
                  <div v-if="selectedEvent.tool_call_id"><small>Call ID</small><span class="mono">{{ selectedEvent.tool_call_id }}</span></div>
                  <div v-if="selectedEvent.source"><small>来源</small><span>{{ selectedEvent.source }}</span></div>
                </div>

                <section v-if="selectedEvent.tokens && Object.keys(selectedEvent.tokens).length" class="detail-block">
                  <h4>Token 消耗</h4>
                  <div class="token-row">
                    <span>输入 <b>{{ selectedEvent.tokens?.input || 0 }}</b></span>
                    <span>输出 <b>{{ selectedEvent.tokens?.output || 0 }}</b></span>
                    <span>合计 <b>{{ selectedEvent.tokens?.total || 0 }}</b></span>
                  </div>
                </section>

                <section v-if="selectedEvent.context?.limit" class="detail-block">
                  <h4>上下文占用</h4>
                  <div class="context-row">
                    <div class="context-bar">
                      <i :style="{ width: `${Math.min((selectedEvent.context?.pct || 0) * 100, 100)}%` }"></i>
                    </div>
                    <span>
                      {{ selectedEvent.context?.used_tokens }} /
                      {{ selectedEvent.context?.limit }}
                      ({{ ((selectedEvent.context?.pct || 0) * 100).toFixed(1) }}%)
                    </span>
                  </div>
                </section>

                <section v-if="selectedEvent.args" class="detail-block">
                  <h4>参数</h4>
                  <pre>{{ selectedEvent.args }}</pre>
                </section>

                <section v-if="selectedEvent.result" class="detail-block">
                  <h4>{{ selectedEvent.type === 'model' ? '思考内容' : '返回结果' }}</h4>
                  <pre>{{ selectedEvent.result }}</pre>
                </section>

                <section v-if="selectedEvent.preview" class="detail-block">
                  <h4>内容预览</h4>
                  <pre>{{ selectedEvent.preview }}</pre>
                </section>

                <section v-if="selectedEvent.summary && Object.keys(selectedEvent.summary).length" class="detail-block">
                  <h4>本轮汇总</h4>
                  <pre>{{ JSON.stringify(selectedEvent.summary, null, 2) }}</pre>
                </section>
              </div>
            </template>
            <div v-else class="empty-detail">
              <AppIcon name="workflow" :size="20" />
              <p>在左侧选择一条过程记录</p>
            </div>
          </aside>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.app-shell {
  min-height: 100vh;
}

.page {
  padding: 14px 18px 22px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.panel {
  border: 0;
  border-top: 1px solid var(--line);
  border-radius: 0;
  background: transparent;
  backdrop-filter: none;
}

.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding: 12px 16px;
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

.head-controls {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.session-select,
.run-select {
  max-width: 260px;
  padding: 7px 10px;
  font-size: 11.5px;
  font-family: inherit;
  color: var(--text-2);
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-sm);
  background: var(--bg-deep);
  outline: none;
}

.status-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex: none;
  padding: 3px 9px;
  font-size: 10.5px;
  white-space: nowrap;
  color: var(--text-2);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: transparent;
}

.status-chip .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  box-shadow: 0 0 6px currentColor;
}

.auto-toggle {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex: none;
}

.auto-toggle em {
  font-style: normal;
  font-size: 11.5px;
  white-space: nowrap;
  color: var(--text-2);
}

.ghost {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  flex: none;
  padding: 5px 10px;
  font-size: 11.5px;
  white-space: nowrap;
  color: var(--text-2);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: transparent;
  transition: color 0.16s, border-color 0.16s, background 0.16s;
}

.ghost:hover:not(:disabled) {
  color: var(--text-1);
  border-color: var(--line-strong);
  background: var(--bg-hover);
}

.ghost:disabled {
  opacity: 0.5;
  cursor: not-allowed;
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

.summary {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 8px;
}

.stat {
  padding: 10px 2px 2px;
  border: 0;
  border-top: 1px solid var(--line);
  border-radius: 0;
  background: transparent;
}

.stat small {
  display: block;
  font-size: 10px;
  color: var(--text-3);
}

.stat strong {
  display: block;
  margin-top: 2px;
  font-family: var(--font-mono);
  font-size: 15px;
  font-variant-numeric: tabular-nums;
  color: var(--text-1);
}

.stat-sub {
  font-size: 9.5px;
  color: var(--text-3);
}

.traj-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 330px;
  gap: 12px;
  align-items: start;
}

.traj-main {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
}

.gantt-panel,
.log-panel {
  padding: 12px 14px 14px;
}

.panel-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 10px;
}

.panel-head strong {
  font-size: 13px;
}

.panel-head small {
  font-size: 10.5px;
  color: var(--text-3);
}

.axis {
  display: grid;
  grid-template-columns: 96px 1fr;
  margin-bottom: 4px;
}

.axis-track {
  position: relative;
  height: 16px;
}

.axis-track span {
  position: absolute;
  top: 0;
  font-size: 9.5px;
  color: var(--text-3);
  transform: translateX(-50%);
  font-variant-numeric: tabular-nums;
}

.axis-track span:first-child {
  transform: none;
}

.axis-track span:last-child {
  transform: translateX(-100%);
}

.lanes {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.lane {
  display: grid;
  grid-template-columns: 96px 1fr;
  align-items: center;
  min-height: 24px;
}

.lane-label {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 10.5px;
  color: var(--text-2);
}

.lane-label i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex: none;
}

.lane-track {
  position: relative;
  height: 20px;
  border-radius: 0;
  background:
    repeating-linear-gradient(
      90deg,
      rgba(126, 154, 198, 0.06) 0,
      rgba(126, 154, 198, 0.06) 1px,
      transparent 1px,
      transparent 25%
    ),
    var(--bg-rail);
}

.gantt-bar {
  position: absolute;
  top: 4px;
  height: 12px;
  padding: 0;
  border: none;
  border-radius: 2px;
  opacity: 0.7;
  overflow: hidden;
  cursor: pointer;
  transition: opacity 0.15s, box-shadow 0.15s;
}

.gantt-bar:hover {
  opacity: 1;
}

.gantt-bar.selected {
  opacity: 1;
  box-shadow: 0 0 0 1px rgba(230, 237, 249, 0.8);
}

.bar-label {
  display: block;
  padding: 0 4px;
  overflow: hidden;
  font-size: 8px;
  line-height: 12px;
  color: rgba(4, 12, 26, 0.9);
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.log-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding-right: 2px;
}

/* 日志分页：分页后列表可以完整展开，不再用内部滚动条挤压内容 */
.log-pager {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 10px 0 2px;
  font-size: 11px;
  color: var(--text-3);
}

.log-pager button {
  padding: 4px 12px;
  font-size: 11px;
  color: var(--text-2);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: transparent;
}

.log-pager button:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.log-pager button:hover:not(:disabled) {
  color: var(--text-1);
  border-color: var(--line-strong);
}

/* 两行式日志：第一行标题，第二行元信息，避免多列挤压内容 */
.log-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 10px;
  font-size: 11px;
  text-align: left;
  color: var(--text-2);
  border: 0;
  border-bottom: 1px solid var(--line);
  border-radius: 0;
  background: transparent;
  transition: background 0.15s, color 0.15s;
}

.log-item:hover {
  background: var(--bg-hover);
}

.log-item.selected {
  color: var(--text-1);
  background: var(--bg-hover);
  box-shadow: inset 2px 0 0 var(--accent);
}

.log-main {
  display: flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
}

.log-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  padding-left: 13px;
  font-size: 9.5px;
  color: var(--text-3);
}

.log-time {
  font-size: 10px;
  color: var(--text-3);
  font-variant-numeric: tabular-nums;
}

.log-run {
  font-size: 9.5px;
  color: var(--text-3);
}

.log-dot {
  width: 6px;
  height: 6px;
  flex: none;
  border-radius: 50%;
}

.log-label {
  flex: 1;
  min-width: 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-1);
  word-break: break-word;
}

.log-type {
  font-size: 9.5px;
  color: var(--text-3);
}

.log-duration,
.log-tokens {
  font-size: 9.5px;
  color: var(--text-3);
  font-variant-numeric: tabular-nums;
}

.log-status {
  flex: none;
  margin-left: auto;
  padding: 1px 6px;
  font-size: 9px;
  text-align: center;
  border-radius: var(--radius-sm);
  border: 1px solid var(--line);
}

.log-status.done {
  color: var(--accent-cyan);
  border-color: rgba(55, 208, 196, 0.3);
}

.log-status.running {
  color: var(--accent);
  border-color: rgba(91, 140, 255, 0.3);
}

.log-status.error {
  color: var(--danger);
  border-color: rgba(226, 96, 94, 0.35);
}

.log-status.interrupted {
  color: var(--amber);
  border-color: rgba(242, 176, 60, 0.35);
}

.log-status.answered,
.log-status.approved {
  color: var(--green);
  border-color: rgba(61, 220, 132, 0.3);
}

.log-status.rejected {
  color: var(--danger);
  border-color: rgba(226, 96, 94, 0.35);
}

.log-empty {
  margin: 0;
  padding: 10px;
  font-size: 11px;
  color: var(--text-3);
}

.detail {
  position: sticky;
  top: calc(var(--topbar-h) + 14px);
  display: flex;
  flex-direction: column;
  max-height: calc(100vh - var(--topbar-h) - 28px);
  overflow: hidden;
}

.detail-head {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 12px 14px;
  border-bottom: 1px solid var(--line);
}

.detail-icon {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  flex: none;
  border-radius: var(--radius-sm);
  background: transparent;
}

.detail-head strong {
  display: block;
  font-size: 12.5px;
}

.detail-head small {
  font-size: 10px;
  color: var(--text-3);
}

.detail-status {
  margin-left: auto;
  padding: 1px 7px;
  font-size: 9.5px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--line);
  color: var(--text-3);
}

.detail-status.done {
  color: var(--green);
  border-color: rgba(61, 220, 132, 0.3);
}

.detail-status.running {
  color: var(--accent-cyan);
  border-color: rgba(55, 208, 196, 0.3);
}

.detail-status.error {
  color: var(--danger);
  border-color: rgba(226, 96, 94, 0.35);
}

.detail-status.interrupted {
  color: var(--amber);
  border-color: rgba(242, 176, 60, 0.35);
}

.detail-status.answered,
.detail-status.approved {
  color: var(--green);
  border-color: rgba(61, 220, 132, 0.3);
}

.detail-status.rejected {
  color: var(--danger);
  border-color: rgba(226, 96, 94, 0.35);
}

.detail-scroll {
  padding: 12px 14px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.kv {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 7px;
}

.kv div {
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding: 6px 8px;
  border-radius: 0;
  background: transparent;
  border-top: 1px solid var(--line);
}

.kv small {
  font-size: 9.5px;
  color: var(--text-3);
}

.kv span {
  font-size: 10.5px;
  color: var(--text-2);
  overflow-wrap: anywhere;
}

.kv .mono {
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 9.5px;
}

.detail-block h4 {
  margin: 0 0 6px;
  font-size: 10.5px;
  color: var(--text-3);
  font-weight: 500;
}

.detail-block pre {
  margin: 0;
  padding: 8px 10px;
  max-height: 220px;
  overflow: auto;
  font-family: var(--font-mono);
  font-size: 10px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--text-2);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--bg-deep);
}

.token-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  font-size: 10.5px;
  color: var(--text-3);
}

.token-row b {
  color: var(--text-1);
  font-variant-numeric: tabular-nums;
}

.context-row {
  display: flex;
  flex-direction: column;
  gap: 5px;
  font-size: 10px;
  color: var(--text-3);
}

.context-bar {
  height: 6px;
  overflow: hidden;
  border-radius: 2px;
  background: rgba(126, 154, 198, 0.16);
}

.context-bar i {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, var(--accent-cyan), var(--accent));
}

.empty-detail {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 180px;
  color: var(--text-3);
  font-size: 11px;
}

.empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: calc(100vh - var(--topbar-h) - 120px);
  text-align: center;
}

.empty-orb {
  display: grid;
  place-items: center;
  width: 48px;
  height: 48px;
  margin-bottom: 12px;
  border-radius: 50%;
  color: var(--accent-cyan);
  border: 1px solid var(--line-strong);
  background: transparent;
}

.empty h3 {
  margin: 0 0 5px;
  font-size: 16px;
}

.empty p {
  margin: 0;
  font-size: 11.5px;
  color: var(--text-3);
}

.empty-hint {
  margin-top: 6px !important;
  opacity: 0.7;
}

@media (max-width: 1200px) {
  .summary {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .traj-layout {
    grid-template-columns: minmax(0, 1fr);
  }

  .detail {
    position: static;
    max-height: none;
  }
}
</style>
