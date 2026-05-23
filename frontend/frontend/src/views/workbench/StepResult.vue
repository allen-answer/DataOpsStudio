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

    <!-- 异步状态 / 预览输出 -->
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
