<script setup lang="ts">
// 文件面板：列出当前会话生成的文件，支持预览编辑与下载
import { ref, watch } from 'vue'
import AppIcon from '@/components/console/AppIcon.vue'
import { downloadText, useFileStore } from '@/stores/files'

const props = defineProps<{ threadId: string | null; refreshKey?: number }>()
const emit = defineEmits<{ close: [] }>()
const files = useFileStore()

const dirty = ref(false)
const savedToast = ref('')

watch(
  () => [props.threadId, props.refreshKey] as const,
  ([id]) => {
    files.closePreview()
    dirty.value = false
    if (id) void files.list(id)
  },
  { immediate: true },
)

async function reload() {
  if (props.threadId) await files.list(props.threadId)
}

async function saveFile() {
  if (!props.threadId) return
  if (await files.save(props.threadId)) {
    dirty.value = false
    flash('已保存到会话文件系统')
  }
}

function flash(msg: string) {
  savedToast.value = msg
  window.setTimeout(() => (savedToast.value = ''), 2200)
}

function fmtSize(n: number) {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}
</script>

<template>
  <div class="file-panel">
    <header class="fp-head">
      <span class="title-icon"><AppIcon name="file" :size="14" /></span>
      <strong>会话文件</strong>
      <span v-if="files.source" class="src">{{ files.source === 'sandbox' ? '沙箱' : '线程状态' }}</span>
      <button class="icon-btn" type="button" title="刷新" @click="reload()">
        <AppIcon name="timer" :size="14" />
      </button>
      <button class="icon-btn" type="button" title="关闭" @click="emit('close')">
        <AppIcon name="close" :size="14" />
      </button>
    </header>

    <div v-if="files.error" class="fp-error">{{ files.error }}</div>

    <!-- 文件列表 -->
    <div v-if="!files.current" class="fp-list">
      <div v-if="files.loading" class="fp-empty">加载中…</div>
      <div v-else-if="files.files.length === 0" class="fp-empty">
        当前会话还没有生成文件。让 Agent 写一个文件试试，例如“把刚才的结论保存为 /notes.md”。
      </div>
      <button
        v-for="f in files.files"
        :key="f.path"
        class="file-row"
        type="button"
        @click="props.threadId && files.open(props.threadId, f.path)"
      >
        <AppIcon name="file" :size="14" class="f-icon" />
        <span class="f-path">{{ f.path }}</span>
        <span class="f-size">{{ fmtSize(f.size) }}</span>
      </button>
    </div>

    <!-- 预览 / 编辑 -->
    <template v-else>
      <div class="fp-editor-head">
        <code class="f-path">{{ files.current.path }}</code>
        <div class="fp-actions">
          <button class="mini" type="button" @click="files.closePreview()">
            <AppIcon name="chevronRight" :size="12" style="transform: rotate(180deg)" /> 列表
          </button>
          <button
            class="mini"
            type="button"
            @click="downloadText(files.current.path.split('/').pop() || 'file.txt', files.current.content)"
          >
            <AppIcon name="layers" :size="12" /> 下载
          </button>
          <button class="mini primary" type="button" :disabled="!dirty || files.saving" @click="saveFile">
            <AppIcon name="check" :size="12" /> {{ files.saving ? '保存中…' : '保存' }}
          </button>
        </div>
      </div>
      <textarea
        v-model="files.current.content"
        class="fp-editor"
        spellcheck="false"
        @input="dirty = true"
      ></textarea>
    </template>

    <Transition name="fade">
      <div v-if="savedToast" class="fp-toast">{{ savedToast }}</div>
    </Transition>
  </div>
</template>

<style scoped>
.file-panel {
  position: relative;
  width: 340px;
  flex: none;
  display: flex;
  flex-direction: column;
  min-height: 0;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: rgba(9, 18, 38, 0.92);
  overflow: hidden;
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
  background: rgba(53, 224, 255, 0.1);
}

.src {
  margin-left: auto;
  font-size: 10.5px;
  color: var(--text-3);
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 2px 8px;
}

.icon-btn {
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  border: 1px solid var(--line);
  border-radius: 7px;
  color: var(--text-2);
  background: transparent;
  transition: all 0.18s;
}

.icon-btn:hover {
  color: var(--text-1);
  border-color: var(--line-strong);
}

.fp-error {
  margin: 10px 12px 0;
  padding: 8px 10px;
  font-size: 11.5px;
  color: var(--amber);
  border: 1px solid rgba(255, 182, 72, 0.3);
  border-radius: 8px;
  background: rgba(255, 182, 72, 0.06);
}

.fp-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.fp-empty {
  margin: auto;
  padding: 24px 18px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--text-3);
  text-align: center;
}

.file-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 10px;
  color: var(--text-1);
  text-align: left;
  border: 1px solid transparent;
  border-radius: 9px;
  background: transparent;
  transition: all 0.15s;
}

.file-row:hover {
  border-color: var(--line);
  background: rgba(79, 140, 255, 0.08);
}

.f-icon {
  color: var(--accent-cyan);
  flex: none;
}

.f-path {
  flex: 1;
  min-width: 0;
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 11.5px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.f-size {
  font-size: 10.5px;
  color: var(--text-3);
  flex: none;
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
  gap: 6px;
  flex: none;
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
  transition: all 0.15s;
}

.mini:hover {
  color: var(--text-1);
  border-color: var(--line-strong);
}

.mini.primary {
  color: #fff;
  border-color: transparent;
  background: linear-gradient(90deg, #3b78ff, #2a5cd6);
}

.mini.primary:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.fp-editor {
  flex: 1;
  min-height: 0;
  padding: 12px;
  font-family: 'Cascadia Code', 'JetBrains Mono', Consolas, monospace;
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-1);
  border: none;
  background: rgba(4, 9, 20, 0.7);
  resize: none;
  outline: none;
}

.fp-toast {
  position: absolute;
  left: 50%;
  bottom: 16px;
  transform: translateX(-50%);
  padding: 7px 14px;
  font-size: 11.5px;
  color: #c9f5db;
  border: 1px solid rgba(61, 220, 132, 0.4);
  border-radius: 999px;
  background: rgba(12, 40, 28, 0.95);
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.25s;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

@media (max-width: 1100px) {
  .file-panel {
    position: absolute;
    right: 12px;
    top: 64px;
    bottom: 12px;
    z-index: 40;
    box-shadow: 0 18px 50px rgba(0, 0, 0, 0.5);
  }
}
</style>
