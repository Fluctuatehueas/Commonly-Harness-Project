<script setup lang="ts">
// 文件抽屉：按本机/沙箱分组 + 目录树；本机文件定位到资源管理器，沙箱文件下载。
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import AppIcon from '@/components/console/AppIcon.vue'
import MarkdownView from './MarkdownView.vue'
import FileTreeNode from './FileTreeNode.vue'
import { buildGroups, type FileGroup } from './fileTree'
import { useFileStore, type FileMeta } from '@/stores/files'
import { absoluteWorkspacePath, revealFile } from '@/api/workspace'

const props = defineProps<{ threadId: string | null; refreshKey?: number }>()
const emit = defineEmits<{ close: [] }>()
const files = useFileStore()

const drawerWidth = ref(440)
const resizing = ref(false)
let resizeStartX = 0
let resizeStartWidth = 0

const groups = computed(() => buildGroups(files.files))
const newCount = computed(
  () => files.files.filter((file) => files.isNew(props.threadId, file)).length,
)

const isMarkdown = computed(() =>
  /\.(?:md|markdown|mdx)$/i.test(files.current?.path || ''),
)
const previewMode = ref(false)
const notice = ref('')
let noticeTimer: number | undefined

watch(
  () => [props.threadId, props.refreshKey] as const,
  ([id]) => {
    files.closePreview()
    if (id) void files.list(id)
  },
  { immediate: true },
)

watch(
  () => files.current?.path,
  (path) => {
    previewMode.value = /\.(?:md|markdown|mdx)$/i.test(path || '')
  },
)

function reload() {
  if (props.threadId) void files.list(props.threadId)
}

function flash(message: string) {
  notice.value = message
  if (noticeTimer) window.clearTimeout(noticeTimer)
  noticeTimer = window.setTimeout(() => (notice.value = ''), 2600)
}

function isNew(file: FileMeta) {
  return files.isNew(props.threadId, file)
}

function groupNewCount(group: FileGroup) {
  return group.files.filter((file) => isNew(file)).length
}

function localPath(file: Pick<FileMeta, 'path' | 'origin'>): string {
  if (file.origin !== 'project' || !files.workspace) return ''
  return absoluteWorkspacePath(files.workspace, file.path)
}

async function openInExplorer(file: Pick<FileMeta, 'path' | 'origin'>) {
  const target = localPath(file)
  if (!target) {
    flash('当前没有可用的本机项目目录')
    return
  }
  if (await revealFile(target)) return
  try {
    await navigator.clipboard.writeText(target)
    flash(`无法直接定位文件，路径已复制：${target}`)
  } catch {
    flash(target)
  }
}

function handleAction(file: FileMeta) {
  if (file.origin === 'project') {
    void openInExplorer(file)
    return
  }
  if (props.threadId) void files.download(props.threadId, file.path, file.origin)
}

function handleOpen(file: FileMeta) {
  if (props.threadId) void files.open(props.threadId, file.path, file.origin)
}

function previewAction() {
  if (!files.current) return
  if (files.current.origin === 'project') {
    void openInExplorer(files.current)
    return
  }
  if (props.threadId) {
    void files.download(props.threadId, files.current.path, files.current.origin)
  }
}

function onResizeMove(event: MouseEvent) {
  drawerWidth.value = Math.min(
    760,
    Math.max(340, resizeStartWidth + (resizeStartX - event.clientX)),
  )
}

function stopResize() {
  resizing.value = false
  window.removeEventListener('mousemove', onResizeMove)
  window.removeEventListener('mouseup', stopResize)
}

function startResize(event: MouseEvent) {
  event.preventDefault()
  resizing.value = true
  resizeStartX = event.clientX
  resizeStartWidth = drawerWidth.value
  window.addEventListener('mousemove', onResizeMove)
  window.addEventListener('mouseup', stopResize)
}

onBeforeUnmount(stopResize)
</script>

