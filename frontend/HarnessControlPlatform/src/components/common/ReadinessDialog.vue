<script setup lang="ts">
// 首屏运行自检弹窗：全息样式，提示后端 / LLM / 沙箱是否满足使用条件
import { computed } from 'vue'
import { Close, Lock, MagicStick, Refresh, Connection, Warning } from '@element-plus/icons-vue'
import type { ReadinessReport } from '@/api/settings'

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    backendOnline: boolean
    report: ReadinessReport | null
    loading?: boolean
  }>(),
  { loading: false },
)

const emit = defineEmits<{
  'update:modelValue': [visible: boolean]
  retry: []
  locate: [focus: 'model' | 'sandbox']
}>()

const llmReady = computed(() => props.report?.llm.configured === true)
const sandboxReady = computed(() => props.report?.sandbox.configured === true)
// 后端离线时，LLM 状态未知，按未就绪处理
const blocking = computed(() => !props.backendOnline || !llmReady.value)
const degraded = computed(() => props.backendOnline && llmReady.value && !sandboxReady.value)

const title = computed(() => {
  if (!props.backendOnline) return '后端服务不可达'
  if (!llmReady.value) return '大模型尚未配置'
  if (!sandboxReady.value) return '运行环境已就绪，但有降级项'
  return '运行环境已就绪'
})

const subtitle = computed(() => {
  if (!props.backendOnline) return '前端读不到运行状态，需要先让后端启动成功'
  if (!llmReady.value) return '缺少模型凭据，对话请求会直接失败'
  if (!sandboxReady.value) return '未配置沙箱服务，Agent 只能读写项目文件，不能执行命令'
  return '所有依赖均已就绪，可以直接开始使用'
})

const envPath = computed(() => props.report?.env_path || '.env')

function close() {
  emit('update:modelValue', false)
}
</script>

<template>
  <Teleport to="body">
    <Transition name="readiness">
      <div v-if="modelValue" class="readiness-layer" @click.self="close">
        <section class="readiness-panel" role="dialog" aria-modal="true" aria-labelledby="readiness-title">
          <div class="readiness-grid" aria-hidden="true"></div>
          <span class="scanline" aria-hidden="true"></span>
          <span class="corner tl"></span>
          <span class="corner tr"></span>
          <span class="corner bl"></span>
          <span class="corner br"></span>

          <header class="readiness-head">
            <div class="head-text">
              <span class="kicker mono">SYSTEM READINESS // 启动自检</span>
              <h2 id="readiness-title" :class="{ danger: blocking, warn: degraded }">{{ title }}</h2>
              <p>{{ subtitle }}</p>
            </div>
            <button type="button" class="close-btn" title="关闭" @click="close">
              <el-icon><Close /></el-icon>
            </button>
          </header>

          <ul class="checks">
            <li :class="{ ok: backendOnline, bad: !backendOnline }">
              <span class="check-icon"><el-icon><Connection /></el-icon></span>
              <div class="check-text">
                <strong>后端服务</strong>
                <span>{{ backendOnline ? '已连接，接口可用' : '无法连接后端（默认 127.0.0.1:8000）' }}</span>
              </div>
              <span class="tag">{{ backendOnline ? '在线' : '离线' }}</span>
            </li>

            <li :class="{ ok: llmReady, bad: backendOnline && !llmReady }">
              <span class="check-icon"><el-icon><MagicStick /></el-icon></span>
              <div class="check-text">
                <strong>大模型</strong>
                <span v-if="!backendOnline">状态未知，需要后端在线后才能确认</span>
                <span v-else-if="llmReady">
                  {{ report?.llm.model_name || '已配置模型' }}
                  <em v-if="report?.llm.base_url">· {{ report.llm.base_url }}</em>
                </span>
                <span v-else>未配置 API Key，所有对话都会失败</span>
              </div>
              <span class="tag">{{ backendOnline ? (llmReady ? '正常' : '阻断') : '未知' }}</span>
            </li>

            <li :class="{ ok: sandboxReady, warn: backendOnline && !sandboxReady }">
              <span class="check-icon"><el-icon><Lock /></el-icon></span>
              <div class="check-text">
                <strong>沙箱服务</strong>
                <span v-if="!backendOnline">状态未知，需要后端在线后才能确认</span>
                <span v-else-if="sandboxReady">已连接沙箱，命令在隔离环境执行</span>
                <span v-else>未配置沙箱，当前为项目工作区模式</span>
              </div>
              <span class="tag">{{ backendOnline ? (sandboxReady ? '正常' : '警告') : '未知' }}</span>
            </li>
          </ul>

          <div v-if="!backendOnline" class="notice">
            <el-icon><Warning /></el-icon>
            <p>
              请先在项目根目录启动 <code>main.py</code>。若启动直接失败，优先检查
              <code>.env</code> 中的数据库连接（DB_HOST / DB_USER / DB_PASSWORD）与 Python 依赖；
              模型配置缺失不会阻止启动，但会导致对话失败。
            </p>
          </div>
          <div v-else-if="!llmReady" class="notice">
            <el-icon><Warning /></el-icon>
            <p>
              请前往「系统设置 → 全局模型」，填写 API Key 与 Base URL 后保存。保存会写入
              <code>{{ envPath }}</code> 并热更新，无需重启后端。
            </p>
          </div>
          <div v-else-if="!sandboxReady" class="notice soft">
            <el-icon><Warning /></el-icon>
            <p>{{ report?.sandbox.notice || '未配置沙箱服务，Agent 只能读写项目文件，不能执行命令。' }}</p>
          </div>

          <footer class="readiness-foot">
            <el-button size="small" :icon="Refresh" :loading="loading" @click="emit('retry')">
              重新检测
            </el-button>
            <template v-if="!backendOnline">
              <el-button size="small" @click="close">知道了</el-button>
            </template>
            <template v-else-if="!llmReady">
              <el-button size="small" @click="close">稍后配置</el-button>
              <el-button size="small" type="primary" @click="emit('locate', 'model')">
                前往配置模型
              </el-button>
            </template>
            <template v-else-if="!sandboxReady">
              <el-button size="small" @click="close">继续使用</el-button>
              <el-button size="small" type="primary" @click="emit('locate', 'sandbox')">
                查看沙箱配置
              </el-button>
            </template>
            <template v-else>
              <el-button size="small" type="primary" @click="close">开始使用</el-button>
            </template>
          </footer>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.readiness-layer {
  position: fixed;
  inset: 0;
  z-index: 2100;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(2, 6, 14, 0.62);
  backdrop-filter: blur(3px);
}

