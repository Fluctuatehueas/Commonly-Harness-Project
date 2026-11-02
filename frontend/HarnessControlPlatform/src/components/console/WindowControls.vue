<script setup lang="ts">
// 无边框窗口的自绘窗口按钮（仅 Electron 中渲染，浏览器里整块隐藏）
import { onBeforeUnmount, onMounted, ref } from 'vue'
import AppIcon from './AppIcon.vue'

interface HarnessWindowApi {
  minimize: () => void
  toggleMaximize: () => void
  close: () => void
  isMaximized: () => Promise<boolean>
  onMaximizeChange: (callback: (maximized: boolean) => void) => () => void
}

const bridge = (window as unknown as { harnessWindow?: HarnessWindowApi }).harnessWindow
const maximized = ref(false)
let dispose: (() => void) | undefined

onMounted(async () => {
  if (!bridge) return
  maximized.value = await bridge.isMaximized().catch(() => false)
  dispose = bridge.onMaximizeChange((value) => (maximized.value = value))
})

onBeforeUnmount(() => dispose?.())
</script>

<template>
  <div v-if="bridge" class="win-controls no-drag">
    <button type="button" title="最小化" @click="bridge.minimize()">
      <AppIcon name="minimize" :size="14" />
    </button>
    <button type="button" :title="maximized ? '向下还原' : '最大化'" @click="bridge.toggleMaximize()">
      <AppIcon :name="maximized ? 'restore' : 'maximize'" :size="12" />
    </button>
    <button type="button" class="close" title="关闭" @click="bridge.close()">
      <AppIcon name="close" :size="13" />
    </button>
  </div>
</template>

<style scoped>
.win-controls {
  display: flex;
  align-items: center;
  gap: 2px;
  align-self: center;
  flex: none;
  margin-right: -14px;
  padding-right: 4px;
  -webkit-app-region: no-drag;
}

.win-controls button {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  padding: 0;
  color: var(--text-2);
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  transition: background 0.15s, color 0.15s;
}

.win-controls button:hover {
  color: var(--text-1);
  background: rgba(126, 154, 198, 0.12);
}

.win-controls button.close:hover {
  color: #fff;
  background: #d13438;
}
</style>
