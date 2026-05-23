<script setup lang="ts">
import { onMounted, ref } from 'vue'
import QRCode from 'qrcode'
import { ShieldCheck, ShieldOff, Smartphone, Copy, Check, AlertCircle, KeyRound, RefreshCw, Download } from 'lucide-vue-next'
import { apiGet, apiJson } from '../../api'
import { useNoticeStore } from '../../stores/notice'

interface MfaStatus { enabled: boolean; enrolled: boolean; recovery_codes_remaining: number }
interface EnrollResponse { secret: string; provisioning_uri: string; verified: boolean }
interface VerifyResponse { ok: boolean; recovery_codes: string[] }
interface RegenerateResponse { ok: boolean; recovery_codes: string[] }

const noticeStore = useNoticeStore()

const loading = ref(false)
const status = ref<MfaStatus>({ enabled: false, enrolled: false, recovery_codes_remaining: 0 })
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

// Recovery codes 一次性显示(verify / regenerate 成功后才有值)
const recoveryCodes = ref<string[]>([])
const recoveryCodesCopied = ref(false)

// Regenerate 中间态(需要当前 OTP)
const regenerating = ref(false)
const regenerateCode = ref('')
const regenerateSubmitting = ref(false)

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
    const res = await apiJson<VerifyResponse>(
      '/api/auth/mfa/verify', 'POST', { code: enrollCode.value.trim() },
    )
    noticeStore.setNotice('MFA 已启用 —— 下次登录将要求 6 位 OTP')
    enrolling.value = false
    enrollSecret.value = ''
    enrollQrDataUrl.value = ''
    enrollCode.value = ''
    // 后端在首次启用时返 10 个明文 recovery codes —— 只此一次显示,必须当场让用户保存
    recoveryCodes.value = res?.recovery_codes || []
    await loadStatus()
  } catch (err: any) {
    errorMsg.value = `验证失败：${err?.message || err}`
  } finally {
    enrollSubmitting.value = false
  }
}

async function copyRecoveryCodes(): Promise<void> {
  try {
    await navigator.clipboard.writeText(recoveryCodes.value.join('\n'))
    recoveryCodesCopied.value = true
    setTimeout(() => { recoveryCodesCopied.value = false }, 2000)
  } catch {
    /* 老浏览器不支持 navigator.clipboard,用户手动选中复制 */
  }
}

