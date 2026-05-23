// 全局 fetch wrapper，自动带 Authorization: Bearer <token>。
// token 由 useAuthStore 管理，落 localStorage：dataops.token；本模块直接读
// localStorage 避免循环依赖（store 还会反过来调 api）。
//
// S4.A：迁 .ts。给 apiGet / apiJson / apiForm 加泛型 T，调用方写
//   `await apiGet<User>('/api/auth/me')` 直接拿 User 类型，不用 store 内 `as T`
// 没标的话默认 unknown（比 any 安全），强迫 caller 显式断言或检查。
const TOKEN_KEY = 'dataops.token'
const REFRESH_KEY = 'dataops.refresh'
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

export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'

export interface TranslateErrorContext {
  error?: string
  sql_excerpt?: string
  db_type?: string
}

interface AIStateProbe {
  enabled: boolean
  autoTranslate: boolean
}

interface AITranslateContext {
  sql_excerpt?: string
  db_type?: string
}

// Phase 10 后期：API 版本化前缀。所有 /api/X 透明改写到 /api/v1/X，让前端
// 走稳定 v1 契约。后端 install_v1_aliases 给 /api/v1/X 注册了同义路由，所以
// /api/X 旧路径仍可用（deprecation window）。新代码可以直接写 /api/v1/...
// 也可以继续写 /api/...（等价）。
function _versionPath(url: string): string {
  if (typeof url !== 'string') return url
  if (url.startsWith('/api/v1/')) return url
  if (url.startsWith('/api/')) return '/api/v1/' + url.slice(5)
  return url
}

function authHeaders(): Record<string, string> {
  const token = localStorage.getItem(TOKEN_KEY) || ''
  return token ? { Authorization: `Bearer ${token}` } : {}
}

// 给 path 追加 ?project_id=<localStorage>。仅命中 PROJECT_AWARE_PATHS 的
// 主路径（不含子路径，如 /api/datasources/:id 不加）。
// 注意：检查的是 versioned 之前的 path（PROJECT_AWARE_PATHS 用 /api/ 前缀写），
// 但返回的是 versioned URL。
function withProjectQuery(url: string): string {
  const projectId = localStorage.getItem(PROJECT_KEY) || ''
  const versioned = _versionPath(url)
  if (!projectId) return versioned
  // 用未版本化的 path 匹配（PROJECT_AWARE_PATHS 写的是 /api/）
  const [pathPart] = url.split('?')
  if (!PROJECT_AWARE_PATHS.includes(pathPart)) return versioned
  const sep = versioned.includes('?') ? '&' : '?'
  return `${versioned}${sep}project_id=${encodeURIComponent(projectId)}`
}

const parseError = async (response: Response): Promise<string> => {
  const type = response.headers.get('content-type') || ''
  if (type.includes('application/json')) {
    const payload = await response.json() as { detail?: string } & Record<string, unknown>
    return payload.detail || JSON.stringify(payload)
  }
  return response.text()
}

// 401 时自动清 token + 跳 /spa/#/login（避免每个 view 自己处理）
function _handleAuthFailure(response: Response): void {
  if (response.status !== 401) return
  // 已经在 login 页就别再跳，避免死循环
  const hash = window.location.hash || ''
  if (hash.startsWith('#/login')) return
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(REFRESH_KEY)
  localStorage.removeItem('dataops.user')
  window.location.hash = '#/login'
}


// ─── refresh rotation 401 自动重试 ─────────────────────────────────────────
// 任何 apiGet/apiJson/apiForm 拿到 401（且不是 /api/auth/refresh 本身）就：
//   1. 用 localStorage 里的 refresh token 调 POST /api/auth/refresh
//   2. 成功 → 写回新 access + 新 refresh,原请求重试一次（带新 Authorization）
//   3. 失败 → 标准 401 流（_handleAuthFailure 清 token 跳 login）
//
// in-flight 锁：多个并发 401 共用一次 refresh 调用,避免重放检测把多次同时
// rotate 错杀成「重放」。
let _refreshInFlight: Promise<boolean> | null = null


