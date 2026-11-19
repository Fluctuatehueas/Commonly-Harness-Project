<script setup lang="ts">
// 轨迹页「上下文」视图：ECharts 占用曲线 + 软/硬水位参考线 + 压缩节点 + 交互 Adorner。
// Adorner 的数据结构与文案保持原样，仅把 SVG 手绘换成 ECharts 渲染。
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import * as echarts from 'echarts/core'
import { LineChart, ScatterChart } from 'echarts/charts'
import {
  GridComponent,
  MarkLineComponent,
  MarkPointComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { TrajectoryEvent, TrajectoryRun } from '@/api/trajectory'
import AppIcon from '@/components/console/AppIcon.vue'
import InfoHint from '@/components/common/InfoHint.vue'

echarts.use([
  LineChart,
  ScatterChart,
  GridComponent,
  MarkLineComponent,
  MarkPointComponent,
  CanvasRenderer,
])

const props = defineProps<{
  runs: TrajectoryRun[]
  selectedRunId: string
}>()

const CHART_HEIGHT = 240

const scopedRuns = computed(() =>
  props.selectedRunId
    ? props.runs.filter((run) => run.run_id === props.selectedRunId)
    : props.runs,
)

const events = computed<TrajectoryEvent[]>(() =>
  scopedRuns.value
    .flatMap((run) => run.events)
    .sort((a, b) => String(a.start).localeCompare(String(b.start))),
)

// Token 统计归属上下文视图：会话（或选中轮次）的累计输入/输出与总量。
const tokens = computed(() =>
  scopedRuns.value.reduce(
    (total, run) => {
      total.input += run.summary.tokens?.input || 0
      total.output += run.summary.tokens?.output || 0
      total.total += run.summary.tokens?.total || 0
      return total
    },
    { input: 0, output: 0, total: 0 },
  ),
)

const points = computed(() =>
  events.value.filter(
    (event) => event.context && typeof event.context.used_tokens === 'number',
  ),
)

const compactions = computed(() => events.value.filter((event) => event.type === 'compaction'))

const windowSize = computed<number>(() => {
  const fromBudget = compactions.value
    .map((event) => Number(event.budget?.window || 0))
    .filter((value) => value > 0)
  if (fromBudget.length) return Math.max(...fromBudget)
  const limits = points.value
    .map((event) => Number(event.context?.limit || 0))
    .filter((value) => value > 0)
  return limits.length ? Math.max(...limits) : 128000
})

// 软/硬水位优先取真实预算；没有压缩记录时按文档默认比例回退。
const softWatermark = computed<number>(() => {
  const values = compactions.value
    .map((event) => Number(event.budget?.soft || 0))
    .filter((value) => value > 0)
  const latest = values.length ? values[values.length - 1] : undefined
  return latest ?? Math.round(windowSize.value * 0.616)
})

const hardWatermark = computed<number>(() => {
  const values = compactions.value
    .map((event) => Number(event.budget?.hard || 0))
    .filter((value) => value > 0)
  const latest = values.length ? values[values.length - 1] : undefined
  return latest ?? Math.round(windowSize.value * 0.774)
})

const peak = computed(() =>
  Math.max(0, ...points.value.map((event) => Number(event.context?.used_tokens || 0))),
)
const lowest = computed(() => {
  const values = points.value
    .map((event) => Number(event.context?.used_tokens || 0))
    .filter((value) => value > 0)
  return values.length ? Math.min(...values) : 0
})
const saved = computed(() =>
  compactions.value.reduce(
    (sum, event) => sum + Number(event.compaction_result?.saved_tokens || 0),
    0,
  ),
)

const maxY = computed(() =>
  Math.max(windowSize.value, peak.value, hardWatermark.value, 1) * 1.02,
)

// 每个压缩点：在曲线上的索引 + 压缩前/后数值
const markers = computed(() =>
  compactions.value.map((event) => {
    let index = points.value.findIndex((item) => item.start >= event.start)
    if (index < 0) index = Math.max(points.value.length - 1, 0)
    return {
      event,
      index: Math.max(index, 0),
      before: Number(event.trigger?.used_tokens || 0),
      after: Number(event.compaction_result?.after_tokens || points.value[index]?.context?.used_tokens || 0),
    }
  }),
)

// ---------- Adorner 交互 ----------
type AdornerKind = 'point' | 'compaction' | 'soft' | 'hard' | 'limit'
interface AdornerRow {
  key: string
  value: string
}
interface AdornerData {
  kind: AdornerKind
  title: string
  rows: AdornerRow[]
  /** 画布内像素坐标（相对 chart 容器） */
  x: number
  y: number
  flip: boolean
}

const hover = ref<AdornerData | null>(null)
const selectedId = ref<string>('')

watch(
  compactions,
  (items) => {
    if (!items.some((item) => item.id === selectedId.value)) {
      selectedId.value = items[items.length - 1]?.id || ''
    }
  },
  { immediate: true },
)

const selected = computed(
  () => compactions.value.find((item) => item.id === selectedId.value) || null,
)

const fmt = (value: number) => value.toLocaleString('zh-CN')

// 各行含义的人话解释，InfoHint 与其它页面复用同一套文案口径。
const HINTS = {
  trigger:
    '系统为什么在这个时刻整理上下文。\n· 软水位：上下文用到设定的比例，系统主动提前整理；\n· 硬水位：快到上限了，必须整理；\n· 手动：助手判断当前阶段已收敛，自己发起整理。',
  before:
    '整理之前，这一轮发给模型的全部内容折算出的 token 数，包含历史对话、工具结果、系统提示与你上传的资料。',
  after:
    '整理之后剩下的 token 数。数字越小，说明释放的空间越多，后面的对话能撑得更久。',
  saved:
    '压缩前后之差，也就是这次整理腾出来的上下文空间。数值越大，越能减少后续的重复整理。',
  fidelity:
    '系统在整理前会把关键信息挑出来锁定：你的要求、文件路径、命令、错误码、任务编号、人工确认结果。\n压缩后会逐条核对，这里显示“留住了几条 / 共几条”。100% 表示关键信息全部保住。',
  duration:
    '这次整理的耗时，包含生成摘要、保存原文、核对关键信息的时间。整理期间对话会短暂暂停。',
  retrieval:
    '压缩后如果助手需要细节，会回头读取保存的原文。\n这里统计读取了几次、以及这些读取额外消耗了多少 token。数字越小说明摘要越够用。',
  integrity:
    '系统对这条压缩记录做了指纹校验，用来确认记录没有被改动过。\n“旧记录”表示这次压缩发生在校验功能上线之前，没有指纹可比对。',
  archive:
    '被压缩掉的原始对话会完整保存成文件，助手需要时可以回去查。这里显示的就是保存位置。',
  summary:
    '压缩后真正放回上下文的那段摘要。它是后续对话能看到的“记忆”，因此必须保留任务目标、结论和未完成事项。',
  verdict:
    '系统对这次压缩质量的评价：时机是否合适、关键信息是否保住、有没有出现需要人工复核的风险。',
} as const

const TRIGGER_LABELS: Record<string, string> = {
  soft_watermark: '软水位：主动提前整理',
  hard_watermark: '硬水位：接近上限，强制整理',
  defer_limit: '延迟用尽后整理',
  manual: '助手主动整理',
}

function triggerLabel(event: TrajectoryEvent): string {
  const type = String(event.trigger?.type || 'unknown')
  return TRIGGER_LABELS[type] || type
}

function integrityState(event: TrajectoryEvent): { label: string; tone: string } {
  const integrity = event.integrity
  if (!integrity?.hash) return { label: '旧记录（无指纹）', tone: 'unknown' }
  return integrity.verified
    ? { label: '校验通过', tone: 'ok' }
    : { label: '校验未通过', tone: 'bad' }
}

function timeLabel(value?: string): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleTimeString('zh-CN', { hour12: false })
}