.readiness-panel {
  position: relative;
  width: min(600px, 94vw);
  max-height: calc(100vh - 48px);
  padding: 26px 28px 20px;
  overflow-y: auto;
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
    linear-gradient(145deg, rgba(6, 24, 36, 0.97), rgba(4, 10, 22, 0.95));
  box-shadow:
    0 0 0 1px rgba(55, 208, 196, 0.1),
    0 32px 100px rgba(0, 0, 0, 0.66),
    inset 0 0 70px rgba(55, 208, 196, 0.06);
  clip-path: polygon(
    0 16px,
    16px 0,
    calc(100% - 30px) 0,
    100% 30px,
    100% calc(100% - 16px),
    calc(100% - 16px) 100%,
    30px 100%,
    0 calc(100% - 30px)
  );
}

.readiness-grid {
  position: absolute;
  inset: 0;
  pointer-events: none;
  background-image:
    linear-gradient(rgba(55, 208, 196, 0.05) 1px, transparent 1px),
    linear-gradient(90deg, rgba(55, 208, 196, 0.05) 1px, transparent 1px);
  background-size: 32px 32px;
  mask-image: radial-gradient(circle at 50% 35%, #000 18%, transparent 76%);
}

.scanline {
  position: absolute;
  top: 0;
  left: -40%;
  width: 40%;
  height: 100%;
  pointer-events: none;
  background: linear-gradient(90deg, transparent, rgba(160, 240, 255, 0.12), transparent);
  animation: readiness-scan 4.2s linear infinite;
}

@keyframes readiness-scan {
  to {
    left: 115%;
  }
}

.corner {
  position: absolute;
  width: 12px;
  height: 12px;
  border-color: rgba(160, 240, 255, 0.9);
  border-style: solid;
  border-width: 0;
  pointer-events: none;
}

.corner.tl { top: 6px; left: 6px; border-top-width: 1px; border-left-width: 1px; }
.corner.tr { top: 6px; right: 6px; border-top-width: 1px; border-right-width: 1px; }
.corner.bl { bottom: 6px; left: 6px; border-bottom-width: 1px; border-left-width: 1px; }
.corner.br { bottom: 6px; right: 6px; border-bottom-width: 1px; border-right-width: 1px; }

.readiness-head {
  position: relative;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding-bottom: 14px;
  border-bottom: 1px solid rgba(55, 208, 196, 0.22);
}

.kicker {
  font-size: 9px;
  letter-spacing: 0.22em;
  color: rgba(55, 208, 196, 0.75);
}

.head-text h2 {
  margin: 6px 0 0;
  font-size: 21px;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: #dffaff;
}

.head-text h2.danger {
  color: #ffb4b4;
  text-shadow: 0 0 18px rgba(255, 96, 96, 0.28);
}

.head-text h2.warn {
  color: #ffd694;
  text-shadow: 0 0 18px rgba(255, 182, 72, 0.24);
}

.head-text p {
  margin: 6px 0 0;
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--text-3);
}

