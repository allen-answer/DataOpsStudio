<script setup>
import { ref, computed } from 'vue'
import { storeToRefs } from 'pinia'
import { Play, Square, Download, Inbox, AlertCircle, CheckCircle2 } from 'lucide-vue-next'
import { useTaskStore } from '../../stores/task'
import { useNoticeStore } from '../../stores/notice'
import { downloadSignedRunFile } from '../../utils/download'

// compareBuckets 之前在 App.vue 是常量；S2.B 收口后直接在这里 inline，
// 不值得为了 4 个对象再起一个 store。如果别处也要用，再抽 constants 模块。
const compareBuckets = [
  { id: 'only_source', label: '只在源端' },
  { id: 'only_target', label: '只在目标端' },
  { id: 'diff', label: '差异' },
  { id: 'same', label: '一致' },
]

const taskStore = useTaskStore()
const {
  isSavedTask, compareResult, asyncJob, asyncStatus, previewOutput,
} = storeToRefs(taskStore)
const { runTask, runAsync, cancelAsync } = taskStore
const { actionStatus } = useNoticeStore()

// 结果 bucket 筛选 —— 默认显示全部，点击可切到单 bucket
const activeBucket = ref('all')

// previewOutput 是个 string,可能是:
//   - sql/assist 返回 (含 readonly_ok / formatted_sql / output_columns / key_candidates)
//   - 错误信息(纯文本)
//   - "执行中..." 类提示
// 优先识别 sql_assist 渲染成可读卡片;不是的就回退 raw <pre>。
const previewParsed = computed(() => {
  const raw = previewOutput.value
  if (!raw || typeof raw !== 'string') return null
  if (!raw.trim().startsWith('{')) return null
  try {
    const obj = JSON.parse(raw)
    if (obj && typeof obj === 'object' && 'readonly_ok' in obj && 'formatted_sql' in obj) {
      return {
        kind: 'sql_assist',
        readonly_ok: obj.readonly_ok,
        readonly_error: obj.readonly_error || '',
        formatted_sql: obj.formatted_sql || '',
        output_columns: Array.isArray(obj.output_columns) ? obj.output_columns : [],
        key_candidates: Array.isArray(obj.key_candidates) ? obj.key_candidates : [],
        rewritten_sql: obj.rewritten_sql || '',
        alias_injected: !!obj.alias_injected,
      }
    }
  } catch { /* JSON parse 失败回退 raw */ }
  return null
})

// 应用 SQL 改写建议 — 把 rewritten_sql 写回 taskDraft.source_sql / target_sql
async function applyRewrittenSql() {
  const rewritten = previewParsed.value?.rewritten_sql
  if (!rewritten) return
  // 默认改源 SQL — task 设计里复合表达式 SUM/COUNT 多出现在源 SQL
  // 用户如果要改目标 SQL 同样道理但当前不暴露选择 UI
  const { useTaskStore } = await import('../../stores/task')
  const taskStore = useTaskStore()
  taskStore.taskDraft.source_sql = rewritten
  const { useNoticeStore } = await import('../../stores/notice')
  useNoticeStore().setActionStatus('success', 'SQL 已应用别名建议', '已自动注入 AS alias,可重新预览')
}

const visibleBuckets = computed(() => {
  if (!compareResult.value) return []
  if (activeBucket.value === 'all') return compareBuckets
  return compareBuckets.filter(b => b.id === activeBucket.value)
})

const summaryCards = computed(() => {
  if (!compareResult.value) return []
  const s = compareResult.value.summary || {}
  return [
    { id: 'same',        label: 'same',        value: s.same || 0,        tone: 'success' },
    { id: 'diff',        label: 'diff',        value: s.diff || 0,        tone: 'error' },
    { id: 'only_source', label: 'only_source', value: s.only_source || 0, tone: 'warning' },
    { id: 'only_target', label: 'only_target', value: s.only_target || 0, tone: 'warning' },
    { id: 'src_rows',    label: '源行数',     value: compareResult.value.source_rows, tone: 'pending' },
    { id: 'tgt_rows',    label: '目标行数',   value: compareResult.value.target_rows, tone: 'pending' },
  ]
})

