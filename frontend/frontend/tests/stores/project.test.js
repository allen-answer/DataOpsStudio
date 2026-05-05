// useProjectStore —— project 切换 / 持久化 / "全部项目"回退。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { useProjectStore } from '../../src/stores/project'

vi.mock('../../src/api', () => ({
  apiGet: vi.fn(),
  apiJson: vi.fn(),
}))
import { apiGet, apiJson } from '../../src/api'

describe('useProjectStore', () => {
  beforeEach(() => vi.clearAllMocks())

  it('默认 currentProjectId 是空串（"全部项目"）', () => {
    const store = useProjectStore()
    expect(store.currentProjectId).toBe('')
    expect(store.currentProject).toBeNull()
  })

  it('setProject 持久化到 localStorage；切空清掉 key', () => {
    const store = useProjectStore()
    store.setProject('proj-a')
    expect(localStorage.getItem('dataops.project_id')).toBe('proj-a')
    store.setProject('')
    expect(localStorage.getItem('dataops.project_id')).toBeNull()
  })

  it('reload 拉项目列表填 projects', async () => {
    apiGet.mockResolvedValueOnce([
      { id: 'p1', name: '项目一' },
      { id: 'p2', name: '项目二' },
    ])
    const store = useProjectStore()
    await store.reload()
    expect(store.projects).toHaveLength(2)
    expect(apiGet).toHaveBeenCalledWith('/api/projects')
  })

  it('当前选中项目从列表消失 → 自动回退"全部"', async () => {
    const store = useProjectStore()
    store.setProject('p-deleted')
    apiGet.mockResolvedValueOnce([{ id: 'p1', name: 'still here' }])
    await store.reload()
    expect(store.currentProjectId).toBe('')
    expect(localStorage.getItem('dataops.project_id')).toBeNull()
  })

  it('reload API 抛错 → projects 退化为空数组（不抛）', async () => {
    apiGet.mockRejectedValueOnce(new Error('network down'))
    const store = useProjectStore()
    await store.reload()
    expect(store.projects).toEqual([])
  })

  it('deleteProject 当前选中也被删 → 自动跳"全部"', async () => {
    apiJson.mockResolvedValueOnce({})
    apiGet.mockResolvedValueOnce([])
    const store = useProjectStore()
    store.setProject('p-mine')
    await store.deleteProject('p-mine')
    expect(store.currentProjectId).toBe('')
    expect(apiJson).toHaveBeenCalledWith('/api/projects/p-mine', 'DELETE')
  })
})
