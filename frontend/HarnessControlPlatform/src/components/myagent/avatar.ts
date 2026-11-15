// Agent 头像的统一渲染规则：两种模式的列表共用，避免同一 Agent 出现两种颜色。
const AVATAR_GRADIENTS = [
  'linear-gradient(135deg, #37d0c4, #2f7cf6)',
  'linear-gradient(135deg, #f6c85f, #f08a5d)',
  'linear-gradient(135deg, #a18cd1, #fbc2eb)',
  'linear-gradient(135deg, #84fab0, #8fd3f4)',
  'linear-gradient(135deg, #ff9a9e, #fad0c4)',
]

export function avatarGradient(agentId: string): string {
  let hash = 0
  for (const char of agentId) hash = (hash * 31 + char.charCodeAt(0)) % 997
  return AVATAR_GRADIENTS[hash % AVATAR_GRADIENTS.length] || AVATAR_GRADIENTS[0] || ''
}

export function avatarInitial(name: string, avatar = ''): string {
  return (avatar || name || '?').slice(0, 1)
}
