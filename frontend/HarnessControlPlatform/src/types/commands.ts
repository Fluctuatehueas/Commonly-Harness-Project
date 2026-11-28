// 命令系统：目录、解析结果、回执与绑定

export type CommandCategory = 'query' | 'session' | 'resource' | 'install' | 'basic'
export type CommandExecutor = 'frontend' | 'backend'
export type CommandRisk = 'low' | 'medium' | 'high'

export interface CommandSpec {
  name: string
  aliases: string[]
  usage: string
  summary: string
  category: CommandCategory | string
  executor: CommandExecutor | string
  risk: CommandRisk | string
  requires_confirm: boolean
  candidates: string
  flags: string[]
  examples: string[]
  visible: boolean
}

export interface CommandCatalog {
  version: number
  commands: CommandSpec[]
}

export interface ParsedCommand {
  raw: string
  name: string
  args: string[]
  flags: Record<string, string | boolean>
  start: number
  end: number
}

export interface ParsedInput {
  commands: ParsedCommand[]
  rest: string
  error?: string
}

export interface CommandResultPayload {
  ok: boolean
  command: string
  kind: 'text' | 'table' | 'list'
  title: string
  text: string
  columns: string[]
  rows: (string | number | boolean | null)[][]
  warnings: string[]
  meta: Record<string, unknown>
}

/** 输入框内一根命令芯片对应的原始文本。 */
export interface CommandToken {
  id: string
  raw: string
  name: string
}

/** 本轮绑定：只随下一次发送生效。 */
export interface TurnBinding {
  agentId: string
  agentName: string
  groupId: string
  groupName: string
  skills: string[]
  mcp_ids: string[]
  plugin_ids: string[]
  flowId: string
  flowName: string
}

export function emptyBinding(): TurnBinding {
  return {
    agentId: '',
    agentName: '',
    groupId: '',
    groupName: '',
    skills: [],
    mcp_ids: [],
    plugin_ids: [],
    flowId: '',
    flowName: '',
  }
}

export interface CommandCandidate {
  value: string
  label: string
  description?: string
  status?: string
  enabled?: boolean
}

export interface CommandCandidateGroup {
  kind: string
  items: CommandCandidate[]
  loadedAt: number
}
