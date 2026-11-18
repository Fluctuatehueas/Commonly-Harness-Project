<script setup lang="ts">
// Chat 消息流中的任务流执行卡片：流名称 / 当前步骤 / 进度条 / 步骤状态图标。
// 鼠标进入自动展开步骤列表，离开或点击收起；归档后固定在本轮轨迹底部。
import { computed, ref } from 'vue'
import AppIcon from '@/components/console/AppIcon.vue'
import type { ExecutionStepView, StepTrace } from '@/api/taskflow'

const props = withDefaults(
  defineProps<{
    flowName: string
    status: 'running' | 'done' | 'failed' | 'aborted'
    /** 当前正在执行的步骤名 */
    currentStep?: string
    steps?: ExecutionStepView[]
    traces?: StepTrace[]
    /** 归档态：位于本轮轨迹底部，只影响样式 */
    archived?: boolean
  }>(),
  { currentStep: '', steps: () => [], traces: () => [], archived: false },
)

const open = ref(false)

function toggle() {
  open.value = !open.value
}

const statusLabel: Record<string, string> = {
  running: '执行中',
  done: '已完成',
  failed: '失败',
  aborted: '已中止',
}

const stepStatusLabel: Record<string, string> = {
  pending: '等待',
  running: '运行',
  done: '完成',
  failed: '失败',
  degraded: '兜底',
}

const stepIcon: Record<string, 'clock' | 'play' | 'check' | 'close' | 'wrench'> = {
  pending: 'clock',
  running: 'play',
  done: 'check',
  failed: 'close',
  degraded: 'wrench',
}

const total = computed(() => props.steps.length)

const doneCount = computed(
  () => props.steps.filter((step) => step.status === 'done' || step.status === 'degraded').length,
)

/** 收起时展示的当前步骤：优先运行中的步骤，其次按名称匹配。 */
const current = computed(() => {
  const index = props.steps.findIndex((step) => step.status === 'running')
  const running = index >= 0 ? props.steps[index] : undefined
  if (running) return { index, step: running }
  if (!props.currentStep) return null
  const fallback = props.steps.findIndex((step) => step.name === props.currentStep)
  const target = fallback >= 0 ? props.steps[fallback] : undefined
  return target ? { index: fallback, step: target } : null
})

/** 本轮结束时仍未执行的步骤数，收起态也能看出流程走到了哪一步。 */
const leftover = computed(() =>
  props.status === 'running' ? 0 : Math.max(0, total.value - doneCount.value),
)

const stateLabel = computed(() => {
  if (props.status === 'done' && leftover.value > 0) return '已结束'
  return statusLabel[props.status] || props.status
})

const partial = computed(() => props.status === 'done' && leftover.value > 0)

const progress = computed(() => {
  if (!total.value) return 0
  return Math.round((doneCount.value / total.value) * 100)
})

function tracesOf(stepId: string): StepTrace[] {
  return props.traces.filter((trace) => trace.step_id === stepId)
}

function traceOutput(trace: StepTrace): string {
  return trace.output || trace.output_summary || ''
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}
</script>

