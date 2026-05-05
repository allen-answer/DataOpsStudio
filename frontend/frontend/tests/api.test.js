// api.js —— versionPath 重写 + project_id 自动追加 + auth header + 401 跳 login。
import { describe, it, expect, vi, beforeEach } from 'vitest'

describe('api.js', () => {
  // 每个 case 重新 import 以隔离 module-level state
  beforeEach(() => {
    vi.resetModules()
    localStorage.clear()
    // jsdom location.hash mutation 用 set hash 而不是 reload
    window.location.hash = ''
  })

  it('apiGet 自动加 /api/v1/ 前缀（path 重写）', async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true, status: 200,
      headers: { get: () => 'application/json' },
      json: async () => ({ ok: 1 }),
    }))
    globalThis.fetch = fetchMock
    const { apiGet } = await import('../src/api')
    await apiGet('/api/datasources')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/datasources')
  })

  it('已带 /api/v1/ 前缀不重写', async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true, status: 200,
      headers: { get: () => 'application/json' },
      json: async () => ({}),
    }))
    globalThis.fetch = fetchMock
    const { apiGet } = await import('../src/api')
    await apiGet('/api/v1/tasks')
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/tasks')
  })

  it('当前 project_id 不空 + path 命中 PROJECT_AWARE_PATHS → 自动追加 ?project_id=', async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true, status: 200,
      headers: { get: () => 'application/json' },
      json: async () => ({}),
    }))
    globalThis.fetch = fetchMock
    localStorage.setItem('dataops.project_id', 'proj-x')
    const { apiGet } = await import('../src/api')
    await apiGet('/api/datasources')
    expect(fetchMock.mock.calls[0][0]).toContain('project_id=proj-x')
  })

  it('path 不在 PROJECT_AWARE_PATHS（如 /api/projects）不追加', async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true, status: 200,
      headers: { get: () => 'application/json' },
      json: async () => ({}),
    }))
    globalThis.fetch = fetchMock
    localStorage.setItem('dataops.project_id', 'proj-x')
    const { apiGet } = await import('../src/api')
    await apiGet('/api/projects')
    expect(fetchMock.mock.calls[0][0]).not.toContain('project_id=')
  })

  it('apiGet 自动带 Authorization: Bearer <token>', async () => {
    let captured = null
    const fetchMock = vi.fn(async (url, opts) => {
      captured = opts
      return {
        ok: true, status: 200,
        headers: { get: () => 'application/json' },
        json: async () => ({}),
      }
    })
    globalThis.fetch = fetchMock
    localStorage.setItem('dataops.token', 'tok-secret')
    const { apiGet } = await import('../src/api')
    await apiGet('/api/datasources')
    expect(captured.headers.Authorization).toBe('Bearer tok-secret')
  })

  it('apiJson 走 method + JSON body', async () => {
    let captured = null
    const fetchMock = vi.fn(async (url, opts) => {
      captured = { url, opts }
      return {
        ok: true, status: 200,
        headers: { get: () => 'application/json' },
        json: async () => ({ id: 'new' }),
      }
    })
    globalThis.fetch = fetchMock
    const { apiJson } = await import('../src/api')
    await apiJson('/api/tasks', 'POST', { name: 'x' })
    expect(captured.opts.method).toBe('POST')
    expect(captured.opts.body).toBe(JSON.stringify({ name: 'x' }))
    expect(captured.opts.headers['Content-Type']).toBe('application/json')
  })

  it('401 → 清 token + 跳 #/login', async () => {
    const fetchMock = vi.fn(async () => ({
      ok: false, status: 401,
      headers: { get: () => 'text/plain' },
      json: async () => ({}),
      text: async () => 'unauthorized',
    }))
    globalThis.fetch = fetchMock
    localStorage.setItem('dataops.token', 'expired')
    const { apiGet } = await import('../src/api')
    await expect(apiGet('/api/tasks')).rejects.toThrow()
    expect(localStorage.getItem('dataops.token')).toBeNull()
    expect(window.location.hash).toBe('#/login')
  })
})
