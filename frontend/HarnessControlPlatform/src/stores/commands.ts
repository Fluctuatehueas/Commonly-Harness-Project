// 命令系统状态：模式开关、目录缓存、候选资源、命令历史与本轮绑定。

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { API_BASE } from '@/api/client'
import { fetchAgents } from '@/api/agents'
import { fetchAgentGroups } from '@/api/agentGroups'
import { fetchSkills } from '@/api/skills'
import { fetchMcpServers } from '@/api/mcp'
import { fetchPlugins } from '@/api/plugins'
import { fetchTaskFlows } from '@/api/taskflow'
import {
  emptyBinding,
  type CommandCandidate,
  type CommandCatalog,
  type CommandResultPayload,
  type CommandSpec,
  type TurnBinding,
} from '@/types/commands'

const MODE_KEY = 'harness.commandMode'
const HISTORY_KEY = 'harness.commandHistory'
const BINDING_PREFIX = 'harness.binding.'
const CANDIDATE_TTL = 30_000

const FALLBACK_COMMANDS: CommandSpec[] = [
  ['help', '/help [command]', '显示命令列表或单条用法', 'query', 'backend', ''],
  ['status', '/status', '查看后端、LLM、沙箱与会话用量', 'query', 'backend', ''],
  ['token', '/token', '查看当前会话 Token 与上下文占用', 'query', 'backend', ''],
  ['rag', '/rag', '查看知识库 Provider 连接状态', 'query', 'backend', ''],
  ['delete', '/delete [-f]', '删除当前会话', 'session', 'frontend', ''],
  ['withdraw', '/withdraw', '撤回最新一条用户消息', 'session', 'frontend', ''],
  ['retry', '/retry', '重试最后一条最终回答', 'session', 'frontend', ''],
  ['stop', '/stop', '中止当前流式生成', 'session', 'frontend', ''],
  ['new', '/new', '新建会话', 'session', 'frontend', ''],
  ['rename', '/rename <title>', '重命名当前会话', 'session', 'frontend', ''],
  ['clear', '/clear', '清空当前页面消息视图', 'session', 'frontend', ''],
  ['copy', '/copy', '复制最后一条助手回答', 'basic', 'frontend', ''],
  ['export', '/export [md|json]', '导出当前会话', 'basic', 'frontend', ''],
  ['history', '/history [n]', '查看最近命令历史', 'basic', 'frontend', ''],
  ['pin', '/pin', '置顶当前会话', 'session', 'frontend', ''],
  ['unpin', '/unpin', '取消置顶当前会话', 'session', 'frontend', ''],
  ['archive', '/archive', '归档当前会话', 'session', 'frontend', ''],
  ['think', '/think [on|off]', '切换思考模式', 'basic', 'frontend', ''],
  ['web', '/web [on|off]', '切换联网搜索', 'basic', 'frontend', ''],
  ['agent', '/agent [name] [-team] [team]', '绑定下一轮 Agent 与组', 'resource', 'frontend', 'agents'],
  ['skills', '/skills [name|off|list]', '绑定下一轮技能', 'resource', 'frontend', 'skills'],
  ['mcp', '/mcp [name|off|list]', '绑定下一轮 MCP', 'resource', 'frontend', 'mcp'],
  ['plugin', '/plugin [name|off|list]', '绑定下一轮插件', 'resource', 'frontend', 'plugins'],
  ['taskflow', '/taskflow [name|off|list]', '绑定下一轮任务流', 'resource', 'frontend', 'taskflows'],
  ['binding', '/binding', '查看本轮资源绑定', 'resource', 'frontend', ''],
  ['skill', '/skill get <url> [IsUse]', '下载技能或预览内容', 'install', 'frontend', ''],
].map(([name, usage, summary, category, executor, candidates]) => ({
  name: name!,
  aliases: [],
  usage: usage!,
  summary: summary!,
  category: category!,
  executor: executor!,
  risk: 'low',
  requires_confirm: false,
  candidates: candidates!,
  flags: [],
  examples: [],
  visible: true,
}))

function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key)
    return raw ? (JSON.parse(raw) as T) : fallback
  } catch {
    return fallback
  }
}

