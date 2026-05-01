// Mock data for the 流程作业 prototype. All values are fabricated but shaped
// like what a real DolphinScheduler / Airflow-style backend would return.

export const projects = [
  { id: 'all', name: '全部项目', count: 32 },
  { id: 'dw', name: '数据仓库', count: 12 },
  { id: 'risk', name: '风控管理', count: 8 },
  { id: 'growth', name: '增长分析', count: 5 },
  { id: 'finance', name: '财务报表', count: 7 },
]

// Status tokens — keep palette consistent with the rest of the app.
export const statusMeta = {
  success:  { label: '成功',   pill: 'bg-emerald-50 text-emerald-700 ring-emerald-200',   dot: 'bg-emerald-500' },
  failed:   { label: '失败',   pill: 'bg-rose-50 text-rose-700 ring-rose-200',             dot: 'bg-rose-500' },
  running:  { label: '运行中', pill: 'bg-sky-50 text-sky-700 ring-sky-200',                dot: 'bg-sky-500 animate-pulse' },
  waiting:  { label: '等待',   pill: 'bg-amber-50 text-amber-700 ring-amber-200',          dot: 'bg-amber-500' },
  paused:   { label: '暂停',   pill: 'bg-slate-100 text-slate-600 ring-slate-200',         dot: 'bg-slate-400' },
  pending:  { label: '未运行', pill: 'bg-slate-50 text-slate-500 ring-slate-200',          dot: 'bg-slate-300' },
  cancelled:{ label: '已取消', pill: 'bg-slate-100 text-slate-600 ring-slate-200',         dot: 'bg-slate-400' },
}

export const scheduleStatusMeta = {
  online:  { label: '已上线', pill: 'bg-emerald-50 text-emerald-700 ring-emerald-200' },
  paused:  { label: '已暂停', pill: 'bg-amber-50 text-amber-700 ring-amber-200' },
  offline: { label: '未上线', pill: 'bg-slate-100 text-slate-500 ring-slate-200' },
}

// Node-type metadata — shape, accent color, icon glyph.
export const nodeTypeMeta = {
  shell:    { label: 'Shell',     accent: '#475569', glyph: '$_'  },
  sql:      { label: 'SQL',       accent: '#0284c7', glyph: 'SQL' },
  python:   { label: 'Python',    accent: '#059669', glyph: 'Py'  },
  http:     { label: 'HTTP',      accent: '#6366f1', glyph: '⇄'   },
  sync:     { label: '数据同步',   accent: '#0ea5e9', glyph: '⇆'   },
  quality:  { label: '质量检查',   accent: '#ea580c', glyph: '✓'   },
  approval: { label: '审批',       accent: '#a855f7', glyph: '☑'   },
}

