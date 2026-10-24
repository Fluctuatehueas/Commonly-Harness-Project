<script setup lang="ts">
// 会话栏：从后端读取会话列表，切换/新建/删除，运行中与等待介入动画
import AppIcon from '@/components/console/AppIcon.vue'
import { useSessionStore } from '@/stores/sessions'

const sessions = useSessionStore()

const emit = defineEmits<{
  select: [threadId: string]
  create: []
  remove: [threadId: string]
}>()

function fmtTime(iso: string) {
  try {
    const d = new Date(iso)
    const now = new Date()
    const sameDay = d.toDateString() === now.toDateString()
    if (sameDay) return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
    return `${d.getMonth() + 1}/${d.getDate()}`
  } catch {
    return ''
  }
}
</script>

<template>
  <aside class="session-panel">
    <div class="sp-head">
      <button class="new-btn" type="button" @click="emit('create')">
        <AppIcon name="plus" :size="14" /> 新会话
      </button>
      <button
        class="icon-btn"
        type="button"
        title="刷新列表"
        :disabled="sessions.loading"
        @click="sessions.refresh()"
      >
        <AppIcon name="timer" :size="13" />
      </button>
    </div>

    <div v-if="sessions.offline" class="sp-note">后端离线，会话列表不可用</div>
    <div v-else-if="sessions.loading && sessions.sessions.length === 0" class="sp-note">加载中…</div>
    <div v-else-if="sessions.sessions.length === 0" class="sp-note">还没有会话，发一条消息开始</div>

    <div class="sp-list">
      <div
        v-for="s in sessions.sessions"
        :key="s.thread_id"
        class="session-item"
        :class="{ active: sessions.activeThreadId === s.thread_id }"
        role="button"
        tabindex="0"
        @click="emit('select', s.thread_id)"
        @keydown.enter="emit('select', s.thread_id)"
      >
        <span class="s-icon" :class="{ running: sessions.runningThreads.has(s.thread_id) }">
          <AppIcon :name="sessions.runningThreads.has(s.thread_id) ? 'play' : 'message'" :size="13" />
        </span>
        <div class="s-meta">
          <strong>{{ s.title || '新会话' }}</strong>
          <span>{{ fmtTime(s.updated_at) }} · {{ s.message_count }} 条</span>
        </div>
        <span
          v-if="sessions.waitingThreads.has(s.thread_id)"
          class="wait-tag"
          title="等待人工介入"
        ><i class="dot"></i></span>
        <button
          class="del"
          type="button"
          title="删除会话"
          @click.stop="emit('remove', s.thread_id)"
        >
          <AppIcon name="close" :size="11" />
        </button>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.session-panel {
  width: 232px;
  flex: none;
  display: flex;
  flex-direction: column;
  min-height: 0;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: rgba(9, 18, 38, 0.78);
  overflow: hidden;
}

.sp-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px;
  border-bottom: 1px solid var(--line);
}

.new-btn {
  flex: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 8px 10px;
  font-size: 12.5px;
  color: #fff;
  border: none;
  border-radius: 9px;
  background: linear-gradient(90deg, #3b78ff, #2a5cd6);
  box-shadow: 0 4px 14px rgba(50, 110, 255, 0.3);
  transition: filter 0.18s;
}

.new-btn:hover {
  filter: brightness(1.12);
}

.icon-btn {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  border: 1px solid var(--line);
  border-radius: 9px;
  color: var(--text-2);
  background: transparent;
  transition: all 0.18s;
}

.icon-btn:hover {
  color: var(--text-1);
  border-color: var(--line-strong);
}

.icon-btn:disabled {
  opacity: 0.5;
}

.sp-note {
  padding: 18px 14px;
  font-size: 11.5px;
  color: var(--text-3);
  text-align: center;
}

.sp-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.session-item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 9px 10px;
  border: 1px solid transparent;
  border-radius: 10px;
  cursor: pointer;
  transition: all 0.15s;
}

.session-item:hover {
  background: rgba(79, 140, 255, 0.08);
}

.session-item.active {
  border-color: var(--line-strong);
  background: rgba(47, 100, 200, 0.16);
}

.s-icon {
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  border-radius: 8px;
  color: var(--text-2);
  background: rgba(20, 38, 74, 0.6);
  flex: none;
}

/* 运行中：图标呼吸 + 旋转描边 */
.s-icon.running {
  color: var(--accent-cyan);
  animation: pulse 1.1s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(53, 224, 255, 0.5); }
  50% { box-shadow: 0 0 0 5px rgba(53, 224, 255, 0); }
}

.s-meta {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  line-height: 1.3;
}

.s-meta strong {
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.s-meta span {
  font-size: 10.5px;
  color: var(--text-3);
}

.wait-tag .dot {
  display: inline-block;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--amber);
  box-shadow: 0 0 7px var(--amber);
  animation: pulse 1.4s infinite;
}

.del {
  display: none;
  place-items: center;
  width: 20px;
  height: 20px;
  border: none;
  border-radius: 6px;
  color: var(--text-3);
  background: transparent;
  flex: none;
}

.session-item:hover .del {
  display: grid;
}

.del:hover {
  color: #ff6b6b;
  background: rgba(255, 93, 93, 0.12);
}

@media (max-width: 900px) {
  .session-panel {
    width: 180px;
  }
}
</style>
