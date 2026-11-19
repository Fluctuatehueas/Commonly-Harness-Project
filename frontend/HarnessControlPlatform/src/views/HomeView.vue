<script setup lang="ts">
// 工作台：轨道式模块导航 + 内陷卡片 + 全息列表 + 本机硬件信息
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Close, View } from '@element-plus/icons-vue'
import TopNav from '@/components/console/TopNav.vue'
import brandIcon from '@/assets/icon.png'
import { ICONS } from '@/components/console/icons'
import { NAV_ITEMS } from '@/mock/dashboard'
import { API_BASE } from '@/api/client'
import { fetchReadiness, type ReadinessReport } from '@/api/settings'
import ReadinessDialog from '@/components/common/ReadinessDialog.vue'
import { useSessionStore } from '@/stores/sessions'
import { useWorkspaceStore } from '@/stores/workspace'

// 自检每个页面生命周期只跑一次；刷新页面后重新检测。
let readinessChecked = false

function introEase(value: number): number {
  const v = Math.min(1, Math.max(0, value))
  return v < 0.5 ? 4 * v * v * v : 1 - Math.pow(-2 * v + 2, 3) / 2
}

interface OrbitBubble {
  key: string
  label: string
  path: string
  icon: keyof typeof ICONS
  homeX: number
  homeY: number
  x: number
  y: number
  vx: number
  vy: number
  w: number
  h: number
  phase: number
  bobSpeed: number
}

const router = useRouter()
const sessions = useSessionStore()
const workspace = useWorkspaceStore()

const canvas = ref<HTMLCanvasElement | null>(null)
const stage = ref<HTMLElement | null>(null)
const online = ref<boolean | null>(null)
const readiness = ref<ReadinessReport | null>(null)
const readinessOpen = ref(false)
const readinessLoading = ref(false)
const holoMode = ref<'sessions' | 'projects' | null>(null)
const revealed = ref(false)
const showHardware = ref(true)
const clock = ref('')
const cpuLabel = ref('检测中…')
const memoryLabel = ref('—')
const gpuLabel = ref('检测中…')

let clockTimer: number | undefined
let raf = 0
let resizeHandler: (() => void) | null = null
let moveHandler: ((event: PointerEvent) => void) | null = null
let downHandler: ((event: PointerEvent) => void) | null = null
let leaveHandler: (() => void) | null = null
let bubbles: OrbitBubble[] = []
let hoveredIndex = -1
let brandImage: HTMLImageElement | null = null
const pointer = { x: -9999, y: -9999, active: false }

const modules = computed(() => NAV_ITEMS.filter((item) => item.key !== 'home'))
const moduleIntro = computed(
  () => new Map(modules.value.map((item) => [item.key, item.description])),
)

// ---------- 悬停全息屏 + 打字机 ----------
interface HoverInfo {
  key: string
  label: string
  description: string
  x: number
  y: number
}

const hoverInfo = ref<HoverInfo | null>(null)
const typedText = ref('')
const typingDone = ref(false)
const warping = ref(false)
let typeTimer: number | undefined
let warpTimer: number | undefined

function stopTyping() {
  if (typeTimer) window.clearInterval(typeTimer)
  typeTimer = undefined
}

function startTyping(info: HoverInfo) {
  stopTyping()
  typedText.value = ''
  typingDone.value = false
  const text = info.description
  let index = 0
  typeTimer = window.setInterval(() => {
    index = Math.min(text.length, index + 2)
    typedText.value = text.slice(0, index)
    if (index >= text.length) {
      typingDone.value = true
      stopTyping()
    }
  }, 24)
}

watch(
  () => hoverInfo.value?.key,
  (key) => {
    if (!key || !hoverInfo.value) {
      stopTyping()
      typedText.value = ''
      typingDone.value = false
      return
    }
    startTyping(hoverInfo.value)
  },
)

function triggerWarp() {
  if (warping.value) return
  warping.value = true
  warpTimer = window.setTimeout(() => {
    void router.push('/chat')
  }, 720)
}
const visibleProjects = computed(() =>
  workspace.sorted.filter((project) => !project.is_default),
)
const recentSessions = computed(() => sessions.sessions.slice(0, 24))
const projectList = computed(() => visibleProjects.value.slice(0, 24))

const statusLabel = computed(() =>
  online.value === null ? '检测后端…' : online.value ? '后端在线' : '后端离线',
)
// 状态标签交给 el-tag，按后端连通性映射 EP 的语义色
const statusTagType = computed<'success' | 'warning' | 'danger'>(() =>
  online.value === null ? 'warning' : online.value ? 'success' : 'danger',
)

const holoTitle = computed(() =>
  holoMode.value === 'sessions' ? '最近对话' : '最近项目',
)

// ---------- 启动自检 ----------
async function probeReadiness() {
  readinessLoading.value = true
  try {
    const res = await fetch(`${API_BASE}/health`, { signal: AbortSignal.timeout(4000) })
    online.value = res.ok
    if (!res.ok) {
      readiness.value = null
      readinessOpen.value = true
      return
    }
    try {
      readiness.value = await fetchReadiness()
    } catch {
      // 旧版后端没有 readiness 接口时无法判断，直接关闭弹窗避免误报
      readiness.value = null
      readinessOpen.value = false
      return
    }
    readinessOpen.value = !(readiness.value.llm.configured && readiness.value.sandbox.configured)
  } catch {
    online.value = false
    readiness.value = null
    readinessOpen.value = true
  } finally {
    readinessLoading.value = false
  }
}

async function refreshOnline() {
  try {
    const res = await fetch(`${API_BASE}/health`, { signal: AbortSignal.timeout(4000) })
    online.value = res.ok
  } catch {
    online.value = false
  }
}

