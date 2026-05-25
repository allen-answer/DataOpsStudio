<script setup lang="ts">
// Phase 14 #3 Round 6 N — 登录页改全暗色控制台风(用户选 C)
// 设计:网格纹理 + cyan 终端配色 + bracket [ AUTH ] 框 + > prompt 字段 label
// + 底部假状态条让页面有"数据中心控制台"质感
// 功能全保留:密码登录 / MFA TOTP / MFA recovery code 三步流不变
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useAuthStore } from '../stores/auth'
import { useNoticeStore } from '../stores/notice'
import { useBootstrapStore } from '../stores/bootstrap'
import { useProjectStore } from '../stores/project'
import { AlertCircle, Zap, ShieldCheck, ChevronRight, Lock, User as UserIcon } from 'lucide-vue-next'

const auth = useAuthStore()
const notice = useNoticeStore()
// 登录成功后必须主动 reload bootstrap / project —— App.vue 的 onMounted 在用户
// 还没登录时已经跑过且早退,登录后路由切换不会触发它重跑,会导致首次进 datasources
// 看到空列表(必须刷新页面才出来)。fix #170。
const bootstrap = useBootstrapStore()
const project = useProjectStore()
const route = useRoute()
const router = useRouter()
const { t } = useI18n()

const username = ref('')
const password = ref('')
const submitting = ref(false)
const errorMsg = ref('')

const step = ref<'password' | 'mfa'>('password')
const mfaToken = ref('')
const mfaCode = ref('')
const mfaMode = ref<'totp' | 'recovery'>('totp')
const recoveryCode = ref('')

// 装饰用:渲染时随机生成的 uptime/nodes — 每次刷新不一样,让控制台底栏有"活着"的感觉
const uptimeDays = computed(() => 30 + Math.floor(Math.random() * 60))
const uptimeHours = computed(() => Math.floor(Math.random() * 24))
const nodeCount = computed(() => 4 + Math.floor(Math.random() * 12))

async function _redirectAfterLogin(): Promise<void> {
  notice.setNotice(t('login.welcome', { name: auth.user?.display_name || auth.user?.username }))
  // 先拉项目 + bootstrap,跳路由前数据就位 —— 避免目标 view 看到空 state
  try {
    await project.reload()
    await bootstrap.reload()
  } catch {
    // 单次失败不阻塞跳转,目标 view 自己有 refresh 按钮
  }
  const redirect = (route.query.redirect as string) || '/datasources'
  router.push(redirect)
}

async function onSubmit(): Promise<void> {
  if (!username.value || !password.value) {
    errorMsg.value = t('login.usernameRequired')
    return
  }
  submitting.value = true
  errorMsg.value = ''
  try {
    const resp = await auth.login(username.value, password.value)
    if (resp.mfa_required && resp.mfa_token) {
      mfaToken.value = resp.mfa_token
      mfaCode.value = ''
      step.value = 'mfa'
      return
    }
    await _redirectAfterLogin()
  } catch (error: any) {
    errorMsg.value = error?.message || t('login.error')
  } finally {
    submitting.value = false
  }
}

async function onSubmitMfa(): Promise<void> {
  if (mfaMode.value === 'totp') {
    const code = mfaCode.value.trim()
    if (code.length < 6) {
      errorMsg.value = '请输入 6 位 OTP'
      return
    }
    submitting.value = true
    errorMsg.value = ''
    try {
      await auth.mfaChallenge(mfaToken.value, { code })
      await _redirectAfterLogin()
    } catch (error: any) {
      errorMsg.value = error?.message || 'OTP 验证失败'
      mfaCode.value = ''
    } finally {
      submitting.value = false
    }
  } else {
    const raw = recoveryCode.value.trim()
    if (raw.replace(/[\s-]/g, '').length < 8) {
      errorMsg.value = '请输入完整的恢复码'
      return
    }
    submitting.value = true
    errorMsg.value = ''
    try {
      await auth.mfaChallenge(mfaToken.value, { recoveryCode: raw })
      await _redirectAfterLogin()
    } catch (error: any) {
      errorMsg.value = error?.message || '恢复码无效'
      recoveryCode.value = ''
    } finally {
      submitting.value = false
    }
  }
}

function toggleMfaMode(): void {
  mfaMode.value = mfaMode.value === 'totp' ? 'recovery' : 'totp'
  mfaCode.value = ''
  recoveryCode.value = ''
  errorMsg.value = ''
}

function backToPassword(): void {
  step.value = 'password'
  mfaToken.value = ''
  mfaCode.value = ''
  recoveryCode.value = ''
  mfaMode.value = 'totp'
  errorMsg.value = ''
}
</script>