const schemaReport = computed(() => compareResult.value?.schema_report || null)
const schemaWarnings = computed(() => schemaReport.value?.warnings || [])
const comparedPreview = computed(() => schemaReport.value?.compared_columns?.slice(0, 12) || [])

const cardClass = (tone) => ({
  success: 'bg-status-success-bg text-status-success',
  error:   'bg-status-error-bg text-status-error',
  warning: 'bg-status-warning-bg text-status-warning',
  pending: 'bg-slate-50 text-slate-700',
}[tone] || 'bg-slate-50 text-slate-700')
</script>

<template>
  <section class="space-y-4">
    <!-- 操作状态条 + 主操作 -->
    <div
      class="card flex items-center justify-between gap-4"
      :class="{
        'border-status-info-bg bg-status-info-bg/30': actionStatus.type === 'running',
        'border-status-success-bg bg-status-success-bg/30': actionStatus.type === 'success',
        'border-status-error-bg bg-status-error-bg/30':   actionStatus.type === 'error',
      }"
    >
      <div class="flex items-center gap-3">
        <div
          class="grid h-10 w-10 place-items-center rounded-lg"
          :class="{
            'bg-slate-100 text-slate-500': actionStatus.type === 'idle' || actionStatus.type === 'ready',
            'bg-status-info-bg text-status-info': actionStatus.type === 'running',
            'bg-status-success-bg text-status-success': actionStatus.type === 'success',
            'bg-status-error-bg text-status-error': actionStatus.type === 'error',
          }"
        >
          <CheckCircle2 v-if="actionStatus.type === 'success'" class="h-5 w-5" />
          <AlertCircle v-else-if="actionStatus.type === 'error'" class="h-5 w-5" />
          <Play v-else class="h-5 w-5" />
        </div>
        <div>
          <p class="text-sm font-bold text-slate-800">{{ actionStatus.title }}</p>
          <p v-if="actionStatus.message" class="muted text-xs">{{ actionStatus.message }}</p>
        </div>
      </div>
      <div class="flex gap-2">
        <button class="btn btn-primary" :disabled="!isSavedTask" @click="runTask">
          <Play class="h-4 w-4" /> 开始执行对比
        </button>
        <button class="btn btn-outline" :disabled="!isSavedTask" @click="runAsync">
          后台执行
        </button>
        <button v-if="asyncJob" class="btn btn-danger" @click="cancelAsync">
          <Square class="h-4 w-4" /> 取消
        </button>
      </div>
    </div>

    <!-- 结果 -->
    <div v-if="compareResult" class="card">
      <div class="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3 class="text-base font-semibold text-slate-800">{{ $t('workbench.result.title') }}</h3>
          <p class="muted text-[11px]">{{ $t('workbench.result.hint') }}</p>
        </div>
        <div class="flex gap-2">
          <button type="button" class="btn btn-outline h-9 gap-1.5 px-3 text-xs" @click="downloadSignedRunFile(compareResult.run_id, 'excel')">
            <Download class="h-3.5 w-3.5" /> Excel
          </button>
          <button type="button" class="btn btn-outline h-9 gap-1.5 px-3 text-xs" @click="downloadSignedRunFile(compareResult.run_id, 'result')">
            <Download class="h-3.5 w-3.5" /> JSON
          </button>
        </div>
      </div>

      <!-- 6 张汇总卡 —— 前 4 张可点击筛选 samples -->
      <div class="grid grid-cols-2 gap-2.5 lg:grid-cols-6">
        <button
          v-for="card in summaryCards"
          :key="card.id"
          type="button"
          class="rounded-lg border p-3 text-left transition"
          :class="[
            cardClass(card.tone),
            ['same', 'diff', 'only_source', 'only_target'].includes(card.id)
              ? (activeBucket === card.id ? 'ring-2 ring-primary' : 'cursor-pointer hover:ring-1 hover:ring-primary/40')
              : 'cursor-default opacity-90',
          ]"
          @click="['same','diff','only_source','only_target'].includes(card.id) && (activeBucket = activeBucket === card.id ? 'all' : card.id)"
        >
          <strong class="block text-2xl font-bold">{{ card.value }}</strong>
          <span class="text-[11px] font-semibold opacity-80">{{ card.label }}</span>
        </button>
      </div>

      <div v-if="schemaReport" class="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-3">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h4 class="text-sm font-semibold text-slate-700">Schema 对齐</h4>
            <p class="muted text-[11px]">
              {{ schemaReport.mapping_mode === 'position' ? '按位置映射' : '手工字段映射' }}
              · source {{ schemaReport.source_count }} 列 / target {{ schemaReport.target_count }} 列
              · 参与值比较 {{ schemaReport.compared_count }} 列
            </p>
          </div>
          <span
            class="rounded-full px-2 py-0.5 text-[10px] font-bold"
            :class="schemaReport.has_schema_mismatch ? 'bg-status-warning-bg text-status-warning' : 'bg-status-success-bg text-status-success'"
          >
            {{ schemaReport.has_schema_mismatch ? 'schema warning' : 'schema aligned' }}
          </span>
        </div>
        <ul v-if="schemaWarnings.length" class="mt-2 space-y-1 text-[11px] text-slate-700">
          <li v-for="(warning, i) in schemaWarnings" :key="i" class="flex items-start gap-1.5">
            <AlertCircle class="mt-0.5 h-3 w-3 shrink-0 text-status-warning" />
            <span>{{ warning.message }}</span>
          </li>
        </ul>
        <div v-if="comparedPreview.length" class="mt-2 grid gap-1 text-[11px] md:grid-cols-2 xl:grid-cols-3">
          <div
            v-for="(item, i) in comparedPreview"
            :key="i"
            class="flex items-center justify-between gap-2 rounded border border-slate-200 bg-white px-2 py-1"
          >
            <span class="truncate font-mono text-slate-700">{{ item.source || '(缺失)' }}</span>
            <span class="shrink-0 text-slate-400">→</span>
            <span class="truncate font-mono text-slate-700">{{ item.target || '(缺失)' }}</span>
          </div>
        </div>
      </div>

      <!-- bucket 样例 -->
      <div class="mt-4 grid gap-3 xl:grid-cols-2">
        <div
          v-for="bucket in visibleBuckets"
          :key="bucket.id"
          class="rounded-lg border border-slate-200"
        >
          <div class="flex items-center justify-between border-b border-slate-200 bg-slate-50 px-3 py-2">
            <h4 class="text-sm font-semibold text-slate-700">{{ bucket.label }}</h4>
            <span class="rounded-full bg-white px-2 py-0.5 text-[10px] font-bold text-slate-500 shadow-sm">
              样例 {{ compareResult.samples[bucket.id]?.length || 0 }}
            </span>
          </div>
          <pre class="max-h-64 overflow-auto bg-slate-950 p-3 text-[11px] leading-relaxed text-slate-100">{{ JSON.stringify(compareResult.samples[bucket.id] || [], null, 2) }}</pre>
        </div>
      </div>
    </div>

    <!-- SQL 校验信息卡片(sql/assist 返回) -->
    <div v-else-if="previewParsed?.kind === 'sql_assist'" class="card space-y-3">
      <div class="flex items-center gap-2">
        <CheckCircle2 v-if="previewParsed.readonly_ok" class="h-5 w-5 text-status-success" />
        <AlertCircle v-else class="h-5 w-5 text-status-error" />
        <h3 class="text-base font-semibold text-slate-800">SQL 校验</h3>
        <span
          class="rounded-full px-2 py-0.5 text-[10px] font-bold"
          :class="previewParsed.readonly_ok ? 'bg-status-success-bg text-status-success' : 'bg-status-error-bg text-status-error'"
        >
          {{ previewParsed.readonly_ok ? '只读 SQL ✓' : '校验失败' }}
        </span>
      </div>

      <p v-if="!previewParsed.readonly_ok" class="text-xs text-status-error">
        {{ previewParsed.readonly_error || 'SQL 含禁止关键字(DML/DDL),无法执行对比' }}
      </p>

      <!-- 自动 alias 建议(无 alias 复合表达式被注入短别名) -->
      <div
        v-if="previewParsed.alias_injected"
        class="rounded-md border border-status-warning/30 bg-status-warning-bg/30 p-3"
      >
        <div class="mb-2 flex items-start gap-2">
          <AlertCircle class="mt-0.5 h-4 w-4 shrink-0 text-status-warning" />
          <div class="flex-1">
            <p class="text-xs font-semibold text-slate-800">SQL 自动改写建议</p>
            <p class="muted text-[11px]">
              检测到 SUM / CASE / CAST 等复合表达式没写 AS 别名 — DB 执行时这些列名
              不可预测(部分 driver 返序号 6/7/8 / 部分返长 SQL 文本),会让 column_mappings 跟实际不一致.
              建议应用下面的改写,自动添加短别名:
            </p>
          </div>
          <button
            type="button"
            class="btn btn-primary h-7 shrink-0 gap-1 px-2 py-1 text-[11px]"
            title="把改写后的 SQL 写回源 SQL,可重新预览验证"
            @click="applyRewrittenSql"
          >
            应用建议
          </button>
        </div>
        <details>
          <summary class="cursor-pointer text-[11px] font-semibold text-slate-600">查看改写后 SQL</summary>
          <pre class="mt-1 max-h-48 overflow-auto rounded bg-slate-950 p-2 text-[11px] leading-relaxed text-slate-100">{{ previewParsed.rewritten_sql }}</pre>
        </details>
      </div>

      <div v-if="previewParsed.output_columns.length">
        <h4 class="mb-1 text-xs font-semibold text-slate-700">输出字段 ({{ previewParsed.output_columns.length }})</h4>
        <div class="flex flex-wrap gap-1.5">
          <span
            v-for="col in previewParsed.output_columns"
            :key="col"
            class="rounded bg-slate-100 px-2 py-0.5 font-mono text-[11px] text-slate-700"
          >{{ col }}</span>
        </div>
      </div>

      <div v-if="previewParsed.key_candidates.length">
        <h4 class="mb-1 text-xs font-semibold text-slate-700">推断主键候选</h4>
        <div class="flex flex-wrap gap-1.5">
          <span
            v-for="col in previewParsed.key_candidates"
            :key="col"
            class="rounded border border-primary/30 bg-primary-light/40 px-2 py-0.5 font-mono text-[11px] text-primary"
          >{{ col }}</span>
        </div>
        <p class="muted mt-1 text-[10px]">如果对比时报 "duplicate key",说明主键不够区分行 — 把更多列加进 key_columns(如时间/流水号)。</p>
      </div>

      <details v-if="previewParsed.formatted_sql">
        <summary class="cursor-pointer text-xs font-semibold text-slate-600">已格式化 SQL</summary>
        <pre class="mt-2 max-h-64 overflow-auto rounded bg-slate-950 p-3 text-[11px] leading-relaxed text-slate-100">{{ previewParsed.formatted_sql }}</pre>
      </details>
    </div>

    <!-- 异步状态 / 其他原文预览(错误信息 / 执行中提示) -->
    <pre
      v-else-if="previewOutput || asyncStatus"
      class="card max-h-[420px] resize-y overflow-auto bg-slate-950 p-4 text-xs text-slate-100"
    >{{ asyncStatus ? JSON.stringify(asyncStatus, null, 2) : previewOutput }}</pre>

    <!-- 空态 -->
    <div v-else class="card flex flex-col items-center gap-2 border-dashed py-10 text-center">
      <Inbox class="h-10 w-10 text-slate-300" />
      <p class="muted">{{ $t('workbench.result.noResult') }}</p>
    </div>
  </section>
</template>
