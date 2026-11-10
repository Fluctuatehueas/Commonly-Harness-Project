// 任务流接口：列表 / CRUD / 发布 / 复制内置流 / 执行记录
// 校验失败时后端返回 422，detail 为 FlowIssue[]，统一转成 FlowValidationError 抛出。
import { API_BASE } from './client'

export type FlowSource = 'builtin' | 'user'
export type FlowStatus = 'draft' | 'published'
export type StepSkills = 'auto' | string[]

export interface SubflowDef {
  name: string
  steps: StepDef[]
}

export interface StepDef {
  id: string
  name: string
  instruction: string
  /** "auto"（Agent 自行选择）或技能名数组 */
  skills: StepSkills
  /** main | auto | subagent:<角色名> */
  executor: string
  constraints: string
  checkpoint: boolean
  example?: string
  parallel?: SubflowDef[]
}

export interface TaskFlowSchema {
  schema_version: string
  id?: string
  name: string
  description: string
  source?: FlowSource
  status?: FlowStatus
  trigger_examples: string[]
  mcp_services: string[]
  steps: StepDef[]
}

export interface FlowSearchDoc {
  name?: string
  description?: string
  trigger_examples?: string[]
  step_summary?: string
}

export interface FlowListItem {
  id: string
  name: string
  status: FlowStatus
  source: FlowSource
  schema_hash: string | null
  search_doc: FlowSearchDoc | null
  updated_at: string
}

export interface FlowList {
  items: FlowListItem[]
  total: number
  page: number
  size: number
}

export interface FlowDetail extends FlowListItem {
  user_id: string
  schema: TaskFlowSchema
  plan: Record<string, unknown> | null
  created_at: string
}

export interface FlowIssue {
  pass_name: string
  step_id: string | null
  level: 'error' | 'warning'
  message: string
}

/** 编译期校验失败：携带全部 FlowIssue，前端按 step_id 就地标注。 */
export class FlowValidationError extends Error {
  issues: FlowIssue[]

  constructor(issues: FlowIssue[]) {
    super(issues.map((issue) => issue.message).join('；') || '任务流校验失败')
    this.name = 'FlowValidationError'
    this.issues = issues
  }
}

export interface SaveResult {
  id: string
  status: FlowStatus
  schema_hash: string
}

export interface ForkResult {
  id: string
  status: FlowStatus
  forked_from: string
}

export type StepTraceStatus = 'done' | 'failed' | 'degraded' | 'skipped'
export type ExecutionStatus = 'running' | 'done' | 'failed' | 'aborted'

export interface StepTrace {
  seq: number
  step_id: string
  branch: string | null
  status: StepTraceStatus
  attempts: number
  input_summary: string
  output_summary: string
  /** 完整步骤产出（执行记录回放用），output_summary 只是前 200 字 */
  output?: string
  duration_ms: number
  check_result: string | null
  state_source: 'step' | 'user_provided'
}

export interface ExecutionItem {
  id: string
  flow_id: string
  thread_id: string
  status: ExecutionStatus
  entry_step: string | null
  trace_id: string | null
  created_at: string
  finished_at: string | null
}

export interface ExecutionDetail extends ExecutionItem {
  user_id: string
  schema_hash: string
  step_traces: StepTrace[]
}

export interface ExecutionList {
  items: ExecutionItem[]
  total: number
  page: number
  size: number
}

/** 执行卡片视图模型（Chat 集成时由 SSE 事件或 Execution 记录映射）。 */
export interface ExecutionStepView {
  id: string
  name: string
  status: 'pending' | 'running' | 'done' | 'failed' | 'degraded'
  /** fork 分支名；主流步骤为空 */
  branch?: string | null
  /** 运行中实时累积的步骤产出 */
  output?: string
}

async function readError(res: Response, fallback: string): Promise<Error> {
  const data = (await res.json().catch(() => ({}))) as { detail?: unknown }
  const detail = data.detail
  if (Array.isArray(detail)) {
    const issues = detail.filter(
      (item): item is FlowIssue =>
        !!item && typeof item === 'object' && typeof (item as FlowIssue).message === 'string',
    )
    if (issues.length) return new FlowValidationError(issues)
  }
  if (typeof detail === 'string' && detail) return new Error(detail)
  if (detail && typeof detail === 'object') {
    const value = detail as { message?: string }
    if (value.message) return new Error(value.message)
  }
  return new Error(`${fallback}: HTTP ${res.status}`)
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { cache: 'no-store', ...init })
  if (!res.ok) throw await readError(res, `请求失败 ${path}`)
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

function jsonInit(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }
}

export function fetchTaskFlows(page = 1, size = 12): Promise<FlowList> {
  return request(`/task-flows?page=${page}&size=${size}`)
}

export function fetchTaskFlow(flowId: string): Promise<FlowDetail> {
  return request(`/task-flows/${encodeURIComponent(flowId)}`)
}

export function createTaskFlow(payload: TaskFlowSchema): Promise<SaveResult> {
  return request('/task-flows', jsonInit('POST', { payload }))
}

export function updateTaskFlow(flowId: string, payload: TaskFlowSchema): Promise<SaveResult> {
  return request(`/task-flows/${encodeURIComponent(flowId)}`, jsonInit('PUT', { payload }))
}

export function deleteTaskFlow(flowId: string): Promise<void> {
  return request(`/task-flows/${encodeURIComponent(flowId)}`, { method: 'DELETE' })
}

export function publishTaskFlow(flowId: string): Promise<SaveResult> {
  return request(`/task-flows/${encodeURIComponent(flowId)}/publish`, { method: 'POST' })
}

export function forkTaskFlow(flowId: string): Promise<ForkResult> {
  return request(`/task-flows/${encodeURIComponent(flowId)}/fork`, { method: 'POST' })
}

export function fetchFlowExecutions(flowId: string, page = 1, size = 20): Promise<ExecutionList> {
  return request(`/task-flows/${encodeURIComponent(flowId)}/executions?page=${page}&size=${size}`)
}

export function fetchExecution(executionId: string): Promise<ExecutionDetail> {
  return request(`/task-flows/executions/${encodeURIComponent(executionId)}`)
}
