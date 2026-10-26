<script setup lang="ts">
// 开关控件
const props = defineProps<{ modelValue: boolean; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [boolean] }>()

function toggle() {
  if (props.disabled) return
  emit('update:modelValue', !props.modelValue)
}
</script>

<template>
  <button class="switch" :class="{ on: props.modelValue }" type="button" role="switch" :aria-checked="props.modelValue" :disabled="props.disabled" @click="toggle">
    <span class="knob"></span>
  </button>
</template>

<style scoped>
.switch {
  position: relative;
  width: 40px;
  height: 22px;
  padding: 0;
  border: 1px solid var(--line-strong);
  border-radius: 999px;
  background: rgba(20, 38, 74, 0.7);
  transition: all 0.22s;
  flex: none;
}

.switch.on {
  background: linear-gradient(90deg, #3b78ff, #2a5cd6);
  border-color: transparent;
  box-shadow: 0 0 12px rgba(59, 120, 255, 0.45);
}

.switch:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.knob {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: #dbe9ff;
  transition: transform 0.22s;
}

.switch.on .knob {
  transform: translateX(18px);
}
</style>
