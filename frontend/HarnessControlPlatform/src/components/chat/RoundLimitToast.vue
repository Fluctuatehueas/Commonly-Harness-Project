<script setup lang="ts">
// 会话轮次达上限的顶部提醒卡片：渐显浮现 / 渐隐消失 + 数码粒子特效
import AppIcon from '@/components/console/AppIcon.vue'

const props = defineProps<{ rounds: number; limit: number }>()
const emit = defineEmits<{ newChat: []; close: [] }>()

// 固定伪随机的粒子布点：位置 / 延迟 / 大小 / 色相，避免每次渲染跳动
const PARTICLES = [
  { x: 5, y: 22, d: 0, s: 2, c: '#35e0ff' },
  { x: 12, y: 68, d: 0.7, s: 1.6, c: '#7cc4ff' },
  { x: 21, y: 38, d: 1.4, s: 1.2, c: '#a78bfa' },
  { x: 30, y: 74, d: 0.3, s: 2, c: '#35e0ff' },
  { x: 38, y: 18, d: 1.1, s: 1.4, c: '#dff3ff' },
  { x: 47, y: 58, d: 0.5, s: 1.8, c: '#7cc4ff' },
  { x: 55, y: 28, d: 1.8, s: 1.2, c: '#35e0ff' },
  { x: 63, y: 70, d: 0.9, s: 2, c: '#a78bfa' },
  { x: 71, y: 40, d: 1.5, s: 1.4, c: '#7cc4ff' },
  { x: 79, y: 64, d: 0.2, s: 1.6, c: '#35e0ff' },
  { x: 86, y: 24, d: 1.2, s: 1.2, c: '#dff3ff' },
  { x: 93, y: 55, d: 0.6, s: 1.8, c: '#7cc4ff' },
]
</script>

<template>
  <div class="rl-toast">
    <!-- 数码粒子层：上浮 + 闪烁 -->
    <span class="rl-grid" aria-hidden="true"></span>
    <i
      v-for="(p, i) in PARTICLES"
      :key="i"
      class="rl-p"
      aria-hidden="true"
      :style="{
        left: `${p.x}%`,
        top: `${p.y}%`,
        width: `${p.s}px`,
        height: `${p.s}px`,
        background: p.c,
        boxShadow: `0 0 ${p.s * 4}px ${p.c}`,
        animationDelay: `${p.d}s`,
      }"
    ></i>

    <span class="rl-icon"><AppIcon name="shield" :size="17" /></span>
    <div class="rl-body">
      <strong>本次会话轮次已达 {{ props.limit }} 次</strong>
      <p>
        每多一轮，模型都要重读全部历史：回答变慢、Token 消耗持续上升，
        早期细节也可能在自动摘要中被压缩丢失。建议新开一个会话——
        你的长期偏好与已生成的文件都保存在记忆里，不会丢失。
      </p>
    </div>
    <div class="rl-actions">
      <button class="rl-new" type="button" @click="emit('newChat')">
        <AppIcon name="plus" :size="12" /> 新开会话
      </button>
      <button class="rl-close" type="button" title="关闭" @click="emit('close')">
        <AppIcon name="close" :size="11" />
      </button>
    </div>
  </div>
</template>

<style scoped>
.rl-toast {
  position: relative;
  overflow: hidden;
  display: flex;
  align-items: center;
  gap: 12px;
  max-width: 720px;
  margin: 0 auto;
  padding: 12px 14px;
  border: 1px solid rgba(53, 224, 255, 0.4);
  border-radius: 14px;
  background:
    radial-gradient(120% 140% at 85% -30%, rgba(63, 122, 255, 0.28), transparent 55%),
    linear-gradient(150deg, rgba(18, 38, 76, 0.94), rgba(7, 15, 32, 0.96));
  box-shadow:
    0 0 0 1px rgba(53, 224, 255, 0.08),
    0 10px 40px rgba(4, 10, 24, 0.7),
    0 0 60px rgba(63, 122, 255, 0.18);
  backdrop-filter: blur(14px);
  animation: rl-breathe 3.2s ease-in-out infinite;
}

/* 呼吸辉光：让卡片本身像一颗低功率的信标 */
@keyframes rl-breathe {
  0%, 100% { box-shadow: 0 0 0 1px rgba(53, 224, 255, 0.08), 0 10px 40px rgba(4, 10, 24, 0.7), 0 0 42px rgba(63, 122, 255, 0.14); }
  50%      { box-shadow: 0 0 0 1px rgba(53, 224, 255, 0.16), 0 10px 40px rgba(4, 10, 24, 0.7), 0 0 66px rgba(53, 224, 255, 0.26); }
}

/* 数码网格底纹：极淡的扫描线质感 */
.rl-grid {
  position: absolute;
  inset: 0;
  pointer-events: none;
  opacity: 0.35;
  background:
    repeating-linear-gradient(90deg, rgba(96, 156, 255, 0.05) 0 1px, transparent 1px 26px),
    repeating-linear-gradient(0deg, rgba(96, 156, 255, 0.04) 0 1px, transparent 1px 18px);
}

/* 粒子上浮 + 闪烁 */
.rl-p {
  position: absolute;
  border-radius: 50%;
  pointer-events: none;
  opacity: 0;
  animation: rl-drift 2.8s linear infinite;
}

@keyframes rl-drift {
  0%   { transform: translateY(6px); opacity: 0; }
  25%  { opacity: 1; }
  70%  { opacity: 0.7; }
  100% { transform: translateY(-16px); opacity: 0; }
}

.rl-icon {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  flex: none;
  border-radius: 10px;
  color: var(--accent-cyan);
  border: 1px solid rgba(53, 224, 255, 0.35);
  background: rgba(53, 224, 255, 0.08);
  box-shadow: 0 0 16px rgba(53, 224, 255, 0.25);
  animation: cosmic-twinkle 2.4s ease-in-out infinite;
}

.rl-body {
  flex: 1;
  min-width: 0;
}

.rl-body strong {
  display: block;
  font-size: 13px;
  background: linear-gradient(100deg, #dff3ff, #7cc4ff 55%, #35e0ff);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}

.rl-body p {
  margin: 3px 0 0;
  font-size: 11.5px;
  line-height: 1.65;
  color: var(--text-2);
}

.rl-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: none;
}

.rl-new {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 7px 13px;
  font-size: 11.5px;
  color: #fff;
  border: none;
  border-radius: 999px;
  background: linear-gradient(120deg, #3b78ff, #2a5cd6);
  box-shadow: 0 4px 14px rgba(50, 110, 255, 0.4);
}

.rl-close {
  display: grid;
  place-items: center;
  width: 22px;
  height: 22px;
  color: var(--text-3);
  border: 1px solid var(--line);
  border-radius: 6px;
  background: transparent;
}

.rl-close:hover {
  color: #ff6b6b;
  border-color: rgba(255, 93, 93, 0.4);
}
</style>
