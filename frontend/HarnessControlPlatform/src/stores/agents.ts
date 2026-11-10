// MyAgent 列表：对话页与 MyAgent 管理页共享。
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import {
  createAgent,
  deleteAgent,
  fetchAgents,
  updateAgent,
  type AgentPayload,
  type AgentProfile,
} from '@/api/agents'

export const useAgentStore = defineStore('agents', () => {
  const items = ref<AgentProfile[]>([])
  const defaultAgentId = ref('default')
  const loading = ref(false)
  const saving = ref(false)
  const error = ref('')

  const options = computed(() =>
    items.value
      .filter((item) => item.enabled)
      .map((item) => ({ value: item.id, label: item.name, role: item.role_title })),
  )

  async function load(force = false) {
    if (loading.value) return
    if (!force && items.value.length) return
    loading.value = true
    try {
      const data = await fetchAgents()
      items.value = data.items
      defaultAgentId.value = data.default_agent_id || 'default'
      error.value = ''
    } catch (err) {
      error.value = (err as Error).message
    } finally {
      loading.value = false
    }
  }

  async function recruit(payload: AgentPayload): Promise<AgentProfile> {
    saving.value = true
    try {
      const profile = await createAgent(payload)
      items.value = [...items.value, profile]
      return profile
    } finally {
      saving.value = false
    }
  }

  async function save(
    agentId: string,
    payload: Partial<AgentPayload>,
  ): Promise<AgentProfile> {
    saving.value = true
    try {
      const profile = await updateAgent(agentId, payload)
      items.value = items.value.map((item) => (item.id === profile.id ? profile : item))
      return profile
    } finally {
      saving.value = false
    }
  }

  async function remove(agentId: string): Promise<void> {
    saving.value = true
    try {
      await deleteAgent(agentId)
      items.value = items.value.filter((item) => item.id !== agentId)
    } finally {
      saving.value = false
    }
  }

  function byId(agentId: string): AgentProfile | undefined {
    return items.value.find((item) => item.id === agentId)
  }

  return {
    items,
    defaultAgentId,
    loading,
    saving,
    error,
    options,
    load,
    recruit,
    save,
    remove,
    byId,
  }
})
