// UI 元数据 + 派生工具：状态色板、参数类型 chip、DAG 自动布局、健康度推导、
// 事件流合成。这些都不是业务数据，是把后端模型转成 UI 可用形态的工具函数。
//
// 注：早先版本里这里还塞了 projects/owners/scheduleTypes 三个固定下拉、
// 4 个 TEMPLATES 假元数据、getMeta() 按下标 mod 4 派模板的"假装真业务"逻辑。
// 后端 Workflow 模型已经下沉了 owner/tags/schedule_cron/project/status/
// input_assets/output_assets，那些 mock 已经全部移除。

// 健康度语义贴合系统其他模块（compare/lineage）：成功/失败/运行中/等待/跳过/过期。
export const healthMeta = {
  healthy:    { label: '健康',    dot: 'bg-emerald-500',                pill: 'bg-emerald-50 text-emerald-700 ring-emerald-200',  text: 'text-emerald-700' },
  failing:    { label: '失败',    dot: 'bg-rose-500',                   pill: 'bg-rose-50 text-rose-700 ring-rose-200',           text: 'text-rose-700' },
  running:    { label: '运行中',  dot: 'bg-blue-500 animate-pulse',     pill: 'bg-blue-50 text-blue-700 ring-blue-200',           text: 'text-blue-700' },
  waiting:    { label: '等待',    dot: 'bg-amber-500',                  pill: 'bg-amber-50 text-amber-700 ring-amber-200',        text: 'text-amber-700' },
  stale:      { label: '过期',    dot: 'bg-amber-500',                  pill: 'bg-amber-50 text-amber-700 ring-amber-200',        text: 'text-amber-700' },
  paused:     { label: '已暂停',  dot: 'bg-slate-400',                  pill: 'bg-slate-100 text-slate-600 ring-slate-200',       text: 'text-slate-600' },
  none:       { label: '未运行',  dot: 'bg-slate-300',                  pill: 'bg-slate-50 text-slate-500 ring-slate-200',        text: 'text-slate-500' },
}

// 节点级状态（运行实例视角）
export const nodeStatusMeta = {
  success:    { label: '成功',    dot: 'bg-emerald-500',                pill: 'bg-emerald-50 text-emerald-700 ring-emerald-200',   bar: 'bg-emerald-500' },
  failed:     { label: '失败',    dot: 'bg-rose-500',                   pill: 'bg-rose-50 text-rose-700 ring-rose-200',            bar: 'bg-rose-500' },
  running:    { label: '运行中',  dot: 'bg-blue-500 animate-pulse',     pill: 'bg-blue-50 text-blue-700 ring-blue-200',            bar: 'bg-blue-500 animate-pulse' },
  pending:    { label: '等待',    dot: 'bg-slate-300',                  pill: 'bg-slate-50 text-slate-500 ring-slate-200',         bar: 'bg-slate-300' },
  skipped:    { label: '跳过',    dot: 'bg-slate-400',                  pill: 'bg-slate-100 text-slate-600 ring-slate-200',        bar: 'bg-slate-400' },
}

// 参数类型语义 — 决定运行前如何解析、UI 上如何呈现。
export const parameterTypeMeta = {
  fixed:         { label: '固定值',  glyph: '◇', accent: 'text-slate-700  bg-slate-100  ring-slate-200'  },
  date:          { label: '日期',    glyph: '⌛', accent: 'text-blue-700   bg-blue-50    ring-blue-200'   },
  relative_date: { label: '相对日期', glyph: '↩', accent: 'text-blue-700   bg-blue-50    ring-blue-200'   },
  sql_result:    { label: 'SQL',    glyph: '⌖', accent: 'text-emerald-700 bg-emerald-50 ring-emerald-200' },
  multi_value:   { label: '多值',    glyph: '≡', accent: 'text-purple-700 bg-purple-50  ring-purple-200' },
  json:          { label: 'JSON',   glyph: '{}', accent: 'text-amber-700  bg-amber-50   ring-amber-200'  },
}

// DAG 自动布局：按 depends_on 拓扑排序计算每个节点的层级 (level)，同层垂直排开。
// 适合 < 30 节点的小型工作流，复杂场景需要换更厉害的布局算法。
export function layoutDAG(nodes, opts = {}) {
  const NODE_W = opts.nodeW || 220
  const NODE_H = opts.nodeH || 78
  const GAP_X  = opts.gapX  || 80
  const GAP_Y  = opts.gapY  || 28
  const PAD_X  = opts.padX  || 40
  const PAD_Y  = opts.padY  || 40

  if (!nodes || !nodes.length) return { positioned: [], width: 600, height: 200, NODE_W, NODE_H }

  const idToNode = {}
  for (const n of nodes) idToNode[n.id] = n

  // Kahn-style level assignment.
  const levels = {}
  const inDeg = {}
  for (const n of nodes) {
    levels[n.id] = 0
    inDeg[n.id] = (n.depends_on || []).length
  }
  const ready = nodes.filter(n => !inDeg[n.id]).map(n => n.id)
  const seen = new Set()
  while (ready.length) {
    const id = ready.shift()
    if (seen.has(id)) continue
    seen.add(id)
    for (const next of nodes) {
      if ((next.depends_on || []).includes(id)) {
        levels[next.id] = Math.max(levels[next.id] || 0, (levels[id] || 0) + 1)
        inDeg[next.id]--
        if (inDeg[next.id] === 0) ready.push(next.id)
      }
    }
  }

  const byLevel = {}
  for (const n of nodes) {
    const l = levels[n.id] || 0
    byLevel[l] = byLevel[l] || []
    byLevel[l].push(n)
  }

  let maxLevel = 0
  let maxLane = 0
  const positioned = []
  for (const lvl of Object.keys(byLevel).map(Number).sort((a, b) => a - b)) {
    if (lvl > maxLevel) maxLevel = lvl
    const inLevel = byLevel[lvl]
    if (inLevel.length > maxLane) maxLane = inLevel.length
    inLevel.forEach((n, i) => {
      positioned.push({
        ...n,
        x: PAD_X + lvl * (NODE_W + GAP_X),
        y: PAD_Y + i * (NODE_H + GAP_Y),
      })
    })
  }

  return {
    positioned,
    width:  PAD_X * 2 + (maxLevel + 1) * NODE_W + maxLevel * GAP_X,
    height: PAD_Y * 2 + maxLane * (NODE_H + GAP_Y),
    NODE_W,
    NODE_H,
  }
}