function shortTime(value?: string): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
}

const apx = (value: number) => `${fmt(value)} tok`

function buildPointAdorner(index: number, px: number, py: number): AdornerData {
  const event = points.value[index]
  const used = Number(event?.context?.used_tokens || 0)
  const pct = windowSize.value ? (used / windowSize.value) * 100 : 0
  const gap = softWatermark.value - used
  return {
    kind: 'point',
    title: `模型调用 #${index + 1}`,
    rows: [
      { key: '时间', value: timeLabel(event?.start) },
      { key: '已用上下文', value: apx(used) },
      { key: '占窗口', value: `${pct.toFixed(1)}%` },
      {
        key: gap >= 0 ? '距软水位' : '超出软水位',
        value: apx(Math.abs(gap)),
      },
      { key: '本步', value: String(event?.label || '') },
    ],
    x: px,
    y: py,
    flip: py > CHART_HEIGHT * 0.5,
  }
}

function buildCompactionAdorner(
  marker: (typeof markers.value)[number],
  px: number,
  py: number,
): AdornerData {
  const { event, before, after } = marker
  return {
    kind: 'compaction',
    title: '上下文压缩',
    rows: [
      { key: '触发', value: String(event.trigger?.type || 'unknown') },
      { key: '压缩前', value: apx(before) },
      { key: '压缩后', value: apx(after) },
      { key: '节省', value: apx(Math.max(before - after, 0)) },
      {
        key: '锚点保真',
        value: `${event.evaluation?.anchors_kept ?? 0}/${event.evaluation?.anchors_total ?? 0}`,
      },
      { key: '耗时', value: `${fmt(Number(event.duration_ms || 0))} ms` },
    ],
    x: px,
    y: py,
    flip: py > CHART_HEIGHT * 0.5,
  }
}

