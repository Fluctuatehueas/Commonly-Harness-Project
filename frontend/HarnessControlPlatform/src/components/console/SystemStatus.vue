<script setup lang="ts">
// 系统状态卡片：GPU / 内存环形指标 + 状态行
import AppIcon from './AppIcon.vue'
import type { IconName } from './icons'

interface Ring {
  key: string
  label: string
  value: number
  icon: IconName
  color: string
}

const rings: Ring[] = [
  { key: 'gpu', label: 'GPU 使用率', value: 42, icon: 'cpu', color: '#35e0ff' },
  { key: 'mem', label: '内存使用率', value: 68, icon: 'memory', color: '#4f8cff' },
]

const rows: { label: string; value: string; icon: IconName; accent?: boolean }[] = [
  { label: '大模型服务', value: '正常', icon: 'server', accent: true },
  { label: 'Agent 运行数', value: '3 / 8', icon: 'bot' },
  { label: '在线用户', value: '1', icon: 'users' },
  { label: '运行时长', value: '12天 4小时', icon: 'timer' },
]

const R = 26
const C = 2 * Math.PI * R
</script>

<template>
  <section class="panel status">
    <div class="panel-head">
      <div class="panel-title">
        <span class="title-icon"><AppIcon name="shield" :size="14" /></span>
        系统状态
      </div>
      <span class="running"><i class="dot"></i>运行中</span>
    </div>

    <div class="rings">
      <div v-for="r in rings" :key="r.key" class="ring-item">
        <div class="ring">
          <svg viewBox="0 0 64 64">
            <circle class="track" cx="32" cy="32" :r="R" />
            <circle
              class="bar"
              cx="32"
              cy="32"
              :r="R"
              :stroke="r.color"
              :stroke-dasharray="C"
              :stroke-dashoffset="C * (1 - r.value / 100)"
            />
          </svg>
          <div class="ring-center">
            <AppIcon :name="r.icon" :size="13" />
            <strong>{{ r.value }}%</strong>
          </div>
        </div>
        <span class="ring-label">{{ r.label }}</span>
      </div>
    </div>

    <ul class="rows">
      <li v-for="row in rows" :key="row.label">
        <span class="row-label">
          <AppIcon :name="row.icon" :size="14" />
          {{ row.label }}
        </span>
        <strong :class="{ accent: row.accent }">{{ row.value }}</strong>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.status {
  padding-bottom: 14px;
}

.running {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  color: var(--green);
}

.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--green);
  box-shadow: 0 0 6px var(--green);
}

.rings {
  display: flex;
  justify-content: space-around;
  padding: 16px 12px 6px;
}

.ring-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
}

.ring {
  position: relative;
  width: 72px;
  height: 72px;
}

.ring svg {
  width: 100%;
  height: 100%;
  transform: rotate(-90deg);
}

.track {
  fill: none;
  stroke: rgba(70, 110, 180, 0.18);
  stroke-width: 6;
}

.bar {
  fill: none;
  stroke-width: 6;
  stroke-linecap: round;
  transition: stroke-dashoffset 0.6s;
}

.ring-center {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 1px;
  color: var(--text-2);
}

.ring-center strong {
  font-size: 14px;
  color: var(--text-1);
}

.ring-label {
  font-size: 11px;
  color: var(--text-3);
}

.rows {
  list-style: none;
  margin: 6px 14px 0;
  padding: 0;
  border-top: 1px solid var(--line);
}

.rows li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 9px 2px;
  font-size: 12px;
  border-bottom: 1px solid rgba(86, 140, 255, 0.07);
}

.rows li:last-child {
  border-bottom: none;
}

.row-label {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  color: var(--text-2);
}

.row-label svg {
  color: var(--text-3);
}

.rows strong {
  font-weight: 500;
  color: var(--text-1);
}

.rows strong.accent {
  color: var(--green);
}
</style>
