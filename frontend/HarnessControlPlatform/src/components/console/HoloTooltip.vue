<script setup lang="ts">
// 全息 ToolTip：与工作台模块悬停屏同一套视觉（切角、四角标记、扫描线）。
// 内容通过 el-tooltip 的 content 插槽渲染，popper 样式必须写在非 scoped 区块。
withDefaults(
  defineProps<{
    title: string
    content: string
    code?: string
    footer?: string
    placement?: 'top' | 'bottom' | 'left' | 'right'
    width?: number
  }>(),
  {
    code: '',
    footer: '',
    placement: 'top',
    width: 300,
  },
)
</script>

<template>
  <el-tooltip :placement="placement" :show-after="120" popper-class="holo-tooltip" effect="dark">
    <template #content>
      <div class="holo-tipcard" :style="{ width: `${width}px` }">
        <span class="holo-tipcard-corner tl"></span>
        <span class="holo-tipcard-corner tr"></span>
        <span class="holo-tipcard-corner bl"></span>
        <span class="holo-tipcard-corner br"></span>
        <header class="holo-tipcard-head">
          <span class="holo-tipcard-scan"></span>
          <strong>{{ title }}</strong>
          <code v-if="code">{{ code }}</code>
        </header>
        <p class="holo-tipcard-body">{{ content }}</p>
        <footer v-if="footer" class="holo-tipcard-foot">
          <span>HOLOGRAPHIC DRAWER BRIEF</span>
          <span>{{ footer }}</span>
        </footer>
      </div>
    </template>
    <slot />
  </el-tooltip>
</template>

<style>
/* popper 被 teleport 到 body，只能用全局选择器覆盖 Element Plus 默认气泡。 */
.el-popper.holo-tooltip.is-dark {
  padding: 0 !important;
  border: 0 !important;
  background: transparent !important;
  box-shadow: none !important;
}

.el-popper.holo-tooltip .el-popper__arrow {
  display: none;
}

.holo-tooltip .holo-tipcard {
  position: relative;
  padding: 13px 15px 11px;
  color: var(--text-2);
  border: 1px solid rgba(55, 208, 196, 0.55);
  background:
    repeating-linear-gradient(
      0deg,
      rgba(55, 208, 196, 0.06) 0,
      rgba(55, 208, 196, 0.06) 1px,
      transparent 1px,
      transparent 4px
    ),
    linear-gradient(145deg, rgba(5, 26, 38, 0.97), rgba(3, 10, 22, 0.94));
  box-shadow:
    0 0 0 1px rgba(55, 208, 196, 0.12),
    0 22px 60px rgba(0, 0, 0, 0.62),
    inset 0 0 40px rgba(55, 208, 196, 0.08);
  clip-path: polygon(
    0 10px,
    10px 0,
    calc(100% - 18px) 0,
    100% 18px,
    100% calc(100% - 10px),
    calc(100% - 10px) 100%,
    18px 100%,
    0 calc(100% - 18px)
  );
}

.holo-tipcard-corner {
  position: absolute;
  width: 10px;
  height: 10px;
  border-color: rgba(160, 240, 255, 0.9);
  border-style: solid;
  border-width: 0;
}

.holo-tipcard-corner.tl {
  top: 5px;
  left: 5px;
  border-top-width: 1px;
  border-left-width: 1px;
}

.holo-tipcard-corner.tr {
  top: 5px;
  right: 5px;
  border-top-width: 1px;
  border-right-width: 1px;
}

.holo-tipcard-corner.bl {
  bottom: 5px;
  left: 5px;
  border-bottom-width: 1px;
  border-left-width: 1px;
}

.holo-tipcard-corner.br {
  bottom: 5px;
  right: 5px;
  border-bottom-width: 1px;
  border-right-width: 1px;
}

.holo-tipcard-head {
  position: relative;
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding-bottom: 7px;
  overflow: hidden;
  border-bottom: 1px solid rgba(55, 208, 196, 0.28);
}

.holo-tipcard-head strong {
  font-size: 13px;
  font-weight: 600;
  color: #dffaff;
  letter-spacing: 0.02em;
}

.holo-tipcard-head code {
  margin-left: auto;
  font-family: var(--font-mono);
  font-size: 8.5px;
  color: rgba(55, 208, 196, 0.7);
  letter-spacing: 0.08em;
}

.holo-tipcard-scan {
  position: absolute;
  top: 0;
  left: -40%;
  width: 40%;
  height: 100%;
  background: linear-gradient(90deg, transparent, rgba(160, 240, 255, 0.28), transparent);
  animation: holo-tipcard-scan 2.4s linear infinite;
}

@keyframes holo-tipcard-scan {
  to {
    left: 110%;
  }
}

.holo-tipcard-body {
  margin: 9px 0 8px;
  font-size: 11.5px;
  line-height: 1.7;
  color: var(--text-2);
  white-space: pre-wrap;
  word-break: break-word;
}

.holo-tipcard-foot {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  padding-top: 7px;
  font-family: var(--font-mono);
  font-size: 8px;
  letter-spacing: 0.1em;
  color: rgba(126, 154, 198, 0.65);
  border-top: 1px solid rgba(55, 208, 196, 0.16);
}
</style>
