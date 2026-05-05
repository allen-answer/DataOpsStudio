/**
 * vue-i18n 配置 —— Phase S4.C bootstrap。
 *
 * 范围（首批）：
 * - sidebar nav 5 项 + admin nav 5 项的 label
 * - LoginView 表单字段 / 按钮
 * - 几个全局 notice / actionStatus 关键文案
 * - topbar 语言切换按钮
 *
 * 不在首批：详情页字段 / 工作流编辑器 / 血缘 9-tab —— 字符串多，后续 PR
 * 一片一片抽，避免单 PR 改太多文件让 review 难做。
 *
 * locale persist key: dataops.locale；默认 zh-CN（项目主语言）。
 * 切 locale 不打 API（messages 都 inline），刷新即时生效。
 */
import { createI18n } from 'vue-i18n'
import zh from './zh'
import en from './en'


const LOCALE_KEY = 'dataops.locale'
const DEFAULT_LOCALE = 'zh-CN'
const SUPPORTED = ['zh-CN', 'en-US']


function resolveInitialLocale() {
  const stored = localStorage.getItem(LOCALE_KEY) || ''
  if (SUPPORTED.includes(stored)) return stored
  // 跟浏览器语言；不命中走默认
  const browser = (navigator.language || '').toLowerCase()
  if (browser.startsWith('en')) return 'en-US'
  return DEFAULT_LOCALE
}


export const i18n = createI18n({
  legacy: false,             // composition API 模式（useI18n in setup）
  globalInjection: true,     // 模板里 $t 不用 import 也能用
  locale: resolveInitialLocale(),
  fallbackLocale: DEFAULT_LOCALE,
  messages: {
    'zh-CN': zh,
    'en-US': en,
  },
  // 缺 key 时不报错（生产更友好；dev 下 console 会 warn）
  missingWarn: false,
  fallbackWarn: false,
})


/**
 * 切语言 + 持久化。组件用 `useI18n().locale.value = 'en-US'` 也行，
 * 但走这个 helper 才能落 localStorage。
 */
export function setLocale(locale) {
  if (!SUPPORTED.includes(locale)) return
  i18n.global.locale.value = locale
  localStorage.setItem(LOCALE_KEY, locale)
  // <html lang="..."> 改一下 —— 给屏幕阅读器 / SEO
  if (typeof document !== 'undefined') {
    document.documentElement.lang = locale
  }
}


export const SUPPORTED_LOCALES = [
  { code: 'zh-CN', label: '中文' },
  { code: 'en-US', label: 'English' },
]
