<script setup lang="ts">
// 上下文压缩状态提示条：分「压缩」「评估」两阶段动画，退场时淡出并收起。
import { computed } from 'vue'
import AppIcon from '@/components/console/AppIcon.vue'
import type { CompactionState } from '@/stores/chat'

const props = defineProps<{ state: CompactionState | null }>()

const reasonLabel: Record<string, string> = {
  soft_watermark: '达到软水位',
  hard_watermark: '达到硬水位',
  defer_limit: '延迟次数用尽',
  manual: '手动压缩',
}

const phase = computed(() => props.state?.phase ?? 'compacting')
const isEvaluating = computed(() => phase.value === 'evaluating' || phase.value === 'exiting')
const isCompressed = computed(() => phase.value === 'exiting')
const failed = computed(() => Boolean(props.state?.error))
const title = computed(() =>
  failed.value ? '上下文压缩失败，已使用兜底策略继续' : '正在压缩并评估上下文',
)
</script>

<template>
  <Transition name="cp-slide">
    <div
      v-if="props.state"
      class="compaction-banner"
      :class="{
        degraded: failed,
        evaluating: isEvaluating && !failed,
        leaving: props.state.exiting,
      }"
    >
      <!-- 左侧：旋转环 + 呼吸光晕，压缩与评估阶段转速不同 -->
      <span class="cp-orb" :class="{ slow: isEvaluating }">
        <i class="ring outer"></i>
        <i class="ring inner"></i>
        <AppIcon name="layers" :size="13" />
      </span>

      <div class="cp-body">
        <div class="cp-title">
          <strong>{{ title }}</strong>
          <span v-if="!failed" class="cp-pct">
            {{ Math.round((props.state.pct || 0) * 100) }}%
          </span>
        </div>

        <div v-if="!failed" class="cp-steps">
          <span class="cp-step" :class="{ active: !isEvaluating, done: isEvaluating }">
            <i class="dot"></i>
            压缩历史
          </span>
          <span class="cp-rail" :class="{ filled: isEvaluating }">
            <i></i>
          </span>
          <span class="cp-step" :class="{ active: isEvaluating, done: isCompressed }">
            <i class="dot"></i>
            评估锚点
          </span>
          <span class="cp-meta">
            {{ reasonLabel[props.state.reason] || props.state.reason }} ·
            {{ props.state.usedTokens.toLocaleString('zh-CN') }} /
            {{ props.state.limit.toLocaleString('zh-CN') }}
          </span>
        </div>
        <small v-else class="cp-error">{{ props.state.error }}</small>
      </div>

      <!-- 右侧：扫描条，持续提示“还在处理” -->
      <span v-if="!failed" class="cp-scan"><i></i></span>
    </div>
  </Transition>
</template>

<style scoped>
.compaction-banner {
  position: relative;
  display: flex;
  align-items: center;
  gap: 11px;
  margin: 10px 0 0;
  padding: 9px 12px;
  max-height: 96px;
  overflow: hidden;
  border-radius: 11px;
  border: 1px solid rgba(124, 140, 255, 0.34);
  background: linear-gradient(100deg, rgba(86, 108, 255, 0.16), rgba(150, 96, 255, 0.1));
  color: #dbe3ff;
  transform-origin: top center;
  transition:
    opacity 0.34s cubic-bezier(0.4, 0, 0.2, 1),
    transform 0.34s cubic-bezier(0.4, 0, 0.2, 1),
    max-height 0.34s cubic-bezier(0.4, 0, 0.2, 1),
    margin 0.34s cubic-bezier(0.4, 0, 0.2, 1),
    padding 0.34s cubic-bezier(0.4, 0, 0.2, 1),
    border-color 0.3s ease;
}

.compaction-banner.evaluating {
  border-color: rgba(90, 200, 220, 0.4);
  background: linear-gradient(100deg, rgba(72, 148, 255, 0.16), rgba(64, 210, 200, 0.1));
}

.compaction-banner.degraded {
  border-color: rgba(242, 176, 60, 0.45);
  background: rgba(242, 176, 60, 0.12);
  color: #f6d79a;
}