export const workflows = [
  {
    id: 'wf-orders-daily', name: '订单事实表日清洗', project: 'dw', owner: '张三',
    schedule_cron: '0 2 * * *', schedule_text: '每日 02:00', schedule_status: 'online',
    last_run_status: 'failed', last_run_at: '2026-05-02 02:00:13', last_duration: '1m45s',
    success_rate_7d: 0.71, runs_24h: 1, avg_duration: '1m32s',
  },
  {
    id: 'wf-user-dim', name: '用户维度表更新', project: 'dw', owner: '李娜',
    schedule_cron: '0 3 * * *', schedule_text: '每日 03:00', schedule_status: 'online',
    last_run_status: 'success', last_run_at: '2026-05-02 03:00:08', last_duration: '47s',
    success_rate_7d: 1.0, runs_24h: 1, avg_duration: '52s',
  },
  {
    id: 'wf-credit-score', name: '风控评分模型推送', project: 'risk', owner: '王伟',
    schedule_cron: '*/30 * * * *', schedule_text: '每 30 分钟', schedule_status: 'online',
    last_run_status: 'running', last_run_at: '2026-05-02 09:30:00', last_duration: '运行中',
    success_rate_7d: 0.95, runs_24h: 18, avg_duration: '3m12s',
  },
  {
    id: 'wf-fraud-rules', name: '反欺诈规则同步', project: 'risk', owner: '王伟',
    schedule_cron: '0 */2 * * *', schedule_text: '每 2 小时', schedule_status: 'paused',
    last_run_status: 'cancelled', last_run_at: '2026-05-02 04:00:00', last_duration: '12s',
    success_rate_7d: 0.83, runs_24h: 0, avg_duration: '24s',
  },
  {
    id: 'wf-ad-attribution', name: '广告归因日报', project: 'growth', owner: '孙琪',
    schedule_cron: '15 5 * * *', schedule_text: '每日 05:15', schedule_status: 'online',
    last_run_status: 'success', last_run_at: '2026-05-02 05:15:42', last_duration: '4m02s',
    success_rate_7d: 0.93, runs_24h: 1, avg_duration: '4m18s',
  },
  {
    id: 'wf-funnel', name: '漏斗转化分析', project: 'growth', owner: '孙琪',
    schedule_cron: null, schedule_text: '手动触发', schedule_status: 'offline',
    last_run_status: 'success', last_run_at: '2026-04-30 11:24:18', last_duration: '2m11s',
    success_rate_7d: 1.0, runs_24h: 0, avg_duration: '2m05s',
  },
  {
    id: 'wf-cashflow', name: '现金流日报', project: 'finance', owner: '周敏',
    schedule_cron: '0 6 * * *', schedule_text: '每日 06:00', schedule_status: 'online',
    last_run_status: 'success', last_run_at: '2026-05-02 06:00:11', last_duration: '38s',
    success_rate_7d: 1.0, runs_24h: 1, avg_duration: '41s',
  },
  {
    id: 'wf-tax-monthly', name: '税务月报准备', project: 'finance', owner: '周敏',
    schedule_cron: '0 7 1 * *', schedule_text: '每月 1 日 07:00', schedule_status: 'online',
    last_run_status: 'waiting', last_run_at: '2026-04-01 07:00:00', last_duration: '-',
    success_rate_7d: 1.0, runs_24h: 0, avg_duration: '6m45s',
  },
  {
    id: 'wf-vendor-recon', name: '供应商对账', project: 'finance', owner: '周敏',
    schedule_cron: '0 9 * * 1', schedule_text: '每周一 09:00', schedule_status: 'online',
    last_run_status: 'success', last_run_at: '2026-04-28 09:00:33', last_duration: '5m17s',
    success_rate_7d: 0.88, runs_24h: 0, avg_duration: '5m02s',
  },
]

// DAG for the focal workflow shown on the detail page.
export const dagDetail = {
  workflow_id: 'wf-orders-daily',
  workflow_name: '订单事实表日清洗',
  project: 'dw',
  owner: '张三',
  schedule: '0 2 * * *',
  schedule_status: 'online',
  status: 'failed',
  nodes: [
    { id: 'pre-check',  name: '前置检查',     type: 'shell',    status: 'success', x: 60,   y: 200, duration: '3s',     retries: 0 },
    { id: 'extract',    name: '抽取订单数据', type: 'sql',      status: 'success', x: 280,  y: 80,  duration: '23s',    retries: 0 },
    { id: 'extract-r',  name: '抽取退款数据', type: 'sql',      status: 'success', x: 280,  y: 320, duration: '17s',    retries: 0 },
    { id: 'quality',    name: '入参质量检查', type: 'quality',  status: 'success', x: 500,  y: 200, duration: '12s',    retries: 0 },
    { id: 'transform',  name: '聚合 / 反范式', type: 'python',  status: 'failed',  x: 720,  y: 80,  duration: '1m43s',  retries: 2 },
    { id: 'enrich',     name: '维度补全',     type: 'python',   status: 'success', x: 720,  y: 320, duration: '54s',    retries: 0 },
    { id: 'merge',      name: '合并写入',     type: 'sql',      status: 'pending', x: 940,  y: 200, duration: '-',      retries: 0 },
    { id: 'sync',       name: '推送下游表',   type: 'sync',     status: 'pending', x: 1160, y: 80,  duration: '-',      retries: 0 },
    { id: 'notify',     name: '失败告警',     type: 'http',     status: 'success', x: 1160, y: 320, duration: '1s',     retries: 0 },
    { id: 'review',     name: '人工审批',     type: 'approval', status: 'pending', x: 1380, y: 200, duration: '-',      retries: 0 },
  ],
  edges: [
    { source: 'pre-check', target: 'extract' },
    { source: 'pre-check', target: 'extract-r' },
    { source: 'extract',   target: 'quality' },
    { source: 'extract-r', target: 'quality' },
    { source: 'quality',   target: 'transform' },
    { source: 'quality',   target: 'enrich' },
    { source: 'transform', target: 'merge' },
    { source: 'enrich',    target: 'merge' },
    { source: 'merge',     target: 'sync' },
    { source: 'transform', target: 'notify' },
    { source: 'sync',      target: 'review' },
    { source: 'notify',    target: 'review' },
  ],
}