function downloadRecoveryCodes(): void {
  const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
  const content = [
    'DataOpsStudio MFA 恢复码',
    `生成时间: ${new Date().toLocaleString()}`,
    '每条只能用一次,妥善保管(打印 / 密码管理器 / 离线存档)',
    '丢手机时登录页选「用恢复码登录」输 1 条即可',
    '',
    ...recoveryCodes.value,
  ].join('\n')
  const blob = new Blob([content], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `dataops-mfa-recovery-codes-${ts}.txt`
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

function dismissRecoveryCodes(): void {
  recoveryCodes.value = []
}

function startRegenerate(): void {
  errorMsg.value = ''
  regenerating.value = true
  regenerateCode.value = ''
}

function cancelRegenerate(): void {
  regenerating.value = false
  regenerateCode.value = ''
}

async function submitRegenerate(): Promise<void> {
  if (!regenerateCode.value.trim()) return
  regenerateSubmitting.value = true
  errorMsg.value = ''
  try {
    const res = await apiJson<RegenerateResponse>(
      '/api/auth/mfa/recovery-codes/regenerate', 'POST', { code: regenerateCode.value.trim() },
    )
    noticeStore.setNotice('恢复码已重新生成 —— 旧码全部失效')
    regenerating.value = false
    regenerateCode.value = ''
    recoveryCodes.value = res?.recovery_codes || []
    await loadStatus()
  } catch (err: any) {
    errorMsg.value = `重新生成失败：${err?.message || err}`
  } finally {
    regenerateSubmitting.value = false
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

    <!-- Recovery codes 一次性显示卡(verify / regenerate 成功后弹出) -->
    <div v-if="recoveryCodes.length > 0" class="card border-2 border-status-warning bg-status-warning-bg/30">
      <div class="flex items-start gap-3">
        <div class="grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-status-warning text-white">
          <KeyRound class="h-5 w-5" />
        </div>
        <div class="flex-1 min-w-0">
          <h3 class="text-base font-bold text-slate-800">⚠ 保存这 10 个恢复码 —— 只此一次显示</h3>
          <p class="muted mt-1 text-xs leading-relaxed">
            丢手机 / 重装 TOTP app 时,登录页选「用恢复码登录」输 1 条即可。
            <strong class="text-status-warning">关闭本卡片后无法再看 —— 务必先复制或下载存档</strong>(密码管理器 / 打印纸条 / 离线 txt)。
          </p>
        </div>
      </div>
      <div class="mt-4 grid grid-cols-2 gap-2 rounded-lg border border-status-warning/30 bg-white p-4 font-mono text-sm">
        <code v-for="c in recoveryCodes" :key="c" class="select-all rounded bg-slate-50 px-2 py-1.5 text-center text-slate-800 tracking-wider">{{ c }}</code>
      </div>
      <div class="mt-4 flex flex-wrap items-center justify-end gap-2">
        <button type="button" class="btn btn-outline gap-1.5" @click="copyRecoveryCodes">
          <Check v-if="recoveryCodesCopied" class="h-4 w-4 text-status-success" />
          <Copy v-else class="h-4 w-4" />
          {{ recoveryCodesCopied ? '已复制' : '复制' }}
        </button>
        <button type="button" class="btn btn-outline gap-1.5" @click="downloadRecoveryCodes">
          <Download class="h-4 w-4" />下载 .txt
        </button>
        <button type="button" class="btn btn-primary" @click="dismissRecoveryCodes">我已存好,关闭</button>
      </div>
    </div>

    <!-- 已启用 MFA:显示剩余 recovery code 数 + 重新生成入口 -->
    <div v-if="status.enabled && recoveryCodes.length === 0" class="card">
      <div class="flex items-start gap-4">
        <div class="grid h-12 w-12 shrink-0 place-items-center rounded-lg" :class="status.recovery_codes_remaining > 3 ? 'bg-status-success-bg text-status-success' : status.recovery_codes_remaining > 0 ? 'bg-status-warning-bg text-status-warning' : 'bg-status-error-bg text-status-error'">
          <KeyRound class="h-6 w-6" />
        </div>
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-2">
            <h3 class="text-base font-bold text-slate-800">恢复码</h3>
            <span class="status-badge" :class="status.recovery_codes_remaining > 3 ? 'status-success' : status.recovery_codes_remaining > 0 ? 'status-warning' : 'status-error'">
              剩 {{ status.recovery_codes_remaining }} / 10
            </span>
          </div>
          <p class="muted mt-1 text-xs leading-relaxed">
            <span v-if="status.recovery_codes_remaining === 0">⚠ 一个不剩了 —— 立即重新生成,否则丢手机会被锁死</span>
            <span v-else-if="status.recovery_codes_remaining <= 3">⚠ 已剩不多 —— 建议重新生成新一批(旧的全失效)</span>
            <span v-else>用过的码不能再用。建议剩 ≤3 个时重新生成。</span>
          </p>
        </div>
        <div class="shrink-0">
          <button
            v-if="!regenerating"
            class="btn btn-outline"
            @click="startRegenerate"
          >
            <RefreshCw class="h-4 w-4" />重新生成
          </button>
        </div>
      </div>
    </div>

    <!-- Regenerate 中间态:要 OTP -->
    <div v-if="regenerating" class="card border-2 border-primary/30 bg-primary-light/30">
      <h3 class="mb-3 text-base font-bold text-slate-800">重新生成恢复码</h3>
      <p class="muted mb-3 text-xs">旧 10 个恢复码将全部失效。请输当前 TOTP app 上的 6 位 OTP 确认 —— 防止 token 被盗后偷换 codes 锁死你。</p>
      <label class="block">
        <span class="text-[11px] font-bold uppercase tracking-wider text-slate-500">6 位 OTP</span>
        <input
          v-model="regenerateCode"
          type="text"
          inputmode="numeric"
          maxlength="6"
          placeholder="000000"
          class="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 font-mono text-base tracking-widest focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
          @keydown.enter="submitRegenerate"
        >
      </label>
      <div class="mt-3 flex justify-end gap-2">
        <button type="button" class="btn btn-outline" :disabled="regenerateSubmitting" @click="cancelRegenerate">取消</button>
        <button type="button" class="btn btn-primary" :disabled="regenerateSubmitting || regenerateCode.length < 6" @click="submitRegenerate">
          {{ regenerateSubmitting ? '重新生成中…' : '确认重新生成' }}
        </button>
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
