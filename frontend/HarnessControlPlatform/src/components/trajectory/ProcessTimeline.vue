<script setup lang="ts">
// 轨迹页「过程轨道」：横轴是全局轨迹索引（按时间戳排序后的 rank），不是真实时间。
// 所有轨道共用同一索引轴，每条轨迹一个等宽色块；空时间段不占任何宽度。
import { computed, nextTick, ref } from 'vue'
import type { TrajectoryEvent } from '@/api/trajectory'
import type { IconName } from '@/components/console/icons'
import AppIcon from '@/components/console/AppIcon.vue'

interface TimelineLane {
  key: string
  label: string
  color: string
  icon: IconName
}

const props = defineProps<{
  events: TrajectoryEvent[]
  selectedId: string
  lanes: ReadonlyArray<TimelineLane>
}>()

const emit = defineEmits<{ select: [eventId: string] }>()

const BAR_BASE = 12
const GAP_BASE = 3
const ZOOM_MIN = 1
const ZOOM_MAX = 3
const ZOOM_STEP = 0.25

const zoom = ref(1)
const scrollEl = ref<HTMLElement | null>(null)

const colWidth = computed(() => Math.round((BAR_BASE + GAP_BASE) * zoom.value))
const barWidth = computed(() => Math.max(Math.round(BAR_BASE * zoom.value), 4))
const zoomPercent = computed(() => Math.round(zoom.value * 100))

// 全局索引：所有轨迹按时间戳排序后的 rank，跨轨道共用。
const ordered = computed(() => {
  const list = props.events.map((event, order) => ({
    event,
    order,
    start: Date.parse(event.start),
  }))
  list.sort((a, b) => {
    const left = Number.isFinite(a.start) ? a.start : Number.POSITIVE_INFINITY
    const right = Number.isFinite(b.start) ? b.start : Number.POSITIVE_INFINITY
    return left - right || a.order - b.order
  })
  return list
})

const rows = computed(() => {
  const ranked = new Map<string, number>()
  ordered.value.forEach((item, rank) => ranked.set(item.event.id, rank))
  return props.lanes.map((lane) => ({
    ...lane,
    bars: props.events
      .filter((event) => event.type === lane.key)
      .map((event) => ({ event, rank: ranked.get(event.id) ?? 0 }))
      .sort((a, b) => a.rank - b.rank),
  }))
})

const trackWidth = computed(() => Math.max(ordered.value.length * colWidth.value, colWidth.value))

function pad(value: number): string {
  return String(value).padStart(2, '0')
}

function timeLabel(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

function durationText(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—'
  if (ms < 1000) return `${ms}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)}s`
  const minutes = Math.floor(ms / 60_000)
  const seconds = Math.round((ms % 60_000) / 1000)
  if (minutes < 60) return `${minutes}m${seconds}s`
  const hours = Math.floor(minutes / 60)
  return `${hours}h${minutes % 60}m`
}

function barTitle(event: TrajectoryEvent): string {
  return `${event.label} · ${timeLabel(event.start)} · ${durationText(event.duration_ms)}`
}

/** 缩放只改变色块与列宽，索引顺序不变。 */
function applyZoom(next: number, anchor = 0.5) {
  const clamped = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, next))
  if (Math.abs(clamped - zoom.value) < 0.001) return
  const el = scrollEl.value
  const ratio = el
    ? (el.scrollLeft + el.clientWidth * anchor) / Math.max(1, el.scrollWidth)
    : anchor
  zoom.value = clamped
  if (!el) return
  void nextTick(() => {
    el.scrollLeft = Math.max(0, ratio * el.scrollWidth - el.clientWidth * anchor)
  })
}

function zoomBy(step: number) {
  applyZoom(zoom.value + step)
}

function onZoomInput(event: Event) {
  applyZoom(Number((event.target as HTMLInputElement).value))
}

function onWheel(event: WheelEvent) {
  // 只在按住 Ctrl / Cmd 时缩放，普通滚轮仍然滚动页面。
  if (!event.ctrlKey && !event.metaKey) return
  event.preventDefault()
  const el = scrollEl.value
  const anchor = el
    ? Math.min(1, Math.max(0, (event.clientX - el.getBoundingClientRect().left) / el.clientWidth))
    : 0.5
  applyZoom(zoom.value + (event.deltaY < 0 ? ZOOM_STEP : -ZOOM_STEP), anchor)
}
</script>

