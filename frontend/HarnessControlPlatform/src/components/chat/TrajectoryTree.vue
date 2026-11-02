<script setup lang="ts">
// 会话内的单轮轨迹树：默认执行中展开，结束后自动折叠，只保留本轮耗时
import { computed, ref, watch } from 'vue'
import AppIcon from '@/components/console/AppIcon.vue'
import type { ChatMsg } from '@/stores/chat'
import TrajectoryNode from './TrajectoryNode.vue'
import type { TreeNode } from './trajectoryTypes'

const props = defineProps<{
  items: ChatMsg[]
  live: boolean
  durationMs?: number
}>()

// 执行中默认全部展开，对话结束后自动折叠。
const expanded = ref(props.live)
watch(
  () => props.live,
  (value) => {
    expanded.value = value
  },
)

const nodes = computed<TreeNode[]>(() => {
  const all: TreeNode[] = props.items.map((msg) => ({ msg, children: [] }))
  const byToolCallId = new Map<string, TreeNode>()
  for (const node of all) {
    if (node.msg.toolCallId) byToolCallId.set(node.msg.toolCallId, node)
  }
  const roots: TreeNode[] = []
  for (const node of all) {
    const parentId = node.msg.parentToolCallId
    const parent = parentId ? byToolCallId.get(parentId) : undefined
    if (parent) parent.children.push(node)
    else roots.push(node)
  }
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
  return value ? `本轮耗时 ${value}` : '本轮轨迹'
})

const stepLabel = computed(() => `${props.items.length} 步`)
</script>

<template>
  <div class="trajectory-tree" :class="{ live }">
    <button
      class="tt-head"
      type="button"
      :title="expanded ? '收起本轮轨迹' : '展开本轮轨迹'"
      @click="expanded = !expanded"
    >
      <span class="tt-icon" :class="{ running: live }">
        <AppIcon name="workflow" :size="12" />
      </span>
      <span class="tt-title">{{ headerLabel }}</span>
      <span v-if="live" class="tt-steps">{{ stepLabel }}</span>
      <span class="tt-dots" v-if="live">
        <i class="s-dot"></i><i class="s-dot"></i><i class="s-dot"></i>
      </span>
      <span class="tt-action">{{ expanded ? '收起轨迹' : '展开轨迹' }}</span>
      <AppIcon
        name="chevronRight"
        :size="11"
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
  border-left: 2px solid transparent;
  transition: border-color 0.2s;
}

.trajectory-tree.live {
  border-left-color: rgba(53, 224, 255, 0.35);
}

.tt-head {
  display: flex;
  align-items: center;
  gap: 7px;
  width: 100%;
  padding: 5px 8px;
  color: var(--text-3);
  font-size: 11.5px;
  text-align: left;
  border: none;
  border-radius: 7px;
  background: transparent;
  transition: color 0.16s, background 0.16s;
}

.tt-head:hover {
  color: var(--text-2);
  background: rgba(79, 140, 255, 0.06);
}

.tt-icon {
  display: grid;
  place-items: center;
  width: 20px;
  height: 20px;
  flex: none;
  border-radius: 6px;
  color: var(--text-3);
  background: rgba(79, 140, 255, 0.1);
}

.tt-icon.running {
  color: var(--accent-cyan);
  animation: tt-glow 1.4s ease-in-out infinite;
}

@keyframes tt-glow {
  0%, 100% { opacity: 0.6; }
  50% { opacity: 1; }
}

.tt-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tt-steps {
  flex: none;
  font-size: 10px;
  color: var(--text-3);
}

.tt-action {
  flex: none;
  padding: 1px 7px;
  font-size: 10px;
  color: var(--text-3);
  border: 1px solid var(--line);
  border-radius: 999px;
}

.tt-head:hover .tt-action {
  color: var(--accent-cyan);
  border-color: rgba(53, 224, 255, 0.4);
}

.tt-dots {
  display: inline-flex;
  gap: 3px;
  flex: none;
}

.s-dot {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--accent-cyan);
  animation: bounce 1.15s infinite;
}

.s-dot:nth-child(2) { animation-delay: 0.14s; }
.s-dot:nth-child(3) { animation-delay: 0.28s; }

@keyframes bounce {
  0%, 60%, 100% { transform: none; opacity: 0.45; }
  30% { transform: translateY(-3px); opacity: 1; }
}

.tt-chev {
  flex: none;
  color: var(--text-3);
  transition: transform 0.18s;
}

.tt-chev.open {
  transform: rotate(90deg);
}

.tt-body {
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding: 2px 4px 6px 6px;
}

.tt-empty {
  margin: 0;
  padding: 4px 10px;
  font-size: 11px;
  color: var(--text-3);
}
</style>
