// Mock data for the Dagster-style asset-oriented prototype. Shaped like
// what `dagster.io/docs` describes: assets, materializations, asset checks,
// partitions, and structured run events.

export const assetGroups = [
  { id: 'raw',       name: 'raw',       desc: '从生产系统接入的原始数据',     color: '#64748b' },
  { id: 'staging',   name: 'staging',   desc: '清洗 + 类型化的中间模型',       color: '#3b82f6' },
  { id: 'analytics', name: 'analytics', desc: '面向业务的分析层资产',         color: '#10b981' },
  { id: 'ml',        name: 'ml',        desc: '特征存储 + 训练模型',          color: '#8b5cf6' },
]

// Asset "kind" maps loosely to the underlying compute. Dagster surfaces
// these as little badges on each asset card (dbt / python / spark / …).
export const assetKinds = {
  table:  { label: '表',     glyph: '⊞',   accent: '#0284c7' },
  view:   { label: '视图',   glyph: '◇',   accent: '#0891b2' },
  python: { label: 'Python', glyph: 'Py',  accent: '#059669' },
  dbt:    { label: 'dbt',    glyph: 'dbt', accent: '#ea580c' },
  spark:  { label: 'Spark',  glyph: '✦',   accent: '#ca8a04' },
  ml:     { label: '模型',   glyph: '◈',   accent: '#7c3aed' },
}

// Health states — 资产健康度，跟系统其他视图（对比任务、作业流）保持
// 一致的颜色语言：emerald 成功 / amber 警告 / rose 失败 / blue 进行中。
export const assetHealth = {
  fresh:        { label: '最新',   dot: 'bg-emerald-500',            pill: 'bg-emerald-50 text-emerald-700 ring-emerald-200' },
  stale:        { label: '过期',   dot: 'bg-amber-500',              pill: 'bg-amber-50 text-amber-700 ring-amber-200' },
  failed:       { label: '失败',   dot: 'bg-rose-500',               pill: 'bg-rose-50 text-rose-700 ring-rose-200' },
  materializing:{ label: '物化中', dot: 'bg-blue-500 animate-pulse', pill: 'bg-blue-50 text-blue-700 ring-blue-200' },
  none:         { label: '未运行', dot: 'bg-slate-300',              pill: 'bg-slate-100 text-slate-500 ring-slate-200' },
}

