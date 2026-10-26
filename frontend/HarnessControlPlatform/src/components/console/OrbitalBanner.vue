<script setup lang="ts">
// 底部探索横幅
import { useRouter } from 'vue-router'
import AppIcon from './AppIcon.vue'
import bg from '@/assets/BG.png'

const router = useRouter()
</script>

<template>
  <section class="orbital panel">
    <img class="orbital-bg" :src="bg" alt="" />
    <div class="orbital-mask"></div>

    <div class="orbital-copy">
      <h3>探索 · 创造 · 连接</h3>
      <p>在浩瀚的宇宙中，让大模型成为你的智慧伙伴</p>
      <button class="cta" type="button" @click="router.push('/')">
        开始你的第一个任务
        <AppIcon name="arrowRight" :size="14" />
      </button>
    </div>

    <div class="orbital-tag">
      <strong>Orbital Station</strong>
      <span>E-01</span>
    </div>
  </section>
</template>

<style scoped>
.orbital {
  position: relative;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 132px;
  padding: 22px 28px;
}

.orbital-bg {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: 50% 78%;
}

.orbital-mask {
  position: absolute;
  inset: 0;
  background:
    linear-gradient(90deg, rgba(5, 11, 24, 0.93) 0%, rgba(5, 11, 24, 0.55) 55%, rgba(5, 11, 24, 0.25) 100%);
}

.orbital-copy {
  position: relative;
}

.orbital-copy h3 {
  margin: 0 0 6px;
  font-size: 19px;
  letter-spacing: 2px;
}

.orbital-copy p {
  margin: 0 0 14px;
  font-size: 12px;
  color: var(--text-2);
}

.cta {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 9px 18px;
  font-size: 12.5px;
  color: #fff;
  border: none;
  border-radius: 999px;
  background: linear-gradient(90deg, #3b78ff, #2a5cd6);
  box-shadow: 0 6px 18px rgba(50, 110, 255, 0.35);
  transition: all 0.2s;
}

.cta:hover {
  filter: brightness(1.12);
  transform: translateY(-1px);
}

.orbital-tag {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 2px;
}

.orbital-tag strong {
  font-size: 14px;
  font-weight: 600;
  letter-spacing: 1px;
  color: #dbe9ff;
  text-shadow: 0 1px 10px rgba(4, 10, 24, 0.9);
}

.orbital-tag span {
  font-size: 12px;
  color: var(--text-2);
}
</style>