function buildRefAdorner(
  kind: 'soft' | 'hard' | 'limit',
  px: number,
  py: number,
): AdornerData {
  let title = '软水位'
  let value = softWatermark.value
  let desc = '达到后允许压缩'
  if (kind === 'hard') {
    title = '硬水位'
    value = hardWatermark.value
    desc = '必须压缩'
  } else if (kind === 'limit') {
    title = '窗口上限'
    value = windowSize.value
    desc = '模型上下文窗口'
  }
  return {
    kind,
    title,
    rows: [
      { key: '阈值', value: apx(value) },
      {
        key: '占窗口',
        value: `${((value / (windowSize.value || 1)) * 100).toFixed(1)}%`,
      },
      { key: '含义', value: desc },
    ],
    x: px,
    y: py,
    flip: py > CHART_HEIGHT * 0.5,
  }
}

// ---------- ECharts ----------
const chartEl = ref<HTMLDivElement | null>(null)
const chart = shallowRef<echarts.ECharts | null>(null)
let observer: ResizeObserver | null = null

const axisTextColor = '#5c6f92'
const splitLineColor = 'rgba(126, 154, 198, 0.1)'

function buildOption(): echarts.EChartsCoreOption {
  const categories = points.value.map((_, i) => String(i))
  const lineData = points.value.map((event) => Number(event.context?.used_tokens || 0))
  const compactionDots = markers.value.flatMap((marker) => [
    { value: [String(marker.index), marker.before], symbol: 'circle', symbolSize: 7, itemStyle: { color: '#b07cff', borderColor: 'rgba(10,14,26,0.9)', borderWidth: 1 } },
    { value: [String(marker.index), marker.after], symbol: 'circle', symbolSize: 7, itemStyle: { color: '#37d0c4', borderColor: 'rgba(10,14,26,0.9)', borderWidth: 1 } },
  ])
  const activeDots = markers.value
    .filter((marker) => marker.event.id === selectedId.value)
    .flatMap((marker) => [
      { value: [String(marker.index), marker.before] },
      { value: [String(marker.index), marker.after] },
    ])

  const refLines = [
    { value: windowSize.value, color: 'rgba(226, 96, 94, 0.55)', label: `窗口上限 ${fmt(windowSize.value)}` },
    { value: hardWatermark.value, color: 'rgba(242, 176, 60, 0.5)', label: `硬水位 ${fmt(hardWatermark.value)}` },
    { value: softWatermark.value, color: 'rgba(126, 224, 210, 0.5)', label: `软水位 ${fmt(softWatermark.value)}` },
  ]

  return {
    animationDuration: 320,
    grid: { left: 46, right: 76, top: 18, bottom: 26 },
    xAxis: {
      type: 'category',
      data: categories,
      boundaryGap: false,
      axisLine: { lineStyle: { color: splitLineColor } },
      axisTick: { show: false },
      axisLabel: {
        color: axisTextColor,
        fontSize: 10,
        interval: 'auto',
        formatter: (value: string) => shortTime(points.value[Number(value)]?.start),
      },
    },
    yAxis: {
      type: 'value',
      max: maxY.value,
      axisLabel: {
        color: axisTextColor,
        fontSize: 10,
        formatter: (value: number) => `${Math.round(value / 1000)}k`,
      },
      splitLine: { lineStyle: { color: splitLineColor } },
    },
    series: [
      {
        name: '上下文占用',
        type: 'line',
        data: lineData,
        connectNulls: true,
        showSymbol: false,
        lineStyle: { color: '#5b8cff', width: 1.8 },
        areaStyle: {
          // 用对象形式的线性渐变，避免依赖 echarts.graphic 的运行时导出
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(91,140,255,0.34)' },
              { offset: 1, color: 'rgba(91,140,255,0)' },
            ],
          },
        },
        markLine: {
          silent: true,
          symbol: 'none',
          data: [
            ...refLines.map((line) => ({
              yAxis: line.value,
              lineStyle: { color: line.color, type: [5, 5], width: 1 },
              label: {
                show: true,
                position: 'insideEndTop',
                formatter: line.label,
                color: line.color,
                fontSize: 10,
              },
            })),
            ...markers.value.map((marker) => ({
              xAxis: String(marker.index),
              lineStyle: { color: 'rgba(176, 124, 255, 0.45)', type: [3, 3], width: 1 },
              label: { show: false },
            })),
          ],
        },
      },
      {
        name: '压缩节点',
        type: 'scatter',
        data: compactionDots,
        z: 4,
        symbolSize: 7,
      },
      {
        name: '选中压缩',
        type: 'scatter',
        data: activeDots,
        z: 5,
        symbolSize: 13,
        itemStyle: { color: 'transparent', borderColor: '#ffffff', borderWidth: 1.4 },
      },
    ],
  }
}

