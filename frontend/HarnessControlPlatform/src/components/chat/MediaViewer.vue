<script setup lang="ts">
// 全屏媒体查看器：支持图片 / Mermaid SVG 缩放、旋转、拖拽、重置，
// 也支持 ECharts 全屏交互与保存为图片。
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import {
  Close,
  Download,
  FullScreen,
  RefreshLeft,
  RefreshRight,
  Refresh,
  ZoomIn,
  ZoomOut,
} from '@element-plus/icons-vue'
import type { ECharts } from 'echarts'
import { saveBlob } from '@/stores/files'
import type { MediaKind } from './mediaTypes'
import { normalizeChartOption } from './chartOption'

const props = defineProps<{
  modelValue: boolean
  kind: MediaKind
  src?: string
  svg?: string
  option?: Record<string, unknown> | null
  title?: string
  filename?: string
}>()

const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()

const scale = ref(1)
const rotate = ref(0)
const tx = ref(0)
const ty = ref(0)
const dragging = ref(false)
const chartEl = ref<HTMLElement | null>(null)
let chart: ECharts | null = null
let dragStart = { x: 0, y: 0, tx: 0, ty: 0 }

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

const zoomLabel = computed(() => `${Math.round(scale.value * 100)}%`)

const transformStyle = computed(() => ({
  transform: `translate(${tx.value}px, ${ty.value}px) scale(${scale.value}) rotate(${rotate.value}deg)`,
}))

function resetView() {
  scale.value = 1
  rotate.value = 0
  tx.value = 0
  ty.value = 0
  if (props.kind === 'chart' && chart && props.option) {
    chart.setOption(normalizeChartOption(props.option), true)
    chart.resize()
  }
}

function zoomBy(delta: number) {
  if (props.kind === 'chart') return
  scale.value = Math.min(8, Math.max(0.2, scale.value + delta))
}

function onWheel(event: WheelEvent) {
  if (props.kind === 'chart') return
  event.preventDefault()
  const factor = Math.exp(-event.deltaY * 0.0015)
  scale.value = Math.min(8, Math.max(0.2, scale.value * factor))
}

function onPointerDown(event: PointerEvent) {
  if (props.kind === 'chart') return
  const target = event.currentTarget as HTMLElement
  target.setPointerCapture(event.pointerId)
  dragging.value = true
  dragStart = { x: event.clientX, y: event.clientY, tx: tx.value, ty: ty.value }
}

function onPointerMove(event: PointerEvent) {
  if (!dragging.value) return
  tx.value = dragStart.tx + (event.clientX - dragStart.x)
  ty.value = dragStart.ty + (event.clientY - dragStart.y)
}

function onPointerUp() {
  dragging.value = false
}

function close() {
  visible.value = false
}

function onKeydown(event: KeyboardEvent) {
  if (!visible.value) return
  if (event.key === 'Escape') close()
  if (event.key === '+' || event.key === '=') zoomBy(0.15)
  if (event.key === '-') zoomBy(-0.15)
  if (event.key === '0') resetView()
}

watch(
  () => props.modelValue,
  async (open) => {
    document.body.style.overflow = open ? 'hidden' : ''
    if (!open) {
      chart?.dispose()
      chart = null
      return
    }
    resetView()
    await nextTick()
    if (props.kind === 'chart' && props.option && chartEl.value) {
      const echarts = await import('echarts')
      chart = echarts.init(chartEl.value, undefined, { renderer: 'canvas' })
      chart.setOption(normalizeChartOption(props.option), true)
      chart.resize()
    }
  },
)

async function download() {
  if (props.kind === 'chart') {
    if (!chart) return
    const url = chart.getDataURL({
      type: 'png',
      pixelRatio: 2,
      backgroundColor: '#04101c',
    })
    const link = document.createElement('a')
    link.href = url
    link.download = `${props.filename || 'chart'}.png`
    link.click()
    return
  }
  if (props.kind === 'svg') {
    if (!props.svg) return
    saveBlob(
      `${props.filename || 'diagram'}.svg`,
      new Blob([props.svg], { type: 'image/svg+xml;charset=utf-8' }),
    )
    return
  }
  if (!props.src) return
  try {
    const res = await fetch(props.src)
    if (!res.ok) throw new Error(String(res.status))
    saveBlob(
      props.filename || 'image',
      await res.blob(),
    )
  } catch {
    // 外链图片可能不允许跨域读取，退回新窗口打开后由用户保存。
    window.open(props.src, '_blank', 'noopener,noreferrer')
  }
}

function onResize() {
  chart?.resize()
}

watch(visible, (open) => {
  if (open) window.addEventListener('resize', onResize)
  else window.removeEventListener('resize', onResize)
})

onBeforeUnmount(() => {
  document.body.style.overflow = ''
  window.removeEventListener('keydown', onKeydown)
  window.removeEventListener('resize', onResize)
  chart?.dispose()
})

window.addEventListener('keydown', onKeydown)
</script>

