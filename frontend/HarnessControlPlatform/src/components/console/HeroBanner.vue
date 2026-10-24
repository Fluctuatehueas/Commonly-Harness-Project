<script setup lang="ts">
// 顶部主视觉横幅
import bg from '@/assets/BG.png'
</script>

<template>
  <section class="hero panel">
    <img class="hero-bg" :src="bg" alt="" />
    <div class="hero-mask"></div>

    <div class="hero-copy">
      <h1>Harness</h1>
      <h2>连接大模型，构建智能未来</h2>
      <p>基于大模型的多智能体控制台，开启你的 AI 之旅</p>
      <div class="slogan">
        <span class="line"></span>
        <em>More Models · More Agents · Greater Possibilities</em>
        <span class="line"></span>
      </div>
    </div>

    <div class="hero-label">
      <strong>太空电梯 ｜ Space Elevator</strong>
      <span>连接地球与太空的文明纽带</span>
    </div>
  </section>
</template>

<style scoped>
.hero {
  position: relative;
  overflow: hidden;
  min-height: 258px;
  display: flex;
  align-items: flex-start;
  padding: 26px 28px;
}

.hero-bg {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: 62% 40%;
}

.hero-mask {
  position: absolute;
  inset: 0;
  background:
    linear-gradient(90deg, rgba(6, 13, 28, 0.96) 0%, rgba(6, 13, 28, 0.78) 48%, rgba(6, 13, 28, 0.18) 78%),
    linear-gradient(0deg, rgba(6, 13, 28, 0.6), transparent 45%);
}

.hero-copy {
  position: relative;
  max-width: 460px;
}

.hero-copy h1 {
  margin: 0;
  font-size: 26px;
  font-weight: 600;
  letter-spacing: 1px;
  color: #dce9ff;
}

.hero-copy h2 {
  margin: 6px 0 12px;
  font-size: 25px;
  font-weight: 700;
  background: linear-gradient(92deg, #eaf3ff, #9cc4ff 65%, #6ea8ff);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}

.hero-copy p {
  margin: 0;
  font-size: 13px;
  color: var(--text-2);
}

.slogan {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 22px;
}

.slogan .line {
  width: 26px;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--line-strong));
}

.slogan .line:last-child {
  background: linear-gradient(90deg, var(--line-strong), transparent);
}

.slogan em {
  font-style: normal;
  font-size: 11.5px;
  letter-spacing: 1px;
  color: var(--text-2);
  text-shadow: 0 1px 6px rgba(4, 10, 24, 0.9);
}

.hero-label {
  position: absolute;
  top: 24px;
  right: 28px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  text-align: left;
}

.hero-label strong {
  font-size: 13px;
  color: #cfe3ff;
  text-shadow: 0 1px 8px rgba(4, 10, 24, 0.8);
}

.hero-label span {
  font-size: 11px;
  color: var(--text-2);
  text-shadow: 0 1px 8px rgba(4, 10, 24, 0.8);
}
</style>