.close-btn {
  display: grid;
  place-items: center;
  flex: none;
  width: 26px;
  height: 26px;
  color: rgba(55, 208, 196, 0.85);
  border: 1px solid rgba(55, 208, 196, 0.3);
  border-radius: 50%;
  background: transparent;
  cursor: pointer;
  transition: color 0.16s ease, border-color 0.16s ease, box-shadow 0.16s ease;
}

.close-btn:hover {
  color: #dffaff;
  border-color: rgba(160, 240, 255, 0.8);
  box-shadow: 0 0 14px rgba(55, 208, 196, 0.3);
}

.checks {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 16px 0 0;
  padding: 0;
  list-style: none;
}

.checks li {
  display: grid;
  grid-template-columns: 30px minmax(0, 1fr) auto;
  gap: 12px;
  align-items: center;
  padding: 11px 13px;
  border: 1px solid rgba(126, 154, 198, 0.18);
  border-radius: 8px;
  background: rgba(8, 16, 30, 0.5);
}

.checks li.ok {
  border-color: rgba(55, 208, 196, 0.28);
}

.checks li.bad {
  border-color: rgba(255, 110, 110, 0.35);
  background: rgba(46, 12, 16, 0.35);
}

.checks li.warn {
  border-color: rgba(255, 182, 72, 0.32);
  background: rgba(42, 30, 8, 0.3);
}

.check-icon {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  color: rgba(160, 240, 255, 0.9);
  border: 1px solid rgba(55, 208, 196, 0.25);
  border-radius: 8px;
  background: rgba(55, 208, 196, 0.06);
}

.checks li.bad .check-icon {
  color: #ffb4b4;
  border-color: rgba(255, 110, 110, 0.35);
  background: rgba(255, 110, 110, 0.08);
}

.checks li.warn .check-icon {
  color: #ffd694;
  border-color: rgba(255, 182, 72, 0.32);
  background: rgba(255, 182, 72, 0.08);
}

.check-text {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 3px;
}

.check-text strong {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-1);
}

.check-text span {
  overflow: hidden;
  font-size: 10.5px;
  line-height: 1.5;
  color: var(--text-3);
  text-overflow: ellipsis;
}

.check-text em {
  font-style: normal;
  color: rgba(147, 165, 196, 0.7);
}

.tag {
  padding: 2px 8px;
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.08em;
  color: rgba(160, 240, 255, 0.85);
  border: 1px solid rgba(55, 208, 196, 0.3);
  border-radius: 999px;
  white-space: nowrap;
}

.checks li.bad .tag {
  color: #ffb4b4;
  border-color: rgba(255, 110, 110, 0.35);
}

.checks li.warn .tag {
  color: #ffd694;
  border-color: rgba(255, 182, 72, 0.32);
}

.notice {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  margin-top: 14px;
  padding: 12px 14px;
  font-size: 11px;
  line-height: 1.7;
  color: #ffd694;
  border: 1px solid rgba(255, 182, 72, 0.28);
  border-radius: 8px;
  background: rgba(42, 30, 8, 0.32);
}

.notice.soft {
  color: var(--text-2);
  border-color: rgba(126, 154, 198, 0.24);
  background: rgba(10, 18, 32, 0.45);
}

.notice .el-icon {
  flex: none;
  margin-top: 2px;
}

.notice p {
  margin: 0;
}

.notice code {
  padding: 1px 5px;
  font-family: var(--font-mono);
  font-size: 10px;
  color: #dffaff;
  border-radius: 4px;
  background: rgba(55, 208, 196, 0.1);
}

.readiness-foot {
  position: relative;
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 18px;
  padding-top: 14px;
  border-top: 1px solid rgba(55, 208, 196, 0.22);
}

.readiness-enter-active,
.readiness-leave-active {
  transition: opacity 0.2s ease;
}

.readiness-enter-active .readiness-panel,
.readiness-leave-active .readiness-panel {
  transition: transform 0.3s cubic-bezier(0.22, 1, 0.36, 1);
}

.readiness-enter-from,
.readiness-leave-to {
  opacity: 0;
}

.readiness-enter-from .readiness-panel,
.readiness-leave-to .readiness-panel {
  transform: translateY(14px) scale(0.97);
}

@media (max-width: 640px) {
  .readiness-panel {
    padding: 20px 18px 16px;
  }

  .readiness-foot {
    flex-wrap: wrap;
  }
}
</style>