function openSettings(focus: 'model' | 'sandbox') {
  readinessOpen.value = false
  void router.push({ path: '/settings', query: { focus } })
}

const latestSessionTitle = computed(
  () => recentSessions.value[0]?.title || '还没有对话',
)
const latestProjectName = computed(
  () => projectList.value[0]?.name || '还没有项目',
)

function flash(message: string) {
  // 提示统一走 Element Plus 全局消息，不再维护自定义悬浮条与定时器
  ElMessage.info(message)
}

function fmtTime(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  const now = new Date()
  if (date.toDateString() === now.toDateString()) {
    return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
  }
  return `${date.getMonth() + 1}/${date.getDate()}`
}

function toggleHolo(mode: 'sessions' | 'projects') {
  holoMode.value = holoMode.value === mode ? null : mode
}

function openSession(threadId: string) {
  sessions.activeThreadId = threadId
  holoMode.value = null
  void router.push('/chat')
}

function openProject(projectId: string) {
  const project = workspace.projects.find((item) => item.id === projectId)
  if (!project) return
  holoMode.value = null
  flash(`项目：${project.path}`)
  void router.push('/chat')
}

// ---------- 硬件信息（全部前端获取） ----------
function detectGpu(): string {
  try {
    const gl = document.createElement('canvas').getContext('webgl')
    if (!gl) return 'WebGL 不可用'
    const ext = gl.getExtension('WEBGL_debug_renderer_info') as
      | { UNMASKED_RENDERER_WEBGL: number }
      | null
    if (!ext) return 'GPU 信息受限'
    const renderer = String(gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) || '')
    if (!renderer) return 'GPU 未知'
    return renderer.length > 56 ? `${renderer.slice(0, 54)}…` : renderer
  } catch {
    return 'GPU 不可见'
  }
}

function detectHardware() {
  const nav = navigator as Navigator & {
    deviceMemory?: number
    userAgentData?: { platform?: string }
  }
  const platform = nav.userAgentData?.platform || navigator.platform || '本机'
  const cores = navigator.hardwareConcurrency
  cpuLabel.value = cores ? `${platform} · ${cores} 线程` : platform
  memoryLabel.value = nav.deviceMemory ? `约 ${nav.deviceMemory} GB` : '浏览器不可见'
  gpuLabel.value = detectGpu()
}

function tickClock() {
  const date = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  clock.value = `${p(date.getHours())}:${p(date.getMinutes())}:${p(date.getSeconds())}`
}

// ---------- Canvas 轨道物理 ----------
function roundRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
) {
  const radius = Math.min(r, w / 2, h / 2)
  ctx.beginPath()
  ctx.moveTo(x + radius, y)
  ctx.arcTo(x + w, y, x + w, y + h, radius)
  ctx.arcTo(x + w, y + h, x, y + h, radius)
  ctx.arcTo(x, y + h, x, y, radius)
  ctx.arcTo(x, y, x + w, y, radius)
  ctx.closePath()
}

function setupBubbles(width: number, height: number) {
  const cx = width / 2
  const cy = height / 2
  const scale = Math.min(1, Math.max(0.72, width / 1280))
  const rx = Math.min(width * 0.33, 420 * scale)
  const ry = Math.min(height * 0.31, 250 * scale)
  bubbles = modules.value.map((item, index) => {
    const angle = (-Math.PI / 2) + (index / modules.value.length) * Math.PI * 2
    const homeX = cx + Math.cos(angle) * rx
    const homeY = cy + Math.sin(angle) * ry
    return {
      key: item.key,
      label: item.label,
      path: item.path,
      icon: item.icon,
      homeX,
      homeY,
      x: homeX,
      y: homeY,
      vx: 0,
      vy: 0,
      w: 156 * scale,
      h: 54 * scale,
      phase: Math.random() * Math.PI * 2,
      bobSpeed: 0.55 + Math.random() * 0.45,
    }
  })
}

function hitTest(x: number, y: number, padding = 0): number {
  for (let i = 0; i < bubbles.length; i += 1) {
    const b = bubbles[i]
    if (!b) continue
    if (
      x >= b.x - b.w / 2 - padding &&
      x <= b.x + b.w / 2 + padding &&
      y >= b.y - b.h / 2 - padding &&
      y <= b.y + b.h / 2 + padding
    ) {
      return i
    }
  }
  return -1
}

