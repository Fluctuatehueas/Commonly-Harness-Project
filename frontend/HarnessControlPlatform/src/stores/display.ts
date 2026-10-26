// 对话控制：消息类型可见性设置（持久化到 localStorage）
import { defineStore } from 'pinia'
import { ref, watch } from 'vue'

const STORAGE_KEY = 'harn…eplay'

interface DisplayPrefs {
  showToolCalls: boolean // 工具调用卡片
  showSkills: boolean // 技能使用卡片
  showDelegation: boolean // 子 Agent 委派卡片
  showReasoning: boolean // 推理/思考状态提示
  showAsyncTasks: boolean // 异步任务状态
  expandToolArgs: boolean // 工具参数默认展开
  typewriter: boolean // 流式打字机效果
}

const DEFAULTS: DisplayPrefs = {
  showToolCalls: true,
  showSkills: true,
  showDelegation: true,
  showReasoning: false,
  showAsyncTasks: true,
  expandToolArgs: false,
  typewriter: true,
}

function load(): DisplayPrefs {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? { ...DEFAULTS, ...JSON.parse(raw) } : { ...DEFAULTS }
  } catch {
    return { ...DEFAULTS }
  }
}

export const useDisplayStore = defineStore('display', () => {
  const prefs = ref<DisplayPrefs>(load())

  watch(
    prefs,
    (value) => localStorage.setItem(STORAGE_KEY, JSON.stringify(value)),
    { deep: true },
  )

  function reset() {
    prefs.value = { ...DEFAULTS }
  }

  return { prefs, reset }
})
