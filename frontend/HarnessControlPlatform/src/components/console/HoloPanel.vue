<script setup lang="ts">
// 全息弹窗卡：与控制台 HOLO ARCHIVE 面板一致的右侧浮层，替代 el-drawer。
// 仅负责外壳（遮罩、切角、标题、页脚），内容通过插槽注入。
import { Close } from '@element-plus/icons-vue'

withDefaults(
  defineProps<{
    modelValue: boolean
    title: string
    kicker?: string
    subtitle?: string
    width?: number
  }>(),
  {
    kicker: 'HOLO PANEL',
    subtitle: '',
    width: 440,
  },
)

const emit = defineEmits<{
  'update:modelValue': [visible: boolean]
}>()

function close() {
  emit('update:modelValue', false)
}
</script>

<template>
  <Teleport to="body">
    <Transition name="holo-pop">
      <div v-if="modelValue" class="holo-layer" @click.self="close">
        <section
          class="holo-panel"
          :style="{ width: `${width}px` }"
          role="dialog"
          aria-modal="true"
        >
          <div class="holo-grid" aria-hidden="true"></div>
          <span class="holo-corner tl"></span>
          <span class="holo-corner tr"></span>
          <span class="holo-corner bl"></span>
          <span class="holo-corner br"></span>

          <header class="holo-head">
            <div class="holo-head-text">
              <span class="holo-kicker mono">{{ kicker }}</span>
              <h2>{{ title }}</h2>
              <p v-if="subtitle">{{ subtitle }}</p>
            </div>
            <button type="button" class="holo-close" title="关闭" @click="close">
              <el-icon><Close /></el-icon>
            </button>
          </header>

          <div class="holo-body">
            <slot />
          </div>

          <footer v-if="$slots.footer" class="holo-foot">
            <slot name="footer" />
          </footer>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.holo-layer {
  position: fixed;
  inset: 0;
  z-index: 2000;
  display: flex;
  align-items: flex-start;
  justify-content: flex-end;
  padding: 82px 22px 22px;
  background: rgba(3, 8, 18, 0.46);
  backdrop-filter: blur(2px);
}

.holo-panel {
  position: relative;
  display: flex;
  flex-direction: column;
  max-height: calc(100vh - 104px);
  padding: 20px 22px 16px;
  overflow: hidden;
  border: 1px solid rgba(55, 208, 196, 0.42);
  border-radius: 10px;
  background:
    repeating-linear-gradient(
      0deg,
      rgba(55, 208, 196, 0.035) 0,
      rgba(55, 208, 196, 0.035) 1px,
      transparent 1px,
      transparent 4px
    ),
    linear-gradient(145deg, rgba(6, 24, 36, 0.97), rgba(4, 10, 22, 0.95));
  box-shadow:
    0 0 0 1px rgba(55, 208, 196, 0.08),
    0 30px 90px rgba(0, 0, 0, 0.6),
    inset 0 0 60px rgba(55, 208, 196, 0.05);
  clip-path: polygon(
    0 14px,
    14px 0,
    calc(100% - 26px) 0,
    100% 26px,
    100% calc(100% - 14px),
    calc(100% - 14px) 100%,
    26px 100%,
    0 calc(100% - 26px)
  );
  transform-origin: right top;
}

.holo-grid {
  position: absolute;
  inset: 0;
  pointer-events: none;
  background-image:
    linear-gradient(rgba(55, 208, 196, 0.05) 1px, transparent 1px),
    linear-gradient(90deg, rgba(55, 208, 196, 0.05) 1px, transparent 1px);
  background-size: 32px 32px;
  mask-image: radial-gradient(circle at 50% 40%, #000 20%, transparent 78%);
}

.holo-corner {
  position: absolute;
  width: 12px;
  height: 12px;
  border-color: rgba(160, 240, 255, 0.9);
  border-style: solid;
  border-width: 0;
  pointer-events: none;
}

.holo-corner.tl {
  top: 6px;
  left: 6px;
  border-top-width: 1px;
  border-left-width: 1px;
}

.holo-corner.tr {
  top: 6px;
  right: 6px;
  border-top-width: 1px;
  border-right-width: 1px;
}

.holo-corner.bl {
  bottom: 6px;
  left: 6px;
  border-bottom-width: 1px;
  border-left-width: 1px;
}

.holo-corner.br {
  bottom: 6px;
  right: 6px;
  border-bottom-width: 1px;
  border-right-width: 1px;
}

.holo-head {
  position: relative;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding-bottom: 14px;
  border-bottom: 1px solid rgba(55, 208, 196, 0.22);
}

.holo-head-text {
  min-width: 0;
}

.holo-kicker {
  font-size: 9px;
  letter-spacing: 0.22em;
  color: rgba(55, 208, 196, 0.75);
}

.holo-head h2 {
  margin: 4px 0 0;
  font-size: 19px;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: #dffaff;
}

.holo-head p {
  margin: 6px 0 0;
  font-size: 10.5px;
  line-height: 1.6;
  color: var(--text-3);
}

.holo-close {
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  flex: none;
  color: rgba(55, 208, 196, 0.85);
  border: 1px solid rgba(55, 208, 196, 0.3);
  border-radius: 50%;
  background: transparent;
  cursor: pointer;
  transition: color 0.16s ease, border-color 0.16s ease, box-shadow 0.16s ease;
}

.holo-close:hover {
  color: #dffaff;
  border-color: rgba(160, 240, 255, 0.8);
  box-shadow: 0 0 14px rgba(55, 208, 196, 0.3);
}

.holo-body {
  position: relative;
  flex: 1;
  min-height: 0;
  padding-top: 16px;
  overflow-y: auto;
}

.holo-foot {
  position: relative;
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding-top: 14px;
  margin-top: 14px;
  border-top: 1px solid rgba(55, 208, 196, 0.22);
}

.holo-pop-enter-active,
.holo-pop-leave-active {
  transition:
    opacity 0.22s ease,
    transform 0.3s cubic-bezier(0.22, 1, 0.36, 1);
}

.holo-pop-enter-from,
.holo-pop-leave-to {
  opacity: 0;
  transform: translateX(26px) scale(0.98);
}
</style>