function mountOrbit() {
  const el = canvas.value
  const host = stage.value
  if (!el || !host) return
  const ctx = el.getContext('2d')
  if (!ctx) return

  const dpr = Math.min(window.devicePixelRatio || 1, 2)
  let width = 0
  let height = 0
  let last = 0
  const introStart = performance.now()

  function resize() {
    if (!el || !host) return
    width = host.clientWidth
    height = host.clientHeight
    el.width = Math.floor(width * dpr)
    el.height = Math.floor(height * dpr)
    ctx!.setTransform(dpr, 0, 0, dpr, 0, 0)
    setupBubbles(width, height)
  }

  function updateBubble(b: OrbitBubble, index: number, t: number, dt: number) {
    const hovered = index === hoveredIndex
    const targetX = b.homeX + Math.sin(t * b.bobSpeed + b.phase) * 7
    const targetY = b.homeY + Math.cos(t * b.bobSpeed * 0.82 + b.phase) * 5

    if (hovered) {
      // hover 时停止弹簧，速度快速衰减，保留“刹停”的惯性
      b.vx *= 0.82
      b.vy *= 0.82
      b.x += b.vx * dt
      b.y += b.vy * dt
      return
    }

    let ax = (targetX - b.x) * 0.026
    let ay = (targetY - b.y) * 0.026

    if (pointer.active) {
      const dx = b.x - pointer.x
      const dy = b.y - pointer.y
      const dist2 = dx * dx + dy * dy
      const influence = 150
      if (dist2 < influence * influence && dist2 > 1) {
        // 平方反比但整体减弱：只给一点“排斥”，不能影响点击。
        const force = 4200 / (dist2 + 5000)
        const dist = Math.sqrt(dist2)
        ax += (dx / dist) * force
        ay += (dy / dist) * force
      }
    }

    b.vx = (b.vx + ax) * 0.92
    b.vy = (b.vy + ay) * 0.92
    b.x += b.vx * dt
    b.y += b.vy * dt

    const margin = 70
    if (b.x < margin) {
      b.x = margin
      b.vx = Math.abs(b.vx) * 0.4
    } else if (b.x > width - margin) {
      b.x = width - margin
      b.vx = -Math.abs(b.vx) * 0.4
    }
    if (b.y < margin) {
      b.y = margin
      b.vy = Math.abs(b.vy) * 0.4
    } else if (b.y > height - margin) {
      b.y = height - margin
      b.vy = -Math.abs(b.vy) * 0.4
    }
  }

  function drawCenter(now: number, t: number) {
    const intro = Math.min(1, (now - introStart) / 900)
    const eased = introEase(intro)
    const centerX = width / 2
    const centerY = height / 2
    const startX = 28
    const startY = 10
    const cx = startX + (centerX - startX) * eased
    const cy = startY + (centerY - startY) * eased
    const finalSize = Math.min(116, Math.max(92, Math.min(width, height) * 0.12))
    const size = 34 + (finalSize - 34) * eased
    const glowAlpha = Math.max(0, (intro - 0.35) / 0.65)

    if (glowAlpha > 0) {
      const glow = ctx!.createRadialGradient(cx, cy, 8, cx, cy, size * 2.4)
      glow.addColorStop(0, `rgba(55, 208, 196, ${0.20 * glowAlpha})`)
      glow.addColorStop(0.45, `rgba(91, 140, 255, ${0.10 * glowAlpha})`)
      glow.addColorStop(1, 'rgba(7, 11, 20, 0)')
      ctx!.beginPath()
      ctx!.fillStyle = glow
      ctx!.arc(cx, cy, size * 2.4, 0, Math.PI * 2)
      ctx!.fill()
    }

    if (glowAlpha > 0) {
      ctx!.save()
      ctx!.translate(cx, cy)
      ctx!.globalAlpha = glowAlpha
      ctx!.rotate(t * 0.12)
      ctx!.setLineDash([16, 12])
      ctx!.lineWidth = 1
      ctx!.strokeStyle = 'rgba(91, 140, 255, 0.42)'
      ctx!.beginPath()
      ctx!.arc(0, 0, size * 0.92, 0, Math.PI * 2)
      ctx!.stroke()
      ctx!.setLineDash([])
      ctx!.rotate(-t * 0.34)
      ctx!.strokeStyle = 'rgba(55, 208, 196, 0.28)'
      ctx!.beginPath()
      ctx!.arc(0, 0, size * 0.74, 0, Math.PI * 1.2)
      ctx!.stroke()
      ctx!.restore()
    }

    // 图标本身不加圆角边框，只画图形与光晕
    if (brandImage?.complete && brandImage.naturalWidth > 0) {
      ctx!.drawImage(brandImage, cx - size / 2, cy - size / 2, size, size)
    } else {
      ctx!.textAlign = 'center'
      ctx!.textBaseline = 'middle'
      ctx!.fillStyle = 'rgba(160, 240, 255, 0.92)'
      ctx!.font = `600 ${Math.round(size * 0.6)}px "Cascadia Code", Consolas, monospace`
      ctx!.fillText('H', cx, cy + 2)
    }

    if (glowAlpha > 0) {
      ctx!.textAlign = 'center'
      ctx!.fillStyle = `rgba(230, 237, 249, ${0.92 * glowAlpha})`
      ctx!.font = '600 12px "Inter", "Microsoft YaHei", sans-serif'
      ctx!.fillText('HARNESS', cx, cy + size * 0.72 + 18)
      ctx!.fillStyle = `rgba(147, 165, 196, ${0.75 * glowAlpha})`
      ctx!.font = '10px "Cascadia Code", Consolas, monospace'
      ctx!.fillText('ORBITAL CONSOLE', cx, cy + size * 0.72 + 34)
    }
  }

  function drawBubbles(now: number) {
    if (!revealed.value) return
    const reveal = Math.min(1, (now - introStart - 900) / 340)
    const eased = introEase(Math.max(0, reveal))
    bubbles.forEach((b, index) => {
      const hovered = index === hoveredIndex
      const scale = hovered ? 1.04 : 1
      const slide = (1 - eased) * (b.homeX < width / 2 ? -90 : 90)
      ctx!.save()
      ctx!.globalAlpha = eased
      ctx!.translate(b.x + slide, b.y)
      ctx!.scale(scale, scale)

      roundRect(ctx!, -b.w / 2, -b.h / 2, b.w, b.h, b.h * 0.32)
      const fill = ctx!.createLinearGradient(-b.w / 2, 0, b.w / 2, 0)
      fill.addColorStop(0, hovered ? 'rgba(55, 208, 196, 0.16)' : 'rgba(10, 18, 34, 0.56)')
      fill.addColorStop(1, hovered ? 'rgba(91, 140, 255, 0.12)' : 'rgba(8, 14, 27, 0.44)')
      ctx!.fillStyle = fill
      ctx!.fill()
      ctx!.lineWidth = 1
      ctx!.strokeStyle = hovered
        ? 'rgba(55, 208, 196, 0.72)'
        : 'rgba(126, 154, 198, 0.26)'
      ctx!.stroke()

      if (hovered) {
        ctx!.shadowColor = 'rgba(55, 208, 196, 0.55)'
        ctx!.shadowBlur = 18
        ctx!.stroke()
        ctx!.shadowBlur = 0
      }

      // 图标
      const iconScale = b.h / 44
      ctx!.save()
      ctx!.translate(-b.w / 2 + 20 * iconScale, 0)
      ctx!.scale(iconScale, iconScale)
      ctx!.translate(-12, -12)
      ctx!.strokeStyle = hovered ? 'rgba(160, 240, 255, 0.95)' : 'rgba(91, 140, 255, 0.8)'
      ctx!.lineWidth = 1.35
      ctx!.lineCap = 'round'
      ctx!.lineJoin = 'round'
      for (const path of ICONS[b.icon]) {
        ctx!.stroke(new Path2D(path))
      }
      ctx!.restore()

      ctx!.textAlign = 'left'
      ctx!.textBaseline = 'middle'
      ctx!.fillStyle = hovered ? 'rgba(235, 250, 255, 1)' : 'rgba(214, 226, 245, 0.92)'
      ctx!.font = `500 ${Math.round(12.5 * iconScale)}px "Inter", "Microsoft YaHei", sans-serif`
      ctx!.fillText(b.label, -b.w / 2 + 38 * iconScale, 1)

      // hover 指示
      ctx!.beginPath()
      ctx!.arc(b.w / 2 - 12 * iconScale, 0, 2.2 * iconScale, 0, Math.PI * 2)
      ctx!.fillStyle = hovered ? 'rgba(55, 208, 196, 0.95)' : 'rgba(126, 154, 198, 0.35)'
      ctx!.fill()
      ctx!.restore()
    })
  }

  function frame(now: number) {
    if (!last) last = now
    const dt = Math.min(2.2, (now - last) / 16.67)
    last = now
    const t = now / 1000

    if (!revealed.value && now - introStart >= 900) {
      revealed.value = true
    }
    ctx!.clearRect(0, 0, width, height)
    drawCenter(now, t)
    bubbles.forEach((b, index) => updateBubble(b, index, t, dt))
    drawBubbles(now)
    raf = requestAnimationFrame(frame)
  }

  function centerHit(x: number, y: number): boolean {
    const size = Math.min(116, Math.max(92, Math.min(width, height) * 0.12))
    const dx = x - width / 2
    const dy = y - height / 2
    return dx * dx + dy * dy <= (size * 0.72) ** 2
  }

  resizeHandler = resize
  moveHandler = (event) => {
    const rect = el.getBoundingClientRect()
    pointer.x = event.clientX - rect.left
    pointer.y = event.clientY - rect.top
    pointer.active = true
    const index = hitTest(pointer.x, pointer.y, 16)
    if (index >= 0) {
      const b = bubbles[index]
      if (!b) return
      if (hoveredIndex !== index) {
        // 进入气泡瞬间保留当前速度，由阻尼产生刹停惯性
        b.vx *= 0.6
        b.vy *= 0.6
        hoverInfo.value = {
          key: b.key,
          label: b.label,
          description: moduleIntro.value.get(b.key) || '',
          // 全息屏锚定在气泡上方，并夹在舞台范围内，避免贴边溢出。
          x: Math.min(Math.max(b.x, 170), Math.max(width - 170, 170)),
          y: Math.max(b.y - b.h / 2 - 16, 150),
        }
      }
      hoveredIndex = index
      el.style.cursor = 'pointer'
      return
    }

    hoveredIndex = -1
    hoverInfo.value = null
    el.style.cursor = centerHit(pointer.x, pointer.y) ? 'pointer' : 'crosshair'
  }
  downHandler = (event) => {
    if (event.button !== 0 || warping.value) return
    const rect = el.getBoundingClientRect()
    const x = event.clientX - rect.left
    const y = event.clientY - rect.top
    if (centerHit(x, y)) {
      triggerWarp()
      return
    }
    const index = hitTest(x, y, 20)
    const b = index >= 0 ? bubbles[index] : undefined
    if (b) {
      void router.push(b.path)
    }
  }
  leaveHandler = () => {
    pointer.active = false
    hoveredIndex = -1
    hoverInfo.value = null
    if (el) el.style.cursor = 'crosshair'
  }

  brandImage = new Image()
  brandImage.src = brandIcon

  el.addEventListener('pointermove', moveHandler)
  el.addEventListener('pointerdown', downHandler)
  el.addEventListener('pointerleave', leaveHandler)
  window.addEventListener('resize', resize)
  resize()
  raf = requestAnimationFrame(frame)
}

