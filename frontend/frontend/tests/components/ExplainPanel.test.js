// ExplainPanel(v0.5)组件测试 —— render 状态 + 复制 + hints chip。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import ExplainPanel from '../../src/components/sql/ExplainPanel.vue'

describe('ExplainPanel', () => {
  beforeEach(() => {
    // 给 navigator.clipboard 打 mock,默认环境 jsdom 无 clipboard API
    Object.assign(navigator, {
      clipboard: { writeText: vi.fn().mockResolvedValue(undefined) },
    })
  })

  it('explain=null 渲染空态', () => {
    const wrapper = mount(ExplainPanel, { props: { explain: null } })
    expect(wrapper.text()).toContain('点击「Explain」查看执行计划')
  })

  it('unsupported 渲染明确原因 banner', () => {
    const wrapper = mount(ExplainPanel, {
      props: {
        explain: {
          success: false, dialect: 'oracle', columns: [], rows: [], explain_sql: '',
          elapsed_ms: 0, unsupported: true, error: 'Oracle EXPLAIN PLAN 未启用',
        },
      },
    })
    expect(wrapper.text()).toContain('暂不支持 EXPLAIN')
    expect(wrapper.text()).toContain('Oracle EXPLAIN PLAN 未启用')
  })

  it('error 路径渲染失败信息', () => {
    const wrapper = mount(ExplainPanel, {
      props: {
        explain: {
          success: false, dialect: 'mysql', columns: [], rows: [], explain_sql: 'EXPLAIN ...',
          elapsed_ms: 0, unsupported: false, error: 'syntax error near "FROM"',
        },
      },
    })
    expect(wrapper.text()).toContain('Explain 失败')
    expect(wrapper.text()).toContain('syntax error near')
  })

  it('success 渲染表格', () => {
    const wrapper = mount(ExplainPanel, {
      props: {
        explain: {
          success: true, dialect: 'mysql',
          columns: ['id', 'type', 'rows'],
          rows: [[1, 'ALL', 1000], [2, 'ref', 10]],
          explain_sql: 'EXPLAIN SELECT * FROM t',
          elapsed_ms: 5, unsupported: false, error: null,
        },
      },
    })
    expect(wrapper.findAll('thead th')).toHaveLength(3)
    expect(wrapper.findAll('tbody tr')).toHaveLength(2)
    expect(wrapper.text()).toContain('ALL')
    expect(wrapper.text()).toContain('1000')
  })

  it('hints 数组渲染为 chip 列表', () => {
    const wrapper = mount(ExplainPanel, {
      props: {
        explain: {
          success: true, dialect: 'mysql', columns: ['a'], rows: [[1]],
          explain_sql: '...', elapsed_ms: 1, unsupported: false, error: null,
          hints: [
            { code: 'select_star', severity: 'warning', message: 'SELECT * 提醒' },
            { code: 'no_where', severity: 'warning', message: '无 WHERE 提醒' },
          ],
        },
      },
    })
    const chips = wrapper.findAll('[data-hint-code]')
    expect(chips).toHaveLength(2)
    expect(chips[0].attributes('data-hint-code')).toBe('select_star')
    expect(wrapper.text()).toContain('SELECT * 提醒')
    expect(wrapper.text()).toContain('无 WHERE 提醒')
  })

  it('hints 在 unsupported 路径也显示(纯文本规则不依赖 plan)', () => {
    const wrapper = mount(ExplainPanel, {
      props: {
        explain: {
          success: false, unsupported: true, dialect: 'oracle',
          columns: [], rows: [], explain_sql: '', elapsed_ms: 0,
          error: '不支持',
          hints: [{ code: 'select_star', severity: 'warning', message: '提醒' }],
        },
      },
    })
    expect(wrapper.findAll('[data-hint-code]')).toHaveLength(1)
    expect(wrapper.text()).toContain('暂不支持')
  })

  it('复制按钮调 navigator.clipboard.writeText', async () => {
    const wrapper = mount(ExplainPanel, {
      props: {
        explain: {
          success: true, dialect: 'mysql',
          columns: ['id', 'type'], rows: [[1, 'ALL']],
          explain_sql: 'EXPLAIN SELECT 1', elapsed_ms: 1, unsupported: false, error: null,
        },
      },
    })
    const btns = wrapper.findAll('button')
    const copyBtn = btns.find(b => b.text().includes('复制'))
    expect(copyBtn).toBeTruthy()
    await copyBtn.trigger('click')
    expect(navigator.clipboard.writeText).toHaveBeenCalled()
    // copied 是 Markdown 表格格式,含 explain_sql + 表头
    const text = navigator.clipboard.writeText.mock.calls[0][0]
    expect(text).toContain('EXPLAIN SELECT 1')
    expect(text).toContain('| id |')
  })

  it('JSON 复制按钮拷贝 JSON 序列化', async () => {
    const wrapper = mount(ExplainPanel, {
      props: {
        explain: {
          success: true, dialect: 'mysql', columns: ['id'], rows: [[1]],
          explain_sql: 'EXPLAIN ...', elapsed_ms: 1, unsupported: false, error: null,
          hints: [{ code: 'select_star', severity: 'warning', message: 'x' }],
        },
      },
    })
    const jsonBtn = wrapper.findAll('button').find(b => b.text() === 'JSON')
    expect(jsonBtn).toBeTruthy()
    await jsonBtn.trigger('click')
    const text = navigator.clipboard.writeText.mock.calls[0][0]
    const parsed = JSON.parse(text)
    expect(parsed.dialect).toBe('mysql')
    expect(parsed.columns).toEqual(['id'])
    expect(parsed.hints).toHaveLength(1)
  })
})
