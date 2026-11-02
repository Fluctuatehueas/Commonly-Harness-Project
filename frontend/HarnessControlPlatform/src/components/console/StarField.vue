<script setup lang="ts">
// 全局背景：星尘 + 几何环 + 跟随光标的光晕 + 数字扩散点击特效
import { onBeforeUnmount, onMounted, ref } from 'vue'

const canvas = ref<HTMLCanvasElement | null>(null)
let raf = 0
let resizeHandler: (() => void) | null = null
let moveHandler: ((event: PointerEvent) => void) | null = null
let downHandler: ((event: PointerEvent) => void) | null = null
let leaveHandler: (() => void) | null = null

interface Star {
  x: number
  y: number
  r: number
  alpha: number
  twinkleSpeed: number
  phase: number
  vx: number
  vy: number
  hue: number
}

interface DigitalParticle {
  x: number
  y: number
  angle: number
  speed: number
  dist: number
  digit: string
  born: number
  life: number
  segments: number
}

interface ShockRing {
  x: number
  y: number
  born: number
  life: number
  max: number
}

function mount() {
  const el = canvas.value
  if (!el) return
  const ctx = el.getContext('2d')
  if (!ctx) return

  let width = 0
  let height = 0
  let stars: Star[] = []
  let particles: DigitalParticle[] = []
  let rings: ShockRing[] = []
  const pointer = { x: -1000, y: -1000, tx: -1000, ty: -1000 }
  const dpr = Math.min(window.devicePixelRatio || 1, 2)
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches

  function seed() {
    const density = Math.floor((width * height) / 15000)
    const count = Math.max(70, Math.min(density, 240))
    stars = Array.from({ length: count }, () => ({
      x: Math.random() * width,
      y: Math.random() * height,
      r: 0.35 + Math.random() * 1.3,
      alpha: 0.18 + Math.random() * 0.7,
      twinkleSpeed: 0.3 + Math.random() * 1.5,
      phase: Math.random() * Math.PI * 2,
      vx: (Math.random() - 0.5) * 0.05,
      vy: -0.02 - Math.random() * 0.05,
      hue: Math.random() < 0.74 ? 214 : 184,
    }))
  }

  function resize() {
    if (!el) return
    width = window.innerWidth
    height = window.innerHeight
    el.width = width * dpr
    el.height = height * dpr
    ctx!.setTransform(dpr, 0, 0, dpr, 0, 0)
    seed()
  }

  function drawGeometry(t: number) {
    // 极淡的同心几何环，缓慢旋转，给深空一点“仪器刻度”感
    const cx = width * 0.62
    const cy = height * 0.36
    const ringRadius = Math.min(width, height) * 0.42
    ctx!.save()
    ctx!.translate(cx, cy)
    ctx!.rotate(t * 0.018)
    for (let i = 0; i < 3; i += 1) {
      const r = ringRadius - i * 48
      ctx!.beginPath()
      ctx!.setLineDash(i === 1 ? [2, 10] : [26, 18])
      ctx!.lineWidth = 1
      ctx!.strokeStyle = `rgba(91, 140, 255, ${0.05 + i * 0.012})`
      ctx!.arc(0, 0, r, 0, Math.PI * 2)
      ctx!.stroke()
    }
    ctx!.setLineDash([])
    ctx!.rotate(-t * 0.09)
    for (let i = 0; i < 8; i += 1) {
      const angle = (i / 8) * Math.PI * 2
      const inner = ringRadius - 92
      const outer = ringRadius - 76
      ctx!.beginPath()
      ctx!.moveTo(Math.cos(angle) * inner, Math.sin(angle) * inner)
      ctx!.lineTo(Math.cos(angle) * outer, Math.sin(angle) * outer)
      ctx!.strokeStyle = 'rgba(55, 208, 196, 0.08)'
      ctx!.stroke()
    }
    ctx!.restore()

    // 细网格：只在左下角出现，避免整屏铺满
    ctx!.save()
    ctx!.globalAlpha = 0.03
    ctx!.strokeStyle = '#7e9ac6'
    ctx!.lineWidth = 1
    const step = 72
    for (let x = -step; x < width * 0.42; x += step) {
      ctx!.beginPath()
      ctx!.moveTo(x, height)
      ctx!.lineTo(x, height - 220)
      ctx!.stroke()
    }
    for (let y = height - 220; y < height + step; y += step) {
      ctx!.beginPath()
      ctx!.moveTo(0, y)
      ctx!.lineTo(width * 0.42, y)
      ctx!.stroke()
    }
    ctx!.restore()
  }

  function drawStars(t: number) {
    for (const star of stars) {
      star.x += star.vx
      star.y += star.vy
      if (star.y < -4) star.y = height + 4
      if (star.x < -4) star.x = width + 4
      else if (star.x > width + 4) star.x = -4
      const twinkle = 0.55 + 0.45 * Math.sin(t * star.twinkleSpeed + star.phase)
      const alpha = star.alpha * twinkle
      ctx!.beginPath()
      ctx!.arc(star.x, star.y, star.r, 0, Math.PI * 2)
      ctx!.fillStyle = `hsla(${star.hue}, 92%, 84%, ${alpha.toFixed(3)})`
      ctx!.fill()
      if (star.r > 1.15) {
        ctx!.beginPath()
        ctx!.arc(star.x, star.y, star.r * 3.2, 0, Math.PI * 2)
        ctx!.fillStyle = `hsla(${star.hue}, 95%, 78%, ${(alpha * 0.07).toFixed(3)})`
        ctx!.fill()
      }
    }
  }

  function drawPointerGlow(t: number) {
    pointer.x += (pointer.tx - pointer.x) * 0.08
    pointer.y += (pointer.ty - pointer.y) * 0.08
    if (pointer.x < -500 || pointer.y < -500) return
    const pulse = 0.85 + 0.15 * Math.sin(t * 2.1)
    const radius = 220 * pulse
    const gradient = ctx!.createRadialGradient(
      pointer.x, pointer.y, 0,
      pointer.x, pointer.y, radius,
    )
    gradient.addColorStop(0, 'rgba(55, 208, 196, 0.10)')
    gradient.addColorStop(0.42, 'rgba(91, 140, 255, 0.045)')
    gradient.addColorStop(1, 'rgba(7, 11, 20, 0)')
    ctx!.beginPath()
    ctx!.fillStyle = gradient
    ctx!.arc(pointer.x, pointer.y, radius, 0, Math.PI * 2)
    ctx!.fill()
  }

  function drawDigitalEffects(t: number) {
    // 扩散环
    rings = rings.filter((ring) => {
      const age = (t - ring.born) / ring.life
      if (age >= 1) return false
      const radius = ring.max * (1 - Math.pow(1 - age, 3))
      ctx!.beginPath()
      ctx!.strokeStyle = `rgba(55, 208, 196, ${(0.36 * (1 - age)).toFixed(3)})`
      ctx!.lineWidth = 1.2
      ctx!.arc(ring.x, ring.y, radius, 0, Math.PI * 2)
      ctx!.stroke()
      ctx!.beginPath()
      ctx!.strokeStyle = `rgba(91, 140, 255, ${(0.18 * (1 - age)).toFixed(3)})`
      ctx!.setLineDash([3, 7])
      ctx!.arc(ring.x, ring.y, radius * 0.82, 0, Math.PI * 2)
      ctx!.stroke()
      ctx!.setLineDash([])
      return true
    })

    // 多段线段 + 0/1 数字
    particles = particles.filter((p) => {
      const age = (t - p.born) / p.life
      if (age >= 1) return false
      const distance = p.dist + p.speed * age * 120
      const x = p.x + Math.cos(p.angle) * distance
      const y = p.y + Math.sin(p.angle) * distance
      const fade = 1 - age
      ctx!.save()
      ctx!.globalAlpha = fade * 0.9
      ctx!.strokeStyle = 'rgba(55, 208, 196, 0.9)'
      ctx!.lineWidth = 1
      for (let s = 0; s < p.segments; s += 1) {
        const start = 4 + s * 11
        ctx!.beginPath()
        ctx!.moveTo(
          p.x + Math.cos(p.angle) * (distance + start),
          p.y + Math.sin(p.angle) * (distance + start),
        )
        ctx!.lineTo(
          p.x + Math.cos(p.angle) * (distance + start + 5),
          p.y + Math.sin(p.angle) * (distance + start + 5),
        )
        ctx!.stroke()
      }
      ctx!.fillStyle = 'rgba(160, 240, 255, 0.92)'
      ctx!.font = '10px "Cascadia Code", Consolas, monospace'
      ctx!.fillText(p.digit, x + 4, y - 3)
      ctx!.restore()
      return true
    })
  }

  function spawnClick(x: number, y: number, now: number) {
    rings.push({ x, y, born: now, life: 900, max: 170 })
    const count = reduced ? 8 : 18
    for (let i = 0; i < count; i += 1) {
      const angle = (i / count) * Math.PI * 2 + Math.random() * 0.5
      particles.push({
        x,
        y,
        angle,
        speed: 0.9 + Math.random() * 1.4,
        dist: 2 + Math.random() * 12,
        digit: Math.random() < 0.5 ? '0' : '1',
        born: now,
        life: 620 + Math.random() * 320,
        segments: 2 + Math.floor(Math.random() * 3),
      })
    }
    if (particles.length > 180) particles.splice(0, particles.length - 180)
  }

  function frame(now: number) {
    const t = now / 1000
    ctx!.clearRect(0, 0, width, height)
    drawGeometry(t)
    drawStars(t)
    drawPointerGlow(t)
    drawDigitalEffects(t)
    raf = requestAnimationFrame(frame)
  }

  resizeHandler = resize
  moveHandler = (event) => {
    pointer.tx = event.clientX
    pointer.ty = event.clientY
  }
  downHandler = (event) => {
    if (event.button !== 0) return
    spawnClick(event.clientX, event.clientY, performance.now())
  }
  leaveHandler = () => {
    pointer.tx = -1000
    pointer.ty = -1000
  }
  window.addEventListener('resize', resize)
  window.addEventListener('pointermove', moveHandler)
  window.addEventListener('pointerdown', downHandler)
  document.addEventListener('mouseleave', leaveHandler)
  resize()
  raf = requestAnimationFrame(frame)
}

onMounted(mount)
onBeforeUnmount(() => {
  cancelAnimationFrame(raf)
  if (resizeHandler) window.removeEventListener('resize', resizeHandler)
  if (moveHandler) window.removeEventListener('pointermove', moveHandler)
  if (downHandler) window.removeEventListener('pointerdown', downHandler)
  if (leaveHandler) document.removeEventListener('mouseleave', leaveHandler)
})
</script>

<template>
  <canvas ref="canvas" class="starfield" aria-hidden="true"></canvas>
</template>

<style scoped>
.starfield {
  position: fixed;
  inset: 0;
  z-index: -1;
  width: 100%;
  height: 100%;
  pointer-events: none;
}
</style>
