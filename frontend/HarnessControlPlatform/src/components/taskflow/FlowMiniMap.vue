<script setup lang="ts">
// 任务流简图：步骤圆点自上而下，fork 步骤左右分叉、子流仍自上而下。
// 列表页作缩略图（compact）；详情页可交互：单击选中、右键菜单、底部 + 追加步骤。
// 空态提供「新增第一个步骤」入口 —— 没有节点时也能建流程。
import { computed, ref } from 'vue'
import { Plus } from '@element-plus/icons-vue'
import type { StepDef } from '@/api/taskflow'
import type { NodeMenuPayload } from './types'

const props = withDefaults(
  defineProps<{
    steps: StepDef[]
    selectedId?: string
    /** 422 校验错误：step_id → 提示文案 */
    errors?: Record<string, string>
    compact?: boolean
    readonly?: boolean
  }>(),
  { selectedId: '', errors: () => ({}), compact: false, readonly: false },
)

const emit = defineEmits<{
  select: [id: string]
  menu: [payload: NodeMenuPayload]
  add: []
  /** 在指定子分支末尾追加步骤 */
  addSub: [payload: { stepId: string; branchIndex: number }]
}>()

function brief(text: string): string {
  const clean = (text || '').trim()
  return clean.length > 48 ? `${clean.slice(0, 48)}…` : clean || '暂无指令描述'
}

function onSelect(id: string) {
  if (props.compact) return
  emit('select', id)
}

function onMenu(event: MouseEvent, step: StepDef, branch: string | null) {
  if (props.compact || props.readonly) return
  emit('menu', {
    id: step.id,
    x: event.clientX,
    y: event.clientY,
    branch,
    isFork: !!step.parallel?.length,
  })
}

function onAdd() {
  if (props.compact || props.readonly) return
  emit('add')
}

function onAddSub(step: StepDef, branchIndex: number) {
  if (props.compact || props.readonly) return
  emit('addSub', { stepId: step.id, branchIndex })
}

// 子分支独立定位：左右交错铺开，避免压住主线的圆点。
function branchStyle(index: number) {
  const gap = props.compact ? 52 : 84
  const ring = Math.floor(index / 2) + 1
  const direction = index % 2 === 0 ? 1 : -1
  return {
    left: `calc(50% + ${direction * ring * gap}px)`,
    transform: 'translateX(-50%)',
  }
}

// fork 采用零高度覆盖层，不再把后续主分支节点推下去；
// 这里补一段空白高度，保证最长的分支也能完整滚动到。
const forkSpacer = computed(() => {
  if (props.compact) return 0
  let longest = 0
  for (const step of props.steps) {
    for (const branch of step.parallel || []) {
      const nodeHeight = 34 + 12
      const addHeight = props.readonly ? 0 : 32
      longest = Math.max(longest, 24 + branch.steps.length * nodeHeight + addHeight)
    }
  }
  return Math.max(0, longest - 48)
})

// ---------- 步骤信息卡 ----------
// 简图外层是 overflow:auto 的滚动容器，绝对定位的卡片会被裁剪。
// 因此用 Teleport 挂到 body + fixed 定位，按圆点位置计算坐标。
interface HoverCard {
  step: StepDef
  branch: string | null
  x: number
  y: number
}

const hoverCard = ref<HoverCard | null>(null)
let hideTimer: number | undefined

function showCard(event: MouseEvent, step: StepDef, branch: string | null) {
  if (props.compact) return
  if (hideTimer) window.clearTimeout(hideTimer)
  const host = event.currentTarget as HTMLElement | null
  const anchor = (host?.querySelector('.dot') as HTMLElement | null) ?? host
  const rect = anchor?.getBoundingClientRect()
  if (!rect) return
  hoverCard.value = {
    step,
    branch,
    x: rect.right + 12,
    y: rect.top + rect.height / 2,
  }
}

