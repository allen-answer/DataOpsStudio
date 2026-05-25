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

  it('execute 把 result 存进按 console_id 索引的 map + reload history', async () => {
    const store = useSqlWorkbenchStore()
    apiJson.mockResolvedValueOnce({
      success: true, columns: ['x'], rows: [[1]],
      row_count: 1, elapsed_ms: 5, truncated: false,
    })
    apiGet.mockResolvedValueOnce({ items: [] })

    const ret = await store.execute('c1', { datasource_id: 'ds-1', sql: 'SELECT 1' })
    expect(ret.success).toBe(true)
    expect(store.results.c1).toEqual(ret)
    expect(store.running.c1).toBe(false)
  })

  it('execute 跑过程中 running 标志', async () => {
    const store = useSqlWorkbenchStore()
    let resolvePending = () => {}
    apiJson.mockImplementationOnce(() => new Promise(r => { resolvePending = r }))
    const p = store.execute('cx', { datasource_id: 'd', sql: 'SELECT 1' })
    expect(store.running.cx).toBe(true)
    resolvePending({ success: true, columns: [], rows: [], row_count: 0, elapsed_ms: 0, truncated: false })
    apiGet.mockResolvedValueOnce({ items: [] })
    await p
    expect(store.running.cx).toBe(false)
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
})
