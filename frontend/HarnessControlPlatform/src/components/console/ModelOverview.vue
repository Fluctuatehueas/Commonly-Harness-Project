<script setup lang="ts">
// 模型概览卡片
import AppIcon from './AppIcon.vue'
import { MODELS } from '@/mock/dashboard'
</script>

<template>
  <section class="panel models">
    <div class="panel-head">
      <div class="panel-title">
        <span class="title-icon"><AppIcon name="cube" :size="14" /></span>
        模型概览
      </div>
      <RouterLink class="panel-link" to="/models">全部模型 <AppIcon name="chevronRight" :size="12" /></RouterLink>
    </div>

    <div class="grid">
      <article v-for="m in MODELS" :key="m.name" class="model-card">
        <div class="model-top">
          <div class="model-avatar" :style="{ background: m.gradient }">{{ m.initial }}</div>
          <div class="model-meta">
            <strong>{{ m.name }}</strong>
            <span>{{ m.provider }}</span>
          </div>
          <span class="status"><i class="dot"></i>在线</span>
        </div>

        <div class="context">
          <AppIcon name="memory" :size="13" />
          <span>{{ m.context }}</span>
        </div>

        <div class="tags">
          <span v-for="t in m.tags" :key="t" class="tag">
            <AppIcon name="check" :size="11" />
            {{ t }}
          </span>
        </div>
      </article>
    </div>
  </section>
</template>

<style scoped>
.models {
  padding-bottom: 16px;
}

.grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  padding: 14px 16px 0;
}

.model-card {
  padding: 14px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--bg-card);
  transition: all 0.2s;
}

.model-card:hover {
  border-color: var(--line-strong);
  background: var(--bg-card-hover);
  transform: translateY(-2px);
}

.model-top {
  display: flex;
  align-items: center;
  gap: 9px;
}

.model-avatar {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  border-radius: 50%;
  font-size: 15px;
  font-weight: 700;
  color: #fff;
  flex: none;
}

.model-meta {
  display: flex;
  flex-direction: column;
  line-height: 1.25;
  min-width: 0;
  flex: 1;
}

.model-meta strong {
  font-size: 13px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.model-meta span {
  font-size: 11px;
  color: var(--text-3);
}

.status {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 10.5px;
  color: var(--green);
  flex: none;
}

.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--green);
  box-shadow: 0 0 6px var(--green);
}

.context {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 12px;
  font-size: 11.5px;
  color: var(--text-2);
}

.context svg {
  color: var(--text-3);
}

.tags {
  display: flex;
  gap: 8px;
  margin-top: 10px;
}

.tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  font-size: 10.5px;
  color: var(--text-2);
  border: 1px solid var(--line);
  border-radius: 6px;
  background: rgba(10, 20, 40, 0.5);
}

.tag svg {
  color: var(--accent-cyan);
}

@media (max-width: 1280px) {
  .grid {
    grid-template-columns: repeat(2, 1fr);
  }
}
</style>
