<script setup lang="ts">
// Markdown 渲染：marked 解析 + DOMPurify 消毒
// 代码块额外带语言标签与一键复制按钮（头部，位于代码上方）
import { computed, h, nextTick, onBeforeUnmount, onMounted, reactive, ref, render, watch } from 'vue'
import { marked, Renderer, type Tokens } from 'marked'
import DOMPurify from 'dompurify'
import { API_BASE } from '@/api/client'
import { currentUserId } from '@/api/identity'
import { fetchFileContent, saveBlob, type FileOrigin } from '@/stores/files'
import MediaViewer from './MediaViewer.vue'
import HtmlPreview from './HtmlPreview.vue'
import type { MediaKind } from './mediaTypes'
import { normalizeChartOption } from './chartOption'

const props = defineProps<{
  content: string
  /** 会话 ID：用于把 Markdown 里的本地图片解析为会话文件下载地址 */
  threadId?: string | null
  /** 流式输出中先不渲染图表，避免半截 Mermaid / JSON 反复报错 */
  streaming?: boolean
}>()

// 气泡里的文件卡片点击「在右侧打开」时交给上层处理
const emit = defineEmits<{
  'open-file': [file: { path: string; origin: FileOrigin }]
}>()

const container = ref<HTMLElement | null>(null)
let chartInstances: { resize: () => void; dispose: () => void }[] = []

const VIZ_LANGUAGES = new Set(['mermaid', 'echarts', 'echart', 'chart'])

// 全屏查看器状态：Mermaid SVG、ECharts option、本地/远程图片共用。
const viewer = reactive({
  open: false,
  kind: 'image' as MediaKind,
  src: '',
  svg: '',
  option: null as Record<string, unknown> | null,
  title: '',
  filename: '',
})

const chartOptions = new WeakMap<HTMLElement, Record<string, unknown>>()
const svgByBlock = new WeakMap<HTMLElement, string>()

const EXPAND_ICON =
  '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
  'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' +
  '<path d="M8 3H5a2 2 0 0 0-2 2v3M16 3h3a2 2 0 0 1 2 2v3M8 21H5a2 2 0 0 1-2-2v-3M16 21h3a2 2 0 0 0 2-2v-3"/></svg>'

const DOWNLOAD_ICON =
  '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
  'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' +
  '<path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M4 21h16"/></svg>'

const ZOOM_ICON =
  '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
  'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' +
  '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/><path d="M8 11h6"/><path d="M11 8v6"/></svg>'

// Electron/桌面壳可注入 openExternal；没有时退回浏览器新窗口。
interface ExternalBridge {
  openExternal?: (url: string) => unknown
}

interface BridgeWindow extends Window {
  electronAPI?: ExternalBridge
  electron?: ExternalBridge
  desktop?: ExternalBridge
  harness?: ExternalBridge
}

