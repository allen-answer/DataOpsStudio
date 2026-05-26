// useSqlTemplatesStore — v0.4 模板库 store 单元测试。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('../../src/api', () => ({
  apiGet: vi.fn(),
  apiJson: vi.fn(),
}))
import { apiGet, apiJson } from '../../src/api'
import { useSqlTemplatesStore } from '../../src/stores/sqlTemplates'

describe('useSqlTemplatesStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('loadTemplates 写回 templates + 拼接 filters 到 query string', async () => {
    apiGet.mockResolvedValueOnce({
      items: [
        { id: 'builtin:c', name: 'C', tags: [], db_types: ['all'], project_id: '', risk_level: 'low', sql: 'SELECT 1', created_by: 'system', created_at: '', updated_at: '', builtin: true, description: '' },
        { id: 'u-1', name: 'my', tags: ['adhoc'], db_types: ['mysql'], project_id: '', risk_level: 'low', sql: 'SELECT 2', created_by: 'alice', created_at: '', updated_at: '', builtin: false, description: '' },
      ],
      count: 2,
    })
    const store = useSqlTemplatesStore()
    store.filters.q = 'foo'
    store.filters.db_type = 'mysql'
    await store.loadTemplates()
    expect(store.templates).toHaveLength(2)
    expect(store.builtinCount).toBe(1)
    expect(store.userCount).toBe(1)
    expect(apiGet).toHaveBeenCalledWith(expect.stringContaining('q=foo'))
    expect(apiGet).toHaveBeenCalledWith(expect.stringContaining('db_type=mysql'))
  })

  it('createTemplate POST 到 endpoint + 把新模板 unshift 进列表', async () => {
    const newT = { id: 'u-new', name: 'new', tags: [], db_types: ['all'], project_id: '', risk_level: 'low', sql: 'SELECT 1', created_by: 'u', created_at: '', updated_at: '', builtin: false, description: '' }
    apiJson.mockResolvedValueOnce(newT)
    const store = useSqlTemplatesStore()
    store.templates = [{ id: 'old', name: 'old' }]
    const r = await store.createTemplate({ name: 'new', sql: 'SELECT 1' })
    expect(r.id).toBe('u-new')
    expect(store.templates[0].id).toBe('u-new')  // unshift
    expect(apiJson).toHaveBeenCalledWith('/api/sql-templates', 'POST', { name: 'new', sql: 'SELECT 1' })
  })

  it('updateTemplate 用返回值替换列表里的对应项', async () => {
    const store = useSqlTemplatesStore()
    store.templates = [
      { id: 'a', name: 'old', tags: [], db_types: ['all'], project_id: '', risk_level: 'low', sql: 'x', created_by: 'u', created_at: '', updated_at: '', builtin: false, description: '' },
    ]
    apiJson.mockResolvedValueOnce({ id: 'a', name: 'new', tags: [], db_types: ['all'], project_id: '', risk_level: 'low', sql: 'y', created_by: 'u', created_at: '', updated_at: '', builtin: false, description: '' })
    await store.updateTemplate('a', { name: 'new', sql: 'y' })
    expect(store.templates[0].name).toBe('new')
    expect(store.templates[0].sql).toBe('y')
  })

  it('deleteTemplate 从列表移除', async () => {
    const store = useSqlTemplatesStore()
    store.templates = [{ id: 'a', name: 'A' }, { id: 'b', name: 'B' }]
    apiJson.mockResolvedValueOnce({ ok: true })
    await store.deleteTemplate('a')
    expect(store.templates.map(t => t.id)).toEqual(['b'])
  })

  it('importTemplates 调 import endpoint + 重拉列表', async () => {
    const store = useSqlTemplatesStore()
    apiJson.mockResolvedValueOnce({ ok: true, created: 2, skipped: 0, errors: 0 })
    apiGet.mockResolvedValueOnce({ items: [], count: 0 })  // 内部 loadTemplates 重拉
    const report = await store.importTemplates([
      { name: 'a', sql: 'SELECT 1' },
      { name: 'b', sql: 'SELECT 2' },
    ], false)
    expect(report.created).toBe(2)
    expect(apiJson).toHaveBeenCalledWith('/api/sql-templates/import', 'POST', {
      templates: [
        { name: 'a', sql: 'SELECT 1' },
        { name: 'b', sql: 'SELECT 2' },
      ],
      overwrite_by_name: false,
    })
  })

  it('exportTemplates 返回 templates list', async () => {
    apiGet.mockResolvedValueOnce({
      templates: [{ name: 'x', sql: 'y' }],
      count: 1,
    })
    const store = useSqlTemplatesStore()
    const result = await store.exportTemplates(false)
    expect(result).toEqual([{ name: 'x', sql: 'y' }])
    expect(apiGet).toHaveBeenCalledWith(expect.stringContaining('include_builtin=false'))
  })

  it('exportTemplates 含 builtin 时 include_builtin=true', async () => {
    apiGet.mockResolvedValueOnce({ templates: [], count: 0 })
    const store = useSqlTemplatesStore()
    await store.exportTemplates(true)
    expect(apiGet).toHaveBeenCalledWith(expect.stringContaining('include_builtin=true'))
  })

  it('loadTemplates 失败时设置 lastError 且不清 templates', async () => {
    const store = useSqlTemplatesStore()
    store.templates = [{ id: 'x', name: 'old' }]
    apiGet.mockRejectedValueOnce(new Error('boom'))
    await store.loadTemplates()
    expect(store.lastError).toContain('boom')
    // 旧 templates 仍在(loadTemplates catch 不清)
    expect(store.templates.length).toBe(1)
  })
})