<template>
  <div class="console-shell relative min-h-screen overflow-hidden font-mono text-cyan-100">
    <!-- 网格背景 + 扫描线 -->
    <div class="console-grid" aria-hidden="true"></div>
    <div class="console-scan" aria-hidden="true"></div>

    <!-- 顶部标题条 -->
    <header class="relative z-10 mx-auto flex max-w-5xl items-center justify-between px-6 pt-8">
      <div class="flex items-center gap-2 text-sm">
        <Zap class="h-4 w-4 text-cyan-300" />
        <span class="font-bold uppercase tracking-[0.3em] text-cyan-200">DataOps Console</span>
        <Zap class="h-4 w-4 text-cyan-300" />
      </div>
      <div class="hidden gap-4 text-[10px] uppercase tracking-widest text-cyan-300/60 sm:flex">
        <span>SYS: ONLINE</span>
        <span class="text-emerald-300">●</span>
      </div>
    </header>

    <!-- 中央 form 区 -->
    <main class="relative z-10 mx-auto flex min-h-[calc(100vh-180px)] max-w-5xl items-center justify-center px-6 py-8">
      <!-- 第一步:账号密码 -->
      <form
        v-if="step === 'password'"
        class="console-card w-full max-w-md"
        @submit.prevent="onSubmit"
      >
        <!-- 顶部 bracket label -->
        <div class="console-card-label">
          <span class="text-cyan-400">╭─[</span>
          <span class="px-2 font-bold text-cyan-100">AUTH</span>
          <span class="text-cyan-400">]</span>
          <span class="ml-2 flex-1 border-t border-dashed border-cyan-500/30"></span>
          <span class="ml-2 text-[10px] uppercase text-cyan-400/60">SECURE LOGIN</span>
        </div>

        <div class="px-6 py-6 space-y-5">
          <!-- USER_ID -->
          <label class="block">
            <span class="mb-1.5 flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-cyan-300">
              <ChevronRight class="h-3 w-3" />
              USER_ID
            </span>
            <div class="console-input-wrap">
              <UserIcon class="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-cyan-500/50" />
              <input
                v-model="username"
                type="text"
                autocomplete="username"
                autofocus
                class="console-input pl-9"
                placeholder="admin"
              />
              <span class="absolute right-3 top-1/2 -translate-y-1/2 text-cyan-400/40">█</span>
            </div>
          </label>

          <!-- PASSPHRASE -->
          <label class="block">
            <span class="mb-1.5 flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-cyan-300">
              <ChevronRight class="h-3 w-3" />
              PASSPHRASE
            </span>
            <div class="console-input-wrap">
              <Lock class="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-cyan-500/50" />
              <input
                v-model="password"
                type="password"
                autocomplete="current-password"
                class="console-input pl-9"
                :placeholder="$t('login.passwordPlaceholder')"
              />
            </div>
          </label>

          <!-- 错误提示 -->
          <div
            v-if="errorMsg"
            class="flex items-start gap-2 rounded border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-xs text-rose-200"
          >
            <AlertCircle class="mt-0.5 h-3.5 w-3.5 shrink-0 text-rose-400" />
            <span>{{ errorMsg }}</span>
          </div>

          <!-- 主按钮 -->
          <button
            type="submit"
            class="console-btn"
            :disabled="submitting"
          >
            <span class="text-cyan-400">[</span>
            <span class="mx-1 tracking-wider">{{ submitting ? 'AUTHENTICATING…' : 'AUTHENTICATE' }}</span>
            <span class="text-cyan-400">→ ]</span>
          </button>

          <!-- 默认账号提示 -->
          <div class="rounded border border-cyan-500/15 bg-cyan-500/5 px-3 py-2 text-center text-[10px] leading-5 text-cyan-300/70">
            首次启动默认账号
            <code class="font-mono font-semibold text-cyan-100">admin / admin</code>
            <br>登录后请到「用户管理」修改密码
          </div>
        </div>

        <!-- 底部 bracket close -->
        <div class="console-card-footer">
          <span class="text-cyan-400">╰</span>
          <span class="flex-1 border-t border-dashed border-cyan-500/30"></span>
          <span class="text-cyan-400">╯</span>
        </div>
      </form>

      <!-- 第二步:MFA -->
      <form
        v-else
        class="console-card w-full max-w-md"
        @submit.prevent="onSubmitMfa"
      >
        <div class="console-card-label">
          <span class="text-violet-400">╭─[</span>
          <span class="px-2 font-bold text-violet-100">2FA</span>
          <span class="text-violet-400">]</span>
          <span class="ml-2 flex-1 border-t border-dashed border-violet-500/30"></span>
          <span class="ml-2 text-[10px] uppercase text-violet-400/60">SECONDARY AUTH</span>
        </div>

        <div class="px-6 py-6 space-y-5">
          <div class="flex items-start gap-3 rounded border border-violet-500/20 bg-violet-500/5 p-3">
            <ShieldCheck class="mt-0.5 h-4 w-4 shrink-0 text-violet-300" />
            <div class="text-[11px] leading-relaxed text-violet-100/90">
              <p v-if="mfaMode === 'totp'">打开 TOTP app(Google Authenticator / Authy 等),输入 6 位当前码。</p>
              <p v-else>输入启用 MFA 时保存的一次性恢复码 — 单次有效。</p>
            </div>
          </div>

          <!-- TOTP -->
          <label v-if="mfaMode === 'totp'" class="block">
            <span class="mb-1.5 flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-violet-300">
              <ChevronRight class="h-3 w-3" />
              OTP_CODE
            </span>
            <input
              v-model="mfaCode"
              type="text"
              inputmode="numeric"
              maxlength="6"
              autocomplete="one-time-code"
              autofocus
              placeholder="000000"
              class="console-input console-input-otp"
            >
          </label>

          <label v-else class="block">
            <span class="mb-1.5 flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-violet-300">
              <ChevronRight class="h-3 w-3" />
              RECOVERY_KEY
            </span>
            <input
              v-model="recoveryCode"
              type="text"
              autocomplete="off"
              autocapitalize="characters"
              autofocus
              placeholder="ABCDE-FGHJK"
              class="console-input console-input-otp text-base tracking-[0.25em]"
            >
            <span class="mt-1.5 block text-[10px] text-violet-300/50">分隔符可省 · 大小写不限 · 用过即失效</span>
          </label>

          <div
            v-if="errorMsg"
            class="flex items-start gap-2 rounded border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-xs text-rose-200"
          >
            <AlertCircle class="mt-0.5 h-3.5 w-3.5 shrink-0 text-rose-400" />
            <span>{{ errorMsg }}</span>
          </div>

          <button
            type="submit"
            class="console-btn console-btn-violet"
            :disabled="submitting || (mfaMode === 'totp' ? mfaCode.length < 6 : recoveryCode.replace(/[\s-]/g, '').length < 8)"
          >
            <span class="text-violet-400">[</span>
            <span class="mx-1 tracking-wider">{{ submitting ? 'VERIFYING…' : 'VERIFY & LOGIN' }}</span>
            <span class="text-violet-400">→ ]</span>
          </button>

          <button
            type="button"
            class="block w-full text-center text-[11px] text-cyan-300/70 transition hover:text-cyan-200 hover:underline"
            :disabled="submitting"
            @click="toggleMfaMode"
          >
            {{ mfaMode === 'totp' ? '丢手机了?用恢复码登录' : '← 改用 TOTP app' }}
          </button>

          <button
            type="button"
            class="block w-full text-center text-[10px] text-cyan-400/40 transition hover:text-cyan-300"
            :disabled="submitting"
            @click="backToPassword"
          >
            ← 返回输入密码
          </button>
        </div>

        <div class="console-card-footer">
          <span class="text-violet-400">╰</span>
          <span class="flex-1 border-t border-dashed border-violet-500/30"></span>
          <span class="text-violet-400">╯</span>
        </div>
      </form>
    </main>

    <!-- 底部状态栏 -->
    <footer class="relative z-10 mx-auto max-w-5xl px-6 pb-6">
      <div class="flex flex-wrap items-center justify-between gap-2 border-t border-cyan-500/15 pt-4 text-[10px] uppercase tracking-widest text-cyan-300/60">
        <div class="flex items-center gap-3">
          <Zap class="h-3 w-3 text-cyan-300" />
          <span>NODES: {{ nodeCount }}</span>
          <span class="text-cyan-500/30">·</span>
          <span>UPTIME: {{ uptimeDays }}D {{ uptimeHours }}H</span>
          <span class="text-cyan-500/30">·</span>
          <span class="text-emerald-300">● HEALTHY</span>
        </div>
        <div class="hidden sm:flex items-center gap-2">
          <ShieldCheck class="h-3 w-3 text-emerald-300" />
          <span>DataOps Studio · CONSOLE ACCESS</span>
        </div>
      </div>
    </footer>
  </div>
