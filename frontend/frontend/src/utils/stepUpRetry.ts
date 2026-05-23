/**
 * Step-up retry helper —— 把任何一个抛 step_up_required 的 api 调用包起来，
 * 自动 prompt 密码 → verify-password → 换新 token → 重试一次。
 *
 * 用法：
 *   await withStepUpRetry(() => apiJson(`/api/users/${id}`, 'DELETE'))
 *
 * `apiJson` 在 4xx 时抛 `new Error(detail)`，detail 是后端返回的字符串。
 * step-up 后端 detail 以 `step_up_required:` 起头，靠它识别。换 token 后
 * `api.ts` 每次重读 localStorage 拼 Authorization 头，写完即对下游生效。
 *
 * 抛特定错误码方便 caller 区分：
 *  - "step_up_cancelled" —— 用户在 prompt 里点了取消
 *  - "step_up_verify_failed" —— 密码错
 */
import { apiJson } from '../api'
import { promptPassword } from '../composables/usePasswordPrompt'

const TOKEN_KEY = 'dataops.token'

export async function withStepUpRetry<T>(op: () => Promise<T>): Promise<T> {
  try {
    return await op()
  } catch (err) {
    const msg = String((err as { message?: string } | null)?.message || '')
    if (!msg.startsWith('step_up_required:')) {
      throw err
    }
    // 走全局 modal（PasswordPromptModal 挂在 App.vue 顶层）—— 比 window.prompt
    // UX 好,且某些浏览器（Chrome 推 deprecation）会块 window.prompt
    const pw = await promptPassword('该操作需要重新输入密码确认 —— 验证成功后会自动重试。')
    if (!pw) {
      throw new Error('step_up_cancelled')
    }
    let data: { access_token: string }
    try {
      data = await apiJson<{ access_token: string }>(
        '/api/auth/verify-password', 'POST', { password: pw },
      )
    } catch {
      throw new Error('step_up_verify_failed')
    }
    localStorage.setItem(TOKEN_KEY, data.access_token)
    return await op()
  }
}