onMounted(async () => {
  void sessions.refresh()
  void workspace.refresh()
  detectHardware()
  tickClock()
  clockTimer = window.setInterval(tickClock, 1000)
  mountOrbit()
  if (readinessChecked) {
    // 已弹过自检，回工作台只刷新在线状态
    await refreshOnline()
    return
  }
  readinessChecked = true
  await probeReadiness()
})

onBeforeUnmount(() => {
  cancelAnimationFrame(raf)
  stopTyping()
  if (warpTimer) window.clearTimeout(warpTimer)
  if (clockTimer) window.clearInterval(clockTimer)
  const el = canvas.value
  if (el && moveHandler) el.removeEventListener('pointermove', moveHandler)
  if (el && downHandler) el.removeEventListener('pointerdown', downHandler)
  if (el && leaveHandler) el.removeEventListener('pointerleave', leaveHandler)
  if (resizeHandler) window.removeEventListener('resize', resizeHandler)
})
</script>

<template>
  <div class="app-shell">
    <TopNav>
      <template #actions>
        <!-- 状态指示改用 el-tag：语义色跟随 EP，省掉手写三套状态样式 -->
        <el-tag
          class="orbit-status"
          :type="statusTagType"
          effect="plain"
          size="small"
          round
        >
          <i class="status-dot"></i>{{ statusLabel }}
        </el-tag>
      </template>
    </TopNav>

    <main ref="stage" class="orbit-stage">
      <canvas ref="canvas" class="orbit-canvas" aria-hidden="true"></canvas>

      <!-- 中央入口：可点击 / 可键盘聚焦，点击后播放跃迁动画再进入对话 -->
      <button
        class="center-orb-hit"
        type="button"
        aria-label="进入对话"
        title="进入对话"
        @click="triggerWarp"
      ></button>

      <!-- 模块悬停：全息屏 + 打字机简介 -->
      <Transition name="holo">
        <aside
          v-if="hoverInfo"
          class="holo-tip"
          :style="{
            left: `${hoverInfo.x}px`,
            top: `${hoverInfo.y}px`,
          }"
        >
          <span class="holo-corner tl"></span>
          <span class="holo-corner tr"></span>
          <span class="holo-corner bl"></span>
          <span class="holo-corner br"></span>
          <header class="holo-tip-head">
            <span class="holo-scan"></span>
            <strong>{{ hoverInfo.label }}</strong>
            <code>MODULE // {{ hoverInfo.key.toUpperCase() }}</code>
          </header>
          <p class="holo-typing">
            {{ typedText }}<i v-if="!typingDone" class="holo-caret"></i>
          </p>
          <footer class="holo-tip-foot">
            <span>HOLOGRAPHIC MODULE BRIEF</span>
            <span>点击气泡进入</span>
          </footer>
        </aside>
      </Transition>

      <!-- 中央按钮点击：环形跃迁过渡 -->
      <Transition name="warp">
        <div v-if="warping" class="warp-overlay" aria-hidden="true">
          <span class="warp-ring r1"></span>
          <span class="warp-ring r2"></span>
          <span class="warp-ring r3"></span>
          <span class="warp-core"></span>
          <span class="warp-label">ENTERING CONVERSATION</span>
        </div>
      </Transition>

      <!-- 左下：内陷卡片 + ComboBox 式全息列表 -->
      <div v-if="holoMode" class="combo-mask" @click="holoMode = null"></div>
      <div class="corner-deck left-deck" :class="{ revealed }">
        <Transition name="combo">
          <section v-if="holoMode" class="holo-panel combo-panel">
            <header class="holo-head">
              <div>
                <span class="holo-kicker mono">HOLO ARCHIVE</span>
                <h2>{{ holoTitle }}</h2>
              </div>
              <el-button
                class="holo-close"
                :icon="Close"
                circle
                size="small"
                title="关闭"
                @click="holoMode = null"
              />
            </header>
            <div class="holo-grid" aria-hidden="true"></div>

            <ul v-if="holoMode === 'sessions'" class="holo-list">
              <li v-for="item in recentSessions" :key="item.thread_id">
                <el-button class="holo-row" text @click="openSession(item.thread_id)">
                  <span class="holo-index mono">{{ item.thread_id.slice(0, 6) }}</span>
                  <span class="holo-main">{{ item.title || '新对话' }}</span>
                  <span class="holo-meta mono">{{ fmtTime(item.updated_at) }}</span>
                </el-button>
              </li>
              <li v-if="recentSessions.length === 0" class="holo-empty">
                {{ sessions.offline ? '后端离线，暂时读不到对话' : '还没有对话，去对话页发一条消息' }}
              </li>
            </ul>

            <ul v-else class="holo-list">
              <li v-for="project in projectList" :key="project.id">
                <el-button class="holo-row" text @click="openProject(project.id)">
                  <span class="holo-index mono">{{ project.marker_state === 'ok' ? 'OK' : '!' }}</span>
                  <span class="holo-main">{{ project.name }}</span>
                  <span class="holo-meta mono">{{ project.path }}</span>
                </el-button>
              </li>
              <li v-if="projectList.length === 0" class="holo-empty">
                {{ workspace.error ? workspace.error : '还没有项目，去侧边栏新建或打开一个目录' }}
              </li>
            </ul>
          </section>
        </Transition>

        <button class="depth-card" type="button" @click="toggleHolo('sessions')">
          <span class="card-kicker">最近对话</span>
          <strong class="card-value">{{ sessions.sessions.length }}</strong>
          <span class="card-preview">{{ latestSessionTitle }}</span>
          <span class="card-action">{{ holoMode === 'sessions' ? '收起列表' : '展开全息列表' }}</span>
        </button>
        <button class="depth-card" type="button" @click="toggleHolo('projects')">
          <span class="card-kicker">最近项目</span>
          <strong class="card-value">{{ visibleProjects.length }}</strong>
          <span class="card-preview">{{ latestProjectName }}</span>
          <span class="card-action">{{ holoMode === 'projects' ? '收起列表' : '展开全息列表' }}</span>
        </button>
      </div>

      <!-- 右下：硬件信息 -->
      <div class="corner-deck right-deck" :class="{ revealed }">
        <div v-if="showHardware" class="depth-card hardware-card">
          <header class="hw-head">
            <span class="card-kicker">系统状态</span>
            <el-button
              class="hw-toggle"
              :icon="Close"
              text
              circle
              size="small"
              title="隐藏硬件信息"
              @click="showHardware = false"
            />
          </header>
          <div class="hw-row">
            <span>时间</span>
            <strong class="mono">{{ clock }}</strong>
          </div>
          <div class="hw-row">
            <span>CPU</span>
            <strong :title="cpuLabel">{{ cpuLabel }}</strong>
          </div>
          <div class="hw-row">
            <span>内存</span>
            <strong>{{ memoryLabel }}</strong>
          </div>
          <div class="hw-row">
            <span>GPU</span>
            <strong :title="gpuLabel">{{ gpuLabel }}</strong>
          </div>
        </div>
        <el-button
          v-else
          class="hw-collapsed"
          :icon="View"
          text
          @click="showHardware = true"
        >
          显示硬件信息
        </el-button>
      </div>
    </main>

    <ReadinessDialog
      v-model="readinessOpen"
      :backend-online="online === true"
      :report="readiness"
      :loading="readinessLoading"
      @retry="probeReadiness"
      @locate="openSettings"
    />
  </div>
