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
import type { ApiUser, ApiUserRole } from '../types/api'


// S4.B：User / UserRole 走 codegen 同步后端 Pydantic model
export type UserRole = ApiUserRole              // 'admin' | 'editor' | 'viewer'
export type User = ApiUser

// LoginResponse 跟着后端 LoginResponse 一起扩 —— refresh rotation + MFA 两步
// 让它从「access only」变成兼容多种返回形态：
//  - 正常：access_token + refresh_token + user
//  - MFA 启用：mfa_required=true + mfa_token（无 access/user）
export interface LoginResponse {
  access_token: string
  token_type: string
  expires_in?: number
  user: User | null
  // refresh rotation：长期 refresh token,access 401 时由 api.ts 自动换新对
  refresh_token?: string
  refresh_expires_in?: number
  // MFA 两步流：login 验密码后若 user.mfa_enabled,返 mfa_required=true + mfa_token
  // 前端跳 OTP 输入 → POST /api/auth/mfa/challenge {mfa_token, code} 换正式 token
  mfa_required?: boolean
  mfa_token?: string
}


const TOKEN_KEY = 'dataops.token'
const REFRESH_KEY = 'dataops.refresh'
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
  const refreshToken = ref<string>(localStorage.getItem(REFRESH_KEY) || '')
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
    if (refreshToken.value) {
      localStorage.setItem(REFRESH_KEY, refreshToken.value)
    } else {
      localStorage.removeItem(REFRESH_KEY)
    }
    if (user.value) {
      localStorage.setItem(USER_KEY, JSON.stringify(user.value))
    } else {
      localStorage.removeItem(USER_KEY)
    }
  }

  async function login(username: string, password: string): Promise<LoginResponse> {
    const data = await apiJson<LoginResponse>('/api/auth/login', 'POST', { username, password })
    // MFA 启用时 access_token 是空字符串、user 是 null,本 store 不写 token；
    // 调用方（LoginView）看 data.mfa_required 决定跳 OTP 输入或正常登录完成
    if (data.access_token) {
      token.value = data.access_token
      user.value = data.user
      refreshToken.value = data.refresh_token || ''
      _persist()
    }
    return data
  }

  function logout(): void {
    // 服务端吊销 token —— fire-and-forget。apiJson 起 fetch 时同步读
    // localStorage 拼 Authorization 头，所以即便下一行立刻清本地，请求仍带
    // 着有效 token 发出去，服务器把 jti 写进 revoked_tokens 表。失败不阻塞
    // 登出（token 已坏 / 网络断时本地清掉跳 login 即可）。
    if (token.value) {
      apiJson('/api/auth/logout', 'POST').catch(() => {})
    }
    token.value = ''
    refreshToken.value = ''
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
      const me = await apiJson<User>('/api/auth/me', 'GET')
      user.value = me
      _persist()
      return me
    } catch {
      // 401 已被 api.js 处理（清 token + 跳 login）
      return null
    }
  }

  return {
    token, refreshToken, user,
    isLoggedIn, isAdmin, isEditor,
    login, logout, refreshMe,
  }
})
