// 命令执行器：前端命令在这里落地，只读查询走后端 /commands/execute。

import { ElMessage, ElMessageBox } from 'element-plus'
import { API_BASE } from '@/api/client'
import {
  downloadSkillFromUrl,
  fetchSkillContent,
  fetchSkillJob,
} from '@/api/skills'
import { fetchPluginJob, startPluginInstall } from '@/api/plugins'
import { useAgentStore } from '@/stores/agents'
import { useChatStore } from '@/stores/chat'
import { useCommandsStore } from '@/stores/commands'
import { useSettingsStore } from '@/stores/settings'
import type {
  CommandCandidate,
  CommandResultPayload,
  ParsedCommand,
} from '@/types/commands'

export interface ChatCommandHooks {
  deleteSession: (threadId: string) => Promise<void>
  withdraw: () => Promise<boolean>
  newSession: () => Promise<void>
  clearView: () => void
  rename: (title: string) => Promise<boolean>
  pin: (pinned: boolean) => Promise<boolean>
  archive: () => Promise<boolean>
  selectAgent: (name: string, team?: string) => boolean
  copyLastAnswer: () => Promise<boolean>
  exportSession: (format: 'md' | 'json') => void
  showResult: (payload: CommandResultPayload) => void
}

function textResult(
  command: string,
  title: string,
  text: string,
  ok = true,
  warnings: string[] = [],
): CommandResultPayload {
  return {
    ok,
    command,
    kind: 'text',
    title,
    text,
    columns: [],
    rows: [],
    warnings,
    meta: {},
  }
}

function candidatesResult(
  command: string,
  title: string,
  items: CommandCandidate[],
  columns: string[],
): CommandResultPayload {
  return {
    ok: true,
    command,
    kind: 'table',
    title,
    text: '',
    columns,
    rows: items.map((item) => [item.label, item.value, item.status || '', item.description || '']),
    warnings: [],
    meta: {},
  }
}

function matchCandidate(items: CommandCandidate[], query: string): CommandCandidate[] {
  const needle = query.trim().toLowerCase()
  if (!needle) return items
  const exact = items.filter(
    (item) =>
      item.value.toLowerCase() === needle || item.label.toLowerCase() === needle,
  )
  if (exact.length) return exact
  return items.filter(
    (item) =>
      item.value.toLowerCase().startsWith(needle) ||
      item.label.toLowerCase().includes(needle),
  )
}

function pickCandidate(
  items: CommandCandidate[],
  query: string,
  label: string,
): CommandCandidate | null {
  const matched = matchCandidate(items, query)
  if (matched.length === 1) return matched[0]!
  if (matched.length > 1) {
    ElMessage.warning(
      `${label}「${query}」匹配到多个候选：${matched
        .slice(0, 3)
        .map((item) => item.value)
        .join('、')}，请写完整名称`,
    )
    return null
  }
  ElMessage.warning(`未找到${label}：${query}`)
  return null
}

async function callBackend(
  command: string,
  args: string[],
  flags: Record<string, string | boolean>,
  threadId: string,
): Promise<CommandResultPayload> {
  const res = await fetch(`${API_BASE}/commands/execute`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      command,
      args,
      flags,
      thread_id: threadId,
      raw: `/${command}`,
    }),
  })
  if (!res.ok) {
    const detail = (await res.json().catch(() => ({}))) as {
      detail?: { code?: string; message?: string }
    }
    return textResult(
      command,
      `/${command} 执行失败`,
      detail.detail?.message || `HTTP ${res.status}`,
      false,
    )
  }
  return (await res.json()) as CommandResultPayload
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms))
}

