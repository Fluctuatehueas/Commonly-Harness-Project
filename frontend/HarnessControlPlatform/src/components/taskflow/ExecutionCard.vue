<script setup lang="ts">
// Chat 消息流中的任务流执行卡片：流名称 / 当前步骤 / 进度条 / 步骤状态图标，可展开看 step_traces。
// 展示型组件：Chat 集成时由 SSE 事件或 Execution 记录映射出 props 传入。
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
  }>(),
  { currentStep: '', steps: () => [], traces: () => [] },
)

const expanded = ref(false)

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

const progress = computed(() => {
  if (!props.steps.length) return 0
  const finished = props.steps.filter(
    (step) => step.status === 'done' || step.status === 'degraded',
  ).length
  return Math.round((finished / props.steps.length) * 100)
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
  <article class="exec-card" :class="status">
    <header class="exec-head">
      <span class="exec-icon">
        <AppIcon :name="status === 'done' ? 'check' : status === 'failed' ? 'close' : 'target'" :size="13" />
      </span>
      <div class="exec-title">
        <strong>{{ flowName }}</strong>
        <small v-if="status === 'running' && currentStep">当前步骤：{{ currentStep }}</small>
        <small v-else>{{ statusLabel[status] || status }}</small>
      </div>
      <span class="exec-state">{{ statusLabel[status] || status }}</span>
      <button
        v-if="traces.length"
        class="expand-btn"
        type="button"
        :title="expanded ? '收起步骤详情' : '展开步骤详情'"
        @click="expanded = !expanded"
      >
        <AppIcon name="chevronRight" :size="12" :class="{ rotated: expanded }" />
      </button>
    </header>

    <div class="exec-progress">
      <i :style="{ width: `${Math.max(4, progress)}%` }"></i>
    </div>

    <ul v-if="steps.length" class="step-list">
      <li v-for="step in steps" :key="step.id" class="step-item" :class="step.status">
        <span class="step-icon">
          <AppIcon :name="stepIcon[step.status] || 'clock'" :size="11" />
        </span>
        <span class="step-name">{{ step.name || step.id }}</span>
        <em v-if="step.branch" class="step-branch">{{ step.branch }}</em>
        <em class="step-state">{{ stepStatusLabel[step.status] || step.status }}</em>

        <!-- 运行中的步骤实时展示模型增量输出，避免长时间无反馈 -->
        <pre v-if="step.status === 'running' && step.output" class="live-output">{{ step.output }}</pre>

        <div v-if="expanded && tracesOf(step.id).length" class="traces">
          <div v-for="(trace, ti) in tracesOf(step.id)" :key="ti" class="trace">
            <p class="trace-meta">
              <span v-if="trace.branch">分支：{{ trace.branch }} · </span>
              <span>尝试 {{ trace.attempts }} 次 · {{ formatDuration(trace.duration_ms) }}</span>
              <span v-if="trace.state_source === 'user_provided'" class="user-provided">用户提供的上游状态</span>
            </p>
            <p v-if="traceOutput(trace)" class="trace-text">{{ traceOutput(trace) }}</p>
            <p v-if="trace.check_result" class="trace-text check">校验：{{ trace.check_result }}</p>
          </div>
        </div>
      </li>
    </ul>
  </article>
</template>

<style scoped>
.exec-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
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

.exec-head {
  display: flex;
  align-items: center;
  gap: 9px;
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

.exec-title small {
  display: block;
  margin-top: 1px;
  font-size: 10px;
  color: var(--text-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.exec-state {
  flex: none;
  font-size: 10px;
  color: var(--text-3);
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

.step-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.step-item {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 7px;
  font-size: 11px;
  color: var(--text-3);
}

.step-icon {
  display: grid;
  place-items: center;
  width: 18px;
  height: 18px;
  flex: none;
  border-radius: 50%;
  border: 1px solid var(--line);
  color: var(--text-3);
}

.step-item.running .step-icon {
  color: var(--accent-cyan);
  border-color: rgba(55, 208, 196, 0.5);
}

.step-item.done .step-icon {
  color: var(--green);
  border-color: rgba(55, 201, 164, 0.45);
}

.step-item.failed .step-icon {
  color: var(--danger);
  border-color: rgba(226, 96, 94, 0.45);
}

.step-item.degraded .step-icon {
  color: var(--amber);
  border-color: rgba(242, 176, 60, 0.45);
}

.step-item.running .step-name {
  color: var(--text-1);
}

.step-state {
  margin-left: auto;
  font-style: normal;
  font-size: 9.5px;
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
