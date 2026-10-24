<script setup lang="ts">
// 总览仪表盘：左主栏 + 右信息栏
import TopNav from '@/components/console/TopNav.vue'
import HeroBanner from '@/components/console/HeroBanner.vue'
import ModelOverview from '@/components/console/ModelOverview.vue'
import AgentFlow from '@/components/console/AgentFlow.vue'
import OrbitalBanner from '@/components/console/OrbitalBanner.vue'
import SystemStatus from '@/components/console/SystemStatus.vue'
import QuickEntry from '@/components/console/QuickEntry.vue'
import ActivityFeed from '@/components/console/ActivityFeed.vue'
</script>

<template>
  <div class="app-shell">
    <TopNav />
    <div class="content">
      <div class="col-left">
        <HeroBanner />
        <ModelOverview />
        <AgentFlow />
        <OrbitalBanner />
      </div>
      <div class="col-right">
        <SystemStatus />
        <QuickEntry />
        <ActivityFeed />
      </div>
    </div>
  </div>
</template>

<style scoped>
.app-shell {
  min-height: 100vh;
}

.content {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
  gap: 16px;
  align-items: start;
  padding: 18px 22px 26px;
}

.col-left,
.col-right {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

@media (max-width: 1180px) {
  .content {
    grid-template-columns: 1fr;
  }
}
</style>
