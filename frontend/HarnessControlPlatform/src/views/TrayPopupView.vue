<script setup lang="ts">
// 托盘右键弹出的全息操作面板：独立透明窗口，样式与工作台全息卡一致。
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import AppIcon from '@/components/console/AppIcon.vue'

document.title = 'Harness 托盘菜单'

const desktop = window.harnessDesktop
const running = ref<{ threadId: string; title: string; waiting?: boolean }[]>([])
const assistantVisible = ref(false)
const root = ref<HTMLElement | null>(null)
let disposeRunning: (() => void) | undefined
let disposeVisible: (() => void) | undefined
let observer: ResizeObserver | undefined
let lastHeight = 0

const assistantLabel = computed(() =>
  assistantVisible.value ? '收起悬浮助手' : '召唤悬浮助手',
)

function reportHeight(): void {
  if (!desktop || !root.value) return
  const height = Math.min(540, Math.max(160, Math.ceil(root.value.scrollHeight) + 16))
  if (height === lastHeight) return
  lastHeight = height
  desktop.resizeTrayPopup(height)
}

onMounted(async () => {
  if (!desktop) return
  running.value = await desktop.getRunningSessions().catch(() => [])
  assistantVisible.value = await desktop.isAssistantVisible().catch(() => false)
  disposeRunning = desktop.onRunningSessions((list) => {
    running.value = list
  })
  disposeVisible = desktop.onAssistantVisibleChange((visible) => {
    assistantVisible.value = visible
  })
  observer = new ResizeObserver(() => reportHeight())
  if (root.value) observer.observe(root.value)
  reportHeight()
})

onBeforeUnmount(() => {
  disposeRunning?.()
  disposeVisible?.()
  observer?.disconnect()
})

function openWindow(): void {
  desktop?.openWindow()
}

function toggleAssistant(): void {
  void desktop?.toggleAssistant()
}

function openSession(threadId: string): void {
  desktop?.openSession(threadId)
}

function quit(): void {
  desktop?.quit()
}
</script>

<template>
  <div ref="root" class="tray-popup">
    <section class="holo-pop">
      <div class="holo-grid" aria-hidden="true"></div>
      <span class="holo-corner tl"></span>
      <span class="holo-corner tr"></span>
      <span class="holo-corner bl"></span>
      <span class="holo-corner br"></span>

      <header class="pop-head">
        <span class="pop-kicker mono">COMMON HARNESS</span>
        <span class="pop-status">
          <i class="dot"></i>桌面常驻
        </span>
      </header>

      <div class="pop-actions">
        <button class="pop-item" type="button" @click="openWindow">
          <span class="item-icon"><AppIcon name="home" :size="14" /></span>
          <span class="item-main">
            <strong>打开窗体</strong>
            <small>恢复控制台主窗口</small>
          </span>
          <AppIcon name="chevronRight" :size="11" class="item-arrow" />
        </button>

        <button class="pop-item" type="button" @click="toggleAssistant">
          <span class="item-icon"><AppIcon name="sparkles" :size="14" /></span>
          <span class="item-main">
            <strong>{{ assistantLabel }}</strong>
            <small>贴边常驻的轻量对话助手</small>
          </span>
          <span class="state-pill" :class="{ on: assistantVisible }">
            {{ assistantVisible ? 'ON' : 'OFF' }}
          </span>
        </button>
      </div>

      <div class="pop-section">
        <header class="section-head">
          <span class="section-kicker mono">ACTIVE SESSIONS</span>
          <span class="section-count mono">{{ running.length }}</span>
        </header>

        <ul v-if="running.length" class="session-list">
          <li v-for="item in running" :key="item.threadId">
            <button class="session-row" type="button" @click="openSession(item.threadId)">
              <span class="work-dot" :class="{ waiting: item.waiting }"></span>
              <span class="session-title">{{ item.title || '新对话' }}</span>
              <span class="session-state mono">
                {{ item.waiting ? '待介入' : '工作中' }}
              </span>
            </button>
          </li>
        </ul>
        <p v-else class="empty-state">暂无正在工作的会话</p>
      </div>

      <footer class="pop-foot">
        <button class="quit-item" type="button" @click="quit">
          <AppIcon name="close" :size="12" />
          <span>退出 CommonHarness</span>
        </button>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.tray-popup {
  width: 100%;
  padding: 8px;
  font-family: var(--font-sans);
  color: var(--text-1);
  user-select: none;
}

.holo-pop {
  position: relative;
  padding: 14px 14px 12px;
  overflow: hidden;
  border: 1px solid rgba(55, 208, 196, 0.42);
  border-radius: 10px;
  background:
    repeating-linear-gradient(
      0deg,
      rgba(55, 208, 196, 0.03) 0,
      rgba(55, 208, 196, 0.03) 1px,
      transparent 1px,
      transparent 4px
    ),
    linear-gradient(145deg, rgba(6, 24, 36, 0.98), rgba(4, 10, 22, 0.97));
  box-shadow:
    0 0 0 1px rgba(55, 208, 196, 0.08),
    0 24px 70px rgba(0, 0, 0, 0.62),
    inset 0 0 50px rgba(55, 208, 196, 0.05);
  clip-path: polygon(
    0 12px,
    12px 0,
    calc(100% - 20px) 0,
    100% 20px,
    100% calc(100% - 12px),
    calc(100% - 12px) 100%,
    20px 100%,
    0 calc(100% - 20px)
  );
}

