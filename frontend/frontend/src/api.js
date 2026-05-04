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

// AI 错误翻译 hook：5xx 或长错误时异步调 /api/ai/translate-error。
// 不阻塞主请求 throw —— 翻译完成后通过事件总线推给 noticeStore。
const _AI_TRANSLATE_THRESHOLD_CHARS = 60
const _AI_TRANSLATE_RECENT = new Map()  // dedupe：相同 error 5 秒内不重复翻译

let _aiEnabled = null  // null=未探测，true/false=探测过的结果
async function _isAIEnabled() {
  if (_aiEnabled !== null) return _aiEnabled
  try {
    const status = await fetch('/api/lineage/ai/status', { headers: { ...authHeaders() } })
    if (status.ok) {
      const data = await status.json()
      _aiEnabled = !!data?.enabled && !!data?.configured
    } else {
      _aiEnabled = false
    }
  } catch {
    _aiEnabled = false
  }
  return _aiEnabled
}

// 给外部（admin AIConfigView 保存配置后）调用，丢掉缓存重新探测
export function invalidateAIEnabledCache() { _aiEnabled = null }

async function _maybeTranslateError(response, errorText, context = {}) {
  if (!errorText || errorText.length < _AI_TRANSLATE_THRESHOLD_CHARS) return
  // 仅对 5xx + 4xx 中的 422 / 400 长错误翻译；403 / 404 / 409 已经够清楚
  if (response.status < 500 && ![400, 422].includes(response.status)) return
  const dedupeKey = `${response.status}:${errorText.slice(0, 100)}`
  const now = Date.now()
  if (_AI_TRANSLATE_RECENT.has(dedupeKey) && now - _AI_TRANSLATE_RECENT.get(dedupeKey) < 5000) return
  _AI_TRANSLATE_RECENT.set(dedupeKey, now)
  if (!await _isAIEnabled()) return
  try {
    const r = await fetch('/api/ai/translate-error', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({
        error_text: errorText,
        sql_excerpt: context.sql_excerpt || '',
        db_type: context.db_type || '',
      }),
    })
    if (!r.ok) return
    const data = await r.json()
    if (!data.ok || !data.translation) return
    // 派发自定义事件 —— App.vue 接 + 推给 noticeStore（避免 api.js 直接依赖 store）
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('dataops:error-translated', {
        detail: { translation: data.translation, suggestions: data.suggestions || [], original: errorText },
      }))
    }
  } catch {
    /* 翻译失败静默 —— 主请求已经 throw 出去了 */
  }
}

export const apiGet = async (url) => {
  const response = await fetch(withProjectQuery(url), { headers: { ...authHeaders() } })
  if (!response.ok) {
    _handleAuthFailure(response)
    const message = await parseError(response)
    _maybeTranslateError(response, message)
    throw new Error(message)
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
    const message = await parseError(response)
    _maybeTranslateError(response, message)
    throw new Error(message)
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
    const message = await parseError(response)
    _maybeTranslateError(response, message)
    throw new Error(message)
  }
  return response.json()
}

// 显式触发翻译 + 上下文（适合预览 SQL 失败时）：
// 业务代码 catch 后调 translateError({ error: e.message, sql_excerpt, db_type })
export async function translateError({ error, sql_excerpt, db_type } = {}) {
  if (!error) return
  if (!await _isAIEnabled()) return
  try {
    const r = await fetch('/api/ai/translate-error', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ error_text: String(error), sql_excerpt, db_type }),
    })
    if (!r.ok) return
    const data = await r.json()
    if (data.ok && data.translation && typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('dataops:error-translated', {
        detail: { translation: data.translation, suggestions: data.suggestions || [], original: String(error) },
      }))
    }
  } catch { /* silent */ }
}

export const readFileText = async (file) => file ? file.text() : ''
