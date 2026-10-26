<script setup lang="ts">
// Markdown 渲染：marked 解析 + DOMPurify 消毒
import { computed } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

const props = defineProps<{ content: string }>()

const html = computed(() => {
  const raw = marked.parse(props.content || '', {
    async: false,
    gfm: true,
    breaks: true,
  }) as string
  return DOMPurify.sanitize(raw)
})
</script>

<template>
  <div class="markdown-body" v-html="html"></div>
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
