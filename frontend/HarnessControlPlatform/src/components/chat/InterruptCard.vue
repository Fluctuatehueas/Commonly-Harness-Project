<script setup lang="ts">
// HITL 人工介入卡片：动画浮现；支持补充信息与工具审批两类中断
import { computed, ref } from 'vue'
import AppIcon from '@/components/console/AppIcon.vue'
import { useChatStore, type ChatMsg } from '@/stores/chat'

const props = defineProps<{ msg: ChatMsg }>()
const chat = useChatStore()

const answer = ref('')
const rejectReason = ref('')

const isApproval = computed(() => props.msg.interruptType === 'hitl_approval')
const payload = computed(() => props.msg.payload || {})
const actionRequests = computed(() => (payload.value.action_requests as { name: string; args: Record<string, unknown> }[]) || [])
const informationNeeded = computed(() => String(payload.value.information_needed || ''))
const contextText = computed(() => String(payload.value.context || ''))
const resolved = computed(() => props.msg.resolved)

function submitAnswer() {
  const text = answer.value.trim()
  if (!text) return
  chat.resume(props.msg, { text }, 'answered')
}

function approveAll() {
  const decisions = actionRequests.value.map(() => ({ type: 'approve' }))
  chat.resume(props.msg, { decisions }, 'approved')
}

function rejectAll() {
  const message = rejectReason.value.trim() || '用户拒绝执行该操作'
  const decisions = actionRequests.value.map(() => ({ type: 'reject', message }))
  chat.resume(props.msg, { decisions }, 'rejected')
}
</script>

<template>
  <div class="hitl panel" :class="{ resolved }">
    <div class="glow"></div>

    <div class="head">
      <span class="icon"><AppIcon :name="isApproval ? 'shield' : 'message'" :size="15" /></span>
      <strong>{{ isApproval ? '人工审批' : '需要补充信息' }}</strong>
      <span v-if="resolved" class="tag-done">
        <AppIcon name="check" :size="11" />
        {{ resolved === 'rejected' ? '已拒绝' : resolved === 'approved' ? '已批准' : '已回复' }}
      </span>
      <span v-else class="tag-wait"><i class="dot"></i>等待处理</span>
    </div>

    <!-- 补充信息 -->
    <template v-if="!isApproval">
      <p class="question">{{ informationNeeded }}</p>
      <p v-if="contextText" class="context">{{ contextText }}</p>
      <template v-if="!resolved">
        <textarea v-model="answer" rows="2" placeholder="输入补充信息…" @keydown.enter.exact.prevent="submitAnswer"></textarea>
        <div class="actions">
          <button class="primary" type="button" :disabled="!answer.trim()" @click="submitAnswer">
            提交回复 <AppIcon name="arrowRight" :size="13" />
          </button>
        </div>
      </template>
    </template>

    <!-- 工具审批 -->
    <template v-else>
      <ul class="actions-list">
        <li v-for="(action, i) in actionRequests" :key="i">
          <code>{{ action.name }}</code>
          <pre>{{ JSON.stringify(action.args ?? {}, null, 2) }}</pre>
        </li>
      </ul>
      <template v-if="!resolved">
        <input v-model="rejectReason" placeholder="拒绝理由（可选）" />
        <div class="actions">
          <button class="primary" type="button" @click="approveAll">
            <AppIcon name="check" :size="13" /> 批准执行
          </button>
          <button class="danger" type="button" @click="rejectAll">
            <AppIcon name="close" :size="13" /> 拒绝
          </button>
        </div>
      </template>
    </template>
  </div>
</template>

<style scoped>
.hitl {
  position: relative;
  overflow: hidden;
  padding: 14px 16px;
  border: 1px solid rgba(53, 224, 255, 0.35);
  background: linear-gradient(180deg, rgba(18, 40, 78, 0.85), rgba(12, 26, 52, 0.9));
  animation: rise 0.55s cubic-bezier(0.22, 1, 0.36, 1);
}

/* 浮现动画：上浮 + 缩放 + 光晕脉冲 */
@keyframes rise {
  from {
    opacity: 0;
    transform: translateY(18px) scale(0.97);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

.glow {
  position: absolute;
  inset: -40%;
  background: radial-gradient(circle at 50% 0%, rgba(53, 224, 255, 0.14), transparent 60%);
  animation: breathe 2.6s ease-in-out infinite;
  pointer-events: none;
}

@keyframes breathe {
  0%, 100% { opacity: 0.5; }
  50% { opacity: 1; }
}

.hitl.resolved {
  opacity: 0.55;
  border-color: var(--line);
}

.hitl.resolved .glow {
  display: none;
}

.head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}

.icon {
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  border-radius: 7px;
  color: var(--accent-cyan);
  background: rgba(53, 224, 255, 0.12);
}

.tag-wait {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 10.5px;
  color: var(--accent-cyan);
}

.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent-cyan);
  box-shadow: 0 0 6px var(--accent-cyan);
  animation: pulse 1s infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}

.tag-done {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 10.5px;
  color: var(--green);
}

.question {
  margin: 10px 0 0;
  font-size: 13px;
  line-height: 1.6;
}

.context {
  margin: 6px 0 0;
  font-size: 11.5px;
  color: var(--text-3);
}

textarea,
input {
  width: 100%;
  margin-top: 10px;
  padding: 9px 11px;
  font-size: 12.5px;
  font-family: inherit;
  color: var(--text-1);
  border: 1px solid var(--line-strong);
  border-radius: 9px;
  background: rgba(6, 13, 28, 0.7);
  resize: vertical;
  outline: none;
}

textarea:focus,
input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 2px rgba(79, 140, 255, 0.15);
}

.actions-list {
  list-style: none;
  margin: 10px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.actions-list li {
  padding: 9px 11px;
  border: 1px solid var(--line);
  border-radius: 9px;
  background: rgba(6, 13, 28, 0.55);
}

.actions-list code {
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 12px;
  color: #ffd28a;
}

.actions-list pre {
  margin: 6px 0 0;
  font-size: 11px;
  line-height: 1.5;
  color: var(--text-2);
  white-space: pre-wrap;
  word-break: break-all;
}

.actions {
  display: flex;
  gap: 10px;
  margin-top: 10px;
}

.actions button {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  font-size: 12.5px;
  border-radius: 999px;
  border: none;
  transition: all 0.18s;
}

.primary {
  color: #fff;
  background: linear-gradient(90deg, #3b78ff, #2a5cd6);
  box-shadow: 0 4px 14px rgba(50, 110, 255, 0.35);
}

.primary:hover {
  filter: brightness(1.12);
}

.primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  filter: none;
}

.danger {
  color: #ff8f8f;
  border: 1px solid rgba(255, 93, 93, 0.4) !important;
  background: rgba(255, 93, 93, 0.08);
}

.danger:hover {
  background: rgba(255, 93, 93, 0.16);
}
</style>
