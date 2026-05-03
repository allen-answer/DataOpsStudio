// 全局 fetch wrapper，自动带 Authorization: Bearer <token>。
// token 由 useAuthStore 管理，落 localStorage：dataops.token；本模块直接读
// localStorage 避免循环依赖（store 还会反过来调 api）。
const TOKEN_KEY = 'dataops.token'

function authHeaders() {
  const token = localStorage.getItem(TOKEN_KEY) || ''
  return token ? { Authorization: `Bearer ${token}` } : {}
}

const parseError = async (response) => {
  const type = response.headers.get('content-type') || ''
  if (type.includes('application/json')) {
    const payload = await response.json()
    return payload.detail || JSON.stringify(payload)
  }
  return response.text()
}

// 401 时自动清 token + 跳 /spa/#/login（避免每个 view 自己处理）
function _handleAuthFailure(response) {
  if (response.status !== 401) return
  // 已经在 login 页就别再跳，避免死循环
  const hash = window.location.hash || ''
  if (hash.startsWith('#/login')) return
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem('dataops.user')
  window.location.hash = '#/login'
}

export const apiGet = async (url) => {
  const response = await fetch(url, { headers: { ...authHeaders() } })
  if (!response.ok) {
    _handleAuthFailure(response)
    throw new Error(await parseError(response))
  }
  return response.json()
}

export const apiJson = async (url, method, payload) => {
  const response = await fetch(url, {
    method,
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  })
  if (!response.ok) {
    _handleAuthFailure(response)
    throw new Error(await parseError(response))
  }
  return response.json()
}

export const apiForm = async (url, formData) => {
  const response = await fetch(url, {
    method: 'POST',
    headers: { ...authHeaders() },
    body: formData,
  })
  if (!response.ok) {
    _handleAuthFailure(response)
    throw new Error(await parseError(response))
  }
  return response.json()
}

export const readFileText = async (file) => file ? file.text() : ''
