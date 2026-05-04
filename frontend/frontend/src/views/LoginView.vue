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
  <div class="login-bg relative grid min-h-screen place-items-center px-4">
    <!-- 背景：紫蓝渐变 + 装饰光斑 + SVG 网格底纹 -->
    <div class="login-bg-orb login-bg-orb-1" aria-hidden="true"></div>
    <div class="login-bg-orb login-bg-orb-2" aria-hidden="true"></div>
    <div class="login-bg-grid" aria-hidden="true"></div>

    <form
      class="relative z-10 w-full max-w-sm space-y-4 rounded-2xl border border-white/30 bg-white/85 p-6 shadow-2xl backdrop-blur-md"
      @submit.prevent="onSubmit"
    >
      <div class="flex items-center gap-3 border-b border-slate-100 pb-4">
        <div class="grid h-10 w-10 place-items-center rounded-lg bg-primary text-white shadow-md shadow-primary/40">
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

    <!-- 底部品牌 / 版权脚 -->
    <p class="absolute bottom-4 z-10 text-center text-[11px] text-white/70">
      DataOps Studio · 数据对比 · 血缘分析 · 作业流编排
    </p>
  </div>
</template>

<style scoped>
/* 主背景：紫蓝深色渐变 —— 跟 sidebar #1a1d2e + 主色 #7c3aed 呼应 */
.login-bg {
  background:
    radial-gradient(circle at 20% 20%, rgba(124, 58, 237, 0.45) 0%, transparent 45%),
    radial-gradient(circle at 80% 70%, rgba(59, 130, 246, 0.35) 0%, transparent 45%),
    linear-gradient(135deg, #1a1d2e 0%, #2d3142 50%, #1e1b4b 100%);
  overflow: hidden;
}

/* 装饰光斑 —— 缓慢呼吸动画，避免画面过死 */
.login-bg-orb {
  position: absolute;
  border-radius: 50%;
  filter: blur(80px);
  opacity: 0.55;
  pointer-events: none;
}
.login-bg-orb-1 {
  width: 480px;
  height: 480px;
  background: #7c3aed;
  top: -120px;
  left: -120px;
  animation: orb-pulse 8s ease-in-out infinite;
}
.login-bg-orb-2 {
  width: 380px;
  height: 380px;
  background: #06b6d4;
  bottom: -100px;
  right: -80px;
  animation: orb-pulse 10s ease-in-out infinite reverse;
}
@keyframes orb-pulse {
  0%, 100% { transform: scale(1) translate(0, 0); opacity: 0.55; }
  50%      { transform: scale(1.15) translate(20px, -20px); opacity: 0.4; }
}

/* SVG 网格底纹 —— 数据感细节，叠在渐变上 */
.login-bg-grid {
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(255, 255, 255, 0.04) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 255, 255, 0.04) 1px, transparent 1px);
  background-size: 48px 48px;
  background-position: -1px -1px;
  pointer-events: none;
}

/* 减弱动画偏好（无障碍） */
@media (prefers-reduced-motion: reduce) {
  .login-bg-orb { animation: none; }
}
</style>
