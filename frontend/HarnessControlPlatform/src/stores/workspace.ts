// 项目工作区：后端是唯一事实来源，前端不做本地伪造
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import {
  createWorkspaceProject,
  deleteWorkspaceProject,
  fetchWorkspaceProjects,
  updateWorkspaceProject,
  validateWorkspaceProject,
  type ProjectMode,
  type WorkspaceProject,
} from '@/api/workspace'

export const DEFAULT_PROJECT_ID = 'default'

export const useWorkspaceStore = defineStore('workspace', () => {
  const projects = ref<WorkspaceProject[]>([])
  const loading = ref(false)
  const error = ref('')

  const sorted = computed(() =>
    [...projects.value].sort((a, b) => {
      if (a.pinned !== b.pinned) return a.pinned ? -1 : 1
      return String(b.created_at || '').localeCompare(String(a.created_at || ''))
    }),
  )

  async function refresh() {
    loading.value = true
    try {
      projects.value = await fetchWorkspaceProjects()
      error.value = ''
    } catch (err) {
      error.value = (err as Error).message
    } finally {
      loading.value = false
    }
  }

  async function create(
    mode: ProjectMode,
    path: string,
    name?: string,
    adopt = false,
  ): Promise<WorkspaceProject | null> {
    const value = path.trim()
    if (!value) {
      error.value = '项目目录不能为空'
      return null
    }
    error.value = ''
    try {
      const created = await createWorkspaceProject({ mode, path: value, name, adopt })
      await refresh()
      return created
    } catch (err) {
      error.value = (err as Error).message
      return null
    }
  }

  async function update(
    projectId: string,
    patch: { name?: string; pinned?: boolean },
  ): Promise<boolean> {
    error.value = ''
    try {
      await updateWorkspaceProject(projectId, patch)
      await refresh()
      return true
    } catch (err) {
      error.value = (err as Error).message
      return false
    }
  }

  async function remove(projectId: string): Promise<boolean> {
    error.value = ''
    try {
      await deleteWorkspaceProject(projectId)
      await refresh()
      return true
    } catch (err) {
      error.value = (err as Error).message
      return false
    }
  }

  async function validate(projectId: string, repair = true): Promise<boolean> {
    error.value = ''
    try {
      await validateWorkspaceProject(projectId, repair)
      await refresh()
      return true
    } catch (err) {
      error.value = (err as Error).message
      return false
    }
  }

  return { projects, sorted, loading, error, refresh, create, update, remove, validate }
})