function hideCard() {
  if (hideTimer) window.clearTimeout(hideTimer)
  // 留一点缓冲，避免鼠标在圆点边缘抖动导致闪烁
  hideTimer = window.setTimeout(() => (hoverCard.value = null), 60)
}

// 超出视口右侧时翻到圆点左边
const popupStyle = computed(() => {
  const card = hoverCard.value
  if (!card) return {}
  const width = 232
  const flip = card.x + width > window.innerWidth - 8
  return {
    left: `${flip ? card.x - width - 24 : card.x}px`,
    top: `${card.y}px`,
  }
})
</script>

<template>
  <div class="mini-map" :class="{ compact }">
    <template v-for="(step, i) in steps" :key="step.id">
      <div
        class="node-wrap main"
        :class="{ selected: step.id === selectedId, invalid: !!errors[step.id] }"
        @click="onSelect(step.id)"
        @mouseenter="showCard($event, step, null)"
        @mouseleave="hideCard"
        @contextmenu.prevent="onMenu($event, step, null)"
      >
        <span class="dot">
          {{ i + 1 }}
          <i v-if="step.checkpoint && !compact" class="cp-mark" title="校验点" />
          <i v-if="step.parallel?.length && !compact" class="fork-mark" title="并行子流" />
        </span>
        <p v-if="!compact && errors[step.id]" class="err-note">{{ errors[step.id] }}</p>
      </div>

      <div v-if="step.parallel?.length" class="fork">
        <div
          v-for="(branch, bi) in step.parallel"
          :key="`${step.id}-${bi}`"
          class="branch"
          :style="branchStyle(bi)"
        >
          <span class="branch-name">{{ branch.name || '未命名分支' }}</span>
          <div
            v-for="(sub, j) in branch.steps"
            :key="sub.id"
            class="node-wrap sub"
            :class="{ selected: sub.id === selectedId, invalid: !!errors[sub.id] }"
            @click="onSelect(sub.id)"
            @mouseenter="showCard($event, sub, branch.name)"
            @mouseleave="hideCard"
            @contextmenu.prevent="onMenu($event, sub, branch.name)"
          >
            <span class="dot">{{ i + 1 }}.{{ j + 1 }}</span>
            <p v-if="!compact && errors[sub.id]" class="err-note">{{ errors[sub.id] }}</p>
          </div>
          <!-- 子分支末尾快速加步 -->
          <button
            v-if="!compact && !readonly"
            class="branch-add"
            type="button"
            :title="`在「${branch.name || '未命名分支'}」末尾新增步骤`"
            @click.stop="onAddSub(step, bi)"
          >
            <el-icon><Plus /></el-icon>
          </button>
        </div>
      </div>
    </template>

    <!-- fork 覆盖层不占主流程高度，用空白占位保证滚动区能容纳最长分支 -->
    <div v-if="forkSpacer > 0" class="fork-spacer" :style="{ height: `${forkSpacer}px` }" />

    <!-- 空态：直接给可点的建流程入口 -->
    <div v-if="!steps.length && !compact" class="empty-state">
      <div class="empty-orb">
        <el-icon><Plus /></el-icon>
      </div>
      <p class="empty-title">还没有步骤</p>
      <p class="empty-desc">任务流由一个个步骤串起来，先建第一个</p>
      <el-button v-if="!readonly" type="primary" :icon="Plus" @click="onAdd">
        新增第一个步骤
      </el-button>
      <el-tag v-else type="info" effect="plain">只读流程</el-tag>
    </div>

    <!-- 追加节点：始终可点，不依赖右键 -->
    <button
      v-else-if="!compact && !readonly"
      class="append-node"
      type="button"
      title="在末尾新增步骤"
      @click="onAdd"
    >
      <el-icon><Plus /></el-icon>
    </button>
  </div>

  <!-- 步骤信息卡：Teleport 到 body + fixed 定位，彻底避开滚动容器裁剪 -->
  <Teleport to="body">
    <Transition name="node-card-fade">
      <div v-if="hoverCard" class="node-popup" :style="popupStyle">
        <div class="np-head">
          <strong>{{ hoverCard.step.name || '未命名步骤' }}</strong>
          <span v-if="hoverCard.step.checkpoint" class="np-badge">校验点</span>
        </div>
        <p class="np-desc">{{ brief(hoverCard.step.instruction) }}</p>
        <div class="np-meta">
          <span v-if="hoverCard.branch">并行分支 · {{ hoverCard.branch }}</span>
          <span v-if="hoverCard.step.parallel?.length">
            {{ hoverCard.step.parallel.length }} 条并行子流
          </span>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.mini-map {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
  padding: 16px 12px 20px;
}

