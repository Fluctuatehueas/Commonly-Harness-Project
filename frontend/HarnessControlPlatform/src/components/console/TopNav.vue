<script setup lang="ts">
// 顶部导航：品牌 + 水平菜单 + 时钟（原左侧菜单移到顶部）
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import AppIcon from './AppIcon.vue'
import { NAV_ITEMS } from '@/mock/dashboard'

const route = useRoute()
const active = computed(() => String(route.name || 'home'))

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
  <header class="topnav">
    <RouterLink to="/" class="brand">
      <span class="brand-logo"><AppIcon name="globe" :size="18" /></span>
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
  gap: 18px;
  padding: 0 20px;
  background: rgba(6, 12, 26, 0.82);
  border-bottom: 1px solid var(--line);
  backdrop-filter: blur(10px);
}

.brand {
  display: flex;
  align-items: center;
  gap: 9px;
  flex: none;
}

.brand-logo {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  border-radius: 50%;
  color: #cfe3ff;
  background: radial-gradient(circle at 30% 30%, #2f6bff, #0a1e4d);
  box-shadow: 0 0 14px rgba(63, 122, 255, 0.4);
}

.brand strong {
  font-size: 16px;
  letter-spacing: 0.5px;
}

.menu {
  display: flex;
  align-items: center;
  gap: 4px;
  overflow-x: auto;
  scrollbar-width: none;
}

.menu::-webkit-scrollbar {
  display: none;
}

.menu-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 12px;
  font-size: 12.5px;
  color: var(--text-2);
  border-radius: 999px;
  white-space: nowrap;
  transition: all 0.18s;
}

.menu-item:hover {
  color: var(--text-1);
  background: rgba(79, 140, 255, 0.1);
}

.menu-item.active {
  color: #fff;
  background: linear-gradient(90deg, rgba(63, 122, 255, 0.9), rgba(47, 94, 210, 0.6));
  box-shadow: 0 2px 12px rgba(47, 108, 255, 0.35);
}

.spacer {
  flex: 1;
}

.clock {
  flex: none;
  padding: 5px 12px;
  font-size: 11.5px;
  font-variant-numeric: tabular-nums;
  color: var(--text-1);
  border: 1px solid var(--line);
  border-radius: 999px;
  background: rgba(16, 32, 64, 0.55);
}
</style>