function renderChart() {
  const el = chartEl.value
  if (!el) return
  if (!chart.value) {
    chart.value = echarts.init(el, undefined, { renderer: 'canvas' })
    bindEvents()
  }
  chart.value.setOption(buildOption(), { notMerge: true })
}

function resizeChart() {
  chart.value?.resize()
}

function pixelOfX(index: number): number | null {
  const instance = chart.value
  if (!instance) return null
  const pixel = instance.convertToPixel({ xAxisIndex: 0 }, String(index))
  return typeof pixel === 'number' ? pixel : null
}

function pixelOfY(value: number): number | null {
  const instance = chart.value
  if (!instance) return null
  const pixel = instance.convertToPixel({ yAxisIndex: 0 }, value)
  return typeof pixel === 'number' ? pixel : null
}

function bindEvents() {
  const instance = chart.value
  if (!instance) return

  instance.getZr().on('mousemove', (event) => {
    const px = event.offsetX
    const py = event.offsetY

    // 参考线优先：靠近软/硬/上限线时给出阈值说明
    const refs: Array<['limit' | 'hard' | 'soft', number]> = [
      ['limit', windowSize.value],
      ['hard', hardWatermark.value],
      ['soft', softWatermark.value],
    ]
    for (const [kind, value] of refs) {
      const y = pixelOfY(value)
      if (y !== null && Math.abs(py - y) <= 8) {
        hover.value = buildRefAdorner(kind, px, y)
        return
      }
    }

    // 压缩节点：横向 10px 内优先展示压缩详情
    for (const marker of markers.value) {
      const x = pixelOfX(marker.index)
      if (x !== null && Math.abs(px - x) <= 10) {
        const y = Math.min(
          pixelOfY(marker.before) ?? py,
          pixelOfY(marker.after) ?? py,
        )
        hover.value = buildCompactionAdorner(marker, x, y)
        return
      }
    }

    // 最近数据点
    if (!points.value.length) {
      hover.value = null
      return
    }
    const data = instance.convertFromPixel({ gridIndex: 0 }, [px, py])
    const rawIndex = Array.isArray(data) ? Number(data[0]) : 0
    const index = Math.min(Math.max(Math.round(rawIndex), 0), points.value.length - 1)
    const x = pixelOfX(index)
    const y = pixelOfY(Number(points.value[index]?.context?.used_tokens || 0))
    if (x === null || y === null) return
    hover.value = buildPointAdorner(index, x, y)
  })

  instance.getZr().on('globalout', () => {
    hover.value = null
  })

  instance.getZr().on('click', (event) => {
    const px = event.offsetX
    for (const marker of markers.value) {
      const x = pixelOfX(marker.index)
      if (x !== null && Math.abs(px - x) <= 12) {
        selectedId.value = marker.event.id
        renderChart()
        return
      }
    }
  })
}

