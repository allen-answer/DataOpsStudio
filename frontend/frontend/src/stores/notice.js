/**
 * 全局 notice + actionStatus —— Pinia store。
 *
 * 抽这个 store 是 Pinia 化 App.vue 的第一步：notice / actionStatus 完全独立，
 * 没跟其它领域 state 耦合，迁移最简单。其它 store 后续陆续抽。
 *
 * App.vue 仍通过 `provide('app', { notice, actionStatus, setNotice, setActionStatus })`
 * 暴露同名 ref / reactive 给现有 view（inject('app')）使用 —— backward compat。
 * 新代码可以直接 `useNoticeStore()`。
 */
import { reactive, ref } from 'vue'
import { defineStore } from 'pinia'

export const useNoticeStore = defineStore('notice', () => {
  const notice = ref('')
  const noticeTimer = ref(null)
  const actionStatus = reactive({
    type: 'idle',
    title: '等待操作',
    message: '保存任务后可执行对比、预览、后台执行或复制任务。',
  })

  // AI 错误翻译：api.js 在 5xx 错误时调 /api/ai/translate-error，把结果丢这里
  // 让 AppShell 弹更醒目的卡片（不是普通 toast 4 秒就消失的那种）
  const aiTranslation = ref(null)  // { translation, suggestions, original } | null

  function setNotice(msg) {
    notice.value = msg
    if (noticeTimer.value) clearTimeout(noticeTimer.value)
    if (msg) {
      noticeTimer.value = setTimeout(() => { notice.value = '' }, 4000)
    }
  }

  function setActionStatus(type, title, message = '') {
    actionStatus.type = type
    actionStatus.title = title
    actionStatus.message = message
  }

  function setAITranslation(payload) {
    aiTranslation.value = payload
  }

  function dismissAITranslation() {
    aiTranslation.value = null
  }

  return {
    notice, actionStatus, aiTranslation,
    setNotice, setActionStatus,
    setAITranslation, dismissAITranslation,
  }
})