async function _attemptRefresh(): Promise<boolean> {
  if (_refreshInFlight) return _refreshInFlight
  _refreshInFlight = (async (): Promise<boolean> => {
    const refresh = localStorage.getItem(REFRESH_KEY) || ''
    if (!refresh) return false
    try {
      const resp = await fetch(_versionPath('/api/auth/refresh'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refresh }),
      })
      if (!resp.ok) {
        // refresh 失败（exp / revoked / reuse 检出）—— 清两个 token 走标准 401
        localStorage.removeItem(TOKEN_KEY)
        localStorage.removeItem(REFRESH_KEY)
        return false
      }
      const data = await resp.json() as {
        access_token?: string
        refresh_token?: string
        user?: unknown
      }
      if (!data.access_token) return false
      localStorage.setItem(TOKEN_KEY, data.access_token)
      if (data.refresh_token) {
        localStorage.setItem(REFRESH_KEY, data.refresh_token)
      }
      // user 也顺手更新（refresh 端点返回完整 user）
      if (data.user) {
        localStorage.setItem('dataops.user', JSON.stringify(data.user))
      }
      return true
    } catch {
      return false
    }
  })()
  try {
    return await _refreshInFlight
  } finally {
    _refreshInFlight = null
  }
}


function _isRefreshEndpoint(url: string): boolean {
  // 不要 refresh 端点自己 401 时再 refresh（无限递归）
  return url.includes('/api/auth/refresh')
}

// AI 错误翻译 hook：5xx 或长错误时异步调 /api/ai/translate-error。
// 不阻塞主请求 throw —— 翻译完成后通过事件总线推给 noticeStore。
const _AI_TRANSLATE_THRESHOLD_CHARS = 60
const _AI_TRANSLATE_RECENT = new Map<string, number>()  // dedupe：相同 error 5 秒内不重复翻译

let _aiEnabled: AIStateProbe | null = null  // null=未探测
async function _isAIEnabled(): Promise<AIStateProbe> {
  if (_aiEnabled !== null) return _aiEnabled
  try {
    const status = await fetch(_versionPath('/api/lineage/ai/status'), { headers: { ...authHeaders() } })
    if (status.ok) {
      const data = await status.json() as { enabled?: boolean; configured?: boolean; enable_auto_translation?: boolean }
      _aiEnabled = {
        enabled: !!data?.enabled && !!data?.configured,
        // Phase 9 Day 6：默认 off。admin 在 AIConfig 显式开启 enable_auto_translation
        // 才会走自动翻译路径；显式 translateError() 仍可用（不受 gate 控制）。
        autoTranslate: !!data?.enable_auto_translation,
      }
    } else {
      _aiEnabled = { enabled: false, autoTranslate: false }
    }
  } catch {
    _aiEnabled = { enabled: false, autoTranslate: false }
  }
  return _aiEnabled
}

// 给外部（admin AIConfigView 保存配置后）调用，丢掉缓存重新探测
export function invalidateAIEnabledCache(): void { _aiEnabled = null }

async function _maybeTranslateError(
  response: Response,
  errorText: string,
  context: AITranslateContext = {},
): Promise<void> {
  if (!errorText || errorText.length < _AI_TRANSLATE_THRESHOLD_CHARS) return
  // 仅对 5xx + 4xx 中的 422 / 400 长错误翻译；403 / 404 / 409 已经够清楚
  if (response.status < 500 && ![400, 422].includes(response.status)) return
  const dedupeKey = `${response.status}:${errorText.slice(0, 100)}`
  const now = Date.now()
  const lastSeen = _AI_TRANSLATE_RECENT.get(dedupeKey)
  if (lastSeen && now - lastSeen < 5000) return
  _AI_TRANSLATE_RECENT.set(dedupeKey, now)
  // GC：清掉超过 dedupe 窗口的历史 entry，避免长会话里 Map 无限增长
  // （每个独特 5xx 错误都留一条直到 SPA 关闭）
  if (_AI_TRANSLATE_RECENT.size > 64) {
    for (const [key, ts] of _AI_TRANSLATE_RECENT) {
      if (now - ts > 10000) _AI_TRANSLATE_RECENT.delete(key)
    }
  }
  // Phase 9 Day 6：默认关。admin 在 AIConfig 开启 enable_auto_translation
  // 才走这条自动翻译路径，避免每个 5xx 都烧 token。
  const aiState = await _isAIEnabled()
  if (!aiState?.enabled || !aiState?.autoTranslate) return
  try {
    const r = await fetch(_versionPath('/api/ai/translate-error'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({
        error_text: errorText,
        sql_excerpt: context.sql_excerpt || '',
        db_type: context.db_type || '',
      }),
    })
    if (!r.ok) return
    const data = await r.json() as { ok?: boolean; translation?: string; suggestions?: string[] }
    if (!data.ok || !data.translation) return
    // 派发自定义事件 —— App.vue 接 + 推给 noticeStore（避免 api.ts 直接依赖 store）
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('dataops:error-translated', {
        detail: { translation: data.translation, suggestions: data.suggestions || [], original: errorText },
      }))
    }
  } catch {
    /* 翻译失败静默 —— 主请求已经 throw 出去了 */
  }
}