// Layout: 4 vertical lanes (one per group) so the layered architecture
// is obvious at a glance. Coordinates are pre-computed so the SVG
// canvas can render a stable layout without a real graph library.
export const assets = [
  // --- raw lane ---
  { key: 'raw/orders',         group: 'raw', kind: 'table',  health: 'fresh',         last_materialized: '2026-05-02 02:00:13', last_run_id: '20260502-020000-fcd2', partitions: { total: 30, fresh: 30, stale: 0, failed: 0 }, upstream: [], owner: 'data-platform',  x: 60,   y: 80,  rows: 1284567,  duration: '12s'   },
  { key: 'raw/users',          group: 'raw', kind: 'table',  health: 'fresh',         last_materialized: '2026-05-02 02:00:08', last_run_id: '20260502-020000-fcd2', partitions: null,                                            upstream: [], owner: 'data-platform',  x: 60,   y: 200, rows: 4128301,  duration: '8s'    },
  { key: 'raw/refunds',        group: 'raw', kind: 'table',  health: 'stale',         last_materialized: '2026-04-30 02:00:11', last_run_id: '20260430-020000-7777', partitions: { total: 30, fresh: 28, stale: 2, failed: 0 }, upstream: [], owner: 'data-platform',  x: 60,   y: 320, rows: 18441,    duration: '4s'    },
  { key: 'raw/products',       group: 'raw', kind: 'view',   health: 'fresh',         last_materialized: '2026-05-02 02:00:03', last_run_id: '20260502-020000-fcd2', partitions: null,                                            upstream: [], owner: 'data-platform',  x: 60,   y: 440, rows: 8201,     duration: '1s'    },

  // --- staging lane ---
  { key: 'staging/orders_clean', group: 'staging', kind: 'dbt',    health: 'failed',        last_materialized: '2026-05-02 02:01:45', last_run_id: '20260502-020000-fcd2', partitions: { total: 30, fresh: 29, stale: 0, failed: 1 }, upstream: ['raw/orders', 'raw/refunds'], owner: 'analytics',   x: 340,  y: 140, rows: 1265432,  duration: '1m43s' },
  { key: 'staging/users_clean',  group: 'staging', kind: 'dbt',    health: 'fresh',         last_materialized: '2026-05-02 02:00:55', last_run_id: '20260502-020000-fcd2', partitions: null,                                            upstream: ['raw/users'],                  owner: 'analytics',   x: 340,  y: 260, rows: 4082911,  duration: '47s'   },
  { key: 'staging/sessions',     group: 'staging', kind: 'python', health: 'materializing', last_materialized: '2026-05-02 09:30:00', last_run_id: '20260502-093000-aaaa', partitions: { total: 30, fresh: 29, stale: 0, failed: 0 }, upstream: ['raw/users'],                  owner: 'growth',      x: 340,  y: 380, rows: 12489201, duration: '物化中'  },

  // --- analytics lane ---
  { key: 'analytics/daily_revenue', group: 'analytics', kind: 'dbt',    health: 'stale',  last_materialized: '2026-05-02 02:01:50', last_run_id: '20260502-020000-fcd2', partitions: { total: 30, fresh: 28, stale: 1, failed: 1 }, upstream: ['staging/orders_clean'],                          owner: 'analytics', x: 620, y: 100, rows: 30,      duration: '5s'    },
  { key: 'analytics/cohort_ltv',    group: 'analytics', kind: 'spark',  health: 'fresh',  last_materialized: '2026-05-02 03:30:11', last_run_id: '20260502-033000-bbbb', partitions: null,                                            upstream: ['staging/users_clean', 'staging/orders_clean'],   owner: 'analytics', x: 620, y: 220, rows: 124892,  duration: '4m12s' },
  { key: 'analytics/funnel',        group: 'analytics', kind: 'python', health: 'fresh',  last_materialized: '2026-05-02 04:00:33', last_run_id: '20260502-040000-cccc', partitions: null,                                            upstream: ['staging/sessions'],                              owner: 'growth',    x: 620, y: 360, rows: 9281,    duration: '2m08s' },

  // --- ml lane ---
  { key: 'ml/churn_features', group: 'ml', kind: 'python', health: 'fresh', last_materialized: '2026-05-02 05:00:00', last_run_id: '20260502-050000-dddd', partitions: null, upstream: ['analytics/cohort_ltv', 'staging/users_clean'], owner: 'ml-platform', x: 900, y: 240, rows: 4082911, duration: '6m45s' },
  { key: 'ml/churn_model',    group: 'ml', kind: 'ml',     health: 'none',  last_materialized: '',                    last_run_id: '',                       partitions: null, upstream: ['ml/churn_features'],                            owner: 'ml-platform', x: 1180, y: 240, rows: null,    duration: '-'     },
]

// Edges — derived from `upstream` but listed separately for the canvas.
export const assetEdges = (() => {
  const edges = []
  for (const asset of assets) {
    for (const up of asset.upstream) edges.push({ source: up, target: asset.key })
  }
  return edges
})()

// --- Detail page focus: analytics/daily_revenue ---

export const focalAssetKey = 'analytics/daily_revenue'

// Materialization history — Dagster shows these as a table + timeline,
// each row tagged with status, partition, duration, and the metadata
// emitted by the asset's compute function (rows, bytes, custom KVs).
export const materializations = [
  { id: 'mat-001', status: 'failed',  started_at: '2026-05-02 02:01:50', duration: '5s',     partition: '2026-05-01', run_id: '20260502-020000-fcd2', operator: 'system',
    metadata: { num_rows: 0, bytes: 0, error: '上游 staging/orders_clean 失败' } },
  { id: 'mat-002', status: 'success', started_at: '2026-05-01 02:01:42', duration: '4s',     partition: '2026-04-30', run_id: '20260501-020000-9b3f', operator: 'system',
    metadata: { num_rows: 30, bytes: '4.2 KB', distinct_dates: 30, last_dt: '2026-04-30' } },
  { id: 'mat-003', status: 'success', started_at: '2026-04-30 02:01:36', duration: '3s',     partition: '2026-04-29', run_id: '20260430-020000-1234', operator: 'system',
    metadata: { num_rows: 30, bytes: '4.1 KB', distinct_dates: 30, last_dt: '2026-04-29' } },
  { id: 'mat-004', status: 'success', started_at: '2026-04-29 15:06:01', duration: '4s',     partition: '2026-04-28', run_id: '20260429-150412-abcd', operator: 'sun.qi',
    metadata: { num_rows: 30, bytes: '4.1 KB', backfill: true } },
  { id: 'mat-005', status: 'failed',  started_at: '2026-04-29 02:00:48', duration: '2s',     partition: '2026-04-28', run_id: '20260429-020000-7890', operator: 'system',
    metadata: { num_rows: 0, error: 'AssertionError: 行数低于下限' } },
]

