<script setup lang="ts">
// 任务规划卡片：展示调度器产出的两级计划条目。
// 执行中固定在滚动区顶部并逐条流式展开；结束/中断后归档到本轮轨迹底部。
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import AppIcon from '@/components/console/AppIcon.vue'
import type { SchedulerPlanItem } from '@/stores/chat'

const props = withDefaults(
  defineProps<{
    goal?: string
    tier?: string
    mode?: string
    status: 'running' | 'done' | 'failed' | 'paused'
    items?: SchedulerPlanItem[]
    planVersion?: number
    summary?: string
    note?: string
    /** 顶部常驻卡片：默认折叠，只显示标题与进度条。 */
    collapsible?: boolean
    /** 归档态：位于轨迹底部，直接展示全部条目。 */
    archived?: boolean
  }>(),
  {
    goal: '',
    tier: '',
    mode: '',
    items: () => [],
    planVersion: 1,
    summary: '',
    note: '',
    collapsible: false,
    archived: false,
  },
)

const open = ref(true)

function toggle() {
  if (!props.collapsible) return
  open.value = !open.value
}

const statusLabel: Record<string, string> = {
  running: '执行中',
  done: '已完成',
  failed: '失败',
  paused: '待介入',
}

const itemStatusLabel: Record<string, string> = {
  pending: '等待',
  running: '执行中',
  done: '完成',
  failed: '失败',
  skipped: '跳过',
  needs_hitl: '待确认',
}

const itemIcon: Record<string, 'clock' | 'play' | 'check' | 'close' | 'wrench'> = {
  pending: 'clock',
  running: 'play',
  done: 'check',
  failed: 'close',
  skipped: 'wrench',
  needs_hitl: 'wrench',
}

const doneCount = computed(
  () => props.items.filter((item) => item.status === 'done' || item.status === 'skipped').length,
)

const progress = computed(() => {
  if (!props.items.length) return 0
  return Math.round((doneCount.value / props.items.length) * 100)
})

const runningItem = computed(() => props.items.find((item) => item.status === 'running'))

const meta = computed(() => {
  const parts = [props.tier, props.mode].filter(Boolean)
  if (props.planVersion && props.planVersion > 1) parts.push(`v${props.planVersion}`)
  return parts.join(' · ')
})

// 逐条展开：计划一次到达时也按条目顺序出现，避免整块闪现。
const shownCount = ref(0)
let timer: number | null = null

function stopReveal() {
  if (timer !== null) {
    window.clearTimeout(timer)
    timer = null
  }
}

function revealTo(target: number) {
  stopReveal()
  if (props.archived) {
    shownCount.value = target
    return
  }
  const step = () => {
    if (shownCount.value >= target) {
      timer = null
      return
    }
    shownCount.value += 1
    timer = window.setTimeout(step, 55)
  }
  step()
}

watch(
  () => [props.items.length, props.archived] as const,
  ([length, archived]) => {
    if (archived) {
      stopReveal()
      shownCount.value = length
      return
    }
    if (length > shownCount.value) revealTo(length)
  },
  { immediate: true },
)

onBeforeUnmount(stopReveal)

const shownItems = computed(() => props.items.slice(0, shownCount.value))

function showPhase(index: number): boolean {
  if (index === 0) return Boolean(shownItems.value[0]?.phaseTitle)
  return shownItems.value[index]?.phaseId !== shownItems.value[index - 1]?.phaseId
}
</script>

<template>
  <article class="sched-card" :class="[status, { archived }]">
    <header class="sched-head" :class="{ clickable: collapsible }" @click="toggle">
      <span class="sched-icon">
        <AppIcon
          :name="status === 'done' ? 'check' : status === 'failed' ? 'close' : 'target'"
          :size="13"
        />
      </span>
      <div class="sched-title">
        <strong>任务规划</strong>
        <small v-if="status === 'running' && runningItem">
          正在执行：{{ runningItem.title }}
        </small>
        <small v-else-if="goal">{{ goal }}</small>
        <small v-else-if="meta">{{ meta }}</small>
      </div>
      <span class="sched-meta">{{ meta }}</span>
      <span class="sched-state">{{ statusLabel[status] || status }}</span>
      <button
        v-if="collapsible"
        class="expand-btn"
        type="button"
        :title="open ? '收起规划条目' : '展开规划条目'"
        @click.stop="toggle"
      >
        <AppIcon name="chevronRight" :size="12" :class="{ rotated: open }" />
      </button>
    </header>

    <div class="sched-progress">
      <i :style="{ width: `${Math.max(4, progress)}%` }"></i>
    </div>

    <ul v-if="items.length && open" class="plan-list">
      <template v-for="(item, index) in shownItems" :key="item.id">
        <li v-if="showPhase(index)" class="phase-row">
          <span class="phase-label">{{ item.phaseTitle || item.phaseId }}</span>
        </li>
        <li class="plan-item" :class="item.status">
          <span class="plan-name">
            <em class="plan-index">{{ index + 1 }}</em>
            <span class="plan-dash">—</span>
            <span class="plan-text">{{ item.title }}</span>
            <span v-if="item.status === 'running'" class="plan-cursor"></span>
          </span>
          <em v-if="item.detail" class="plan-detail" :title="item.detail">{{ item.detail }}</em>
          <span class="plan-state" :title="itemStatusLabel[item.status] || item.status">
            <AppIcon :name="itemIcon[item.status] || 'clock'" :size="13" />
          </span>
        </li>
      </template>
      <li v-if="shownCount < items.length" class="plan-item pending">
        <span class="plan-name">
          <em class="plan-index">{{ shownCount + 1 }}</em>
          <span class="plan-dash">—</span>
          <span class="plan-text typing">规划中…</span>
        </span>
      </li>
    </ul>

    <footer v-if="note || summary" class="sched-foot">
      <p v-if="note" class="sched-note">{{ note }}</p>
      <p v-if="summary" class="sched-summary">{{ summary }}</p>
    </footer>
  </article>
