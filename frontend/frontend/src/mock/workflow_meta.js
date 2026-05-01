// 元数据补全：后端 Workflow 模型只存了 name + nodes + variables。
// 在产品演进期间，我们把项目归属/负责人/调度/资产关系/质量检查这些
// "数据平台后台才会有"的维度先用 mock 补上，等后端落库后再切换。
//
// 通过工作流 id 或下标找到对应的元数据模板。

export const projects = [
  { id: 'all',     name: '全部项目' },
  { id: 'dw',      name: '数据仓库' },
  { id: 'risk',    name: '风控管理' },
  { id: 'growth',  name: '增长分析' },
  { id: 'finance', name: '财务报表' },
]

export const owners = [
  { id: 'all',      name: '全部负责人' },
  { id: 'zhangsan', name: '张三' },
  { id: 'lina',     name: '李娜' },
  { id: 'wangwei',  name: '王伟' },
  { id: 'sunqi',    name: '孙琪' },
  { id: 'zhoumin',  name: '周敏' },
]

export const scheduleTypes = [
  { id: 'all',      name: '全部调度' },
  { id: 'cron',     name: 'Cron 定时' },
  { id: 'sensor',   name: '事件触发' },
  { id: 'manual',   name: '手动触发' },
]

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

const TEMPLATES = [
  {
    project: 'dw', owner: '张三', description: '订单事实表日清洗作业流。每日 02:00 触发，依赖 ods.orders、ods.refunds，输出到 dwd.fact_orders。',
    tags: ['tier-1', 'prod', 'critical'],
    schedule: { cron: '0 2 * * *', text: '每日 02:00', type: 'cron', status: 'online' },
    alert_targets: ['张三', '@数据值班群'], queue: 'spark-large',
    input_assets:  [
      { key: 'ods/orders',  kind: 'table', health: 'healthy', last_materialized: '2026-05-02 01:55' },
      { key: 'ods/refunds', kind: 'table', health: 'stale',   last_materialized: '2026-04-30 02:00' },
    ],
    output_assets: [
      { key: 'dwd/fact_orders',  kind: 'table', downstream_count: 7 },
      { key: 'dwd/dim_orders',   kind: 'table', downstream_count: 3 },
    ],
    quality_checks: [
      { name: 'row_count_floor',     severity: '阻断', status: 'passed', message: '行数 ≥ 1000',                last_run: '2026-05-02 02:01:42' },
      { name: 'no_negative_amount',  severity: '阻断', status: 'passed', message: '没有负数金额行',              last_run: '2026-05-02 02:01:42' },
      { name: 'partition_coverage',  severity: '警告', status: 'failed', message: '近 7 天有 1 个分区缺失',       last_run: '2026-05-02 02:01:42' },
      { name: 'currency_allowlist',  severity: '阻断', status: 'passed', message: '币种全部在 {USD, EUR, CNY}',   last_run: '2026-05-02 02:01:42' },
    ],
  },
  {
    project: 'risk', owner: '王伟', description: '反欺诈规则评分作业流。每 30 分钟触发，对在线交易实时计算风险分。',
    tags: ['tier-1', 'realtime'],
    schedule: { cron: '*/30 * * * *', text: '每 30 分钟', type: 'cron', status: 'online' },
    alert_targets: ['王伟', '@风控值班'], queue: 'realtime',
    input_assets: [
      { key: 'ods/transactions', kind: 'stream', health: 'healthy', last_materialized: '实时' },
      { key: 'dim/users',        kind: 'table',  health: 'healthy', last_materialized: '2026-05-02 03:00' },
    ],
    output_assets: [
      { key: 'rt/fraud_scores',  kind: 'stream', downstream_count: 4 },
    ],
    quality_checks: [
      { name: 'score_distribution', severity: '警告', status: 'passed', message: '分布无突变',                  last_run: '2026-05-02 09:30:00' },
      { name: 'rule_coverage',      severity: '阻断', status: 'passed', message: '规则集全部命中',              last_run: '2026-05-02 09:30:00' },
    ],
  },
  {
    project: 'growth', owner: '孙琪', description: '广告归因日报。每日 05:15 触发，关联渠道日志和订单数据生成归因明细。',
    tags: ['daily', 'prod'],
    schedule: { cron: '15 5 * * *', text: '每日 05:15', type: 'cron', status: 'online' },
    alert_targets: ['孙琪'], queue: 'spark-default',
    input_assets: [
      { key: 'ods/ad_clicks',   kind: 'table', health: 'healthy', last_materialized: '2026-05-02 04:30' },
      { key: 'dwd/fact_orders', kind: 'table', health: 'healthy', last_materialized: '2026-05-02 02:01' },
    ],
    output_assets: [
      { key: 'app/ad_attribution_daily', kind: 'table', downstream_count: 2 },
    ],
    quality_checks: [
      { name: 'attribution_sum_match', severity: '阻断', status: 'passed', message: '归因金额 = 订单金额',     last_run: '2026-05-02 05:18:01' },
    ],
  },
  {
    project: 'finance', owner: '周敏', description: '现金流日报，对账系统的下游输入。每日 06:00 触发。',
    tags: ['daily', 'finance'],
    schedule: { cron: '0 6 * * *', text: '每日 06:00', type: 'cron', status: 'online' },
    alert_targets: ['周敏', '@财务对账群'], queue: 'default',
    input_assets: [
      { key: 'dwd/fact_orders',     kind: 'table', health: 'healthy', last_materialized: '2026-05-02 02:01' },
      { key: 'ods/refunds',         kind: 'table', health: 'stale',   last_materialized: '2026-04-30 02:00' },
      { key: 'dwd/fact_payments',   kind: 'table', health: 'healthy', last_materialized: '2026-05-02 03:00' },
    ],
    output_assets: [
      { key: 'app/cashflow_daily',  kind: 'table', downstream_count: 1 },
    ],
    quality_checks: [
      { name: 'balance_zero',  severity: '阻断', status: 'passed', message: '收支差额 = 0',  last_run: '2026-05-02 06:00:42' },
    ],
  },
]

// 简单地按下标取模分配模板。真实生产里这一层应该来自数据库列。
export function getMeta(workflow, index = 0) {
  if (!workflow) return TEMPLATES[0]
  return TEMPLATES[index % TEMPLATES.length]
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
