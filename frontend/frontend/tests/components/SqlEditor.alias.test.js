// SqlEditor alias 推导单元测试 —— 直接测正则,不 mount 组件。
import { describe, it, expect } from 'vitest'

// 复制 SqlEditor.vue 里同样的实现(单测用)。若 SqlEditor 那边正则改了,
// 这里跟着改 —— 我们故意复制而不 import,因为 SqlEditor 用 <script setup>
// 没 export,而且这函数纯字符串处理,复制最简单。
const _SQL_RESERVED = new Set([
  'where', 'group', 'order', 'having', 'limit', 'offset',
  'left', 'right', 'inner', 'outer', 'cross', 'full', 'natural',
  'on', 'using', 'join', 'union', 'select', 'from', 'as',
  'and', 'or', 'not', 'in', 'is', 'null', 'case', 'when',
  'then', 'else', 'end', 'between', 'like', 'exists',
])

function _stripSqlComments(text) {
  return String(text || '')
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/--[^\n]*/g, ' ')
}

function deriveAliasMap(sqlText) {
  const out = {}
  const cleaned = _stripSqlComments(sqlText)
  const re = /(?:\bfrom|\bjoin)\s+([`"]?[\w$]+[`"]?(?:\.[`"]?[\w$]+[`"]?)?)\s+(?:as\s+)?([a-zA-Z_]\w*)\b/gi
  let m
  while ((m = re.exec(cleaned)) !== null) {
    const tableRef = m[1].replace(/[`"]/g, '')
    const alias = m[2]
    if (_SQL_RESERVED.has(alias.toLowerCase())) continue
    out[alias] = tableRef
  }
  return out
}

describe('SqlEditor alias derivation', () => {
  it('simple FROM table alias', () => {
    expect(deriveAliasMap('SELECT t.id FROM users t')).toEqual({ t: 'users' })
  })

  it('explicit AS keyword', () => {
    expect(deriveAliasMap('SELECT u.id FROM users AS u')).toEqual({ u: 'users' })
  })

  it('schema-qualified table', () => {
    expect(deriveAliasMap('SELECT u.id FROM ods.users u')).toEqual({ u: 'ods.users' })
  })

  it('JOIN with alias', () => {
    const result = deriveAliasMap('SELECT u.id, o.amount FROM users u JOIN orders o ON u.id=o.user_id')
    expect(result).toEqual({ u: 'users', o: 'orders' })
  })

  it('LEFT/INNER/RIGHT JOIN handled (left/inner/right not eaten as alias)', () => {
    const result = deriveAliasMap('FROM users u LEFT JOIN orders o ON u.id=o.user_id')
    expect(result).toEqual({ u: 'users', o: 'orders' })
  })

  it('multiple joins with mix of AS and bare alias', () => {
    const sql = `
      SELECT u.id, o.amount, p.name
      FROM users u
      JOIN orders AS o ON u.id = o.user_id
      LEFT JOIN products p ON o.product_id = p.id
    `
    expect(deriveAliasMap(sql)).toEqual({ u: 'users', o: 'orders', p: 'products' })
  })

  it('reserved word after FROM is NOT taken as alias', () => {
    // 'FROM users WHERE id=1' —— WHERE 不应该被当作 alias
    expect(deriveAliasMap('SELECT * FROM users WHERE id=1')).toEqual({})
  })

  it('case-insensitive FROM/JOIN/AS keywords', () => {
    expect(deriveAliasMap('select t.id from Users T')).toEqual({ T: 'Users' })
    expect(deriveAliasMap('SELECT * from users as t')).toEqual({ t: 'users' })
  })

  it('backtick-quoted table name', () => {
    expect(deriveAliasMap('SELECT t.id FROM `users` t')).toEqual({ t: 'users' })
  })

  it('comments are stripped before scanning', () => {
    // 注释里的 "FROM evil e" 不该污染 alias map
    const sql = '-- FROM evil e\nSELECT t.id FROM users t'
    expect(deriveAliasMap(sql)).toEqual({ t: 'users' })
    const sql2 = '/* FROM evil e */ SELECT t.id FROM users t'
    expect(deriveAliasMap(sql2)).toEqual({ t: 'users' })
  })

  it('no FROM clause → empty map', () => {
    expect(deriveAliasMap('SELECT 1')).toEqual({})
  })

  it('FROM without alias → empty map', () => {
    expect(deriveAliasMap('SELECT id FROM users')).toEqual({})
  })

  it('empty / null input', () => {
    expect(deriveAliasMap('')).toEqual({})
    expect(deriveAliasMap(null)).toEqual({})
  })
})
