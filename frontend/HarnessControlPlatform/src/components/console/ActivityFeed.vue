<script setup lang="ts">
// 最新活动列表
import AppIcon from './AppIcon.vue'
import { ACTIVITIES } from '@/mock/dashboard'
</script>

<template>
  <section class="panel activity">
    <div class="panel-head">
      <div class="panel-title">
        <span class="title-icon"><AppIcon name="clock" :size="14" /></span>
        最新活动
      </div>
      <a class="panel-link" href="#">查看全部 <AppIcon name="chevronRight" :size="12" /></a>
    </div>

    <ul class="list">
      <li v-for="a in ACTIVITIES" :key="a.key">
        <i class="dot" :style="{ background: a.color, boxShadow: `0 0 6px ${a.color}` }"></i>
        <div class="body">
          <div class="line">
            <strong>{{ a.title }}</strong>
            <span class="time">{{ a.time }}</span>
          </div>
          <p v-if="a.desc">{{ a.desc }}</p>
        </div>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.activity {
  padding-bottom: 10px;
}

.list {
  list-style: none;
  margin: 10px 14px 0;
  padding: 0;
}

.list li {
  display: flex;
  gap: 10px;
  padding: 9px 2px;
  border-bottom: 1px solid rgba(86, 140, 255, 0.07);
}

.list li:last-child {
  border-bottom: none;
}

.dot {
  width: 7px;
  height: 7px;
  margin-top: 5px;
  border-radius: 50%;
  flex: none;
}

.body {
  flex: 1;
  min-width: 0;
}

.line {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}

.line strong {
  font-size: 12.5px;
  font-weight: 500;
}

.time {
  font-size: 10.5px;
  color: var(--text-3);
  flex: none;
}

.body p {
  margin: 3px 0 0;
  font-size: 11px;
  color: var(--text-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
