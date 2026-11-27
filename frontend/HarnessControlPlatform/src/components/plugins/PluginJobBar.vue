<script setup lang="ts">
import { computed } from 'vue'
import { Close } from '@element-plus/icons-vue'
import type { PluginJob } from '@/types/plugins'

const props = defineProps<{ jobs: PluginJob[] }>()
const emit = defineEmits<{ dismiss: [] }>()

const visible = computed(() => props.jobs.filter((job) => job.status !== 'done' || job.progress < 100))
const activeCount = computed(
  () => props.jobs.filter((job) => job.status === 'queued' || job.status === 'running').length,
)
const failed = computed(() => props.jobs.filter((job) => job.status === 'error'))
const statusText = (job: PluginJob) => {
  if (job.status === 'error') return job.error || '安装失败'
  return job.message || job.stage
}
</script>

<template>
  <div v-if="props.jobs.length" class="job-bar">
    <header class="job-head">
      <span class="job-title">
        插件安装任务
        <span v-if="activeCount" class="badge">{{ activeCount }}</span>
        <span v-if="failed.length" class="badge danger">{{ failed.length }} 失败</span>
      </span>
      <el-button text circle size="small" :icon="Close" title="收起" @click="emit('dismiss')" />
    </header>
    <div class="job-list">
      <div v-for="job in visible.length ? visible : props.jobs" :key="job.job_id" class="job-row">
        <div class="job-label">
          <span class="name">{{ job.label || job.plugin_id || job.job_id }}</span>
          <span class="msg" :class="{ err: job.status === 'error' }">{{ statusText(job) }}</span>
        </div>
        <el-progress
          :percentage="job.progress"
          :status="job.status === 'error' ? 'exception' : job.status === 'done' ? 'success' : undefined"
          :stroke-width="5"
          :show-text="false"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.job-bar {
  position: fixed;
  right: 20px;
  bottom: 18px;
  z-index: 2400;
  width: min(380px, calc(100vw - 40px));
  background: var(--bg-panel);
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-lg);
  box-shadow: 0 16px 40px rgba(0, 0, 0, 0.4);
  overflow: hidden;
}
.job-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 9px 12px;
  border-bottom: 1px solid var(--line);
}
.job-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-1);
}
.badge {
  padding: 0 6px;
  border-radius: 8px;
  background: var(--bg-active);
  color: var(--accent);
  font-size: 10px;
}
.badge.danger {
  background: rgba(226, 96, 94, 0.16);
  color: var(--danger);
}
.job-list {
  max-height: 180px;
  overflow-y: auto;
}
.job-row {
  padding: 9px 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  border-bottom: 1px solid var(--line);
}
.job-row:last-child {
  border-bottom: none;
}
.job-label {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  font-size: 11px;
}
.name {
  color: var(--text-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.msg {
  color: var(--text-3);
  flex-shrink: 0;
}
.msg.err {
  color: var(--danger);
}
</style>
