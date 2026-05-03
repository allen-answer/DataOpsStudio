// 全局 fetch wrapper，自动带 Authorization: Bearer <token>。
// token 由 useAuthStore 管理，落 localStorage：dataops.token；本模块直接读
// localStorage 避免循环依赖（store 还会反过来调 api）。
const TOKEN_KEY = 'dataops.token'
const PROJECT_KEY = 'dataops.project_id'

// 命中这些 path 时自动追加 ?project_id=<当前项目>，让列表/首屏 bootstrap
// 自动按当前项目筛选。/api/projects 自身不能加（会让管理列表也被筛）。
const PROJECT_AWARE_PATHS = [
  '/api/bootstrap',
  '/api/datasources',
  '/api/tasks',
  '/api/workflows',
  '/api/history',
]

function authHeaders() {
  const token = localStorage.getItem(TOKEN_KEY) || ''
  return token ? { Authorization: `Bearer ${token}` } : {}
}

// 给 path 追加 ?project_id=<localStorage>。仅命中 PROJECT_AWARE_PATHS 的
// 主路径（不含子路径，如 /api/datasources/:id 不加）。
function withProjectQuery(url) {
  const projectId = localStorage.getItem(PROJECT_KEY) || ''
  if (!projectId) return url
  // 只对 GET 列表主路径加；id 子路径 / mutating 操作走自身存储的 project_id 不动
  const [pathPart] = url.split('?')
  if (!PROJECT_AWARE_PATHS.includes(pathPart)) return url
  const sep = url.includes('?') ? '&' : '?'
  return `${url}${sep}project_id=${encodeURIComponent(projectId)}`
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
  const response = await fetch(withProjectQuery(url), { headers: { ...authHeaders() } })
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
