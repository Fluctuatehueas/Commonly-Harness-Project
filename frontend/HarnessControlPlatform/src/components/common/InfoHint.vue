<script setup lang="ts">
// 通用说明气泡：行标题旁的 info 图标，点击弹出人话解释。
// 通过 Teleport 渲染到 body，避免被父级 overflow / 层叠上下文裁切，可在任意页面复用。
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import AppIcon from '@/components/console/AppIcon.vue'

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

const wrap = ref<HTMLElement | null>(null)
const icon = ref<HTMLElement | null>(null)
const open = ref(false)
const position = ref({ left: 0, top: 0, below: false })

const iconClass = computed(() => (props.tone === 'accent' ? 'ih-icon accent' : 'ih-icon'))

function place() {
  const anchor = icon.value
  if (!anchor) return
  const rect = anchor.getBoundingClientRect()
  const width = 268
  const left = Math.min(
    Math.max(rect.left + rect.width / 2, width / 2 + 12),
    window.innerWidth - width / 2 - 12,
  )
  const belowByDemand = rect.top < 150
  const below =
    props.placement === 'bottom' || (props.placement === 'auto' && belowByDemand)
  position.value = {
    left,
    top: below ? rect.bottom + 10 : rect.top - 10,
    below,
  }
}

function show() {
  void nextTick(() => {
    place()
    open.value = true
  })
}

function toggle() {
  if (open.value) {
    open.value = false
    return
  }
  show()
}

function onDocumentDown(event: MouseEvent) {
  const target = event.target as Node
  if (wrap.value?.contains(target)) return
  open.value = false
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') open.value = false
}

function bind() {
  document.addEventListener('mousedown', onDocumentDown)
  document.addEventListener('keydown', onKeydown)
  window.addEventListener('scroll', place, true)
  window.addEventListener('resize', place)
}

function unbind() {
  document.removeEventListener('mousedown', onDocumentDown)
  document.removeEventListener('keydown', onKeydown)
  window.removeEventListener('scroll', place, true)
  window.removeEventListener('resize', place)
}

watch(open, (value) => (value ? bind() : unbind()))
onBeforeUnmount(unbind)
</script>

<template>
  <span ref="wrap" class="info-hint">
    <span
      ref="icon"
      class="ih-trigger"
      role="button"
      tabindex="0"
      :aria-label="title ? `${title}说明` : '查看说明'"
      :title="title ? `${title}说明` : '查看说明'"
      @click.stop="toggle"
      @keydown.enter.stop.prevent="toggle"
    >
      <AppIcon name="info" :size="props.size" :class="iconClass" />
    </span>

    <Teleport to="body">
      <Transition name="ih-pop">
        <div
          v-if="open"
          class="ih-popup"
          :style="{
            left: `${position.left}px`,
            top: `${position.top}px`,
            transform: position.below
              ? 'translate(-50%, 0)'
              : 'translate(-50%, -100%)',
          }"
          role="dialog"
        >
          <strong v-if="props.title">{{ props.title }}</strong>
          <p>{{ props.text }}</p>
        </div>
      </Transition>
    </Teleport>
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
  cursor: pointer;
  color: var(--text-3);
  border-radius: 50%;
  outline: none;
  transition: color 0.18s ease, transform 0.18s ease;
}

.ih-trigger:hover,
.ih-trigger:focus-visible {
  color: var(--accent-cyan);
  transform: scale(1.08);
}

.ih-icon.accent {
  color: var(--accent-cyan);
}

.ih-popup {
  position: fixed;
  z-index: 4000;
  width: 268px;
  padding: 10px 12px;
  border-radius: 10px;
  border: 1px solid rgba(124, 140, 255, 0.32);
  background: rgba(13, 19, 34, 0.98);
  box-shadow: 0 16px 36px rgba(0, 0, 0, 0.5);
  backdrop-filter: blur(6px);
  pointer-events: none;
}

.ih-popup strong {
  display: block;
  margin-bottom: 5px;
  font-size: 12px;
  color: var(--text-1);
}

.ih-popup p {
  margin: 0;
  font-size: 11.5px;
  line-height: 1.65;
  color: var(--text-2);
  white-space: pre-wrap;
}

.ih-pop-enter-active,
.ih-pop-leave-active {
  transition: opacity 0.16s ease, transform 0.16s ease;
}

.ih-pop-enter-from,
.ih-pop-leave-to {
  opacity: 0;
}
</style>
