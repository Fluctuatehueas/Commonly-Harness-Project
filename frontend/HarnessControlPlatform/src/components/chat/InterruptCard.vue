<script setup lang="ts">
// HITL 人工介入卡片：支持审批、补充信息与推荐选项（hitl_request）
import { computed, reactive, ref, watch } from 'vue'
import AppIcon from '@/components/console/AppIcon.vue'
import UiSwitch from '@/components/console/UiSwitch.vue'
import { useChatStore, type ChatMsg } from '@/stores/chat'

interface HitlOption {
  value: string
  label: string
  description?: string
  recommended?: boolean
}

interface HitlField {
  name: string
  label: string
  type: string
  required?: boolean
  default?: unknown
  placeholder?: string
  description?: string
  options?: HitlOption[]
}

interface HitlRequest {
  hitl_id: string
  kind: string
  title: string
  description: string
  fields: HitlField[]
  options: HitlOption[]
  allow_multiple: boolean
  allow_free_text: boolean
  submit_label: string
  cancel_label: string
  metadata?: Record<string, unknown>
}

interface HostApproval {
  tool: string
  summary: string
  reason: string
  args: Record<string, unknown>
}

const props = defineProps<{ msg: ChatMsg }>()
const chat = useChatStore()

const payload = computed(() => props.msg.payload || {})
const isApproval = computed(() => props.msg.interruptType === 'hitl_approval')
const isHostApproval = computed(() => props.msg.interruptType === 'host_approval')
const hostApproval = computed<HostApproval | null>(() =>
  isHostApproval.value
    ? ((payload.value.host_approval as HostApproval | undefined) ?? null)
    : null,
)
const hitlRequest = computed(() =>
  props.msg.interruptType === 'hitl_request'
    ? ((payload.value.hitl as HitlRequest) || null)
    : null,
)
const metadataContext = computed(() =>
  String(hitlRequest.value?.metadata?.context || ''),
)
const resolved = computed(() => props.msg.resolved)

// ---------- 旧版补充信息 ----------
const answer = ref('')
const informationNeeded = computed(() => String(payload.value.information_needed || ''))
const legacyContext = computed(() => String(payload.value.context || ''))

// ---------- 新版 HitlRequest ----------
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const values = reactive<Record<string, any>>({})
const selected = ref<string[]>([])
const freeText = ref('')
const formError = ref('')

watch(
  hitlRequest,
  (request) => {
    for (const key of Object.keys(values)) delete values[key]
    selected.value = []
    freeText.value = ''
    formError.value = ''
    for (const field of request?.fields || []) {
      values[field.name] = field.default ?? (field.type === 'boolean' ? false : '')
    }
  },
  { immediate: true },
)

function fieldOptions(field: HitlField): HitlOption[] {
  return field.options || []
}

function optionSelected(field: HitlField, value: string): boolean {
  const current = values[field.name]
  return Array.isArray(current) ? current.includes(value) : current === value
}

function toggleFieldOption(field: HitlField, value: string) {
  if (field.type === 'multiselect') {
    const current = Array.isArray(values[field.name])
      ? [...(values[field.name] as string[])]
      : []
    const index = current.indexOf(value)
    if (index >= 0) current.splice(index, 1)
    else current.push(value)
    values[field.name] = current
    return
  }
  values[field.name] = value
}

function toggleOption(option: HitlOption) {
  if (!hitlRequest.value) return
  if (hitlRequest.value.allow_multiple) {
    const index = selected.value.indexOf(option.value)
    if (index >= 0) selected.value.splice(index, 1)
    else selected.value.push(option.value)
  } else {
    selected.value = [option.value]
  }
}

function optionPicked(option: HitlOption): boolean {
  return selected.value.includes(option.value)
}

function missingRequired(): boolean {
  const request = hitlRequest.value
  if (!request) return false
  for (const field of request.fields || []) {
    if (!field.required) continue
    const value = values[field.name]
    const empty = Array.isArray(value)
      ? value.length === 0
      : value === '' || value === null || value === undefined
    if (empty) return true
  }
  // 只有选项、没有自由输入时，必须选一个
  if (request.options.length > 0 && !request.allow_free_text && selected.value.length === 0) {
    return true
  }
  return false
}

function submitHitl() {
  const request = hitlRequest.value
  if (!request) return
  if (missingRequired()) {
    formError.value = '请先完成必填项'
    return
  }
  formError.value = ''
  chat.resume(
    props.msg,
    {
      hitl_id: request.hitl_id,
      values: { ...values },
      selected: selected.value,
      free_text: freeText.value,
      cancelled: false,
    },
    'answered',
  )
}

