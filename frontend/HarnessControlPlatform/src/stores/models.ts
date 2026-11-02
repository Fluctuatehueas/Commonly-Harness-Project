// 模型管理：目录数据 + .env 配置读写（保存才落盘）
import { defineStore } from 'pinia'
import { computed, reactive, ref } from 'vue'
import { API_BASE } from '@/api/client'

export interface ModelSpec {
  id: string // 写入 DEEPSEEK_MODEL_NAME 的值
  name: string
  provider: string
  initial: string
  gradient: string
  /** 展示用的上下文标签 */
  context: string
  /** 可选上下文窗口（tokens）；前端用固定选项，避免手填和下拉滚动 */
  contextOptions: number[]
  /** 选中该模型时默认写入的上下文窗口 */
  defaultContext: number
  features: string[]
  baseUrl: string
  temperature: number
  note?: string
}

// 默认上下文窗口：Provider 大致按官方文档整理，可在配置面板里改。
const CTX_128K = 128000
const CTX_256K = 256000
const CTX_200K = 200000
const CTX_1M = 1000000

// 市面主流模型目录（base_url 均为 OpenAI 兼容端点；Anthropic 原生端点需网关转换）
export const MODEL_CATALOG: ModelSpec[] = [
  // ---------- DeepSeek ----------
  {
    id: 'deepseek-chat', name: 'DeepSeek V3 Chat', provider: 'DeepSeek', initial: 'D',
    gradient: 'linear-gradient(135deg, #4f8cff, #2456c8)', context: '128K 上下文',
    contextOptions: [CTX_128K], defaultContext: CTX_128K,
    features: ['函数调用', '中文优化'], baseUrl: 'https://api.deepseek.com', temperature: 0.7,
    note: 'V3 系列默认 128K 输入窗口',
  },
  {
    id: 'deepseek-reasoner', name: 'DeepSeek V3 Reasoner', provider: 'DeepSeek', initial: 'R',
    gradient: 'linear-gradient(135deg, #6a5cff, #3a2fb0)', context: '128K 上下文',
    contextOptions: [CTX_128K], defaultContext: CTX_128K,
    features: ['强推理', '思考过程'], baseUrl: 'https://api.deepseek.com', temperature: 0.6,
    note: 'V3 系列默认 128K 输入窗口',
  },
  {
    id: 'deepseek-v4-flash', name: 'DeepSeek V4 Flash', provider: 'DeepSeek', initial: '4',
    gradient: 'linear-gradient(135deg, #2f8bff, #1b4fd8)', context: '1M 上下文',
    contextOptions: [CTX_128K, CTX_256K, CTX_1M], defaultContext: CTX_1M,
    features: ['1M 窗口', '高性价比', '思考/函数调用'], baseUrl: 'https://api.deepseek.com', temperature: 0.7,
    note: 'V4 系列上下文窗口 1M；输入 token 上限仍受服务端配额约束',
  },
  {
    id: 'deepseek-v4-pro', name: 'DeepSeek V4 Pro', provider: 'DeepSeek', initial: 'P',
    gradient: 'linear-gradient(135deg, #3a6cff, #2038b8)', context: '1M 上下文',
    contextOptions: [CTX_128K, CTX_256K, CTX_1M], defaultContext: CTX_1M,
    features: ['1M 窗口', '强推理', '工具调用'], baseUrl: 'https://api.deepseek.com', temperature: 0.6,
    note: 'V4 系列上下文窗口 1M；输入 token 上限仍受服务端配额约束',
  },

  // ---------- OpenAI ----------
  {
    id: 'gpt-4o', name: 'GPT-4o', provider: 'OpenAI', initial: 'O',
    gradient: 'linear-gradient(135deg, #10a37f, #0d7a5f)', context: '128K 上下文',
    contextOptions: [CTX_128K], defaultContext: CTX_128K,
    features: ['多模态', '函数调用'], baseUrl: 'https://api.openai.com/v1', temperature: 0.7,
  },
  {
    id: 'gpt-4o-mini', name: 'GPT-4o mini', provider: 'OpenAI', initial: 'm',
    gradient: 'linear-gradient(135deg, #14b89a, #0a6b58)', context: '128K 上下文',
    contextOptions: [CTX_128K], defaultContext: CTX_128K,
    features: ['低价', '快速'], baseUrl: 'https://api.openai.com/v1', temperature: 0.7,
  },
  {
    id: 'gpt-4.1', name: 'GPT-4.1', provider: 'OpenAI', initial: '4',
    gradient: 'linear-gradient(135deg, #0ea5a4, #0b6e6d)', context: '1M 上下文',
    contextOptions: [CTX_128K, CTX_1M], defaultContext: CTX_1M,
    features: ['超长上下文', '函数调用'], baseUrl: 'https://api.openai.com/v1', temperature: 0.7,
  },
  {
    id: 'o3', name: 'OpenAI o3', provider: 'OpenAI', initial: '3',
    gradient: 'linear-gradient(135deg, #0f766e, #083e39)', context: '200K 上下文',
    contextOptions: [CTX_128K, CTX_200K], defaultContext: CTX_200K,
    features: ['深度推理', '工具调用'], baseUrl: 'https://api.openai.com/v1', temperature: 0.6,
  },

  // ---------- Anthropic ----------
  {
    id: 'claude-sonnet-4-5', name: 'Claude Sonnet 4.5', provider: 'Anthropic', initial: 'C',
    gradient: 'linear-gradient(135deg, #d97757, #a8543a)', context: '200K 上下文',
    contextOptions: [CTX_200K, CTX_1M], defaultContext: CTX_200K,
    features: ['长文本', '工具调用'], baseUrl: 'https://api.deepseek.com', temperature: 0.5,
    note: '需 OpenAI 兼容网关',
  },
  {
    id: 'claude-haiku-4-5', name: 'Claude Haiku 4.5', provider: 'Anthropic', initial: 'h',
    gradient: 'linear-gradient(135deg, #e0896a, #b56648)', context: '200K 上下文',
    contextOptions: [CTX_200K], defaultContext: CTX_200K,
    features: ['低延迟', '工具调用'], baseUrl: 'https://api.deepseek.com', temperature: 0.6,
    note: '需 OpenAI 兼容网关',
  },
  {
    id: 'claude-opus-4-5', name: 'Claude Opus 4.5', provider: 'Anthropic', initial: 'O',
    gradient: 'linear-gradient(135deg, #c66548, #8b3f2a)', context: '200K 上下文',
    contextOptions: [CTX_200K, CTX_1M], defaultContext: CTX_200K,
    features: ['最强推理', '长文本'], baseUrl: 'https://api.deepseek.com', temperature: 0.5,
    note: '需 OpenAI 兼容网关',
  },

  // ---------- Alibaba ----------
  {
    id: 'qwen3-max', name: 'Qwen3 Max', provider: 'Alibaba', initial: 'Q',
    gradient: 'linear-gradient(135deg, #615ced, #3c34b8)', context: '256K 上下文',
    contextOptions: [CTX_128K, CTX_256K], defaultContext: CTX_256K,
    features: ['中文优化', '多轮对话'], baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1', temperature: 0.7,
  },
  {
    id: 'qwen-plus', name: 'Qwen Plus', provider: 'Alibaba', initial: 'q',
    gradient: 'linear-gradient(135deg, #7c6cf0, #4a3cc8)', context: '128K 上下文',
    contextOptions: [CTX_128K], defaultContext: CTX_128K,
    features: ['高性价比', '函数调用'], baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1', temperature: 0.7,
  },
  {
    id: 'qwen3-coder', name: 'Qwen3 Coder', provider: 'Alibaba', initial: 'C',
    gradient: 'linear-gradient(135deg, #8a7cf5, #5b45cf)', context: '256K 上下文',
    contextOptions: [CTX_256K], defaultContext: CTX_256K,
    features: ['代码', 'Agent'], baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1', temperature: 0.6,
  },

  // ---------- Moonshot ----------
  {
    id: 'kimi-k2', name: 'Kimi K2', provider: 'Moonshot', initial: 'K',
    gradient: 'linear-gradient(135deg, #1f6feb, #0b3d91)', context: '256K 上下文',
    contextOptions: [CTX_128K, CTX_256K], defaultContext: CTX_256K,
    features: ['Agent 能力', '长上下文'], baseUrl: 'https://api.moonshot.cn/v1', temperature: 0.6,
  },

  // ---------- 智谱 AI ----------
  {
    id: 'glm-4.6', name: 'GLM-4.6', provider: '智谱 AI', initial: 'G',
    gradient: 'linear-gradient(135deg, #35c3f5, #1a7fc0)', context: '200K 上下文',
    contextOptions: [CTX_128K, CTX_200K], defaultContext: CTX_200K,
    features: ['推理', '工具调用'], baseUrl: 'https://open.bigmodel.cn/api/paas/v4', temperature: 0.7,
  },
  {
    id: 'glm-4.5-flash', name: 'GLM-4.5 Flash', provider: '智谱 AI', initial: 'F',
    gradient: 'linear-gradient(135deg, #4fd0f7, #2599cc)', context: '128K 上下文',
    contextOptions: [CTX_128K], defaultContext: CTX_128K,
    features: ['低价', '快速'], baseUrl: 'https://open.bigmodel.cn/api/paas/v4', temperature: 0.7,
  },

  // ---------- Google ----------
  {
    id: 'gemini-2.5-pro', name: 'Gemini 2.5 Pro', provider: 'Google', initial: 'G',
    gradient: 'linear-gradient(135deg, #4285f4, #8ab4f8)', context: '1M 上下文',
    contextOptions: [CTX_128K, CTX_1M], defaultContext: CTX_1M,
    features: ['多模态', '超长上下文'], baseUrl: 'https://generativelanguage.googleapis.com/v1beta/openai', temperature: 0.7,
  },
  {
    id: 'gemini-2.5-flash', name: 'Gemini 2.5 Flash', provider: 'Google', initial: 'f',
    gradient: 'linear-gradient(135deg, #5b9bf7, #a7c6fb)', context: '1M 上下文',
    contextOptions: [CTX_128K, CTX_1M], defaultContext: CTX_1M,
    features: ['低延迟', '多模态'], baseUrl: 'https://generativelanguage.googleapis.com/v1beta/openai', temperature: 0.7,
  },

  // ---------- 自托管 / 自定义 ----------
  {
    id: 'vllm-local', name: 'vLLM 本地', provider: 'Self-hosted', initial: 'V',
    gradient: 'linear-gradient(135deg, #8f7bff, #5a3fd0)', context: '按部署',
    contextOptions: [32000, CTX_128K, CTX_256K], defaultContext: CTX_128K,
    features: ['私有部署', 'OpenAI 兼容'], baseUrl: 'http://127.0.0.1:8000/v1', temperature: 0.7,
    note: '填写你的推理服务地址',
  },
  {
    id: 'custom', name: '自定义模型', provider: 'Custom', initial: '✦',
    gradient: 'linear-gradient(135deg, #9db0d6, #5f739c)', context: '手动填写',
    contextOptions: [CTX_128K, CTX_256K, CTX_1M], defaultContext: CTX_128K,
    features: ['任意 OpenAI 兼容端点'], baseUrl: '', temperature: 0.7,
  },
]