</template>

<style scoped>
.app-shell {
  min-height: 100vh;
}

.orbit-stage {
  position: relative;
  height: calc(100vh - var(--topbar-h));
  min-height: 640px;
  overflow: hidden;
}

.orbit-canvas {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  cursor: crosshair;
}

.center-orb-hit {
  position: absolute;
  left: 50%;
  top: 50%;
  z-index: 2;
  width: clamp(92px, 12vmin, 116px);
  height: clamp(92px, 12vmin, 116px);
  padding: 0;
  transform: translate(-50%, -50%);
  border: 0;
  border-radius: 50%;
  background: transparent;
  cursor: pointer;
}

.center-orb-hit:focus-visible {
  outline: 1px solid var(--accent-cyan);
  outline-offset: 6px;
}

/* el-tag 自带描边与语义色，这里只保留紧凑尺寸与间距 */
.orbit-status {
  flex: none;
  font-size: 10.5px;
  white-space: nowrap;
}

.orbit-status :deep(.el-tag__content) {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}

/* ---------- 内陷卡片 ---------- */
.corner-deck {
  position: absolute;
  bottom: 30px;
  z-index: 6;
  display: flex;
  gap: 14px;
  perspective: 1200px;
}

.left-deck {
  left: 34px;
  transform-origin: right bottom;
  opacity: 0;
  transform: translateX(-56px);
  transition:
    opacity 0.45s cubic-bezier(0.22, 1, 0.36, 1),
    transform 0.62s cubic-bezier(0.22, 1, 0.36, 1);
}

