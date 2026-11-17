<script setup lang="ts">
// Agent 模型配置抽屉：编辑后点「应用」，真正落库仍由主表单的保存完成。
import { computed, reactive, watch } from 'vue'
import { Check } from '@element-plus/icons-vue'
import HoloPanel from '@/components/console/HoloPanel.vue'
import SegmentedSwitch from '@/components/console/SegmentedSwitch.vue'
import { MODEL_CATALOG, formatContext } from '@/stores/models'

const MODEL_MODE_OPTIONS: Array<{ value: boolean; label: string }> = [
  { value: false, label: '沿用全局' },
  { value: true, label: '独立配置' },
]

const props = defineProps<{
  modelValue: boolean
  modelName: string
  baseUrl: string
  hasApiKey: boolean
  thinkingEnabled: boolean
  contextLimit: number
  /** 系统设置里的全局模型；「沿用全局」时只读展示。 */
  globalModel: {
    model_name: string
    base_url: string
    has_api_key: boolean
    context_limit: number
  } | null
}>()

const emit = defineEmits<{
  'update:modelValue': [visible: boolean]
  apply: [
    payload: {
      useCustom: boolean
      modelName: string
      baseUrl: string
      apiKey: string
      thinkingEnabled: boolean
      contextLimit: number
    },
  ]
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

const draft = reactive({
  useCustom: false,
  modelName: '',
  baseUrl: '',
  apiKey: '',
  thinkingEnabled: false,
  contextLimit: 128000,
})

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    draft.useCustom = Boolean(props.modelName || props.baseUrl)
    draft.modelName = props.modelName
    draft.baseUrl = props.baseUrl
    draft.apiKey = ''
    draft.thinkingEnabled = props.thinkingEnabled
    draft.contextLimit = props.contextLimit || 128000
  },
  { immediate: true },
)

function onPickModel(modelId: string) {
  const spec = MODEL_CATALOG.find((item) => item.id === modelId)
  if (!spec) return
  draft.modelName = spec.id
  draft.baseUrl = spec.baseUrl
  draft.contextLimit = spec.defaultContext
  draft.useCustom = true
}

function apply() {
  emit('apply', {
    useCustom: draft.useCustom,
    modelName: draft.modelName,
    baseUrl: draft.baseUrl,
    apiKey: draft.apiKey,
    thinkingEnabled: draft.thinkingEnabled,
    contextLimit: draft.contextLimit,
  })
  visible.value = false
}
</script>

<template>
  <HoloPanel
    v-model="visible"
    title="模型配置"
    kicker="AGENT DOCK // MODEL"
    subtitle="「沿用全局」直接使用系统设置里的全局模型；「独立配置」只覆盖该 Agent。"
  >
    <div class="drawer-body">
      <SegmentedSwitch
        v-model="draft.useCustom"
        :options="MODEL_MODE_OPTIONS"
        block
        class="model-mode"
      />

      <template v-if="draft.useCustom">
        <label>
          <span>模型</span>
          <el-select
            :model-value="draft.modelName"
            filterable
            placeholder="选择模型"
            @update:model-value="onPickModel"
          >
            <el-option
              v-for="item in MODEL_CATALOG"
              :key="item.id"
              :label="`${item.name} · ${item.provider}`"
              :value="item.id"
            />
          </el-select>
        </label>
        <label>
          <span>Base URL</span>
          <el-input v-model="draft.baseUrl" placeholder="https://api.deepseek.com" />
        </label>
        <label>
          <span>API Key</span>
          <el-input
            v-model="draft.apiKey"
            type="password"
            show-password
            autocomplete="off"
            :placeholder="hasApiKey ? '留空保持不变' : '未配置'"
          />
        </label>
        <div class="inline-field">
          <span>上下文窗口</span>
          <el-select v-model="draft.contextLimit">
            <el-option :value="128000" label="128K" />
            <el-option :value="200000" label="200K" />
            <el-option :value="256000" label="256K" />
            <el-option :value="1000000" label="1M" />
          </el-select>
        </div>
        <div class="inline-field">
          <span>思考能力</span>
          <el-switch v-model="draft.thinkingEnabled" />
        </div>
        <p class="drawer-hint">
          当前窗口 {{ formatContext(draft.contextLimit) }}，用于压缩阈值与占用展示。
        </p>
      </template>

      <div v-else class="global-model">
        <div class="global-row">
          <span>来源</span>
          <strong>系统设置 · 全局模型（.env）</strong>
        </div>
        <div class="global-row">
          <span>模型</span>
          <strong>{{ globalModel?.model_name || '未配置' }}</strong>
        </div>
        <div class="global-row">
          <span>Base URL</span>
          <span>{{ globalModel?.base_url || '未配置' }}</span>
        </div>
        <div class="global-row">
          <span>API Key</span>
          <span>{{ globalModel?.has_api_key ? '已配置' : '未配置' }}</span>
        </div>
        <div class="global-row">
          <span>上下文窗口</span>
          <span>{{ formatContext(globalModel?.context_limit || 128000) }}</span>
        </div>
        <p class="drawer-hint">
          该 Agent 未单独配置模型，直接使用全局模型；需要覆盖时切到「独立配置」。
        </p>
      </div>
    </div>

    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :icon="Check" @click="apply">应用</el-button>
    </template>
  </HoloPanel>
</template>

<style scoped>
.drawer-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.drawer-hint {
  margin: 0;
  font-size: 11px;
  line-height: 1.6;
  color: var(--text-3);
}

.model-mode {
  padding-bottom: 4px;
}

.global-model {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 12px 14px;
  border: 1px dashed var(--line-strong, rgba(126, 154, 198, 0.3));
  border-radius: var(--radius-md, 6px);
  background: var(--bg-elevated, #101a2c);
}

.global-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  font-size: 11.5px;
  color: var(--text-2);
}

.global-row strong {
  color: var(--text-1);
  font-weight: 600;
}

.global-row > span:last-child {
  color: var(--text-1);
  word-break: break-all;
  text-align: right;
}

label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 11.5px;
  color: var(--text-2);
}

.inline-field {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-size: 11.5px;
  color: var(--text-2);
}

.inline-field > span {
  flex: none;
  white-space: nowrap;
}

.inline-field .el-select {
  width: 160px;
}
</style>