<template>
  <aside class="file-panel" :class="{ resizing }" :style="{ width: `${drawerWidth}px` }">
    <div class="resizer" title="拖动调整宽度" @mousedown="startResize"></div>

    <header class="fp-head">
      <span class="title-icon"><AppIcon name="file" :size="14" /></span>
      <strong>会话文件</strong>
      <span class="src">{{ files.source === 'project+sandbox' ? '本机 + 沙箱' : '沙箱' }}</span>
      <span v-if="newCount" class="new-tag head-new">本轮 +{{ newCount }} new</span>
      <button class="icon-btn" type="button" title="刷新" @click="reload">
        <AppIcon name="timer" :size="14" />
      </button>
      <button class="icon-btn" type="button" title="关闭" @click="emit('close')">
        <AppIcon name="close" :size="14" />
      </button>
    </header>

    <div v-if="files.error" class="fp-error">{{ files.error }}</div>
    <p v-if="notice" class="fp-notice mono">{{ notice }}</p>

    <div v-if="!files.current" class="fp-list">
      <p class="fp-hint">
        {{ files.projectId
          ? '项目会话同时展示本机项目目录与沙箱工作目录：本机文件可在资源管理器定位，沙箱文件可下载。'
          : '自由会话的 AI 产物保留在沙箱内，可在这里预览与下载。' }}
      </p>

      <div v-if="files.loading" class="fp-empty">加载中…</div>
      <div v-else-if="files.files.length === 0" class="fp-empty">
        当前会话还没有文件。让 Agent 写一个文件试试，例如“把刚才的结论保存为 notes.md”。
      </div>

      <section v-for="group in groups" :key="group.origin" class="file-group">
        <header class="group-head">
          <AppIcon name="folder" :size="13" class="group-icon" />
          <span class="group-label">{{ group.label }}</span>
          <span class="group-count mono">{{ group.files.length }}</span>
          <span v-if="groupNewCount(group)" class="new-tag">本轮 +{{ groupNewCount(group) }}</span>
        </header>
        <div class="group-tree">
          <FileTreeNode
            v-for="node in group.tree"
            :key="node.path"
            :node="node"
            :depth="0"
            :is-new="isNew"
            @open="handleOpen"
            @action="handleAction"
          />
        </div>
      </section>
    </div>

    <template v-else>
      <div class="fp-editor-head">
        <code class="f-path">{{ files.current.path }}</code>
        <div class="fp-actions">
          <span class="f-origin" :class="files.current.origin">
            {{ files.current.origin === 'project' ? '本机' : '沙箱' }}
          </span>
          <button class="mini" type="button" @click="files.closePreview()">
            <AppIcon name="chevronRight" :size="12" style="transform: rotate(180deg)" /> 列表
          </button>
          <button v-if="isMarkdown" class="mini" type="button" @click="previewMode = !previewMode">
            <AppIcon :name="previewMode ? 'edit' : 'eye'" :size="12" />
            {{ previewMode ? '源码' : '预览' }}
          </button>
          <button class="mini" type="button" @click="previewAction">
            <AppIcon :name="files.current.origin === 'project' ? 'external' : 'download'" :size="12" />
            {{ files.current.origin === 'project' ? '在资源管理器打开' : '下载' }}
          </button>
        </div>
      </div>
      <div v-if="previewMode && isMarkdown" class="fp-preview">
        <MarkdownView :content="files.current.content" />
      </div>
      <pre v-else class="fp-text">{{ files.current.content }}</pre>
    </template>
  </aside>
</template>

<style scoped>
.file-panel {
  position: fixed;
  top: var(--topbar-h);
  right: 0;
  bottom: 0;
  z-index: 45;
  display: flex;
  flex-direction: column;
  min-width: 340px;
  overflow: hidden;
  border-left: 1px solid var(--line-strong);
  background: rgba(6, 14, 26, 0.72);
  backdrop-filter: blur(18px);
  box-shadow: -18px 0 40px rgba(2, 8, 20, 0.45);
}

