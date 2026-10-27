<script setup lang="ts">
// 全局星辰粒子背景：canvas 绘制的漂浮星尘 + 闪烁恒星
// 挂在 #app 最底层（z-index:-1），面板的半透明背景可以直接透出星域。
import { onBeforeUnmount, onMounted, ref } from 'vue'

const canvas = ref<HTMLCanvasElement | null>(null)
let raf = 0
let resizeHandler: (() => void) | null = null

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

function mount() {
  const el = canvas.value
  if (!el) return
  const ctx = el.getContext('2d')
  if (!ctx) return

  let width = 0
  let height = 0
  let stars: Star[] = []
  const dpr = Math.min(window.devicePixelRatio || 1, 2)

  function seed() {
    const density = Math.floor((width * height) / 14000)
    const count = Math.max(80, Math.min(density, 260))
    stars = Array.from({ length: count }, () => ({
      x: Math.random() * width,
      y: Math.random() * height,
      r: 0.4 + Math.random() * 1.4,
      alpha: 0.2 + Math.random() * 0.8,
      twinkleSpeed: 0.4 + Math.random() * 1.6,
      phase: Math.random() * Math.PI * 2,
      vx: (Math.random() - 0.5) * 0.06,
      vy: -0.02 - Math.random() * 0.06,
      hue: Math.random() < 0.72 ? 210 : 185,
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

  function frame(now: number) {
    const t = now / 1000
    ctx!.clearRect(0, 0, width, height)
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
      ctx!.fillStyle = `hsla(${star.hue}, 90%, 82%, ${alpha.toFixed(3)})`
      ctx!.fill()
      // 较亮的星带一圈光晕，模拟粒子辉光
      if (star.r > 1.2) {
        ctx!.beginPath()
        ctx!.arc(star.x, star.y, star.r * 3, 0, Math.PI * 2)
        ctx!.fillStyle = `hsla(${star.hue}, 95%, 75%, ${(alpha * 0.08).toFixed(3)})`
        ctx!.fill()
      }
    }
    raf = requestAnimationFrame(frame)
  }

  resizeHandler = resize
  window.addEventListener('resize', resize)
  resize()
  raf = requestAnimationFrame(frame)
}

onMounted(mount)
onBeforeUnmount(() => {
  cancelAnimationFrame(raf)
  if (resizeHandler) window.removeEventListener('resize', resizeHandler)
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
