<script setup lang="ts">
// 顶部导航：品牌 + 水平菜单 + 时钟（原左侧菜单移到顶部）
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import AppIcon from './AppIcon.vue'
import WindowControls from './WindowControls.vue'
import brandIcon from '@/assets/icon.png'
import { NAV_ITEMS } from '@/mock/dashboard'

const route = useRoute()
const active = computed(() => String(route.name || 'home'))

// Electron 里才启用自绘窗口拖动区与右上角按钮；浏览器构建完全不受影响。
const bridge = (
  window as unknown as {
    harnessWindow?: { toggleMaximize: () => void }
  }
).harnessWindow
const isElectron = Boolean(bridge)

function onTopbarDblClick(event: MouseEvent) {
  if (!bridge) return
  const target = event.target as HTMLElement | null
  // 点在链接 / 按钮 / 输入框上时不触发窗口最大化
  if (target?.closest('a, button, input, select, textarea, .no-drag')) return
  bridge.toggleMaximize()
}

const now = ref(format(new Date()))
let timer: number | undefined

function format(d: Date) {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

onMounted(() => {
  timer = window.setInterval(() => (now.value = format(new Date())), 1000)
})
onBeforeUnmount(() => window.clearInterval(timer))
</script>

<template>
  <header class="topnav" :class="{ electron: isElectron }" @dblclick="onTopbarDblClick">
    <RouterLink to="/" class="brand">
      <img class="brand-logo" :src="brandIcon" alt="Harness" />
      <strong>Harness</strong>
    </RouterLink>

    <nav class="menu">
      <RouterLink
        v-for="item in NAV_ITEMS"
        :key="item.key"
        class="menu-item"
        :class="{ active: active === item.key }"
        :to="item.path"
      >
        <AppIcon :name="item.icon" :size="15" />
        <span>{{ item.label }}</span>
      </RouterLink>
    </nav>

    <div class="spacer"></div>
    <slot name="actions"></slot>
    <div class="clock">{{ now }}</div>
    <WindowControls />
  </header>
</template>

<style scoped>
.topnav {
  position: sticky;
  top: 0;
  z-index: 30;
  height: var(--topbar-h);
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 14px 0 12px;
  background: rgba(9, 14, 25, 0.62);
  border-bottom: 1px solid var(--line);
  backdrop-filter: blur(12px);
}

/* Electron 无边框窗口：顶栏作为拖动区，交互元素与窗口按钮单独排除 */
.topnav.electron {
  -webkit-app-region: drag;
}

.topnav.electron :deep(a),
.topnav.electron :deep(button),
.topnav.electron :deep(input),
.topnav.electron :deep(select),
.topnav.electron :deep(textarea) {
  -webkit-app-region: no-drag;
}

.brand {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: none;
}

.brand-logo {
  width: 30px;
  height: 30px;
  flex: none;
  border-radius: 0;
  object-fit: contain;
  box-shadow: none;
}

.brand strong {
  font-size: 14px;
  font-weight: 600;
  letter-spacing: -0.02em;
}

.menu {
  display: flex;
  align-items: center;
  gap: 2px;
  height: 100%;
  overflow-x: auto;
  scrollbar-width: none;
}

.menu::-webkit-scrollbar {
  display: none;
}

.menu-item {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 100%;
  flex: none;
  padding: 6px 10px;
  font-size: 12px;
  letter-spacing: 0;
  color: var(--text-2);
  white-space: nowrap;
  transition: color 0.16s ease, background 0.16s ease;
}

.menu-item span {
  white-space: nowrap;
}

.menu-item::after {
  content: '';
  position: absolute;
  left: 10px;
  right: 10px;
  bottom: 0;
  height: 1px;
  background: transparent;
  transition: background 0.16s ease;
}

.menu-item:hover {
  color: var(--text-1);
  background: rgba(126, 154, 198, 0.06);
}

.menu-item.active {
  color: var(--text-1);
  background: transparent;
}

.menu-item.active::after {
  background: var(--accent);
}

.menu-item.active :deep(svg) {
  color: var(--accent);
}

.spacer {
  flex: 1;
}

.clock {
  flex: none;
  padding: 0;
  font-family: var(--font-mono);
  font-size: 10.5px;
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.02em;
  white-space: nowrap;
  color: var(--text-3);
  border: 0;
  border-radius: 0;
  background: transparent;
}
</style>