/** 把 token 数渲染成 128K / 1M 这类标签。 */
export function formatContext(tokens: number): string {
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(tokens % 1_000_000 ? 1 : 0)}M`
  if (tokens >= 1000) return `${Math.round(tokens / 1000)}K`
  return String(tokens)
}

interface ModelState {
  model_name: string
  base_url: string
  has_api_key: boolean
  thinking_enabled?: boolean
  context_limit?: number
}

export const useModelStore = defineStore('models', () => {
  const offline = ref(false) // 后端不可达：允许本地选择，但保存会失败
  const loaded = ref(false)
  const current = ref<ModelState>({ model_name: '', base_url: '', has_api_key: false })
  const selectedId = ref('')
  const form = reactive({
    model_name: '',
    base_url: '',
    api_key: '',
    thinking_enabled: false,
    context_limit: 128000,
  })
  const saving = ref(false)
  const toast = ref('')
  const toastType = ref<'ok' | 'err'>('ok')

  const selected = computed(() => MODEL_CATALOG.find((m) => m.id === selectedId.value) || null)
  // 按厂商分组展示：顺序固定，组内保持目录定义顺序。
  const groupedCatalog = computed(() => {
    const groups: { provider: string; models: ModelSpec[] }[] = []
    for (const spec of MODEL_CATALOG) {
      const group = groups.find((item) => item.provider === spec.provider)
      if (group) group.models.push(spec)
      else groups.push({ provider: spec.provider, models: [spec] })
    }
    return groups
  })
  const dirty = computed(
    () =>
      !!selectedId.value &&
      (form.model_name !== current.value.model_name ||
        form.base_url !== current.value.base_url ||
        form.thinking_enabled !== !!current.value.thinking_enabled ||
        form.context_limit !== (current.value.context_limit || 128000) ||
        form.api_key !== ''),
  )

  async function load() {
    try {
      const res = await fetch(`${API_BASE}/settings/model`)
      if (!res.ok) throw new Error(String(res.status))
      current.value = (await res.json()) as ModelState
      form.model_name = current.value.model_name
      form.base_url = current.value.base_url
      form.thinking_enabled = !!current.value.thinking_enabled
      form.context_limit = current.value.context_limit || 128000
      const match = MODEL_CATALOG.find((m) => m.id === current.value.model_name)
      selectedId.value = match ? match.id : 'custom'
      offline.value = false
    } catch {
      offline.value = true
      form.model_name = 'deepseek-chat'
      form.base_url = 'https://api.deepseek.com'
      form.thinking_enabled = false
      form.context_limit = 128000
      selectedId.value = 'deepseek-chat'
    } finally {
      loaded.value = true
    }
  }

  // 选择模型：回填参数；API Key 自动清空，需用户重新输入或保持原值
  function select(spec: ModelSpec) {
    selectedId.value = spec.id
    form.model_name = spec.id === 'custom' ? form.model_name : spec.id
    form.base_url = spec.baseUrl
    form.context_limit = spec.defaultContext
    form.api_key = ''
  }

  function flash(message: string, type: 'ok' | 'err' = 'ok') {
    toast.value = message
    toastType.value = type
    window.setTimeout(() => (toast.value === message ? (toast.value = '') : null), 3200)
  }

  // 保存：写 .env（api_key 留空则保留原值）并触发热更新
  async function save() {
    if (saving.value) return
    saving.value = true
    try {
      const res = await fetch(`${API_BASE}/settings/model`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model_name: form.model_name || undefined,
          base_url: form.base_url,
          api_key: form.api_key || undefined,
          thinking_enabled: form.thinking_enabled,
          context_limit: form.context_limit,
        }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error((data as { detail?: string }).detail || `HTTP ${res.status}`)
      current.value = (data as { current: ModelState }).current
      form.api_key = ''
      flash('已写入 .env 并热更新，下次对话生效')
    } catch (error) {
      flash(`保存失败：${(error as Error).message}`, 'err')
    } finally {
      saving.value = false
    }
  }

  return { offline, loaded, current, selectedId, form, selected, groupedCatalog, dirty, saving, toast, toastType, load, select, save, flash }
})
