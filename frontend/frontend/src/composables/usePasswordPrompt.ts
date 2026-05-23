/**
 * 全局密码 prompt —— step-up 再认证用，取代 window.prompt。
 *
 * 用法（在任意 async 函数里）：
 *   const pw = await promptPassword('该操作需要重新输入密码确认')
 *   if (!pw) return  // 用户取消
 *
 * 组件侧（App.vue 顶层挂一个 <PasswordPromptModal />）通过 usePasswordPromptState
 * 读 state.open / state.message，按 confirmPassword(pw) / cancelPassword() 关闭。
 */
import { ref } from 'vue'

interface PromptState {
  open: boolean
  message: string
  resolve: ((pw: string | null) => void) | null
}

const _state = ref<PromptState>({
  open: false,
  message: '',
  resolve: null,
})


export function usePasswordPromptState() {
  return _state
}


/** 弹密码 prompt，resolve 用户输入的密码字符串，取消返回 null。 */
export function promptPassword(message = '请输入密码确认'): Promise<string | null> {
  return new Promise((resolve) => {
    // 旧 prompt 没关掉就又被叫 —— 先把它 reject 成 null
    if (_state.value.resolve) {
      _state.value.resolve(null)
    }
    _state.value.message = message
    _state.value.open = true
    _state.value.resolve = resolve
  })
}


export function confirmPassword(pw: string): void {
  const r = _state.value.resolve
  _state.value.open = false
  _state.value.resolve = null
  if (r) r(pw)
}


export function cancelPassword(): void {
  const r = _state.value.resolve
  _state.value.open = false
  _state.value.resolve = null
  if (r) r(null)
}
