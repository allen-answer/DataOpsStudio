<script setup>
import { computed, onUnmounted, ref, watch } from 'vue'
import { Sparkles, AlertTriangle, ChevronRight, FileWarning, Code, AlertCircle, Columns, Loader2 } from 'lucide-vue-next'
import { apiGet } from '../../api'

const props = defineProps({
  inferred: { type: Object, default: () => ({ edges: [], column_hints: [], warnings: [], trigger_count: 0, filtered_count: 0 }) },
  parseErrors: { type: Array, default: () => [] },
  dynamicSqlSegments: { type: Array, default: () => [] },
  warnings: { type: Array, default: () => [] },
})

const emit = defineEmits(['update:inferred'])

// Phase 9 Day 5：异步 inference —— 收到 pending placeholder 时本地轮询；
// 拿到最终结果后用 finalInferred 替换 prop 显示，并 emit 让父级保存。
const finalInferred = ref(null)

const effective = computed(() => finalInferred.value || props.inferred || {})

const isPending = computed(() => {
  const s = effective.value?.status
  return s === 'pending' || s === 'running'
})
const jobId = computed(() => effective.value?.job_id || null)

const edges = computed(() => Array.isArray(effective.value?.edges) ? effective.value.edges : [])
const columnHints = computed(() => Array.isArray(effective.value?.column_hints) ? effective.value.column_hints : [])
const aiWarnings = computed(() => Array.isArray(effective.value?.warnings) ? effective.value.warnings : [])
const triggerCount = computed(() => Number(effective.value?.trigger_count || 0))
const filteredCount = computed(() => Number(effective.value?.filtered_count || 0))

let pollTimer = null

function stopPolling () {
  if (pollTimer) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
}

async function pollOnce () {
  if (!jobId.value) return
  try {
    const job = await apiGet(`/api/lineage/ai/jobs/${jobId.value}`)
    if (job?.status === 'ok' || job?.status === 'error') {
      finalInferred.value = job
      emit('update:inferred', job)
      stopPolling()
      return
    }
  } catch (e) {
    // 404 = job 已被清理 / 端点不可用：停轮询，留 pending banner
    stopPolling()
    return
  }
  // 还在 pending/running，继续轮询（指数退避：500ms → 1s → 2s → 上限 3s）
  pollTimer = setTimeout(pollOnce, Math.min(currentInterval(), 3000))
}

let intervalState = 500
function currentInterval () {
  const v = intervalState
  intervalState = Math.min(intervalState * 1.5, 3000)
  return v
}

watch(() => props.inferred, (val) => {
  // 父级换了新 result（新一轮分析）→ reset 本地状态
  finalInferred.value = null
  intervalState = 500
  stopPolling()
  if (val?.status === 'pending' || val?.status === 'running') {
    pollTimer = setTimeout(pollOnce, intervalState)
  }
}, { immediate: true })

onUnmounted(stopPolling)

// Phase 2：按 source_kind 分组（parse_error / dynamic_sql）
const parseErrorEdges = computed(() => edges.value.filter(e => (e.source_kind || 'parse_error') === 'parse_error'))
const dynamicSqlEdges = computed(() => edges.value.filter(e => e.source_kind === 'dynamic_sql'))

// 触发候选数（前端展示用，不一定全送了 AI）
const dynamicAICandidatesCount = computed(() => {
  const triggers = new Set(['unresolved', 'low', 'string_literal'])
  return (props.dynamicSqlSegments || []).filter(s => triggers.has(s?.confidence)).length
})

// Phase 3：result.warnings 里"字段歧义 / 字段来源未知"的数量（AI 候选）
const ambiguousColumnCount = computed(() => {
  const types = new Set(['字段歧义', '字段来源未知'])
  return (props.warnings || []).filter(w => types.has(w?.type)).length
})

const confidenceClass = (c) => ({
  low:    'bg-amber-100 text-amber-700',
  medium: 'bg-blue-100 text-blue-700',
}[c] || 'bg-slate-100 text-slate-600')

const dmlClass = (d) => ({
  INSERT:           'bg-green-100 text-green-700',
  CTAS:             'bg-green-100 text-green-700',
  UPDATE:           'bg-blue-100 text-blue-700',
  MERGE:            'bg-purple-100 text-purple-700',
  DELETE:           'bg-rose-100 text-rose-700',
  TRUNCATE_INSERT:  'bg-orange-100 text-orange-700',
}[d] || 'bg-slate-100 text-slate-600')
</script>