<template>
  <Teleport to="body">
    <Transition name="mv">
      <div v-if="visible" class="mv-mask" @click.self="close">
        <div class="mv-shell" @click.stop>
          <header class="mv-head">
            <div class="mv-title">
              <el-icon><FullScreen /></el-icon>
              <span>{{ title || '预览' }}</span>
            </div>
            <div class="mv-tools">
              <template v-if="kind !== 'chart'">
                <button class="mv-btn" title="缩小 (-)" @click="zoomBy(-0.15)">
                  <el-icon><ZoomOut /></el-icon>
                </button>
                <span class="mv-zoom">{{ zoomLabel }}</span>
                <button class="mv-btn" title="放大 (+)" @click="zoomBy(0.15)">
                  <el-icon><ZoomIn /></el-icon>
                </button>
                <button class="mv-btn" title="向左旋转" @click="rotate -= 90">
                  <el-icon><RefreshLeft /></el-icon>
                </button>
                <button class="mv-btn" title="向右旋转" @click="rotate += 90">
                  <el-icon><RefreshRight /></el-icon>
                </button>
              </template>
              <button class="mv-btn" title="重置 (0)" @click="resetView">
                <el-icon><Refresh /></el-icon>
              </button>
              <button class="mv-btn" title="保存到本地" @click="download">
                <el-icon><Download /></el-icon>
              </button>
              <button class="mv-btn close" title="关闭 (Esc)" @click="close">
                <el-icon><Close /></el-icon>
              </button>
            </div>
          </header>

          <div
            class="mv-stage"
            :class="{ 'mv-chart-stage': kind === 'chart', dragging }"
            @wheel="onWheel"
            @pointerdown="onPointerDown"
            @pointermove="onPointerMove"
            @pointerup="onPointerUp"
            @pointercancel="onPointerUp"
            @dblclick="resetView"
          >
            <div v-if="kind === 'chart'" ref="chartEl" class="mv-chart"></div>
            <div
              v-else-if="kind === 'svg'"
              class="mv-media"
              :style="transformStyle"
              v-html="svg"
            ></div>
            <img
              v-else
              class="mv-media"
              :style="transformStyle"
              :src="src"
              :alt="title || ''"
              draggable="false"
            />
          </div>

          <footer class="mv-foot">
            <span v-if="kind !== 'chart'">滚轮缩放 · 拖拽平移 · 双击重置</span>
            <span v-else>图表支持 tooltip / dataZoom 等交互</span>
          </footer>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.mv-mask {
  position: fixed;
  inset: 0;
  z-index: 4000;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(1, 5, 12, 0.88);
  backdrop-filter: blur(8px);
}

.mv-shell {
  display: flex;
  flex-direction: column;
  width: min(1400px, 96vw);
  height: min(900px, 92vh);
  overflow: hidden;
  border: 1px solid rgba(55, 208, 196, 0.35);
  border-radius: 14px;
  background: linear-gradient(160deg, rgba(8, 18, 32, 0.98), rgba(3, 9, 18, 0.98));
  box-shadow: 0 30px 100px rgba(0, 0, 0, 0.7);
}

.mv-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--line);
  background: rgba(10, 24, 42, 0.7);
}

.mv-title {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
  font-size: 12.5px;
  color: var(--text-1);
}

.mv-title span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mv-tools {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: none;
}

.mv-btn {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  padding: 0;
  color: var(--text-2);
  border: 1px solid var(--line);
  border-radius: 7px;
  background: rgba(10, 18, 34, 0.6);
  transition: color 0.15s, border-color 0.15s, background 0.15s;
}

.mv-btn:hover {
  color: var(--accent-cyan);
  border-color: rgba(55, 208, 196, 0.5);
  background: rgba(55, 208, 196, 0.08);
}

.mv-btn.close:hover {
  color: #ff8e8e;
  border-color: rgba(255, 110, 110, 0.5);
}

.mv-zoom {
  min-width: 44px;
  font-family: var(--font-mono);
  font-size: 10.5px;
  text-align: center;
  color: var(--text-3);
}

.mv-stage {
  position: relative;
  flex: 1;
  display: grid;
  place-items: center;
  overflow: hidden;
  cursor: grab;
  background:
    radial-gradient(circle at 50% 45%, rgba(55, 208, 196, 0.05), transparent 60%),
    repeating-linear-gradient(0deg, rgba(55, 208, 196, 0.025) 0 1px, transparent 1px 32px),
    repeating-linear-gradient(90deg, rgba(55, 208, 196, 0.025) 0 1px, transparent 1px 32px);
}

.mv-stage.dragging {
  cursor: grabbing;
}

.mv-chart-stage {
  cursor: default;
  padding: 12px;
}

.mv-media {
  max-width: 86%;
  max-height: 86%;
  transform-origin: center center;
  user-select: none;
  transition: transform 0.08s linear;
}

img.mv-media {
  border-radius: 8px;
  box-shadow: 0 18px 60px rgba(0, 0, 0, 0.55);
}

.mv-media :deep(svg) {
  max-width: 100%;
  height: auto;
  display: block;
}

.mv-chart {
  width: 100%;
  height: 100%;
}

.mv-foot {
  padding: 8px 14px;
  font-size: 10.5px;
  color: var(--text-3);
  text-align: center;
  border-top: 1px solid var(--line);
}

.mv-enter-active,
.mv-leave-active {
  transition: opacity 0.18s ease;
}

.mv-enter-from,
.mv-leave-to {
  opacity: 0;
}
</style>