/** GET /api/...，自动 versioning + project_id 注入 + Bearer token。
 * 调用方传 T 拿强类型；不传默认 unknown。
 * 401 + 非 refresh 端点 → 自动用 refresh token 续一次再重试。 */
export const apiGet = async <T = unknown>(url: string): Promise<T> => {
  let response = await fetch(withProjectQuery(url), { headers: { ...authHeaders() } })
  if (response.status === 401 && !_isRefreshEndpoint(url)) {
    if (await _attemptRefresh()) {
      response = await fetch(withProjectQuery(url), { headers: { ...authHeaders() } })
    }
  }
  if (!response.ok) {
    _handleAuthFailure(response)
    const message = await parseError(response)
    _maybeTranslateError(response, message)
    throw new Error(message)
  }
  return response.json() as Promise<T>
}

/** POST/PUT/PATCH/DELETE 带 JSON body。payload undefined → no body。 */
export const apiJson = async <T = unknown>(
  url: string,
  method: HttpMethod,
  payload?: unknown,
): Promise<T> => {
  const _fire = () => fetch(_versionPath(url), {
    method,
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  })
  let response = await _fire()
  if (response.status === 401 && !_isRefreshEndpoint(url)) {
    if (await _attemptRefresh()) response = await _fire()
  }
  if (!response.ok) {
    _handleAuthFailure(response)
    const message = await parseError(response)
    _maybeTranslateError(response, message)
    throw new Error(message)
  }
  return response.json() as Promise<T>
}

/** multipart/form-data POST。FormData body，无 Content-Type header（浏览器自填 boundary）。 */
export const apiForm = async <T = unknown>(url: string, formData: FormData): Promise<T> => {
  const _fire = () => fetch(_versionPath(url), {
    method: 'POST',
    headers: { ...authHeaders() },
    body: formData,
  })
  let response = await _fire()
  if (response.status === 401 && !_isRefreshEndpoint(url)) {
    if (await _attemptRefresh()) response = await _fire()
  }
  if (!response.ok) {
    _handleAuthFailure(response)
    const message = await parseError(response)
    _maybeTranslateError(response, message)
    throw new Error(message)
  }
  return response.json() as Promise<T>
}

// 显式触发翻译 + 上下文（适合预览 SQL 失败 / 错误卡片底部"AI 解释"按钮）：
// 业务代码 catch 后调 translateError({ error: e.message, sql_excerpt, db_type })
// Phase 9 Day 6：显式调用不受 enable_auto_translation gate 限制，只要 AI provider
// 已配置就走 —— 用户主动点按钮 = 明确同意烧 token。
export async function translateError({ error, sql_excerpt, db_type }: TranslateErrorContext = {}): Promise<void> {
  if (!error) return
  const aiState = await _isAIEnabled()
  if (!aiState?.enabled) return
  try {
    const r = await fetch(_versionPath('/api/ai/translate-error'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ error_text: String(error), sql_excerpt, db_type }),
    })
    if (!r.ok) return
    const data = await r.json() as { ok?: boolean; translation?: string; suggestions?: string[] }
    if (data.ok && data.translation && typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('dataops:error-translated', {
        detail: { translation: data.translation, suggestions: data.suggestions || [], original: String(error) },
      }))
    }
  } catch { /* silent */ }
}

export const readFileText = async (file: File | null): Promise<string> => file ? file.text() : ''
