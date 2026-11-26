<script setup lang="ts">
// 用户长消息折叠：默认展示前 N 行，点击展开查看全文。
// 折叠状态只存在于组件内部，切换会话或刷新页面后自动回到折叠态。
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = withDefaults(defineProps<{ text: string; lines?: number }>(), {
  lines: 8,
})

const expanded = ref(false)
const overflow = ref(false)
const limitPx = ref(0)
const contentEl = ref<HTMLElement | null>(null)
let observer: ResizeObserver | null = null

function measure() {
  const el = contentEl.value
  if (!el) return
  const styles = window.getComputedStyle(el)
  const lineHeight = Number.parseFloat(styles.lineHeight) || 20
  const limit = Math.round(lineHeight * props.lines)
  limitPx.value = limit
  // max-height + overflow:hidden 不会改变 scrollHeight，因此可在折叠态下稳定测量。
  overflow.value = el.scrollHeight > limit + 2
}

onMounted(async () => {
  await nextTick()
  measure()
  if (typeof ResizeObserver !== 'undefined' && contentEl.value) {
    observer = new ResizeObserver(() => measure())
    observer.observe(contentEl.value)
  }
})

onBeforeUnmount(() => observer?.disconnect())

watch(
  () => props.text,
  () => void nextTick(measure),
)
</script>

<template>
  <div class="ct">
    <div
      ref="contentEl"
      class="ct-body"
      :class="{ clamped: overflow && !expanded }"
      :style="overflow && !expanded ? { maxHeight: `${limitPx}px` } : undefined"
    >{{ text }}</div>
    <button
      v-if="overflow"
      class="ct-toggle"
      type="button"
      :aria-expanded="expanded"
      @click="expanded = !expanded"
    >
      {{ expanded ? '收起' : '展开' }}
    </button>
  </div>
</template>

<style scoped>
.ct {
  display: block;
  min-width: 0;
}

.ct-body {
  white-space: pre-wrap;
  word-break: break-word;
}

/* 折叠时底部渐隐，提示还有内容未展示 */
.ct-body.clamped {
  overflow: hidden;
  -webkit-mask-image: linear-gradient(180deg, #000 86%, transparent 100%);
  mask-image: linear-gradient(180deg, #000 86%, transparent 100%);
}

.ct-toggle {
  display: inline-block;
  margin-top: 3px;
  padding: 0;
  font-family: inherit;
  font-size: 11px;
  line-height: 1.5;
  color: var(--text-3);
  white-space: nowrap;
  border: 0;
  background: none;
  cursor: pointer;
  transition: color 0.15s;
}

.ct-toggle:hover {
  color: var(--accent-cyan);
}
</style>
