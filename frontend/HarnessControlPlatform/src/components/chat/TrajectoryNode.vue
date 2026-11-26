<script setup lang="ts">
// 轨迹树节点：递归渲染推理 / 工具 / 委派及其子节点
import { computed, inject, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import AppIcon from '@/components/console/AppIcon.vue'
import type { IconName } from '@/components/console/icons'
import { useDisplayStore } from '@/stores/display'
import { useAgentStore } from '@/stores/agents'
import {
  TRAJECTORY_REGISTRY,
  type TrajectoryEntry,
  type TreeNode,
} from './trajectoryTypes'
import TrajectoryNode from './TrajectoryNode.vue'

const props = defineProps<{ node: TreeNode; agentName?: string; level?: number }>()
const display = useDisplayStore()
const agents = useAgentStore()

// 事件里只有 agent_id（如 agent_ecf7a6c2），展示层统一换成档案里的名字；
// 档案未加载或已删除时退回原始标识，避免显示为空。
function displayAgent(raw?: string): string {
  const value = String(raw || '').trim()
  if (!value || value === 'main' || value === 'subagent') return ''
  const found = agents.items.find((item) => item.id === value)
  return found?.name || value
}

// 递归深度：根节点为 L1，用于顶部粘性折叠条展示层级。
const level = computed(() => props.level ?? 1)
const registry = inject(TRAJECTORY_REGISTRY, null)

// 委派节点（SubAgent）默认折叠：子 Agent 的推理、工具与输出都收在
// 折叠行后面，行内只显示最近一步预览。其他节点同样默认折叠。
const isDelegationNode = computed(
  () => props.node.msg.kind === 'tool' && props.node.msg.category === 'delegation',
)
// 执行中不随流式自动展开，展开状态完全由用户点击决定。
const open = ref(false)

const msg = computed(() => props.node.msg)

// 子 Agent 实际名称：后端 source 可能是具体名字，也可能退化成 "subagent"，
// 后者从父节点继承，保证 UI 上始终显示具体成员名而不是原始标识。
const ownAgent = computed(() => {
  const source = String(msg.value.source || '')
  if (source && source !== 'main' && source !== 'subagent') return displayAgent(source)
  return displayAgent(msg.value.subagentName)
})
const effectiveAgent = computed(() => ownAgent.value || props.agentName || '')

const skill = computed(() => msg.value.kind === 'tool' && msg.value.category === 'skill')
const delegation = computed(
  () => msg.value.kind === 'tool' && msg.value.category === 'delegation',
)
const hasDetail = computed(
  () =>
    msg.value.kind !== 'marker' &&
    (props.node.children.length > 0 ||
      (Boolean(msg.value.content || msg.value.args || msg.value.result) && !skill.value)),
)

const icon = computed<IconName>(() => {
  if (msg.value.kind === 'marker') {
    return msg.value.markerType === 'taskflow' ? 'target' : 'sparkles'
  }
  if (msg.value.kind === 'reasoning') return 'sparkles'
  if (msg.value.kind === 'subagent') return 'bot'
  if (msg.value.kind === 'assistant') return 'message'
  if (msg.value.kind === 'interrupt') return 'shield'
  if (skill.value) return 'sparkles'
  if (delegation.value) return 'users'
  return 'wrench'
})

const label = computed(() => {
  const value = msg.value
  if (value.kind === 'marker') {
    return value.markerType === 'taskflow' ? '命中任务流' : '采用经验'
  }
  if (value.kind === 'reasoning') return '思考'
  if (value.kind === 'subagent') {
    return effectiveAgent.value
      ? `子智能体 ${effectiveAgent.value} 输出`
      : '子智能体输出'
  }
  if (value.kind === 'assistant') return '模型输出'
  if (value.kind === 'interrupt') {
    return value.interruptType === 'hitl_approval' ? '人工审批' : '人工介入'
  }
  if (skill.value) return `使用技能 ${value.toolName || 'skill'}`
  if (delegation.value) {
    // 委派节点必须点名"派给谁"，不能让用户看到原始的 task 工具名。
    return `委派 ${displayAgent(value.subagentName) || effectiveAgent.value || '子智能体'}`
  }
  return `调用工具 ${value.toolName || 'tool'}`
})

const statusText = computed(() => {
  if (msg.value.kind === 'interrupt' && msg.value.resolved) {
    if (msg.value.resolved === 'approved') return '已批准'
    if (msg.value.resolved === 'rejected') return '已拒绝'
    return '已回复'
  }
  if (msg.value.status === 'calling') return '执行中'
  if (msg.value.status === 'error') return '失败'
  return ''
})

const atLabel = computed(() => {
  if (!msg.value.at) return ''
  const date = new Date(msg.value.at)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
})

const markerItems = computed(() => msg.value.markerItems || [])

function oneLine(value?: string, limit = 48): string {
  const text = String(value || '')
    .replace(/\s+/g, ' ')
    .trim()
  if (!text) return ''
  return text.length > limit ? `${text.slice(0, limit)}…` : text
}

// 折叠状态下只展示最近一步：工具调用、思考或输出摘要，保持一行。
const collapsedPreview = computed(() => {
  if (!isDelegationNode.value || open.value) return ''
  const children = props.node.children.filter((child) => child.msg.kind !== 'marker')
  const last = children[children.length - 1]
  if (!last) return ''
  const prefix = `${children.length} 步 · 最近：`
  const child = last.msg
  if (child.kind === 'tool') {
    const action =
      child.category === 'delegation'
        ? `委派 ${displayAgent(child.subagentName) || '子智能体'}`
        : `调用 ${child.toolName || '工具'}`
    const detail = oneLine(child.result || child.args || child.content)
    return `${prefix}${detail ? `${action} · ${detail}` : action}`
  }
  if (child.kind === 'reasoning') {
    const detail = oneLine(child.content)
    return `${prefix}${detail ? `思考：${detail}` : '思考中'}`
  }
  const detail = oneLine(child.content)
  return detail ? `${prefix}输出：${detail}` : ''
})

function pretty(raw?: string) {
  if (!raw) return ''
  try {
    return JSON.stringify(JSON.parse(raw), null, 2)
  } catch {
    return raw
  }
}

// 工具参数与结果默认按 display.prefs.expandToolArgs 展开
watch(
  () => display.prefs.expandToolArgs,
  (value) => {
    if (value && hasDetail.value) {
      open.value = true
    } else if (!props.node.msg.streaming && props.node.msg.status !== 'calling') {
      open.value = false
    }
  },
)

// 登记到聊天页的轨迹索引：滚动时由顶部粘性折叠条反查当前节点并代理收起。
const entry: TrajectoryEntry = {
  level: () => level.value,
  label: () => label.value,
  collapsible: () => hasDetail.value,
  open: () => open.value,
  toggle: () => {
    if (hasDetail.value) open.value = !open.value
  },
}
onMounted(() => registry?.set(props.node.msg.id, entry))
onBeforeUnmount(() => registry?.delete(props.node.msg.id))
</script>

<template>
  <div class="tnode" :class="[msg.kind, msg.category, msg.status]">
    <div
      class="tnode-row"
      :class="{ clickable: hasDetail }"
      :data-traj-row="msg.id"
      :data-traj-level="level"
      @click="hasDetail && (open = !open)"
    >
      <AppIcon
        :name="icon"
        :size="12"
        class="tnode-icon"
        :class="{ active: msg.streaming }"
      />
      <span class="tnode-label">{{ label }}</span>
      <span v-if="effectiveAgent && !delegation && msg.kind !== 'subagent'" class="tnode-agent">
        {{ effectiveAgent }}
      </span>
      <span v-if="statusText" class="tnode-status" :class="msg.kind === 'interrupt' ? 'resolved' : msg.status">{{ statusText }}</span>
      <!-- 经验 / 任务流标记：直接在本行渲染命中标签 -->
      <span v-if="msg.kind === 'marker'" class="tnode-marker-chips">
        <span
          v-for="(item, index) in markerItems"
          :key="`${item.id}-${index}`"
          class="marker-chip"
          :title="item.rule || ''"
        >
          【根据{{ item.name }}经验】
        </span>
        <span v-if="msg.markerType === 'taskflow'" class="marker-chip flow">
          {{ msg.content }}
        </span>
      </span>
      <!-- 委派节点折叠时：只显示一行最近步骤预览 -->
      <span v-else-if="collapsedPreview" class="tnode-preview">{{ collapsedPreview }}</span>
      <span v-if="atLabel" class="tnode-time">{{ atLabel }}</span>
      <AppIcon
        v-if="hasDetail"
        name="chevronRight"
        :size="11"
        class="tnode-chev"
        :class="{ open }"
      />
    </div>

    <div v-if="hasDetail && open" class="tnode-body">
      <template v-if="msg.kind === 'reasoning' || msg.kind === 'subagent' || msg.kind === 'assistant' || msg.kind === 'interrupt'">
        <div class="prose">{{ msg.content }}</div>
      </template>
      <template v-else>
        <div v-if="msg.args" class="part">
          <small>参数</small>
          <pre>{{ pretty(msg.args) }}</pre>
        </div>
        <div v-if="msg.result" class="part">
          <small>返回结果</small>
          <pre class="result">{{ msg.result }}</pre>
        </div>
      </template>
    </div>

    <!-- 子 Agent 轨迹跟随委派节点一起折叠，只有用户展开后才渲染 -->
    <div v-if="open && node.children.length" class="tnode-children">
      <TrajectoryNode
        v-for="child in node.children"
        :key="child.msg.id"
        :node="child"
        :agent-name="effectiveAgent || undefined"
        :level="level + 1"
      />
    </div>
  </div>
</template>

<style scoped>
.tnode {
  position: relative;
}

/* Claude 风格：无彩色徽章的单行文本 + 右侧箭头 */
.tnode-row {
  display: flex;
  align-items: center;
  gap: 7px;
  min-height: 27px;
  padding: 2px 4px;
  border-radius: 6px;
  color: var(--text-2);
  font-size: 12px;
  line-height: 1.6;
  transition: background 0.15s, color 0.15s;
}

.tnode-row.clickable {
  cursor: pointer;
}

.tnode-row.clickable:hover {
  color: var(--text-1);
  background: rgba(255, 255, 255, 0.04);
}

.tnode-icon {
  flex: none;
  color: var(--text-3);
}

.tnode-icon.active {
  color: var(--accent-cyan);
  animation: node-pulse 1.3s ease-in-out infinite;
}

@keyframes node-pulse {
  0%, 100% { opacity: 0.5; }
  50% { opacity: 1; }
}

.tnode-label {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: inherit;
}

.tnode-status {
  flex: none;
  font-size: 11px;
  color: var(--text-3);
}

/* 子 Agent 名称：弱化为纯文字，不使用彩色胶囊 */
.tnode-agent {
  flex: none;
  font-size: 11px;
  color: var(--text-3);
  white-space: nowrap;
}

.tnode-agent::before {
  content: '· ';
  opacity: 0.6;
}

/* 折叠委派节点的一行过程预览 */
.tnode-preview {
  flex: 1 1 0;
  min-width: 0;
  overflow: hidden;
  font-size: 11.5px;
  color: var(--text-3);
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 经验 / 任务流命中标记：仅过程渲染，不进入轨迹存储 */
.tnode-marker-chips {
  display: flex;
  flex: 1;
  flex-wrap: wrap;
  gap: 4px;
  min-width: 0;
}

.marker-chip {
  font-size: 11.5px;
  line-height: 1.5;
  color: var(--text-3);
  white-space: nowrap;
}

.marker-chip.flow {
  color: var(--text-2);
}

.tnode-status.calling {
  color: var(--accent-cyan);
}

.tnode-status.error {
  color: #ff7b7b;
}

.tnode-status.resolved {
  color: var(--text-2);
}

.tnode-time {
  flex: none;
  font-size: 10px;
  color: var(--text-3);
  font-variant-numeric: tabular-nums;
  opacity: 0.7;
}

.tnode-chev {
  flex: none;
  color: var(--text-3);
  opacity: 0.75;
  transition: transform 0.18s;
}

.tnode-chev.open {
  transform: rotate(90deg);
}

/* 展开内容：仅一条细竖线 + 缩进，不加底色 */
.tnode-body {
  margin: 0 0 4px 23px;
  padding: 4px 0 4px 12px;
  border-left: 1px solid var(--line);
}

.prose {
  font-size: 12px;
  line-height: 1.75;
  color: var(--text-2);
  white-space: pre-wrap;
  word-break: break-word;
}

.part + .part {
  margin-top: 8px;
}

.part small {
  display: block;
  margin-bottom: 4px;
  font-size: 11px;
  color: var(--text-3);
}

.part pre {
  margin: 0;
  padding: 8px 10px;
  max-height: 220px;
  overflow: auto;
  font-family: 'Cascadia Code', 'JetBrains Mono', Consolas, monospace;
  font-size: 11px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--text-2);
  border: 0;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.045);
}

.part pre.result {
  color: var(--text-2);
}

/* 子节点：缩进 + 细竖线，层级靠线与留白表达 */
.tnode-children {
  margin-left: 23px;
  padding-left: 12px;
  border-left: 1px solid var(--line);
}
</style>
