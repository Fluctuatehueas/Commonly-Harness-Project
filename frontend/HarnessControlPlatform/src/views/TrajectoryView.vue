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
  { key: 'run', label: 'Run', color: '#5b6b8c', icon: 'workflow' },
  { key: 'model', label: '模型', color: '#4f8cff', icon: 'sparkles' },
  { key: 'tool', label: '工具', color: '#35e0ff', icon: 'wrench' },
  { key: 'skill', label: '技能', color: '#a78bfa', icon: 'layers' },
  { key: 'delegation', label: '委派', color: '#ffb648', icon: 'users' },
  { key: 'hitl', label: '人工介入', color: '#ff6b6b', icon: 'shield' },
] as const

const TYPE_META: Record<string, { label: string; icon: IconName; color: string }> = {
  run: { label: 'Run', icon: 'workflow', color: '#5b6b8c' },
  model: { label: '模型', icon: 'sparkles', color: '#4f8cff' },
  tool: { label: '工具', icon: 'wrench', color: '#35e0ff' },
  skill: { label: '技能', icon: 'layers', color: '#a78bfa' },
  delegation: { label: '委派', icon: 'users', color: '#ffb648' },
  hitl: { label: '人工介入', icon: 'shield', color: '#ff6b6b' },
}

const selectedRun = computed(
  () => runs.value.find((run) => run.run_id === selectedRunId.value) || runs.value[runs.value.length - 1],
)

const events = computed(() => selectedRun.value?.events || [])

const selectedEvent = computed(
  () => events.value.find((event) => event.id === selectedEventId.value) || null,
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
  return `+${Math.floor(seconds / 60)}m${Math.round(seconds % 60)}s`
}

