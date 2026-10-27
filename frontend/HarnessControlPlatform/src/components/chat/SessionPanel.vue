<script setup lang="ts">
// 会话栏：读取会话列表，支持切换/新建/重命名/置顶/删除
import { nextTick, ref } from 'vue'
import AppIcon from '@/components/console/AppIcon.vue'
import { useSessionStore, type SessionItem } from '@/stores/sessions'

const sessions = useSessionStore()

const emit = defineEmits<{
  select: [threadId: string]
  create: []
  remove: [threadId: string]
  rename: [threadId: string, title: string]
  pin: [threadId: string, pinned: boolean]
}>()

const editingId = ref<string | null>(null)
const editTitle = ref('')

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

async function startRename(session: SessionItem) {
  editingId.value = session.thread_id
  editTitle.value = session.title || '新会话'
  await nextTick()
  const input = document.querySelector<HTMLInputElement>('.rename-input')
  input?.focus()
  input?.select()
}

function cancelRename() {
  editingId.value = null
}

function commitRename(session: SessionItem) {
  // blur 与 Enter 可能同时触发，用 editingId 保证只提交一次
  if (editingId.value !== session.thread_id) return
  const title = editTitle.value.trim()
  editingId.value = null
  if (title && title !== session.title) {
    emit('rename', session.thread_id, title)
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
        :class="{
          active: sessions.activeThreadId === s.thread_id,
          running: sessions.runningThreads.has(s.thread_id),
        }"
        role="button"
        tabindex="0"
        @click="emit('select', s.thread_id)"
        @keydown.enter="emit('select', s.thread_id)"
      >
        <!-- 左侧固定占位：仅置顶会话展示 Pin，其余保持空白但不塌陷 -->
        <span class="s-slot" aria-hidden="true">
          <AppIcon v-if="s.pinned" name="pin" :size="12" />
        </span>

        <div class="s-meta">
          <input
            v-if="editingId === s.thread_id"
            v-model="editTitle"
            class="rename-input"
            type="text"
            maxlength="120"
            @click.stop
            @keydown.enter.stop.prevent="commitRename(s)"
            @keydown.esc.stop.prevent="cancelRename"
            @blur="commitRename(s)"
          />
          <strong v-else>{{ s.title || '新会话' }}</strong>
          <span>
            <i v-if="sessions.runningThreads.has(s.thread_id)" class="run-dot"></i>
            {{ fmtTime(s.updated_at) }} · {{ s.message_count }} 条
          </span>
        </div>

        <span
          v-if="sessions.waitingThreads.has(s.thread_id)"
          class="wait-tag"
          title="等待人工介入"
        ><i class="dot"></i></span>

        <div class="s-actions">
          <button
            class="act"
            type="button"
            title="重命名"
            @click.stop="startRename(s)"
          >
            <AppIcon name="edit" :size="11" />
          </button>
          <button
            class="act"
            type="button"
            :title="s.pinned ? '取消置顶' : '置顶会话'"
            :class="{ pinned: s.pinned }"
            @click.stop="emit('pin', s.thread_id, !s.pinned)"
          >
            <AppIcon :name="s.pinned ? 'unpin' : 'pin'" :size="11" />
          </button>
          <button
            class="act del"
            type="button"
            title="删除会话"
            @click.stop="emit('remove', s.thread_id)"
          >
            <AppIcon name="close" :size="11" />
          </button>
        </div>
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
  gap: 7px;
  padding: 8px 8px 8px 6px;
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

/* 运行中：边框呼吸，不再用左侧图标表达 */
.session-item.running {
  border-color: rgba(53, 224, 255, 0.3);
  box-shadow: inset 0 0 14px rgba(53, 224, 255, 0.08);
  animation: running-glow 1.6s ease-in-out infinite;
}

@keyframes running-glow {
  0%, 100% { box-shadow: inset 0 0 10px rgba(53, 224, 255, 0.06); }
  50% { box-shadow: inset 0 0 18px rgba(53, 224, 255, 0.16); }
}

/* 固定占位：置顶显示 Pin，其余完全空白 */
.s-slot {
  display: grid;
  place-items: center;
  width: 16px;
  height: 24px;
  flex: none;
  color: var(--accent-cyan);
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
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
  overflow: hidden;
  font-size: 10.5px;
  color: var(--text-3);
  white-space: nowrap;
  text-overflow: ellipsis;
}

.run-dot {
  display: inline-block;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--accent-cyan);
  box-shadow: 0 0 6px var(--accent-cyan);
  animation: dot-pulse 1.2s ease-in-out infinite;
}

@keyframes dot-pulse {
  0%, 100% { opacity: 0.35; }
  50% { opacity: 1; }
}

.rename-input {
  width: 100%;
  padding: 2px 6px;
  font-size: 12px;
  font-family: inherit;
  color: var(--text-1);
  border: 1px solid var(--accent);
  border-radius: 6px;
  background: rgba(6, 13, 28, 0.9);
  outline: none;
}

.wait-tag .dot {
  display: inline-block;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--amber);
  box-shadow: 0 0 7px var(--amber);
  animation: dot-pulse 1.4s infinite;
}

/* 右侧操作区：透明背景，低透明度常驻，悬停提亮 */
.s-actions {
  display: flex;
  align-items: center;
  gap: 1px;
  flex: none;
}

.act {
  display: grid;
  place-items: center;
  width: 20px;
  height: 20px;
  padding: 0;
  color: var(--text-3);
  border: none;
  border-radius: 6px;
  background: transparent;
  opacity: 0.55;
  transition: all 0.15s;
}

/* 删除按钮默认不占位，仅悬停/选中时出现 */
.del {
  display: none;
}

.session-item:hover .act,
.session-item.active .act,
.act.pinned {
  opacity: 1;
}

.session-item:hover .del,
.session-item.active .del {
  display: grid;
  opacity: 1;
}

.act:hover {
  color: var(--accent-cyan);
  background: rgba(53, 224, 255, 0.12);
}

.act.pinned {
  color: var(--accent-cyan);
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
