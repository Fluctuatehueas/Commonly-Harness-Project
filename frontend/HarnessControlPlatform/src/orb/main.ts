// 图标窗口的独立轻量页面：不加载 Vue 应用，避免大依赖影响拖拽与动画性能。
type AssistantSide = 'left' | 'right' | 'none'

const bridge = window.harnessDesktop
const wrap = document.getElementById('wrap') as HTMLDivElement
const orb = document.getElementById('orb') as HTMLButtonElement
const warp = document.getElementById('warp') as HTMLDivElement

let side: AssistantSide = 'right'
let hovered = false
let hoveredAt = 0
let locked = false
let warping = false
let warpTimer: number | undefined
let pointerId: number | null = null
let pointerStart = { x: 0, y: 0 }
let pointerMoved = false

function render(): void {
  wrap.className = [
    `edge-${side}`,
    hovered ? 'hovered' : '',
    locked ? 'locked' : '',
    pointerMoved ? 'dragging' : '',
  ]
    .filter(Boolean)
    .join(' ')
}

function setSide(next: AssistantSide): void {
  side = next
  render()
}

function setHover(next: boolean): void {
  if (next && !hovered) hoveredAt = performance.now()
  hovered = next
  render()
}

function openPanel(): void {
  if (warping) return
  warping = true
  // 贴边时先让图标完整滑出，再播放波纹；两者共用 stage，坐标始终一致。
  if (side !== 'none' && !hovered) {
    hovered = true
    hoveredAt = performance.now()
  }
  locked = true
  render()
  const elapsed = hovered ? performance.now() - hoveredAt : 0
  const slideWait = side === 'none' ? 0 : Math.max(0, 300 - elapsed)
  warpTimer = window.setTimeout(() => {
    warp.classList.add('on')
    warpTimer = window.setTimeout(() => {
      warp.classList.remove('on')
      warping = false
      bridge?.openAssistantPanel()
    }, 720)
  }, slideWait)
}

function resetOrb(): void {
  if (warpTimer) window.clearTimeout(warpTimer)
  warping = false
  locked = false
  warp.classList.remove('on')
  setHover(false)
  cleanupPointer()
}

/** 图标半个边长/半高，与 orb.html 中的 96px 一致。 */
const ICON_HALF = 48

/**
 * 命中区域取「收起位置」与「滑出位置」的并集：
 * 图标滑出后不会从光标下方跑掉，靠边直接点击也能命中。
 */
function inTrack(x: number, y: number): boolean {
  const cx = window.innerWidth / 2
  const cy = window.innerHeight / 2
  if (Math.abs(y - cy) > ICON_HALF) return false
  if (side === 'none') return Math.abs(x - cx) <= ICON_HALF
  if (side === 'right') return x >= cx - ICON_HALF
  return x <= cx + ICON_HALF
}

function onHoverMove(event: PointerEvent): void {
  if (pointerMoved) return
  setHover(inTrack(event.clientX, event.clientY))
}

function onHoverOut(event: MouseEvent): void {
  if (pointerMoved || event.relatedTarget) return
  setHover(false)
}

// ---------- 拖拽：window 级监听，窗口移动过程中不会丢事件 ----------
function onPointerDown(event: PointerEvent): void {
  if (event.button !== 0 || warping) return
  if (!inTrack(event.clientX, event.clientY)) return
  event.preventDefault()
  pointerId = event.pointerId
  pointerMoved = false
  pointerStart = { x: event.screenX, y: event.screenY }
  window.addEventListener('pointermove', onPointerMove)
  window.addEventListener('pointerup', onPointerUp)
  window.addEventListener('pointercancel', onPointerCancel)
}

function onPointerMove(event: PointerEvent): void {
  if (pointerId === null || event.pointerId !== pointerId) return
  if (
    !pointerMoved &&
    Math.hypot(event.screenX - pointerStart.x, event.screenY - pointerStart.y) > 4
  ) {
    pointerMoved = true
    render()
    bridge?.startAssistantDrag(hovered || locked)
  }
}

function onPointerUp(event: PointerEvent): void {
  if (pointerId === null || event.pointerId !== pointerId) return
  const moved = pointerMoved
  cleanupPointer()
  if (moved) bridge?.endAssistantDrag()
  else openPanel()
}

function onPointerCancel(event: PointerEvent): void {
  if (pointerId === null || event.pointerId !== pointerId) return
  const moved = pointerMoved
  cleanupPointer()
  if (moved) bridge?.endAssistantDrag()
}

function cleanupPointer(): void {
  window.removeEventListener('pointermove', onPointerMove)
  window.removeEventListener('pointerup', onPointerUp)
  window.removeEventListener('pointercancel', onPointerCancel)
  pointerId = null
  pointerMoved = false
  render()
}

// 指针监听挂在整窗上：贴边时图标会滑动，绑定在图标上会在滑出瞬间丢失 hover 而反复抖动。
wrap.addEventListener('pointerdown', onPointerDown)
window.addEventListener('pointermove', onHoverMove)
window.addEventListener('mouseout', onHoverOut)
orb.addEventListener('keydown', (event) => {
  if (event.key !== 'Enter' && event.key !== ' ') return
  event.preventDefault()
  openPanel()
})
wrap.addEventListener('dragstart', (event) => event.preventDefault())

bridge?.onAssistantMode((next) => setSide(next))
bridge?.onAssistantReset(resetOrb)
if (bridge) {
  bridge
    .getAssistantMode()
    .then((next) => setSide(next))
    .catch(() => setSide('right'))
} else {
  setSide('right')
}
