<script setup>
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { useNoticeStore } from '../stores/notice'
import {
  Activity,
  AlertCircle,
  Database,
  GitCompareArrows,
  Lock,
  Network,
  ShieldCheck,
  User as UserIcon,
  Workflow,
} from 'lucide-vue-next'

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
  <div class="login-shell relative min-h-screen overflow-hidden bg-slate-950 text-slate-900">
    <div class="login-grid" aria-hidden="true"></div>
    <div class="relative z-10 grid min-h-screen lg:grid-cols-[minmax(0,1fr)_520px]">
      <section class="hidden min-h-screen flex-col justify-between px-10 py-9 text-white lg:flex xl:px-14">
        <div class="flex items-center gap-3">
          <div class="grid h-10 w-10 place-items-center rounded-xl bg-white text-primary shadow-lg shadow-black/20">
            <Network class="h-5 w-5" />
          </div>
          <div>
            <h1 class="text-base font-bold">DataOps Studio</h1>
            <p class="text-xs text-slate-300">数据运维控制台</p>
          </div>
        </div>

        <div class="max-w-2xl">
          <p class="text-xs font-bold uppercase tracking-wider text-cyan-200">Operations Workspace</p>
          <h2 class="mt-4 max-w-xl text-4xl font-bold leading-tight text-white">
            数据对比、血缘分析和作业编排，一个入口完成。
          </h2>
          <p class="mt-4 max-w-lg text-sm leading-7 text-slate-300">
            登录后继续管理数据源、任务运行、审计记录和 AI 辅助分析。
          </p>

          <div class="mt-10 w-full max-w-2xl rounded-2xl border border-white/10 bg-white/10 p-4 shadow-2xl shadow-black/30">
            <div class="mb-4 flex items-center justify-between border-b border-white/10 pb-3">
              <div>
                <p class="text-sm font-semibold text-white">今日运行概览</p>
                <p class="text-xs text-slate-400">Pipeline health</p>
              </div>
              <div class="flex items-center gap-1.5 rounded-full border border-emerald-300/25 bg-emerald-400/10 px-2.5 py-1 text-xs text-emerald-200">
                <Activity class="h-3.5 w-3.5" />
                正常
              </div>
            </div>

            <div class="grid grid-cols-3 gap-3">
              <div class="rounded-xl border border-white/10 bg-slate-900/70 p-3">
                <div class="mb-4 flex h-8 w-8 items-center justify-center rounded-lg bg-cyan-400/15 text-cyan-200">
                  <Database class="h-4 w-4" />
                </div>
                <p class="text-2xl font-bold text-white">18</p>
                <p class="mt-1 text-xs text-slate-400">数据源</p>
              </div>
              <div class="rounded-xl border border-white/10 bg-slate-900/70 p-3">
                <div class="mb-4 flex h-8 w-8 items-center justify-center rounded-lg bg-violet-400/15 text-violet-200">
                  <GitCompareArrows class="h-4 w-4" />
                </div>
                <p class="text-2xl font-bold text-white">42</p>
                <p class="mt-1 text-xs text-slate-400">对比任务</p>
              </div>
              <div class="rounded-xl border border-white/10 bg-slate-900/70 p-3">
                <div class="mb-4 flex h-8 w-8 items-center justify-center rounded-lg bg-amber-300/15 text-amber-100">
                  <Workflow class="h-4 w-4" />
                </div>
                <p class="text-2xl font-bold text-white">7</p>
                <p class="mt-1 text-xs text-slate-400">作业流</p>
              </div>
            </div>

            <div class="mt-4 rounded-xl border border-white/10 bg-slate-950/80 p-4">
              <div class="flex items-center gap-3">
                <div class="flex h-9 w-9 items-center justify-center rounded-lg bg-cyan-400/15 text-cyan-200">
                  <Database class="h-4 w-4" />
                </div>
                <div class="h-px flex-1 bg-cyan-200/35"></div>
                <div class="flex h-9 w-9 items-center justify-center rounded-lg bg-violet-400/15 text-violet-200">
                  <GitCompareArrows class="h-4 w-4" />
                </div>
                <div class="h-px flex-1 bg-violet-200/35"></div>
                <div class="flex h-9 w-9 items-center justify-center rounded-lg bg-emerald-400/15 text-emerald-200">
                  <Network class="h-4 w-4" />
                </div>
              </div>
              <div class="mt-3 grid grid-cols-3 text-[11px] text-slate-400">
                <span>采集</span>
                <span class="text-center">校验</span>
                <span class="text-right">血缘</span>
              </div>
            </div>
          </div>
        </div>

        <div class="flex items-center gap-2 text-xs text-slate-400">
          <ShieldCheck class="h-4 w-4 text-emerald-300" />
          <span>DataOps Studio · Console Access</span>
        </div>
      </section>

      <main class="flex min-h-screen items-center justify-center bg-slate-50 px-4 py-8 sm:px-6 lg:bg-white">
        <form
          class="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-6 shadow-xl shadow-slate-200/70 sm:p-8"
          @submit.prevent="onSubmit"
        >
          <div class="mb-8">
            <div class="mb-5 flex items-center gap-3 lg:hidden">
              <div class="grid h-10 w-10 place-items-center rounded-xl bg-primary text-white shadow-md shadow-primary/30">
                <Network class="h-5 w-5" />
              </div>
              <div>
                <h1 class="text-base font-bold text-slate-900">DataOps Studio</h1>
                <p class="text-xs text-slate-500">数据运维控制台</p>
              </div>
            </div>
            <p class="text-xs font-bold uppercase tracking-wider text-primary">Secure Login</p>
            <h2 class="mt-2 text-2xl font-bold text-slate-950">欢迎回来</h2>
            <p class="mt-2 text-sm text-slate-500">输入账号信息进入工作台。</p>
          </div>

          <div class="space-y-4">
            <label class="block">
              <span class="mb-1.5 block text-xs font-bold text-slate-600">用户名</span>
              <div class="relative">
                <UserIcon class="absolute left-3 top-3 h-4 w-4 text-slate-400" />
                <input
                  v-model="username"
                  type="text"
                  autocomplete="username"
                  autofocus
                  class="h-11 w-full rounded-xl border-slate-200 bg-slate-50 pl-10 text-sm transition focus:border-primary focus:bg-white focus:ring-primary/20"
                  placeholder="admin"
                />
              </div>
            </label>

            <label class="block">
              <span class="mb-1.5 block text-xs font-bold text-slate-600">密码</span>
              <div class="relative">
                <Lock class="absolute left-3 top-3 h-4 w-4 text-slate-400" />
                <input
                  v-model="password"
                  type="password"
                  autocomplete="current-password"
                  class="h-11 w-full rounded-xl border-slate-200 bg-slate-50 pl-10 text-sm transition focus:border-primary focus:bg-white focus:ring-primary/20"
                  placeholder="请输入密码"
                />
              </div>
            </label>
          </div>

          <div
            v-if="errorMsg"
            class="mt-4 flex items-start gap-2 rounded-xl border border-status-error/20 bg-status-error-bg px-3 py-2.5 text-xs text-status-error"
          >
            <AlertCircle class="mt-0.5 h-4 w-4 shrink-0" />
            <span>{{ errorMsg }}</span>
          </div>

          <button
            type="submit"
            class="mt-6 inline-flex h-11 w-full items-center justify-center rounded-xl bg-primary px-4 text-sm font-bold text-white shadow-lg shadow-primary/25 transition hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-60"
            :disabled="submitting"
          >
            {{ submitting ? '登录中…' : '登录' }}
          </button>

          <div class="mt-5 rounded-xl border border-slate-200 bg-slate-50 px-3 py-3 text-center text-[11px] leading-5 text-slate-500">
            首次启动默认账号 <code class="font-mono font-semibold text-slate-700">admin / admin</code>
            ，登录后请到「用户管理」修改密码
          </div>
        </form>
      </main>
    </div>
  </div>
</template>

<style scoped>
.login-shell {
  background:
    linear-gradient(120deg, rgba(14, 165, 233, 0.20), transparent 34%),
    linear-gradient(150deg, #111827 0%, #182135 46%, #0f172a 100%);
}

.login-grid {
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(255, 255, 255, 0.055) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 255, 255, 0.055) 1px, transparent 1px);
  background-size: 40px 40px;
  background-position: -1px -1px;
  mask-image: linear-gradient(90deg, #000 0%, rgba(0, 0, 0, 0.84) 44%, transparent 74%);
  pointer-events: none;
}
</style>
