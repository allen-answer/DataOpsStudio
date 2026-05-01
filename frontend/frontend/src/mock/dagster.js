// Mock data for the Dagster-style asset-oriented prototype. Shaped like
// what `dagster.io/docs` describes: assets, materializations, asset checks,
// partitions, and structured run events.

export const assetGroups = [
  { id: 'raw',       name: 'raw',       desc: 'Source-of-truth ingest from prod systems',  color: '#94a3b8' },
  { id: 'staging',   name: 'staging',   desc: 'Cleaned + typed staging models',            color: '#22d3ee' },
  { id: 'analytics', name: 'analytics', desc: 'Business-facing analytical assets',         color: '#34d399' },
  { id: 'ml',        name: 'ml',        desc: 'Feature stores + trained models',           color: '#c084fc' },
]

// Asset "kind" maps loosely to the underlying compute. Dagster surfaces
// these as little badges on each asset card (dbt / python / spark / …).
export const assetKinds = {
  table:  { label: 'Table',   glyph: '⊞',   accent: '#0ea5e9' },
  view:   { label: 'View',    glyph: '◇',   accent: '#06b6d4' },
  python: { label: 'Python',  glyph: 'Py',  accent: '#34d399' },
  dbt:    { label: 'dbt',     glyph: 'dbt', accent: '#fb923c' },
  spark:  { label: 'Spark',   glyph: '✦',   accent: '#facc15' },
  ml:     { label: 'Model',   glyph: '◈',   accent: '#c084fc' },
}

// Health states — Dagster's signature is a single dot per asset that
// tells you if it's fresh, stale, failed, currently materializing, or
// has never been materialized. Mood: terse + technical.
export const assetHealth = {
  fresh:        { label: 'Fresh',         dot: 'bg-emerald-500',                pill: 'bg-emerald-500/10 text-emerald-300 ring-emerald-500/30' },
  stale:        { label: 'Stale',         dot: 'bg-amber-500',                  pill: 'bg-amber-500/10 text-amber-300 ring-amber-500/30' },
  failed:       { label: 'Failed',        dot: 'bg-rose-500',                   pill: 'bg-rose-500/10 text-rose-300 ring-rose-500/30' },
  materializing:{ label: 'Materializing', dot: 'bg-cyan-400 animate-pulse',     pill: 'bg-cyan-500/10 text-cyan-300 ring-cyan-500/30' },
  none:         { label: 'Never',         dot: 'bg-slate-600',                  pill: 'bg-slate-700 text-slate-400 ring-slate-700' },
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
  { key: 'staging/sessions',     group: 'staging', kind: 'python', health: 'materializing', last_materialized: '2026-05-02 09:30:00', last_run_id: '20260502-093000-aaaa', partitions: { total: 30, fresh: 29, stale: 0, failed: 0 }, upstream: ['raw/users'],                  owner: 'growth',      x: 340,  y: 380, rows: 12489201, duration: '运行中'  },

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
    metadata: { num_rows: 0, bytes: 0, error: 'Upstream `staging/orders_clean` failed' } },
  { id: 'mat-002', status: 'success', started_at: '2026-05-01 02:01:42', duration: '4s',     partition: '2026-04-30', run_id: '20260501-020000-9b3f', operator: 'system',
    metadata: { num_rows: 30, bytes: '4.2 KB', distinct_dates: 30, last_dt: '2026-04-30' } },
  { id: 'mat-003', status: 'success', started_at: '2026-04-30 02:01:36', duration: '3s',     partition: '2026-04-29', run_id: '20260430-020000-1234', operator: 'system',
    metadata: { num_rows: 30, bytes: '4.1 KB', distinct_dates: 30, last_dt: '2026-04-29' } },
  { id: 'mat-004', status: 'success', started_at: '2026-04-29 15:06:01', duration: '4s',     partition: '2026-04-28', run_id: '20260429-150412-abcd', operator: 'sun.qi',
    metadata: { num_rows: 30, bytes: '4.1 KB', backfill: true } },
  { id: 'mat-005', status: 'failed',  started_at: '2026-04-29 02:00:48', duration: '2s',     partition: '2026-04-28', run_id: '20260429-020000-7890', operator: 'system',
    metadata: { num_rows: 0, error: 'AssertionError: row count below floor' } },
]

