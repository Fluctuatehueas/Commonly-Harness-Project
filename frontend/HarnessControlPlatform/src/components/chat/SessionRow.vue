<script setup lang="ts">
// 单条会话行：置顶标记 / 运行状态 / 重命名 / 删除
import { nextTick, ref } from 'vue'
import AppIcon from '@/components/console/AppIcon.vue'
import type { SessionItem } from '@/stores/sessions'

const props = withDefaults(
  defineProps<{
    session: SessionItem
    active: boolean
    running: boolean
    waiting: boolean
    indent?: boolean
  }>(),
  { indent: false },
)

const emit = defineEmits<{
  select: [threadId: string]
  rename: [threadId: string, title: string]
  remove: [threadId: string]
  pin: [threadId: string, pinned: boolean]
}>()

const editing = ref(false)
const editTitle = ref('')
const inputEl = ref<HTMLInputElement | null>(null)

function fmtTime(iso: string) {
  try {
    const d = new Date(iso)
    const now = new Date()
    if (d.toDateString() === now.toDateString()) {
      return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
    }
    return `${d.getMonth() + 1}/${d.getDate()}`
  } catch {
    return ''
  }
}

async function startRename() {
  editing.value = true
  editTitle.value = props.session.title || '新对话'
  await nextTick()
  inputEl.value?.focus()
  inputEl.value?.select()
}

function commitRename() {
  if (!editing.value) return
  const title = editTitle.value.trim()
  editing.value = false
  if (title && title !== props.session.title) {
    emit('rename', props.session.thread_id, title)
  }
}

</script>

<template>
  <div
    class="srow"
    :class="{
      active: props.active,
      running: props.running && !props.active,
      indent: props.indent,
    }"
    role="button"
    tabindex="0"
    @click="emit('select', props.session.thread_id)"
    @keydown.enter="emit('select', props.session.thread_id)"
  >
    <span class="flag" aria-hidden="true">
      <AppIcon v-if="props.session.pinned" name="pin" :size="10" />
      <i v-else-if="props.waiting" class="dot wait"></i>
      <i v-else-if="props.running" class="dot run"></i>
    </span>

    <input
      v-if="editing"
      ref="inputEl"
      v-model="editTitle"
      class="rename"
      type="text"
      maxlength="120"
      @click.stop
      @keydown.enter.stop.prevent="commitRename"
      @keydown.esc.stop.prevent="editing = false"
      @blur="commitRename"
    />
    <strong v-else class="title">{{ props.session.title || '新对话' }}</strong>

    <span class="time mono">{{ fmtTime(props.session.updated_at) }}</span>

    <div class="actions">
      <button
        class="act"
        type="button"
        :class="{ pinned: props.session.pinned }"
        :title="props.session.pinned ? '取消置顶' : '置顶'"
        @click.stop="emit('pin', props.session.thread_id, !props.session.pinned)"
      >
        <AppIcon :name="props.session.pinned ? 'unpin' : 'pin'" :size="10" />
      </button>
      <button class="act" type="button" title="重命名" @click.stop="startRename">
        <AppIcon name="edit" :size="10" />
      </button>
      <button
        class="act del"
        type="button"
        title="删除"
        @click.stop="emit('remove', props.session.thread_id)"
      >
        <AppIcon name="close" :size="10" />
      </button>
    </div>
  </div>
</template>

<style scoped>
.srow {
  position: relative;
  display: flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 6px 0 7px;
  border-left: 2px solid transparent;
  color: var(--text-2);
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease;
}

.srow.indent {
  padding-left: 20px;
}

.srow:hover {
  color: var(--text-1);
  background: var(--bg-hover);
}

.srow.active {
  color: var(--text-1);
  border-left-color: var(--accent);
  background: var(--bg-active);
}

.srow.running .title::after {
  content: '';
  display: inline-block;
  width: 4px;
  height: 4px;
  margin-left: 5px;
  vertical-align: 2px;
  border-radius: 50%;
  background: var(--accent-cyan);
  animation: pulse 1.4s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 0.4; }
  50% { opacity: 1; }
}

.flag {
  display: grid;
  place-items: center;
  width: 12px;
  flex: none;
  color: var(--accent-cyan);
}

.dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--accent-cyan);
}

.dot.wait {
  background: var(--amber);
}

.title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  font-size: 12px;
  font-weight: 400;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.time {
  flex: none;
  font-size: 9.5px;
  color: var(--text-3);
}

.actions {
  position: absolute;
  right: 4px;
  top: 50%;
  display: flex;
  align-items: center;
  gap: 1px;
  padding-left: 14px;
  transform: translateY(-50%);
  background: linear-gradient(90deg, transparent, var(--bg-panel) 28%);
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.15s ease;
}

.srow:hover .actions,
.srow.active .actions,
.srow:focus-within .actions {
  opacity: 1;
  pointer-events: auto;
}

.srow:hover .time,
.srow.active .time,
.srow:focus-within .time {
  opacity: 0;
}

.act {
  display: grid;
  place-items: center;
  width: 20px;
  height: 20px;
  padding: 0;
  color: var(--text-3);
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
}

.act:hover {
  color: var(--accent-cyan);
  background: var(--bg-hover);
}

.act.pinned {
  color: var(--accent-cyan);
}

.del:hover {
  color: var(--danger);
  background: rgba(226, 96, 94, 0.12);
}

.rename {
  flex: 1;
  min-width: 0;
  height: 22px;
  padding: 0 6px;
  font-size: 12px;
  font-family: inherit;
  color: var(--text-1);
  border: 1px solid var(--accent);
  border-radius: var(--radius-sm);
  background: var(--bg-deep);
  outline: none;
}

.menu-mask {
  position: fixed;
  inset: 0;
  z-index: 35;
}

.move-menu {
  position: absolute;
  top: calc(100% - 2px);
  right: 4px;
  z-index: 36;
  display: flex;
  flex-direction: column;
  gap: 1px;
  width: 168px;
  padding: 4px;
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-md);
  background: var(--bg-elevated);
  box-shadow: 0 8px 24px rgba(2, 6, 14, 0.5);
}

.move-menu button {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 6px 8px;
  font-size: 11px;
  text-align: left;
  color: var(--text-2);
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
}

.move-menu button span {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.move-menu button:hover:not(:disabled) {
  color: var(--text-1);
  background: var(--bg-hover);
}

.move-menu button:disabled {
  color: var(--text-3);
  cursor: default;
}

.move-menu :deep(svg) {
  color: var(--accent-cyan);
  flex: none;
}

.move-menu .none :deep(svg) {
  color: var(--text-3);
}

.menu-empty {
  margin: 0;
  padding: 6px 8px;
  font-size: 10.5px;
  color: var(--text-3);
}
</style>
