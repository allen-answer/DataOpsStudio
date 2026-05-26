// useSqlWorkbenchStore —— Phase 2 store 单元测试。
// 走 vitest + mocked apiGet/apiJson(不打真 API),验证 consoles 数组管理 /
// execute / activeConsole 计算属性 / history reload。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('../../src/api', () => ({
  apiGet: vi.fn(),
  apiJson: vi.fn(),
}))
import { apiGet, apiJson } from '../../src/api'
import { useSqlWorkbenchStore } from '../../src/stores/sqlWorkbench'

describe('useSqlWorkbenchStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('loadConsoles 从后端拉 + 自动选第一个', async () => {
    apiGet.mockResolvedValueOnce({
      items: [
        { id: 'c1', name: 'A', datasource_id: '', sql: '', project_id: '', owner_user_id: 'u', created_at: '', updated_at: '' },
        { id: 'c2', name: 'B', datasource_id: '', sql: '', project_id: '', owner_user_id: 'u', created_at: '', updated_at: '' },
      ],
    })
    const store = useSqlWorkbenchStore()
    await store.loadConsoles()
    expect(store.consoles).toHaveLength(2)
    expect(store.activeConsoleId).toBe('c1')
    expect(store.activeConsole?.name).toBe('A')
  })

  it('createConsole 把返回的 console push 进列表并设为 active', async () => {
    const newC = { id: 'new', name: 'Console 1', datasource_id: '', sql: '', project_id: '', owner_user_id: '', created_at: '', updated_at: '' }
    apiJson.mockResolvedValueOnce(newC)
    const store = useSqlWorkbenchStore()
    const ret = await store.createConsole({ name: 'Console 1' })
    expect(ret).toEqual(newC)
    expect(store.consoles).toContainEqual(newC)
    expect(store.activeConsoleId).toBe('new')
  })

  it('updateConsole 替换列表中的对应 console', async () => {
    const store = useSqlWorkbenchStore()
    apiGet.mockResolvedValueOnce({ items: [{ id: 'c1', name: 'old', datasource_id: '', sql: '', project_id: '', owner_user_id: 'u', created_at: '', updated_at: '' }] })
    await store.loadConsoles()
    apiJson.mockResolvedValueOnce({ id: 'c1', name: 'new', datasource_id: 'ds-1', sql: 'SELECT 1', project_id: '', owner_user_id: 'u', created_at: '', updated_at: '' })
    await store.updateConsole('c1', { name: 'new', sql: 'SELECT 1', datasource_id: 'ds-1' })
    expect(store.consoles[0].name).toBe('new')
    expect(store.consoles[0].sql).toBe('SELECT 1')
  })

  it('deleteConsole 移除并切到剩下的第一个', async () => {
    const store = useSqlWorkbenchStore()
    apiGet.mockResolvedValueOnce({ items: [
      { id: 'c1', name: 'A', datasource_id: '', sql: '', project_id: '', owner_user_id: 'u', created_at: '', updated_at: '' },
      { id: 'c2', name: 'B', datasource_id: '', sql: '', project_id: '', owner_user_id: 'u', created_at: '', updated_at: '' },
    ] })
    await store.loadConsoles()
    expect(store.activeConsoleId).toBe('c1')

    apiJson.mockResolvedValueOnce({ ok: true })
    await store.deleteConsole('c1')
    expect(store.consoles.map(c => c.id)).toEqual(['c2'])
    expect(store.activeConsoleId).toBe('c2')
  })

  it('execute 把 result 存进按 console_id 索引的 map + reload history (v0.2 envelope)', async () => {
    const store = useSqlWorkbenchStore()
    // v0.2:envelope 顶层有 execution_id + status,done 时平铺 success/columns/rows
    apiJson.mockResolvedValueOnce({
      execution_id: 'exe-1', status: 'done',
      success: true, columns: ['x'], rows: [[1]],
      row_count: 1, elapsed_ms: 5, truncated: false,
    })
    apiGet.mockResolvedValueOnce({ items: [] })

    const ret = await store.execute('c1', { datasource_id: 'ds-1', sql: 'SELECT 1' })
    expect(ret.success).toBe(true)
    expect(store.results.c1.success).toBe(true)
    expect(store.results.c1.columns).toEqual(['x'])
    expect(store.running.c1).toBe(false)
  })

  it('execute 跑过程中 running 标志', async () => {
    const store = useSqlWorkbenchStore()
    let resolvePending = () => {}
    apiJson.mockImplementationOnce(() => new Promise(r => { resolvePending = r }))
    const p = store.execute('cx', { datasource_id: 'd', sql: 'SELECT 1' })
    expect(store.running.cx).toBe(true)
    resolvePending({ execution_id: 'exe-x', status: 'done', success: true, columns: [], rows: [], row_count: 0, elapsed_ms: 0, truncated: false })
    apiGet.mockResolvedValueOnce({ items: [] })
    await p
    expect(store.running.cx).toBe(false)
  })

  it('execute running → poll → done (v0.2 异步)', async () => {
    const store = useSqlWorkbenchStore()
    // 第 1 次:返 running
    apiJson.mockResolvedValueOnce({ execution_id: 'exe-r', status: 'running' })
    // poll(apiGet):返 done
    apiGet.mockResolvedValueOnce({
      execution_id: 'exe-r', status: 'done',
      success: true, columns: ['x'], rows: [[1]], row_count: 1, elapsed_ms: 12, truncated: false,
    })
    // 异步刷历史
    apiGet.mockResolvedValueOnce({ items: [] })

    const ret = await store.execute('cy', { datasource_id: 'd', sql: 'SELECT 1' })
    expect(ret).not.toBeNull()
    expect(ret.success).toBe(true)
    expect(store.results.cy.row_count).toBe(1)
  })

  it('cancelExecution POST cancel endpoint', async () => {
    const store = useSqlWorkbenchStore()
    // 注入一个 in-flight 的 console
    store.currentExecutionId['cz'] = 'exe-cancel'
    apiJson.mockResolvedValueOnce({ ok: true })
    await store.cancelExecution('cz')
    expect(apiJson).toHaveBeenCalledWith(
      '/api/sql-workbench/executions/exe-cancel/cancel', 'POST', {},
    )
  })

  it('formatSql POST format endpoint', async () => {
    const store = useSqlWorkbenchStore()
    apiJson.mockResolvedValueOnce({ success: true, formatted_sql: 'SELECT 1', dialect: 'mysql' })
    const r = await store.formatSql('select 1', 'ds-1')
    expect(r.formatted_sql).toBe('SELECT 1')
    expect(apiJson).toHaveBeenCalledWith('/api/sql-workbench/format', 'POST', {
      datasource_id: 'ds-1', sql: 'select 1',
    })
  })

  it('explain 把 result 存进 explainResults map', async () => {
    const store = useSqlWorkbenchStore()
    apiJson.mockResolvedValueOnce({
      success: true, dialect: 'mysql', columns: ['id'], rows: [[1]],
      explain_sql: 'EXPLAIN SELECT 1', elapsed_ms: 3, unsupported: false,
    })
    const r = await store.explain('ce', { datasource_id: 'd', sql: 'SELECT 1' })
    expect(r.success).toBe(true)
    expect(store.explainResults.ce.dialect).toBe('mysql')
  })

  it('loadHistory 接 datasource_id query', async () => {
    const store = useSqlWorkbenchStore()
    apiGet.mockResolvedValueOnce({ items: [{ id: 'h1', sql: 'SELECT 1', success: true }] })
    await store.loadHistory('ds-1', 50)
    expect(apiGet).toHaveBeenCalledWith(expect.stringContaining('datasource_id=ds-1'))
    expect(apiGet).toHaveBeenCalledWith(expect.stringContaining('limit=50'))
    expect(store.history).toHaveLength(1)
  })

  it('setActive 只接受已存在的 id', () => {
    const store = useSqlWorkbenchStore()
    store.consoles.push({ id: 'a', name: 'A', datasource_id: '', sql: '', project_id: '', owner_user_id: '', created_at: '', updated_at: '' })
    store.setActive('a')
    expect(store.activeConsoleId).toBe('a')
    store.setActive('nonexistent')
    expect(store.activeConsoleId).toBe('a')
  })

  // ─── 本地草稿(localStorage) ─────────────────────────────────────────
  describe('local draft', () => {
    beforeEach(() => {
      // jsdom 自带 localStorage,清掉前一测试残留
      localStorage.clear()
    })

    it('saveDraft 写 localStorage,loadDraft 读回来', () => {
      const store = useSqlWorkbenchStore()
      store.saveDraft('c1', 'SELECT 1;')
      expect(store.loadDraft('c1')).toBe('SELECT 1;')
    })

    it('saveDraft 空字符串等价于删除', () => {
      const store = useSqlWorkbenchStore()
      store.saveDraft('c1', 'select')
      store.saveDraft('c1', '')
      expect(store.loadDraft('c1')).toBe('')
    })

    it('clearDraft 删除指定 console 的草稿', () => {
      const store = useSqlWorkbenchStore()
      store.saveDraft('c1', 'A')
      store.saveDraft('c2', 'B')
      store.clearDraft('c1')
      expect(store.loadDraft('c1')).toBe('')
      expect(store.loadDraft('c2')).toBe('B')
    })

    it('deleteConsole 顺手清掉本地草稿', async () => {
      const store = useSqlWorkbenchStore()
      apiGet.mockResolvedValueOnce({ items: [
        { id: 'd1', name: 'A', datasource_id: '', sql: '', project_id: '', owner_user_id: 'u', created_at: '', updated_at: '' },
      ] })
      await store.loadConsoles()
      store.saveDraft('d1', 'SELECT 99;')
      apiJson.mockResolvedValueOnce({ ok: true })
      await store.deleteConsole('d1')
      expect(store.loadDraft('d1')).toBe('')
    })

    it('空 consoleId 不抛错', () => {
      const store = useSqlWorkbenchStore()
      expect(() => store.saveDraft('', 'x')).not.toThrow()
      expect(store.loadDraft('')).toBe('')
      expect(() => store.clearDraft('')).not.toThrow()
    })
  })

  // ─── metadata cache:cached_at 字段透传 + refresh 路径(v0.3) ─────────
  describe('metadata cache v0.3', () => {
    it('loadSchemas 把 cached_at 写进 metadataByDs.schemasCachedAt', async () => {
      const store = useSqlWorkbenchStore()
      apiGet.mockResolvedValueOnce({
        items: [{ name: 'public' }],
        cached_at: '2026-05-26T03:00:00+00:00',
        source: 'cache',
      })
      await store.loadSchemas('ds-1')
      expect(store.metadataByDs['ds-1'].schemasCachedAt).toBe('2026-05-26T03:00:00+00:00')
      expect(store.metadataByDs['ds-1'].schemas).toHaveLength(1)
    })

    it('loadSchemas refresh=true 时 URL 带 refresh=true', async () => {
      const store = useSqlWorkbenchStore()
      apiGet.mockResolvedValueOnce({ items: [], cached_at: null, source: 'live' })
      await store.loadSchemas('ds-1', true)
      expect(apiGet).toHaveBeenCalledWith(expect.stringContaining('refresh=true'))
    })

    it('loadTables 把 cached_at 写到 schema.tablesCachedAt', async () => {
      const store = useSqlWorkbenchStore()
      apiGet.mockResolvedValueOnce({ items: [{ name: 'public' }], cached_at: '2026-05-26T03:00:00+00:00' })
      await store.loadSchemas('ds-1')
      apiGet.mockResolvedValueOnce({
        items: [{ name: 'users', schema: 'public' }],
        cached_at: '2026-05-26T03:05:00+00:00',
        source: 'cache',
      })
      await store.loadTables('ds-1', 'public')
      const sch = store.metadataByDs['ds-1'].schemas[0]
      expect(sch.tablesCachedAt).toBe('2026-05-26T03:05:00+00:00')
      expect(sch.tables).toEqual([{ name: 'users', schema: 'public' }])
    })

    it('loadTables stale + 旧值非空时保留旧值', async () => {
      const store = useSqlWorkbenchStore()
      apiGet.mockResolvedValueOnce({ items: [{ name: 'public' }] })
      await store.loadSchemas('ds-1')
      // 第一次拉成功
      apiGet.mockResolvedValueOnce({ items: [{ name: 'users', schema: 'public' }] })
      await store.loadTables('ds-1', 'public')
      // 第二次 refresh:后端 stale + 返旧值
      apiGet.mockResolvedValueOnce({
        items: [{ name: 'users', schema: 'public' }],
        source: 'stale',
        error: 'db down',
      })
      await store.loadTables('ds-1', 'public', true)
      const sch = store.metadataByDs['ds-1'].schemas[0]
      expect(sch.tables).toEqual([{ name: 'users', schema: 'public' }])
    })

    it('refreshAllMetadata 调 POST refresh + 重拉 schemas', async () => {
      const store = useSqlWorkbenchStore()
      // 先 load schemas + tables 一次
      apiGet.mockResolvedValueOnce({ items: [{ name: 'public' }], cached_at: 'x' })
      await store.loadSchemas('ds-1')
      apiGet.mockResolvedValueOnce({ items: [{ name: 'users', schema: 'public' }] })
      await store.loadTables('ds-1', 'public')
      // 执行 refreshAll
      apiJson.mockResolvedValueOnce({ ok: true })
      apiGet.mockResolvedValueOnce({ items: [{ name: 'public' }], cached_at: 'new-time' })
      await store.refreshAllMetadata('ds-1')
      expect(apiJson).toHaveBeenCalledWith(
        '/api/sql-workbench/metadata/refresh', 'POST',
        { datasource_id: 'ds-1', scope: 'all' },
      )
      // 重拉后 schemas 的 tables 字段被清空(因为后端 cache 被清,等用户再展开才重拉)
      const sch = store.metadataByDs['ds-1'].schemas[0]
      expect(sch.tables).toBeUndefined()
    })

    it('loadCacheSummary 写回 scopes', async () => {
      const store = useSqlWorkbenchStore()
      apiGet.mockResolvedValueOnce({
        datasource_id: 'ds-1',
        scopes: { schemas: '2026-05-26T03:00:00+00:00', tables: null, columns: null, indexes: null, views: null },
      })
      await store.loadCacheSummary('ds-1')
      expect(store.metadataByDs['ds-1'].cacheSummary.schemas).toBeTruthy()
      expect(store.metadataByDs['ds-1'].cacheSummary.tables).toBeNull()
    })
  })

  // ─── 对象搜索 ─────────────────────────────────────────────────────────
  describe('searchMetadata', () => {
    it('打 /metadata/search 端点 + 写回 searchResults', async () => {
      const store = useSqlWorkbenchStore()
      apiGet.mockResolvedValueOnce({
        items: [
          { kind: 'table', schema: 'public', table: 'users', score: 50, snippet: 'public.users' },
        ],
        count: 1,
      })
      const r = await store.searchMetadata('ds-1', 'users')
      expect(r).toHaveLength(1)
      expect(r[0].kind).toBe('table')
      expect(store.searchResults['ds-1']).toEqual(r)
      expect(apiGet).toHaveBeenCalledWith(expect.stringContaining('/api/sql-workbench/metadata/search'))
      expect(apiGet).toHaveBeenCalledWith(expect.stringContaining('q=users'))
    })

    it('空 query 直接返 [],不打后端', async () => {
      const store = useSqlWorkbenchStore()
      const r = await store.searchMetadata('ds-1', '')
      expect(r).toEqual([])
      expect(apiGet).not.toHaveBeenCalled()
    })

    it('kinds 参数透传到 query string', async () => {
      const store = useSqlWorkbenchStore()
      apiGet.mockResolvedValueOnce({ items: [], count: 0 })
      await store.searchMetadata('ds-1', 'foo', 'table,column')
      expect(apiGet).toHaveBeenCalledWith(expect.stringContaining('kinds=table%2Ccolumn'))
    })

    it('searchLoading 在调用中标 true,完成后 false', async () => {
      const store = useSqlWorkbenchStore()
      let resolvePending = () => {}
      apiGet.mockImplementationOnce(() => new Promise(r => { resolvePending = r }))
      const p = store.searchMetadata('ds-1', 'foo')
      expect(store.searchLoading['ds-1']).toBe(true)
      resolvePending({ items: [], count: 0 })
      await p
      expect(store.searchLoading['ds-1']).toBe(false)
    })
  })

  // ─── 表详情 ─────────────────────────────────────────────────────────
  describe('loadTableDetail', () => {
    it('并发拉 columns + indexes + ddl', async () => {
      const store = useSqlWorkbenchStore()
      // 3 个 apiGet 调用并发
      apiGet.mockResolvedValueOnce({ items: [{ name: 'id', data_type: 'INT' }] })  // columns
      apiGet.mockResolvedValueOnce({ items: [{ index_name: 'PRIMARY', column_name: 'id', non_unique: 0, seq_in_index: 1, index_type: 'BTREE' }] })  // indexes
      apiGet.mockResolvedValueOnce({ supported: true, ddl: 'CREATE TABLE users (id INT);' })  // ddl
      const detail = await store.loadTableDetail('ds-1', 'public', 'users')
      expect(detail.columns).toHaveLength(1)
      expect(detail.indexes).toHaveLength(1)
      expect(detail.ddl).toBe('CREATE TABLE users (id INT);')
      expect(detail.ddlSupported).toBe(true)
    })

    it('DDL 不支持时 ddlSupported=false', async () => {
      const store = useSqlWorkbenchStore()
      apiGet.mockResolvedValueOnce({ items: [] })
      apiGet.mockResolvedValueOnce({ items: [] })
      apiGet.mockResolvedValueOnce({ supported: false, ddl: null, error: '不支持' })
      const detail = await store.loadTableDetail('ds-1', 'public', 'users')
      expect(detail.ddlSupported).toBe(false)
      expect(detail.ddl).toBeNull()
    })

    it('某个子请求挂掉不影响其他', async () => {
      const store = useSqlWorkbenchStore()
      apiGet.mockResolvedValueOnce({ items: [{ name: 'id' }] })
      apiGet.mockRejectedValueOnce(new Error('indexes endpoint failed'))
      apiGet.mockResolvedValueOnce({ supported: true, ddl: 'CREATE TABLE x ()' })
      const detail = await store.loadTableDetail('ds-1', 'public', 'users')
      expect(detail.columns).toHaveLength(1)
      expect(detail.indexes).toEqual([])
      expect(detail.ddl).toBe('CREATE TABLE x ()')
    })
  })

  // ─── 列名补全:loadColumns 写回 metadata ─────────────────────────────
  describe('loadColumns', () => {
    it('拉某表列名并写回 metadata.schemas[].tables[].columns', async () => {
      const store = useSqlWorkbenchStore()
      // 先 loadSchemas + loadTables,把基础结构建起来
      apiGet.mockResolvedValueOnce({ items: [{ name: 'public' }] })
      await store.loadSchemas('ds-1')
      apiGet.mockResolvedValueOnce({ items: [{ name: 'users', schema: 'public' }] })
      await store.loadTables('ds-1', 'public')
      // 然后拉 columns
      apiGet.mockResolvedValueOnce({ items: [{ name: 'id' }, { name: 'email' }] })
      const cols = await store.loadColumns('ds-1', 'public', 'users')
      expect(cols).toEqual(['id', 'email'])
      const table = store.metadataByDs['ds-1'].schemas[0].tables[0]
      expect(table.columns).toEqual(['id', 'email'])
    })

    it('重复 loadColumns 不再打后端', async () => {
      const store = useSqlWorkbenchStore()
      apiGet.mockResolvedValueOnce({ items: [{ name: 'public' }] })
      await store.loadSchemas('ds-1')
      apiGet.mockResolvedValueOnce({ items: [{ name: 'orders', schema: 'public' }] })
      await store.loadTables('ds-1', 'public')
      apiGet.mockResolvedValueOnce({ items: [{ name: 'id' }] })
      await store.loadColumns('ds-1', 'public', 'orders')
      const callCountBefore = apiGet.mock.calls.length
      // 第二次:走缓存,不应再调 apiGet
      const cols = await store.loadColumns('ds-1', 'public', 'orders')
      expect(cols).toEqual(['id'])
      expect(apiGet.mock.calls.length).toBe(callCountBefore)
    })

    it('schema/table 不存在时返回空数组,不抛错', async () => {
      const store = useSqlWorkbenchStore()
      const cols = await store.loadColumns('nonexistent-ds', 'x', 'y')
      expect(cols).toEqual([])
    })
  })
})