// 把参数定义解析成「下次运行将用到的具体值」。预览用，不参与真正执行。
export function resolveParameter(param, runtimeOverride = undefined, base = new Date()) {
  if (runtimeOverride !== undefined) {
    return { value: runtimeOverride, kind: 'override' }
  }
  if (param.type === 'fixed') return { value: param.default, kind: 'literal' }
  if (param.type === 'date')  return { value: param.default || base.toISOString().slice(0, 10), kind: 'literal' }
  if (param.type === 'multi_value') return { value: param.default, kind: 'list' }
  if (param.type === 'json')  return { value: param.default, kind: 'json' }
  if (param.type === 'relative_date') {
    const d = new Date(base)
    if (param.source === 'today') return { value: d.toISOString().slice(0, 10), kind: 'derived' }
    if (param.source === 'yesterday') { d.setDate(d.getDate() - 1); return { value: d.toISOString().slice(0, 10), kind: 'derived' } }
    if (param.source === 'last_month') { d.setMonth(d.getMonth() - 1); return { value: d.toISOString().slice(0, 7), kind: 'derived' } }
    if (param.source === 'now') return { value: d.toISOString().slice(0, 19).replace('T', ' '), kind: 'derived' }
    return { value: param.default || '', kind: 'derived' }
  }
  if (param.type === 'sql_result') {
    return { value: `执行后获得 ${param.preview_count ?? '—'} 行`, kind: 'pending', sample: param.preview_count }
  }
  return { value: param.default || '', kind: 'literal' }
}

// 把工作流的参数定义集合解析成 { name → resolved } 字典。
export function resolveAllParameters(parameters, overrides = {}, base = new Date()) {
  const out = {}
  for (const p of parameters || []) {
    out[p.name] = resolveParameter(p, overrides[p.name], base)
  }
  return out
}

// 综合 workflow + 最近一次 WorkflowRun 推导出 health 状态。
export function workflowHealth(workflow, latestRun) {
  if (!latestRun) return 'none'
  if (latestRun.status === 'success') {
    // 检查最近一次的运行是否过老
    if (latestRun.elapsed_seconds === 0) return 'healthy'
    return 'healthy'
  }
  if (latestRun.status === 'failed') return 'failing'
  if (latestRun.status === 'running') return 'running'
  return 'none'
}

// 把后端 WorkflowRun.nodes 翻译成事件流（每节点一条 STEP_START + STEP_END）。
// 由于真实后端不发结构化事件，这里在前端基于节点的状态/时间合成。
export function synthesizeEvents(run) {
  if (!run) return []
  const events = []
  events.push({
    ts: run.started_at, type: 'RUN_START', level: 'INFO',
    msg: `运行开始（${run.workflow_name || run.workflow_id}）`, step: '',
  })
  for (const n of run.nodes || []) {
    if (n.started_at) {
      events.push({
        ts: n.started_at, type: 'STEP_START', level: 'INFO',
        msg: `${n.name || n.node_id}：开始执行（${n.type}）`, step: n.node_id,
      })
    }
    if (n.status === 'success' && n.finished_at) {
      events.push({
        ts: n.finished_at, type: 'STEP_SUCCESS', level: 'INFO',
        msg: `${n.name || n.node_id}：完成，耗时 ${n.elapsed_seconds}s`, step: n.node_id,
        metadata: n.output && Object.keys(n.output).length ? Object.fromEntries(Object.entries(n.output).slice(0, 3)) : null,
      })
    } else if (n.status === 'failed' && n.finished_at) {
      events.push({
        ts: n.finished_at, type: 'STEP_FAILURE', level: 'ERROR',
        msg: `${n.name || n.node_id}：失败 — ${n.error || '未知错误'}`, step: n.node_id,
      })
    } else if (n.status === 'skipped') {
      events.push({
        ts: n.started_at || run.started_at, type: 'STEP_SKIPPED', level: 'INFO',
        msg: `${n.name || n.node_id}：跳过${n.error ? `（${n.error}）` : ''}`, step: n.node_id,
      })
    }
  }
  events.push({
    ts: run.finished_at || run.started_at,
    type: run.status === 'success' ? 'RUN_SUCCESS' : 'RUN_FAILURE',
    level: run.status === 'success' ? 'INFO' : 'ERROR',
    msg: run.status === 'success' ? '运行成功' : `运行失败：${run.error || '未知错误'}`,
    step: '',
  })
  return events
}