.right-deck {
  right: 34px;
  transform-origin: left bottom;
  opacity: 0;
  transform: translateX(56px);
  transition:
    opacity 0.45s cubic-bezier(0.22, 1, 0.36, 1),
    transform 0.62s cubic-bezier(0.22, 1, 0.36, 1);
}

.left-deck.revealed,
.right-deck.revealed {
  opacity: 1;
  transform: translateX(0);
}

.depth-card {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 5px;
  width: 212px;
  padding: 16px 18px 14px;
  text-align: left;
  color: var(--text-1);
  border: 1px solid rgba(126, 154, 198, 0.26);
  border-radius: 12px;
  background:
    linear-gradient(135deg, rgba(17, 30, 52, 0.62), rgba(7, 14, 26, 0.38));
  box-shadow:
    0 18px 48px rgba(2, 7, 16, 0.52),
    inset 0 1px 0 rgba(190, 220, 255, 0.08);
  backdrop-filter: blur(12px);
  transform:
    rotateY(14deg) rotateX(-4deg) translateZ(-22px);
  transform-origin: right center;
  transform-style: preserve-3d;
  transition:
    transform 0.35s cubic-bezier(0.22, 1, 0.36, 1),
    border-color 0.25s,
    background 0.25s;
}

.depth-card:hover {
  transform: rotateY(8deg) rotateX(-2deg) translateZ(-8px);
  border-color: rgba(55, 208, 196, 0.55);
}

.depth-card:focus-visible {
  outline: 1px solid var(--accent-cyan);
  outline-offset: 3px;
}

.right-deck .depth-card {
  transform: rotateY(-14deg) rotateX(-4deg) translateZ(-22px);
  transform-origin: left center;
}

.right-deck .depth-card:hover {
  transform: rotateY(-8deg) rotateX(-2deg) translateZ(-8px);
}

.card-kicker {
  font-size: 10px;
  letter-spacing: 0.12em;
  color: var(--accent-cyan);
}

.card-value {
  font-size: 28px;
  font-weight: 600;
  line-height: 1;
  letter-spacing: -0.04em;
}

.card-preview {
  overflow: hidden;
  font-size: 11.5px;
  color: var(--text-2);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.card-action {
  margin-top: 4px;
  font-size: 10px;
  color: var(--text-3);
  transition: color 0.2s;
}

.depth-card:hover .card-action {
  color: var(--accent-cyan);
}

/* 硬件卡 */
.hardware-card {
  width: 248px;
  gap: 8px;
  transform: rotateY(-14deg) rotateX(-4deg) translateZ(-22px);
}

.hw-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

/* el-button 的尺寸/悬停底色由主题桥接统一提供，这里只约束占位 */
.hw-toggle {
  flex: none;
}

.hw-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  font-size: 11px;
  color: var(--text-3);
  border-top: 1px solid rgba(126, 154, 198, 0.12);
  padding-top: 6px;
}