function cancelHitl() {
  const request = hitlRequest.value
  if (!request) return
  chat.resume(
    props.msg,
    {
      hitl_id: request.hitl_id,
      values: {},
      selected: [],
      free_text: '',
      cancelled: true,
    },
    'rejected',
  )
}

// ---------- 旧版补充信息提交 ----------
function submitAnswer() {
  const text = answer.value.trim()
  if (!text) return
  chat.resume(props.msg, { text }, 'answered')
}

// ---------- 工具审批 ----------
const rejectReason = ref('')
const actionRequests = computed(
  () =>
    (payload.value.action_requests as {
      name: string
      args: Record<string, unknown>
    }[]) || [],
)

function approveAll() {
  const decisions = actionRequests.value.map(() => ({ type: 'approve' }))
  chat.resume(props.msg, { decisions }, 'approved')
}

function rejectAll() {
  const message = rejectReason.value.trim() || '用户拒绝执行该操作'
  const decisions = actionRequests.value.map(() => ({ type: 'reject', message }))
  chat.resume(props.msg, { decisions }, 'rejected')
}

// ---------- 宿主能力审批 ----------
function approveHost(scope: 'once' | 'thread') {
  chat.resume(
    props.msg,
    { decision: scope === 'thread' ? 'approve_thread' : 'approve' },
    'approved',
  )
}

function rejectHost() {
  const message = rejectReason.value.trim() || '用户拒绝执行该宿主操作'
  chat.resume(props.msg, { decision: 'reject', message }, 'rejected')
}
</script>

<template>
  <div class="hitl panel" :class="{ resolved }">
    <div class="glow"></div>

    <div class="head">
      <span class="icon">
        <AppIcon :name="isHostApproval || isApproval ? 'shield' : hitlRequest?.kind === 'choice' || hitlRequest?.kind === 'mixed' ? 'target' : 'message'" :size="15" />
      </span>
      <strong>{{ isHostApproval ? '宿主操作审批' : isApproval ? '人工审批' : hitlRequest?.title || '需要补充信息' }}</strong>
      <span v-if="resolved" class="tag-done">
        <AppIcon name="check" :size="11" />
        {{ resolved === 'rejected' ? '已拒绝' : resolved === 'approved' ? '已批准' : '已回复' }}
      </span>
      <span v-else class="tag-wait"><i class="dot"></i>等待处理</span>
    </div>

    <!-- 宿主能力审批：批准一次 / 仅本会话允许 / 拒绝 -->
    <template v-if="isHostApproval && hostApproval">
      <p class="question">{{ hostApproval.summary }}</p>
      <p v-if="hostApproval.reason" class="context">原因：{{ hostApproval.reason }}</p>
      <ul class="actions-list">
        <li>
          <code>{{ hostApproval.tool }}</code>
          <pre>{{ JSON.stringify(hostApproval.args ?? {}, null, 2) }}</pre>
        </li>
      </ul>
      <template v-if="!resolved">
        <input v-model="rejectReason" placeholder="拒绝理由（可选）" />
        <div class="actions">
          <button class="primary" type="button" @click="approveHost('once')">
            <AppIcon name="check" :size="13" /> 批准一次
          </button>
          <button class="ghost-btn" type="button" @click="approveHost('thread')">
            <AppIcon name="shield" :size="13" /> 仅本会话允许
          </button>
          <button class="danger" type="button" @click="rejectHost">
            <AppIcon name="close" :size="13" /> 拒绝
          </button>
        </div>
      </template>
    </template>

    <!-- 审批 -->
    <template v-else-if="isApproval">
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

    <!-- 新版 HitlRequest：表单 + 推荐选项 -->
    <template v-else-if="hitlRequest">
      <p v-if="hitlRequest.description" class="question">{{ hitlRequest.description }}</p>
      <p v-if="metadataContext" class="context">
        {{ metadataContext }}
      </p>

      <div v-for="field in hitlRequest.fields" :key="field.name" class="field">
        <label>
          {{ field.label || field.name }}
          <em v-if="field.required">*</em>
        </label>
        <p v-if="field.description" class="field-desc">{{ field.description }}</p>

        <UiSwitch
          v-if="field.type === 'boolean'"
          :model-value="Boolean(values[field.name])"
          :disabled="Boolean(resolved)"
          @update:model-value="(v) => (values[field.name] = v)"
        />

        <select
          v-else-if="field.type === 'select'"
          v-model="values[field.name]"
          :disabled="Boolean(resolved)"
        >
          <option value="">请选择</option>
          <option v-for="opt in fieldOptions(field)" :key="opt.value" :value="opt.value">
            {{ opt.label }}
          </option>
        </select>

        <div v-else-if="field.type === 'multiselect'" class="chips">
          <button
            v-for="opt in fieldOptions(field)"
            :key="opt.value"
            type="button"
            class="chip"
            :class="{ on: optionSelected(field, opt.value) }"
            :disabled="Boolean(resolved)"
            @click="toggleFieldOption(field, opt.value)"
          >
            {{ opt.label }}
          </button>
        </div>

        <textarea
          v-else-if="field.type === 'textarea'"
          v-model="values[field.name]"
          rows="2"
          :placeholder="field.placeholder || '请输入…'"
          :disabled="Boolean(resolved)"
        ></textarea>

        <input
          v-else
          v-model="values[field.name]"
          :type="field.type === 'number' ? 'number' : 'text'"
          :placeholder="field.placeholder || '请输入…'"
          :disabled="Boolean(resolved)"
        />
      </div>

      <div v-if="hitlRequest.options.length" class="choice-block">
        <div class="chips">
          <button
            v-for="opt in hitlRequest.options"
            :key="opt.value"
            type="button"
            class="chip option"
            :class="{ on: optionPicked(opt), rec: opt.recommended }"
            :disabled="Boolean(resolved)"
            :title="opt.description || ''"
            @click="toggleOption(opt)"
          >
            <span>{{ opt.label }}</span>
            <em v-if="opt.recommended">推荐</em>
          </button>
        </div>
      </div>

      <textarea
        v-if="hitlRequest.allow_free_text && !resolved"
        v-model="freeText"
        rows="2"
        placeholder="补充说明（可选）"
      ></textarea>

      <p v-if="formError" class="form-error">{{ formError }}</p>

      <div v-if="!resolved" class="actions">
        <button class="primary" type="button" @click="submitHitl">
          {{ hitlRequest.submit_label || '提交' }} <AppIcon name="arrowRight" :size="13" />
        </button>
        <button class="ghost-btn" type="button" @click="cancelHitl">
          {{ hitlRequest.cancel_label || '取消' }}
        </button>
      </div>
    </template>

    <!-- 旧版补充信息 -->
    <template v-else>
      <p class="question">{{ informationNeeded }}</p>
      <p v-if="legacyContext" class="context">{{ legacyContext }}</p>
      <template v-if="!resolved">
        <textarea v-model="answer" rows="2" placeholder="输入补充信息…" @keydown.enter.exact.prevent="submitAnswer"></textarea>
        <div class="actions">
          <button class="primary" type="button" :disabled="!answer.trim()" @click="submitAnswer">
            提交回复 <AppIcon name="arrowRight" :size="13" />
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

