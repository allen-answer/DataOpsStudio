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
 */
import { reactive, ref } from 'vue'
import { defineStore } from 'pinia'
import { apiJson } from '../api'
import { useNoticeStore } from './notice'
import { useBootstrapStore } from './bootstrap'

function _toErrorMessage(error) {
  return error?.message || String(error || '未知错误')
}

export const useDatasourceStore = defineStore('datasource', () => {
  const datasourceDraft = reactive({
    name: '',
    db_type: 'mysql',
    host: '',
    port: 3306,
    database: '',
    username: '',
    password: '',
  })

  const editingDatasourceId = ref('')
  const editDraft = reactive({
    name: '', db_type: '', host: '', port: 3306,
    database: '', username: '', password: '',
  })

  function startEditDatasource(item) {
    editingDatasourceId.value = item.id
    Object.assign(editDraft, {
      name: item.name,
      db_type: item.db_type,
      host: item.host,
      port: item.port,
      database: item.database,
      username: item.username,
      password: '',
    })
  }

  function cancelEditDatasource() {
    editingDatasourceId.value = ''
  }

  function resetDatasourceDraft() {
    Object.assign(datasourceDraft, {
      name: '', host: '', database: '', username: '', password: '',
    })
  }

  // ─── CRUD handlers ──────────────────────────────────────────────────────────
  // 设计：直接操作 bootstrap store 的 state.datasources，不调 loadBootstrap
  // 跨 store 联动（避免 createDatasource 触发 loadBootstrap → 自动切任务的副作用）

  async function createDatasource() {
    const notice = useNoticeStore()
    const bootstrap = useBootstrapStore()
    try {
      const created = await apiJson('/api/datasources', 'POST', { ...datasourceDraft })
      bootstrap.state.datasources.push(created)
      resetDatasourceDraft()
      notice.setNotice('数据源已创建')
      return created
    } catch (error) {
      notice.setNotice(`创建失败：${_toErrorMessage(error)}`)
      throw error
    }
  }

  async function updateDatasource(id) {
    const notice = useNoticeStore()
    const bootstrap = useBootstrapStore()
    try {
      const updated = await apiJson(`/api/datasources/${id}`, 'PUT', { ...editDraft })
      const idx = bootstrap.state.datasources.findIndex(d => d.id === id)
      if (idx !== -1) bootstrap.state.datasources[idx] = updated
      editingDatasourceId.value = ''
      notice.setNotice('数据源已更新')
      return updated
    } catch (error) {
      notice.setNotice(`更新失败：${_toErrorMessage(error)}`)
    }
  }

  async function deleteDatasource(id) {
    const notice = useNoticeStore()
    const bootstrap = useBootstrapStore()
    try {
      await apiJson(`/api/datasources/${id}`, 'DELETE')
      bootstrap.state.datasources = bootstrap.state.datasources.filter(d => d.id !== id)
      notice.setNotice('数据源已删除')
    } catch (error) {
      notice.setNotice(`删除失败：${_toErrorMessage(error)}`)
    }
  }

  async function testDatasource(id) {
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
