/**
 * 数据源 store —— Pinia 化第二步。
 *
 * 仅持有跟"数据源管理"强相关的本地状态：
 *   - datasourceDraft：新建表单
 *   - editDraft / editingDatasourceId：行内编辑表单
 *
 * 数据源列表本身（state.datasources）暂时仍由 App.vue 的 reactive `state` 持有，
 * loadBootstrap 写入。后续可以再把 list / drivers 整体迁过来；本轮先把"表单
 * 状态"抽出，让 DatasourceView 不再耦合到 App.vue 大 state。
 */
import { reactive, ref } from 'vue'
import { defineStore } from 'pinia'

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

  return {
    datasourceDraft, editingDatasourceId, editDraft,
    startEditDatasource, cancelEditDatasource, resetDatasourceDraft,
  }
})