</template>

<style scoped>
.console-shell {
  background:
    radial-gradient(ellipse at top, rgba(6, 182, 212, 0.08), transparent 50%),
    radial-gradient(ellipse at bottom, rgba(124, 58, 237, 0.08), transparent 50%),
    linear-gradient(180deg, #050810 0%, #0a0e1a 50%, #050810 100%);
}

.console-grid {
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(6, 182, 212, 0.04) 1px, transparent 1px),
    linear-gradient(90deg, rgba(6, 182, 212, 0.04) 1px, transparent 1px);
  background-size: 32px 32px;
  background-position: -1px -1px;
  mask-image: radial-gradient(ellipse at center, #000 0%, rgba(0, 0, 0, 0.4) 70%, transparent 100%);
  pointer-events: none;
}

.console-scan {
  position: absolute;
  inset: 0;
  background: linear-gradient(
    180deg,
    transparent 0%,
    rgba(6, 182, 212, 0.04) 50%,
    transparent 100%
  );
  background-size: 100% 8px;
  pointer-events: none;
  opacity: 0.5;
  animation: scan 8s linear infinite;
}

@keyframes scan {
  0%   { background-position: 0 0; }
  100% { background-position: 0 100vh; }
}

.console-card {
  background:
    linear-gradient(180deg, rgba(8, 47, 73, 0.6) 0%, rgba(10, 14, 26, 0.85) 100%);
  border: 1px solid rgba(6, 182, 212, 0.25);
  border-radius: 8px;
  box-shadow:
    0 0 0 1px rgba(6, 182, 212, 0.05) inset,
    0 8px 32px rgba(6, 182, 212, 0.08),
    0 0 60px rgba(124, 58, 237, 0.05);
  backdrop-filter: blur(8px);
}

.console-card-label,
.console-card-footer {
  display: flex;
  align-items: center;
  padding: 0.5rem 1rem;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.05em;
}

.console-card-label {
  border-bottom: 1px solid rgba(6, 182, 212, 0.15);
}

.console-card-footer {
  border-top: 1px solid rgba(6, 182, 212, 0.15);
  padding-top: 0.35rem;
  padding-bottom: 0.35rem;
}

.console-input-wrap {
  position: relative;
}

.console-input {
  display: block;
  width: 100%;
  height: 2.5rem;
  padding-left: 0.75rem;
  padding-right: 0.75rem;
  border-radius: 6px;
  border: 1px solid rgba(6, 182, 212, 0.25);
  background-color: rgba(8, 47, 73, 0.4);
  color: #e0f2fe;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px;
  transition: all 0.15s;
}

.console-input::placeholder {
  color: rgba(165, 243, 252, 0.3);
}

.console-input:focus {
  outline: none;
  border-color: rgba(6, 182, 212, 0.7);
  background-color: rgba(8, 47, 73, 0.7);
  box-shadow: 0 0 0 3px rgba(6, 182, 212, 0.15);
}

.console-input-otp {
  height: 3rem;
  font-size: 1.1rem;
  text-align: center;
  letter-spacing: 0.5em;
}

.console-btn {
  display: inline-flex;
  width: 100%;
  height: 2.75rem;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  border: 1px solid rgba(6, 182, 212, 0.5);
  background:
    linear-gradient(180deg, rgba(6, 182, 212, 0.15) 0%, rgba(6, 182, 212, 0.05) 100%);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px;
  font-weight: 700;
  color: #cffafe;
  letter-spacing: 0.05em;
  transition: all 0.15s;
  box-shadow:
    0 0 0 1px rgba(6, 182, 212, 0.1) inset,
    0 4px 12px rgba(6, 182, 212, 0.15);
}

.console-btn:hover:not(:disabled) {
  background:
    linear-gradient(180deg, rgba(6, 182, 212, 0.25) 0%, rgba(6, 182, 212, 0.1) 100%);
  border-color: rgba(6, 182, 212, 0.8);
  box-shadow:
    0 0 0 1px rgba(6, 182, 212, 0.2) inset,
    0 6px 20px rgba(6, 182, 212, 0.3);
}

.console-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.console-btn-violet {
  border-color: rgba(124, 58, 237, 0.5);
  background:
    linear-gradient(180deg, rgba(124, 58, 237, 0.15) 0%, rgba(124, 58, 237, 0.05) 100%);
  color: #ede9fe;
  box-shadow:
    0 0 0 1px rgba(124, 58, 237, 0.1) inset,
    0 4px 12px rgba(124, 58, 237, 0.15);
}

.console-btn-violet:hover:not(:disabled) {
  background:
    linear-gradient(180deg, rgba(124, 58, 237, 0.25) 0%, rgba(124, 58, 237, 0.1) 100%);
  border-color: rgba(124, 58, 237, 0.8);
  box-shadow:
    0 0 0 1px rgba(124, 58, 237, 0.2) inset,
    0 6px 20px rgba(124, 58, 237, 0.3);
}
</style>