const adornerStyle = computed(() => {
  const data = hover.value
  if (!data) return {}
  const width = chart.value?.getWidth() ?? 640
  // 左右留边，避免卡片被裁切
  const clampedX = Math.min(Math.max(data.x, 96), Math.max(width - 96, 96))
  return {
    left: `${clampedX}px`,
    top: `${data.y}px`,
    transform: data.flip ? 'translate(-50%, 12px)' : 'translate(-50%, calc(-100% - 12px))',
  }
})

onMounted(() => {
  if (points.value.length) renderChart()
  if (chartEl.value) {
    observer = new ResizeObserver(() => resizeChart())
    observer.observe(chartEl.value)
  }
})

onBeforeUnmount(() => {
  observer?.disconnect()
  observer = null
  chart.value?.dispose()
  chart.value = null
})

watch(
  () => [points.value.length, props.selectedRunId, compactions.value.length],
  () => renderChart(),
)

watch([softWatermark, hardWatermark, windowSize, selectedId], () => renderChart())
</script>

<template>
  <section class="ctx-panel panel">
    <header class="ctx-head">
      <div>
        <strong>上下文时间线</strong>
        <small>占用曲线 · 软硬水位 · 压缩节点 · 锚点保真与成本回执</small>
      </div>
      <div class="ctx-stats">
        <span><small>Token</small><b>{{ fmt(tokens.total) }}</b></span>
        <span><small>输入 / 输出</small><b>{{ fmt(tokens.input) }} / {{ fmt(tokens.output) }}</b></span>
        <span><small>窗口</small><b>{{ fmt(windowSize) }}</b></span>
        <span><small>峰值</small><b>{{ fmt(peak) }}</b></span>
        <span><small>最低</small><b>{{ fmt(lowest) }}</b></span>
        <span><small>压缩</small><b>{{ compactions.length }}</b></span>
        <span><small>累计节省</small><b>{{ fmt(saved) }} tok</b></span>
      </div>
    </header>

    <div v-if="!points.length" class="ctx-empty">
      <el-icon :size="20"><AppIcon name="workflow" :size="20" /></el-icon>
      <p>本次运行还没有上下文快照，发送一条消息后再刷新。</p>
    </div>

    <template v-else>
      <div class="chart-wrap">
        <div class="chart-plot">
          <div ref="chartEl" class="chart-canvas" :style="{ height: `${CHART_HEIGHT}px` }" />

          <!-- Adorner 数据卡片：结构保持不变 -->
          <div v-if="hover" class="adorner" :class="hover.kind" :style="adornerStyle">
            <header>
              <b>{{ hover.title }}</b>
              <em v-if="hover.kind === 'compaction'">点击可查看详情</em>
            </header>
            <div v-for="row in hover.rows" :key="row.key" class="adorner-row">
              <span class="k">{{ row.key }}</span>
              <span class="v">{{ row.value }}</span>
            </div>
          </div>
        </div>
      </div>

      <div class="ctx-body">
        <div class="ctx-list">
          <el-button
            v-for="event in compactions"
            :key="event.id"
            class="ctx-card"
            :class="{ active: event.id === selectedId }"
            text
            @click="selectedId = event.id"
          >
            <span class="ctx-card-title">
              {{ String(event.label || '上下文压缩') }}
              <em :class="String(event.trigger?.type || '')">
                {{ event.evaluation?.timing === 'forced' ? '强制' : event.evaluation?.timing === 'manual' ? '手动' : '软水位' }}
              </em>
            </span>
            <span class="ctx-card-meta">
              {{ fmt(Number(event.trigger?.used_tokens || 0)) }} →
              {{ fmt(Number(event.compaction_result?.after_tokens || 0)) }} tok
            </span>
          </el-button>
          <p v-if="!compactions.length" class="ctx-hint">本次运行未触发上下文压缩</p>
        </div>

        <aside v-if="selected" class="ctx-detail">
          <h4>{{ String(selected.label || '上下文压缩') }}</h4>
          <div class="detail-row">
            <span class="k">触发原因<InfoHint title="触发原因" :text="HINTS.trigger" /></span>
            <span class="v">{{ triggerLabel(selected) }}</span>
          </div>
          <div class="detail-row">
            <span class="k">压缩前占用<InfoHint title="压缩前占用" :text="HINTS.before" /></span>
            <span class="v mono">{{ fmt(Number(selected.trigger?.used_tokens || 0)) }} tok</span>
          </div>
          <div class="detail-row">
            <span class="k">压缩后占用<InfoHint title="压缩后占用" :text="HINTS.after" /></span>
            <span class="v mono">{{ fmt(Number(selected.compaction_result?.after_tokens || 0)) }} tok</span>
          </div>
          <div class="detail-row">
            <span class="k">腾出空间<InfoHint title="腾出空间" :text="HINTS.saved" /></span>
            <span class="v mono">{{ fmt(Number(selected.compaction_result?.saved_tokens || 0)) }} tok</span>
          </div>
          <div class="detail-row">
            <span class="k">关键信息保留<InfoHint title="关键信息保留" :text="HINTS.fidelity" /></span>
            <span class="v mono">
              {{ selected.evaluation?.anchors_kept ?? 0 }} / {{ selected.evaluation?.anchors_total ?? 0 }}
              <em v-if="selected.evaluation?.fidelity !== undefined">
                （{{ (Number(selected.evaluation.fidelity) * 100).toFixed(0) }}%）
              </em>
            </span>
          </div>
          <div class="detail-row">
            <span class="k">整理耗时<InfoHint title="整理耗时" :text="HINTS.duration" /></span>
            <span class="v mono">{{ fmt(Number(selected.duration_ms || 0)) }} ms</span>
          </div>
          <div class="detail-row">
            <span class="k">回查旧内容<InfoHint title="回查旧内容" :text="HINTS.retrieval" /></span>
            <span class="v mono">
              {{ selected.evaluation?.retrieval_cost?.reads ?? 0 }} 次读取 ·
              {{ fmt(Number(selected.evaluation?.retrieval_cost?.tokens || 0)) }} tok
            </span>
          </div>
          <div class="detail-row">
            <span class="k">记录校验<InfoHint title="记录校验" :text="HINTS.integrity" /></span>
            <span class="v mono">
              <em class="verify" :class="integrityState(selected).tone">
                {{ integrityState(selected).label }}
              </em>
              · {{ selected.integrity?.signed ? '已签名' : '未签名' }}
            </span>
          </div>
          <div class="detail-row">
            <span class="k">原文保存位置<InfoHint title="原文保存位置" :text="HINTS.archive" /></span>
            <span class="v mono wrap">{{ selected.compaction_result?.history_file || '未记录' }}</span>
          </div>
          <div class="detail-row block">
            <span class="k">压缩后摘要（节选）<InfoHint title="压缩后摘要" :text="HINTS.summary" /></span>
            <span class="v preview">{{ selected.compaction_result?.summary_preview || '无' }}</span>
          </div>
          <div class="detail-row block">
            <span class="k">系统判断<InfoHint title="系统判断" :text="HINTS.verdict" /></span>
            <span class="v">{{ selected.evaluation?.reason || '未评估' }}</span>
          </div>
        </aside>
      </div>
    </template>
  </section>