// Asset checks (Dagster's "expectations" / data-quality assertions
// attached to an asset). Each check has its own status independent of
// the materialization; UI shows them stacked next to the asset.
export const assetChecks = [
  { name: 'row_count_floor',         severity: '阻断', status: 'passed', message: '行数 30 ≥ 下限 1',                  last_run: '2026-05-01 02:01:42' },
  { name: 'no_negative_revenue',     severity: '阻断', status: 'passed', message: '没有负数金额行',                     last_run: '2026-05-01 02:01:42' },
  { name: 'partition_continuity',    severity: '警告', status: 'failed', message: '近 30 天有 2 个分区缺失',            last_run: '2026-05-01 02:01:42' },
  { name: 'currency_in_allowlist',   severity: '阻断', status: 'passed', message: '全部行币种在 {USD, EUR, CNY} 内',     last_run: '2026-05-01 02:01:42' },
]

// 30-day partition heatmap. Each entry is a status code: ok / stale /
// failed / missing. Rendered as a compact grid below the asset header.
export const partitionStrip = (() => {
  const out = []
  for (let i = 30; i >= 1; i--) {
    let status = 'ok'
    if (i === 1) status = 'failed'    // most recent
    else if (i === 8) status = 'stale'
    else if (i === 22) status = 'missing'
    const day = String(i).padStart(2, '0')
    out.push({ partition: `2026-04-${day}`, status })
  }
  return out
})()

// Definition source — Dagster's UI shows the actual Python decorator + body.
export const focalDefinition = `from dagster import asset, AssetExecutionContext, MaterializeResult, MetadataValue

@asset(
    deps=["staging/orders_clean"],
    group_name="analytics",
    partitions_def=daily_partitions,
    metadata={"owner": "analytics", "tier": "tier-1"},
    compute_kind="dbt",
)
def daily_revenue(context: AssetExecutionContext) -> MaterializeResult:
    """Daily revenue rollup for finance/exec dashboards.

    Joins cleaned orders against currency rates and emits one row
    per (date, currency, business_unit).
    """
    partition = context.partition_key
    rows = run_dbt_model("daily_revenue", vars={"date": partition})

    return MaterializeResult(
        metadata={
            "num_rows": MetadataValue.int(len(rows)),
            "bytes": MetadataValue.text(format_bytes(rows)),
            "last_dt": MetadataValue.text(partition),
            "preview": MetadataValue.md(preview_md(rows)),
        }
    )`

// --- Run page focus: 20260502-020000-fcd2 ---

export const focalRunId = '20260502-020000-fcd2'

// Steps in the run — each step materializes (or attempts to materialize)
// one asset. Gantt rendering uses `start_offset_s` and `duration_s`.
export const runSteps = [
  { name: 'raw__orders',              asset: 'raw/orders',             status: 'success', start_offset_s: 0,   duration_s: 12 },
  { name: 'raw__users',               asset: 'raw/users',              status: 'success', start_offset_s: 0,   duration_s: 8  },
  { name: 'raw__refunds',             asset: 'raw/refunds',            status: 'success', start_offset_s: 0,   duration_s: 4  },
  { name: 'raw__products',            asset: 'raw/products',           status: 'success', start_offset_s: 0,   duration_s: 1  },
  { name: 'staging__users_clean',     asset: 'staging/users_clean',    status: 'success', start_offset_s: 12,  duration_s: 47 },
  { name: 'staging__orders_clean',    asset: 'staging/orders_clean',   status: 'failed',  start_offset_s: 12,  duration_s: 103 },
  { name: 'analytics__daily_revenue', asset: 'analytics/daily_revenue',status: 'failed',  start_offset_s: 115, duration_s: 5 },
]