<template>
  <article
    class="exec-card"
    :class="[status, { archived, partial }]"
    @mouseenter="open = true"
    @mouseleave="open = false"
  >
    <header class="exec-head" @click="toggle">
      <span class="exec-icon">
        <AppIcon
          :name="
            status === 'done'
              ? 'check'
              : status === 'failed'
                ? 'close'
                : status === 'aborted'
                  ? 'stop'
                  : 'target'
          "
          :size="13"
        />
      </span>
      <div class="exec-title">
        <strong>{{ flowName }}</strong>
        <small class="exec-sub">
          <template v-if="status === 'running'">
            <template v-if="current">
              <span class="cur-step">
                <em>第 {{ current.index + 1 }} / {{ total }} 步</em>
                <AppIcon
                  :name="stepIcon[current.step.status] || 'play'"
                  :size="12"
                  class="cur-icon"
                  :class="current.step.status"
                />
                <span class="cur-name">{{ current.step.name }}</span>
              </span>
            </template>
            <template v-else>准备中 · 已完成 {{ doneCount }} / {{ total }} 步</template>
          </template>
          <template v-else>
            已完成 {{ doneCount }} / {{ total }} 步
            <span v-if="leftover" class="leftover">· {{ leftover }} 步未执行</span>
          </template>
        </small>
      </div>
      <span class="exec-state">{{ stateLabel }}</span>
      <button
        class="expand-btn"
        type="button"
        :title="open ? '收起步骤进度' : '展开步骤进度'"
        @click.stop="toggle"
      >
        <AppIcon name="chevronRight" :size="12" :class="{ rotated: open }" />
      </button>
    </header>

    <div class="exec-progress">
      <i :style="{ width: `${Math.max(4, progress)}%` }"></i>
    </div>

    <div class="exec-body" :class="{ open }">
      <div class="exec-body-inner">
        <ul v-if="steps.length" class="step-list">
          <li v-for="(step, index) in steps" :key="step.id" class="step-item" :class="step.status">
            <span class="step-name">
              <em class="step-index">{{ index + 1 }}</em>
              <span class="step-dash">—</span>
              <span>{{ step.name || step.id }}</span>
            </span>
            <em v-if="step.branch" class="step-branch">{{ step.branch }}</em>
            <span class="step-state" :title="stepStatusLabel[step.status] || step.status">
              <AppIcon :name="stepIcon[step.status] || 'clock'" :size="13" />
            </span>

            <!-- 运行中的步骤实时展示模型增量输出，避免长时间无反馈 -->
            <pre v-if="step.status === 'running' && step.output" class="live-output">{{
              step.output
            }}</pre>

            <div v-if="tracesOf(step.id).length" class="traces">
              <div v-for="(trace, ti) in tracesOf(step.id)" :key="ti" class="trace">
                <p class="trace-meta">
                  <span v-if="trace.branch">分支：{{ trace.branch }} · </span>
                  <span>尝试 {{ trace.attempts }} 次 · {{ formatDuration(trace.duration_ms) }}</span>
                  <span v-if="trace.state_source === 'user_provided'" class="user-provided"
                    >用户提供的上游状态</span
                  >
                </p>
                <p v-if="traceOutput(trace)" class="trace-text">{{ traceOutput(trace) }}</p>
                <p v-if="trace.check_result" class="trace-text check">
                  校验：{{ trace.check_result }}
                </p>
              </div>
            </div>
          </li>
        </ul>
      </div>
    </div>
  </article>
</template>

<style scoped>
.exec-card {
  display: flex;
  flex-direction: column;
  padding: 12px 14px;
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-md);
  background: var(--bg-elevated);
}

.exec-card.done {
  border-color: rgba(55, 208, 196, 0.35);
}

.exec-card.failed {
  border-color: rgba(226, 96, 94, 0.42);
}

.exec-card.partial {
  border-color: rgba(242, 176, 60, 0.35);
}

.exec-card.archived {
  margin-top: 4px;
  background: transparent;
}

.exec-head {
  display: flex;
  align-items: center;
  gap: 9px;
  margin-bottom: 8px;
  cursor: pointer;
}

.exec-icon {
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  flex: none;
  border-radius: var(--radius-sm);
  color: var(--accent-cyan);
  background: transparent;
}

.exec-card.done .exec-icon {
  color: var(--green);
  background: rgba(55, 201, 164, 0.12);
}

.exec-card.failed .exec-icon {
  color: var(--danger);
  background: rgba(226, 96, 94, 0.12);
}

.exec-card.aborted .exec-icon {
  color: var(--text-3);
  background: rgba(255, 255, 255, 0.05);
}

.exec-title {
  flex: 1;
  min-width: 0;
}

.exec-title strong {
  display: block;
  font-size: 12px;
  color: var(--text-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.exec-sub {
  display: block;
  margin-top: 1px;
  font-size: 10px;
  color: var(--text-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 收起态也能看到当前步骤序号、状态图标与名称 */
.cur-step {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  max-width: 100%;
}

.cur-step em {
  flex: none;
  font-style: normal;
  font-family: var(--font-mono);
  color: var(--accent-cyan);
}

.cur-icon.running {
  color: var(--accent-cyan);
  animation: exec-pulse 1.2s ease-in-out infinite;
}

.cur-icon.done {
  color: var(--green);
}

.cur-icon.failed {
  color: var(--danger);
}

.cur-icon.degraded {
  color: var(--amber);
}

.cur-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-2);
}

.leftover {
  color: var(--amber);
}

@keyframes exec-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.45; }
}