/* 主流纵向连接线 */
.mini-map::before {
  content: '';
  position: absolute;
  top: 24px;
  bottom: 24px;
  left: 50%;
  width: 1px;
  background: var(--line);
}

.node-wrap {
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  cursor: pointer;
}

.mini-map.compact .node-wrap {
  cursor: default;
}

.dot {
  position: relative;
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  font-family: var(--font-mono);
  font-size: 11.5px;
  font-weight: 600;
  border-radius: 50%;
  border: 1px solid rgba(55, 208, 196, 0.5);
  color: var(--accent-cyan);
  background: var(--bg-deep);
  transition:
    background 0.16s,
    border-color 0.16s,
    box-shadow 0.16s;
}

.node-wrap.main .dot {
  border-color: rgba(55, 208, 196, 0.5);
  color: var(--accent-cyan);
}

/* 子流节点用紫色与主流区分 */
.node-wrap.sub .dot {
  border-color: rgba(143, 123, 255, 0.5);
  color: #a99bff;
}

.node-wrap:hover .dot {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba(91, 140, 255, 0.12);
}

.node-wrap.selected .dot {
  color: #fff;
  border-color: var(--accent);
  background: var(--accent);
  box-shadow: 0 0 0 4px rgba(91, 140, 255, 0.18);
}

.node-wrap.invalid .dot {
  color: var(--danger);
  border-color: var(--danger);
}

.node-wrap.invalid.selected .dot {
  color: #fff;
  background: var(--danger);
}

/* 校验点角标 */
.cp-mark {
  position: absolute;
  top: -2px;
  right: -2px;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  border: 1px solid var(--bg-deep);
  background: var(--green);
}

/* 并行角标 */
.fork-mark {
  position: absolute;
  bottom: -2px;
  right: -2px;
  width: 8px;
  height: 8px;
  border-radius: 2px;
  border: 1px solid var(--bg-deep);
  background: var(--amber);
}

.err-note {
  position: absolute;
  top: calc(100% + 4px);
  left: 50%;
  transform: translateX(-50%);
  margin: 0;
  width: max-content;
  max-width: 220px;
  padding: 3px 8px;
  font-size: 10px;
  line-height: 1.5;
  color: var(--danger);
  border: 1px solid rgba(226, 96, 94, 0.45);
  border-radius: var(--radius-sm);
  background: var(--bg-elevated);
  z-index: 3;
}

/* fork：零高度覆盖层，子分支独立定位，不再把主分支节点推下去 */
.fork {
  position: relative;
  z-index: 2;
  width: 100%;
  height: 0;
}

.fork::before {
  content: '';
  position: absolute;
  top: 0;
  left: 8%;
  right: 8%;
  height: 1px;
  background: var(--line-strong);
}

.branch {
  position: absolute;
  top: 8px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  width: max-content;
  min-width: 66px;
}

.branch::before {
  content: '';
  position: absolute;
  top: -8px;
  left: 50%;
  width: 1px;
  height: 8px;
  background: var(--line-strong);
}