// Structured event stream — Dagster's signature: typed events, not just
// freeform log lines. UI renders an icon per event type.
export const runEvents = [
  { ts: '02:00:00.001', type: 'RUN_START',           level: 'INFO',  msg: '由监听器 partitioned_daily 触发运行', step: '' },
  { ts: '02:00:00.118', type: 'STEP_START',          level: 'INFO',  msg: 'raw__orders：开始执行',                                                  step: 'raw__orders' },
  { ts: '02:00:12.401', type: 'MATERIALIZATION',     level: 'INFO',  msg: 'raw/orders 分区=2026-05-01 行数=1,284,567',                              step: 'raw__orders', metadata: { num_rows: 1284567, bytes: '188 MB' } },
  { ts: '02:00:12.402', type: 'STEP_SUCCESS',        level: 'INFO',  msg: 'raw__orders：完成，耗时 12.28 秒',                                        step: 'raw__orders' },
  { ts: '02:00:12.503', type: 'STEP_START',          level: 'INFO',  msg: 'staging__orders_clean：开始执行',                                        step: 'staging__orders_clean' },
  { ts: '02:00:13.011', type: 'EXPECTATION_RESULT',  level: 'INFO',  msg: 'row_count_floor：通过（1,284,567 ≥ 1）',                                step: 'staging__orders_clean' },
  { ts: '02:00:14.221', type: 'LOG',                 level: 'INFO',  msg: '提交 dbt 模型 orders_clean 至仓库 snowflake-prod',                       step: 'staging__orders_clean' },
  { ts: '02:01:12.802', type: 'LOG',                 level: 'WARN',  msg: 'Spark executor 丢失（host=spark-worker-09），重试 stage 3',              step: 'staging__orders_clean' },
  { ts: '02:01:33.117', type: 'LOG',                 level: 'WARN',  msg: 'Spark executor 再次丢失，准备最后一次重试',                              step: 'staging__orders_clean' },
  { ts: '02:01:44.901', type: 'STEP_FAILURE',        level: 'ERROR', msg: 'org.apache.spark.shuffle.FetchFailedException: shuffle 3 partition 17',  step: 'staging__orders_clean', stack: [
    'org.apache.spark.shuffle.FetchFailedException:',
    '  shuffle 3 partition 17 (executor=lost-executor-3, host=spark-worker-09)',
    '  at org.apache.spark.shuffle.BlockStoreShuffleReader$$anon$1.next(BlockStoreShuffleReader.scala:120)',
    '  at scala.collection.Iterator$$anon$10.next(Iterator.scala:459)',
    'Caused by: java.io.IOException: Connection reset by peer',
    '  at sun.nio.ch.FileDispatcherImpl.read0(Native Method)',
    '  ... 24 more',
  ] },
  { ts: '02:01:44.902', type: 'STEP_START',          level: 'INFO',  msg: 'analytics__daily_revenue：开始执行',                                     step: 'analytics__daily_revenue' },
  { ts: '02:01:50.110', type: 'STEP_FAILURE',        level: 'ERROR', msg: '上游 staging/orders_clean 失败，无法物化',                              step: 'analytics__daily_revenue' },
  { ts: '02:01:50.119', type: 'RUN_FAILURE',         level: 'ERROR', msg: '运行失败（1 个步骤成功，2 个失败）',                                    step: '' },
]

export const focalRun = {
  id: focalRunId,
  status: 'failed',
  started_at: '2026-05-02 02:00:00',
  ended_at:   '2026-05-02 02:01:50',
  duration:   '1分50秒',
  triggered_by: '监听器：partitioned_daily',
  partition: '2026-05-01',
  operator: 'system',
  code_location: 'analytics_repo @ main / a8f2e93',
  tags: { 'op_selection': 'analytics/daily_revenue', 'env': 'prod', 'team': 'analytics' },
  step_summary: { total: 7, success: 5, failed: 2, skipped: 0 },
}

// Sensor / schedule activity for the asset graph header.
export const sensorActivity = {
  active: 4,
  paused: 1,
  recent_ticks: [
    { name: 'partitioned_daily',  type: '监听器', last_tick: '02:00:00',  status: '触发', note: '入队 7 个分区' },
    { name: 'orders_freshness',   type: '监听器', last_tick: '02:30:00',  status: '跳过', note: '无新分区' },
    { name: 'cohort_weekly',      type: '调度',   last_tick: 'Mon 03:30', status: '触发', note: 'cohort_ltv 已物化' },
    { name: 'churn_retrain',      type: '调度',   last_tick: 'Mon 06:00', status: '触发', note: 'churn_features 已物化' },
  ],
}
