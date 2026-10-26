<script setup lang="ts">
// 工具/技能/委派 调用卡片
import { computed, ref, watch } from 'vue'
import AppIcon from '@/components/console/AppIcon.vue'
import type { ChatMsg } from '@/stores/chat'
import { useDisplayStore } from '@/stores/display'

const props = defineProps<{ msg: ChatMsg }>()
const display = useDisplayStore()

const expanded = ref(props.msg.status === 'error')
watch(
  () => props.msg.status,
  (status) => {
    if (status === 'error') expanded.value = true
  },
)
watch(
  () => display.prefs.expandToolArgs,
  (value) => (expanded.value = value),
  { immediate: true },
)

const categoryLabel = computed(
  () => ({ tool: '工具', skill: '技能', delegation: '委派' })[props.msg.category || 'tool'],
)
const categoryIcon = computed(
  () => ({ tool: 'wrench', skill: 'sparkles', delegation: 'bot' })[props.msg.category || 'tool'] as 'wrench' | 'sparkles' | 'bot',
)
const statusLabel = computed(
  () => ({ calling: '调用中', done: '完成', error: '失败' })[props.msg.status || 'calling'],
)

function pretty(raw?: string) {
  if (!raw) return ''
  try {
    return JSON.stringify(JSON.parse(raw), null, 2)
  } catch {
    return raw
  }
}
</script>

<template>
  <div class="tool-card" :class="[props.msg.category, props.msg.status]">
    <button class="head" type="button" @click="expanded = !expanded">
      <span class="cat-icon"><AppIcon :name="categoryIcon" :size="13" /></span>
      <span class="badge">{{ categoryLabel }}</span>
      <code class="name">{{ props.msg.toolName }}</code>
      <span v-if="props.msg.subagentName" class="subagent">→ {{ props.msg.subagentName }}</span>
      <span class="status" :class="props.msg.status">
        <i class="dot"></i>{{ statusLabel }}
      </span>
      <AppIcon :name="expanded ? 'close' : 'chevronRight'" :size="12" class="chev" :style="expanded ? 'transform: rotate(90deg)' : ''" />
    </button>

    <div v-if="expanded" class="body">
      <template v-if="props.msg.args">
        <div class="label">参数</div>
        <pre>{{ pretty(props.msg.args) }}</pre>
      </template>
      <template v-if="props.msg.result">
        <div class="label">结果</div>
        <pre class="result">{{ pretty(props.msg.result) }}</pre>
      </template>
      <div v-if="!props.msg.args && !props.msg.result" class="label muted">等待执行…</div>
    </div>
  </div>
</template>

<style scoped>
.tool-card {
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--bg-card);
  overflow: hidden;
  transition: border-color 0.2s;
}

.tool-card.error {
  border-color: rgba(255, 93, 93, 0.4);
}

.tool-card.skill {
  border-color: rgba(53, 224, 255, 0.28);
}

.tool-card.delegation {
  border-color: rgba(143, 123, 255, 0.32);
}

.head {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 9px 12px;
  border: none;
  background: transparent;
  color: var(--text-1);
  font-size: 12.5px;
  text-align: left;
}

.head:hover {
  background: rgba(79, 140, 255, 0.06);
}

.cat-icon {
  display: grid;
  place-items: center;
  width: 22px;
  height: 22px;
  border-radius: 6px;
  color: var(--accent-cyan);
  background: rgba(53, 224, 255, 0.1);
  flex: none;
}

.delegation .cat-icon {
  color: #b3a4ff;
  background: rgba(143, 123, 255, 0.12);
}

.badge {
  padding: 1.5px 7px;
  font-size: 10px;
  color: var(--text-2);
  border: 1px solid var(--line);
  border-radius: 5px;
  flex: none;
}

.skill .badge {
  color: var(--accent-cyan);
  border-color: rgba(53, 224, 255, 0.35);
}

.delegation .badge {
  color: #b3a4ff;
  border-color: rgba(143, 123, 255, 0.4);
}

.name {
  font-family: 'Cascadia Code', 'JetBrains Mono', Consolas, monospace;
  font-size: 12px;
  color: var(--text-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.subagent {
  font-size: 11px;
  color: #b3a4ff;
  flex: none;
}

.status {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 10.5px;
  color: var(--text-3);
  flex: none;
}

.status.done {
  color: var(--green);
}

.status.error {
  color: #ff6b6b;
}

.status.calling {
  color: var(--accent-cyan);
}

.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}

.status.calling .dot {
  animation: pulse 1s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.25; }
}

.chev {
  color: var(--text-3);
  transition: transform 0.2s;
  flex: none;
}

.body {
  padding: 0 12px 11px;
}

.label {
  font-size: 10.5px;
  color: var(--text-3);
  margin: 6px 0 4px;
}

.label.muted {
  margin: 2px 0 0;
}

pre {
  margin: 0;
  padding: 8px 10px;
  max-height: 180px;
  overflow: auto;
  font-family: 'Cascadia Code', 'JetBrains Mono', Consolas, monospace;
  font-size: 11px;
  line-height: 1.55;
  color: var(--text-2);
  background: rgba(6, 13, 28, 0.72);
  border: 1px solid var(--line);
  border-radius: 8px;
  white-space: pre-wrap;
  word-break: break-all;
}

pre.result {
  color: #a9d3ff;
}
</style>