/* 退场：淡出 + 上移 + 收起高度 */
.compaction-banner.leaving {
  opacity: 0;
  transform: translateY(-6px) scale(0.985);
  max-height: 0;
  margin-top: 0;
  padding-top: 0;
  padding-bottom: 0;
}

.cp-orb {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  flex: none;
  color: #cfd8ff;
}

.cp-orb .ring {
  position: absolute;
  inset: 0;
  border-radius: 50%;
  border: 1.5px solid transparent;
}

.cp-orb .ring.outer {
  border-top-color: #7c8cff;
  border-right-color: #7c8cff;
  animation: cp-spin 1.1s linear infinite;
}

.cp-orb .ring.inner {
  inset: 4px;
  border-bottom-color: rgba(124, 140, 255, 0.6);
  border-left-color: rgba(124, 140, 255, 0.6);
  animation: cp-spin 1.8s linear infinite reverse;
}

.cp-orb.slow .ring.outer {
  border-top-color: #46d6c8;
  border-right-color: #46d6c8;
  animation-duration: 1.7s;
}

.cp-orb.slow .ring.inner {
  border-bottom-color: rgba(70, 214, 200, 0.6);
  border-left-color: rgba(70, 214, 200, 0.6);
}

.cp-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.cp-title {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.cp-title strong {
  font-size: 12px;
  font-weight: 600;
}

.cp-pct {
  font-family: var(--font-mono);
  font-size: 10.5px;
  color: #b8c4ff;
}

.cp-steps {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 10.5px;
  color: rgba(219, 227, 255, 0.72);
  min-width: 0;
}

.cp-step {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
  opacity: 0.55;
  transition: opacity 0.25s ease, color 0.25s ease;
}

.cp-step .dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentColor;
}

.cp-step.active {
  opacity: 1;
  color: #cfd8ff;
}

.cp-step.active .dot {
  animation: cp-pulse 1.1s ease-in-out infinite;
}

.cp-step.done {
  opacity: 0.9;
  color: #7ee0d2;
}

.cp-rail {
  position: relative;
  width: 34px;
  height: 2px;
  border-radius: 2px;
  background: rgba(255, 255, 255, 0.16);
  overflow: hidden;
  flex: none;
}

.cp-rail i {
  position: absolute;
  inset: 0;
  width: 0;
  background: linear-gradient(90deg, #7c8cff, #46d6c8);
  transition: width 0.45s cubic-bezier(0.4, 0, 0.2, 1);
}

.cp-rail.filled i {
  width: 100%;
}

.cp-meta {
  margin-left: auto;
  font-family: var(--font-mono);
  font-size: 10px;
  color: rgba(219, 227, 255, 0.55);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.cp-error {
  font-size: 10.5px;
  opacity: 0.85;
}

.cp-scan {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 1.5px;
  overflow: hidden;
}

.cp-scan i {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 28%;
  background: linear-gradient(90deg, transparent, rgba(160, 176, 255, 0.9), transparent);
  animation: cp-scan 1.6s ease-in-out infinite;
}

.cp-slide-enter-active {
  transition:
    opacity 0.32s cubic-bezier(0.22, 0.8, 0.28, 1),
    transform 0.32s cubic-bezier(0.22, 0.8, 0.28, 1),
    max-height 0.32s cubic-bezier(0.22, 0.8, 0.28, 1);
}

.cp-slide-enter-from {
  opacity: 0;
  transform: translateY(-8px) scale(0.98);
  max-height: 0;
}

@keyframes cp-spin {
  to {
    transform: rotate(360deg);
  }
}

@keyframes cp-pulse {
  0%, 100% {
    transform: scale(1);
    box-shadow: 0 0 0 0 rgba(160, 176, 255, 0.45);
  }
  50% {
    transform: scale(1.35);
    box-shadow: 0 0 0 3px rgba(160, 176, 255, 0);
  }
}

@keyframes cp-scan {
  0% {
    left: -30%;
  }
  100% {
    left: 100%;
  }
}
</style>
