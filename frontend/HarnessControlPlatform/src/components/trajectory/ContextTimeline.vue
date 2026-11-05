<script setup lang="ts">
// 轨迹页「上下文」视图：占用曲线 + 软水位/窗口上限参考线 + 压缩节点 + 交互 Adorner。
import { computed, ref, watch } from 'vue'
import type { TrajectoryEvent, TrajectoryRun } from '@/api/trajectory'
import AppIcon from '@/components/console/AppIcon.vue'
import InfoHint from '@/components/common/InfoHint.vue'

const props = defineProps<{
  runs: TrajectoryRun[]
  selectedRunId: string
}>()

const WIDTH = 640
const HEIGHT = 210
const PAD_X = 46
const PAD_Y = 28

const events = computed<TrajectoryEvent[]>(() => {
  const scoped = props.selectedRunId
    ? props.runs.filter((run) => run.run_id === props.selectedRunId)
    : props.runs
  return scoped
    .flatMap((run) => run.events)
    .sort((a, b) => String(a.start).localeCompare(String(b.start)))
})

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

function xAt(index: number): number {
  const total = Math.max(points.value.length - 1, 1)
  return PAD_X + (WIDTH - PAD_X * 2) * (index / total)
}
function yAt(value: number): number {
  return HEIGHT - PAD_Y - (HEIGHT - PAD_Y * 2) * (value / maxY.value)
}
const pctX = (coordinate: number) => `${(coordinate / WIDTH) * 100}%`
const pctY = (coordinate: number) => `${(coordinate / HEIGHT) * 100}%`

const linePath = computed(() =>
  points.value
    .map(
      (event, index) =>
        `${index === 0 ? 'M' : 'L'}${xAt(index).toFixed(1)},${yAt(
          Number(event.context?.used_tokens || 0),
        ).toFixed(1)}`,
    )
    .join(' '),
)

const areaPath = computed(() => {
  if (!points.value.length) return ''
  const first = xAt(0)
  const last = xAt(points.value.length - 1)
  const base = HEIGHT - PAD_Y
  return `${linePath.value} L${last},${base} L${first},${base} Z`
})

// 每个压缩点：竖线 + 压缩前/后两个圆点
const markers = computed(() =>
  compactions.value.map((event) => {
    let index = points.value.findIndex((item) => item.start >= event.start)
    if (index < 0) index = Math.max(points.value.length - 1, 0)
    const point = points.value[index]
    return {
      event,
      x: xAt(Math.max(index, 0)),
      before: Number(event.trigger?.used_tokens || 0),
      after: Number(event.compaction_result?.after_tokens || point?.context?.used_tokens || 0),
    }
  }),
)

function nearestIndex(ratio: number): number {
  const total = Math.max(points.value.length - 1, 1)
  return Math.min(Math.max(Math.round(ratio * total), 0), total)
}

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

function buildPointAdorner(index: number): AdornerData {
  const event = points.value[index]
  const used = Number(event?.context?.used_tokens || 0)
  const pct = windowSize.value ? (used / windowSize.value) * 100 : 0
  const x = xAt(index)
  const y = yAt(used)
  const gap = softWatermark.value - used
  return {
    kind: 'point',
    title: `模型调用 #${index + 1}`,
    rows: [
      { key: '时间', value: timeLabel(event?.start) },
      { key: '已用上下文', value: `${fmt(used)} tok` },
      { key: '占窗口', value: `${pct.toFixed(1)}%` },
      {
        key: gap >= 0 ? '距软水位' : '超出软水位',
        value: `${fmt(Math.abs(gap))} tok`,
      },
      { key: '本步', value: String(event?.label || '') },
    ],
    x,
    y,
    flip: y < HEIGHT * 0.45,
  }
}

function buildCompactionAdorner(marker: (typeof markers.value)[number]): AdornerData {
  const { event, x, before, after } = marker
  return {
    kind: 'compaction',
    title: '上下文压缩',
    rows: [
      { key: '触发', value: String(event.trigger?.type || 'unknown') },
      { key: '压缩前', value: `${fmt(before)} tok` },
      { key: '压缩后', value: `${fmt(after)} tok` },
      { key: '节省', value: `${fmt(Math.max(before - after, 0))} tok` },
      { key: '锚点保真', value: `${event.evaluation?.anchors_kept ?? 0}/${event.evaluation?.anchors_total ?? 0}` },
      { key: '耗时', value: `${fmt(Number(event.duration_ms || 0))} ms` },
    ],
    x,
    y: Math.min(yAt(before), yAt(after)),
    flip: Math.min(yAt(before), yAt(after)) < HEIGHT * 0.5,
  }
}

