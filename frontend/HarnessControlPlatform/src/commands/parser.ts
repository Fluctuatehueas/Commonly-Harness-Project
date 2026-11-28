// 命令解析：把输入框文本拆成「命令 + 正文」。
// 规则：命令只能出现在行首或空白后；每条命令按声明的最多参数个数吞掉参数，
// 参数之后的同一行文本视为正文（例如 `/mcp slack 帮我总结`）。

import type { ParsedCommand, ParsedInput } from '@/types/commands'

/** 每条命令最多吞掉多少个位置参数（不够时按实际数量）。 */
const MAX_ARGS: Record<string, number> = {
  help: 1,
  history: 1,
  rename: 1,
  export: 1,
  think: 1,
  web: 1,
  agent: 2,
  skills: 1,
  mcp: 1,
  plugin: 3,
  taskflow: 1,
  skill: 3,
}

interface Token {
  value: string
  start: number
  end: number
}

function tokenize(text: string): Token[] {
  const tokens: Token[] = []
  let index = 0
  while (index < text.length) {
    if (/\s/.test(text[index]!)) {
      index += 1
      continue
    }
    const start = index
    let value = ''
    if (text[index] === '"') {
      index += 1
      while (index < text.length && text[index] !== '"') {
        if (text[index] === '\\' && index + 1 < text.length) {
          index += 1
        }
        value += text[index]
        index += 1
      }
      if (text[index] === '"') index += 1
      tokens.push({ value, start, end: index })
      continue
    }
    while (index < text.length && !/\s/.test(text[index]!)) {
      value += text[index]
      index += 1
    }
    tokens.push({ value, start, end: index })
  }
  return tokens
}

function isCommandToken(token: Token, text: string, names: Set<string>): boolean {
  if (!token.value.startsWith('/') || token.value.length < 2) return false
  if (token.start > 0 && !/\s/.test(text[token.start - 1]!)) return false
  return names.has(token.value.slice(1).toLowerCase())
}

/**
 * 解析输入文本。
 * - 未知命令且出现在行首时返回 error，不当作正文静默发送。
 * - commands 按出现顺序返回，start/end 用于从正文中剔除。
 */
export function parseInput(text: string, names: Set<string>): ParsedInput {
  const raw = text ?? ''
  if (!raw.trim()) return { commands: [], rest: raw }
  const tokens = tokenize(raw)
  const commands: ParsedCommand[] = []
  const consumed: Array<{ start: number; end: number }> = []

  let index = 0
  while (index < tokens.length) {
    const token = tokens[index]!
    const atStart = token.start === 0 || /\s/.test(raw[token.start - 1]!)
    if (!atStart || !token.value.startsWith('/')) {
      index += 1
      continue
    }
    const name = token.value.slice(1).toLowerCase()
    if (!names.has(name)) {
      // 行首的未知命令直接提示，避免用户以为执行了。
      if (token.start === 0) {
        return {
          commands,
          rest: '',
          error: `未知命令：${token.value}（输入 /help 查看可用命令）`,
        }
      }
      index += 1
      continue
    }

    const maxArgs = MAX_ARGS[name] ?? 0
    const args: string[] = []
    const flags: Record<string, string | boolean> = {}
    let cursor = index + 1
    let end = token.end
    while (cursor < tokens.length && args.length < maxArgs) {
      const next = tokens[cursor]!
      if (next.value.startsWith('/') && isCommandToken(next, raw, names)) break
      if (next.value.startsWith('--')) {
        const body = next.value.slice(2)
        const [key, value] = body.split('=', 2)
        if (key) flags[key] = value ?? true
        end = next.end
        cursor += 1
        continue
      }
      if (next.value.startsWith('-') && next.value !== '-') {
        const key = next.value.replace(/^-+/, '')
        if (key) flags[key] = true
        end = next.end
        cursor += 1
        continue
      }
      args.push(next.value)
      end = next.end
      cursor += 1
    }

    commands.push({
      raw: raw.slice(token.start, end),
      name,
      args,
      flags,
      start: token.start,
      end,
    })
    consumed.push({ start: token.start, end })
    index = cursor
  }

  let rest = ''
  let cursor = 0
  for (const range of consumed) {
    rest += raw.slice(cursor, range.start)
    cursor = range.end
  }
  rest += raw.slice(cursor)
  rest = rest
    .split('\n')
    .map((line) => line.replace(/^\s+|\s+$/g, ''))
    .join('\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()

  return { commands, rest }
}

/** 用于建议面板的「当前输入段」：命令名或某个命令的参数前缀。 */
export interface CommandContextInfo {
  command: string
  commandTyped: string
  partial: string
  offset: number
  stage: 'command' | 'argument'
}

export function contextAt(text: string, offset: number): CommandContextInfo {
  const before = text.slice(0, offset)
  const line = before.slice(before.lastIndexOf('\n') + 1)
  const trimmed = line.trimStart()
  const leading = line.length - trimmed.length
  const parts = trimmed.split(/\s+/)
  const commandPart = parts[0] || ''
  const stage: 'command' | 'argument' = parts.length > 1 || /\s$/.test(line) ? 'argument' : 'command'
  return {
    command: commandPart.startsWith('/') ? commandPart.slice(1).toLowerCase() : '',
    commandTyped: commandPart,
    partial: parts.length > 1 ? parts[parts.length - 1] || '' : stage === 'argument' ? '' : commandPart,
    offset: leading,
    stage,
  }
}
