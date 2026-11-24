<script setup lang="ts">
// PDF 预览：pdfjs-dist 自研渲染，不依赖浏览器/Electron 的 PDF 插件
import { onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { ArrowLeft, ArrowRight, ZoomIn, ZoomOut } from '@element-plus/icons-vue'
import * as pdfjsLib from 'pdfjs-dist'
import type {
  PDFDocumentLoadingTask,
  PDFDocumentProxy,
  RenderTask,
} from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'

pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl

const props = defineProps<{ url: string }>()

const canvas = ref<HTMLCanvasElement | null>(null)
const doc = shallowRef<PDFDocumentProxy | null>(null)
const task = shallowRef<PDFDocumentLoadingTask | null>(null)
const renderTask = shallowRef<RenderTask | null>(null)
const loading = ref(false)
const error = ref('')
const pageCount = ref(0)
const pageNo = ref(1)
const scale = ref(1.2)
// 渲染是异步的：页码/缩放快速切换时用 token 丢弃过期结果
let renderToken = 0

function reset() {
  renderToken += 1
  renderTask.value?.cancel()
  renderTask.value = null
  void task.value?.destroy()
  task.value = null
  doc.value = null
  pageCount.value = 0
  pageNo.value = 1
  error.value = ''
}

async function render() {
  const pdf = doc.value
  const target = canvas.value
  if (!pdf || !target) return
  const token = ++renderToken
  // 同一个 canvas 不能并发渲染；取消上一次后再画新的一页
  renderTask.value?.cancel()
  renderTask.value = null
  try {
    const page = await pdf.getPage(pageNo.value)
    if (token !== renderToken) return
    const viewport = page.getViewport({ scale: scale.value })
    // 按设备像素比放大画布，避免高分屏下文字发虚
    const ratio = window.devicePixelRatio || 1
    target.width = Math.floor(viewport.width * ratio)
    target.height = Math.floor(viewport.height * ratio)
    target.style.width = `${Math.floor(viewport.width)}px`
    target.style.height = `${Math.floor(viewport.height)}px`
    const current = page.render({
      canvas: target,
      viewport,
      transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0],
    })
    renderTask.value = current
    await current.promise
  } catch (err) {
    // 主动取消是正常流程，不当作错误展示
    if (token === renderToken) error.value = (err as Error).message
  }
}

async function load() {
  reset()
  if (!props.url) return
  loading.value = true
  try {
    const loadingTask = pdfjsLib.getDocument({ url: props.url })
    task.value = loadingTask
    const pdf = await loadingTask.promise
    doc.value = pdf
    pageCount.value = pdf.numPages
    pageNo.value = 1
    await render()
  } catch (err) {
    error.value = (err as Error).message || 'PDF 加载失败'
  } finally {
    loading.value = false
  }
}

function stepPage(delta: number) {
  const next = pageNo.value + delta
  if (next < 1 || next > pageCount.value) return
  pageNo.value = next
}

function zoom(delta: number) {
  scale.value = Math.min(3, Math.max(0.5, Number((scale.value + delta).toFixed(1))))
}

watch(() => props.url, load, { immediate: true })
watch([pageNo, scale], () => void render())
onBeforeUnmount(reset)
</script>

<template>
  <div class="pdf-preview">
    <div class="pdf-bar">
      <el-button
        class="pdf-btn"
        :icon="ArrowLeft"
        text
        circle
        size="small"
        title="上一页"
        :disabled="pageNo <= 1"
        @click="stepPage(-1)"
      />
      <span class="pdf-page mono">{{ pageNo }} / {{ pageCount || '-' }}</span>
      <el-button
        class="pdf-btn"
        :icon="ArrowRight"
        text
        circle
        size="small"
        title="下一页"
        :disabled="pageNo >= pageCount"
        @click="stepPage(1)"
      />
      <span class="pdf-divider"></span>
      <el-button
        class="pdf-btn"
        :icon="ZoomOut"
        text
        circle
        size="small"
        title="缩小"
        @click="zoom(-0.2)"
      />
      <span class="pdf-zoom mono">{{ Math.round(scale * 100) }}%</span>
      <el-button
        class="pdf-btn"
        :icon="ZoomIn"
        text
        circle
        size="small"
        title="放大"
        @click="zoom(0.2)"
      />
    </div>

    <div v-loading="loading" class="pdf-body">
      <p v-if="error" class="pdf-error">{{ error }}</p>
      <canvas v-show="!error" ref="canvas" class="pdf-canvas"></canvas>
    </div>
  </div>
</template>

<style scoped>
.pdf-preview {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  width: 100%;
}

.pdf-bar {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 6px 8px;
  border-bottom: 1px solid var(--line);
  background: rgba(6, 14, 26, 0.6);
}

.pdf-btn.el-button {
  width: 26px;
  height: 26px;
  margin-left: 0;
  padding: 0;
  color: var(--text-2);
}

.pdf-page,
.pdf-zoom {
  min-width: 58px;
  font-size: 11px;
  color: var(--text-2);
  text-align: center;
}

.pdf-divider {
  width: 1px;
  height: 14px;
  margin: 0 6px;
  background: var(--line);
}

.pdf-body {
  flex: 1;
  min-height: 0;
  padding: 12px;
  overflow: auto;
  text-align: center;
}

.pdf-canvas {
  max-width: 100%;
  background: #fff;
  box-shadow: 0 6px 18px rgba(2, 8, 20, 0.45);
}

.pdf-error {
  margin: 16px 8px;
  font-size: 12px;
  color: var(--amber);
}
</style>
