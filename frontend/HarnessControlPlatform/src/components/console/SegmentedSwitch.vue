<script setup lang="ts" generic="T extends string | number | boolean">
// 自定义分段单选：替代 el-radio，与全息界面同一套配色。
interface SegOption {
  value: T
  label: string
}

withDefaults(
  defineProps<{
    modelValue: T
    options: SegOption[]
    block?: boolean
  }>(),
  { block: false },
)

const emit = defineEmits<{
  'update:modelValue': [value: T]
}>()
</script>

<template>
  <div class="seg" :class="{ block }" role="radiogroup">
    <button
      v-for="option in options"
      :key="String(option.value)"
      type="button"
      role="radio"
      class="seg-item"
      :class="{ active: option.value === modelValue }"
      :aria-checked="option.value === modelValue"
      @click="emit('update:modelValue', option.value)"
    >
      <span class="seg-dot" aria-hidden="true"></span>
      {{ option.label }}
    </button>
  </div>
</template>

<style scoped>
.seg {
  display: inline-flex;
  align-items: stretch;
  gap: 2px;
  padding: 3px;
  border: 1px solid rgba(126, 154, 198, 0.16);
  border-radius: 7px;
  background: rgba(8, 16, 30, 0.55);
}

.seg.block {
  display: flex;
  width: 100%;
}

.seg.block .seg-item {
  flex: 1;
  justify-content: center;
}

.seg-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 12px;
  font-size: 11.5px;
  color: var(--text-3);
  border: 1px solid transparent;
  border-radius: 5px;
  background: transparent;
  white-space: nowrap;
  cursor: pointer;
  transition:
    color 0.16s ease,
    background 0.16s ease,
    box-shadow 0.16s ease;
}

.seg-item:hover:not(.active) {
  color: var(--text-1);
  background: rgba(126, 154, 198, 0.08);
}

.seg-item.active {
  color: #041018;
  background: linear-gradient(135deg, var(--accent-cyan), var(--accent));
  box-shadow: 0 0 14px rgba(55, 208, 196, 0.24);
}

.seg-dot {
  width: 5px;
  height: 5px;
  flex: none;
  border-radius: 50%;
  background: currentColor;
  opacity: 0.35;
  transition: opacity 0.16s ease;
}

.seg-item.active .seg-dot {
  opacity: 1;
}
</style>
