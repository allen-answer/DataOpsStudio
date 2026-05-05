// useNoticeStore —— notice 4s 自动消失 / actionStatus 切换 / AI translation 弹卡。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { useNoticeStore } from '../../src/stores/notice'

describe('useNoticeStore', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  it('setNotice 立即生效，4s 后自动清空', () => {
    const store = useNoticeStore()
    store.setNotice('保存失败：连接拒绝')
    expect(store.notice).toBe('保存失败：连接拒绝')
    vi.advanceTimersByTime(3999)
    expect(store.notice).toBe('保存失败：连接拒绝')  // 还没到 4s
    vi.advanceTimersByTime(2)
    expect(store.notice).toBe('')
  })

  it('连续 setNotice 重置 timer（第二次的 message 也保留 4s）', () => {
    const store = useNoticeStore()
    store.setNotice('first')
    vi.advanceTimersByTime(2000)
    store.setNotice('second')
    vi.advanceTimersByTime(2000)
    expect(store.notice).toBe('second')  // 老 timer 应被清，新的还有 2s
    vi.advanceTimersByTime(2001)
    expect(store.notice).toBe('')
  })

  it('setActionStatus 改 type / title / message', () => {
    const store = useNoticeStore()
    store.setActionStatus('running', '执行中', '请稍候')
    expect(store.actionStatus.type).toBe('running')
    expect(store.actionStatus.title).toBe('执行中')
    expect(store.actionStatus.message).toBe('请稍候')

    store.setActionStatus('success', '完成')  // 不传 message → 空串
    expect(store.actionStatus.message).toBe('')
  })

  it('setAITranslation / dismissAITranslation 控制 AI 翻译卡', () => {
    const store = useNoticeStore()
    expect(store.aiTranslation).toBeNull()
    const payload = {
      translation: '数据库连接被拒绝',
      suggestions: ['检查防火墙', '确认 host:port'],
    }
    store.setAITranslation(payload)
    expect(store.aiTranslation).toEqual(payload)
    store.dismissAITranslation()
    expect(store.aiTranslation).toBeNull()
  })
})
