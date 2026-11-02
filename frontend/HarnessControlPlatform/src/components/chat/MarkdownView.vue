<script setup lang="ts">
// Markdown 渲染：marked 解析 + DOMPurify 消毒
// 代码块额外带语言标签与一键复制按钮（头部，位于代码上方）
import { computed } from 'vue'
import { marked, Renderer, type Tokens } from 'marked'
import DOMPurify from 'dompurify'

const props = defineProps<{ content: string }>()

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

const renderer = new Renderer()

// 链接统一带 target=_blank；点击事件里再交给桌面壳或浏览器新开窗口。
renderer.link = function (this: Renderer, token: Tokens.Link) {
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

// 代码块自定义渲染：语言标签 + 复制按钮放在代码上方
renderer.code = ({ text, lang, escaped }) => {
  const language = (lang || '').trim().split(/\s+/)[0] || 'text'
  const body = escaped ? text : escapeHtml(text)
  const languageAttr = escapeHtml(language)
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

async function onBodyClick(event: MouseEvent) {
  const target = event.target as HTMLElement | null

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
  <div class="markdown-body" v-html="html" @click="onBodyClick"></div>
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

/* ---------- 代码块：语言标签 + 复制（顶部） ---------- */
.markdown-body :deep(.code-block) {
  margin: 8px 0 12px;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 9px;
  background: rgba(4, 10, 22, 0.85);
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
