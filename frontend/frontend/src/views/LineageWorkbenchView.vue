<script setup lang="ts">
import { defineAsyncComponent, ref, computed, watch, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { storeToRefs } from 'pinia'
import { Code2, FileText, Files, Archive, Sparkles } from 'lucide-vue-next'
import SchemaPanel from '../components/SchemaPanel.vue'
import LineageReportView from './LineageReportView.vue'
import { apiGet } from '../api'
import { useLineageStore } from '../stores/lineage'
import { useBatchStore } from '../stores/batch'
import { downloadResultFile } from '../utils/download'
import { takeSqlTransfer } from '../utils/sqlTransfer'

type ModeId = 'paste' | 'file' | 'multi' | 'zip'
type Pipeline = 'single' | 'batch'

// Phase 4：合并"单脚本血缘"和"多脚本分析"为统一血缘分析工作台。
// 4 种输入模式：粘贴 SQL / 上传单文件 / 上传多文件 / 上传 ZIP
// 前两种走 analyzeLineage（单脚本路径），后两种走 analyzeBatch（多脚本路径）。
// 结果区统一用 LineageReportView 消费 result.report。

const SqlEditor = defineAsyncComponent(() => import('../components/SqlEditor.vue'))

const lineageStore = useLineageStore()
const { lineage } = lineageStore                       // reactive
const { lineageAIStatus } = storeToRefs(lineageStore)  // ref
const { analyzeLineage, loadLineageAIStatus } = lineageStore

const batchStore = useBatchStore()
const { batch } = batchStore                           // reactive
const { batchSelectedFileNames } = storeToRefs(batchStore)
const { analyzeBatch } = batchStore

const route = useRoute()

interface ModeMeta {
  id: ModeId
  label: string
  icon: unknown
  pipeline: Pipeline
  hint: string
}

const MODES: ModeMeta[] = [
  { id: 'paste',     label: '粘贴 SQL',     icon: Code2,   pipeline: 'single', hint: '直接粘贴 SQL 文本（推荐快速调试）' },
  { id: 'file',      label: '上传单文件',   icon: FileText,pipeline: 'single', hint: '单个 .sql / .txt 文件' },
  { id: 'multi',     label: '上传多文件',   icon: Files,   pipeline: 'batch',  hint: '多个 .sql / .txt（跨脚本血缘 + 影响传递）' },
  { id: 'zip',       label: '上传 ZIP 包',  icon: Archive, pipeline: 'batch',  hint: '一个 .zip 项目包（自动解压全部 .sql/.txt）' },
]

// 默认 mode 由 route.path 决定：/lineage → paste；/batch-lineage → multi
function defaultModeFromRoute(path: string): ModeId {
  if (path === '/batch-lineage' || path.startsWith('/batch-lineage')) return 'multi'
  return 'paste'
}

const mode = ref<ModeId>(defaultModeFromRoute(route.path))
watch(() => route.path, (p) => { mode.value = defaultModeFromRoute(p) })
onMounted(async () => {
  mode.value = defaultModeFromRoute(route.path)
  loadLineageAIStatus?.()

  // Phase 4 (sql-workbench): 从 SqlWorkbench 跳过来时,sessionStorage 里有 SQL,
  // 预填到粘贴 SQL 模式的输入框
  const transfer = takeSqlTransfer()
  if (transfer?.sql) {
    mode.value = 'paste'
    lineage.sql = transfer.sql
  }

  // Phase 10 #1：URL ?stress=N → 加载合成压测 fixture，跳过分析。用于在浏览器
  // 跑 G6 / Cytoscape 双引擎压测对比。落到 lineage state 即可，单脚本路径渲染。
  const stress = route.query.stress
  if (stress) {
    const size = parseInt(String(stress), 10)
    if (Number.isNaN(size) || size < 10 || size > 10000) {
      lineage.error = `stress 参数必须在 [10, 10000] 区间，当前 ${stress}`
      return
    }
    mode.value = 'paste'
    try {
      lineage.error = ''
      const data = await apiGet<Record<string, unknown>>(`/api/lineage/stress-fixture?size=${size}`)
      lineage.result = data
    } catch (e: any) {
      lineage.error = `加载 stress fixture 失败：${e?.message || e}`
    }
  }
})

const currentModeMeta = computed(() => MODES.find(m => m.id === mode.value))
const isSinglePipeline = computed(() => currentModeMeta.value?.pipeline === 'single')

// 当前 mode 对应的 state（lineage 或 batch）和分析 handler
const inputState = computed(() => isSinglePipeline.value ? lineage : batch)
const result = computed(() => inputState.value?.result || null)
const error = computed(() => inputState.value?.error || '')
const report = computed(() => result.value?.report || null)
const isAnalyzing = computed(() => Boolean(inputState.value?.isAnalyzing))
const aiPending = computed(() => (result.value?.ai_enrichment as any)?.status === 'pending')
const progressVisible = computed(() => isAnalyzing.value || aiPending.value)
const progressLabel = computed(() => {
  if (isAnalyzing.value) return '规则血缘解析中'
  if (aiPending.value) return 'AI 辅助分析中'
  return ''
})
const progressHint = computed(() => {
  if (isAnalyzing.value) return '正在读取 SQL / 文件并生成确定性血缘结果'
  if (aiPending.value) return '规则血缘已返回，AI 结果完成后会自动补充到 AI 辅助页'
  return ''
})
const aiReady = computed(() => Boolean(lineageAIStatus.value?.enabled && lineageAIStatus.value?.configured))
const aiStatusHint = computed(() => {
  if (!lineageAIStatus.value) return '正在读取 AI 配置状态'
  if (aiReady.value) {
    const parts = [`provider: ${lineageAIStatus.value.provider}`]
    if (lineageAIStatus.value.model) parts.push(`model: ${lineageAIStatus.value.model}`)
    return parts.join(' · ')
  }
  if (lineageAIStatus.value.enabled) return 'AI provider 已设置但配置不完整，请补齐 model / API Key'
  return 'AI provider 未启用；请到 AI 配置选择 provider 并保存'
})

watch([aiReady, mode], () => {
  if (!aiReady.value && inputState.value?.aiEnabled) {
    inputState.value.aiEnabled = false
  }
}, { immediate: true })

function runAnalyze(): void {
  if (isSinglePipeline.value) {
    analyzeLineage()
  } else {
    analyzeBatch()
  }
}

// "上传脚本包" / "导出 Excel" 仅多脚本有 batch.exports
const batchExports = computed(() => batch.exports)

// 单文件模式：用 lineage.sqlFile 但前端 SqlEditor 隐藏；后端 analyzeLineage 优先取 sqlFile
function onSingleFileChange(e: Event): void {
  const target = e.target as HTMLInputElement | null
  const f = target?.files?.[0]
  if (f) lineage.sqlFile = f
}

function onMultiFilesChange(e: Event): void {
  const target = e.target as HTMLInputElement | null
  batch.files = target?.files ? Array.from(target.files) : []
}

function onZipChange(e: Event): void {
  const target = e.target as HTMLInputElement | null
  const f = target?.files?.[0]
  batch.files = f ? [f] : []
}
</script>

<template>
  <section class="space-y-4">
    <!-- 头部 + 模式切换 -->
    <div class="card">
      <div class="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 class="text-2xl font-bold text-slate-800">{{ $t('pages.lineage.title') }}</h2>
          <p class="muted text-sm">{{ $t('pages.lineage.subtitle') }}</p>
        </div>
        <button class="btn btn-primary" :disabled="progressVisible" @click="runAnalyze">
          <Sparkles class="h-4 w-4" /> {{ $t('pages.lineage.analyze') }}
        </button>
      </div>

      <div v-if="progressVisible" class="mb-4 rounded-xl border border-violet-100 bg-violet-50/70 px-3 py-2">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p class="text-sm font-semibold text-violet-800">{{ progressLabel }}</p>
            <p class="mt-1 text-xs text-violet-600">{{ progressHint }}</p>
          </div>
          <span class="rounded-full bg-white px-2.5 py-1 text-xs font-semibold text-violet-700 ring-1 ring-violet-200">
            {{ isAnalyzing ? '解析' : 'AI' }}
          </span>
        </div>
        <div class="mt-2 h-2 overflow-hidden rounded-full bg-white">
          <div class="h-full w-1/2 rounded-full bg-violet-600 motion-safe:animate-pulse"></div>
        </div>
      </div>

      <!-- 4 模式 tab -->
      <div class="mb-4 grid grid-cols-2 gap-2 md:grid-cols-4">
        <button
          v-for="m in MODES" :key="m.id"
          type="button"
          class="flex items-center gap-2 rounded-lg border-2 p-3 text-left transition"
          :class="mode === m.id
            ? 'border-primary bg-primary-light'
            : 'border-slate-200 bg-white hover:border-primary/40'"
          @click="mode = m.id"
        >
          <component :is="m.icon" class="h-5 w-5 shrink-0"
            :class="mode === m.id ? 'text-primary' : 'text-slate-400'" />
          <div class="min-w-0">
            <div class="text-sm font-semibold"
              :class="mode === m.id ? 'text-primary' : 'text-slate-700'">{{ m.label }}</div>
            <div class="muted truncate text-[11px]">{{ m.hint }}</div>
          </div>
        </button>
      </div>

      <!-- 输入区：按 mode 渲染 -->
      <div v-if="mode === 'paste'">
        <SqlEditor v-model="lineage.sql" placeholder="粘贴 SQL，多条语句用分号分隔" />
      </div>

      <div v-else-if="mode === 'file'" class="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-6 text-center">
        <FileText class="mx-auto mb-2 h-8 w-8 text-slate-400" />
        <input
          type="file" accept=".sql,.txt"
          class="mx-auto block w-fit"
          @change="onSingleFileChange"
        >
        <p v-if="lineage.sqlFile" class="mt-2 text-sm text-slate-700">
          已选择：<strong>{{ lineage.sqlFile.name }}</strong>
        </p>
        <p v-else class="muted mt-2 text-xs">支持 .sql / .txt；上传后用单脚本路径分析</p>
      </div>

      <div v-else-if="mode === 'multi'" class="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-6 text-center">
        <Files class="mx-auto mb-2 h-8 w-8 text-slate-400" />
        <input
          type="file" accept=".sql,.txt" multiple
          class="mx-auto block w-fit"
          @change="onMultiFilesChange"
        >
        <p v-if="batchSelectedFileNames.length" class="mt-3 flex flex-wrap justify-center gap-1.5">
          <span v-for="n in batchSelectedFileNames" :key="n" class="rounded-full bg-primary-light px-2.5 py-1 text-xs font-semibold text-primary sql-font">{{ n }}</span>
        </p>
        <p v-else class="muted mt-2 text-xs">多脚本路径：跨文件血缘 + 影响传递闭包</p>
      </div>

      <div v-else-if="mode === 'zip'" class="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-6 text-center">
        <Archive class="mx-auto mb-2 h-8 w-8 text-slate-400" />
        <input
          type="file" accept=".zip"
          class="mx-auto block w-fit"
          @change="onZipChange"
        >
        <p v-if="batch.files?.[0]" class="mt-2 text-sm text-slate-700">
          已选择：<strong>{{ batch.files[0].name }}</strong>
        </p>
        <p v-else class="muted mt-2 text-xs">后端自动解压所有 .sql/.txt 后批量分析</p>
      </div>

      <!-- Schema 配置（共用两侧 state） -->
      <div class="mt-4">
        <SchemaPanel
          :target="inputState"
          :sql-tables-label="isSinglePipeline ? '只拉 SQL 中出现的表' : '只拉脚本中出现的表'"
        >
          <template #prefix>
            <label>
              <span class="muted mb-1 block text-[10px] font-bold uppercase tracking-wider">SQL 方言</span>
              <select v-model="inputState.dialect" class="bg-slate-50">
                <option value="">自动</option>
                <option>mysql</option>
                <option>oracle</option>
                <option>tsql</option>
                <option>postgres</option>
                <option>dm</option>
                <option>ob_mysql</option>
                <option>ob_oracle</option>
              </select>
            </label>
          </template>
        </SchemaPanel>
      </div>

      <div class="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-violet-100 bg-violet-50/60 px-3 py-2">
        <label class="inline-flex items-center gap-2 text-sm font-semibold text-slate-700">
          <input
            v-model="inputState.aiEnabled"
            type="checkbox"
            :disabled="!aiReady"
            class="h-4 w-4 rounded border-violet-300 text-violet-600 disabled:cursor-not-allowed disabled:opacity-50"
          >
          AI 辅助分析
        </label>
        <p class="text-xs text-slate-500">
          <span>{{ aiStatusHint }}</span>
          <a
            v-if="!aiReady"
            href="#/admin/ai"
            class="ml-2 font-semibold text-violet-700 hover:text-violet-800"
          >去配置</a>
        </p>
      </div>
    </div>

    <!-- 错误条 -->
    <div v-if="progressVisible" class="card border-violet-100 bg-violet-50/60">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p class="text-sm font-semibold text-violet-800">{{ progressLabel }}</p>
          <p class="mt-1 text-xs text-violet-600">{{ progressHint }}</p>
        </div>
        <span class="rounded-full bg-white px-2.5 py-1 text-xs font-semibold text-violet-700 ring-1 ring-violet-200">
          {{ isAnalyzing ? '解析' : 'AI' }}
        </span>
      </div>
      <div class="mt-3 h-2 overflow-hidden rounded-full bg-white">
        <div class="h-full w-1/2 rounded-full bg-violet-600 motion-safe:animate-pulse"></div>
      </div>
    </div>

    <div v-if="error" class="card border-status-error-bg bg-status-error-bg/40 text-status-error">
      {{ error }}
    </div>

    <!-- 多脚本导出条 -->
    <div v-if="!isSinglePipeline && batchExports?.excel_filename" class="card flex flex-wrap items-center justify-between gap-3">
      <span class="muted text-xs">分析完成，可下载完整报告：</span>
      <div class="flex gap-2">
        <button type="button" class="btn btn-outline h-9 gap-1.5 px-3 text-xs" @click="downloadResultFile(batchExports.excel_filename)">下载 Excel</button>
        <button type="button" class="btn btn-outline h-9 gap-1.5 px-3 text-xs" @click="downloadResultFile(batchExports.json_filename)">下载 JSON</button>
      </div>
    </div>

    <!-- 9-tab 报告 -->
    <LineageReportView
      v-if="report && result"
      :report="report"
      :graph-groups="isSinglePipeline ? ((result as any).graph_groups || []) : ((result as any).table_groups || [])"
      :graph-edges="isSinglePipeline ? ((result as any).graph_edges || []) : ((result as any).table_edges || [])"
      :columns="isSinglePipeline ? ((result as any).columns || []) : []"
      :insert-mappings="isSinglePipeline ? ((result as any).insert_mappings || []) : ((result as any).field_mappings || [])"
      :ai-enrichment="(result as any).ai_enrichment || {}"
      :ai-inferred="(result as any).ai_inferred || {}"
      :parse-errors="(result as any).parse_errors || []"
      :dynamic-sql-segments="(result as any).dynamic_sql_segments || []"
      :ambiguous-column-warnings="(result as any).warnings || []"
    />
  </section>
</template>
