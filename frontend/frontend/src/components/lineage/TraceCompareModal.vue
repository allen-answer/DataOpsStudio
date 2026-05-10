<script setup lang="ts">
// Phase 11 MVP #2 —— 沿字段血缘对每跳生成 compare 节点的弹窗。
//
// 流程：
//   1. 用户填 key_column / sample_keys / depth / base_task_id
//   2. 点「预览 chain」→ POST /api/lineage/trace-compare（空 datasource_map）拿
//      到节点列表 + 链上涉及的所有表，展示给用户
//   3. 用户给每张表选 datasource（预填 lineage 反查到的 datasource，缺的留空）
//   4. 点「保存为作业流」→ 再调一次带完整 map 的 trace-compare 拿最终 draft，
//      接着 POST /api/workflows 持久化，emit 关闭 + 跳到 /workflows/:id
//
// 不做：
//   - Ad-hoc inline 跑（要后端新 endpoint，留下次）
//   - per_table_keys 编辑（先信全链同 PK 的常见情况）
import { computed, onMounted, ref, watch } from 'vue'
import { X, Sparkles, AlertCircle, Save } from 'lucide-vue-next'
import { apiGet, apiJson } from '../../api'

interface Props {
  tableName: string
  columnName: string
}
const props = defineProps<Props>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'workflow-created', payload: { id: string; name: string }): void
}>()

interface Datasource { id: string; name: string; db_type: string }
interface CompareTask { id: string; name: string }
interface ChainEdge {
  hop: number
  upstream: { table: string; column: string }
  downstream: { table: string; column: string }
  strategy: string
}
interface DraftNode {
  id: string
  name: string
  config: {
    task_id: string
    source_sql_override: string
    target_sql_override: string
    key_columns_override: string[]
    _trace_compare?: {
      hop: number
      datasource_source: string
      datasource_target: string
      unmapped_tables: string[]
    }
  }
}
interface TraceResult {
  focal: { table: string; column: string }
  chain: ChainEdge[]
  workflow_draft: {
    name: string
    description: string
    nodes: DraftNode[]
    tags: string[]
    project_id: string
  }
  warnings: string[]
  stats: { edge_count: number; node_count: number; upstream_truncated: boolean }
}

// ─── form state ─────────────────────────────────────────────────────────────

const keyColumn = ref<string>('id')
const sampleKeysText = ref<string>('')
const depth = ref<number>(3)
const baseTaskId = ref<string>('')
const tasks = ref<CompareTask[]>([])
const datasources = ref<Datasource[]>([])

// ─── result state ───────────────────────────────────────────────────────────

const previewing = ref<boolean>(false)
const previewResult = ref<TraceResult | null>(null)
const error = ref<string>('')

// 链上涉及的所有表 → 用户给每张表选的 datasource_id
const datasourceMap = ref<Record<string, string>>({})

const saving = ref<boolean>(false)

// ─── data load ──────────────────────────────────────────────────────────────

async function loadDeps(): Promise<void> {
  try {
    const [t, d] = await Promise.all([
      apiGet<CompareTask[]>('/api/tasks').catch(() => [] as CompareTask[]),
      apiGet<Datasource[]>('/api/assets/datasources').catch(() => [] as Datasource[]),
    ])
    tasks.value = Array.isArray(t) ? t : []
    datasources.value = Array.isArray(d) ? d : []
    if (!baseTaskId.value && tasks.value[0]) baseTaskId.value = tasks.value[0].id
  } catch (e: any) {
    error.value = `初始化失败：${e?.message || e}`
  }
}

const sampleKeys = computed<Array<string | number>>(() => {
  const text = sampleKeysText.value.trim()
  if (!text) return []
  return text.split(/[\s,，]+/).filter(Boolean).map((v) => {
    // 数字识别：纯数字串走 number，其它走 string
    if (/^-?\d+$/.test(v)) return Number(v)
    if (/^-?\d+\.\d+$/.test(v)) return Number(v)
    return v
  })
})

