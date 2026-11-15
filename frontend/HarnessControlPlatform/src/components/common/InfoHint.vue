<script setup lang="ts">
// 通用说明气泡：内部复用 HoloTooltip，保证全站图标提示样式统一。
// 调用方 API 保持不变（title / text / placement / size / tone）。
import { computed } from 'vue'
import AppIcon from '@/components/console/AppIcon.vue'
import HoloTooltip from '@/components/console/HoloTooltip.vue'

const props = withDefaults(
  defineProps<{
    title?: string
    text: string
    /** 气泡相对图标的倾向位置，默认自动（空间不足时翻转） */
    placement?: 'auto' | 'top' | 'bottom'
    size?: number
    /** 图标颜色，默认继承 */
    tone?: 'muted' | 'accent'
  }>(),
  { placement: 'auto', size: 12, tone: 'muted' },
)

const iconClass = computed(() => (props.tone === 'accent' ? 'ih-icon accent' : 'ih-icon'))
const tipPlacement = computed<'top' | 'bottom'>(() =>
  props.placement === 'bottom' ? 'bottom' : 'top',
)
</script>

<template>
  <span class="info-hint">
    <HoloTooltip
      :title="title || '说明'"
      :content="text"
      :placement="tipPlacement"
      :width="320"
    >
      <span
        class="ih-trigger"
        :aria-label="title ? `${title}说明` : '查看说明'"
      >
        <AppIcon name="info" :size="size" :class="iconClass" />
      </span>
    </HoloTooltip>
  </span>
</template>

<style scoped>
.info-hint {
  display: inline-flex;
  align-items: center;
}

.ih-trigger {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--text-3);
  cursor: help;
  border-radius: 50%;
  outline: none;
  transition: color 0.18s ease, transform 0.18s ease;
}

.ih-trigger:hover {
  color: var(--accent-cyan);
  transform: scale(1.08);
}

.ih-icon.accent {
  color: var(--accent-cyan);
}
</style>