function buildRefAdorner(kind: 'soft' | 'hard' | 'limit'): AdornerData {
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
  const y = yAt(value)
  return {
    kind,
    title,
    rows: [
      { key: '阈值', value: `${fmt(value)} tok` },
      {
        key: '占窗口',
        value: `${((value / (windowSize.value || 1)) * 100).toFixed(1)}%`,
      },
      { key: '含义', value: desc },
    ],
    x: WIDTH - PAD_X - 40,
    y,
    flip: y < HEIGHT * 0.5,
  }
}

const svgRef = ref<SVGSVGElement | null>(null)

/**
 * 屏幕坐标 → SVG 用户坐标（viewBox 坐标）。
 * 用 getScreenCTM 反变换，避免按外层卡片尺寸换算带来的偏移误差。
 */
function clientToViewBox(clientX: number, clientY: number): { x: number; y: number } | null {
  const svg = svgRef.value
  const ctm = svg?.getScreenCTM()
  if (!svg || !ctm) return null
  const point = svg.createSVGPoint()
  point.x = clientX
  point.y = clientY
  const local = point.matrixTransform(ctm.inverse())
  return { x: local.x, y: local.y }
}

function onChartMove(event: MouseEvent) {
  const local = clientToViewBox(event.clientX, event.clientY)
  if (!local) return
  const plotX = Math.min(Math.max(local.x, 0), WIDTH)
  const plotY = Math.min(Math.max(local.y, 0), HEIGHT)
  const ratioX = plotX / WIDTH

  // 参考线优先：鼠标靠近软/硬/上限线时给出阈值说明。
  const refs: Array<['soft' | 'hard' | 'limit', number]> = [
    ['limit', yAt(windowSize.value)],
    ['hard', yAt(hardWatermark.value)],
    ['soft', yAt(softWatermark.value)],
  ]
  const hitRef = refs.find(([, y]) => Math.abs(plotY - y) <= 9)
  if (hitRef) {
    hover.value = buildRefAdorner(hitRef[0])
    return
  }

  // 压缩节点：横向距离 10px 内优先展示压缩详情。
  const hitMarker = markers.value.find((marker) => Math.abs(plotX - marker.x) <= 10)
  if (hitMarker) {
    hover.value = buildCompactionAdorner(hitMarker)
    return
  }

  if (!points.value.length) {
    hover.value = null
    return
  }
  hover.value = buildPointAdorner(nearestIndex(ratioX))
}

function onChartLeave() {
  hover.value = null
}

function onChartClick(event: MouseEvent) {
  const local = clientToViewBox(event.clientX, event.clientY)
  if (!local) return
  const marker = markers.value.find((item) => Math.abs(local.x - item.x) <= 12)
  if (marker) selectedId.value = marker.event.id
}

const adornerStyle = computed(() => {
  const data = hover.value
  if (!data) return {}
  const leftPct = (data.x / WIDTH) * 100
  const topPct = (data.y / HEIGHT) * 100
  const clampedLeft = Math.min(Math.max(leftPct, 16), 84)
  return {
    left: `${clampedLeft}%`,
    top: `${topPct}%`,
    transform: data.flip ? 'translate(-50%, 14px)' : 'translate(-50%, calc(-100% - 14px))',
  }
})
</script>

