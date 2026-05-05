/**
 * Auth store —— 当前登录用户 + token + login/logout。
 *
 * - token 落 localStorage（key: dataops.token），fetch wrapper 自动读
 * - user 落 localStorage（key: dataops.user）热启动恢复，避免每次刷新都
 *   等 /api/auth/me 才显示用户名
 * - login 后调 router.push 跳到原始 redirect 目标 / 默认数据源页
 * - 401 由 api.js 自动清 token 跳 login，store 这里不重复处理
 *
 * S3.B：作为 TS 迁移第一刀，立 pattern：
 *   - export 类型给 view 用（User / LoginResponse）
 *   - ref<T>() 显式标注泛型，避免 ref(null) 推成 Ref<null>
 *   - 函数签名带类型；返回值由 TS 推断
 */
import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { apiJson } from '../api'


export type UserRole = 'admin' | 'editor' | 'viewer'

export interface User {
  id?: string
  username: string
  display_name?: string
  role: UserRole
  created_at?: string
}

export interface LoginResponse {
  access_token: string
  token_type: string
  user: User
}


const TOKEN_KEY = 'dataops.token'
const USER_KEY = 'dataops.user'


function _readUser(): User | null {
  try {
    const raw = localStorage.getItem(USER_KEY)
    return raw ? (JSON.parse(raw) as User) : null
  } catch {
    return null
  }
}


export const useAuthStore = defineStore('auth', () => {
  const token = ref<string>(localStorage.getItem(TOKEN_KEY) || '')
  const user = ref<User | null>(_readUser())

  const isLoggedIn = computed(() => !!token.value)
  const isAdmin = computed(() => user.value?.role === 'admin')
  const isEditor = computed(
    () => user.value?.role === 'admin' || user.value?.role === 'editor',
  )

  function _persist(): void {
    if (token.value) {
      localStorage.setItem(TOKEN_KEY, token.value)
    } else {
      localStorage.removeItem(TOKEN_KEY)
    }
    if (user.value) {
      localStorage.setItem(USER_KEY, JSON.stringify(user.value))
    } else {
      localStorage.removeItem(USER_KEY)
    }
  }

  async function login(username: string, password: string): Promise<LoginResponse> {
    const data = await apiJson('/api/auth/login', 'POST', { username, password }) as LoginResponse
    token.value = data.access_token
    user.value = data.user
    _persist()
    return data
  }

  function logout(): void {
    token.value = ''
    user.value = null
    _persist()
    // 让 hash router 跳到 login —— 这里不 import router 避免循环
    if (typeof window !== 'undefined') {
      window.location.hash = '#/login'
    }
  }

  // 启动后调一次 /api/auth/me 验证 token 还有效（过期就触发 401 自动跳 login）
  async function refreshMe(): Promise<User | null> {
    if (!token.value) return null
    try {
      const me = await apiJson('/api/auth/me', 'GET') as User
      user.value = me
      _persist()
      return me
    } catch {
      // 401 已被 api.js 处理（清 token + 跳 login）
      return null
    }
  }

  return {
    token, user,
    isLoggedIn, isAdmin, isEditor,
    login, logout, refreshMe,
  }
})
