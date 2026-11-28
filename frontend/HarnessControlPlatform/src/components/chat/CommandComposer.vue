<script setup lang="ts">
// 命令输入框：contenteditable + 原子芯片 + 建议面板
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useCommandsStore } from '@/stores/commands'
import { contextAt } from '@/commands/parser'
import type { CommandCandidate } from '@/types/commands'

const props = defineProps<{
  modelValue: string
  disabled?: boolean
  placeholder?: string
}>()

const emit = defineEmits<{
  'update:modelValue': [string]
  submit: [string]
}>()

const commands = useCommandsStore()
const rootRef = ref<HTMLDivElement | null>(null)
const wrapRef = ref<HTMLDivElement | null>(null)
const empty = ref(true)
const composing = ref(false)
const open = ref(false)
const active = ref(0)
const items = ref<Suggestion[]>([])
const hintText = ref('')
const popStyle = ref<Record<string, string>>({ left: '6px', bottom: 'calc(100% + 6px)' })
let refreshSeq = 0

interface Suggestion {
  key: string
  label: string
  insert: string
  description?: string
  status?: string
  chip: boolean
}

// 带子命令/固定参数的命令：输入空格后给出可选项，避免用户不知道该填什么。
const ARG_HINTS: Record<
  string,
  Array<{ label: string; insert: string; description: string }>
> = {
  skill: [
    { label: 'get <url> [IsUse]', insert: '/skill get ', description: '下载并安装技能' },
    { label: 'show <name>', insert: '/skill show ', description: '预览技能内容' },
  ],
  plugin: [
    { label: 'get <url> [IsUse]', insert: '/plugin get ', description: '安装插件' },
    { label: 'list', insert: '/plugin list ', description: '列出已安装插件' },
    { label: 'status', insert: '/plugin status ', description: '查看插件状态' },
    { label: 'off', insert: '/plugin off ', description: '取消本轮绑定' },
  ],
  mcp: [
    { label: 'list', insert: '/mcp list ', description: '列出 MCP 服务' },
    { label: 'status', insert: '/mcp status ', description: '查看连接状态' },
    { label: 'off', insert: '/mcp off ', description: '取消本轮绑定' },
  ],
  skills: [
    { label: 'list', insert: '/skills list ', description: '列出技能' },
    { label: 'off', insert: '/skills off ', description: '取消本轮绑定' },
  ],
  taskflow: [
    { label: 'list', insert: '/taskflow list ', description: '列出任务流' },
    { label: 'off', insert: '/taskflow off ', description: '取消本轮绑定' },
  ],
  agent: [{ label: 'list', insert: '/agent list ', description: '列出 Agent 与组' }],
  export: [
    { label: 'md', insert: '/export md ', description: '导出 Markdown' },
    { label: 'json', insert: '/export json ', description: '导出 JSON' },
  ],
  think: [
    { label: 'on', insert: '/think on ', description: '开启思考模式' },
    { label: 'off', insert: '/think off ', description: '关闭思考模式' },
  ],
  web: [
    { label: 'on', insert: '/web on ', description: '开启联网搜索' },
    { label: 'off', insert: '/web off ', description: '关闭联网搜索' },
  ],
}

function serialize(): string {
  const root = rootRef.value
  if (!root) return ''
  return root.innerText.replace(/\u00a0/g, ' ')
}

function emitChange() {
  const text = serialize()
  empty.value = !text.trim()
  emit('update:modelValue', text)
}

function setText(text: string) {
  const root = rootRef.value
  if (!root) return
  root.textContent = text
  empty.value = !text.trim()
}

function caretOffset(): number {
  const root = rootRef.value
  const selection = window.getSelection()
  if (!root || !selection || !selection.rangeCount) return serialize().length
  const range = selection.getRangeAt(0)
  if (!root.contains(range.endContainer)) return serialize().length
  const probe = document.createRange()
  probe.selectNodeContents(root)
  probe.setEnd(range.endContainer, range.endOffset)
  return probe.toString().length
}

function closeSuggestions() {
  open.value = false
  items.value = []
  hintText.value = ''
  active.value = 0
}

function positionPopup() {
  const wrap = wrapRef.value
  const selection = window.getSelection()
  if (!wrap) return
  const wrapRect = wrap.getBoundingClientRect()
  // 默认弹在输入框上方，避免被底部输入区裁切。
  const bottom = 'calc(100% + 6px)'
  if (!selection || !selection.rangeCount) {
    popStyle.value = { left: '6px', bottom }
    return
  }
  const rect = selection.getRangeAt(0).getBoundingClientRect()
  const left = Math.max(4, Math.min(rect.left - wrapRect.left, wrapRect.width - 380))
  popStyle.value = { left: `${left}px`, bottom }
}

