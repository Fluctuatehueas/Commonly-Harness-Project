// 上下文附件：格式声明、上传、删除
import { API_BASE } from './client'

export interface ContextFile {
  id: string
  name: string
  ext: string
  kind: 'image' | 'document' | 'text'
  mime: string
  size: number
}

export interface ContextFormats {
  accept: string
  max_files: number
  max_file_mb: number
  groups: Record<string, string[]>
}

// 后端不可达时的兜底，保证离线也能选择常见文件并给出上传失败提示
const FALLBACK_FORMATS: ContextFormats = {
  accept: '.png,.jpg,.jpeg,.webp,.gif,.docx,.pdf,.txt,.md,.csv,.json,.yaml,.yml',
  max_files: 6,
  max_file_mb: 20,
  groups: {
    image: ['.png', '.jpg', '.jpeg', '.webp', '.gif'],
    document: ['.docx', '.pdf'],
    text: ['.txt', '.md', '.markdown', '.csv', '.json', '.yaml', '.yml', '.log', '.xml', '.html', '.htm', '.py', '.js', '.ts', '.java', '.sql', '.ini', '.toml'],
  },
}

export async function fetchContextFormats(): Promise<ContextFormats> {
  try {
    const res = await fetch(`${API_BASE}/context/formats`, {
      signal: AbortSignal.timeout(4000),
    })
    if (!res.ok) throw new Error(String(res.status))
    return (await res.json()) as ContextFormats
  } catch {
    return FALLBACK_FORMATS
  }
}

export async function uploadContextFile(file: File, userId: string): Promise<ContextFile> {
  const form = new FormData()
  form.append('file', file)
  const res = await fetch(
    `${API_BASE}/context/upload?user_id=${encodeURIComponent(userId)}`,
    { method: 'POST', body: form },
  )
  if (!res.ok) {
    const detail = (await res.json().catch(() => ({}))) as { detail?: string }
    throw new Error(detail.detail || `上传失败: HTTP ${res.status}`)
  }
  return (await res.json()) as ContextFile
}

export async function deleteContextFile(id: string, userId: string): Promise<void> {
  await fetch(`${API_BASE}/context/${id}?user_id=${encodeURIComponent(userId)}`, {
    method: 'DELETE',
  }).catch(() => undefined)
}

// 抓取公网链接并转成上下文附件（后端会做内网/大小校验）
export async function importContextUrl(url: string, userId: string): Promise<ContextFile> {
  const res = await fetch(`${API_BASE}/context/url`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url, user_id: userId }),
  })
  if (!res.ok) {
    const detail = (await res.json().catch(() => ({}))) as { detail?: string }
    throw new Error(detail.detail || `导入失败: HTTP ${res.status}`)
  }
  return (await res.json()) as ContextFile
}