.branch-name {
  padding: 1px 8px;
  font-size: 9.5px;
  color: #a99bff;
  border: 1px solid rgba(143, 123, 255, 0.4);
  border-radius: var(--radius-sm);
  background: var(--bg-deep);
}

/* 子分支末尾快速加步 */
.branch-add {
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  color: #a99bff;
  border: 1px dashed rgba(143, 123, 255, 0.5);
  border-radius: 50%;
  background: transparent;
  transition:
    color 0.16s,
    border-color 0.16s,
    background 0.16s;
}

.branch-add:hover {
  color: #fff;
  border-color: var(--accent);
  background: rgba(143, 123, 255, 0.14);
}

.fork-spacer {
  width: 100%;
  pointer-events: none;
}

/* 空态 */
.empty-state {
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 28px 8px;
  text-align: center;
}

.empty-orb {
  display: grid;
  place-items: center;
  width: 48px;
  height: 48px;
  margin-bottom: 2px;
  font-size: 20px;
  color: var(--accent);
  border: 1px solid rgba(91, 140, 255, 0.35);
  border-radius: 50%;
  background: rgba(91, 140, 255, 0.07);
}

.empty-title {
  margin: 0;
  font-size: 13px;
  color: var(--text-1);
}

.empty-desc {
  margin: 0 0 6px;
  font-size: 11px;
  color: var(--text-3);
}

/* 末尾追加节点 */
.append-node {
  position: relative;
  z-index: 1;
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  color: var(--text-3);
  border: 1px dashed var(--line-strong);
  border-radius: 50%;
  background: transparent;
  transition:
    color 0.16s,
    border-color 0.16s,
    background 0.16s;
}

.append-node:hover {
  color: var(--accent);
  border-color: var(--accent);
  background: rgba(91, 140, 255, 0.08);
}

/* 列表页缩略图模式 */
.mini-map.compact {
  gap: 7px;
  padding: 8px 6px;
}

.mini-map.compact .dot {
  width: 18px;
  height: 18px;
  font-size: 8.5px;
}

.mini-map.compact .fork {
  height: auto;
  display: flex;
  justify-content: space-evenly;
  align-items: flex-start;
  gap: 10px;
  padding-top: 8px;
}

.mini-map.compact .fork::before {
  left: 14%;
  right: 14%;
}

.mini-map.compact .branch {
  position: static;
  top: auto;
  left: auto !important;
  width: auto;
  min-width: 0;
  transform: none !important;
  gap: 6px;
}

.mini-map.compact .branch::before {
  top: -8px;
  height: 8px;
}

.mini-map.compact .branch-name {
  display: none;
}
</style>

<!-- 信息卡被 Teleport 到 body，scoped 样式作用不到，这里用全局样式 -->
<style>
.node-popup {
  position: fixed;
  z-index: 3000;
  width: 232px;
  padding: 9px 11px;
  transform: translateY(-50%);
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-md);
  background: var(--bg-elevated);
  box-shadow: 0 14px 32px rgba(2, 6, 14, 0.62);
  pointer-events: none;
}

.node-popup .np-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 4px;
}

.node-popup .np-head strong {
  font-size: 11.5px;
  color: var(--text-1);
}

.node-popup .np-badge {
  flex: none;
  padding: 0 5px;
  font-size: 9px;
  line-height: 15px;
  color: var(--green);
  border: 1px solid rgba(55, 201, 164, 0.35);
  border-radius: var(--radius-sm);
}

.node-popup .np-desc {
  margin: 0;
  font-size: 10.5px;
  line-height: 1.55;
  color: var(--text-3);
  word-break: break-word;
}

.node-popup .np-meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-top: 5px;
  font-size: 10px;
  color: var(--amber);
}

.node-card-fade-enter-active,
.node-card-fade-leave-active {
  transition: opacity 0.12s ease;
}

.node-card-fade-enter-from,
.node-card-fade-leave-to {
  opacity: 0;
}
</style>
