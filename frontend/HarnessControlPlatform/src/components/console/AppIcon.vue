<script setup lang="ts">
// 统一线性图标
import { ICONS, type IconName } from './icons'

const props = withDefaults(defineProps<{ name: IconName; size?: number }>(), {
  size: 18,
})
</script>

<template>
  <svg
    :width="props.size"
    :height="props.size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.7"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path v-for="(d, i) in ICONS[props.name]" :key="i" :d="d" />
  </svg>
</template>