// Asset checks (Dagster's "expectations" / data-quality assertions
// attached to an asset). Each check has its own status independent of
// the materialization; UI shows them stacked next to the asset.
export const assetChecks = [
  { name: 'row_count_floor',         severity: 'ERROR', status: 'passed', message: 'Row count 30 ≥ floor 1', last_run: '2026-05-01 02:01:42' },
  { name: 'no_negative_revenue',     severity: 'ERROR', status: 'passed', message: 'No negative revenue rows', last_run: '2026-05-01 02:01:42' },
  { name: 'partition_continuity',    severity: 'WARN',  status: 'failed', message: '2 missing partitions in last 30 days', last_run: '2026-05-01 02:01:42' },
  { name: 'currency_in_allowlist',   severity: 'ERROR', status: 'passed', message: 'All rows in {USD, EUR, CNY}', last_run: '2026-05-01 02:01:42' },
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
  { ts: '02:00:00.001', type: 'RUN_START',           level: 'INFO',  msg: 'Run started by sensor partitioned_daily', step: '' },
  { ts: '02:00:00.118', type: 'STEP_START',          level: 'INFO',  msg: 'raw__orders: starting',                                                    step: 'raw__orders' },
  { ts: '02:00:12.401', type: 'MATERIALIZATION',     level: 'INFO',  msg: 'raw/orders partition=2026-05-01 num_rows=1,284,567',                       step: 'raw__orders', metadata: { num_rows: 1284567, bytes: '188 MB' } },
  { ts: '02:00:12.402', type: 'STEP_SUCCESS',        level: 'INFO',  msg: 'raw__orders: completed in 12.28s',                                          step: 'raw__orders' },
  { ts: '02:00:12.503', type: 'STEP_START',          level: 'INFO',  msg: 'staging__orders_clean: starting',                                          step: 'staging__orders_clean' },
  { ts: '02:00:13.011', type: 'EXPECTATION_RESULT',  level: 'INFO',  msg: 'row_count_floor: PASSED (1,284,567 ≥ 1)',                                  step: 'staging__orders_clean' },
  { ts: '02:00:14.221', type: 'LOG',                 level: 'INFO',  msg: 'Submitting dbt model orders_clean to warehouse=snowflake-prod',            step: 'staging__orders_clean' },
  { ts: '02:01:12.802', type: 'LOG',                 level: 'WARN',  msg: 'Spark executor lost-executor-3 (host=spark-worker-09); retrying stage 3',  step: 'staging__orders_clean' },
  { ts: '02:01:33.117', type: 'LOG',                 level: 'WARN',  msg: 'Spark executor lost again; final attempt before giving up',                step: 'staging__orders_clean' },
  { ts: '02:01:44.901', type: 'STEP_FAILURE',        level: 'ERROR', msg: 'org.apache.spark.shuffle.FetchFailedException: shuffle 3 partition 17',    step: 'staging__orders_clean', stack: [
    'org.apache.spark.shuffle.FetchFailedException:',
    '  shuffle 3 partition 17 (executor=lost-executor-3, host=spark-worker-09)',
    '  at org.apache.spark.shuffle.BlockStoreShuffleReader$$anon$1.next(BlockStoreShuffleReader.scala:120)',
    '  at scala.collection.Iterator$$anon$10.next(Iterator.scala:459)',
    'Caused by: java.io.IOException: Connection reset by peer',
    '  at sun.nio.ch.FileDispatcherImpl.read0(Native Method)',
    '  ... 24 more',
  ] },
  { ts: '02:01:44.902', type: 'STEP_START',          level: 'INFO',  msg: 'analytics__daily_revenue: starting',                                       step: 'analytics__daily_revenue' },
  { ts: '02:01:50.110', type: 'STEP_FAILURE',        level: 'ERROR', msg: 'Upstream staging/orders_clean failed; cannot materialize',                step: 'analytics__daily_revenue' },
  { ts: '02:01:50.119', type: 'RUN_FAILURE',         level: 'ERROR', msg: 'Run failed (1 step succeeded, 2 failed)',                                  step: '' },
]

export const focalRun = {
  id: focalRunId,
  status: 'failed',
  started_at: '2026-05-02 02:00:00',
  ended_at:   '2026-05-02 02:01:50',
  duration:   '1m50s',
  triggered_by: 'sensor: partitioned_daily',
  partition: '2026-05-01',
  operator: 'system',
  code_location: 'analytics_repo @ main / a8f2e93',
  tags: { 'dagster/op_selection': 'analytics/daily_revenue', 'env': 'prod', 'team': 'analytics' },
  step_summary: { total: 7, success: 5, failed: 2, skipped: 0 },
}

// Sensor / schedule activity for the asset graph header.
export const sensorActivity = {
  active: 4,
  paused: 1,
  recent_ticks: [
    { name: 'partitioned_daily',  type: 'sensor',   last_tick: '02:00:00', status: 'fired',   note: '7 partitions enqueued' },
    { name: 'orders_freshness',   type: 'sensor',   last_tick: '02:30:00', status: 'skipped', note: 'No new partitions' },
    { name: 'cohort_weekly',      type: 'schedule', last_tick: 'Mon 03:30', status: 'fired',  note: 'cohort_ltv materialized' },
    { name: 'churn_retrain',      type: 'schedule', last_tick: 'Mon 06:00', status: 'fired',  note: 'churn_features materialized' },
  ],
}
