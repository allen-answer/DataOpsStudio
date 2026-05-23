// useAuthStore —— role checks + persist + logout 行为。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { useAuthStore } from '../../src/stores/auth'

// 必须 mock api 模块（store login 会调 apiJson）
vi.mock('../../src/api', () => ({
  apiJson: vi.fn(),
}))
import { apiJson } from '../../src/api'

describe('useAuthStore', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('isLoggedIn / isAdmin / isEditor 默认全 false（无 token / 无 user）', () => {
    const store = useAuthStore()
    expect(store.isLoggedIn).toBe(false)
    expect(store.isAdmin).toBe(false)
    expect(store.isEditor).toBe(false)
  })

  it('login 成功后 token / user 落 localStorage + isAdmin 生效', async () => {
    apiJson.mockResolvedValueOnce({
      access_token: 'tok-abc',
      user: { username: 'admin', role: 'admin' },
    })
    const store = useAuthStore()
    await store.login('admin', 'admin')
    expect(store.token).toBe('tok-abc')
    expect(store.isLoggedIn).toBe(true)
    expect(store.isAdmin).toBe(true)
    expect(store.isEditor).toBe(true)  // admin 也算 editor
    expect(localStorage.getItem('dataops.token')).toBe('tok-abc')
    expect(JSON.parse(localStorage.getItem('dataops.user'))).toMatchObject({ username: 'admin' })
  })

  it('editor 角色：isEditor=true 但 isAdmin=false', async () => {
    apiJson.mockResolvedValueOnce({
      access_token: 'tok',
      user: { username: 'alice', role: 'editor' },
    })
    const store = useAuthStore()
    await store.login('alice', 'alice123')
    expect(store.isAdmin).toBe(false)
    expect(store.isEditor).toBe(true)
  })

  it('viewer 角色：isAdmin / isEditor 都 false', async () => {
    apiJson.mockResolvedValueOnce({
      access_token: 'tok',
      user: { username: 'bob', role: 'viewer' },
    })
    const store = useAuthStore()
    await store.login('bob', 'bob123')
    expect(store.isAdmin).toBe(false)
    expect(store.isEditor).toBe(false)
    expect(store.isLoggedIn).toBe(true)
  })

  it('logout 清空 token / user / localStorage', async () => {
    apiJson.mockResolvedValueOnce({
      access_token: 'tok',
      user: { username: 'admin', role: 'admin' },
    })
    // logout 还会 fire-and-forget 调 POST /api/auth/logout 服务端吊销 token
    apiJson.mockResolvedValueOnce({ ok: true })
    const store = useAuthStore()
    await store.login('admin', 'admin')
    expect(store.isLoggedIn).toBe(true)
    store.logout()
    expect(apiJson).toHaveBeenCalledWith('/api/auth/logout', 'POST')
    expect(store.token).toBe('')
    expect(store.user).toBeNull()
    expect(store.isLoggedIn).toBe(false)
    expect(localStorage.getItem('dataops.token')).toBeNull()
    expect(localStorage.getItem('dataops.user')).toBeNull()
  })

  it('refreshMe 无 token 直接 return null（不打 API）', async () => {
    const store = useAuthStore()
    const result = await store.refreshMe()
    expect(result).toBeNull()
    expect(apiJson).not.toHaveBeenCalled()
  })
})
