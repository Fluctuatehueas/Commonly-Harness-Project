<script setup lang="ts">
// 文件抽屉：按本机/沙箱分组 + 目录树；本机文件定位到资源管理器，沙箱文件下载。
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  ArrowLeft,
  Close,
  Download,
  EditPen,
  Fold,
  FolderOpened,
  InfoFilled,
  Refresh,
  View,
} from '@element-plus/icons-vue'
import AppIcon from '@/components/console/AppIcon.vue'
import HoloTooltip from '@/components/console/HoloTooltip.vue'
import MarkdownView from './MarkdownView.vue'
import HtmlPreview from './HtmlPreview.vue'
import PdfPreview from './PdfPreview.vue'
import FileTreeNode from './FileTreeNode.vue'
import FileTypeIcon from './FileTypeIcon.vue'
import { buildGroups, type FileGroup } from './fileTree'
import { useFileStore, type FileMeta } from '@/stores/files'
import { absoluteWorkspacePath, revealFile } from '@/api/workspace'
import { API_BASE } from '@/api/client'
import { USER_ID } from '@/stores/sessions'
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
// HTML 预览：sandbox iframe 渲染自包含 HTML（图表 / 报告）
const isHtml = computed(() =>
  /\.(?:html?|xhtml)$/i.test(files.current?.path || ''),
)
// PDF 预览：pdfjs-dist 渲染，不依赖浏览器/Electron 的 PDF 插件
const isPdf = computed(() => /\.pdf$/i.test(files.current?.path || ''))
const isPreviewable = computed(() => isMarkdown.value || isHtml.value)
const previewMode = ref(false)

const pdfUrl = computed(() => {
  const current = files.current
  if (!current || !props.threadId) return ''
  return (
    `${API_BASE}/threads/${props.threadId}/files/download` +
    `?user_id=${encodeURIComponent(USER_ID)}` +
    `&path=${encodeURIComponent(current.path)}` +
    `&source=${current.origin}&inline=true`
  )
})

// 全部折叠：每次递增信号，让所有目录节点收起。
const collapseSignal = ref(0)

function collapseAll() {
  collapseSignal.value += 1
}

watch(
  () => [props.threadId, props.refreshKey] as const,
  ([id]) => {
    files.closePreview()
    if (id) void files.list(id)
  },
  { immediate: true },
)

// 消息泡「在右侧打开」：组件挂载后立即消费一次性定位请求
watch(
  () => files.requested,
  (req) => {
    if (!req) return
    files.clearRequested()
    if (!props.threadId) return
    void files.open(props.threadId, req.path, req.origin)
  },
  { immediate: true },
)

watch(
  () => files.current?.path,
  (path) => {
    previewMode.value = /\.(?:md|markdown|mdx|html?|xhtml)$/i.test(path || '')
  },
)

function openInlineFile(file: { path: string; origin: 'project' | 'sandbox' }) {
  if (props.threadId) void files.open(props.threadId, file.path, file.origin)
}

function reload() {
  if (props.threadId) void files.list(props.threadId)
}

function flash(message: string) {
  ElMessage.info(message)
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
      <el-button
        class="icon-btn"
        :icon="Fold"
        text
        circle
        size="small"
        title="全部折叠"
        @click="collapseAll"
      />
      <el-button
        class="icon-btn"
        :icon="Refresh"
        text
        circle
        size="small"
        title="刷新"
        @click="reload"
      />
      <el-button
        class="icon-btn"
        :icon="Close"
        text
        circle
        size="small"
        title="关闭"
        @click="emit('close')"
      />
    </header>

    <div v-if="files.error" class="fp-error">{{ files.error }}</div>

    <div v-if="!files.current" v-loading="files.loading" class="fp-list">
      <p class="fp-hint">
        <span class="fp-hint-text">
          {{
            files.projectId
              ? '项目会话同时展示本机项目目录与沙箱工作目录：本机文件可在资源管理器定位，沙箱文件可下载。'
              : '自由会话的 AI 产物保留在沙箱内，可在这里预览与下载。'
          }}
        </span>
        <HoloTooltip
          title="沙箱文件生命周期"
          code="SANDBOX // LIFECYCLE"
          content="沙箱内的文件会随沙箱销毁而销毁，如有重要产出物请及时下载。"
          footer="悬停查看说明"
          placement="top"
          :width="280"
        >
          <el-icon class="info-icon"><InfoFilled /></el-icon>
        </HoloTooltip>
      </p>

      <el-empty
        v-if="!files.loading && files.files.length === 0"
        :image-size="54"
        description="当前会话还没有文件。让 Agent 写一个文件试试，例如“把刚才的结论保存为 notes.md”。"
      />

      <section v-for="group in groups" :key="group.origin" class="file-group">
        <header class="group-head">
          <AppIcon name="folder" :size="15" class="group-icon" />
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
            :collapse-signal="collapseSignal"
            @open="handleOpen"
            @action="handleAction"
          />
        </div>
      </section>
    </div>

    <template v-else-if="files.current">
      <div class="fp-editor-head">
        <FileTypeIcon class="f-icon" :name="files.current.path" :size="20" />
        <code class="f-path">{{ files.current.path }}</code>
        <div class="fp-actions">
          <span class="f-origin" :class="files.current.origin">
            {{ files.current.origin === 'project' ? '本机' : '沙箱' }}
          </span>
          <el-button class="mini" :icon="ArrowLeft" size="small" @click="files.closePreview()">
            列表
          </el-button>
          <el-button
            v-if="isPreviewable"
            class="mini"
            :icon="previewMode ? EditPen : View"
            size="small"
            @click="previewMode = !previewMode"
          >
            {{ previewMode ? '源码' : '预览' }}
          </el-button>
          <el-button
            class="mini"
            :icon="files.current.origin === 'project' ? FolderOpened : Download"
            size="small"
            @click="previewAction"
          >
            {{ files.current.origin === 'project' ? '在资源管理器打开' : '下载' }}
          </el-button>
        </div>
      </div>
      <div v-if="isPdf" class="fp-preview fp-preview-html">
        <PdfPreview :url="pdfUrl" />
      </div>
      <div v-else-if="previewMode && isMarkdown" class="fp-preview">
        <MarkdownView :content="files.current.content" @open-file="openInlineFile" />
      </div>
      <div v-else-if="previewMode && isHtml" class="fp-preview fp-preview-html">
        <HtmlPreview :content="files.current.content" />
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

.icon-btn.el-button {
  width: 26px;
  height: 26px;
  margin-left: 0;
  padding: 0;
  color: var(--text-2);
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
  display: flex;
  align-items: flex-start;
  gap: 4px;
  margin: 2px 6px 4px;
  font-size: 10.5px;
  line-height: 1.6;
  color: var(--text-3);
}

.fp-hint-text {
  flex: 1;
  min-width: 0;
}

.info-icon {
  flex: none;
  margin-top: 2px;
  font-size: 12px;
  color: var(--text-3);
  cursor: help;
  transition: color 0.15s ease;
}

.info-icon:hover {
  color: var(--accent-cyan);
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

.mini.el-button {
  margin-left: 0;
  padding: 5px 9px;
  font-size: 11px;
  color: var(--text-2);
}

.mini.el-button + .mini.el-button {
  margin-left: 0;
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

.f-icon {
  flex: none;
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

/* HTML 预览占满整个抽屉，不由外层滚动 */
.fp-preview-html {
  display: flex;
  padding: 0;
  overflow: hidden;
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