<template>
  <div class="process-track">
    <header class="track-head">
      <div class="track-zoom">
        <button type="button" class="zoom-btn" title="缩小" @click="zoomBy(-ZOOM_STEP)">−</button>
        <input
          class="zoom-range"
          type="range"
          :min="ZOOM_MIN"
          :max="ZOOM_MAX"
          :step="ZOOM_STEP"
          :value="zoom"
          aria-label="轨道缩放"
          @input="onZoomInput"
        />
        <button type="button" class="zoom-btn" title="放大" @click="zoomBy(ZOOM_STEP)">+</button>
        <button type="button" class="zoom-reset" title="恢复默认缩放" @click="applyZoom(1)">
          {{ zoomPercent }}%
        </button>
      </div>
    </header>

    <div class="track-body">
      <!-- 左侧轨道名固定，不随索引轴横向滚动 -->
      <div class="track-labels">
        <div v-for="row in rows" :key="row.key" class="track-label">
          <i :style="{ background: row.color }"></i>
          <AppIcon :name="row.icon" :size="12" />
          <span>{{ row.label }}</span>
        </div>
      </div>

      <div ref="scrollEl" class="track-scroll" @wheel="onWheel">
        <div class="track-inner" :style="{ width: `${trackWidth}px` }">
          <div v-for="row in rows" :key="row.key" class="track-row">
            <button
              v-for="bar in row.bars"
              :key="bar.event.id"
              class="track-bar"
              type="button"
              :class="{ selected: bar.event.id === selectedId }"
              :style="{
                left: `${bar.rank * colWidth}px`,
                width: `${barWidth}px`,
                background: row.color,
              }"
              :title="barTitle(bar.event)"
              :aria-label="bar.event.label"
              @click="emit('select', bar.event.id)"
            ></button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.process-track {
  min-width: 0;
}

.track-head {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  margin-bottom: 8px;
}

.track-zoom {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: none;
}

.zoom-btn {
  display: grid;
  place-items: center;
  width: 20px;
  height: 20px;
  padding: 0;
  font-size: 13px;
  line-height: 1;
  color: var(--text-2);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: transparent;
  cursor: pointer;
}

.zoom-btn:hover {
  color: var(--text-1);
  border-color: var(--line-strong);
}

.zoom-range {
  width: 108px;
  accent-color: var(--accent);
  cursor: pointer;
}

.zoom-reset {
  min-width: 48px;
  padding: 2px 6px;
  font-size: 10px;
  font-variant-numeric: tabular-nums;
  color: var(--text-3);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: transparent;
  cursor: pointer;
}

.zoom-reset:hover {
  color: var(--text-1);
  border-color: var(--line-strong);
}

.track-body {
  display: flex;
  align-items: flex-start;
  min-width: 0;
}

.track-labels {
  display: flex;
  flex: none;
  flex-direction: column;
  gap: 5px;
  width: 96px;
  padding-right: 8px;
}

.track-label {
  display: flex;
  align-items: center;
  gap: 5px;
  flex: none;
  height: 16px;
  font-size: 10.5px;
  color: var(--text-2);
  white-space: nowrap;
}

.track-label i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex: none;
}

.track-scroll {
  flex: 1;
  min-width: 0;
  overflow-x: auto;
  overflow-y: hidden;
}

.track-scroll::-webkit-scrollbar {
  height: 8px;
}

.track-scroll::-webkit-scrollbar-track {
  background: transparent;
}

.track-scroll::-webkit-scrollbar-thumb {
  border-radius: 4px;
  background: var(--line-strong);
}

.track-inner {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.track-row {
  position: relative;
  flex: none;
  height: 16px;
}

.track-bar {
  position: absolute;
  top: 0;
  height: 16px;
  padding: 0;
  border: none;
  border-radius: 2px;
  opacity: 0.82;
  cursor: pointer;
  transition: opacity 0.15s, box-shadow 0.15s;
}

.track-bar:hover {
  opacity: 1;
}

.track-bar.selected {
  opacity: 1;
  box-shadow: 0 0 0 1px rgba(230, 237, 249, 0.85);
}
</style>