async function refreshSuggestions() {
  if (!commands.modeEnabled || composing.value) {
    closeSuggestions()
    return
  }
  if (!commands.catalog.length) await commands.loadCatalog()
  const text = serialize()
  const offset = caretOffset()
  const ctx = contextAt(text, offset)
  if (!ctx.commandTyped.startsWith('/')) {
    closeSuggestions()
    return
  }
  const seq = ++refreshSeq
  const spec = commands.catalog.find((item) => item.name === ctx.command)
  hintText.value = spec
    ? [spec.usage, ...(spec.examples || []).slice(0, 2)].join('    ')
    : ''

  if (ctx.stage === 'command') {
    // 刚输入 / 时 needle 为空，直接展示全部命令。
    const needle = ctx.command
    const list = commands.catalog
      .filter((item) => item.name.startsWith(needle))
      .slice(0, 12)
      .map<Suggestion>((item) => ({
        key: item.name,
        label: `/${item.name}`,
        insert: `/${item.name} `,
        description: item.summary,
        status: '',
        chip: false,
      }))
    if (seq !== refreshSeq) return
    items.value = list
    active.value = 0
    open.value = list.length > 0
    if (open.value) void nextTick(positionPopup)
    return
  }

  const needle = ctx.partial.toLowerCase()
  const quick: Suggestion[] = (ARG_HINTS[ctx.command] || [])
    .filter((item) => !needle || item.label.toLowerCase().startsWith(needle))
    .map((item) => ({
      key: `hint:${ctx.command}:${item.label}`,
      label: item.label,
      insert: item.insert,
      description: item.description,
      status: '子命令',
      chip: false,
    }))

  let candidates: CommandCandidate[] = []
  if (spec?.candidates) {
    candidates = await commands.loadCandidates(spec.candidates)
    if (seq !== refreshSeq) return
  }
  const candidateItems: Suggestion[] = candidates
    .filter(
      (candidate) =>
        !needle ||
        candidate.value.toLowerCase().includes(needle) ||
        candidate.label.toLowerCase().includes(needle),
    )
    .slice(0, Math.max(0, 10 - quick.length))
    .map((candidate) => ({
      key: `${ctx.command}:${candidate.value}`,
      label: candidate.label,
      insert: `/${ctx.command} ${candidate.value}`,
      description: candidate.description || candidate.status,
      status: candidate.status || '',
      chip: true,
    }))

  const seen = new Set<string>()
  const list = [...quick, ...candidateItems].filter((item) => {
    if (seen.has(item.insert)) return false
    seen.add(item.insert)
    return true
  })
  items.value = list
  active.value = 0
  open.value = list.length > 0 || Boolean(hintText.value)
  if (open.value) void nextTick(positionPopup)
}

function rangeFromOffsets(start: number, end: number): Range | null {
  const root = rootRef.value
  if (!root) return null
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  const range = document.createRange()
  let current = 0
  let startNode: Text | null = null
  let endNode: Text | null = null
  let startOffset = 0
  let endOffset = 0
  let node = walker.nextNode() as Text | null
  while (node) {
    const length = node.textContent?.length || 0
    if (!startNode && current + length >= start) {
      startNode = node
      startOffset = Math.max(0, start - current)
    }
    if (!endNode && current + length >= end) {
      endNode = node
      endOffset = Math.max(0, end - current)
      break
    }
    current += length
    node = walker.nextNode() as Text | null
  }
  if (!startNode || !endNode) return null
  range.setStart(startNode, startOffset)
  range.setEnd(endNode, endOffset)
  return range
}

function insertChip(raw: string) {
  const text = serialize()
  const offset = caretOffset()
  const slash = text.lastIndexOf('/', Math.max(0, offset - 1))
  const start = slash >= 0 && (slash === 0 || /\s/.test(text[slash - 1]!)) ? slash : offset
  const range = rangeFromOffsets(start, offset)
  if (!range) return
  range.deleteContents()
  const chip = document.createElement('span')
  chip.className = 'cmd-chip'
  chip.contentEditable = 'false'
  chip.dataset.raw = raw
  chip.textContent = raw
  const space = document.createTextNode(' ')
  range.insertNode(space)
  range.insertNode(chip)
  const selection = window.getSelection()
  const caret = document.createRange()
  caret.setStartAfter(space)
  caret.collapse(true)
  selection?.removeAllRanges()
  selection?.addRange(caret)
  emitChange()
}

function applySuggestion(item: Suggestion) {
  if (item.chip) {
    insertChip(item.insert)
    closeSuggestions()
    return
  }
  const text = serialize()
  const offset = caretOffset()
  const slash = text.lastIndexOf('/', Math.max(0, offset - 1))
  const start = slash >= 0 ? slash : offset
  const range = rangeFromOffsets(start, offset)
  if (!range) return
  range.deleteContents()
  const node = document.createTextNode(item.insert)
  range.insertNode(node)
  const selection = window.getSelection()
  const caret = document.createRange()
  caret.setStartAfter(node)
  caret.collapse(true)
  selection?.removeAllRanges()
  selection?.addRange(caret)
  emitChange()
  closeSuggestions()
  void nextTick(refreshSuggestions)
}

function onInput() {
  emitChange()
  if (composing.value) return
  void refreshSuggestions()
}

