<script setup lang="ts">
// 文件树节点：目录可折叠，文件行提供预览 + 来源对应的操作按钮。
import { computed, ref } from 'vue'
import AppIcon from '@/components/console/AppIcon.vue'
import type { TreeNode } from './fileTree'
import { countFiles } from './fileTree'
import type { FileMeta } from '@/stores/files'

defineOptions({ name: 'FileTreeNode' })

const props = defineProps<{
  node: TreeNode
  depth: number
  isNew?: (file: FileMeta) => boolean
}>()

const emit = defineEmits<{
  open: [file: FileMeta]
  action: [file: FileMeta]
}>()

const folder = computed(() => (props.node.kind === 'folder' ? props.node : null))
const file = computed(() => (props.node.kind === 'file' ? props.node : null))
const open = ref(true)
const indent = computed(() => ({ paddingLeft: `${8 + props.depth * 14}px` }))

function toggle() {
  open.value = !open.value
}

function emitOpen(target: FileMeta) {
  emit('open', target)
}

function emitAction(target: FileMeta) {
  emit('action', target)
}

function fmtSize(size: number) {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}
</script>

<template>
  <div v-if="folder" class="tree-folder">
    <button class="tree-row folder-row" :style="indent" type="button" @click="toggle">
      <span class="t-chev" :class="{ open: open }">
        <AppIcon name="chevronRight" :size="10" />
      </span>
      <AppIcon name="folder" :size="13" class="t-icon folder-icon" />
      <span class="t-name">{{ folder.name }}</span>
      <span class="t-count mono">{{ countFiles(folder) }}</span>
    </button>
    <div v-show="open">
      <FileTreeNode
        v-for="child in folder.children"
        :key="child.path"
        :node="child"
        :depth="depth + 1"
        :is-new="isNew"
        @open="emitOpen"
        @action="emitAction"
      />
    </div>
  </div>

  <div v-else-if="file" class="tree-file">
    <div class="tree-row file-row" :style="indent">
      <span class="t-chev placeholder"></span>
      <button class="t-main" type="button" :title="file.file.path" @click="emitOpen(file.file)">
        <AppIcon name="file" :size="13" class="t-icon file-icon" />
        <span class="t-name">{{ file.name }}</span>
        <span v-if="isNew?.(file.file)" class="new-tag">new</span>
      </button>
      <span class="t-size">{{ fmtSize(file.file.size) }}</span>
      <button
        class="t-action"
        type="button"
        :title="file.file.origin === 'project' ? '在资源管理器打开' : '下载'"
        @click.stop="emitAction(file.file)"
      >
        <AppIcon :name="file.file.origin === 'project' ? 'external' : 'download'" :size="12" />
      </button>
    </div>
  </div>
</template>

<style scoped>
.tree-folder,
.tree-file {
  min-width: 0;
}

.tree-row {
  display: flex;
  align-items: center;
  gap: 6px;
  min-height: 30px;
  padding-right: 6px;
  border-radius: 7px;
}

.tree-row:hover {
  background: rgba(79, 140, 255, 0.07);
}

.folder-row {
  width: 100%;
  color: var(--text-2);
  text-align: left;
  border: 0;
  background: transparent;
}

.folder-row:hover {
  color: var(--text-1);
}

.t-chev {
  display: grid;
  place-items: center;
  width: 12px;
  flex: none;
  color: var(--text-3);
  transition: transform 0.16s ease;
}

.t-chev.open {
  transform: rotate(90deg);
}

.t-chev.placeholder {
  visibility: hidden;
}

.t-icon {
  flex: none;
}

.folder-icon {
  color: var(--accent-cyan);
}

.file-icon {
  color: var(--text-3);
}

.t-main {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1;
  min-width: 0;
  padding: 0;
  color: var(--text-1);
  text-align: left;
  border: 0;
  background: transparent;
}

.t-name {
  min-width: 0;
  overflow: hidden;
  font-size: 11.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.t-count {
  flex: none;
  padding: 0 6px;
  font-size: 9.5px;
  color: var(--text-3);
}

.t-size {
  flex: none;
  font-size: 10px;
  color: var(--text-3);
}

.t-action {
  display: grid;
  place-items: center;
  width: 22px;
  height: 22px;
  flex: none;
  padding: 0;
  color: var(--text-3);
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
}

.t-action:hover {
  color: var(--accent-cyan);
  border-color: var(--line);
}

.new-tag {
  flex: none;
  padding: 1px 5px;
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.02em;
  line-height: 1.5;
  color: #4be3a4;
  border: 1px solid rgba(75, 227, 164, 0.35);
  border-radius: 999px;
  background: rgba(75, 227, 164, 0.1);
}
</style>