.hw-row strong {
  overflow: hidden;
  max-width: 160px;
  font-size: 11px;
  font-weight: 500;
  color: var(--text-1);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.hw-collapsed {
  flex: none;
  font-size: 11.5px;
}

/* ---------- ComboBox 式全息列表 ---------- */
.combo-mask {
  position: absolute;
  inset: 0;
  z-index: 5;
}

.combo-panel {
  position: absolute;
  left: 0;
  bottom: calc(100% + 14px);
  z-index: 7;
  width: min(520px, 82vw);
  max-height: min(460px, 58vh);
  transform-origin: left bottom;
}

.holo-panel {
  position: relative;
  width: min(720px, 92vw);
  max-height: min(620px, 78vh);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  padding: 22px 24px 18px;
  border: 1px solid rgba(55, 208, 196, 0.42);
  border-radius: 10px;
  background:
    repeating-linear-gradient(
      0deg,
      rgba(55, 208, 196, 0.035) 0,
      rgba(55, 208, 196, 0.035) 1px,
      transparent 1px,
      transparent 4px
    ),
    linear-gradient(145deg, rgba(6, 24, 36, 0.94), rgba(4, 10, 22, 0.88));
  box-shadow:
    0 0 0 1px rgba(55, 208, 196, 0.08),
    0 30px 90px rgba(0, 0, 0, 0.6),
    inset 0 0 60px rgba(55, 208, 196, 0.05);
  clip-path: polygon(
    0 14px, 14px 0, calc(100% - 26px) 0, 100% 26px,
    100% calc(100% - 14px), calc(100% - 14px) 100%,
    26px 100%, 0 calc(100% - 26px)
  );
}

.holo-panel.combo-panel {
  position: absolute;
  left: 0;
  bottom: calc(100% + 14px);
  z-index: 7;
  width: min(520px, 82vw);
  max-height: min(460px, 58vh);
  transform-origin: left bottom;
}

.holo-grid {
  position: absolute;
  inset: 0;
  pointer-events: none;
  background-image:
    linear-gradient(rgba(55, 208, 196, 0.05) 1px, transparent 1px),
    linear-gradient(90deg, rgba(55, 208, 196, 0.05) 1px, transparent 1px);
  background-size: 32px 32px;
  mask-image: radial-gradient(circle at 50% 40%, #000 20%, transparent 78%);
}

.holo-head {
  position: relative;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding-bottom: 14px;
  border-bottom: 1px solid rgba(55, 208, 196, 0.22);
}

.holo-kicker {
  font-size: 9px;
  letter-spacing: 0.22em;
  color: rgba(55, 208, 196, 0.75);
}

.holo-head h2 {
  margin: 4px 0 0;
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: #dffaff;
}

.holo-close {
  flex: none;
}

.holo-list {
  position: relative;
  flex: 1;
  min-height: 0;
  list-style: none;
  margin: 14px 0 0;
  padding: 0;
  overflow-y: auto;
}

.holo-list li {
  border-bottom: 1px solid rgba(55, 208, 196, 0.1);
}

/* 列表行改用 el-button(text)：保留整行可点击，网格布局与原来一致 */
.holo-list .holo-row {
  display: grid;
  grid-template-columns: 58px minmax(0, 1fr) auto;
  gap: 14px;
  align-items: center;
  width: 100%;
  height: auto;
  padding: 12px 6px;
  justify-content: normal;
  font-weight: 400;
  font-size: 13px;
  text-align: left;
  color: var(--text-1);
  border-radius: 0;
}

/*
  el-button 会给默认插槽再包一层 <span>，用 display:contents 让它“消失”，
  三个子元素就能直接参与上面的网格布局。
*/
.holo-list .holo-row :deep(> span) {
  display: contents;
}

.holo-index {
  font-size: 9.5px;
  color: rgba(55, 208, 196, 0.7);
}

.holo-main {
  overflow: hidden;
  font-size: 12.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.holo-meta {
  overflow: hidden;
  max-width: 260px;
  font-size: 10px;
  color: var(--text-3);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.holo-empty {
  padding: 26px 6px;
  font-size: 12px;
  color: var(--text-3);
  text-align: center;
}

.combo-enter-active,
.combo-leave-active {
  transition:
    opacity 0.26s ease,
    transform 0.32s cubic-bezier(0.22, 1, 0.36, 1);
}

.combo-enter-from,
.combo-leave-to {
  opacity: 0;
  transform: translateY(16px) scale(0.98);
}

/* ---------- 模块悬停全息屏 ---------- */
.holo-tip {
  position: absolute;
  z-index: 9;
  width: min(320px, 76vw);
  padding: 14px 16px 12px;
  pointer-events: none;
  transform: translate(-50%, -100%);
  border: 1px solid rgba(55, 208, 196, 0.55);
  background:
    repeating-linear-gradient(
      0deg,
      rgba(55, 208, 196, 0.06) 0,
      rgba(55, 208, 196, 0.06) 1px,
      transparent 1px,
      transparent 4px
    ),
    linear-gradient(145deg, rgba(5, 26, 38, 0.96), rgba(3, 10, 22, 0.92));
  box-shadow:
    0 0 0 1px rgba(55, 208, 196, 0.12),
    0 22px 60px rgba(0, 0, 0, 0.62),
    inset 0 0 40px rgba(55, 208, 196, 0.08);
  clip-path: polygon(
    0 10px, 10px 0, calc(100% - 18px) 0, 100% 18px,
    100% calc(100% - 10px), calc(100% - 10px) 100%,
    18px 100%, 0 calc(100% - 18px)
  );
}

.holo-tip::after {
  content: '';
  position: absolute;
  left: 50%;
  bottom: -7px;
  width: 12px;
  height: 12px;
  transform: translateX(-50%) rotate(45deg);
  border-right: 1px solid rgba(55, 208, 196, 0.55);
  border-bottom: 1px solid rgba(55, 208, 196, 0.55);
  background: rgba(4, 16, 28, 0.96);
}

.holo-corner {
  position: absolute;
  width: 10px;
  height: 10px;
  border-color: rgba(160, 240, 255, 0.9);
  border-style: solid;
  border-width: 0;
}

.holo-corner.tl { top: 5px; left: 5px; border-top-width: 1px; border-left-width: 1px; }
.holo-corner.tr { top: 5px; right: 5px; border-top-width: 1px; border-right-width: 1px; }
.holo-corner.bl { bottom: 5px; left: 5px; border-bottom-width: 1px; border-left-width: 1px; }
.holo-corner.br { bottom: 5px; right: 5px; border-bottom-width: 1px; border-right-width: 1px; }

.holo-tip-head {
  position: relative;
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding-bottom: 7px;
  overflow: hidden;
  border-bottom: 1px solid rgba(55, 208, 196, 0.28);
}

.holo-tip-head strong {
  font-size: 13px;
  font-weight: 600;
  color: #dffaff;
  letter-spacing: 0.02em;
}

.holo-tip-head code {
  margin-left: auto;
  font-family: var(--font-mono);
  font-size: 8.5px;
  color: rgba(55, 208, 196, 0.7);
  letter-spacing: 0.08em;
}

.holo-scan {
  position: absolute;
  top: 0;
  left: -40%;
  width: 40%;
  height: 100%;
  background: linear-gradient(90deg, transparent, rgba(160, 240, 255, 0.28), transparent);
  animation: holo-scan 2.4s linear infinite;
}

@keyframes holo-scan {
  to { left: 110%; }
}

.holo-typing {
  margin: 9px 0 8px;
  min-height: 54px;
  font-size: 11.5px;
  line-height: 1.7;
  color: var(--text-2);
  white-space: pre-wrap;
  word-break: break-word;
}

.holo-caret {
  display: inline-block;
  width: 6px;
  height: 12px;
  margin-left: 2px;
  vertical-align: -1px;
  background: rgba(160, 240, 255, 0.9);
  box-shadow: 0 0 8px rgba(55, 208, 196, 0.8);
  animation: caret-blink 0.82s steps(1) infinite;
}

@keyframes caret-blink {
  50% { opacity: 0; }
}

.holo-tip-foot {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  padding-top: 7px;
  font-family: var(--font-mono);
  font-size: 8px;
  letter-spacing: 0.1em;
  color: rgba(126, 154, 198, 0.65);
  border-top: 1px solid rgba(55, 208, 196, 0.16);
}

.holo-enter-active,
.holo-leave-active {
  transition: opacity 0.18s ease, transform 0.22s cubic-bezier(0.22, 1, 0.36, 1);
}

.holo-enter-from,
.holo-leave-to {
  opacity: 0;
  transform: translate(-50%, calc(-100% + 8px)) scale(0.98);
}

/* ---------- 中央按钮跃迁动画 ---------- */
.warp-overlay {
  position: absolute;
  inset: 0;
  z-index: 20;
  display: grid;
  place-items: center;
  pointer-events: none;
  background: radial-gradient(circle at 50% 50%, rgba(6, 30, 42, 0.5), rgba(2, 6, 14, 0.86));
  animation: warp-bg 0.72s ease forwards;
}

@keyframes warp-bg {
  from { opacity: 0; }
  35% { opacity: 1; }
  to { opacity: 1; }
}

.warp-ring {
  position: absolute;
  width: 120px;
  height: 120px;
  border-radius: 50%;
  border: 1px solid rgba(160, 240, 255, 0.9);
  box-shadow: 0 0 30px rgba(55, 208, 196, 0.55), inset 0 0 26px rgba(55, 208, 196, 0.25);
  animation: warp-ring 0.72s cubic-bezier(0.16, 1, 0.3, 1) forwards;
}

.warp-ring.r2 { animation-delay: 0.07s; border-color: rgba(91, 140, 255, 0.8); }
.warp-ring.r3 { animation-delay: 0.14s; border-color: rgba(55, 208, 196, 0.5); }

@keyframes warp-ring {
  from { transform: scale(0.2); opacity: 0.1; }
  40% { opacity: 1; }
  to { transform: scale(9); opacity: 0; }
}

.warp-core {
  position: absolute;
  width: 110px;
  height: 110px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(160, 240, 255, 0.95), rgba(55, 208, 196, 0.35) 45%, transparent 72%);
  animation: warp-core 0.72s ease forwards;
}

@keyframes warp-core {
  from { transform: scale(0.4); opacity: 0.3; }
  45% { transform: scale(1.25); opacity: 1; }
  to { transform: scale(0.1); opacity: 0; }
}

.warp-label {
  position: absolute;
  margin-top: 210px;
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.32em;
  color: rgba(160, 240, 255, 0.9);
  text-shadow: 0 0 12px rgba(55, 208, 196, 0.8);
  animation: warp-label 0.72s ease forwards;
}

@keyframes warp-label {
  from { opacity: 0; transform: translateY(6px); }
  40% { opacity: 1; transform: none; }
  to { opacity: 0; transform: translateY(-6px); }
}

.warp-enter-active,
.warp-leave-active {
  transition: opacity 0.18s ease;
}

.warp-enter-from,
.warp-leave-to {
  opacity: 0;
}

@media (max-width: 900px) {
  .corner-deck {
    bottom: 18px;
    gap: 10px;
  }

  .left-deck {
    left: 16px;
  }

  .right-deck {
    right: 16px;
    flex-direction: column-reverse;
  }

  .depth-card {
    width: 168px;
    padding: 12px 14px;
  }

  .hardware-card {
    width: 204px;
  }

  .card-value {
    font-size: 22px;
  }
}
</style>
