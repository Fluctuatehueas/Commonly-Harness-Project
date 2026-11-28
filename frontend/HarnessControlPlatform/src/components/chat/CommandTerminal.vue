<script setup lang="ts">
// 全息终端：只保留最新一条命令结果，打字机逐字输出，可手动关闭
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useCommandsStore } from '@/stores/commands'

const props = withDefaults(defineProps<{ variant?: 'fixed' | 'panel' }>(), {
  variant: 'fixed',
})

const commands = useCommandsStore()
const bodyRef = ref<HTMLElement | null>(null)
const rendered = ref('')
const typing = ref(false)
let timer: number | undefined

const payload = computed(() => commands.terminalResult)

const lines = computed<string[]>(() => {
  const data = payload.value
  if (!data) return []
  const output: string[] = []
  if (data.text) output.push(...String(data.text).split('\n'))
  const columns = data.columns || []
  const rows = data.rows || []
  if (columns.length && rows.length) {
    if (output.length) output.push('')
    const widths = columns.map((column, index) =>
      Math.max(
        String(column).length,
        ...rows.map((row) => String(row[index] ?? '').length),
      ),
    )
    const formatRow = (row: (string | number | boolean | null)[]) =>
      row
        .map((cell, index) => String(cell ?? '').padEnd(widths[index] || 0))
        .join('  ')
    output.push(formatRow(columns))
    output.push(widths.map((width) => '─'.repeat(width)).join('  '))
    rows.forEach((row) => output.push(formatRow(row)))
  }
  if (!output.length) output.push('(无输出)')
  const warnings = data.warnings || []
  if (warnings.length) {
    output.push('')
    warnings.forEach((warning) => output.push(`! ${warning}`))
  }
  output.push('')
  output.push(data.ok ? '[OK] command finished' : '[ERR] command failed')
  return output
})

function stopTyping() {
  if (timer) window.clearTimeout(timer)
  timer = undefined
  typing.value = false
}

function scrollBottom() {
  const element = bodyRef.value
  if (element) element.scrollTop = element.scrollHeight
}

function startTyping() {
  stopTyping()
  const target = lines.value.join('\n')
  if (!target) {
    rendered.value = ''
    return
  }
  rendered.value = ''
  typing.value = true
  let cursor = 0
  const step = () => {
    cursor = Math.min(target.length, cursor + 2)
    rendered.value = target.slice(0, cursor)
    void nextTick(scrollBottom)
    if (cursor < target.length) {
      timer = window.setTimeout(step, 10)
    } else {
      typing.value = false
      timer = undefined
    }
  }
  step()
}

watch(
  () => commands.terminalSeq,
  () => {
    if (!commands.terminalVisible) return
    startTyping()
  },
  { immediate: true },
)

onBeforeUnmount(stopTyping)
</script>

<template>
  <Transition name="holo-terminal">
    <section
      v-if="commands.terminalVisible"
      class="holo-terminal"
      :class="props.variant"
      aria-live="polite"
    >
      <header class="ht-head">
        <span class="ht-dot" />
        <span class="ht-title mono">
          HARNESS TERMINAL
          <span class="ht-cmd">// /{{ payload?.command || '' }}</span>
        </span>
        <span class="ht-status mono" :class="{ error: payload && !payload.ok }">
          {{ payload?.ok ? 'OK' : 'ERR' }}
        </span>
        <button class="ht-close" type="button" title="关闭终端" @click="commands.closeTerminal()">
          ×
        </button>
      </header>
      <div ref="bodyRef" class="ht-body mono">
        <div class="ht-output">{{ rendered }}<span v-if="typing" class="ht-cursor">▊</span></div>
      </div>
      <footer class="ht-foot mono">
        <span>{{ payload?.title || 'command result' }}</span>
        <span class="ht-hint">{{ typing ? 'streaming…' : 'ready' }}</span>
      </footer>
    </section>
  </Transition>
</template>

<style scoped>
.holo-terminal {
  position: fixed;
  right: 24px;
  bottom: 128px;
  z-index: 60;
  width: min(560px, calc(100vw - 32px));
  max-height: 52vh;  display: flex;
  flex-direction: column;
  border: 1px solid rgba(55, 208, 196, 0.55);
  border-radius: 8px;
  background:
    linear-gradient(180deg, rgba(8, 24, 32, 0.94), rgba(6, 16, 24, 0.96));
  box-shadow:
    0 0 0 1px rgba(55, 208, 196, 0.12),
    0 18px 48px rgba(0, 0, 0, 0.55),
    0 0 28px rgba(55, 208, 196, 0.12) inset;
  overflow: hidden;
  backdrop-filter: blur(8px);
}
.holo-terminal.panel {
  position: absolute;
  left: 12px;
  right: 12px;
  bottom: 118px;
  width: auto;
  max-height: 46vh;
}
.holo-terminal::after {
  content: '';
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: repeating-linear-gradient(
    180deg,
    rgba(55, 208, 196, 0.045) 0,
    rgba(55, 208, 196, 0.045) 1px,
    transparent 1px,
    transparent 3px
  );
  opacity: 0.55;
}
.ht-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 10px;
  border-bottom: 1px solid rgba(55, 208, 196, 0.25);
  background: rgba(55, 208, 196, 0.08);
}
.ht-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--accent-cyan);
  box-shadow: 0 0 8px var(--accent-cyan);
}
.ht-title {
  flex: 1;
  font-size: 11px;
  letter-spacing: 0.08em;
  color: var(--accent-cyan);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.ht-cmd {
  color: var(--text-3);
}
.ht-status {
  font-size: 10px;
  padding: 0 6px;
  border-radius: 3px;
  border: 1px solid rgba(55, 208, 196, 0.45);
  color: var(--accent-cyan);
}
.ht-status.error {
  border-color: rgba(226, 96, 94, 0.55);
  color: var(--danger);
}
.ht-close {
  border: 0;
  background: transparent;
  color: var(--text-2);
  font-size: 16px;
  line-height: 1;
  cursor: pointer;
  padding: 0 2px;
}
.ht-close:hover {
  color: var(--danger);
}
.ht-body {
  flex: 1;
  min-height: 64px;
  overflow-y: auto;
  padding: 10px 12px;
}
.ht-output {
  margin: 0;
  color: #9fe8df;
  font-size: 11.5px;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
  text-shadow: 0 0 6px rgba(55, 208, 196, 0.25);
}
.ht-cursor {
  color: var(--accent-cyan);
  animation: ht-blink 0.9s steps(1) infinite;
}
@keyframes ht-blink {
  50% {
    opacity: 0;
  }
}
.ht-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 5px 10px;
  border-top: 1px solid rgba(55, 208, 196, 0.2);
  color: var(--text-3);
  font-size: 10px;
}
.ht-hint {
  color: rgba(55, 208, 196, 0.75);
}
.holo-terminal-enter-active,
.holo-terminal-leave-active {
  transition: opacity 0.18s ease, transform 0.18s ease;
}
.holo-terminal-enter-from,
.holo-terminal-leave-to {
  opacity: 0;
  transform: translateY(10px) scale(0.98);
}
.mono {
  font-family: var(--font-mono);
}
</style>