// 链上所有表（focal 也算）
const tablesInChain = computed<string[]>(() => {
  if (!previewResult.value) return []
  const seen = new Set<string>()
  for (const edge of previewResult.value.chain) {
    seen.add(edge.upstream.table)
    seen.add(edge.downstream.table)
  }
  return Array.from(seen).sort()
})

const hasUnmapped = computed<boolean>(() => {
  if (!previewResult.value) return false
  return tablesInChain.value.some((t) => !datasourceMap.value[t.toLowerCase()])
})

// ─── actions ────────────────────────────────────────────────────────────────

async function preview(): Promise<void> {
  error.value = ''
  if (!keyColumn.value.trim()) { error.value = '请填 key_column'; return }
  if (!baseTaskId.value) { error.value = '请选 base task（compare 节点壳子）'; return }
  previewing.value = true
  try {
    const result = await apiJson<TraceResult>('/api/lineage/trace-compare', 'POST', {
      table: props.tableName,
      column: props.columnName,
      key_column: keyColumn.value.trim(),
      base_task_id: baseTaskId.value,
      sample_keys: sampleKeys.value,
      datasource_map: { ...datasourceMap.value },
      depth: depth.value,
    })
    previewResult.value = result
    // 用 result 里 _trace_compare.datasource_source / datasource_target 反向预填
    // datasourceMap，让用户只补缺的表
    for (const node of result.workflow_draft.nodes) {
      const meta = node.config._trace_compare
      if (!meta) continue
      const up = result.chain.find((e) => e.hop === meta.hop)?.upstream.table
      const down = result.chain.find((e) => e.hop === meta.hop)?.downstream.table
      if (up && meta.datasource_source && !datasourceMap.value[up.toLowerCase()]) {
        datasourceMap.value[up.toLowerCase()] = meta.datasource_source
      }
      if (down && meta.datasource_target && !datasourceMap.value[down.toLowerCase()]) {
        datasourceMap.value[down.toLowerCase()] = meta.datasource_target
      }
    }
  } catch (e: any) {
    error.value = `预览失败：${e?.message || e}`
    previewResult.value = null
  } finally {
    previewing.value = false
  }
}

async function saveAsWorkflow(): Promise<void> {
  if (!previewResult.value) return
  if (hasUnmapped.value && !confirm('仍有表未选 datasource，作业流跑会失败。继续保存吗？')) {
    return
  }
  saving.value = true
  error.value = ''
  try {
    // 重新拉一次 trace-compare 拿带最终 datasource_map 的 draft（让 _trace_compare
    // meta 也跟着更新）
    const fresh = await apiJson<TraceResult>('/api/lineage/trace-compare', 'POST', {
      table: props.tableName,
      column: props.columnName,
      key_column: keyColumn.value.trim(),
      base_task_id: baseTaskId.value,
      sample_keys: sampleKeys.value,
      datasource_map: { ...datasourceMap.value },
      depth: depth.value,
    })
    previewResult.value = fresh
    const created = await apiJson<{ id: string; name: string }>(
      '/api/workflows', 'POST', fresh.workflow_draft,
    )
    emit('workflow-created', { id: created.id, name: created.name })
    emit('close')
  } catch (e: any) {
    error.value = `保存失败：${e?.message || e}`
  } finally {
    saving.value = false
  }
}

function copyDraftJson(): void {
  if (!previewResult.value) return
  const text = JSON.stringify(previewResult.value.workflow_draft, null, 2)
  navigator.clipboard?.writeText(text).then(
    () => { error.value = '✓ 已复制 draft JSON 到剪贴板' },
    () => { error.value = '复制失败 —— 浏览器拒绝 clipboard 权限' },
  )
}

