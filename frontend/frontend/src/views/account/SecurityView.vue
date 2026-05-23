<script setup lang="ts">
import { onMounted, ref } from 'vue'
import QRCode from 'qrcode'
import { ShieldCheck, ShieldOff, Smartphone, Copy, Check, AlertCircle } from 'lucide-vue-next'
import { apiGet, apiJson } from '../../api'
import { useNoticeStore } from '../../stores/notice'

interface MfaStatus { enabled: boolean; enrolled: boolean }
interface EnrollResponse { secret: string; provisioning_uri: string; verified: boolean }

const noticeStore = useNoticeStore()

const loading = ref(false)
const status = ref<MfaStatus>({ enabled: false, enrolled: false })
const errorMsg = ref('')

// enroll 中间态：拿到 secret 渲染 QR + 等用户输 OTP
const enrolling = ref(false)
const enrollSecret = ref('')
const enrollQrDataUrl = ref('')
const enrollCode = ref('')
const enrollSubmitting = ref(false)
const secretCopied = ref(false)

// disable 中间态
const disabling = ref(false)
const disableCode = ref('')
const disableSubmitting = ref(false)

async function loadStatus(): Promise<void> {
  loading.value = true
  errorMsg.value = ''
  try {
    status.value = await apiGet<MfaStatus>('/api/auth/mfa/status')
  } catch (err: any) {
    errorMsg.value = err?.message || String(err)
  } finally {
    loading.value = false
  }
}

async function startEnroll(): Promise<void> {
  errorMsg.value = ''
  enrolling.value = true
  enrollCode.value = ''
  enrollSecret.value = ''
  enrollQrDataUrl.value = ''
  try {
    const data = await apiJson<EnrollResponse>('/api/auth/mfa/enroll', 'POST')
    enrollSecret.value = data.secret
    enrollQrDataUrl.value = await QRCode.toDataURL(data.provisioning_uri, {
      width: 220,
      margin: 1,
      errorCorrectionLevel: 'M',
    })
  } catch (err: any) {
    errorMsg.value = `生成 MFA secret 失败：${err?.message || err}`
    enrolling.value = false
  }
}

function cancelEnroll(): void {
  enrolling.value = false
  enrollCode.value = ''
  enrollSecret.value = ''
  enrollQrDataUrl.value = ''
}

async function submitEnrollVerify(): Promise<void> {
  if (!enrollCode.value.trim()) return
  enrollSubmitting.value = true
  errorMsg.value = ''
  try {
    await apiJson('/api/auth/mfa/verify', 'POST', { code: enrollCode.value.trim() })
    noticeStore.setNotice('MFA 已启用 —— 下次登录将要求 6 位 OTP')
    enrolling.value = false
    enrollSecret.value = ''
    enrollQrDataUrl.value = ''
    enrollCode.value = ''
    await loadStatus()
  } catch (err: any) {
    errorMsg.value = `验证失败：${err?.message || err}`
  } finally {
    enrollSubmitting.value = false
  }
}

function startDisable(): void {
  errorMsg.value = ''
  disabling.value = true
  disableCode.value = ''
}

function cancelDisable(): void {
  disabling.value = false
  disableCode.value = ''
}

async function submitDisable(): Promise<void> {
  if (!disableCode.value.trim()) return
  disableSubmitting.value = true
  errorMsg.value = ''
  try {
    await apiJson('/api/auth/mfa/disable', 'POST', { code: disableCode.value.trim() })
    noticeStore.setNotice('MFA 已关闭')
    disabling.value = false
    disableCode.value = ''
    await loadStatus()
  } catch (err: any) {
    errorMsg.value = `关闭失败：${err?.message || err}`
  } finally {
    disableSubmitting.value = false
  }
}

async function copySecret(): Promise<void> {
  try {
    await navigator.clipboard.writeText(enrollSecret.value)
    secretCopied.value = true
    setTimeout(() => { secretCopied.value = false }, 1500)
  } catch {
    /* 老浏览器不支持 navigator.clipboard,用户手动选中复制 */
  }
}

onMounted(() => { loadStatus() })
</script>

