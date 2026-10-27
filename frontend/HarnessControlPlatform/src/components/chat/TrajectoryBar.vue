<script setup lang="ts">
// 对话页折叠条：把工具 / 技能 / 委派 / 中间思考收敛成一行「轨迹」
import { computed } from 'vue'
import AppIcon from '@/components/console/AppIcon.vue'
import type { ChatMsg } from '@/stores/chat'

const props = defineProps<{
  items: ChatMsg[]
  threadId?: string | null
}>()

const emit = defineEmits<{ open: [threadId: string | null] }>()

function shorten(text: string, limit = 52) {
  const value = text.replace(/\s+/g, ' ').trim()
  return value.length > limit ? `${value.slice(0, limit)}…` : value
}

const latestLabel = computed(() => {
  const item = props.items[props.items.length - 1]
  if (!item) return '等待过程记录'
  if (item.kind === 'assistant') {
    return item.content ? `思考：${shorten(item.content)}` : '模型思考中'
  }
  const name = item.toolName || 'tool'
  if (item.category === 'skill') return `使用技能 ${name}`
  if (item.category === 'delegation') {
    return `委派子任务 ${item.subagentName || name}`
  }
  if (item.status === 'calling') return `正在调用 ${name}`
  if (item.status === 'error') return `${name} 执行失败`
  return `调用工具 ${name}`
})

const running = computed(() =>
  props.items.some((item) => item.status === 'calling' || item.kind === 'assistant'),
)

const stepLabel = computed(() => `${props.items.length} 步过程`)
</script>

<template>
  <button
    class="trajectory-bar"
    type="button"
    :title="threadId ? '打开轨迹页查看完整过程' : '发送消息后可查看轨迹'"
    @click="emit('open', threadId || null)"
  >
    <span class="tb-icon" :class="{ running }">
      <AppIcon name="workflow" :size="13" />
    </span>
    <span class="tb-tag">轨迹</span>
    <span class="tb-text">{{ latestLabel }}</span>
    <span class="tb-meta">{{ stepLabel }}</span>
    <span class="tb-open">查看<AppIcon name="chevronRight" :size="11" /></span>
  </button>
</template>

<style scoped>
/* 透明背景：过程信息不抢正式回答的视觉层级 */
.trajectory-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 7px 10px;
  text-align: left;
  color: var(--text-3);
  border: 1px dashed var(--line);
  border-radius: 9px;
  background: transparent;
  transition: all 0.18s;
}

.trajectory-bar:hover {
  color: var(--text-2);
  border-color: var(--line-strong);
  background: rgba(53, 224, 255, 0.035);
}

.tb-icon {
  display: grid;
  place-items: center;
  width: 22px;
  height: 22px;
  flex: none;
  border-radius: 6px;
  color: var(--text-3);
  background: rgba(79, 140, 255, 0.1);
}

.tb-icon.running {
  color: var(--accent-cyan);
  box-shadow: 0 0 10px rgba(53, 224, 255, 0.25);
  animation: tb-pulse 1.4s ease-in-out infinite;
}

@keyframes tb-pulse {
  0%, 100% { opacity: 0.65; }
  50% { opacity: 1; }
}

.tb-tag {
  flex: none;
  padding: 1px 6px;
  font-size: 10px;
  letter-spacing: 0.4px;
  color: var(--accent-cyan);
  border: 1px solid rgba(53, 224, 255, 0.25);
  border-radius: 999px;
}

.tb-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  font-size: 11.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tb-meta {
  flex: none;
  font-size: 10.5px;
  color: var(--text-3);
}

.tb-open {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  flex: none;
  font-size: 10.5px;
  color: var(--text-3);
}

.trajectory-bar:hover .tb-open {
  color: var(--accent-cyan);
}
</style>
