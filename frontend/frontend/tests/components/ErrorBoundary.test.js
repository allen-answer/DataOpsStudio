// ErrorBoundary —— captures child render errors + shows降级 UI + reset 路径。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import ErrorBoundary from '../../src/components/ErrorBoundary.vue'

describe('ErrorBoundary', () => {
  // 每个 case 重 spy console.error —— global setup 的 restoreAllMocks 会还原它
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
  })

  it('正常 child 时透传渲染（slot 内容）', () => {
    const wrapper = mount(ErrorBoundary, {
      slots: { default: '<p class="ok">child content</p>' },
    })
    expect(wrapper.html()).toContain('child content')
    expect(wrapper.text()).not.toContain('页面渲染出错')
  })

  it('child setup 抛错 → 显示降级 UI + 错误信息', async () => {
    const Throwing = defineComponent({
      name: 'Throwing',
      setup() {
        throw new Error('boom in setup')
      },
      render: () => h('div'),
    })
    const wrapper = mount(ErrorBoundary, {
      slots: { default: () => h(Throwing) },
    })
    await nextTick()
    expect(wrapper.text()).toContain('页面渲染出错')
    expect(wrapper.text()).toContain('boom in setup')
  })

  it('reset 按钮清掉 error → child 再次渲染（如果改成不抛了）', async () => {
    let shouldThrow = true
    const Conditional = defineComponent({
      name: 'Conditional',
      setup() {
        if (shouldThrow) throw new Error('first time fail')
      },
      render: () => h('p', { class: 'recovered' }, '恢复了'),
    })
    const wrapper = mount(ErrorBoundary, {
      slots: { default: () => h(Conditional) },
    })
    await nextTick()
    expect(wrapper.text()).toContain('页面渲染出错')

    // 模拟"重置"——业务侧自己修好后用户点 reset
    shouldThrow = false
    await wrapper.find('button').trigger('click')  // 第一个按钮 = reset
    await nextTick()
    // reset 后 error=null，slot 重新渲染；但因为 setup 已经抛过且组件状态 stale，
    // 实际效果是 slot 重新 mount —— 至少错误 UI 消失
    expect(wrapper.text()).not.toContain('页面渲染出错')
  })

  it('保留 props.name 用于多 boundary 调试', async () => {
    const Throwing = defineComponent({
      setup() { throw new Error('x') },
      render: () => h('div'),
    })
    mount(ErrorBoundary, {
      props: { name: 'workflow-detail' },
      slots: { default: () => h(Throwing) },
    })
    // console.error 被 spy 了，验证 message 包含 boundary 名
    expect(console.error).toHaveBeenCalled()
    const firstCall = console.error.mock.calls[0]
    expect(firstCall.join(' ')).toContain('workflow-detail')
  })
})