onMounted(loadDeps)
// table/column 切换时清掉旧结果，重新预热
watch(() => `${props.tableName}::${props.columnName}`, () => {
  previewResult.value = null
  datasourceMap.value = {}
  error.value = ''
})
</script>

<template>
  <div class="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 px-4" @click.self="emit('close')">
    <div class="w-full max-w-3xl rounded-xl bg-white shadow-xl">
      <!-- header -->
      <div class="flex items-start justify-between border-b border-slate-200 px-5 py-3">
        <div>
          <h3 class="flex items-center gap-2 text-base font-semibold text-slate-800">
            <Sparkles class="h-4 w-4 text-primary" />
            沿血缘溯源对比
          </h3>
          <p class="mt-0.5 text-xs text-slate-500">
            焦点：<span class="sql-font">{{ tableName }}.{{ columnName }}</span>
            <span class="ml-2 muted">— 按字段血缘上溯 N 跳，每条边生成一个 compare 节点</span>
          </p>
        </div>
        <button class="text-slate-400 hover:text-slate-600" @click="emit('close')" title="关闭">
          <X class="h-4 w-4" />
        </button>
      </div>

      <!-- body -->
      <div class="space-y-4 px-5 py-4">
        <!-- form -->
        <div class="grid grid-cols-2 gap-3 text-xs">
          <label class="flex flex-col gap-1">
            <span class="font-medium text-slate-700">PK 字段（join 主键）</span>
            <input v-model="keyColumn" class="rounded border border-slate-300 px-2 py-1 sql-font"
                   placeholder="id" />
          </label>
          <label class="flex flex-col gap-1">
            <span class="font-medium text-slate-700">追溯深度</span>
            <select v-model.number="depth" class="rounded border border-slate-300 px-2 py-1">
              <option :value="1">1 跳（直接邻居）</option>
              <option :value="2">2 跳</option>
              <option :value="3">3 跳</option>
              <option :value="5">5 跳</option>
            </select>
          </label>
          <label class="col-span-2 flex flex-col gap-1">
            <span class="font-medium text-slate-700">采样 PK 值（可选，逗号分隔）</span>
            <input v-model="sampleKeysText" class="rounded border border-slate-300 px-2 py-1 sql-font"
                   placeholder="例如 1, 2, 3 或 'A001', 'A002'（留空 = 全表对比）" />
            <span class="muted text-[10px]">
              纯数字直传，其它当字符串；流式 compare 要求两端按 PK 同序，所以 SQL 必加 ORDER BY
            </span>
          </label>
          <label class="col-span-2 flex flex-col gap-1">
            <span class="font-medium text-slate-700">Base task（compare 节点壳子）</span>
            <select v-model="baseTaskId" class="rounded border border-slate-300 px-2 py-1">
              <option value="">— 选一个已有 task —</option>
              <option v-for="t in tasks" :key="t.id" :value="t.id">{{ t.name }} ({{ t.id.slice(0, 8) }})</option>
            </select>
            <span v-if="!tasks.length" class="text-[10px] text-amber-700">
              ⚠ 没有现有 task。请先在「数据对比」页建一个，作为各 hop 节点的 SQL 覆盖壳子
            </span>
          </label>
        </div>

        <!-- preview button row -->
        <div class="flex items-center justify-between">
          <button
            class="btn btn-primary text-xs"
            :disabled="previewing || !keyColumn || !baseTaskId"
            @click="preview"
          >
            {{ previewing ? '生成中…' : (previewResult ? '重新预览' : '预览 chain') }}
          </button>
          <span v-if="error"
                class="flex items-center gap-1 text-xs"
                :class="error.startsWith('✓') ? 'text-emerald-700' : 'text-rose-600'">
            <AlertCircle v-if="!error.startsWith('✓')" class="h-3.5 w-3.5" />
            {{ error }}
          </span>
        </div>

        <!-- preview result -->
        <div v-if="previewResult" class="space-y-3">
          <!-- chain summary -->
          <div class="rounded border border-slate-200 bg-slate-50/60 px-3 py-2">
            <p class="text-xs font-semibold text-slate-700">
              生成 {{ previewResult.stats.node_count }} 个 compare 节点
              · {{ tablesInChain.length }} 张表
              <span v-if="previewResult.warnings.length"
                    class="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] text-amber-800">
                {{ previewResult.warnings.length }} 条警告
              </span>
            </p>
            <ul class="mt-1 space-y-0.5 text-[11px] sql-font text-slate-600">
              <li v-for="(edge, idx) in previewResult.chain" :key="idx">
                <span class="muted mr-1">hop {{ edge.hop }}</span>
                <span class="text-blue-700">{{ edge.upstream.table }}.{{ edge.upstream.column }}</span>
                <span class="mx-1 text-slate-400">→</span>
                <span class="text-emerald-700">{{ edge.downstream.table }}.{{ edge.downstream.column }}</span>
              </li>
            </ul>
            <ul v-if="previewResult.warnings.length"
                class="mt-2 space-y-0.5 text-[10px] text-amber-700">
              <li v-for="(w, i) in previewResult.warnings" :key="`w_${i}`">⚠ {{ w }}</li>
            </ul>
          </div>

          <!-- per-table datasource picker -->
          <div v-if="tablesInChain.length" class="space-y-1">
            <p class="text-xs font-semibold text-slate-700">
              为链上各表选 datasource：
            </p>
            <div class="grid grid-cols-2 gap-2 text-[11px]">
              <label v-for="t in tablesInChain" :key="t" class="flex items-center gap-2">
                <span class="sql-font flex-1 truncate" :title="t">{{ t }}</span>
                <select
                  v-model="datasourceMap[t.toLowerCase()]"
                  class="flex-1 rounded border border-slate-300 px-1.5 py-0.5"
                  :class="!datasourceMap[t.toLowerCase()] && 'border-amber-400 bg-amber-50'"
                >
                  <option value="">—未选—</option>
                  <option v-for="ds in datasources" :key="ds.id" :value="ds.id">
                    {{ ds.name }} ({{ ds.db_type }})
                  </option>
                </select>
              </label>
            </div>
            <p v-if="hasUnmapped" class="text-[10px] text-amber-700">
              ⚠ 标黄的表没选 datasource，作业流跑会失败 —— 至少给每张表选一个再保存
            </p>
          </div>

          <!-- SQL preview accordion -->
          <details class="rounded border border-slate-200">
            <summary class="cursor-pointer px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50">
              查看生成的 SQL（{{ previewResult.workflow_draft.nodes.length }} 节点）
            </summary>
            <div class="space-y-2 px-3 py-2 text-[11px]">
              <div v-for="(node, idx) in previewResult.workflow_draft.nodes" :key="node.id"
                   class="rounded border border-slate-100 p-2">
                <p class="mb-1 font-semibold text-slate-700">{{ idx + 1 }}. {{ node.name }}</p>
                <pre class="sql-font whitespace-pre-wrap text-[10px] text-slate-600">source: {{ node.config.source_sql_override }}
target: {{ node.config.target_sql_override }}
keys:   {{ JSON.stringify(node.config.key_columns_override) }}</pre>
              </div>
            </div>
          </details>
        </div>
      </div>

      <!-- footer -->
      <div v-if="previewResult"
           class="flex items-center justify-between border-t border-slate-200 px-5 py-3">
        <button class="btn btn-ghost text-xs" @click="copyDraftJson">复制 draft JSON</button>
        <div class="flex items-center gap-2">
          <button class="btn btn-ghost text-xs" @click="emit('close')">关闭</button>
          <button class="btn btn-primary text-xs flex items-center gap-1"
                  :disabled="saving" @click="saveAsWorkflow">
            <Save class="h-3.5 w-3.5" />
            {{ saving ? '保存中…' : '保存为作业流' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