</template>

<style scoped>
.ctx-panel {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 16px;
  min-height: 320px;
}

.ctx-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.ctx-head strong {
  display: block;
  font-size: 15px;
}

.ctx-head small {
  color: var(--text-3);
  font-size: 11px;
}

.ctx-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
}

.ctx-stats span {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 1px;
}

.ctx-stats small {
  font-size: 10px;
  color: var(--text-3);
}

.ctx-stats b {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-1);
}

/* 图表容器：Adorner 的定位基准 */
.chart-wrap {
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 6px;
  background: rgba(255, 255, 255, 0.02);
}

.chart-plot {
  position: relative;
  cursor: crosshair;
}

.chart-canvas {
  width: 100%;
}

/* Adorner 卡片 */
.adorner {
  position: absolute;
  z-index: 6;
  min-width: 168px;
  max-width: 240px;
  padding: 8px 10px;
  border-radius: 9px;
  border: 1px solid rgba(124, 140, 255, 0.45);
  background: rgba(14, 20, 36, 0.96);
  box-shadow: 0 12px 28px rgba(0, 0, 0, 0.45);
  pointer-events: none;
  backdrop-filter: blur(4px);
}

.adorner.compaction { border-color: rgba(176, 124, 255, 0.6); }
.adorner.soft { border-color: rgba(126, 224, 210, 0.55); }
.adorner.hard { border-color: rgba(242, 176, 60, 0.55); }
.adorner.limit { border-color: rgba(226, 96, 94, 0.55); }

