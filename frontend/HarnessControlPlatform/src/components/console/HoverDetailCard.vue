<script setup lang="ts">
// 通用悬停详情卡：MCP 列表、插件市场等场景可复用。
// 只负责展示，不感知业务接口；数据由调用方注入。
import { computed } from 'vue'
import AppIcon from './AppIcon.vue'
import type { IconName } from './icons'

interface MetaItem {
  label: string
  value: string
}

interface ListItem {
  name: string
  description?: string
}

const props = withDefaults(
  defineProps<{
    title: string
    subtitle?: string
    icon?: IconName
    tags?: string[]
    meta?: MetaItem[]
    items?: ListItem[]
    itemLabel?: string
    loading?: boolean
    emptyText?: string
    maxItems?: number
    footer?: string
  }>(),
  {
    subtitle: '',
    icon: 'server',
    tags: () => [],
    meta: () => [],
    items: () => [],
    itemLabel: '内容',
    loading: false,
    emptyText: '暂无内容',
    maxItems: 8,
    footer: '',
  },
)

const visibleItems = computed(() => props.items.slice(0, props.maxItems))
const overflowCount = computed(() =>
  Math.max(0, props.items.length - props.maxItems),
)
</script>

<template>
  <article class="hover-card">
    <header class="card-head">
      <span class="card-icon"><AppIcon :name="props.icon" :size="14" /></span>
      <div class="card-title">
        <strong>{{ props.title }}</strong>
        <small v-if="props.subtitle">{{ props.subtitle }}</small>
      </div>
      <span v-if="props.tags.length" class="card-tags">
        <em v-for="tag in props.tags" :key="tag">{{ tag }}</em>
      </span>
    </header>

    <dl v-if="props.meta.length" class="card-meta">
      <div v-for="item in props.meta" :key="item.label">
        <dt>{{ item.label }}</dt>
        <dd>{{ item.value }}</dd>
      </div>
    </dl>

    <section class="card-list">
      <div class="list-head">
        <span>{{ props.itemLabel }}</span>
        <i v-if="props.loading" class="spinner"></i>
      </div>

      <p v-if="props.loading && props.items.length === 0" class="list-empty">
        正在读取…
      </p>
      <p v-else-if="props.items.length === 0" class="list-empty">
        {{ props.emptyText }}
      </p>
      <ul v-else>
        <li v-for="item in visibleItems" :key="item.name">
          <code>{{ item.name }}</code>
          <span v-if="item.description">{{ item.description }}</span>
        </li>
      </ul>
      <p v-if="overflowCount > 0" class="list-more">还有 {{ overflowCount }} 项未展示</p>
    </section>

    <footer v-if="props.footer" class="card-foot">{{ props.footer }}</footer>
  </article>
</template>

<style scoped>
.hover-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: 100%;
  padding: 12px 13px;
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-md);
  background: var(--bg-elevated);
  box-shadow: 0 14px 32px rgba(2, 6, 14, 0.5);
  font-size: 11.5px;
  color: var(--text-2);
}

.card-head {
  display: flex;
  align-items: flex-start;
  gap: 9px;
}

.card-icon {
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  flex: none;
  border-radius: var(--radius-sm);
  color: var(--accent-cyan);
  background: transparent;
}

.card-title {
  flex: 1;
  min-width: 0;
}

.card-title strong {
  display: block;
  font-size: 12.5px;
  color: var(--text-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.card-title small {
  display: block;
  margin-top: 2px;
  font-size: 10.5px;
  line-height: 1.5;
  color: var(--text-3);
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.card-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  max-width: 130px;
  justify-content: flex-end;
}

.card-tags em {
  font-style: normal;
  padding: 1px 6px;
  font-size: 9.5px;
  color: var(--accent-cyan);
  border: 1px solid rgba(55, 208, 196, 0.24);
  border-radius: var(--radius-sm);
  white-space: nowrap;
}

.card-meta {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 5px 10px;
  margin: 0;
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  background: var(--bg-rail);
}

.card-meta div {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  min-width: 0;
}

.card-meta dt {
  font-size: 10px;
  color: var(--text-3);
  white-space: nowrap;
}

.card-meta dd {
  margin: 0;
  font-size: 10.5px;
  color: var(--text-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.card-list {
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-height: 40px;
}

.list-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 10px;
  color: var(--text-3);
}

.spinner {
  width: 10px;
  height: 10px;
  border: 1.5px solid transparent;
  border-top-color: var(--accent-cyan);
  border-radius: 50%;
  animation: hover-spin 0.7s linear infinite;
}

@keyframes hover-spin {
  to {
    transform: rotate(360deg);
  }
}

.card-list ul {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
  max-height: 148px;
  overflow: hidden;
}

.card-list li {
  display: flex;
  align-items: baseline;
  gap: 7px;
  min-width: 0;
  padding: 3px 0;
  border-bottom: 1px solid var(--line);
}

.card-list li:last-child {
  border-bottom: none;
}

.card-list code {
  flex: none;
  max-width: 46%;
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 10px;
  color: var(--accent-cyan);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.card-list li span {
  flex: 1;
  min-width: 0;
  font-size: 10px;
  line-height: 1.5;
  color: var(--text-3);
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
}

.list-empty,
.list-more {
  margin: 0;
  font-size: 10.5px;
  color: var(--text-3);
}

.list-more {
  padding-top: 2px;
  color: var(--accent-cyan);
}

.card-foot {
  font-size: 10px;
  color: var(--text-3);
  border-top: 1px solid var(--line);
  padding-top: 7px;
}
</style>
