/**
 * SQL 工作台 v0.4 — 模板库 store。
 *
 * 单独建一个 store(不塞 sqlWorkbench)是因为关注分离:
 *   - sqlWorkbench:console / execute / metadata,运行时数据
 *   - sqlTemplates:模板 CRUD,准静态配置数据
 *
 * 数据流:
 *   1. SqlWorkbenchView onMounted → loadTemplates()
 *   2. 用户点"保存为模板" → createTemplate(payload)
 *   3. 用户在模板 tab 编辑 → updateTemplate(id, payload)
 *   4. 用户删除 → deleteTemplate(id)
 *   5. 用户导入 JSON → importTemplates(list, overwrite)
 *   6. 用户点导出 → exportTemplates() 拿 JSON 返回给浏览器下载
 */
import { reactive, ref, computed } from 'vue'
import { defineStore } from 'pinia'
import { apiGet, apiJson } from '../api'

export type RiskLevel = 'low' | 'medium' | 'high'

export interface SQLTemplate {
  id: string
  name: string
  description: string
  tags: string[]
  db_types: string[]
  project_id: string
  risk_level: RiskLevel
  sql: string
  created_by: string
  created_at: string
  updated_at: string
  builtin: boolean
}

export interface SQLTemplateCreate {
  name: string
  description?: string
  tags?: string[]
  db_types?: string[]
  project_id?: string
  risk_level?: RiskLevel
  sql: string
}

export interface ImportReport {
  ok: boolean
  created: number
  skipped: number
  errors: number
}

export const useSqlTemplatesStore = defineStore('sqlTemplates', () => {
  const templates = ref<SQLTemplate[]>([])
  const loading = ref(false)
  const lastError = ref('')

  // 过滤态 —— 用户在模板 panel 调,会触发 loadTemplates 重拉(简单方案;
  // 也可以前端纯客户端过滤,但 GET 时塞 query 更省内存且支持 server-side 排序)。
  const filters = reactive<{
    q: string
    tag: string         // 逗号分隔
    db_type: string
    project_id: string | null  // null 表示"不过滤";空串表示"只看全局"
  }>({ q: '', tag: '', db_type: '', project_id: null })

  // 派生:builtin / user 两类计数,UI 展示用
  const builtinCount = computed(() => templates.value.filter(t => t.builtin).length)
  const userCount = computed(() => templates.value.filter(t => !t.builtin).length)

  async function loadTemplates(): Promise<void> {
    loading.value = true
    lastError.value = ''
    try {
      const qs = new URLSearchParams()
      if (filters.q) qs.set('q', filters.q)
      if (filters.tag) qs.set('tag', filters.tag)
      if (filters.db_type) qs.set('db_type', filters.db_type)
      if (filters.project_id !== null) qs.set('project_id', filters.project_id)
      const resp = await apiGet<{ items: SQLTemplate[]; count: number }>(
        `/api/sql-templates${qs.toString() ? '?' + qs.toString() : ''}`,
      )
      templates.value = resp.items || []
    } catch (e: unknown) {
      lastError.value = (e as Error)?.message || String(e)
    } finally {
      loading.value = false
    }
  }

  async function createTemplate(payload: SQLTemplateCreate): Promise<SQLTemplate> {
    const t = await apiJson<SQLTemplate>('/api/sql-templates', 'POST', payload)
    templates.value.unshift(t)
    return t
  }

  async function updateTemplate(id: string, payload: SQLTemplateCreate): Promise<SQLTemplate> {
    const t = await apiJson<SQLTemplate>(`/api/sql-templates/${id}`, 'PUT', payload)
    const idx = templates.value.findIndex(x => x.id === id)
    if (idx >= 0) templates.value[idx] = t
    return t
  }

  async function deleteTemplate(id: string): Promise<void> {
    await apiJson(`/api/sql-templates/${id}`, 'DELETE')
    templates.value = templates.value.filter(t => t.id !== id)
  }

  async function importTemplates(items: any[], overwriteByName: boolean = false): Promise<ImportReport> {
    const report = await apiJson<ImportReport>('/api/sql-templates/import', 'POST', {
      templates: items, overwrite_by_name: overwriteByName,
    })
    // 拉一次最新列表
    await loadTemplates()
    return report
  }

  async function exportTemplates(includeBuiltin: boolean = false): Promise<any[]> {
    const qs = new URLSearchParams()
    qs.set('include_builtin', includeBuiltin ? 'true' : 'false')
    const resp = await apiGet<{ templates: any[]; count: number }>(
      `/api/sql-templates/export/json?${qs.toString()}`,
    )
    return resp.templates || []
  }

  return {
    templates, loading, lastError, filters,
    builtinCount, userCount,
    loadTemplates, createTemplate, updateTemplate, deleteTemplate,
    importTemplates, exportTemplates,
  }
})
