/**
 * Project store —— 当前项目 id + 项目列表 + 切换。
 *
 * D-MVP 多项目空间：sidebar 顶部有项目切换 dropdown，切换后所有列表
 * 接口（/api/datasources / /api/tasks / /api/workflows /api/bootstrap）
 * 自动追加 ?project_id= 当前项目过滤 —— 在 api.js 里读这个 store。
 *
 * 持久化 key: dataops.project_id（空串 = "全部项目"）
 */
import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { apiGet, apiJson } from '../api'


export interface Project {
  id: string
  name: string
  description?: string
  members?: string[]
}

export interface ProjectUpdatePayload {
  name?: string
  description?: string
  members?: string[]
}


const PROJECT_KEY = 'dataops.project_id'


export const useProjectStore = defineStore('project', () => {
  const currentProjectId = ref<string>(localStorage.getItem(PROJECT_KEY) || '')
  const projects = ref<Project[]>([])

  const currentProject = computed<Project | null>(() =>
    projects.value.find(p => p.id === currentProjectId.value) || null
  )

  function setProject(projectId: string): void {
    currentProjectId.value = projectId || ''
    if (currentProjectId.value) {
      localStorage.setItem(PROJECT_KEY, currentProjectId.value)
    } else {
      localStorage.removeItem(PROJECT_KEY)
    }
  }

  async function reload(): Promise<void> {
    try {
      const data = await apiGet<Project[]>('/api/projects')
      projects.value = Array.isArray(data) ? data : []
      // 当前选中项目已从可见列表里消失（项目被删 / 用户被踢）→ 回退"全部"
      if (currentProjectId.value && !projects.value.some(p => p.id === currentProjectId.value)) {
        setProject('')
      }
    } catch {
      projects.value = []
    }
  }

  async function createProject(name: string, description = ''): Promise<Project> {
    const project = await apiJson<Project>('/api/projects', 'POST', { name, description, members: [] })
    await reload()
    return project
  }

  async function updateProject(projectId: string, payload: ProjectUpdatePayload): Promise<Project> {
    const updated = await apiJson<Project>(`/api/projects/${projectId}`, 'PUT', payload)
    await reload()
    return updated
  }

  async function deleteProject(projectId: string): Promise<void> {
    await apiJson(`/api/projects/${projectId}`, 'DELETE')
    if (currentProjectId.value === projectId) {
      setProject('')
    }
    await reload()
  }

  return {
    currentProjectId, projects, currentProject,
    setProject, reload, createProject, updateProject, deleteProject,
  }
})
