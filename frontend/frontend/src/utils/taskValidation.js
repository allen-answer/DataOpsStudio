/**
 * Compare task draft 校验。前端做即时反馈，后端 _validate_compare_inputs 做权威校验。
 *
 * 规则与 app/models/compare.py:_validate_compare_inputs 一一对应：
 * - SQL 端：必须 source_id / source_sql；double 模式下 target_kind=sql 还要 target_sql
 * - Excel 端：必须 source_excel_path / target_excel_path
 * - sql_mode='single' + 任一边是 Excel：互斥
 * - stream_compare + 任一边是 Excel：互斥
 * - key_columns 必填
 *
 * 返回 { issues: [{field, level, message, step}] }，level ∈ 'error'|'warning'。
 * - error：阻止保存
 * - warning：提示但不阻止
 * step：'source'/'rules'/'mapping'/'result' —— 给步骤条着色用
 */

const STEP_BY_FIELD = {
  name: 'source', source_id: 'source', target_id: 'source',
  source_sql: 'source', target_sql: 'source',
  source_excel_path: 'source', target_excel_path: 'source',
  sql_mode: 'source', source_kind: 'source', target_kind: 'source',
  key_columns: 'rules',
  stream_compare: 'rules',
}

function parseKeyColumns(raw) {
  if (Array.isArray(raw)) return raw.filter(s => s && s.trim())
  if (typeof raw !== 'string') return []
  return raw.split(',').map(s => s.trim()).filter(Boolean)
}

export function validateTaskDraft(draft) {
  if (!draft) return { issues: [], canSave: true }
  const issues = []
  const push = (field, message, level = 'error') => {
    issues.push({ field, level, message, step: STEP_BY_FIELD[field] || 'source' })
  }

  // 名称
  if (!draft.name || !String(draft.name).trim()) {
    push('name', '请填写任务名称')
  }

  // 源端
  if (draft.source_kind === 'sql') {
    if (!draft.source_id) push('source_id', '请选择源数据源')
    if (!draft.source_sql || !String(draft.source_sql).trim()) {
      push('source_sql', '请填写源 SQL')
    }
  } else if (draft.source_kind === 'excel') {
    if (!draft.source_excel_path) push('source_excel_path', '请上传源 Excel 文件')
  }

  // 目标端
  if (draft.target_kind === 'sql') {
    if (!draft.target_id) push('target_id', '请选择目标数据源')
    if (draft.sql_mode === 'double' && (!draft.target_sql || !String(draft.target_sql).trim())) {
      push('target_sql', '双 SQL 模式下，目标 SQL 必填')
    }
  } else if (draft.target_kind === 'excel') {
    if (!draft.target_excel_path) push('target_excel_path', '请上传目标 Excel 文件')
  }

  // 互斥规则 1：single SQL + 任一边 Excel
  if (draft.sql_mode === 'single' && (draft.source_kind === 'excel' || draft.target_kind === 'excel')) {
    push(
      'sql_mode',
      '单 SQL 模式不支持 Excel 端 —— 单 SQL 是"同一段 SELECT 在源/目标都跑一遍"，Excel 没法跑 SQL。请切换为「双 SQL」模式',
    )
  }

  // 互斥规则 2：stream_compare + 任一边 Excel
  if (draft.stream_compare && (draft.source_kind === 'excel' || draft.target_kind === 'excel')) {
    push(
      'stream_compare',
      '流式分块对比要求两边都是 SQL（按主键有序）；Excel 端不支持，请关闭「流式分块对比」开关',
    )
  }

  // 主键
  if (parseKeyColumns(draft.key_columns).length === 0) {
    push('key_columns', '请填写至少一个主键列（多列用逗号分隔）')
  }

  return {
    issues,
    canSave: issues.every(i => i.level !== 'error'),
  }
}

/** 给定字段名，从 issues 列表里挑出该字段的所有问题（前端 inline 提示用）。 */
export function issuesForField(issues, field) {
  return issues.filter(i => i.field === field)
}