</template>

<style scoped>
.sched-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 14px;
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-md);
  background: var(--bg-elevated);
}

.sched-card.done {
  border-color: rgba(55, 208, 196, 0.35);
}

.sched-card.failed {
  border-color: rgba(226, 96, 94, 0.42);
}

.sched-card.paused {
  border-color: rgba(242, 176, 60, 0.42);
}

.sched-card.archived {
  margin-top: 4px;
  background: transparent;
}

.sched-head {
  display: flex;
  align-items: center;
  gap: 9px;
}

.sched-head.clickable {
  cursor: pointer;
}

.sched-icon {
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  flex: none;
  border-radius: var(--radius-sm);
  color: var(--accent-cyan);
  background: transparent;
}

.sched-card.done .sched-icon {
  color: var(--green);
  background: rgba(55, 201, 164, 0.12);
}

.sched-card.failed .sched-icon {
  color: var(--danger);
  background: rgba(226, 96, 94, 0.12);
}

.sched-card.paused .sched-icon {
  color: var(--amber);
  background: rgba(242, 176, 60, 0.12);
}

.sched-title {
  flex: 1;
  min-width: 0;
}

.sched-title strong {
  display: block;
  font-size: 12px;
  color: var(--text-1);
}

.sched-title small {
  display: block;
  margin-top: 1px;
  font-size: 10px;
  color: var(--text-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sched-meta,
.sched-state {
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

.sched-progress {
  height: 5px;
  overflow: hidden;
  border-radius: 2px;
  background: rgba(126, 154, 198, 0.14);
}

.sched-progress i {
  display: block;
  height: 100%;
  border-radius: 2px;
  background: linear-gradient(90deg, var(--accent-cyan), var(--accent));
  transition: width 0.35s ease;
}

.sched-card.failed .sched-progress i {
  background: var(--danger);
}

.plan-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  /* 条目较多时卡片自身滚动，避免顶部常驻卡片挤占对话区。 */
  max-height: 220px;
  overflow: auto;
}

.phase-row {
  margin-top: 4px;
  padding-left: 2px;
  font-size: 9.5px;
  letter-spacing: 0.04em;
  color: var(--text-3);
  text-transform: uppercase;
}

.plan-item {
  display: flex;
  align-items: center;
  gap: 7px;
  min-height: 20px;
  font-size: 11px;
  color: var(--text-3);
}

.plan-name {
  display: flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
  color: var(--text-2);
}

.plan-index {
  flex: none;
  min-width: 14px;
  font-family: var(--font-mono);
  font-style: normal;
  font-size: 10px;
  color: var(--text-3);
  text-align: right;
}

.plan-dash {
  flex: none;
  color: var(--line-strong);
}

.plan-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.plan-item.done .plan-name,
.plan-item.running .plan-name {
  color: var(--text-1);
}

.plan-item.running .plan-text {
  color: var(--accent-cyan);
}

.plan-cursor {
  width: 2px;
  height: 11px;
  flex: none;
  background: var(--accent-cyan);
  animation: plan-blink 0.9s step-end infinite;
}

@keyframes plan-blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}

.plan-detail {
  min-width: 0;
  max-width: 45%;
  margin-left: auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-style: normal;
  font-size: 9.5px;
  color: var(--text-3);
}

.plan-state {
  display: grid;
  place-items: center;
  flex: none;
  width: 18px;
  height: 18px;
  margin-left: 6px;
  border-radius: 50%;
  color: var(--text-3);
}

.plan-item.running .plan-state {
  color: var(--accent-cyan);
  border: 1px solid rgba(55, 208, 196, 0.5);
}

.plan-item.done .plan-state {
  color: var(--green);
  border: 1px solid rgba(55, 201, 164, 0.45);
}

.plan-item.failed .plan-state {
  color: var(--danger);
  border: 1px solid rgba(226, 96, 94, 0.45);
}

.plan-item.skipped .plan-state,
.plan-item.needs_hitl .plan-state {
  color: var(--amber);
  border: 1px solid rgba(242, 176, 60, 0.45);
}

.plan-text.typing {
  color: var(--text-3);
  animation: plan-typing 1.2s ease-in-out infinite;
}

@keyframes plan-typing {
  0%, 100% { opacity: 0.45; }
  50% { opacity: 0.9; }
}

.sched-foot {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding-top: 2px;
  border-top: 1px solid var(--line);
}

.sched-note,
.sched-summary {
  margin: 0;
  font-size: 10px;
  line-height: 1.5;
  color: var(--text-3);
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
}
</style>
