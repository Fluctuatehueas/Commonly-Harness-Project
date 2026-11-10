<script setup lang="ts">
// 顶部导航：品牌 + 水平菜单 + 时钟（原左侧菜单移到顶部）
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Menu as MenuIcon } from '@element-plus/icons-vue'
import AppIcon from './AppIcon.vue'
import UserSwitcher from './UserSwitcher.vue'
import WindowControls from './WindowControls.vue'
import brandIcon from '@/assets/icon.png'
import { NAV_ITEMS } from '@/mock/dashboard'

const route = useRoute()
const router = useRouter()

function onNavCommand(path: string) {
  if (path && path !== route.path) void router.push(path)
}

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

    <!--
      导航改用 el-menu（水平模式）：活动项状态、键盘方向键切换都交给组件统一维护；
      路由动作仍由内部 RouterLink 完成，保留锚点语义（可中键/新标签打开）。
      default-active 传当前路径，EP 会在路由变化时同步高亮。
    -->
    <el-menu
      class="menu"
      mode="horizontal"
      :default-active="route.path"
      :ellipsis="false"
    >
      <el-menu-item v-for="item in NAV_ITEMS" :key="item.key" :index="item.path">
        <RouterLink class="menu-link" :to="item.path">
          <AppIcon :name="item.icon" :size="15" />
          <span>{{ item.label }}</span>
        </RouterLink>
      </el-menu-item>
    </el-menu>

    <!-- 窄窗口：导航折叠成一个按钮，点击后以普通下拉菜单展开 -->
    <el-dropdown
      class="nav-dropdown"
      trigger="click"
      placement="bottom-start"
      popper-class="nav-dropdown-popper"
      @command="onNavCommand"
    >
      <button class="nav-toggle" type="button" aria-label="打开导航菜单">
        <el-icon><MenuIcon /></el-icon>
      </button>
      <template #dropdown>
        <el-dropdown-menu>
          <el-dropdown-item
            v-for="item in NAV_ITEMS"
            :key="item.key"
            :command="item.path"
            :class="{ 'nav-item-active': route.path === item.path }"
          >
            <AppIcon :name="item.icon" :size="15" />
            <span class="nav-item-label">{{ item.label }}</span>
          </el-dropdown-item>
        </el-dropdown-menu>
      </template>
    </el-dropdown>

    <div class="spacer"></div>
    <slot name="actions"></slot>
    <UserSwitcher />
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

/* el-menu-item 渲染为 li，需要一起排除拖动区，否则菜单点击会被窗口拖动吃掉 */
.topnav.electron :deep(a),
.topnav.electron :deep(button),
.topnav.electron :deep(li),
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

/* 菜单：只覆盖尺寸/文字色等主题变量，底色仍由主题桥接提供 */
.menu {
  height: 100%;
  /* 可收缩并允许内部横向滚动，窄窗口下不会挤掉右侧操作区 */
  flex: 0 1 auto;
  min-width: 0;
  gap: 2px;
  border-bottom: 0; /* 顶栏已有整体下边线，避免与 EP 菜单自带的边线叠成双线 */
  overflow-x: auto;
  scrollbar-width: none;
  --el-menu-text-color: var(--text-2);
  --el-menu-hover-text-color: var(--text-1);
  --el-menu-active-color: var(--text-1);
  --el-menu-horizontal-height: 100%;
}

.menu::-webkit-scrollbar {
  display: none;
}

/* 带上 .topnav 前缀提高优先级，避免与 EP 的水平菜单规则在打包顺序变化时打平 */
.topnav .menu :deep(.el-menu-item) {
  flex: none;
  height: 100%;
  padding: 0;
  font-size: 12px;
  border-bottom: 0;
}

/* 活动指示线沿用项目原来的 1px 强调色，而不是 EP 默认的 2px 文字色 */
.topnav .menu :deep(.el-menu-item.is-active) {
  border-bottom: 1px solid var(--accent);
}

.topnav .menu :deep(.el-menu-item.is-active .menu-link svg) {
  color: var(--accent);
}

.menu-link {
  display: flex;
  align-items: center;
  gap: 5px;
  height: 100%;
  padding: 6px 10px;
  color: inherit;
  letter-spacing: 0;
  white-space: nowrap;
}

.menu-link span {
  white-space: nowrap;
}

/* 窄窗口导航按钮：默认隐藏，媒体查询里替代水平菜单 */
.nav-dropdown {
  display: none;
  flex: none;
}

.nav-toggle {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  padding: 0;
  color: var(--text-2);
  border: 1px solid var(--line);
  border-radius: 8px;
  background: rgba(10, 18, 34, 0.55);
  transition: color 0.15s, border-color 0.15s, background 0.15s;
}

.nav-toggle:hover,
.nav-toggle:focus-visible {
  color: var(--accent-cyan);
  border-color: rgba(53, 224, 255, 0.45);
  background: rgba(53, 224, 255, 0.08);
  outline: none;
}

.nav-item-label {
  margin-left: 7px;
}

/* Dropdown 会被 teleport 到 body，需要用全局选择器命中 */
:global(.nav-dropdown-popper .el-dropdown-menu__item) {
  display: flex;
  align-items: center;
  min-width: 156px;
  font-size: 12px;
}

:global(.nav-dropdown-popper .nav-item-active) {
  color: var(--accent-cyan);
  background: rgba(55, 208, 196, 0.08);
}

/* 10 个导航项在 1440 宽度下仍会溢出，提前折叠避免出现横向滚动 */
@media (max-width: 1500px) {
  .menu {
    display: none;
  }

  .nav-dropdown {
    display: inline-flex;
  }
}

@media (max-width: 760px) {
  .clock {
    display: none;
  }
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
