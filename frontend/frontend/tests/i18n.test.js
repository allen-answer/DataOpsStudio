// i18n —— locale 解析 / setLocale 持久化 / messages 完整性。
import { describe, it, expect, beforeEach } from 'vitest'
import { i18n, setLocale, SUPPORTED_LOCALES } from '../src/i18n'
import zh from '../src/i18n/zh'
import en from '../src/i18n/en'

describe('i18n', () => {
  beforeEach(() => {
    // 重置回默认 locale 让每个 case 独立
    setLocale('zh-CN')
  })

  it('zh / en 两份 messages 顶层 key 完全对齐（不漏翻译）', () => {
    expect(Object.keys(zh).sort()).toEqual(Object.keys(en).sort())
    // 二级也对齐
    for (const top of Object.keys(zh)) {
      expect(Object.keys(zh[top]).sort()).toEqual(Object.keys(en[top]).sort())
    }
  })

  // PR23：递归对齐所有嵌套层级（lineagePanel.summary.varKinds.* 这类深层 key
  // 也要中英文同步，避免硬编码字符串迁移到 i18n 时忘加翻译）
  it('zh / en 所有嵌套层级 key 完全对齐', () => {
    function flatKeys(obj, prefix = '') {
      const out = []
      for (const k of Object.keys(obj || {})) {
        const path = prefix ? `${prefix}.${k}` : k
        const v = obj[k]
        if (v && typeof v === 'object' && !Array.isArray(v)) {
          out.push(...flatKeys(v, path))
        } else {
          out.push(path)
        }
      }
      return out.sort()
    }
    expect(flatKeys(zh)).toEqual(flatKeys(en))
  })

  it('SUPPORTED_LOCALES 跟 messages 一一对应', () => {
    const codes = SUPPORTED_LOCALES.map(l => l.code).sort()
    expect(codes).toEqual(['en-US', 'zh-CN'])
  })

  it('setLocale 落 localStorage + i18n.global.locale 切换', () => {
    setLocale('en-US')
    expect(localStorage.getItem('dataops.locale')).toBe('en-US')
    expect(i18n.global.locale.value).toBe('en-US')
    expect(i18n.global.t('nav.datasources')).toBe('Data Sources')
  })

  it('setLocale 拒绝不支持的 locale', () => {
    setLocale('fr-FR')
    expect(localStorage.getItem('dataops.locale')).not.toBe('fr-FR')
  })

  it('zh nav.* 都有非空翻译', () => {
    for (const key of ['datasources', 'dataCompare', 'workflows', 'lineage', 'history']) {
      expect(zh.nav[key]).toBeTruthy()
      expect(en.nav[key]).toBeTruthy()
    }
  })

  it('login.welcome 模板插值正常工作', () => {
    setLocale('zh-CN')
    expect(i18n.global.t('login.welcome', { name: 'alice' })).toBe('欢迎，alice')
    setLocale('en-US')
    expect(i18n.global.t('login.welcome', { name: 'alice' })).toBe('Welcome, alice')
  })
})
