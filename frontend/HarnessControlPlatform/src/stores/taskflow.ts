// 任务流：列表分页 / 当前详情 / 保存发布复制 / 执行记录
import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  createTaskFlow,
  deleteTaskFlow,
  fetchExecution,
  fetchFlowExecutions,
  fetchTaskFlow,
  fetchTaskFlows,
  forkTaskFlow,
  publishTaskFlow,
  setTaskFlowEnabled,
  updateTaskFlow,
  type ExecutionDetail,
  type ExecutionItem,
  type FlowDetail,
  type FlowListItem,
  type ForkResult,
  type SaveResult,
  type TaskFlowSchema,
} from '@/api/taskflow'

export const useTaskflowStore = defineStore('taskflow', () => {
  const PAGE_SIZE = 12

  // ---------- 列表 ----------
  const items = ref<FlowListItem[]>([])
  const total = ref(0)
  const page = ref(1)
  const listLoading = ref(false)
  const listError = ref('')

  let listSeq = 0

  async function loadList(p = page.value) {
    const seq = ++listSeq
    listLoading.value = true
    listError.value = ''
    try {
      const data = await fetchTaskFlows(p, PAGE_SIZE)
      if (seq !== listSeq) return
      items.value = data.items
      total.value = data.total
      page.value = data.page
    } catch (err) {
      if (seq !== listSeq) return
      listError.value = (err as Error).message
    } finally {
      if (seq === listSeq) listLoading.value = false
    }
  }

  // ---------- 当前详情 ----------
  const current = ref<FlowDetail | null>(null)
  const detailLoading = ref(false)
  const detailError = ref('')

  async function loadDetail(id: string) {
    detailLoading.value = true
    detailError.value = ''
    try {
      current.value = await fetchTaskFlow(id)
    } catch (err) {
      current.value = null
      detailError.value = (err as Error).message
    } finally {
      detailLoading.value = false
    }
  }

  function save(payload: TaskFlowSchema, id?: string): Promise<SaveResult> {
    return id ? updateTaskFlow(id, payload) : createTaskFlow(payload)
  }

  async function publish(id: string): Promise<SaveResult> {
    const res = await publishTaskFlow(id)
    if (current.value?.id === id) current.value.status = res.status
    return res
  }

  function fork(id: string): Promise<ForkResult> {
    return forkTaskFlow(id)
  }

  /** 启用/停用：同步更新列表与详情里的状态，避免两端显示不一致。 */
  async function setEnabled(id: string, enabled: boolean): Promise<void> {
    const res = await setTaskFlowEnabled(id, enabled)
    const item = items.value.find((flow) => flow.id === id)
    if (item) item.enabled = res.enabled
    if (current.value?.id === id) current.value.enabled = res.enabled
  }

  async function remove(id: string): Promise<void> {
    await deleteTaskFlow(id)
    if (current.value?.id === id) current.value = null
  }

  // ---------- 执行记录 ----------
  const executions = ref<ExecutionItem[]>([])
  const executionsTotal = ref(0)
  const executionsLoading = ref(false)

  async function loadExecutions(flowId: string, p = 1) {
    executionsLoading.value = true
    try {
      const data = await fetchFlowExecutions(flowId, p)
      executions.value = data.items
      executionsTotal.value = data.total
    } finally {
      executionsLoading.value = false
    }
  }

  function loadExecution(executionId: string): Promise<ExecutionDetail> {
    return fetchExecution(executionId)
  }

  return {
    items,
    total,
    page,
    listLoading,
    listError,
    loadList,
    current,
    detailLoading,
    detailError,
    loadDetail,
    save,
    publish,
    fork,
    setEnabled,
    remove,
    executions,
    executionsTotal,
    executionsLoading,
    loadExecutions,
    loadExecution,
  }
})