.exec-state {
  flex: none;
  font-size: 10px;
  color: var(--text-3);
}

.exec-card.partial .exec-state {
  color: var(--amber);
}

.expand-btn {
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

.expand-btn:hover {
  color: var(--text-1);
  background: rgba(255, 255, 255, 0.06);
}

.expand-btn .rotated {
  transform: rotate(90deg);
}

.exec-progress {
  height: 5px;
  overflow: hidden;
  border-radius: 2px;
  background: rgba(126, 154, 198, 0.14);
}

.exec-progress i {
  display: block;
  height: 100%;
  border-radius: 2px;
  background: linear-gradient(90deg, var(--accent-cyan), var(--accent));
  transition: width 0.35s ease;
}

.exec-card.failed .exec-progress i {
  background: var(--danger);
}

.exec-card.aborted .exec-progress i {
  background: var(--text-3);
}

.exec-card.partial .exec-progress i {
  background: linear-gradient(90deg, var(--accent-cyan), var(--amber));
}

/* 高度过渡：0fr -> 1fr，内容多少都能平滑展开 */
.exec-body {
  display: grid;
  grid-template-rows: 0fr;
  transition: grid-template-rows 0.24s ease;
}

.exec-body.open {
  grid-template-rows: 1fr;
}

.exec-body-inner {
  min-height: 0;
  overflow: hidden;
}

.exec-body-inner > :first-child {
  margin-top: 8px;
}

.step-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 260px;
  overflow: auto;
}

.step-item {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 7px;
  min-height: 20px;
  /* 列表超高时不允许压缩条目，否则步骤产出会溢出并与相邻行重叠 */
  flex: 0 0 auto;
  font-size: 11px;
  color: var(--text-3);
}

/* 左侧：序号 —— 步骤名；右侧只保留状态图标 */
.step-name {
  display: flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
  color: var(--text-2);
}

.step-index {
  flex: none;
  min-width: 14px;
  font-family: var(--font-mono);
  font-style: normal;
  font-size: 10px;
  color: var(--text-3);
  text-align: right;
}

.step-dash {
  flex: none;
  color: var(--line-strong);
}

.step-item.done .step-name,
.step-item.running .step-name {
  color: var(--text-1);
}

.step-state {
  display: grid;
  place-items: center;
  flex: none;
  width: 18px;
  height: 18px;
  margin-left: auto;
  border-radius: 50%;
  color: var(--text-3);
}

.step-item.running .step-state {
  color: var(--accent-cyan);
  border: 1px solid rgba(55, 208, 196, 0.5);
}

.step-item.done .step-state {
  color: var(--green);
  border: 1px solid rgba(55, 201, 164, 0.45);
}

.step-item.failed .step-state {
  color: var(--danger);
  border: 1px solid rgba(226, 96, 94, 0.45);
}

.step-item.degraded .step-state {
  color: var(--amber);
  border: 1px solid rgba(242, 176, 60, 0.45);
}

.step-branch {
  padding: 1px 6px;
  font-style: normal;
  font-size: 9px;
  color: var(--accent-cyan);
  border: 1px solid rgba(55, 208, 196, 0.35);
  border-radius: 999px;
}

.live-output {
  width: 100%;
  max-height: 132px;
  margin: 2px 0 2px 25px;
  padding: 7px 9px;
  overflow: auto;
  font-family: inherit;
  font-size: 10.5px;
  line-height: 1.55;
  color: var(--text-2);
  white-space: pre-wrap;
  word-break: break-word;
  border-left: 1px solid rgba(55, 208, 196, 0.4);
  background: rgba(55, 208, 196, 0.04);
}

.traces {
  width: 100%;
  margin: 4px 0 2px 25px;
  padding: 7px 9px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  border-left: 1px solid var(--line);
}

.trace-meta {
  margin: 0;
  font-size: 9.5px;
  color: var(--text-3);
}

.user-provided {
  margin-left: 4px;
  color: var(--amber);
}

.trace-text {
  margin: 0;
  font-size: 10.5px;
  line-height: 1.5;
  color: var(--text-2);
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
}

.trace-text.check {
  color: var(--amber);
}
</style>