export function useCommandRunner(hooks: ChatCommandHooks) {
  const chat = useChatStore()
  const agents = useAgentStore()
  const settings = useSettingsStore()
  const commands = useCommandsStore()

  function currentThread(): string {
    return chat.threadId || ''
  }

  function bindResource(kind: 'skills' | 'mcp_ids' | 'plugin_ids', value: string) {
    const threadId = currentThread()
    if (!threadId) {
      ElMessage.warning('请先创建会话再绑定资源')
      return
    }
    const binding = commands.bindingFor(threadId)
    const values = Array.from(new Set([...binding[kind], value]))
    commands.patchBinding(threadId, { [kind]: values } as never)
    ElMessage.success(`已绑定本轮 ${kind}：${value}`)
  }

  async function showCandidates(kind: string, command: string, title: string) {
    const items = await commands.loadCandidates(kind)
    hooks.showResult(candidatesResult(command, title, items, ['名称', 'ID', '状态', '说明']))
  }

  async function pollSkillJob(jobId: string, bind: boolean) {
    for (let index = 0; index < 60; index += 1) {
      await wait(1200)
      try {
        const job = await fetchSkillJob(jobId)
        if (job.status === 'error') {
          ElMessage.error(job.error || '技能安装失败')
          return
        }
        if (job.status === 'done') {
          ElMessage.success(`技能已安装：${job.skill?.name || jobId}`)
          if (bind && job.skill?.name) bindResource('skills', job.skill.name)
          return
        }
      } catch {
        // 轮询失败继续等待，最终由超时兜底。
      }
    }
    ElMessage.warning('技能安装仍在后台进行，可稍后在技能页查看')
  }

  async function pollPluginJob(jobId: string, bind: boolean) {
    for (let index = 0; index < 60; index += 1) {
      await wait(1200)
      try {
        const job = await fetchPluginJob(jobId)
        if (job.status === 'error') {
          ElMessage.error(job.error || '插件安装失败')
          return
        }
        if (job.status === 'done') {
          const pluginId = job.result?.plugin_id || job.plugin_id
          ElMessage.success(`插件已安装：${pluginId || jobId}`)
          if (bind && pluginId) bindResource('plugin_ids', pluginId)
          return
        }
      } catch {
        // 同上，继续等待。
      }
    }
    ElMessage.warning('插件安装仍在后台进行，可稍后在插件页查看')
  }

  async function runInstall(command: ParsedCommand): Promise<void> {
    const target = command.args[0]
    const url = command.args[1]
    const use = String(command.args[2] || '').toLowerCase() === 'true' || command.args[2] === 'use'
    if (!target || !url) {
      ElMessage.warning(`用法：/${command.name} get <url> [IsUse]`)
      return
    }
    if (!/^https?:\/\//i.test(url)) {
      ElMessage.error('只支持 http(s) 链接')
      return
    }
    if (command.name === 'skill') {
      const result = await downloadSkillFromUrl(url)
      ElMessage.success('已开始下载技能，完成后可在技能页查看')
      void pollSkillJob(result.job_id, use)
      return
    }
    const result = await startPluginInstall({ source: 'url', url, auto_enable: true })
    ElMessage.success('已开始安装插件，完成后可在插件页查看')
    void pollPluginJob(result.job_id, use)
  }

  async function runOne(command: ParsedCommand): Promise<'stop' | 'continue'> {
    const threadId = currentThread()
    const args = command.args
    const first = String(args[0] || '').toLowerCase()

    switch (command.name) {
      case 'help':
      case 'status':
      case 'token':
      case 'rag': {
        hooks.showResult(await callBackend(command.name, args, command.flags, threadId))
        return 'continue'
      }
      case 'delete': {
        if (!threadId) {
          ElMessage.warning('当前没有会话')
          return 'stop'
        }
        if (!command.flags.f) {
          try {
            await ElMessageBox.confirm(
              '删除后，当前会话的消息记录、轨迹与文件关联将一并移除，且无法恢复。',
              '删除这个会话？',
              { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' },
            )
          } catch {
            return 'stop'
          }
        }
        await hooks.deleteSession(threadId)
        return 'stop'
      }
      case 'withdraw': {
        const ok = await hooks.withdraw()
        if (!ok) ElMessage.warning('当前没有可撤回的消息，或会话正在执行')
        return 'stop'
      }
      case 'retry': {
        const last = [...chat.messages].reverse().find((item) => item.kind === 'assistant')
        if (!last || !chat.retryLast(last)) {
          ElMessage.warning('只允许重试最后一条最终回答')
        }
        return 'stop'
      }
      case 'stop': {
        chat.stop()
        ElMessage.success('已中止当前生成')
        return 'stop'
      }
      case 'new': {
        await hooks.newSession()
        return 'stop'
      }
      case 'rename': {
        const title = args.join(' ').trim()
        if (!title) {
          ElMessage.warning('用法：/rename <title>')
          return 'stop'
        }
        hooks.rename(title)
        return 'stop'
      }
      case 'clear': {
        try {
          await ElMessageBox.confirm(
            '只清空当前页面的显示，不会删除后端会话历史。',
            '清空当前视图？',
            { confirmButtonText: '清空', cancelButtonText: '取消', type: 'warning' },
          )
        } catch {
          return 'stop'
        }
        hooks.clearView()
        return 'stop'
      }
      case 'copy': {
        const ok = await hooks.copyLastAnswer()
        if (ok) ElMessage.success('已复制最后一条回答')
        else ElMessage.warning('没有可复制的回答')
        return 'continue'
      }
      case 'export': {
        const format = first === 'json' ? 'json' : 'md'
        hooks.exportSession(format)
        return 'stop'
      }
      case 'history': {
        const count = Math.max(1, Math.min(Number(args[0] || 10) || 10, 20))
        const rows = commands.history.slice(0, count).map((item, index) => [index + 1, item])
        hooks.showResult({
          ok: true,
          command: 'history',
          kind: 'table',
          title: '命令历史',
          text: rows.length ? '' : '还没有命令历史',
          columns: ['#', '命令'],
          rows,
          warnings: [],
          meta: {},
        })
        return 'continue'
      }
      case 'pin':
      case 'unpin': {
        const ok = await hooks.pin(command.name === 'pin')
        if (ok) ElMessage.success(command.name === 'pin' ? '已置顶' : '已取消置顶')
        return 'stop'
      }
      case 'archive': {
        const ok = await hooks.archive()
        if (ok) ElMessage.success('已归档当前会话')
        return 'stop'
      }
      case 'think':
      case 'web': {
        const current = command.name === 'think' ? settings.thinking : settings.webSearch
        const value = first === 'on' ? true : first === 'off' ? false : null
        if (value === null) {
          hooks.showResult(
            textResult(command.name, `/${command.name}`, `当前：${current ? '开启' : '关闭'}`),
          )
          return 'continue'
        }
        if (command.name === 'think') await settings.setThinking(value)
        else await settings.setWebSearch(value)
        ElMessage.success(`已${value ? '开启' : '关闭'}${
          command.name === 'think' ? '思考模式' : '联网搜索'
        }`)
        return 'continue'
      }
      case 'agent': {
        if (!threadId) {
          ElMessage.warning('请先创建会话')
          return 'stop'
        }
        if (!first || first === 'list') {
          await showCandidates('agents', 'agent', '可用 Agent')
          return 'continue'
        }
        if (first === 'off' || first === 'default') {
          hooks.selectAgent('default')
          commands.patchBinding(threadId, { agentId: '', agentName: '', groupId: '', groupName: '' })
          ElMessage.success('已恢复默认 Agent')
          return 'continue'
        }
        const team = command.flags.team ? args[1] : ''
        if (!hooks.selectAgent(args[0]!, team)) {
          ElMessage.warning(`未找到 Agent：${args[0]}`)
          return 'stop'
        }
        const agent = agents.items.find((item) => item.id === chat.selectedAgentId)
        const group = agents.groups.find((item) => item.group_id === chat.selectedGroupId)
        commands.patchBinding(threadId, {
          agentId: chat.selectedAgentId,
          agentName: agent?.name || chat.selectedAgentId,
          groupId: chat.selectedGroupId,
          groupName: group?.name || '',
        })
        ElMessage.success(`本轮使用 Agent：${agent?.name || chat.selectedAgentId}`)
        return 'continue'
      }
      case 'taskflow': {
        if (!threadId) {
          ElMessage.warning('请先创建会话')
          return 'stop'
        }
        if (!first || first === 'list') {
          await showCandidates('taskflows', 'taskflow', '可用任务流')
          return 'continue'
        }
        if (first === 'off') {
          commands.patchBinding(threadId, { flowId: '', flowName: '' })
          ElMessage.success('已取消任务流绑定')
          return 'continue'
        }
        const items = await commands.loadCandidates('taskflows')
        const picked = pickCandidate(items, args[0]!, '任务流')
        if (!picked) return 'stop'
        commands.patchBinding(threadId, {
          flowId: picked.value,
          flowName: picked.label,
        })
        ElMessage.success(`本轮使用任务流：${picked.label}`)
        return 'continue'
      }
      case 'skills':
      case 'mcp':
      case 'plugin': {
        if (!threadId) {
          ElMessage.warning('请先创建会话')
          return 'stop'
        }
        const kindMap = {
          skills: { binding: 'skills', candidates: 'skills', label: '技能' },
          mcp: { binding: 'mcp_ids', candidates: 'mcp', label: 'MCP' },
          plugin: { binding: 'plugin_ids', candidates: 'plugins', label: '插件' },
        } as const
        const conf = kindMap[command.name]
        if (!first || first === 'list') {
          await showCandidates(conf.candidates, command.name, `可用${conf.label}`)
          return 'continue'
        }
        if (first === 'off') {
          commands.patchBinding(threadId, { [conf.binding]: [] } as never)
          ElMessage.success(`已取消${conf.label}绑定`)
          return 'continue'
        }
        if (first === 'status') {
          const backend = command.name === 'mcp' ? 'list_mcp' : 'list_plugins'
          hooks.showResult(await callBackend(backend, [], {}, threadId))
          return 'continue'
        }
        if (command.name !== 'skills' && first === 'get') {
          await runInstall(command)
          return 'continue'
        }
        if (first === 'show') {
          if (command.name !== 'skills') {
            ElMessage.warning('/plugin 不支持 show，请用 /plugin status')
            return 'continue'
          }
          const name = args[1]
          if (!name) {
            ElMessage.warning('用法：/skill show <name>')
            return 'continue'
          }
          const content = await fetchSkillContent('main', name)
          hooks.showResult(
            textResult('skill', `技能：${name}`, String(content.content || '').slice(0, 6000)),
          )
          return 'continue'
        }
        const items = await commands.loadCandidates(conf.candidates)
        const picked = pickCandidate(items, args[0]!, conf.label)
        if (!picked) return 'stop'
        bindResource(conf.binding, picked.value)
        return 'continue'
      }
      case 'binding': {
        const binding = commands.bindingFor(threadId)
        const rows = [
          ['Agent', binding.agentName || chat.selectedAgentId || '默认'],
          ['组', binding.groupName || chat.selectedGroupId || '无'],
          ['技能', binding.skills.join('、') || '无'],
          ['MCP', binding.mcp_ids.join('、') || '全部启用'],
          ['插件', binding.plugin_ids.join('、') || '全部启用'],
          ['任务流', binding.flowName || binding.flowId || '无'],
        ]
        hooks.showResult({
          ok: true,
          command: 'binding',
          kind: 'table',
          title: '本轮绑定',
          text: '',
          columns: ['资源', '值'],
          rows,
          warnings: [],
          meta: {},
        })
        return 'continue'
      }
      case 'skill': {
        if (first === 'get') {
          await runInstall(command)
          return 'continue'
        }
        if (first === 'show') {
          const name = args[1]
          if (!name) {
            ElMessage.warning('用法：/skill show <name>')
            return 'continue'
          }
          const content = await fetchSkillContent('main', name)
          hooks.showResult(
            textResult('skill', `技能：${name}`, String(content.content || '').slice(0, 6000)),
          )
          return 'continue'
        }
        ElMessage.warning('用法：/skill get <url> [IsUse] 或 /skill show <name>')
        return 'stop'
      }
      default: {
        ElMessage.warning(`命令 /${command.name} 暂未实现`)
        return 'stop'
      }
    }
  }

  async function run(parsed: ParsedCommand[]): Promise<'stop' | 'continue'> {
    let stop = false
    for (const command of parsed) {
      commands.pushHistory(command.raw)
      try {
        const result = await runOne(command)
        if (result === 'stop') stop = true
      } catch (error) {
        ElMessage.error(error instanceof Error ? error.message : '命令执行失败')
        stop = true
      }
    }
    return stop ? 'stop' : 'continue'
  }

  return { run }
}
