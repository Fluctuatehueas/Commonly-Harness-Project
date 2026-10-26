<script setup lang="ts">
// 快捷入口九宫格
import { useRouter } from 'vue-router'
import AppIcon from './AppIcon.vue'
import { QUICK_ENTRIES } from '@/mock/dashboard'

const router = useRouter()

const ROUTES: Record<string, string> = {
  chat: '/',
  workflow: '/workflow',
  models: '/models',
  plugins: '/plugins',
  kb: '/kb',
  settings: '/settings',
}
</script>

<template>
  <section class="panel quick">
    <div class="panel-head">
      <div class="panel-title">
        <span class="title-icon"><AppIcon name="sparkles" :size="14" /></span>
        快捷入口
      </div>
    </div>

    <div class="grid">
      <button
        v-for="q in QUICK_ENTRIES"
        :key="q.key"
        class="entry"
        type="button"
        @click="router.push(ROUTES[q.key] || '/')"
      >
        <span class="entry-icon" :style="{ color: q.accent, background: q.accent + '1a' }">
          <AppIcon :name="q.icon" :size="17" />
        </span>
        <span class="entry-label">{{ q.label }}</span>
      </button>
    </div>
  </section>
</template>

<style scoped>
.quick {
  padding-bottom: 16px;
}

.grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
  padding: 14px 14px 0;
}

.entry {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 14px 6px 12px;
  color: var(--text-1);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--bg-card);
  transition: all 0.18s;
}

.entry:hover {
  border-color: var(--line-strong);
  background: var(--bg-card-hover);
  transform: translateY(-2px);
}

.entry-icon {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  border-radius: 10px;
}

.entry-label {
  font-size: 11.5px;
  color: var(--text-2);
}
</style>
