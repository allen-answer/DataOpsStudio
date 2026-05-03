<script setup>
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { useNoticeStore } from '../stores/notice'
import { Database, Lock, User as UserIcon } from 'lucide-vue-next'

const auth = useAuthStore()
const notice = useNoticeStore()
const route = useRoute()
const router = useRouter()

const username = ref('')
const password = ref('')
const submitting = ref(false)
const errorMsg = ref('')

async function onSubmit() {
  if (!username.value || !password.value) {
    errorMsg.value = '用户名和密码必填'
    return
  }
  submitting.value = true
  errorMsg.value = ''
  try {
    await auth.login(username.value, password.value)
    notice.setNotice(`欢迎，${auth.user?.display_name || auth.user?.username}`)
    const redirect = route.query.redirect || '/datasources'
    router.push(redirect)
  } catch (error) {
    errorMsg.value = error?.message || '登录失败'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="grid min-h-screen place-items-center bg-canvas px-4">
    <form
      class="w-full max-w-sm space-y-4 rounded-2xl border border-slate-200 bg-white p-6 shadow-soft"
      @submit.prevent="onSubmit"
    >
      <div class="flex items-center gap-3 border-b border-slate-100 pb-4">
        <div class="grid h-10 w-10 place-items-center rounded-lg bg-primary text-white">
          <Database class="h-5 w-5" />
        </div>
        <div>
          <h1 class="text-base font-bold text-slate-800">DataOps Studio</h1>
          <p class="muted text-xs">登录后进入控制台</p>
        </div>
      </div>

      <label class="block">
        <span class="muted mb-1 block text-[11px] font-bold uppercase tracking-wider">用户名</span>
        <div class="relative">
          <UserIcon class="absolute left-2.5 top-2.5 h-4 w-4 text-slate-400" />
          <input
            v-model="username"
            type="text"
            autocomplete="username"
            autofocus
            class="w-full pl-9"
            placeholder="admin"
          />
        </div>
      </label>

      <label class="block">
        <span class="muted mb-1 block text-[11px] font-bold uppercase tracking-wider">密码</span>
        <div class="relative">
          <Lock class="absolute left-2.5 top-2.5 h-4 w-4 text-slate-400" />
          <input
            v-model="password"
            type="password"
            autocomplete="current-password"
            class="w-full pl-9"
            placeholder="********"
          />
        </div>
      </label>

      <p v-if="errorMsg" class="rounded-md bg-status-error-bg px-3 py-2 text-xs text-status-error">
        {{ errorMsg }}
      </p>

      <button
        type="submit"
        class="btn btn-primary w-full"
        :disabled="submitting"
      >
        {{ submitting ? '登录中…' : '登录' }}
      </button>

      <p class="muted text-center text-[11px]">
        首次启动默认账号 <code class="font-mono">admin / admin</code>
        ——登录后请到「用户管理」改密码
      </p>
    </form>
  </div>
</template>
