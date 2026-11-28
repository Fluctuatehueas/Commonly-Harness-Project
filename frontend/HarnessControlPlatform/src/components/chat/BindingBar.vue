<script setup lang="ts">
// 本轮绑定条：展示即将随下一条消息生效的资源
export interface BindingChip {
  key: string
  label: string
  value: string
}

defineProps<{ items: BindingChip[] }>()
const emit = defineEmits<{ remove: [string] }>()
</script>

<template>
  <div v-if="items.length" class="binding-bar">
    <span class="binding-title">本轮绑定</span>
    <span v-for="item in items" :key="item.key" class="binding-chip">
      <span class="binding-kind">{{ item.label }}</span>
      <span class="binding-value mono">{{ item.value }}</span>
      <button type="button" class="binding-remove" title="取消绑定" @click="emit('remove', item.key)">
        ×
      </button>
    </span>
  </div>
</template>

<style scoped>
.binding-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  padding: 4px 2px 6px;
}
.binding-title {
  color: var(--text-3);
  font-size: 11px;
}
.binding-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 22px;
  padding: 0 6px 0 8px;
  border-radius: 6px;
  border: 1px solid rgba(55, 208, 196, 0.4);
  background: rgba(55, 208, 196, 0.1);
  font-size: 11px;
  color: var(--text-2);
}
.binding-kind {
  color: var(--accent-cyan);
}
.binding-value {
  color: var(--text-1);
}
.binding-remove {
  border: 0;
  background: transparent;
  color: var(--text-3);
  cursor: pointer;
  font-size: 13px;
  line-height: 1;
}
.binding-remove:hover {
  color: var(--danger);
}
.mono {
  font-family: var(--font-mono);
}
</style>