// Per-node properties for the right-side panel. Keyed by node id.
export const nodePropertyTemplates = {
  'pre-check': {
    base: { name: '前置检查', description: '校验源表水位线和上游 SLA' },
    params: { command: 'check_upstream.sh --strict', env: 'PROD', user: 'etl' },
    retry: { max: 1, interval: '30s', on_failure: 'fail' },
    timeout: { seconds: 60 },
    queue: 'default',
    alert: { on_failure: ['张三', '@值班群'], on_timeout: ['@值班群'] },
    inputs: [],
    outputs: [{ name: 'water_mark', type: 'string' }],
  },
  'extract': {
    base: { name: '抽取订单数据', description: '从 ods.orders 增量抽取' },
    params: { sql_path: 'sql/extract_orders.sql', biz_date: '${biz_date}' },
    retry: { max: 3, interval: '1m', on_failure: 'fail' },
    timeout: { seconds: 600 },
    queue: 'data-extract',
    alert: { on_failure: ['张三'], on_timeout: ['张三', '@值班群'] },
    inputs: ['pre-check.water_mark'],
    outputs: [{ name: 'rows', type: 'number' }, { name: 'partition', type: 'string' }],
  },
  'transform': {
    base: { name: '聚合 / 反范式', description: '按用户/商品聚合，落到 dwd_orders' },
    params: { script: 'transforms/agg_orders.py', spark_resources: '4×16G' },
    retry: { max: 2, interval: '2m', on_failure: 'fail' },
    timeout: { seconds: 1800 },
    queue: 'spark-large',
    alert: { on_failure: ['张三', '@值班群'], on_timeout: ['@值班群'] },
    inputs: ['quality.passed', 'extract.partition'],
    outputs: [{ name: 'rows_out', type: 'number' }, { name: 'checksum', type: 'string' }],
  },
}

export const runInstances = [
  {
    id: '20260502-020000-fcd2', workflow_id: 'wf-orders-daily', status: 'failed',
    started_at: '2026-05-02 02:00:00', ended_at: '2026-05-02 02:01:45',
    duration: '1m45s', triggered_by: 'cron', operator: 'system',
    summary: { success: 6, failed: 1, running: 0, pending: 3 },
  },
  {
    id: '20260501-020000-9b3f', workflow_id: 'wf-orders-daily', status: 'success',
    started_at: '2026-05-01 02:00:00', ended_at: '2026-05-01 02:01:32',
    duration: '1m32s', triggered_by: 'cron', operator: 'system',
    summary: { success: 10, failed: 0, running: 0, pending: 0 },
  },
  {
    id: '20260430-020000-1234', workflow_id: 'wf-orders-daily', status: 'success',
    started_at: '2026-04-30 02:00:00', ended_at: '2026-04-30 02:01:28',
    duration: '1m28s', triggered_by: 'cron', operator: 'system',
    summary: { success: 10, failed: 0, running: 0, pending: 0 },
  },
  {
    id: '20260429-150412-abcd', workflow_id: 'wf-orders-daily', status: 'success',
    started_at: '2026-04-29 15:04:12', ended_at: '2026-04-29 15:06:01',
    duration: '1m49s', triggered_by: '手动', operator: '张三',
    summary: { success: 10, failed: 0, running: 0, pending: 0 },
  },
  {
    id: '20260429-020000-7890', workflow_id: 'wf-orders-daily', status: 'failed',
    started_at: '2026-04-29 02:00:00', ended_at: '2026-04-29 02:00:48',
    duration: '48s', triggered_by: 'cron', operator: 'system',
    summary: { success: 4, failed: 1, running: 0, pending: 5 },
  },
]

