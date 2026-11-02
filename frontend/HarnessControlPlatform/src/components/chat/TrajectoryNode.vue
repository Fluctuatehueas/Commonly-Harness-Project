<script setup lang="ts">
// 轨迹树节点：递归渲染推理 / 工具 / 委派及其子节点
import { computed, ref, watch } from 'vue'
import AppIcon from '@/components/console/AppIcon.vue'
import type { IconName } from '@/components/console/icons'
import { useDisplayStore } from '@/stores/display'
import type { TreeNode } from './trajectoryTypes'
import TrajectoryNode from './TrajectoryNode.vue'

const props = defineProps<{ node: TreeNode }>()
const display = useDisplayStore()

// 推理 / 子 Agent 输出在流式期间展开；工具节点在调用期间展开，
// 结束后折叠，用户可点击节点再查看完整参数与结果。
const open = ref(
  Boolean(props.node.msg.streaming) || props.node.msg.status === 'calling',
)
watch(
  () => [props.node.msg.streaming, props.node.msg.status] as const,
  ([streaming, status]) => {
    // 用户显式开启「参数默认展开」时，结束也不自动折叠。
    open.value = display.prefs.expandToolArgs
      ? true
      : Boolean(streaming) || status === 'calling'
  },
)

const msg = computed(() => props.node.msg)

const skill = computed(() => msg.value.kind === 'tool' && msg.value.category === 'skill')
const delegation = computed(
  () => msg.value.kind === 'tool' && msg.value.category === 'delegation',
)
const hasDetail = computed(
  () => Boolean(msg.value.content || msg.value.args || msg.value.result) && !skill.value,
)

const icon = computed<IconName>(() => {
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
  if (value.kind === 'reasoning') return '思考'
  if (value.kind === 'subagent') return '子智能体输出'
  if (value.kind === 'assistant') return '模型输出'
  if (value.kind === 'interrupt') {
    return value.interruptType === 'hitl_approval' ? '人工审批' : '人工介入'
  }
  if (skill.value) return `使用技能 ${value.toolName || 'skill'}`
  if (delegation.value) {
    return `委派 ${value.subagentName || value.toolName || '子任务'}`
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
</script>

<template>
  <div class="tnode" :class="[msg.kind, msg.category, msg.status]">
    <div class="tnode-row" :class="{ clickable: hasDetail }" @click="hasDetail && (open = !open)">
      <span class="rail-dot" :class="{ active: msg.streaming }">
        <AppIcon :name="icon" :size="11" />
      </span>
      <span class="tnode-label">{{ label }}</span>
      <span v-if="statusText" class="tnode-status" :class="msg.kind === 'interrupt' ? 'resolved' : msg.status">{{ statusText }}</span>
      <span v-if="atLabel" class="tnode-time">{{ atLabel }}</span>
      <AppIcon
        v-if="hasDetail"
        name="chevronRight"
        :size="10"
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

    <div v-if="node.children.length" class="tnode-children">
      <TrajectoryNode v-for="child in node.children" :key="child.msg.id" :node="child" />
    </div>
  </div>
</template>

<style scoped>
.tnode {
  position: relative;
  padding-left: 4px;
}

.tnode-row {
  display: flex;
  align-items: center;
  gap: 7px;
  min-height: 26px;
  padding: 2px 4px;
  border-radius: 6px;
  color: var(--text-3);
  font-size: 11.5px;
  transition: background 0.15s, color 0.15s;
}

.tnode-row.clickable {
  cursor: pointer;
}

.tnode-row.clickable:hover {
  color: var(--text-2);
  background: rgba(79, 140, 255, 0.06);
}

.rail-dot {
  display: grid;
  place-items: center;
  width: 18px;
  height: 18px;
  flex: none;
  border-radius: 50%;
  color: var(--text-3);
  background: rgba(79, 140, 255, 0.1);
}

.tnode.reasoning .rail-dot {
  color: #9fb4ff;
}

.tnode.delegation .rail-dot {
  color: #b3a4ff;
  background: rgba(143, 123, 255, 0.14);
}

.tnode.tool .rail-dot {
  color: var(--accent-cyan);
  background: rgba(53, 224, 255, 0.1);
}

.tnode.interrupt .rail-dot {
  color: #7fe0b0;
  background: rgba(72, 214, 140, 0.12);
}

.rail-dot.active {
  animation: node-pulse 1.3s ease-in-out infinite;
}

@keyframes node-pulse {
  0%, 100% { opacity: 0.55; }
  50% { opacity: 1; }
}

.tnode-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tnode-status {
  flex: none;
  font-size: 10px;
}

.tnode-status.calling {
  color: var(--accent-cyan);
}

.tnode-status.error {
  color: #ff7b7b;
}

.tnode-status.resolved {
  color: #58e0a0;
}

.tnode-time {
  flex: none;
  font-size: 9.5px;
  color: var(--text-3);
  font-variant-numeric: tabular-nums;
  opacity: 0.75;
}

.tnode-chev {
  flex: none;
  color: var(--text-3);
  transition: transform 0.18s;
}

.tnode-chev.open {
  transform: rotate(90deg);
}

.tnode-body {
  margin: 2px 0 4px 25px;
  padding: 7px 9px;
  border-left: 1px solid var(--line);
  background: rgba(6, 13, 28, 0.45);
  border-radius: 0 6px 6px 0;
}

.prose {
  font-size: 11.5px;
  line-height: 1.65;
  color: var(--text-2);
  white-space: pre-wrap;
  word-break: break-word;
}

.part + .part {
  margin-top: 6px;
}

.part small {
  display: block;
  margin-bottom: 3px;
  font-size: 9.5px;
  color: var(--text-3);
}

.part pre {
  margin: 0;
  padding: 6px 8px;
  max-height: 180px;
  overflow: auto;
  font-family: 'Cascadia Code', 'JetBrains Mono', Consolas, monospace;
  font-size: 10.5px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--text-2);
  border: 1px solid var(--line);
  border-radius: 6px;
  background: rgba(4, 10, 22, 0.7);
}

.part pre.result {
  color: #a9d3ff;
}

/* 子节点缩进 + 左侧竖线，形成树状结构 */
.tnode-children {
  position: relative;
  margin-left: 13px;
  padding-left: 10px;
  border-left: 1px solid var(--line);
}
</style>