export const useCommandsStore = defineStore('commands', () => {
  const modeEnabled = ref(localStorage.getItem(MODE_KEY) !== 'false')
  const catalog = ref<CommandSpec[]>([])
  const catalogAt = ref(0)
  const loading = ref(false)
  const history = ref<string[]>(readJson<string[]>(HISTORY_KEY, []).slice(0, 20))
  const bindings = ref<Record<string, TurnBinding>>({})
  const candidates = ref<Record<string, { items: CommandCandidate[]; at: number }>>({})
  // 全息终端：只保留最新一条命令结果
  const terminalResult = ref<CommandResultPayload | null>(null)
  const terminalVisible = ref(false)
  const terminalSeq = ref(0)

  const commandNames = computed(() => new Set(catalog.value.map((item) => item.name)))

  function setMode(value: boolean) {
    modeEnabled.value = value
    localStorage.setItem(MODE_KEY, String(value))
  }

  async function loadCatalog(force = false): Promise<void> {
    if (!force && catalog.value.length && Date.now() - catalogAt.value < 300_000) {
      return
    }
    loading.value = true
    try {
      const res = await fetch(`${API_BASE}/commands/catalog`, {
        cache: 'no-store',
        signal: AbortSignal.timeout(8000),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = (await res.json()) as CommandCatalog
      catalog.value = (data.commands || []).filter((item) => item.visible !== false)
      catalogAt.value = Date.now()
    } catch {
      // 后端不可达时使用本地目录兜底，命令模式仍可展示。
      if (!catalog.value.length) catalog.value = FALLBACK_COMMANDS
    } finally {
      loading.value = false
    }
  }

  function pushHistory(raw: string) {
    const value = raw.trim()
    if (!value) return
    history.value = [value, ...history.value.filter((item) => item !== value)].slice(0, 20)
    localStorage.setItem(HISTORY_KEY, JSON.stringify(history.value))
  }

  /** 展示命令结果；同一时刻只保留一个终端。 */
  function showTerminal(payload: CommandResultPayload) {
    terminalResult.value = payload
    terminalVisible.value = true
    terminalSeq.value += 1
  }

  function closeTerminal() {
    terminalVisible.value = false
  }

  function bindingFor(threadId: string): TurnBinding {
    if (!threadId) return emptyBinding()
    if (!bindings.value[threadId]) {
      const stored = readJson<TurnBinding | null>(`${BINDING_PREFIX}${threadId}`, null)
      bindings.value[threadId] = stored ? { ...emptyBinding(), ...stored } : emptyBinding()
    }
    return bindings.value[threadId]!
  }

  function patchBinding(threadId: string, patch: Partial<TurnBinding>) {
    if (!threadId) return
    const next = { ...bindingFor(threadId), ...patch }
    bindings.value[threadId] = next
    localStorage.setItem(`${BINDING_PREFIX}${threadId}`, JSON.stringify(next))
  }

  function clearBinding(threadId: string) {
    if (!threadId) return
    bindings.value[threadId] = emptyBinding()
    localStorage.removeItem(`${BINDING_PREFIX}${threadId}`)
  }

  function hasBinding(threadId: string): boolean {
    const binding = bindingFor(threadId)
    return Boolean(
      binding.agentId ||
        binding.groupId ||
        binding.flowId ||
        binding.skills.length ||
        binding.mcp_ids.length ||
        binding.plugin_ids.length,
    )
  }

  function requestBinding(threadId: string):
    | { skills: string[]; mcp_ids: string[]; plugin_ids: string[]; flow_id?: string }
    | undefined {
    const binding = bindingFor(threadId)
    const hasResources =
      binding.skills.length || binding.mcp_ids.length || binding.plugin_ids.length
    if (!hasResources && !binding.flowId) return undefined
    return {
      skills: binding.skills,
      mcp_ids: binding.mcp_ids,
      plugin_ids: binding.plugin_ids,
      ...(binding.flowId ? { flow_id: binding.flowId } : {}),
    }
  }

  async function loadCandidates(kind: string, force = false): Promise<CommandCandidate[]> {
    const cached = candidates.value[kind]
    if (!force && cached && Date.now() - cached.at < CANDIDATE_TTL) return cached.items
    let items: CommandCandidate[] = []
    try {
      if (kind === 'agents') {
        const [agents, groups] = await Promise.all([
          fetchAgents(),
          fetchAgentGroups().catch(() => ({ items: [] })),
        ])
        items = agents.items.map((item) => ({
          value: item.id,
          label: item.name || item.id,
          description: item.role_title || item.description,
          enabled: item.enabled,
        }))
        for (const group of groups.items || []) {
          items.push({
            value: group.group_id,
            label: `组：${group.name}`,
            description: '配合 /agent <name> -team 使用',
            status: 'group',
          })
        }
      } else if (kind === 'skills') {
        const data = await fetchSkills({ page: 1, page_size: 100 })
        items = data.items.map((item) => ({
          value: item.name,
          label: item.title || item.name,
          description: item.target,
          status: item.source,
          enabled: item.enabled,
        }))
      } else if (kind === 'mcp') {
        const data = await fetchMcpServers({ page: 1, page_size: 100, enabled: true })
        items = data.items.map((item) => ({
          value: item.server_id,
          label: item.server_id,
          description: item.description,
          status: item.state,
          enabled: item.enabled,
        }))
      } else if (kind === 'plugins') {
        const data = await fetchPlugins()
        items = data.items.map((item) => ({
          value: item.plugin_id,
          label: item.name || item.plugin_id,
          description: `v${item.version}`,
          status: item.source,
          enabled: item.enabled,
        }))
      } else if (kind === 'taskflows') {
        const data = await fetchTaskFlows(1, 100)
        items = data.items.map((item) => ({
          value: item.id,
          label: item.name || item.id,
          description: item.source,
          status: item.status,
        }))
      }
    } catch {
      items = []
    }
    candidates.value[kind] = { items, at: Date.now() }
    return items
  }

  return {
    modeEnabled,
    catalog,
    loading,
    history,
    bindings,
    commandNames,
    terminalResult,
    terminalVisible,
    terminalSeq,
    setMode,
    loadCatalog,
    pushHistory,
    showTerminal,
    closeTerminal,
    bindingFor,
    patchBinding,
    clearBinding,
    hasBinding,
    requestBinding,
    loadCandidates,
  }
})