// Logs for the focal failed run. Each entry is a node id → log payload.
export const logSamples = {
  'pre-check': {
    summary: { started: '02:00:00', ended: '02:00:03', duration: '3s', retries: 0, host: 'worker-04', queue: 'default' },
    stdout: [
      '[02:00:00.124] starting pre-check',
      '[02:00:00.421] reading water mark from meta_store...',
      '[02:00:01.038] water_mark=2026-05-01 23:59:59',
      '[02:00:02.911] upstream tables OK (orders / refunds)',
      '[02:00:03.002] pre-check passed',
    ],
    stderr: [],
    retries: [],
  },
  'extract': {
    summary: { started: '02:00:03', ended: '02:00:26', duration: '23s', retries: 0, host: 'worker-04', queue: 'data-extract' },
    stdout: [
      '[02:00:03.110] resolving sql/extract_orders.sql',
      '[02:00:03.221] biz_date=2026-05-01',
      '[02:00:03.418] connecting to mysql://prod-rw01:3306/ods',
      '[02:00:04.802] executing INSERT OVERWRITE ods_orders_daily PARTITION (dt=2026-05-01)',
      '[02:00:25.611] rows written: 1,284,567 partition=2026-05-01',
      '[02:00:25.998] extract done',
    ],
    stderr: [],
    retries: [],
  },
  'transform': {
    summary: { started: '02:00:38', ended: '02:01:45', duration: '1m43s', retries: 2, host: 'spark-12', queue: 'spark-large' },
    stdout: [
      '[02:00:38.001] retry 0 — submitting spark job',
      '[02:00:42.118] application_id=application_1730481234567_0892',
      '[02:00:55.221] stage 0 → 12/12 tasks complete',
      '[02:01:08.430] stage 1 → 7/64 tasks running...',
      '[02:01:12.802] retry 1 — re-submitting after executor lost',
      '[02:01:33.117] retry 2 — re-submitting after executor lost',
      '[02:01:44.901] all retries exhausted',
    ],
    stderr: [
      'org.apache.spark.shuffle.FetchFailedException:',
      '  shuffle 3 partition 17 (executor=lost-executor-3, host=spark-worker-09)',
      '  at org.apache.spark.shuffle.BlockStoreShuffleReader$$anon$1.next(BlockStoreShuffleReader.scala:120)',
      '  at scala.collection.Iterator$$anon$10.next(Iterator.scala:459)',
      '  at scala.collection.Iterator$$anon$11.next(Iterator.scala:495)',
      'Caused by: java.io.IOException: Connection reset by peer',
      '  at sun.nio.ch.FileDispatcherImpl.read0(Native Method)',
      '  at sun.nio.ch.SocketDispatcher.read(SocketDispatcher.java:39)',
      '  ... 24 more',
    ],
    retries: [
      { idx: 0, started: '02:00:38', ended: '02:01:11', status: 'failed', error: 'FetchFailedException: shuffle 3 partition 17' },
      { idx: 1, started: '02:01:12', ended: '02:01:32', status: 'failed', error: 'FetchFailedException: shuffle 3 partition 17' },
      { idx: 2, started: '02:01:33', ended: '02:01:44', status: 'failed', error: 'FetchFailedException: shuffle 3 partition 17' },
    ],
  },
  'enrich': {
    summary: { started: '02:00:38', ended: '02:01:32', duration: '54s', retries: 0, host: 'spark-09', queue: 'spark-default' },
    stdout: [
      '[02:00:38.001] starting dimension enrichment',
      '[02:00:38.510] left join dim_user (12.4M rows)',
      '[02:01:14.880] left join dim_product (3.1M rows)',
      '[02:01:30.118] writing 1,284,567 rows',
      '[02:01:31.911] enrich done',
    ],
    stderr: [],
    retries: [],
  },
  'notify': {
    summary: { started: '02:01:45', ended: '02:01:46', duration: '1s', retries: 0, host: 'worker-02', queue: 'default' },
    stdout: [
      '[02:01:45.501] POST https://oncall.internal/alert',
      '[02:01:46.018] response 200 OK',
    ],
    stderr: [],
    retries: [],
  },
}

// Operations dashboard summary stats.
export const opsStats = {
  runs_today: 142,
  success_today: 128,
  failed_today: 9,
  running_now: 5,
  avg_duration: '2m38s',
  failure_trend_7d: [3, 2, 5, 1, 4, 6, 9],   // last 7 days, oldest → newest
  top_slow: [
    { name: '广告归因日报',     workflow_id: 'wf-ad-attribution', avg: '4m18s' },
    { name: '供应商对账',       workflow_id: 'wf-vendor-recon',   avg: '5m02s' },
    { name: '风控评分模型推送', workflow_id: 'wf-credit-score',   avg: '3m12s' },
    { name: '订单事实表日清洗', workflow_id: 'wf-orders-daily',   avg: '1m32s' },
  ],
  recent_failures: [
    { workflow_id: 'wf-orders-daily', name: '订单事实表日清洗', failed_node: 'transform', at: '02:01:45' },
    { workflow_id: 'wf-fraud-rules',  name: '反欺诈规则同步',   failed_node: 'sync',      at: '04:00:12' },
  ],
  queue_usage: [
    { name: 'spark-large', running: 3, capacity: 4 },
    { name: 'spark-default', running: 5, capacity: 8 },
    { name: 'data-extract', running: 2, capacity: 4 },
    { name: 'default', running: 4, capacity: 16 },
  ],
}