<template>
  <section class="space-y-4">
    <div class="card">
      <div class="flex items-start justify-between gap-4">
        <div>
          <h2 class="text-2xl font-bold text-slate-800">账号安全</h2>
          <p class="muted mt-1 text-sm">管理你的二步验证（MFA）—— 用 TOTP app 防钓鱼 / 密码泄露</p>
        </div>
      </div>
    </div>

    <!-- MFA 状态 + 启用/关闭 -->
    <div class="card">
      <div class="flex items-start gap-4">
        <div class="grid h-12 w-12 shrink-0 place-items-center rounded-lg" :class="status.enabled ? 'bg-status-success-bg text-status-success' : 'bg-slate-100 text-slate-400'">
          <ShieldCheck v-if="status.enabled" class="h-6 w-6" />
          <ShieldOff v-else class="h-6 w-6" />
        </div>
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-2">
            <h3 class="text-base font-bold text-slate-800">TOTP 二步验证</h3>
            <span v-if="status.enabled" class="status-badge status-success">已启用</span>
            <span v-else class="status-badge status-pending">未启用</span>
          </div>
          <p class="muted mt-1 text-xs leading-relaxed">
            <span v-if="status.enabled">每次登录密码对了之后,还要输手机 TOTP app 上的 6 位码。</span>
            <span v-else>启用后,密码泄露不再 = 账号沦陷。需要 Google Authenticator / Microsoft Authenticator / Authy 等 TOTP app。</span>
          </p>
        </div>
        <div class="shrink-0">
          <button
            v-if="!status.enabled && !enrolling"
            class="btn btn-primary"
            :disabled="loading"
            @click="startEnroll"
          >
            <ShieldCheck class="h-4 w-4" />启用 MFA
          </button>
          <button
            v-if="status.enabled && !disabling"
            class="btn btn-outline border-status-error-bg text-status-error hover:bg-status-error-bg/40"
            @click="startDisable"
          >
            <ShieldOff class="h-4 w-4" />关闭 MFA
          </button>
        </div>
      </div>

      <div v-if="errorMsg" class="mt-4 flex items-start gap-2 rounded-lg border border-status-error-bg bg-status-error-bg/40 px-3 py-2 text-sm text-status-error">
        <AlertCircle class="h-4 w-4 shrink-0" />
        <span>{{ errorMsg }}</span>
      </div>
    </div>

    <!-- enroll：QR + OTP 输入 -->
    <div v-if="enrolling" class="card border-2 border-primary/30 bg-primary-light/30">
      <h3 class="mb-3 flex items-center gap-2 text-base font-bold text-slate-800">
        <Smartphone class="h-5 w-5 text-primary" />
        扫码绑定到 TOTP app
      </h3>
      <div class="grid grid-cols-1 gap-6 md:grid-cols-[auto_1fr]">
        <div class="grid place-items-center rounded-lg border border-slate-200 bg-white p-3">
          <img v-if="enrollQrDataUrl" :src="enrollQrDataUrl" alt="MFA QR" class="h-[220px] w-[220px]">
          <div v-else class="grid h-[220px] w-[220px] place-items-center text-xs text-slate-400">QR 渲染中...</div>
        </div>
        <div class="space-y-3 min-w-0">
          <div>
            <p class="text-[11px] font-bold uppercase tracking-wider text-slate-500">手动添加（不能扫码时）</p>
            <div class="mt-1 flex items-center gap-2">
              <code class="break-all rounded bg-slate-100 px-2 py-1 text-[11px] sql-font text-slate-700">{{ enrollSecret }}</code>
              <button type="button" class="shrink-0 rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700" title="复制" @click="copySecret">
                <Check v-if="secretCopied" class="h-3.5 w-3.5 text-status-success" />
                <Copy v-else class="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
          <div>
            <label class="block">
              <span class="text-[11px] font-bold uppercase tracking-wider text-slate-500">输入 app 上的 6 位 OTP</span>
              <input
                v-model="enrollCode"
                type="text"
                inputmode="numeric"
                maxlength="6"
                placeholder="000000"
                class="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 font-mono text-base tracking-widest focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                @keydown.enter="submitEnrollVerify"
              >
            </label>
          </div>
          <div class="flex justify-end gap-2 pt-2">
            <button type="button" class="btn btn-outline" :disabled="enrollSubmitting" @click="cancelEnroll">取消</button>
            <button type="button" class="btn btn-primary" :disabled="enrollSubmitting || enrollCode.length < 6" @click="submitEnrollVerify">
              {{ enrollSubmitting ? '验证中…' : '验证并启用' }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- disable：要 OTP 才能关 -->
    <div v-if="disabling" class="card border-2 border-status-error-bg bg-status-error-bg/20">
      <h3 class="mb-3 text-base font-bold text-slate-800">关闭 MFA</h3>
      <p class="muted mb-3 text-xs">输入当前 TOTP app 上的 6 位 OTP 确认 —— 防止 token 泄露后被悄悄关 MFA 锁死你。</p>
      <label class="block">
        <span class="text-[11px] font-bold uppercase tracking-wider text-slate-500">6 位 OTP</span>
        <input
          v-model="disableCode"
          type="text"
          inputmode="numeric"
          maxlength="6"
          placeholder="000000"
          class="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 font-mono text-base tracking-widest focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
          @keydown.enter="submitDisable"
        >
      </label>
      <div class="mt-3 flex justify-end gap-2">
        <button type="button" class="btn btn-outline" :disabled="disableSubmitting" @click="cancelDisable">取消</button>
        <button type="button" class="btn btn-danger" :disabled="disableSubmitting || disableCode.length < 6" @click="submitDisable">
          {{ disableSubmitting ? '关闭中…' : '确认关闭 MFA' }}
        </button>
      </div>
    </div>
  </section>
</template>
