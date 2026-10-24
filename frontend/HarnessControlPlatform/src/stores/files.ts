// 线程文件：列表 / 读取 / 保存（预览与下载用）
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { API_BASE } from '@/api/client'
import { USER_ID } from './sessions'

export interface FileMeta {
  path: string
  size: number
  modified_at: string | null
}

export const useFileStore = defineStore('files', () => {
  const files = ref<FileMeta[]>([])
  const source = ref<'state' | 'sandbox' | ''>('')
  const loading = ref(false)
  const current = ref<{ path: string; content: string } | null>(null)
  const saving = ref(false)
  const error = ref('')

  async function list(threadId: string | null) {
    if (!threadId) {
      files.value = []
      source.value = ''
      return
    }
    loading.value = true
    error.value = ''
    try {
      const res = await fetch(
        `${API_BASE}/threads/${threadId}/files?user_id=${USER_ID}`,
      )
      if (!res.ok) throw new Error((await res.json().catch(() => ({})))?.detail || String(res.status))
      const data = await res.json()
      files.value = data.files || []
      source.value = data.source || ''
    } catch (e) {
      error.value = (e as Error).message
      files.value = []
    } finally {
      loading.value = false
    }
  }

  async function open(threadId: string, path: string) {
    error.value = ''
    try {
      const res = await fetch(
        `${API_BASE}/threads/${threadId}/files/content?user_id=${USER_ID}&path=${encodeURIComponent(path)}`,
      )
      if (!res.ok) throw new Error((await res.json().catch(() => ({})))?.detail || String(res.status))
      const data = await res.json()
      current.value = { path: data.path, content: data.content }
    } catch (e) {
      error.value = (e as Error).message
    }
  }

  async function save(threadId: string) {
    if (!current.value || saving.value) return false
    saving.value = true
    error.value = ''
    try {
      const res = await fetch(
        `${API_BASE}/threads/${threadId}/files/content?user_id=${USER_ID}`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: current.value.path, content: current.value.content }),
        },
      )
      if (!res.ok) throw new Error((await res.json().catch(() => ({})))?.detail || String(res.status))
      await list(threadId)
      return true
    } catch (e) {
      error.value = (e as Error).message
      return false
    } finally {
      saving.value = false
    }
  }

  function closePreview() {
    current.value = null
  }

  return { files, source, loading, current, saving, error, list, open, save, closePreview }
})

// 浏览器端下载
export function downloadText(filename: string, content: string) {
  const blob = new Blob([content], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}