.field {
  margin-top: 10px;
}

.field label {
  display: block;
  margin-bottom: 4px;
  font-size: 11.5px;
  color: var(--text-2);
}

.field label em {
  font-style: normal;
  color: #ff8f8f;
}

.field-desc {
  margin: 0 0 5px;
  font-size: 10.5px;
  color: var(--text-3);
}

textarea,
input,
select {
  width: 100%;
  margin-top: 6px;
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
input:focus,
select:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 2px rgba(79, 140, 255, 0.15);
}

.field .chips,
.choice-block {
  margin-top: 6px;
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 6px 11px;
  font-size: 11.5px;
  color: var(--text-2);
  border: 1px solid var(--line-strong);
  border-radius: 999px;
  background: rgba(6, 13, 28, 0.6);
  transition: all 0.16s;
}

.chip:hover:not(:disabled) {
  color: var(--text-1);
  border-color: var(--accent);
}

.chip.on {
  color: #fff;
  border-color: transparent;
  background: linear-gradient(120deg, #3b78ff, #2a5cd6);
  box-shadow: 0 2px 12px rgba(47, 108, 255, 0.35);
}

.chip.rec {
  border-color: rgba(255, 182, 72, 0.45);
}

.chip em {
  font-style: normal;
  font-size: 9.5px;
  color: var(--amber);
}

.chip.on em {
  color: #ffe0a3;
}

.chip:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.form-error {
  margin: 8px 0 0;
  font-size: 11px;
  color: #ff8f8f;
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
  flex-wrap: wrap;
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

.ghost-btn {
  color: var(--text-2);
  border: 1px solid var(--line-strong) !important;
  background: transparent;
}

.ghost-btn:hover {
  color: var(--text-1);
  border-color: var(--accent) !important;
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