function openExternalUrl(url: string) {
  const bridgeWindow = window as BridgeWindow
  const bridges = [
    bridgeWindow.electronAPI,
    bridgeWindow.electron,
    bridgeWindow.desktop,
    bridgeWindow.harness,
  ]
  for (const bridge of bridges) {
    if (bridge?.openExternal) {
      try {
        void bridge.openExternal(url)
        return
      } catch {
        // 桥接调用失败时继续走浏览器兜底
      }
    }
  }
  window.open(url, '_blank', 'noopener,noreferrer')
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

const COPY_ICON =
  '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
  'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">' +
  '<path d="M9 9h10a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H9a1 1 0 0 1-1-1V10a1 1 0 0 1 1-1z"/>' +
  '<path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"/></svg>'

// 只允许页面链接与邮箱链接离开应用，其他协议（javascript: 等）不生成 <a>。
function safeExternalHref(href: string): string {
  const value = (href || '').trim()
  return /^(https?:\/\/|mailto:)/i.test(value) ? value : ''
}

/** 把 Agent 写的本地图片路径转换成会话文件下载地址。 */
function buildSessionFileUrl(path: string, origin: 'project' | 'sandbox'): string {
  const threadId = encodeURIComponent(props.threadId || '')
  const query = new URLSearchParams({
    user_id: currentUserId(),
    path,
    source: origin,
    inline: 'true',
  })
  return `${API_BASE}/threads/${threadId}/files/download?${query.toString()}`
}

interface LocalImage {
  path: string
  origin: 'project' | 'sandbox'
}

/** 识别 `sandbox:/path` / `project:/path` 显式协议的会话文件引用。 */
function resolveSessionProtocol(raw: string): LocalImage | null {
  const protocol = /^(sandbox|project):(.*)$/i.exec((raw || '').trim())
  if (!protocol) return null
  const origin = protocol[1]!.toLowerCase() as 'project' | 'sandbox'
  let rest = (protocol[2] || '/').trim() || '/'
  rest = rest.replace(/^\.\//, '')
  if (!rest.startsWith('/')) rest = `/${rest}`
  return { path: rest, origin }
}

function resolveLocalImage(raw: string): LocalImage | null {
  if (!props.threadId) return null
  let value = (raw || '').trim()
  if (!value) return null
  if (/^(https?:|data:|blob:)/i.test(value)) return null
  // 明确协议：![图](sandbox:/chart.png) / ![图](project:/report/chart.png)
  const protocol = resolveSessionProtocol(value)
  if (protocol) return protocol
  if (value.startsWith('file://')) {
    try {
      value = decodeURIComponent(value.replace(/^file:\/\//, ''))
    } catch {
      return null
    }
  }
  const threadId = props.threadId
  // 沙箱绝对路径：/workspace/sessions/{thread_id}/xxx 或 /workspace/{project}/xxx
  const sessionPrefix = `/workspace/sessions/${threadId}/`
  if (value.startsWith(sessionPrefix)) {
    return { path: `/${value.slice(sessionPrefix.length)}`, origin: 'sandbox' }
  }
  if (value.startsWith('/workspace/')) {
    const rest = value.replace(/^\/workspace\/[^/]+\//, '')
    return { path: `/${rest}`, origin: 'sandbox' }
  }
  // Windows 绝对路径无法通过会话文件接口读取
  if (/^[A-Za-z]:[\\/]/.test(value)) return null
  const path = value.startsWith('/') ? value : `/${value}`
  return { path, origin: 'project' }
}

const renderer = new Renderer()

// 本地图片：解析成会话文件地址；失败时由后续事件监听自动尝试另一来源。
renderer.image = (token: Tokens.Image) => {
  const raw = (token.href || '').trim()
  const alt = escapeHtml(token.text || '')
  const local = resolveLocalImage(raw)
  const image = local
    ? `<img class="md-local-image" src="${escapeHtml(buildSessionFileUrl(local.path, local.origin))}"` +
      ` data-local-path="${escapeHtml(local.path)}"` +
      ` data-local-origin="${local.origin}" alt="${alt}" loading="lazy" />`
    : `<img src="${escapeHtml(raw)}" alt="${alt}" loading="lazy" />`
  return (
    `<span class="md-image-block">` +
    image +
    `<span class="md-image-actions">` +
    `<button type="button" class="md-image-action" data-image-action="download" title="保存到本地">${DOWNLOAD_ICON}</button>` +
    `<button type="button" class="md-image-action" data-image-action="fullscreen" title="全屏查看">${EXPAND_ICON}</button>` +
    `</span>` +
    `</span>`
  )
}

const FILE_ICON =
  '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
  'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">' +
  '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/></svg>'

const HTML_FILE = /\.(?:html?|xhtml)$/i

/** 会话文件卡片：AI 用 `[名称](sandbox:/x.html)` 引用产物时渲染。 */
function renderFileCard(file: LocalImage, label?: string) {
  const name =
    file.path.split('/').filter(Boolean).pop() || (label || '').trim() || 'file'
  const preview = HTML_FILE.test(file.path)
    ? '<button type="button" class="md-file-action" data-file-action="preview">预览</button>'
    : ''
  return (
    `<span class="md-file-card" data-file-path="${escapeHtml(file.path)}" data-file-origin="${file.origin}">` +
    `<span class="md-file-head">` +
    `<span class="md-file-icon">${FILE_ICON}</span>` +
    `<span class="md-file-name">${escapeHtml(name)}</span>` +
    `<span class="md-file-origin">${file.origin === 'sandbox' ? '沙箱' : '本机'}</span>` +
    `<span class="md-file-actions">${preview}` +
    `<button type="button" class="md-file-action primary" data-file-action="drawer">在右侧打开</button>` +
    `</span>` +
    `</span>` +
    `<span class="md-file-preview"></span>` +
    `</span>`
  )
}

// 链接统一带 target=_blank；点击事件里再交给桌面壳或浏览器新开窗口。
renderer.link = function (this: Renderer, token: Tokens.Link) {
  // sandbox: / project: 协议优先当会话文件处理，不离开应用
  const local = resolveSessionProtocol(token.href || '')
  if (local) return renderFileCard(local, token.text)
  const url = safeExternalHref(token.href)
  if (!url) return escapeHtml(token.text)
  const label =
    this.parser?.parseInline(token.tokens) || escapeHtml(token.text)
  const titleAttr = token.title ? ` title="${escapeHtml(token.title)}"` : ''
  return (
    `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer"${titleAttr}>` +
    `${label}</a>`
  )
}

// 代码块自定义渲染：语言标签 + 复制按钮放在代码上方。
// mermaid / echarts 代码块先保留源码，待本次回答结束后由 renderVisuals 渲染成图表。
renderer.code = ({ text, lang, escaped }) => {
  const language = (lang || '').trim().split(/\s+/)[0] || 'text'
  const body = escaped ? text : escapeHtml(text)
  const languageAttr = escapeHtml(language)
  if (VIZ_LANGUAGES.has(language.toLowerCase())) {
    const vizType = language.toLowerCase() === 'mermaid' ? 'mermaid' : 'echarts'
    return (
      `<div class="viz-block" data-viz="${vizType}">` +
      `<div class="code-head">` +
      `<span class="code-lang">${languageAttr.toUpperCase()}</span>` +
      `<span class="code-actions">` +
      `<button type="button" class="viz-action" data-viz-action="zoom" title="缩放查看">${ZOOM_ICON}<span>缩放</span></button>` +
      `<button type="button" class="viz-action" data-viz-action="fullscreen" title="全屏查看">${EXPAND_ICON}<span>全屏</span></button>` +
      `<button type="button" class="viz-copy" aria-label="复制源码">${COPY_ICON}<span>复制</span></button>` +
      `</span>` +
      `</div>` +
      `<pre class="viz-source"><code class="language-${languageAttr}">${body}</code></pre>` +
      `<div class="viz-output"></div>` +
      `<div class="viz-error"></div>` +
      `</div>`
    )
  }
  return (
    `<div class="code-block">` +
    `<div class="code-head">` +
    `<span class="code-lang">${escapeHtml(language)}</span>` +
    `<button type="button" class="code-copy" aria-label="复制代码">${COPY_ICON}<span>复制</span></button>` +
    `</div>` +
    `<pre><code class="language-${languageAttr}">${body}</code></pre>` +
    `</div>`
  )
}

const html = computed(() => {
  const raw = marked.parse(props.content || '', {
    async: false,
    gfm: true,
    breaks: true,
    renderer,
  }) as string
  return DOMPurify.sanitize(raw)
})

// ---------- Mermaid / ECharts 渲染 ----------
let mermaidPromise: Promise<any> | null = null
let vizRun = 0

async function getMermaid(): Promise<any> {
  if (!mermaidPromise) {
    mermaidPromise = import('mermaid').then((mod) => {
      const mermaid = mod.default
      mermaid.initialize({
        startOnLoad: false,
        theme: 'dark',
        securityLevel: 'strict',
        // 关键：语法错误时不要把「炸弹」错误图追加到 body 底部。
        suppressErrorRendering: true,
        fontFamily: 'Inter, "Microsoft YaHei", sans-serif',
      })
      return mermaid
    })
  }
  return mermaidPromise
}

function disposeCharts() {
  for (const chart of chartInstances) {
    try {
      chart.dispose()
    } catch {
      /* ignore */
    }
  }
  chartInstances = []
}

/** 清理旧版本 Mermaid 渲染失败时追加到 body 的错误图（炸弹）。 */
function cleanupMermaidErrorArtifacts() {
  document
    .querySelectorAll<SVGElement>('body > svg, body > div > svg')
    .forEach((svg) => {
      const text = svg.textContent || ''
      if (text.includes('Syntax error in text') || text.includes('mermaid version')) {
        svg.remove()
      }
    })
}

function attachImageFallbacks() {
  const host = container.value
  if (!host) return
  host.querySelectorAll<HTMLImageElement>('img[data-local-path]').forEach((img) => {
    if (img.dataset.fallbackBound) return
    img.dataset.fallbackBound = '1'
    img.addEventListener(
      'error',
      () => {
        const path = img.dataset.localPath
        if (!path) return
        // 项目 / 沙箱路径无法从链接本身判断时，失败后自动换另一来源再试一次。
        const next = img.dataset.localOrigin === 'project' ? 'sandbox' : 'project'
        img.dataset.localOrigin = next
        img.src = buildSessionFileUrl(path, next)
      },
      { once: true },
    )
  })
}

async function renderVisuals() {
  const host = container.value
  if (!host) return
  cleanupMermaidErrorArtifacts()
  const run = ++vizRun
  disposeCharts()
  host.querySelectorAll<HTMLElement>('.viz-block').forEach((block) => {
    block.classList.remove('rendered')
    const output = block.querySelector<HTMLElement>('.viz-output')
    const error = block.querySelector<HTMLElement>('.viz-error')
    if (output) output.innerHTML = ''
    if (error) error.textContent = ''
  })
  attachImageFallbacks()
  // 流式阶段保留源码，避免半截 Mermaid / JSON 反复报错和闪烁。
  if (props.streaming) return

  const blocks = Array.from(host.querySelectorAll<HTMLElement>('.viz-block'))
  for (const block of blocks) {
    if (run !== vizRun) return
    const source = block.querySelector<HTMLElement>('.viz-source code')?.textContent || ''
    const output = block.querySelector<HTMLElement>('.viz-output')
    const error = block.querySelector<HTMLElement>('.viz-error')
    if (!output || !source.trim()) continue
    try {
      if (block.dataset.viz === 'mermaid') {
        const mermaid = await getMermaid()
        // 先静默校验语法，避免 render 失败时向页面注入错误图。
        const valid = await mermaid.parse(source, { suppressErrors: true })
        if (!valid) throw new Error('Mermaid 语法错误')
        const id = `mermaid-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
        const result = await mermaid.render(id, source)
        if (run !== vizRun) return
        output.innerHTML = result.svg
        svgByBlock.set(block, result.svg)
        block.classList.add('rendered')
      } else {
        const parsed = JSON.parse(source) as Record<string, unknown>
        const option = normalizeChartOption(
          parsed && typeof parsed === 'object' && 'option' in parsed
            ? parsed.option
            : parsed,
        )
        // ECharts 初始化时必须已有宽高，先让输出容器显示出来。
        block.classList.add('rendered')
        const echarts = await import('echarts')
        if (run !== vizRun) return
        const chart = echarts.init(output, undefined, { renderer: 'canvas' })
        chart.setOption(option, true)
        chartInstances.push(chart)
        chartOptions.set(block, option)
      }
    } catch (err) {
      block.classList.remove('rendered')
      svgByBlock.delete(block)
      chartOptions.delete(block)
      if (error) {
        error.textContent =
          block.dataset.viz === 'mermaid'
            ? `Mermaid 渲染失败：${(err as Error).message}`
            : `ECharts 配置解析失败：${(err as Error).message}`
      }
    }
  }
}

function resizeCharts() {
  for (const chart of chartInstances) chart.resize()
}

watch(
  () => [html.value, props.streaming] as const,
  () => {
    // v-html 会重建整个 DOM，先卸载内嵌预览组件再重新渲染
    disposeInlinePreviews()
    void nextTick(() => void renderVisuals())
  },
)

onMounted(() => {
  window.addEventListener('resize', resizeCharts)
  void nextTick(() => void renderVisuals())
})

onBeforeUnmount(() => {
  vizRun += 1
  disposeInlinePreviews()
  window.removeEventListener('resize', resizeCharts)
  disposeCharts()
})

async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    // 非安全上下文（http://IP）没有 clipboard API，退回 execCommand
    const area = document.createElement('textarea')
    area.value = text
    document.body.appendChild(area)
    area.select()
    document.execCommand('copy')
    area.remove()
  }
}

function openVizViewer(block: HTMLElement) {
  if (block.dataset.viz === 'mermaid') {
    const svg = svgByBlock.get(block)
    if (!svg) return
    viewer.kind = 'svg'
    viewer.svg = svg
    viewer.option = null
    viewer.title = 'Mermaid 图表'
    viewer.filename = 'diagram'
  } else {
    const option = chartOptions.get(block)
    if (!option) return
    viewer.kind = 'chart'
    viewer.option = option
    viewer.svg = ''
    viewer.title = 'ECharts 图表'
    viewer.filename = 'chart'
  }
  viewer.open = true
}

function imageFilename(img: HTMLImageElement): string {
  const localPath = img.dataset.localPath || ''
  const name = localPath.split('/').filter(Boolean).pop()
  if (name) return name
  const alt = (img.alt || '').trim()
  return alt ? `${alt}.png` : 'image.png'
}

function openImageViewer(block: HTMLElement) {
  const img = block.querySelector('img')
  if (!img?.src) return
  viewer.kind = 'image'
  viewer.src = img.src
  viewer.svg = ''
  viewer.option = null
  viewer.title = img.alt || '图片预览'
  viewer.filename = imageFilename(img)
  viewer.open = true
}

async function downloadImage(block: HTMLElement) {
  const img = block.querySelector('img')
  if (!img?.src) return
  try {
    const res = await fetch(img.src)
    if (!res.ok) throw new Error(String(res.status))
    saveBlob(imageFilename(img), await res.blob())
  } catch {
    // 跨域外链无法读取字节时，打开原图交给用户保存。
    window.open(img.src, '_blank', 'noopener,noreferrer')
  }
}

// ---------- 气泡内嵌 HTML 预览 ----------
// 卡片与 Vue 组件实例一一对应；v-html 重建时必须显式卸载，否则会泄漏。
const inlinePreviews = new Map<HTMLElement, () => void>()

function disposeInlinePreviews() {
  for (const dispose of inlinePreviews.values()) {
    try {
      dispose()
    } catch {
      /* ignore */
    }
  }
  inlinePreviews.clear()
}

async function toggleInlinePreview(card: HTMLElement, path: string, origin: FileOrigin) {
  const slot = card.querySelector<HTMLElement>('.md-file-preview')
  if (!slot) return
  const existing = inlinePreviews.get(card)
  if (existing) {
    existing()
    inlinePreviews.delete(card)
    card.classList.remove('previewing')
    slot.innerHTML = ''
    return
  }
  if (!props.threadId) return
  const trigger = card.querySelector<HTMLButtonElement>('[data-file-action="preview"]')
  if (trigger) {
    trigger.disabled = true
    trigger.textContent = '加载中'
  }
  try {
    const content = await fetchFileContent(props.threadId, path, origin)
    render(h(HtmlPreview, { content, height: '420px' }), slot)
    inlinePreviews.set(card, () => render(null, slot))
    card.classList.add('previewing')
  } catch (err) {
    slot.textContent = `预览失败：${(err as Error).message}`
    slot.classList.add('error')
  } finally {
    if (trigger) {
      trigger.disabled = false
      trigger.textContent = '预览'
    }
  }
}

async function onBodyClick(event: MouseEvent) {
  const target = event.target as HTMLElement | null

  // 会话文件卡片：预览 / 在右侧打开
  const fileAction = target?.closest('[data-file-action]') as HTMLElement | null
  if (fileAction) {
    const card = fileAction.closest('.md-file-card') as HTMLElement | null
    if (card) {
      const path = card.dataset.filePath || ''
      const origin = (card.dataset.fileOrigin as FileOrigin) || 'sandbox'
      if (fileAction.dataset.fileAction === 'drawer') emit('open-file', { path, origin })
      else await toggleInlinePreview(card, path, origin)
    }
    return
  }

  // 图表 Header：查看大图 / 全屏
  const vizAction = target?.closest('.viz-action') as HTMLElement | null
  if (vizAction) {
    const block = vizAction.closest('.viz-block') as HTMLElement | null
    if (block) openVizViewer(block)
    return
  }

  // 图片右下角：保存 / 全屏查看
  const imageAction = target?.closest('.md-image-action') as HTMLElement | null
  if (imageAction) {
    const block = imageAction.closest('.md-image-block') as HTMLElement | null
    if (!block) return
    if (imageAction.dataset.imageAction === 'download') await downloadImage(block)
    else openImageViewer(block)
    return
  }

  // 图表源码复制
  const vizButton = target?.closest('.viz-copy') as HTMLElement | null
  if (vizButton) {
    const block = vizButton.closest('.viz-block')
    const code = block?.querySelector('.viz-source code')?.textContent || ''
    if (!code) return
    await copyText(code)
    vizButton.classList.add('copied')
    window.setTimeout(() => vizButton.classList.remove('copied'), 1500)
    return
  }

  // 优先处理代码复制，避免链接点击逻辑误触
  const button = target?.closest('.code-copy') as HTMLElement | null
  if (button) {
    const block = button.closest('.code-block')
    const code = block?.querySelector('code')?.textContent || ''
    if (!code) return
    await copyText(code)

    const label = button.querySelector('span')
    button.classList.add('copied')
    if (label) label.textContent = '已复制'
    window.setTimeout(() => {
      button.classList.remove('copied')
      if (label) label.textContent = '复制'
    }, 1500)
    return
  }

  // 链接一律外部打开：浏览器新标签页，Electron 走默认浏览器
  const anchor = target?.closest('a[href]') as HTMLAnchorElement | null
  if (!anchor) return
  const href = anchor.getAttribute('href') || ''
  if (!/^(https?:\/\/|mailto:)/i.test(href)) return
  event.preventDefault()
  openExternalUrl(href)
}
</script>

<template>
  <div ref="container" class="markdown-body" v-html="html" @click="onBodyClick"></div>
  <MediaViewer
    v-model="viewer.open"
    :kind="viewer.kind"
    :src="viewer.src"
    :svg="viewer.svg"
    :option="viewer.option"
    :title="viewer.title"
    :filename="viewer.filename"
  />
</template>

<style scoped>
.markdown-body {
  font-size: 13px;
  line-height: 1.75;
  word-break: break-word;
}

.markdown-body :deep(> *:first-child) {
  margin-top: 0;
}

.markdown-body :deep(> *:last-child) {
  margin-bottom: 0;
}

.markdown-body :deep(p) {
  margin: 0 0 8px;
}

.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3),
.markdown-body :deep(h4) {
  margin: 14px 0 8px;
  line-height: 1.4;
  color: var(--text-1);
}

.markdown-body :deep(h1) {
  font-size: 18px;
}

.markdown-body :deep(h2) {
  font-size: 16px;
}

.markdown-body :deep(h3) {
  font-size: 14.5px;
}

.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  margin: 6px 0 10px;
  padding-left: 22px;
}

.markdown-body :deep(li) {
  margin: 3px 0;
}

.markdown-body :deep(li::marker) {
  color: var(--accent-cyan);
}

.markdown-body :deep(code) {
  padding: 1.5px 5px;
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 12px;
  color: #9fe8ff;
  border: 1px solid rgba(53, 224, 255, 0.18);
  border-radius: 5px;
  background: rgba(6, 18, 36, 0.8);
}

/* ---------- 会话文件卡片 ---------- */
.markdown-body :deep(.md-file-card) {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 8px 0;
  padding: 9px 11px;
  border: 1px solid var(--line);
  border-radius: 9px;
  background: rgba(6, 18, 36, 0.72);
}

.markdown-body :deep(.md-file-head) {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.markdown-body :deep(.md-file-icon) {
  display: inline-flex;
  flex: none;
  color: var(--accent-cyan);
}

.markdown-body :deep(.md-file-name) {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-1);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.markdown-body :deep(.md-file-origin) {
  flex: none;
  padding: 1px 7px;
  font-size: 9.5px;
  color: var(--text-3);
  border: 1px solid var(--line);
  border-radius: 999px;
}

.markdown-body :deep(.md-file-actions) {
  display: flex;
  flex: none;
  gap: 6px;
}

.markdown-body :deep(.md-file-action) {
  padding: 3px 9px;
  font-size: 11px;
  color: var(--text-2);
  cursor: pointer;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: transparent;
}

.markdown-body :deep(.md-file-action:hover:not(:disabled)) {
  color: var(--text-1);
  border-color: var(--line-strong);
}

.markdown-body :deep(.md-file-action.primary) {
  color: var(--accent-cyan);
  border-color: rgba(53, 224, 255, 0.35);
  background: rgba(53, 224, 255, 0.08);
}

.markdown-body :deep(.md-file-action:disabled) {
  opacity: 0.6;
  cursor: default;
}

.markdown-body :deep(.md-file-preview) {
  display: none;
  overflow: hidden;
  border-radius: 7px;
}

.markdown-body :deep(.md-file-card.previewing .md-file-preview) {
  display: block;
  height: 420px;
  border: 1px solid var(--line);
  background: #0b1220;
}

.markdown-body :deep(.md-file-preview.error) {
  display: block;
  padding: 8px 10px;
  font-size: 11.5px;
  color: var(--amber);
  border: 1px solid rgba(255, 182, 72, 0.3);
  border-radius: 7px;
  background: rgba(255, 182, 72, 0.05);
}

/* ---------- 代码块：语言标签 + 复制（顶部） ---------- */
.markdown-body :deep(.code-block),
.markdown-body :deep(.viz-block) {
  margin: 8px 0 12px;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 9px;
  background: rgba(4, 10, 22, 0.85);
}

/* ---------- Mermaid / ECharts 可视化 ---------- */
.markdown-body :deep(.viz-source) {
  margin: 0;
  border: 0;
  border-radius: 0;
  background: transparent;
}

.markdown-body :deep(.viz-output) {
  display: none;
  padding: 10px 12px;
  overflow-x: auto;
}

.markdown-body :deep(.viz-block.rendered .viz-source) {
  display: none;
}

.markdown-body :deep(.viz-block.rendered .viz-output) {
  display: block;
}

/* ECharts 需要明确高度才能绘制 canvas */
.markdown-body :deep(.viz-block[data-viz='echarts'].rendered .viz-output) {
  height: 420px;
}

.markdown-body :deep(.viz-output svg) {
  max-width: 100%;
  height: auto;
}

.markdown-body :deep(.viz-error) {
  padding: 0 12px 8px;
  font-size: 10.5px;
  color: #ff9e9e;
}

.markdown-body :deep(.viz-error:empty) {
  display: none;
}

.markdown-body :deep(.code-actions) {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.markdown-body :deep(.viz-copy),
.markdown-body :deep(.viz-action) {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  font-size: 10px;
  color: var(--text-3);
  border: 1px solid var(--line);
  border-radius: 999px;
  background: transparent;
  transition: color 0.15s, border-color 0.15s;
}

.markdown-body :deep(.viz-copy):hover,
.markdown-body :deep(.viz-action):hover {
  color: var(--accent-cyan);
  border-color: rgba(53, 224, 255, 0.4);
}

.markdown-body :deep(.viz-copy.copied) {
  color: var(--green);
  border-color: rgba(61, 220, 132, 0.5);
}

/* ---------- 图片块：右下角保存 / 全屏 ---------- */
.markdown-body :deep(.md-image-block) {
  position: relative;
  display: inline-block;
  max-width: 100%;
  line-height: 0;
}

.markdown-body :deep(.md-image-block img) {
  display: block;
  max-width: 100%;
  height: auto;
  border-radius: 8px;
}

.markdown-body :deep(.md-image-actions) {
  position: absolute;
  right: 8px;
  bottom: 8px;
  display: inline-flex;
  gap: 5px;
  padding: 3px;
  border: 1px solid rgba(126, 154, 198, 0.25);
  border-radius: 8px;
  background: rgba(3, 9, 18, 0.72);
  opacity: 0;
  transform: translateY(3px);
  transition: opacity 0.15s, transform 0.15s;
}

.markdown-body :deep(.md-image-block:hover .md-image-actions),
.markdown-body :deep(.md-image-block:focus-within .md-image-actions) {
  opacity: 1;
  transform: none;
}

.markdown-body :deep(.md-image-action) {
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  padding: 0;
  color: var(--text-2);
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
}

.markdown-body :deep(.md-image-action:hover) {
  color: var(--accent-cyan);
  border-color: rgba(55, 208, 196, 0.45);
  background: rgba(55, 208, 196, 0.1);
}

/* 触屏设备没有 hover，图片操作按钮常驻 */
@media (hover: none) {
  .markdown-body :deep(.md-image-actions) {
    opacity: 1;
    transform: none;
  }
}

.markdown-body :deep(.code-head) {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 4px 10px;
  border-bottom: 1px solid var(--line);
  background: rgba(18, 36, 68, 0.55);
}

.markdown-body :deep(.code-lang) {
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 10px;
  color: var(--text-3);
}

.markdown-body :deep(.code-copy) {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  font-size: 10px;
  color: var(--text-3);
  border: 1px solid var(--line);
  border-radius: 999px;
  background: transparent;
  transition: color 0.15s, border-color 0.15s;
}

.markdown-body :deep(.code-copy):hover {
  color: var(--accent-cyan);
  border-color: rgba(53, 224, 255, 0.4);
}

.markdown-body :deep(.code-copy.copied) {
  color: var(--green);
  border-color: rgba(61, 220, 132, 0.5);
}

.markdown-body :deep(.code-block pre) {
  margin: 0;
  padding: 10px 12px;
  overflow-x: auto;
  border: none;
  border-radius: 0;
  background: transparent;
}

.markdown-body :deep(pre) {
  margin: 8px 0 12px;
  padding: 10px 12px;
  overflow-x: auto;
  border: 1px solid var(--line);
  border-radius: 9px;
  background: rgba(4, 10, 22, 0.85);
}

.markdown-body :deep(pre code) {
  padding: 0;
  border: none;
  background: transparent;
  color: #d7e6ff;
  font-size: 12px;
  line-height: 1.6;
}

.markdown-body :deep(blockquote) {
  margin: 8px 0 12px;
  padding: 6px 12px;
  color: var(--text-2);
  border-left: 2px solid var(--accent);
  background: rgba(47, 100, 200, 0.08);
  border-radius: 0 6px 6px 0;
}

.markdown-body :deep(table) {
  width: 100%;
  margin: 8px 0 12px;
  border-collapse: collapse;
  font-size: 12px;
}

.markdown-body :deep(th),
.markdown-body :deep(td) {
  padding: 6px 10px;
  text-align: left;
  border: 1px solid var(--line);
}

.markdown-body :deep(th) {
  color: var(--text-1);
  background: rgba(47, 100, 200, 0.14);
}

.markdown-body :deep(a) {
  color: var(--accent-cyan);
  text-decoration: underline;
  text-underline-offset: 2px;
}

.markdown-body :deep(hr) {
  height: 1px;
  margin: 12px 0;
  border: none;
  background: var(--line);
}

.markdown-body :deep(img) {
  max-width: 100%;
  border-radius: 8px;
}
</style>