.file-panel.resizing {
  user-select: none;
}

.resizer {
  position: absolute;
  top: 0;
  left: -4px;
  bottom: 0;
  width: 8px;
  cursor: col-resize;
}

.fp-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 14px;
  border-bottom: 1px solid var(--line);
}

.fp-head strong {
  font-size: 13.5px;
}

.title-icon {
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  border-radius: 6px;
  color: var(--accent-cyan);
  background: rgba(53, 224, 255, 0.08);
}

.src {
  margin-left: auto;
  font-size: 10px;
  color: var(--text-3);
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 2px 8px;
}

.head-new {
  margin-left: 0;
}

.icon-btn {
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  color: var(--text-2);
  border: 1px solid var(--line);
  border-radius: 7px;
  background: transparent;
}

.fp-error {
  margin: 10px 12px 0;
  padding: 8px 10px;
  font-size: 11.5px;
  color: var(--amber);
  border: 1px solid rgba(255, 182, 72, 0.3);
  border-radius: 8px;
  background: rgba(255, 182, 72, 0.05);
}

.fp-notice {
  margin: 8px 12px 0;
  padding: 6px 9px;
  overflow: hidden;
  font-size: 9.5px;
  color: var(--accent-cyan);
  text-overflow: ellipsis;
  white-space: nowrap;
  border: 1px solid rgba(53, 224, 255, 0.2);
  border-radius: 7px;
  background: rgba(53, 224, 255, 0.05);
}

.fp-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.fp-hint {
  margin: 2px 6px 4px;
  font-size: 10.5px;
  line-height: 1.6;
  color: var(--text-3);
}

.fp-empty {
  margin: auto;
  padding: 24px 18px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--text-3);
  text-align: center;
}

.file-group {
  border: 1px solid var(--line);
  border-radius: 9px;
  background: rgba(6, 13, 28, 0.35);
}

.group-head {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 9px;
  border-bottom: 1px solid var(--line);
  color: var(--text-2);
}

.group-icon {
  color: var(--accent-cyan);
}

.group-label {
  font-size: 11.5px;
  font-weight: 600;
}

.group-count {
  font-size: 9.5px;
  color: var(--text-3);
}

.group-tree {
  padding: 4px 4px 6px;
}

.new-tag {
  flex: none;
  padding: 1px 6px;
  font-size: 9px;
  font-weight: 600;
  line-height: 1.6;
  color: #4be3a4;
  border: 1px solid rgba(75, 227, 164, 0.35);
  border-radius: 999px;
  background: rgba(75, 227, 164, 0.1);
}

.fp-editor-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--line);
}

.fp-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 6px;
  flex: none;
  align-items: center;
}

.mini {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 5px 9px;
  font-size: 11px;
  color: var(--text-2);
  border: 1px solid var(--line);
  border-radius: 7px;
  background: transparent;
}

.mini:hover {
  color: var(--text-1);
  border-color: var(--line-strong);
}

.f-path {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  font-family: var(--font-mono);
  font-size: 11.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.f-origin {
  flex: none;
  padding: 1px 6px;
  font-size: 9.5px;
  line-height: 1.6;
  border: 1px solid var(--line);
  border-radius: 999px;
  color: var(--accent-cyan);
  background: rgba(53, 224, 255, 0.08);
}

.f-origin.sandbox {
  color: var(--text-2);
  border-color: var(--line);
  background: transparent;
}

.fp-text {
  flex: 1;
  min-height: 0;
  margin: 0;
  padding: 12px;
  overflow: auto;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-1);
  white-space: pre-wrap;
}

.fp-preview {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 14px 16px 24px;
}

@media (max-width: 1100px) {
  .file-panel {
    width: 100% !important;
  }

  .resizer {
    display: none;
  }
}
</style>
