<script setup lang="ts">
// 单条会话行：置顶标记 / 运行状态 / 更多操作（置顶、重命名、归档、删除）
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
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
  archive: [threadId: string]
}>()

const editing = ref(false)
const editTitle = ref('')
const inputEl = ref<HTMLInputElement | null>(null)

// 「更多」菜单挂在 body 上，避免被侧边栏滚动容器裁切。
interface MenuPosition {
  top?: number
  bottom?: number
  right: number
}
const menu = ref<MenuPosition | null>(null)

function toggleMenu(event: MouseEvent) {
  if (menu.value) {
    menu.value = null
    return
  }
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
  const right = Math.max(window.innerWidth - rect.right, 8)
  // 下方空间不足时向上展开，避免菜单落到底部被视口截断。
  const openUp = window.innerHeight - rect.bottom < 184
  menu.value = openUp
    ? { bottom: window.innerHeight - rect.top + 4, right }
    : { top: rect.bottom + 4, right }
}

function closeMenu() {
  menu.value = null
}

onMounted(() => {
  // 捕获阶段监听可以收到侧边栏内部的滚动事件。
  window.addEventListener('scroll', closeMenu, true)
  window.addEventListener('resize', closeMenu)
})

onBeforeUnmount(() => {
  window.removeEventListener('scroll', closeMenu, true)
  window.removeEventListener('resize', closeMenu)
})

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

function chooseRename() {
  closeMenu()
  void startRename()
}

function choosePin() {
  closeMenu()
  emit('pin', props.session.thread_id, !props.session.pinned)
}

function chooseArchive() {
  closeMenu()
  emit('archive', props.session.thread_id)
}

function chooseRemove() {
  closeMenu()
  emit('remove', props.session.thread_id)
}

</script>

<template>
  <div
    class="srow"
    :class="{
      active: props.active,
      running: props.running && !props.active,
      indent: props.indent,
      'menu-open': Boolean(menu),
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
        class="more"
        type="button"
        title="更多操作"
        aria-haspopup="menu"
        @click.stop="toggleMenu($event)"
      >
        <AppIcon name="more" :size="18" />
      </button>
    </div>

    <Teleport to="body">
      <div v-if="menu" class="row-menu-mask" @click.stop="closeMenu"></div>
      <div
        v-if="menu"
        class="row-menu"
        role="menu"
        :style="{
          top: menu.top === undefined ? undefined : `${menu.top}px`,
          bottom: menu.bottom === undefined ? undefined : `${menu.bottom}px`,
          right: `${menu.right}px`,
        }"
        @click.stop
      >
        <button type="button" role="menuitem" @click="choosePin">
          <AppIcon :name="props.session.pinned ? 'unpin' : 'pin'" :size="12" />
          <span>{{ props.session.pinned ? '取消置顶' : '置顶' }}</span>
        </button>
        <button type="button" role="menuitem" @click="chooseRename">
          <AppIcon name="edit" :size="12" />
          <span>重命名</span>
        </button>
        <button type="button" role="menuitem" @click="chooseArchive">
          <AppIcon name="archive" :size="12" />
          <span>归档</span>
        </button>
        <button type="button" role="menuitem" class="danger" @click="chooseRemove">
          <AppIcon name="trash" :size="12" />
          <span>删除</span>
        </button>
      </div>
    </Teleport>
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
  right: 2px;
  top: 1px;
  bottom: 1px;
  display: flex;
  align-items: stretch;
  padding-left: 10px;
  background: linear-gradient(90deg, transparent, var(--bg-panel) 32%);
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.15s ease;
}

.srow:hover .actions,
.srow.active .actions,
.srow.menu-open .actions,
.srow:focus-within .actions {
  opacity: 1;
  pointer-events: auto;
}

.srow:hover .time,
.srow.active .time,
.srow.menu-open .time,
.srow:focus-within .time {
  opacity: 0;
}

/* 更多按钮：上下各留 1px，尽量撑满行高，让图标更大更醒目 */
.more {
  display: grid;
  place-items: center;
  width: 26px;
  margin: 1px 0;
  padding: 0;
  color: var(--text-3);
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
}

.more:hover {
  color: var(--accent-cyan);
  background: var(--bg-hover);
}

.more :deep(svg) {
  width: 18px;
  height: 18px;
}

/* 更多操作菜单：Teleport 到 body，避免被侧边栏滚动容器裁切 */
.row-menu-mask {
  position: fixed;
  inset: 0;
  z-index: 74;
}

.row-menu {
  position: fixed;
  z-index: 75;
  display: flex;
  flex-direction: column;
  gap: 1px;
  width: 136px;
  padding: 4px;
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-md);
  background: var(--bg-elevated);
  box-shadow: 0 8px 24px rgba(2, 6, 14, 0.5);
}

.row-menu button {
  display: flex;
  align-items: center;
  gap: 7px;
  width: 100%;
  padding: 6px 8px;
  font-size: 11px;
  text-align: left;
  color: var(--text-2);
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
}

.row-menu button:hover {
  color: var(--text-1);
  background: var(--bg-hover);
}

.row-menu button :deep(svg) {
  flex: none;
  color: var(--accent-cyan);
}

.row-menu button.danger {
  color: var(--danger);
}

.row-menu button.danger :deep(svg) {
  color: var(--danger);
}

.row-menu button.danger:hover {
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
