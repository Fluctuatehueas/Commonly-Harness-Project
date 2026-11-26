<script setup lang="ts">
// 会话内的单轮轨迹树：默认折叠，执行中也需用户手动展开
import { computed, ref } from 'vue'
import AppIcon from '@/components/console/AppIcon.vue'
import type { ChatMsg } from '@/stores/chat'
import TrajectoryNode from './TrajectoryNode.vue'
import type { TreeNode } from './trajectoryTypes'

const props = defineProps<{
  items: ChatMsg[]
  live: boolean
  durationMs?: number
}>()

// 所有轨迹默认不展开：执行中不自动展开，也不随对话结束自动折叠。
const expanded = ref(false)

const nodes = computed<TreeNode[]>(() => {
  const all: TreeNode[] = props.items.map((msg) => ({ msg, children: [] }))
  const byToolCallId = new Map<string, TreeNode>()
  for (const node of all) {
    if (node.msg.toolCallId) byToolCallId.set(node.msg.toolCallId, node)
  }

  const roots: TreeNode[] = []
  // 记录当前已经出现过的委派节点，用于给缺失 parent 的子 Agent 事件补层级。
  const delegations: TreeNode[] = []

  for (const node of all) {
    const msg = node.msg
    const isDelegation = msg.kind === 'tool' && msg.category === 'delegation'
    const explicitParent = msg.parentToolCallId
      ? byToolCallId.get(msg.parentToolCallId)
      : undefined

    if (explicitParent && explicitParent !== node) {
      explicitParent.children.push(node)
    } else if (isDelegation) {
      roots.push(node)
    } else if (msg.source && msg.source !== 'main' && msg.kind !== 'user') {
      // 后端偶尔拿不到 parent_tool_call_id（并发委派、事件顺序错位）：
      // 按子 Agent 名称找最近的委派节点，找不到就挂到最近的委派节点下，
      // 避免子 Agent 的工具和输出全部平铺在根层。
      const sameAgent = [...delegations]
        .reverse()
        .find((item) => item.msg.subagentName === msg.source)
      const target = sameAgent || delegations[delegations.length - 1]
      if (target) target.children.push(node)
      else roots.push(node)
    } else {
      roots.push(node)
    }

    if (isDelegation) delegations.push(node)
  }

  // 到达时间排序：同一节点下的子项保持真实时间顺序。
  const sortChildren = (list: TreeNode[]) => {
    list.sort((a, b) => (a.msg.at || 0) - (b.msg.at || 0))
    for (const item of list) sortChildren(item.children)
  }
  sortChildren(roots)
  return roots
})

function formatDuration(ms?: number): string {
  if (ms === undefined || ms === null || ms < 0) return ''
  if (ms < 1000) return `${ms}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)}s`
  const minutes = Math.floor(ms / 60_000)
  const seconds = Math.round((ms % 60_000) / 1000)
  return `${minutes}m${seconds}s`
}

const headerLabel = computed(() => {
  if (props.live) return '正在执行'
  const value = formatDuration(props.durationMs)
  return value ? `本轮轨迹 · ${value}` : '本轮轨迹'
})

// 经验 / 任务流标记只是过程渲染，不计入轨迹步数。
const stepLabel = computed(
  () => `${props.items.filter((item) => item.kind !== 'marker').length} 步`,
)
</script>

<template>
  <div class="trajectory-tree" :class="{ live }">
    <button
      class="tt-head"
      type="button"
      :title="expanded ? '收起本轮轨迹' : '展开本轮轨迹'"
      @click="expanded = !expanded"
    >
      <span class="tt-title">{{ headerLabel }}</span>
      <template v-if="live">
        <span class="tt-steps">{{ stepLabel }}</span>
        <span class="tt-dots"><i class="s-dot"></i><i class="s-dot"></i><i class="s-dot"></i></span>
      </template>
      <AppIcon
        name="chevronRight"
        :size="12"
        class="tt-chev"
        :class="{ open: expanded }"
      />
    </button>

    <div v-if="expanded" class="tt-body">
      <TrajectoryNode v-for="node in nodes" :key="node.msg.id" :node="node" />
      <p v-if="nodes.length === 0" class="tt-empty">等待模型输出…</p>
    </div>
  </div>
</template>

<style scoped>
.trajectory-tree {
  width: 100%;
}

/* Claude 风格：一行弱化的过程摘要，箭头紧随文字 */
.tt-head {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  max-width: 100%;
  padding: 3px 6px 3px 2px;
  color: var(--text-3);
  font-size: 12px;
  line-height: 1.55;
  text-align: left;
  border: none;
  border-radius: 6px;
  background: transparent;
  transition: color 0.16s;
}

.tt-head:hover {
  color: var(--text-2);
}

.tt-title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tt-steps {
  flex: none;
  font-size: 11px;
  color: var(--text-3);
  opacity: 0.85;
}

.tt-dots {
  display: inline-flex;
  gap: 3px;
  flex: none;
}

.s-dot {
  width: 3px;
  height: 3px;
  border-radius: 50%;
  background: currentColor;
  animation: bounce 1.15s infinite;
}

.s-dot:nth-child(2) { animation-delay: 0.14s; }
.s-dot:nth-child(3) { animation-delay: 0.28s; }

@keyframes bounce {
  0%, 60%, 100% { transform: none; opacity: 0.4; }
  30% { transform: translateY(-3px); opacity: 1; }
}

.tt-chev {
  flex: none;
  color: currentColor;
  opacity: 0.7;
  transition: transform 0.18s;
}

.tt-chev.open {
  transform: rotate(90deg);
}

.tt-body {
  display: flex;
  flex-direction: column;
  padding: 1px 0 4px;
}

.tt-empty {
  margin: 0;
  padding: 3px 0 3px 2px;
  font-size: 11.5px;
  color: var(--text-3);
}
</style>
