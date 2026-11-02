// 后端运行参数（思考模式 / 联网搜索）：与 /settings/model 双向同步
import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  fetchModelSettings,
  updateModelSettings,
  type ModelSettingsPatch,
} from '@/api/settings'

export const useSettingsStore = defineStore('settings', () => {
  const thinking = ref(false)
  const webSearch = ref(true)
  // null=未探测；false=后端不可达（开关只做本地展示，不写后端）
  const online = ref<boolean | null>(null)
  const loading = ref(false)
  const saving = ref(false)
  const error = ref('')

  async function load() {
    loading.value = true
    try {
      const data = await fetchModelSettings()
      thinking.value = Boolean(data.thinking_enabled)
      webSearch.value = Boolean(data.web_search_enabled)
      online.value = true
      error.value = ''
    } catch {
      online.value = false
    } finally {
      loading.value = false
    }
  }

  async function patch(values: ModelSettingsPatch) {
    // 先落本地值，保证开关手感即时；失败再回滚。
    const previous = { thinking: thinking.value, webSearch: webSearch.value }
    if (values.thinking_enabled !== undefined) thinking.value = values.thinking_enabled
    if (values.web_search_enabled !== undefined) webSearch.value = values.web_search_enabled
    if (online.value === false) return
    saving.value = true
    try {
      const data = await updateModelSettings(values)
      thinking.value = Boolean(data.thinking_enabled)
      webSearch.value = Boolean(data.web_search_enabled)
      online.value = true
      error.value = ''
    } catch (err) {
      thinking.value = previous.thinking
      webSearch.value = previous.webSearch
      error.value = (err as Error).message
    } finally {
      saving.value = false
    }
  }

  const setThinking = (value: boolean) => patch({ thinking_enabled: value })
  const setWebSearch = (value: boolean) => patch({ web_search_enabled: value })

  return {
    thinking,
    webSearch,
    online,
    loading,
    saving,
    error,
    load,
    setThinking,
    setWebSearch,
  }
})
