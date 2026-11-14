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
import {
  addAgentGroupMember,
  clearAgentGroupLeader,
  createAgentGroup,
  deleteAgentGroup,
  fetchAgentGroups,
  removeAgentGroupMember,
  transferAgentGroupLeader,
  updateAgentGroup,
  type AgentGroup,
  type AgentGroupPayload,
} from '@/api/agentGroups'

export const useAgentStore = defineStore('agents', () => {
  const items = ref<AgentProfile[]>([])
  const defaultAgentId = ref('default')
  const loading = ref(false)
  const saving = ref(false)
  const error = ref('')
  const groups = ref<AgentGroup[]>([])
  const groupsLoading = ref(false)
  // 组列表是否至少成功加载过一次；用于区分「没有组」与「还没加载」。
  const groupsLoaded = ref(false)
  let groupsPromise: Promise<void> | null = null

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

  async function loadGroups(force = false): Promise<void> {
    // 并发调用共享同一个请求，避免抽屉/组页面同时打开时拿到空列表
    if (groupsPromise) return groupsPromise
    if (!force && groups.value.length) return
    groupsLoading.value = true
    groupsPromise = (async () => {
      try {
        const data = await fetchAgentGroups()
        groups.value = data.items
        groupsLoaded.value = true
      } catch (err) {
        error.value = (err as Error).message
      } finally {
        groupsLoading.value = false
        groupsPromise = null
      }
    })()
    return groupsPromise
  }

  function groupsOf(agentId: string): AgentGroup[] {
    return groups.value.filter((group) =>
      (group.members || []).some((member) => member.agent_id === agentId),
    )
  }

  function leaderGroupsOf(agentId: string): AgentGroup[] {
    return groups.value.filter((group) => group.leader_agent_id === agentId)
  }

  async function createGroup(payload: AgentGroupPayload): Promise<AgentGroup> {
    saving.value = true
    try {
      const group = await createAgentGroup(payload)
      await loadGroups(true)
      return group
    } finally {
      saving.value = false
    }
  }

  async function saveGroup(
    groupId: string,
    payload: Partial<AgentGroupPayload>,
  ): Promise<AgentGroup> {
    saving.value = true
    try {
      const group = await updateAgentGroup(groupId, payload)
      await loadGroups(true)
      return group
    } finally {
      saving.value = false
    }
  }

  async function removeGroup(groupId: string): Promise<void> {
    saving.value = true
    try {
      await deleteAgentGroup(groupId)
      await loadGroups(true)
    } finally {
      saving.value = false
    }
  }

  async function addGroupMember(groupId: string, agentId: string): Promise<void> {
    await addAgentGroupMember(groupId, agentId)
    await loadGroups(true)
  }

  async function removeGroupMember(groupId: string, agentId: string): Promise<void> {
    await removeAgentGroupMember(groupId, agentId)
    await loadGroups(true)
  }

  async function transferLeader(groupId: string, agentId: string): Promise<void> {
    await transferAgentGroupLeader(groupId, agentId)
    await loadGroups(true)
  }

  async function clearLeader(groupId: string): Promise<void> {
    await clearAgentGroupLeader(groupId)
    await loadGroups(true)
  }

  async function recruit(payload: AgentPayload): Promise<AgentProfile> {
    saving.value = true
    try {
      const profile = await createAgent(payload)
      items.value = [...items.value, profile]
      await loadGroups(true)
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
      await loadGroups(true)
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
      await loadGroups(true)
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
    groups,
    groupsLoading,
    groupsLoaded,
    options,
    load,
    loadGroups,
    groupsOf,
    leaderGroupsOf,
    createGroup,
    saveGroup,
    removeGroup,
    addGroupMember,
    removeGroupMember,
    transferLeader,
    clearLeader,
    recruit,
    save,
    remove,
    byId,
  }
})