.adorner header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  padding-bottom: 5px;
  margin-bottom: 4px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.09);
}

.adorner header b {
  font-size: 11.5px;
  color: var(--text-1);
}

.adorner header em {
  font-style: normal;
  font-size: 9.5px;
  color: var(--text-3);
}

.adorner-row {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  font-size: 10.5px;
  line-height: 1.7;
}

.adorner-row .k { color: var(--text-3); }

.adorner-row .v {
  color: var(--text-1);
  font-family: var(--font-mono);
}

/* 压缩事件列表 */
.ctx-body {
  display: grid;
  grid-template-columns: minmax(200px, 0.72fr) minmax(300px, 1.28fr);
  gap: 12px;
}

.ctx-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.ctx-card {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 3px;
  padding: 9px 11px;
  height: auto;
  border-radius: 9px;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.02);
  color: var(--text-2);
  text-align: left;
}

.ctx-card:hover {
  border-color: var(--line-strong);
  background: rgba(255, 255, 255, 0.03);
}

.ctx-card.active {
  border-color: rgba(124, 140, 255, 0.55);
  background: rgba(92, 111, 255, 0.12);
}

.ctx-card-title {
  font-size: 12px;
  font-weight: 600;
  display: flex;
  gap: 8px;
  align-items: center;
  color: var(--text-1);
}

.ctx-card-title em {
  font-style: normal;
  font-size: 10px;
  color: #b8c4ff;
}

.ctx-card-meta {
  font-size: 10.5px;
  color: var(--text-3);
  font-family: var(--font-mono);
}

.ctx-hint {
  color: var(--text-3);
  font-size: 11px;
}

.ctx-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 36px 0;
  color: var(--text-3);
  font-size: 11.5px;
}

/* 压缩详情卡片：标题更大、逐行分隔线、行首加粗 */
.ctx-detail {
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 4px 14px 8px;
  background: rgba(255, 255, 255, 0.02);
}

.ctx-detail h4 {
  margin: 10px 0 6px;
  font-size: 14.5px;
  font-weight: 700;
  letter-spacing: 0.2px;
}

.detail-row {
  display: grid;
  grid-template-columns: 82px 1fr;
  gap: 10px;
  align-items: start;
  padding: 9px 0;
  border-bottom: 1px solid rgba(255, 255, 255, 0.14);
  font-size: 11.5px;
}

.detail-row:last-child {
  border-bottom: none;
}

.detail-row.block {
  grid-template-columns: 1fr;
}

.detail-row .k {
  color: var(--text-3);
  font-weight: 700;
}

.detail-row .v {
  color: var(--text-2);
  word-break: break-word;
}

.detail-row .v.mono {
  font-family: var(--font-mono);
  font-size: 11px;
}

.detail-row .v em {
  font-style: normal;
  color: var(--text-3);
}

.detail-row .v em.verify.ok {
  color: #7ee0d2;
}

.detail-row .v em.verify.bad {
  color: #f2a09f;
}

.detail-row .v em.verify.unknown {
  color: var(--text-3);
}

/* 行首说明图标与文字保持基线对齐 */
.detail-row .k :deep(.info-hint) {
  margin-left: 4px;
  vertical-align: middle;
}

.detail-row .v.preview {
  max-height: 132px;
  overflow: auto;
  white-space: pre-wrap;
  line-height: 1.55;
  padding: 6px 8px;
  border-radius: 7px;
  background: rgba(0, 0, 0, 0.22);
}

@media (max-width: 960px) {
  .ctx-body {
    grid-template-columns: 1fr;
  }
  .ctx-head {
    flex-direction: column;
  }
}
</style>