<template>
  <section class="ctx-panel panel">
    <header class="ctx-head">
      <div>
        <strong>上下文时间线</strong>
        <small>占用曲线 · 软硬水位 · 压缩节点 · 锚点保真与成本回执</small>
      </div>
      <div class="ctx-stats">
        <span><small>窗口</small><b>{{ fmt(windowSize) }}</b></span>
        <span><small>峰值</small><b>{{ fmt(peak) }}</b></span>
        <span><small>最低</small><b>{{ fmt(lowest) }}</b></span>
        <span><small>压缩</small><b>{{ compactions.length }}</b></span>
        <span><small>累计节省</small><b>{{ fmt(saved) }} tok</b></span>
      </div>
    </header>

    <div v-if="!points.length" class="ctx-empty">
      <AppIcon name="workflow" :size="20" />
      <p>本次运行还没有上下文快照，发送一条消息后再刷新。</p>
    </div>

    <template v-else>
      <div class="chart-wrap">
        <div
          class="chart-plot"
          @mousemove="onChartMove"
          @mouseleave="onChartLeave"
          @click="onChartClick"
        >
          <svg
            ref="svgRef"
            :viewBox="`0 0 ${WIDTH} ${HEIGHT}`"
            class="chart"
            role="img"
            aria-label="上下文占用曲线"
          >
          <defs>
            <linearGradient id="ctx-area" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="rgba(91,140,255,0.34)" />
              <stop offset="100%" stop-color="rgba(91,140,255,0)" />
            </linearGradient>
          </defs>

          <!-- 参考线：窗口上限 / 硬水位 / 软水位 -->
          <line
            :x1="PAD_X"
            :x2="WIDTH - PAD_X"
            :y1="yAt(windowSize)"
            :y2="yAt(windowSize)"
            class="ref-line limit"
          />
          <line
            :x1="PAD_X"
            :x2="WIDTH - PAD_X"
            :y1="yAt(hardWatermark)"
            :y2="yAt(hardWatermark)"
            class="ref-line hard"
          />
          <line
            :x1="PAD_X"
            :x2="WIDTH - PAD_X"
            :y1="yAt(softWatermark)"
            :y2="yAt(softWatermark)"
            class="ref-line soft"
          />

          <path :d="areaPath" fill="url(#ctx-area)" />
          <path :d="linePath" class="usage-line" />

          <!-- 压缩节点：竖虚线 + 压缩前后圆点 -->
          <g v-for="marker in markers" :key="marker.event.id">
            <line
              :x1="marker.x"
              :x2="marker.x"
              :y1="PAD_Y"
              :y2="HEIGHT - PAD_Y"
              class="mark-line"
            />
            <circle
              :cx="marker.x"
              :cy="yAt(marker.before)"
              r="4"
              class="mark-dot before"
              :class="{ active: marker.event.id === selectedId }"
            />
            <circle
              :cx="marker.x"
              :cy="yAt(marker.after)"
              r="4"
              class="mark-dot after"
              :class="{ active: marker.event.id === selectedId }"
            />
          </g>

          <!-- 悬停定位虚线 -->
          <line
            v-if="hover"
            :x1="hover.x"
            :x2="hover.x"
            :y1="PAD_Y"
            :y2="HEIGHT - PAD_Y"
            class="hover-line"
          />
          <circle
            v-if="hover && hover.kind === 'point'"
            :cx="hover.x"
            :cy="hover.y"
            r="4.5"
            class="hover-dot"
          />
        </svg>

        <!-- 图表内联阈值标签（替代底部图例） -->
          <span class="pin limit" :style="{ top: pctY(yAt(windowSize)) }">
            窗口上限 {{ fmt(windowSize) }}
          </span>
          <span class="pin hard" :style="{ top: pctY(yAt(hardWatermark)) }">
            硬水位 {{ fmt(hardWatermark) }}
          </span>
          <span class="pin soft" :style="{ top: pctY(yAt(softWatermark)) }">
            软水位 {{ fmt(softWatermark) }}
          </span>

          <!-- Adorner 数据卡片 -->
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
          <button
            v-for="event in compactions"
            :key="event.id"
            type="button"
            class="ctx-card"
            :class="{ active: event.id === selectedId }"
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
          </button>
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

/* 与 SVG viewBox 完全对齐的绘图区，Adorner 用百分比定位 */
.chart-plot {
  position: relative;
  cursor: crosshair;
}

.chart {
  width: 100%;
  height: auto;
  display: block;
}

.ref-line {
  stroke-dasharray: 5 5;
  stroke-width: 1;
}

.ref-line.limit { stroke: rgba(226, 96, 94, 0.5); }
.ref-line.hard { stroke: rgba(242, 176, 60, 0.45); }
.ref-line.soft { stroke: rgba(126, 224, 210, 0.45); }

.usage-line {
  fill: none;
  stroke: #5b8cff;
  stroke-width: 1.8;
  stroke-linejoin: round;
}

.mark-line {
  stroke: rgba(176, 124, 255, 0.45);
  stroke-dasharray: 3 3;
}

.mark-dot {
  fill: #b07cff;
  stroke: rgba(10, 14, 26, 0.9);
  stroke-width: 1;
}

.mark-dot.after { fill: #37d0c4; }

.mark-dot.active {
  stroke: #ffffff;
  stroke-width: 1.6;
}

.hover-line {
  stroke: rgba(255, 255, 255, 0.35);
  stroke-dasharray: 4 3;
}

.hover-dot {
  fill: none;
  stroke: #ffffff;
  stroke-width: 1.6;
}

/* 图表内联阈值标签 */
.pin {
  position: absolute;
  right: 10px;
  transform: translateY(-50%);
  padding: 1px 7px;
  border-radius: 999px;
  font-size: 10px;
  font-family: var(--font-mono);
  line-height: 16px;
  pointer-events: none;
  background: rgba(12, 18, 32, 0.86);
  border: 1px solid transparent;
}

.pin.limit { color: #f2a09f; border-color: rgba(226, 96, 94, 0.45); }
.pin.hard { color: #f2c777; border-color: rgba(242, 176, 60, 0.45); }
.pin.soft { color: #7ee0d2; border-color: rgba(126, 224, 210, 0.4); }

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
  border-radius: 9px;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.02);
  color: var(--text-2);
  cursor: pointer;
  text-align: left;
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
  gap: 5px;
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

.ctx-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 220px;
  color: var(--text-3);
  font-size: 12px;
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
