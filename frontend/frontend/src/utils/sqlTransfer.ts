/**
 * 跨视图传 SQL 的轻量桥 —— SqlWorkbench 发起方写,LineageWorkbench /
 * SqlDiagnosis(/ Compare backlog)在 onMounted 读 + 清。
 *
 * 走 sessionStorage 而非 URL query:
 *   - SQL 经常含特殊字符,URL encode 后丑
 *   - 长 SQL 可能超 URL 上限
 *   - 用户用浏览器分享链接时不希望意外把 SQL payload 带走
 *
 * 走 sessionStorage 而非 localStorage:
 *   - 跨 tab 状态隔离;不污染下次会话
 *   - 浏览器关闭自动清掉
 */
const TRANSFER_KEY = 'dataops.sqlTransfer'

export interface SqlTransfer {
  sql: string
  datasourceId?: string
  source?: string  // 'sql-workbench' / 'history' / ...,目的地展示溯源用
  // v0.5 扩展元数据(全可选,向后兼容):优化工作台用来在顶部展示
  //   "来源: SQL 工作台 · console: 月报查询 · MySQL · 耗时 3.2s"
  consoleId?: string
  consoleName?: string
  datasourceName?: string
  datasourceDbType?: string
  elapsedMs?: number
  executedAt?: string  // ISO 8601
}

export function setSqlTransfer(data: SqlTransfer): void {
  try {
    sessionStorage.setItem(TRANSFER_KEY, JSON.stringify(data))
  } catch {
    // sessionStorage 可能在 incognito + storage full 时抛
  }
}

/**
 * 取一次性的 SQL transfer payload。读完即清,防重复消费。
 * 调用方:`onMounted(() => { const t = takeSqlTransfer(); if (t) inputSql.value = t.sql })`
 */
export function takeSqlTransfer(): SqlTransfer | null {
  let raw: string | null = null
  try {
    raw = sessionStorage.getItem(TRANSFER_KEY)
    if (raw) sessionStorage.removeItem(TRANSFER_KEY)
  } catch {
    return null
  }
  if (!raw) return null
  try {
    return JSON.parse(raw) as SqlTransfer
  } catch {
    return null
  }
}