function onKeydown(event: KeyboardEvent) {
  if (composing.value) return
  if (open.value && items.value.length) {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      active.value = (active.value + 1) % items.value.length
      return
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault()
      active.value = (active.value - 1 + items.value.length) % items.value.length
      return
    }
    if (event.key === 'Tab') {
      event.preventDefault()
      applySuggestion(items.value[active.value]!)
      return
    }
    if (event.key === 'Enter') {
      event.preventDefault()
      applySuggestion(items.value[active.value]!)
      return
    }
    if (event.key === 'Escape') {
      event.preventDefault()
      closeSuggestions()
      return
    }
  }
  if (event.key === 'Enter' && !event.shiftKey && !composing.value) {
    event.preventDefault()
    const text = serialize()
    if (text.trim()) emit('submit', text)
    return
  }
  if (event.key === 'Escape') {
    closeSuggestions()
  }
}

function onCompositionStart() {
  composing.value = true
}

function onCompositionEnd() {
  composing.value = false
  emitChange()
  void refreshSuggestions()
}

function onDocumentMouseDown(event: MouseEvent) {
  if (!wrapRef.value) return
  if (!wrapRef.value.contains(event.target as Node)) closeSuggestions()
}

function focus() {
  rootRef.value?.focus()
}

function clear() {
  closeSuggestions()
  setText('')
  emitChange()
}

function setValue(text: string) {
  setText(text)
}

watch(
  () => props.modelValue,
  (value) => {
    if (composing.value) return
    if (serialize() !== value) setValue(value)
  },
)

watch(
  () => props.disabled,
  (value) => {
    const root = rootRef.value
    if (root) root.contentEditable = value ? 'false' : 'true'
    if (value) closeSuggestions()
  },
  { immediate: true },
)

onMounted(() => {
  setValue(props.modelValue || '')
  document.addEventListener('mousedown', onDocumentMouseDown)
  void commands.loadCatalog()
})

onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocumentMouseDown)
})

defineExpose({ focus, clear, setValue })
</script>

<template>
  <div ref="wrapRef" class="cmd-composer-wrap">
    <span v-if="empty" class="cmd-placeholder">{{ placeholder || '输入消息' }}</span>
    <div
      ref="rootRef"
      class="cmd-composer"
      role="textbox"
      aria-multiline="true"
      :contenteditable="!props.disabled"
      @input="onInput"
      @keydown="onKeydown"
      @compositionstart="onCompositionStart"
      @compositionend="onCompositionEnd"
      @click="refreshSuggestions"
      @blur="closeSuggestions"
    />
    <div
      v-if="open && (items.length || hintText)"
      class="cmd-suggest"
      :style="popStyle"
      @mousedown.prevent
    >
      <button
        v-for="(item, index) in items"
        :key="item.key"
        type="button"
        class="cmd-suggest-item"
        :class="{ active: index === active }"
        @mouseenter="active = index"
        @click="applySuggestion(item)"
      >
        <span class="cmd-suggest-label">{{ item.label }}</span>
        <span v-if="item.description" class="cmd-suggest-desc">{{ item.description }}</span>
        <span v-if="item.status" class="cmd-suggest-status">{{ item.status }}</span>
      </button>
      <div v-if="hintText" class="cmd-suggest-hint mono">{{ hintText }}</div>
    </div>
  </div>
</template>

<style scoped>
.cmd-composer-wrap {
  position: relative;
  width: 100%;
}
.cmd-placeholder {
  position: absolute;
  top: 2px;
  left: 2px;
  color: var(--text-3);
  font-size: 14px;
  pointer-events: none;
  user-select: none;
}
.cmd-composer {
  width: 100%;
  min-height: 26px;
  max-height: 132px;
  overflow-y: auto;
  outline: none;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 14px;
  line-height: 1.65;
  color: var(--text-1);
}
.cmd-composer :deep(.cmd-chip) {
  display: inline-flex;
  align-items: center;
  height: 22px;
  margin: 0 2px;
  padding: 0 8px;
  border-radius: 6px;
  border: 1px solid rgba(91, 140, 255, 0.45);
  background: rgba(91, 140, 255, 0.14);
  color: var(--accent);
  font-family: var(--font-mono);
  font-size: 12px;
  white-space: nowrap;
  cursor: default;
}
.cmd-suggest {
  position: absolute;
  z-index: 40;
  width: min(380px, calc(100vw - 24px));
  max-height: min(280px, 42vh);
  overflow-y: auto;
  padding: 6px;
  border: 1px solid var(--line-strong);
  border-radius: 10px;
  background: var(--bg-panel);
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.32);
}
.cmd-suggest-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 6px 8px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--text-2);
  font-size: 12px;
  text-align: left;
  cursor: pointer;
}
.cmd-suggest-item.active {
  background: var(--accent-soft);
  color: var(--text-1);
}
.cmd-suggest-label {
  font-family: var(--font-mono);
  color: var(--text-1);
  white-space: nowrap;
}
.cmd-suggest-desc {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-3);
}
.cmd-suggest-hint {
  margin-top: 4px;
  padding: 6px 8px 2px;
  border-top: 1px solid var(--line);
  color: var(--text-3);
  font-size: 11px;
  line-height: 1.5;
  white-space: pre-wrap;
}
.cmd-suggest-status {
  color: var(--text-3);
  font-size: 11px;
  white-space: nowrap;
}
</style>
