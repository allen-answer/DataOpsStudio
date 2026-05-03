/**
 * Lineage store —— 单脚本血缘工作台 state（lineage reactive） + analyze handler。
 *
 * 跟 batch store 拆开是因为 LineageWorkbenchView 同时支持单脚本 / 多脚本 /
 * ZIP 模式，每种模式有自己的本地状态（result + error + 上传文件）。
 */
import { reactive } from 'vue'
import { defineStore } from 'pinia'
import { apiForm, apiJson } from '../api'


function makeLineageDraft() {
  return reactive({
    sql: '',
    dialect: '',
    schemaDatasourceId: '',
    schemaName: '',
    schemaTableFilter: '',
    schemaOnlySqlTables: true,
    schemaDialect: '',
    schemaFiles: [],
    sqlFile: null,
    result: null,
    error: '',
  })
}


export const useLineageStore = defineStore('lineage', () => {
  const lineage = makeLineageDraft()

  function resetResult() {
    lineage.result = null
    lineage.error = ''
  }

  async function analyzeLineage() {
    lineage.error = ''
    lineage.result = null
    try {
      if (lineage.sqlFile || lineage.schemaFiles.length) {
        const form = new FormData()
        form.append('sql', lineage.sql)
        form.append('dialect', lineage.dialect)
        form.append('schema_datasource_id', lineage.schemaDatasourceId)
        form.append('schema_name', lineage.schemaName)
        form.append('schema_table_filter', lineage.schemaTableFilter)
        form.append('schema_only_sql_tables', lineage.schemaOnlySqlTables ? 'true' : '')
        form.append('schema_dialect', lineage.schemaDialect)
        if (lineage.sqlFile) form.append('sql_file', lineage.sqlFile)
        lineage.schemaFiles.forEach((file) => form.append('schema_file', file))
        lineage.result = await apiForm('/api/lineage/analyze-form', form)
      } else {
        lineage.result = await apiJson('/api/lineage/analyze', 'POST', {
          sql: lineage.sql,
          dialect: lineage.dialect,
          schema_datasource_id: lineage.schemaDatasourceId,
          schema_name: lineage.schemaName,
          schema_table_filter: lineage.schemaTableFilter,
          schema_only_sql_tables: lineage.schemaOnlySqlTables ? 'true' : '',
          schema_dialect: lineage.schemaDialect,
          schema: '',
        })
      }
    } catch (error) {
      lineage.error = error.message
    }
  }

  return { lineage, resetResult, analyzeLineage }
})
