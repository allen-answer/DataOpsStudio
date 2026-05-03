/**
 * Auth store —— 当前登录用户 + token + login/logout。
 *
 * - token 落 localStorage（key: dataops.token），fetch wrapper 自动读
 * - user 落 localStorage（key: dataops.user）热启动恢复，避免每次刷新都
 *   等 /api/auth/me 才显示用户名
 * - login 后调 router.push 跳到原始 redirect 目标 / 默认数据源页
 * - 401 由 api.js 自动清 token 跳 login，store 这里不重复处理
 */
import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { apiJson } from '../api'


const TOKEN_KEY = 'dataops.token'
const USER_KEY = 'dataops.user'


function _readUser() {
  try {
    const raw = localStorage.getItem(USER_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}


export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem(TOKEN_KEY) || '')
  const user = ref(_readUser())

  const isLoggedIn = computed(() => !!token.value)
  const isAdmin = computed(() => user.value?.role === 'admin')
  const isEditor = computed(() => ['admin', 'editor'].includes(user.value?.role))

  function _persist() {
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

  async function login(username, password) {
    const data = await apiJson('/api/auth/login', 'POST', { username, password })
    token.value = data.access_token
    user.value = data.user
    _persist()
    return data
  }

  function logout() {
    token.value = ''
    user.value = null
    _persist()
    // 让 hash router 跳到 login —— 这里不 import router 避免循环
    if (typeof window !== 'undefined') {
      window.location.hash = '#/login'
    }
  }

  // 启动后调一次 /api/auth/me 验证 token 还有效（过期就触发 401 自动跳 login）
  async function refreshMe() {
    if (!token.value) return null
    try {
      const me = await apiJson('/api/auth/me', 'GET')
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
