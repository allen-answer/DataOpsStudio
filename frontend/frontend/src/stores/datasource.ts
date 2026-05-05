/**
 * 数据源 store —— Pinia 化第二步。
 *
 * 持有：
 *   - datasourceDraft：新建表单
 *   - editDraft / editingDatasourceId：行内编辑表单
 *   - CRUD handlers（create/update/delete/test）—— 自闭环，调 useNoticeStore +
 *     useBootstrapStore.reload() 不依赖 App.vue
 *
 * 数据源列表本身（state.datasources）由 useBootstrapStore 持有；handlers 写入
 * list 时直接操作 bootstrap store 的 state.datasources。
 *
 * S3.B：迁 .ts。Datasource 实例 shape 在 view 不强类型，先 unknown[] 处理；
 * 等 task / workflow store 迁完后统一收口。
 */
import { reactive, ref } from 'vue'
import { defineStore } from 'pinia'
import { apiJson } from '../api'
import { useNoticeStore } from './notice'
import { useBootstrapStore } from './bootstrap'
import { useProjectStore } from './project'
import type { ApiDataSource, ApiDataSourceCreate } from '../types/api'


// S4.B：Datasource shape 走 codegen 同步后端 Pydantic
export type Datasource = ApiDataSource

// 表单草稿：新建表单不带 project_id（提交时再从 useProjectStore 注入）；
// 编辑草稿带 project_id（让 admin 跨项目移动 datasource）。
export type DatasourceDraft = Omit<ApiDataSourceCreate, 'project_id'>
export type DatasourceEditDraft = ApiDataSourceCreate


function _toErrorMessage(error: unknown): string {
  if (error && typeof error === 'object' && 'message' in error) {
    return String((error as { message?: unknown }).message)
  }
  return String(error ?? '未知错误')
}

export const useDatasourceStore = defineStore('datasource', () => {
  // S4.B 收口的字段名一致性 fix：db_type 后端 enum 是 'MySQL' / 'Oracle' /
  // 'DM' / 'DB2'（区分大小写）。原来写 'mysql' 小写会被后端 422 reject —— 之前
  // 没暴露是因为表单上 select dropdown 的 value 也都是大写覆盖默认值，
  // 但默认 state 偷偷错了 1 年。codegen 类型对齐后 typecheck 直接抓出来。
  const datasourceDraft = reactive<DatasourceDraft>({
    name: '',
    db_type: 'MySQL',
    host: '',
    port: 3306,
    database: '',
    username: '',
    password: '',
  })

  const editingDatasourceId = ref<string>('')
  const editDraft = reactive<DatasourceEditDraft>({
    name: '', db_type: 'MySQL', host: '', port: 3306,
    database: '', username: '', password: '',
    project_id: '',
  })

  function startEditDatasource(item: Datasource): void {
    editingDatasourceId.value = item.id
    Object.assign(editDraft, {
      name: item.name,
      db_type: item.db_type,
      host: item.host,
      port: item.port,
      database: item.database,
      username: item.username,
      password: '',
      project_id: item.project_id || '',
    })
  }

  function cancelEditDatasource(): void {
    editingDatasourceId.value = ''
  }

  function resetDatasourceDraft(): void {
    Object.assign(datasourceDraft, {
      name: '', host: '', database: '', username: '', password: '',
    })
  }

  // ─── CRUD handlers ──────────────────────────────────────────────────────────
  // 设计：直接操作 bootstrap store 的 state.datasources，不调 loadBootstrap
  // 跨 store 联动（避免 createDatasource 触发 loadBootstrap → 自动切任务的副作用）

  async function createDatasource(): Promise<Datasource | undefined> {
    const notice = useNoticeStore()
    const bootstrap = useBootstrapStore()
    const project = useProjectStore()
    // 当前选中项目 → 新建资源自动归属该项目（"全部"模式 = 不指定 project_id）
    const payload = { ...datasourceDraft, project_id: project.currentProjectId || '' }
    try {
      const created = await apiJson<Datasource>('/api/datasources', 'POST', payload)
      bootstrap.state.datasources.push(created)
      resetDatasourceDraft()
      notice.setNotice('数据源已创建')
      return created
    } catch (error) {
      notice.setNotice(`创建失败：${_toErrorMessage(error)}`)
      throw error
    }
  }

  async function updateDatasource(id: string): Promise<Datasource | undefined> {
    const notice = useNoticeStore()
    const bootstrap = useBootstrapStore()
    try {
      const updated = await apiJson<Datasource>(`/api/datasources/${id}`, 'PUT', { ...editDraft })
      const idx = bootstrap.state.datasources.findIndex((d) => (d as Datasource).id === id)
      if (idx !== -1) bootstrap.state.datasources[idx] = updated
      editingDatasourceId.value = ''
      notice.setNotice('数据源已更新')
      return updated
    } catch (error) {
      notice.setNotice(`更新失败：${_toErrorMessage(error)}`)
    }
  }

  async function deleteDatasource(id: string): Promise<void> {
    const notice = useNoticeStore()
    const bootstrap = useBootstrapStore()
    try {
      await apiJson(`/api/datasources/${id}`, 'DELETE')
      bootstrap.state.datasources = bootstrap.state.datasources.filter((d) => (d as Datasource).id !== id)
      notice.setNotice('数据源已删除')
    } catch (error) {
      notice.setNotice(`删除失败：${_toErrorMessage(error)}`)
    }
  }

  async function testDatasource(id: string): Promise<void> {
    const notice = useNoticeStore()
    try {
      await apiJson(`/api/datasources/${id}/test`, 'POST')
      notice.setNotice('连接成功')
    } catch (error) {
      notice.setNotice(`连接失败：${_toErrorMessage(error)}`)
    }
  }

  return {
    datasourceDraft, editingDatasourceId, editDraft,
    startEditDatasource, cancelEditDatasource, resetDatasourceDraft,
    createDatasource, updateDatasource, deleteDatasource, testDatasource,
  }
})