function durationLabel(ms: number | null | undefined) {
  if (ms === null || ms === undefined) return '—'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)}s`
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
  const run = runs.value.find((item) => item.run_id === runId)
  const list = run?.events || []
  const last = [...list].reverse().find((event) => event.type !== 'run') || list[list.length - 1]
  selectedEventId.value = last?.id || ''
}

function onRunChange(event: Event) {
  selectRun((event.target as HTMLSelectElement).value)
}

function selectEvent(eventId: string) {
  selectedEventId.value = eventId
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
    if (!data.runs.some((run) => run.run_id === selectedRunId.value)) {
      selectRun(data.latest_run_id || data.runs[data.runs.length - 1]?.run_id || '')
    } else if (!selectedEventId.value) {
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
    if (selectedRun.value?.status === 'running') void load()
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
          <i class="dot"></i>{{ selectedRun ? statusLabel(selectedRun.status) : '暂无运行' }}
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
            v-if="runs.length > 1"
            class="run-select"
            :value="selectedRunId"
            @change="onRunChange"
          >
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

      <div v-else-if="!selectedRun" class="panel empty">
        <div class="empty-orb"><AppIcon name="workflow" :size="24" /></div>
        <h3>本会话还没有轨迹数据</h3>
        <p>轨迹由后端 TrajectoryMiddleware 记录；发一条消息后再刷新即可。</p>
        <p class="empty-hint">如果刚升级代码，旧会话不会补记历史过程。</p>
      </div>

      <template v-else>
        <section class="summary">
          <div class="stat">
            <small>总耗时</small>
            <strong>{{ durationLabel(selectedRun.summary.duration_ms) }}</strong>
          </div>
          <div class="stat">
            <small>模型调用</small>
            <strong>{{ selectedRun.summary.model_calls }}</strong>
          </div>
          <div class="stat">
            <small>工具 / 技能</small>
            <strong>{{ selectedRun.summary.tool_calls }} / {{ selectedRun.summary.skill_calls }}</strong>
          </div>
          <div class="stat">
            <small>委派 / 介入</small>
            <strong>{{ selectedRun.summary.delegations }} / {{ selectedRun.summary.hitl }}</strong>
          </div>
          <div class="stat">
            <small>Token</small>
            <strong>{{ selectedRun.summary.tokens.total }}</strong>
            <span class="stat-sub">
              in {{ selectedRun.summary.tokens.input }} · out {{ selectedRun.summary.tokens.output }}
            </span>
          </div>
          <div class="stat">
            <small>上下文峰值</small>
            <strong>{{ selectedRun.summary.peak_context_tokens }}</strong>
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
                <small>按时间顺次排列，点击查看详情</small>
              </header>
              <div class="log-list">
                <button
                  v-for="event in events"
                  :key="event.id"
                  class="log-item"
                  type="button"
                  :class="{ selected: selectedEventId === event.id }"
                  @click="selectEvent(event.id)"
                >
                  <span class="log-time">{{ timeLabel(eventTime(event)) }}</span>
                  <span class="log-dot" :style="{ background: typeMeta(event.type).color }"></span>
                  <span class="log-label">{{ event.label }}</span>
                  <span class="log-type">{{ typeMeta(event.type).label }}</span>
                  <span class="log-duration">{{ durationLabel(event.duration_ms) }}</span>
                  <span class="log-status" :class="event.status">{{ statusLabel(event.status) }}</span>
                  <span class="log-tokens" v-if="event.tokens?.total">{{ event.tokens.total }} tok</span>
                </button>
                <p v-if="events.length === 0" class="log-empty">本轮没有过程记录</p>
              </div>
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
  border: 1px solid var(--line);
  border-radius: 14px;
  background: var(--bg-card);
  backdrop-filter: blur(8px);
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
  width: 30px;
  height: 30px;
  border-radius: 8px;
  color: var(--accent-cyan);
  background: rgba(53, 224, 255, 0.1);
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
  border-radius: 8px;
  background: rgba(6, 13, 28, 0.8);
  outline: none;
}

.status-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex: none;
  padding: 4px 11px;
  font-size: 11px;
  white-space: nowrap;
  color: var(--text-2);
  border: 1px solid var(--line);
  border-radius: 999px;
  background: rgba(16, 32, 64, 0.5);
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
  padding: 6px 12px;
  font-size: 12px;
  white-space: nowrap;
  color: var(--text-2);
  border: 1px solid var(--line);
  border-radius: 999px;
  background: transparent;
  transition: all 0.18s;
}

.ghost:hover:not(:disabled) {
  color: var(--text-1);
  border-color: var(--line-strong);
}

.ghost:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.error-note {
  margin: 0;
  padding: 8px 12px;
  font-size: 12px;
  color: var(--amber);
  border: 1px solid rgba(255, 182, 72, 0.3);
  border-radius: 10px;
  background: rgba(255, 182, 72, 0.08);
}

.summary {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 8px;
}

.stat {
  position: relative;
  overflow: hidden;
  padding: 11px 13px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background:
    radial-gradient(120% 90% at 85% -10%, rgba(63, 122, 255, 0.16), transparent 55%),
    linear-gradient(160deg, rgba(24, 44, 84, 0.62), rgba(9, 18, 38, 0.74));
  box-shadow: inset 0 0 20px rgba(63, 122, 255, 0.05);
  backdrop-filter: blur(6px);
  transition: border-color 0.22s, box-shadow 0.22s, transform 0.22s;
}

/* 卡内漂浮的星尘光点 */
.stat::before {
  content: "";
  position: absolute;
  inset: 0;
  pointer-events: none;
  background-image:
    radial-gradient(1.2px 1.2px at 18% 30%, rgba(160, 225, 255, 0.9), transparent 60%),
    radial-gradient(1px 1px at 68% 18%, rgba(200, 235, 255, 0.75), transparent 60%),
    radial-gradient(1.4px 1.4px at 84% 62%, rgba(120, 210, 255, 0.7), transparent 60%),
    radial-gradient(1px 1px at 38% 78%, rgba(190, 225, 255, 0.6), transparent 60%);
  animation: cosmic-twinkle 3.2s ease-in-out infinite;
}

.stat:hover {
  transform: translateY(-2px);
  border-color: var(--line-strong);
  box-shadow:
    inset 0 0 20px rgba(63, 122, 255, 0.08),
    0 6px 22px rgba(8, 20, 46, 0.55),
    0 0 26px rgba(53, 224, 255, 0.14);
}

.stat small {
  display: block;
  font-size: 10px;
  color: var(--text-3);
}

.stat strong {
  display: block;
  margin-top: 2px;
  font-size: 16px;
  font-variant-numeric: tabular-nums;
  background: linear-gradient(100deg, #dff3ff, #7cc4ff 55%, #35e0ff);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  text-shadow: 0 0 18px rgba(53, 224, 255, 0.25);
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
  border-radius: 5px;
  background:
    repeating-linear-gradient(
      90deg,
      rgba(96, 156, 255, 0.07) 0,
      rgba(96, 156, 255, 0.07) 1px,
      transparent 1px,
      transparent 25%
    ),
    rgba(6, 13, 28, 0.45);
}

.gantt-bar {
  position: absolute;
  top: 4px;
  height: 12px;
  padding: 0;
  border: none;
  border-radius: 4px;
  opacity: 0.65;
  overflow: hidden;
  cursor: pointer;
  transition: opacity 0.15s, box-shadow 0.15s, transform 0.15s;
}

.gantt-bar:hover {
  opacity: 0.95;
  transform: translateY(-1px);
}

.gantt-bar.selected {
  opacity: 1;
  box-shadow: 0 0 0 1.5px rgba(255, 255, 255, 0.75), 0 0 10px rgba(53, 224, 255, 0.35);
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
  gap: 2px;
  max-height: 340px;
  overflow-y: auto;
}

.log-item {
  display: grid;
  grid-template-columns: 68px 8px minmax(0, 1fr) 54px 52px 58px 66px;
  align-items: center;
  gap: 7px;
  padding: 7px 8px;
  font-size: 11px;
  text-align: left;
  color: var(--text-2);
  border: 1px solid transparent;
  border-radius: 7px;
  background: transparent;
  transition: all 0.15s;
}

.log-item:hover {
  background: rgba(79, 140, 255, 0.07);
}

.log-item.selected {
  color: var(--text-1);
  border-color: var(--line-strong);
  background: rgba(53, 224, 255, 0.06);
}

.log-time {
  font-size: 10px;
  color: var(--text-3);
  font-variant-numeric: tabular-nums;
}

.log-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
}

.log-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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
  text-align: right;
}

.log-status {
  padding: 1px 5px;
  font-size: 9px;
  text-align: center;
  border-radius: 999px;
  border: 1px solid var(--line);
}

.log-status.done {
  color: var(--green);
  border-color: rgba(61, 220, 132, 0.3);
}

.log-status.running {
  color: var(--accent-cyan);
  border-color: rgba(53, 224, 255, 0.3);
}

.log-status.error {
  color: #ff7b7b;
  border-color: rgba(255, 93, 93, 0.35);
}

.log-status.interrupted {
  color: var(--amber);
  border-color: rgba(255, 182, 72, 0.35);
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
  width: 30px;
  height: 30px;
  flex: none;
  border-radius: 8px;
  background: rgba(79, 140, 255, 0.12);
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
  padding: 2px 7px;
  font-size: 9.5px;
  border-radius: 999px;
  border: 1px solid var(--line);
  color: var(--text-3);
}

.detail-status.done {
  color: var(--green);
  border-color: rgba(61, 220, 132, 0.3);
}

.detail-status.running {
  color: var(--accent-cyan);
  border-color: rgba(53, 224, 255, 0.3);
}

.detail-status.error {
  color: #ff7b7b;
  border-color: rgba(255, 93, 93, 0.35);
}

.detail-status.interrupted {
  color: var(--amber);
  border-color: rgba(255, 182, 72, 0.35);
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
  border-radius: 7px;
  background: rgba(6, 13, 28, 0.5);
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
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 10px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--text-2);
  border: 1px solid var(--line);
  border-radius: 8px;
  background: rgba(4, 10, 22, 0.72);
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
  border-radius: 999px;
  background: rgba(96, 156, 255, 0.16);
}

.context-bar i {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, #35e0ff, #4f8cff);
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
  width: 56px;
  height: 56px;
  margin-bottom: 12px;
  border-radius: 50%;
  color: var(--accent-cyan);
  background: radial-gradient(circle at 35% 30%, rgba(53, 224, 255, 0.22), rgba(30, 70, 150, 0.25));
  box-shadow: 0 0 28px rgba(53, 224, 255, 0.2);
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