.holo-grid {
  position: absolute;
  inset: 0;
  pointer-events: none;
  background-image:
    linear-gradient(rgba(55, 208, 196, 0.05) 1px, transparent 1px),
    linear-gradient(90deg, rgba(55, 208, 196, 0.05) 1px, transparent 1px);
  background-size: 28px 28px;
  mask-image: radial-gradient(circle at 50% 30%, #000 20%, transparent 78%);
}

.holo-corner {
  position: absolute;
  width: 10px;
  height: 10px;
  border-color: rgba(160, 240, 255, 0.9);
  border-style: solid;
  border-width: 0;
  pointer-events: none;
}

.holo-corner.tl {
  top: 5px;
  left: 5px;
  border-top-width: 1px;
  border-left-width: 1px;
}
.holo-corner.tr {
  top: 5px;
  right: 5px;
  border-top-width: 1px;
  border-right-width: 1px;
}
.holo-corner.bl {
  bottom: 5px;
  left: 5px;
  border-bottom-width: 1px;
  border-left-width: 1px;
}
.holo-corner.br {
  bottom: 5px;
  right: 5px;
  border-bottom-width: 1px;
  border-right-width: 1px;
}

.pop-head {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 10px;
  border-bottom: 1px solid rgba(55, 208, 196, 0.22);
}

.pop-kicker {
  font-size: 9px;
  letter-spacing: 0.22em;
  color: rgba(55, 208, 196, 0.8);
}

.pop-status {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 10px;
  color: var(--text-3);
}

.pop-status .dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--accent-cyan);
  box-shadow: 0 0 8px rgba(55, 208, 196, 0.9);
}

.pop-actions {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding: 8px 0;
  border-bottom: 1px solid rgba(55, 208, 196, 0.16);
}

.pop-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 8px 9px;
  color: var(--text-1);
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  text-align: left;
}

.pop-item:hover {
  border-color: rgba(55, 208, 196, 0.35);
  background: rgba(55, 208, 196, 0.08);
  box-shadow: inset 0 0 18px rgba(55, 208, 196, 0.08);
}

.item-icon {
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  flex: none;
  color: var(--accent-cyan);
  border: 1px solid rgba(55, 208, 196, 0.32);
  border-radius: 6px;
  background: rgba(55, 208, 196, 0.06);
}

.item-main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.item-main strong {
  font-size: 12px;
  font-weight: 600;
}

.item-main small {
  font-size: 10px;
  color: var(--text-3);
}

.item-arrow {
  margin-left: auto;
  color: var(--text-3);
}

.state-pill {
  margin-left: auto;
  padding: 2px 7px;
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.08em;
  color: var(--text-3);
  border: 1px solid rgba(126, 154, 198, 0.28);
  border-radius: 999px;
}

.state-pill.on {
  color: #9ef5ea;
  border-color: rgba(55, 208, 196, 0.55);
  box-shadow: 0 0 12px rgba(55, 208, 196, 0.22);
}

.pop-section {
  position: relative;
  padding: 9px 0 4px;
}

.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 4px 6px;
}

.section-kicker {
  font-size: 8.5px;
  letter-spacing: 0.2em;
  color: rgba(55, 208, 196, 0.66);
}

.section-count {
  font-size: 9px;
  color: var(--text-3);
}

.session-list {
  display: flex;
  flex-direction: column;
  gap: 1px;
  max-height: 240px;
  margin: 0;
  padding: 0 0 2px;
  overflow-y: auto;
  list-style: none;
}

.session-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 6px 8px;
  color: var(--text-2);
  border: 0;
  border-radius: 5px;
  background: transparent;
  text-align: left;
}

.session-row:hover {
  color: var(--text-1);
  background: rgba(91, 140, 255, 0.1);
}

.work-dot {
  width: 6px;
  height: 6px;
  flex: none;
  border-radius: 50%;
  background: var(--accent-cyan);
  box-shadow: 0 0 8px rgba(55, 208, 196, 0.8);
  animation: work-pulse 1.4s ease-in-out infinite;
}

.work-dot.waiting {
  background: var(--amber);
  box-shadow: 0 0 8px rgba(242, 176, 60, 0.8);
}

@keyframes work-pulse {
  0%,
  100% {
    opacity: 0.45;
    transform: scale(0.9);
  }
  50% {
    opacity: 1;
    transform: scale(1.15);
  }
}

.session-title {
  min-width: 0;
  overflow: hidden;
  font-size: 11.5px;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.session-state {
  margin-left: auto;
  flex: none;
  font-size: 9px;
  color: var(--text-3);
}

.empty-state {
  margin: 0;
  padding: 10px 8px 12px;
  font-size: 11px;
  color: var(--text-3);
  text-align: center;
  border: 1px dashed rgba(126, 154, 198, 0.2);
  border-radius: 6px;
}

.pop-foot {
  position: relative;
  padding-top: 8px;
  margin-top: 4px;
  border-top: 1px solid rgba(55, 208, 196, 0.16);
}

.quit-item {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  width: 100%;
  padding: 7px;
  font-size: 11px;
  color: rgba(226, 96, 94, 0.85);
  border: 1px solid rgba(226, 96, 94, 0.2);
  border-radius: 5px;
  background: transparent;
}

.quit-item:hover {
  color: #ffb4b2;
  border-color: rgba(226, 96, 94, 0.55);
  background: rgba(226, 96, 94, 0.08);
}
</style>
