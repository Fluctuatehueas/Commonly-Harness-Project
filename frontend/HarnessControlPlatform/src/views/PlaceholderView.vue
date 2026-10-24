<script setup lang="ts">
// 未实现页面的统一占位
import TopNav from '@/components/console/TopNav.vue'
import AppIcon from '@/components/console/AppIcon.vue'
import type { IconName } from '@/components/console/icons'

const props = defineProps<{ title: string; icon: IconName }>()
</script>

<template>
  <div class="app-shell">
    <TopNav />
    <div class="page">
        <section class="panel placeholder">
          <div class="orb"><AppIcon :name="props.icon" :size="26" /></div>
          <h2>{{ props.title }}</h2>
          <p>该模块正在建设中，敬请期待。</p>
        </section>
      </div>
  </div>
</template>

<style scoped>
.app-shell {
  min-height: 100vh;
}

.page {
  padding: 18px 22px 26px;
}

.placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: calc(100vh - var(--topbar-h) - 44px);
  text-align: center;
}

.orb {
  display: grid;
  place-items: center;
  width: 60px;
  height: 60px;
  margin-bottom: 16px;
  border-radius: 50%;
  color: var(--accent-cyan);
  background: radial-gradient(circle at 35% 30%, rgba(53, 224, 255, 0.22), rgba(30, 70, 150, 0.25));
  box-shadow: 0 0 30px rgba(53, 224, 255, 0.2);
}

h2 {
  margin: 0 0 6px;
  font-size: 18px;
}

p {
  margin: 0;
  font-size: 12.5px;
  color: var(--text-3);
}
</style>