<template>
  <section class="space-y-4">
    <!-- 顶部说明 + 计数 -->
    <header class="card flex items-start gap-3 border-purple-200 bg-purple-50/40 p-4">
      <div class="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-purple-100 text-purple-700">
        <Sparkles class="h-5 w-5" />
      </div>
      <div class="flex-1 space-y-1">
        <h3 class="text-base font-bold text-slate-800">AI 兜底推断</h3>
        <p class="text-xs leading-relaxed text-slate-600">
          静态解析器对
          <strong class="font-bold text-rose-700">{{ parseErrors.length }}</strong> 条片段直接报错、
          <strong class="font-bold text-amber-700">{{ dynamicAICandidatesCount }}</strong> 条低置信 / 未解析的动态 SQL、
          <strong class="font-bold text-blue-700">{{ ambiguousColumnCount }}</strong> 个字段歧义/未归属 unqualified column，
          AI 用脚本里出现过的 <strong>表名 / 字段名白名单</strong>约束推断了
          <strong class="font-bold text-purple-700">{{ edges.length }}</strong> 条血缘边
          + <strong class="font-bold text-blue-700">{{ columnHints.length }}</strong> 条字段归属
          <span v-if="filteredCount" class="muted">（另过滤 {{ filteredCount }} 条非白名单 hallucination）</span>。
          <span class="block mt-0.5 text-[11px] text-purple-700">
            本面板内容均为 AI 推断（紫色 / 蓝色徽章区分），不进入主血缘图，仅供人工核对。
          </span>
        </p>
      </div>
    </header>

    <!-- Phase 9 Day 5：异步 pending 状态 -->
    <div
      v-if="isPending"
      class="card flex items-center gap-3 border-blue-200 bg-blue-50/40 p-3"
    >
      <Loader2 class="h-4 w-4 animate-spin text-blue-600" />
      <div class="flex-1">
        <p class="text-sm font-bold text-blue-800">
          AI 兜底推断进行中
          <span class="ml-2 pill bg-blue-100 text-blue-700 text-[10px]">
            {{ effective.status === 'running' ? '运行中' : '排队中' }}
          </span>
        </p>
        <p class="muted mt-0.5 text-[11px]">
          后台线程跑完会自动刷新结果（job_id: <code class="sql-font">{{ jobId?.slice(0, 8) }}</code>）。
        </p>
      </div>
    </div>

    <!-- 警告 -->
    <div v-if="aiWarnings.length" class="card border-amber-200 bg-amber-50/40 p-3">
      <p class="mb-2 flex items-center gap-1.5 text-xs font-bold text-amber-800">
        <AlertTriangle class="h-3.5 w-3.5" /> 推断过程警告 ({{ aiWarnings.length }})
      </p>
      <ul class="space-y-1 text-[11px] text-amber-900">
        <li v-for="(w, i) in aiWarnings" :key="i" class="flex gap-2">
          <span class="muted shrink-0">[{{ w.type }}{{ w.source_kind ? '/' + w.source_kind : '' }}]</span>
          <span>{{ w.message }}</span>
        </li>
      </ul>
    </div>

    <!-- 来源 1：parse_errors 兜底 -->
    <section v-if="parseErrorEdges.length">
      <div class="mb-2 flex items-center gap-2">
        <AlertCircle class="h-4 w-4 text-rose-600" />
        <h4 class="text-sm font-bold text-slate-800">解析失败兜底</h4>
        <span class="pill bg-rose-100 text-rose-700 text-[10px]">{{ parseErrorEdges.length }} 条</span>
        <span class="muted text-[11px]">sqlglot 直接抛错的片段，AI 从 0 推断</span>
      </div>
      <div class="space-y-3">
        <article
          v-for="(edge, i) in parseErrorEdges" :key="`pe-${i}`"
          class="card relative space-y-3 border-l-4 border-l-rose-400 p-4"
        >
          <span class="pill absolute right-3 top-3 bg-rose-100 text-[10px] uppercase tracking-wider text-rose-700">
            AI · parse_error
          </span>

          <div class="flex flex-wrap items-center gap-2 text-sm">
            <span class="sql-font rounded bg-slate-100 px-2 py-1 font-medium text-slate-700">
              {{ edge.source_table || '(未知源)' }}
            </span>
            <ChevronRight class="h-4 w-4 text-rose-400" />
            <span class="sql-font rounded bg-rose-50 px-2 py-1 font-medium text-rose-800 ring-1 ring-rose-200">
              {{ edge.target_table }}
            </span>
            <span class="pill" :class="dmlClass(edge.dml_type)">{{ edge.dml_type }}</span>
            <span class="pill" :class="confidenceClass(edge.confidence)">{{ edge.confidence }}</span>
            <span v-if="edge.fragment_index !== undefined" class="pill bg-slate-100 text-slate-500 text-[10px]">
              片段 #{{ edge.fragment_index }}
            </span>
          </div>

          <div v-if="edge.source_columns?.length || edge.target_columns?.length" class="grid grid-cols-1 gap-2 md:grid-cols-2">
            <div v-if="edge.source_columns?.length">
              <p class="muted mb-1 text-[10px] font-bold uppercase">源字段</p>
              <ul class="flex flex-wrap gap-1">
                <li v-for="col in edge.source_columns" :key="col"
                    class="sql-font rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-700">{{ col }}</li>
              </ul>
            </div>
            <div v-if="edge.target_columns?.length">
              <p class="muted mb-1 text-[10px] font-bold uppercase">目标字段</p>
              <ul class="flex flex-wrap gap-1">
                <li v-for="col in edge.target_columns" :key="col"
                    class="sql-font rounded bg-rose-50 px-1.5 py-0.5 text-[11px] text-rose-700">{{ col }}</li>
              </ul>
            </div>
          </div>

          <div>
            <p class="muted mb-0.5 text-[10px] font-bold uppercase">AI 推断依据</p>
            <p class="text-[12px] leading-relaxed text-slate-700">{{ edge.reason }}</p>
          </div>

          <details v-if="edge.evidence" class="text-[11px]">
            <summary class="cursor-pointer text-rose-700 hover:underline">查看 evidence（原 SQL 片段）</summary>
            <pre class="sql-font mt-1.5 max-h-48 overflow-auto rounded bg-slate-50 p-2 text-[11px] text-slate-700">{{ edge.evidence }}</pre>
          </details>
        </article>
      </div>
    </section>

    <!-- 来源 3：字段归属推荐（Phase 3） -->
    <section v-if="columnHints.length">
      <div class="mb-2 flex items-center gap-2">
        <Columns class="h-4 w-4 text-blue-600" />
        <h4 class="text-sm font-bold text-slate-800">字段归属推荐</h4>
        <span class="pill bg-blue-100 text-blue-700 text-[10px]">{{ columnHints.length }} 条</span>
        <span class="muted text-[11px]">多表 unqualified column 缺 schema → AI 推荐归属</span>
      </div>
      <div class="space-y-2">
        <article
          v-for="(hint, i) in columnHints" :key="`col-${i}`"
          class="card relative space-y-2 border-l-4 border-l-blue-400 p-3"
        >
          <span class="pill absolute right-3 top-3 bg-blue-100 text-[10px] uppercase tracking-wider text-blue-700">
            AI · column_attribution
          </span>
          <div class="flex flex-wrap items-center gap-2 text-sm">
            <span class="sql-font rounded bg-slate-100 px-2 py-0.5 font-medium text-slate-700">
              {{ hint.column }}
            </span>
            <ChevronRight class="h-3.5 w-3.5 text-blue-400" />
            <span class="muted text-[11px]">归属</span>
            <span class="sql-font rounded bg-blue-50 px-2 py-0.5 font-medium text-blue-800 ring-1 ring-blue-200">
              {{ hint.suggested_table }}
            </span>
            <span class="pill" :class="confidenceClass(hint.confidence)">{{ hint.confidence }}</span>
          </div>
          <p class="text-[12px] leading-relaxed text-slate-700">{{ hint.reason }}</p>
          <details v-if="hint.evidence" class="text-[11px]">
            <summary class="cursor-pointer text-blue-700 hover:underline">查看 evidence</summary>
            <pre class="sql-font mt-1 max-h-32 overflow-auto rounded bg-slate-50 p-1.5 text-[11px] text-slate-700">{{ hint.evidence }}</pre>
          </details>
        </article>
      </div>
    </section>

    <!-- 来源 2：dynamic_sql_segments 兜底 -->
    <section v-if="dynamicSqlEdges.length">
      <div class="mb-2 flex items-center gap-2">
        <Code class="h-4 w-4 text-amber-600" />
        <h4 class="text-sm font-bold text-slate-800">动态 SQL 兜底</h4>
        <span class="pill bg-amber-100 text-amber-700 text-[10px]">{{ dynamicSqlEdges.length }} 条</span>
        <span class="muted text-[11px]">变量拼接 / 未解析的 EXECUTE IMMEDIATE，AI 用过程上下文推断目标</span>
      </div>
      <div class="space-y-3">
        <article
          v-for="(edge, i) in dynamicSqlEdges" :key="`dyn-${i}`"
          class="card relative space-y-3 border-l-4 border-l-amber-400 p-4"
        >
          <span class="pill absolute right-3 top-3 bg-amber-100 text-[10px] uppercase tracking-wider text-amber-700">
            AI · dynamic_sql
          </span>

          <div class="flex flex-wrap items-center gap-2 text-sm">
            <span class="sql-font rounded bg-slate-100 px-2 py-1 font-medium text-slate-700">
              {{ edge.source_table || '(未知源)' }}
            </span>
            <ChevronRight class="h-4 w-4 text-amber-400" />
            <span class="sql-font rounded bg-amber-50 px-2 py-1 font-medium text-amber-800 ring-1 ring-amber-200">
              {{ edge.target_table }}
            </span>
            <span class="pill" :class="dmlClass(edge.dml_type)">{{ edge.dml_type }}</span>
            <span class="pill" :class="confidenceClass(edge.confidence)">{{ edge.confidence }}</span>
            <span v-if="edge.fragment_index !== undefined" class="pill bg-slate-100 text-slate-500 text-[10px]">
              segment #{{ edge.fragment_index }}
            </span>
          </div>

          <div v-if="edge.source_columns?.length || edge.target_columns?.length" class="grid grid-cols-1 gap-2 md:grid-cols-2">
            <div v-if="edge.source_columns?.length">
              <p class="muted mb-1 text-[10px] font-bold uppercase">源字段</p>
              <ul class="flex flex-wrap gap-1">
                <li v-for="col in edge.source_columns" :key="col"
                    class="sql-font rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-700">{{ col }}</li>
              </ul>
            </div>
            <div v-if="edge.target_columns?.length">
              <p class="muted mb-1 text-[10px] font-bold uppercase">目标字段</p>
              <ul class="flex flex-wrap gap-1">
                <li v-for="col in edge.target_columns" :key="col"
                    class="sql-font rounded bg-amber-50 px-1.5 py-0.5 text-[11px] text-amber-700">{{ col }}</li>
              </ul>
            </div>
          </div>

          <div>
            <p class="muted mb-0.5 text-[10px] font-bold uppercase">AI 推断依据</p>
            <p class="text-[12px] leading-relaxed text-slate-700">{{ edge.reason }}</p>
          </div>

          <details v-if="edge.evidence" class="text-[11px]">
            <summary class="cursor-pointer text-amber-700 hover:underline">查看 evidence（动态 SQL 片段）</summary>
            <pre class="sql-font mt-1.5 max-h-48 overflow-auto rounded bg-slate-50 p-2 text-[11px] text-slate-700">{{ edge.evidence }}</pre>
          </details>
        </article>
      </div>
    </section>

    <!-- 无推断结果 -->
    <div v-else-if="triggerCount === 0 && parseErrors.length === 0 && dynamicAICandidatesCount === 0 && ambiguousColumnCount === 0" class="card border-dashed py-10 text-center">
      <FileWarning class="mx-auto h-8 w-8 text-slate-300" />
      <p class="mt-2 text-sm text-slate-500">本次分析没有解析失败片段、低置信动态 SQL 或字段歧义，无需 AI 兜底</p>
    </div>

    <div v-else-if="triggerCount === 0 && (parseErrors.length > 0 || dynamicAICandidatesCount > 0 || ambiguousColumnCount > 0)" class="card border-dashed py-8 text-center">
      <Sparkles class="mx-auto h-8 w-8 text-slate-300" />
      <p class="mt-2 text-sm text-slate-500">
        检测到 {{ parseErrors.length }} 条 parse_errors
        <span v-if="dynamicAICandidatesCount">+ {{ dynamicAICandidatesCount }} 条低置信动态 SQL</span>
        <span v-if="ambiguousColumnCount">+ {{ ambiguousColumnCount }} 条字段歧义</span>
      </p>
      <p class="mt-1 text-xs text-slate-400">在 admin → AI 配置中开启「启用 AI 解析失败兜底」即可让 AI 推断这些片段的血缘</p>
    </div>

    <div v-else class="card border-dashed py-6 text-center">
      <p class="text-sm text-slate-500">AI 调用 {{ triggerCount }} 次，但都没产出有效推断</p>
      <p class="mt-1 text-xs text-slate-400">可能是 AI 拒绝推断（理由不足）或返回了 hallucination 全被白名单过滤掉</p>
    </div>
  </section>
</template>
