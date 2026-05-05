<script setup>
// Phase 10 #4：表资产详情页 MVP + Phase 10 enhancement: custom aspects。
// 路由 /assets/table/:name —— 拿表名反向查找谁在引用：tasks / workflows /
// lineage_scripts / history。aspects（owner / pii / sla / sensitive / tag /
// business_term）editor+ 角色可编辑，schema 由后端 yaml 定义，前端拿
// /api/assets/aspects/types 动态渲染表单字段。
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { ChevronLeft, Database, GitCompareArrows, Workflow, FileCode, History as HistoryIcon, AlertCircle, Tag, Plus, Trash2, Pencil, X, Columns3, ArrowDownToLine, ArrowUpFromLine } from 'lucide-vue-next'
import { apiGet, apiJson } from '../api'
import { useAuthStore } from '../stores/auth'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const { isEditor } = storeToRefs(authStore)

const loading = ref(false)
const error = ref('')
const asset = ref(null)

// aspect schema 定义（来自后端 yaml）—— 决定编辑器渲染哪些字段
const aspectTypes = ref([])  // [{type, label, description, schema, color}, ...]

// 字段列表（来自最近 workflow_run 的 lineage insert_mappings）
const columns = ref([])      // [{name, read_count, write_count, transforms, ...}]
const columnsLoading = ref(false)

// S1.A：变更历史（aspect 改动 timeline）
const history = ref([])
const historyOpen = ref(false)

// S1.B：字段血缘深化 —— 点字段名展开上下游字段链。同一时间只展开一行
const expandedColumn = ref('')             // column name 或 ''
const columnLineageMap = ref({})           // {colName: {upstream, downstream}}
const columnLineageLoading = ref('')       // 当前正在拉的 col name

// S1.C：datasource introspection —— 拉真实字段
const datasources = ref([])                // [{id, name, db_type, ...}]
const introspectDsId = ref('')             // 当前选中的 datasource_id
const introspectColumns = ref([])          // 拉到的真实字段列表
const introspectLoading = ref(false)
const introspectError = ref('')
const introspectMeta = ref(null)           // {datasource_name, db_type, ...}

const tableName = computed(() => route.params.name || '')

// 编辑器状态：null = 关闭；object = 当前正在编辑的 aspect（含 mode = 'add' / 'edit'）
const editing = ref(null)
const saving = ref(false)
const saveError = ref('')

// aspect type → tailwind 颜色映射（example yml 里的 color hint）
const ASPECT_COLOR_MAP = {
  blue: 'bg-blue-100 text-blue-700',
  red: 'bg-red-100 text-red-700',
  amber: 'bg-amber-100 text-amber-700',
  emerald: 'bg-emerald-100 text-emerald-700',
  slate: 'bg-slate-100 text-slate-700',
  purple: 'bg-purple-100 text-purple-700',
}
function aspectColor(type) {
  const spec = aspectTypes.value.find((t) => t.type === type)
  return ASPECT_COLOR_MAP[spec?.color || 'slate'] || ASPECT_COLOR_MAP.slate
}
function aspectLabel(type) {
  return aspectTypes.value.find((t) => t.type === type)?.label || type
}

// value 简短预览（pill 旁的灰字）
function previewValue(aspect) {
  const v = aspect?.value || {}
  // 优先级：先取 enum 类字段（level/tier）、再取 username、再取 list 第一个
  if (v.level) return v.level
  if (v.tier) return v.tier
  if (v.username) return v.username
  if (Array.isArray(v.values) && v.values.length) return v.values.slice(0, 3).join(' / ')
  if (Array.isArray(v.categories) && v.categories.length) return v.categories.slice(0, 3).join(' / ')
  if (v.glossary_key) return v.glossary_key
  if (v.reason) return v.reason.slice(0, 32)
  return ''
}

async function load() {
  if (!tableName.value) return
  loading.value = true
  error.value = ''
  try {
    const [assetData, types] = await Promise.all([
      apiGet(`/api/assets/table/${encodeURIComponent(tableName.value)}`),
      apiGet('/api/assets/aspects/types').catch(() => []),
    ])
    asset.value = assetData
    aspectTypes.value = Array.isArray(types) ? types : []
    // 字段列表 + 变更历史 + datasource 列表并行拉
    loadColumns()
    loadHistory()
    loadDatasourcesList()
  } catch (e) {
    error.value = `加载失败：${e.message || e}`
    asset.value = null
  } finally {
    loading.value = false
  }
}

async function loadColumns() {
  if (!tableName.value) return
  columnsLoading.value = true
  try {
    columns.value = await apiGet(`/api/assets/columns/${encodeURIComponent(tableName.value)}`)
  } catch {
    columns.value = []
  } finally {
    columnsLoading.value = false
  }
}

async function toggleColumnLineage(colName) {
  if (expandedColumn.value === colName) {
    expandedColumn.value = ''
    return
  }
  expandedColumn.value = colName
  // 已经拉过 → 不重复拉
  if (columnLineageMap.value[colName]) return
  columnLineageLoading.value = colName
  try {
    const params = new URLSearchParams({ column: colName })
    const data = await apiGet(
      `/api/assets/column-lineage/${encodeURIComponent(tableName.value)}?${params.toString()}`,
    )
    columnLineageMap.value = {
      ...columnLineageMap.value,
      [colName]: data || { upstream: [], downstream: [] },
    }
  } catch {
    columnLineageMap.value = {
      ...columnLineageMap.value,
      [colName]: { upstream: [], downstream: [] },
    }
  } finally {
    columnLineageLoading.value = ''
  }
}

function gotoColumn(table, column) {
  // 跳到目标表 + 自动展开该字段。reset 旧状态让 watch 触发 load
  router.push(`/assets/table/${encodeURIComponent(table)}`)
  // 子表单展开放在跳转后；这里设个延迟让 load 完毕后再 toggle
  setTimeout(() => { toggleColumnLineage(column) }, 600)
}

async function loadDatasourcesList() {
  try {
    datasources.value = await apiGet('/api/assets/datasources') || []
  } catch {
    datasources.value = []
  }
}

async function runIntrospect() {
  if (!introspectDsId.value || !tableName.value) return
  introspectLoading.value = true
  introspectError.value = ''
  introspectMeta.value = null
  introspectColumns.value = []
  try {
    const params = new URLSearchParams({ datasource_id: introspectDsId.value })
    const data = await apiGet(
      `/api/assets/introspect/${encodeURIComponent(tableName.value)}?${params.toString()}`,
    )
    introspectColumns.value = data.columns || []
    introspectMeta.value = {
      datasource_name: data.datasource_name,
      db_type: data.db_type,
      column_count: data.column_count,
    }
  } catch (e) {
    introspectError.value = e.message || String(e)
  } finally {
    introspectLoading.value = false
  }
}

// merge：lineage cols + introspect cols。lineage 里有的标 active，introspect
// 里独有的标 dormant（"从来没动过"）
const mergedColumns = computed(() => {
  if (!introspectColumns.value.length) return []
  const lineageByName = new Map(columns.value.map((c) => [c.name.toLowerCase(), c]))
  const out = []
  const seen = new Set()
  for (const ic of introspectColumns.value) {
    const lower = ic.name.toLowerCase()
    seen.add(lower)
    const linColl = lineageByName.get(lower)
    out.push({
      name: ic.name,
      data_type: ic.data_type,
      nullable: ic.nullable,
      comment: ic.comment,
      ordinal: ic.ordinal,
      read_count: linColl?.read_count || 0,
      write_count: linColl?.write_count || 0,
      lineage_known: !!linColl,
    })
  }
  // lineage 里有但 introspect 没（可能是已删除字段 / 别的 datasource）
  for (const lc of columns.value) {
    if (!seen.has(lc.name.toLowerCase())) {
      out.push({
        name: lc.name,
        data_type: '',
        nullable: null,
        comment: '',
        ordinal: 9999,
        read_count: lc.read_count,
        write_count: lc.write_count,
        lineage_known: true,
        introspect_missing: true,
      })
    }
  }
  return out
})

async function loadHistory() {
  if (!tableName.value) return
  try {
    const params = new URLSearchParams({
      asset_kind: 'table',
      asset_name: tableName.value,
      limit: '50',
    })
    history.value = await apiGet(`/api/assets/aspects/history?${params.toString()}`) || []
  } catch {
    history.value = []
  }
}

onMounted(load)
watch(() => route.params.name, load)

function gotoTask(taskId) {
  router.push({ path: '/data-compare', query: { task: taskId } })
}
function gotoWorkflow(id) {
  router.push(`/workflows/${id}`)
}
function gotoWorkflowRun(runId) {
  router.push(`/workflow-runs/${runId}`)
}

// ─── aspect 编辑 ────────────────────────────────────────────────────────────

function openAdd() {
  // 默认选第一个 type；用户可在表单里改
  const firstType = aspectTypes.value[0]?.type || ''
  editing.value = { mode: 'add', aspect_type: firstType, value: {} }
  saveError.value = ''
}

function openEdit(aspect) {
  // 复制一份避免改原对象（用户取消时回滚）
  editing.value = {
    mode: 'edit',
    aspect_type: aspect.aspect_type,
    value: JSON.parse(JSON.stringify(aspect.value || {})),
  }
  saveError.value = ''
}

function cancelEdit() {
  editing.value = null
  saveError.value = ''
}

const editingTypeSpec = computed(() =>
  aspectTypes.value.find((t) => t.type === editing.value?.aspect_type) || null
)

function onTypeChange() {
  // 切换 type 时清空 value（不同 type 字段不同，留旧值会很乱）
  if (editing.value) editing.value.value = {}
}

async function save() {
  if (!editing.value || !editingTypeSpec.value) return
  saving.value = true
  saveError.value = ''
  try {
    await apiJson('/api/assets/aspects', 'PUT', {
      asset_kind: 'table',
      asset_name: tableName.value,
      aspect_type: editing.value.aspect_type,
      value: editing.value.value,
      project_id: '',
    })
    editing.value = null
    await load()
  } catch (e) {
    saveError.value = e.message || String(e)
  } finally {
    saving.value = false
  }
}

async function removeAspect(aspect) {
  if (!confirm(`确认删除 aspect「${aspectLabel(aspect.aspect_type)}」？`)) return
  try {
    const params = new URLSearchParams({
      asset_kind: 'table',
      asset_name: tableName.value,
      aspect_type: aspect.aspect_type,
      project_id: aspect.project_id || '',
    })
    await apiJson(`/api/assets/aspects?${params.toString()}`, 'DELETE')
    await load()
  } catch (e) {
    alert(`删除失败：${e.message || e}`)
  }
}

// 编辑器里 list 字段 helper：用 comma 分隔字符串，保存时拆 array
function listValueAsText(field) {
  const v = editing.value?.value?.[field]
  return Array.isArray(v) ? v.join(', ') : (v || '')
}
function setListValue(field, text) {
  if (!editing.value) return
  editing.value.value[field] = text.split(',').map((s) => s.trim()).filter(Boolean)
}
</script>

<template>
  <section class="space-y-4">
    <!-- 顶部 -->
    <div class="flex items-start gap-3">
      <button class="btn btn-ghost h-8 px-2" @click="router.back()">
        <ChevronLeft class="h-4 w-4" />
      </button>
      <div class="flex-1">
        <p class="muted text-[11px] uppercase tracking-wider">{{ $t('pages.assetDetail.kindTable') }}</p>
        <h2 class="sql-font text-2xl font-bold text-slate-800">{{ tableName }}</h2>
        <p v-if="asset" class="muted mt-0.5 text-xs">
          schema: <strong>{{ asset.schema }}</strong> · basename:
          <strong>{{ asset.basename }}</strong>
          <span v-if="asset.stats?.total_references != null" class="ml-2">
            · 共 <strong class="text-primary">{{ asset.stats.total_references }}</strong> 处引用
          </span>
        </p>
      </div>
    </div>

    <!-- Phase 10 #3 v1：从全局 lineage 索引拉来的元数据（role / refresh_mode / 上下游） -->
    <div v-if="asset && (asset.primary_role || asset.refresh_mode || asset.upstream_count || asset.downstream_count)" class="card flex flex-wrap items-center gap-3 p-4">
      <span v-if="asset.primary_role" class="pill bg-blue-100 text-blue-700">
        role: {{ asset.primary_role }}
      </span>
      <span v-if="asset.refresh_mode" class="pill bg-emerald-100 text-emerald-700">
        refresh: {{ asset.refresh_mode }}
        <span v-if="asset.refresh_modes?.length > 1" class="ml-1 text-[10px] opacity-70">
          (+{{ asset.refresh_modes.length - 1 }} 其它)
        </span>
      </span>
      <span class="pill bg-slate-100 text-slate-700">
        上游 <strong>{{ asset.upstream_count }}</strong>
      </span>
      <span class="pill bg-slate-100 text-slate-700">
        下游 <strong>{{ asset.downstream_count }}</strong>
      </span>
      <span v-if="asset.last_seen_at" class="muted ml-auto text-[11px]">
        上次出现 {{ asset.last_seen_at }}
      </span>
    </div>

    <div v-if="loading" class="card p-4 text-sm text-slate-500">加载中…</div>
    <div v-if="error" class="card border-status-error-bg bg-status-error-bg/40 p-3 text-sm text-status-error">
      <AlertCircle class="mr-1 inline h-4 w-4" /> {{ error }}
    </div>

    <!-- Custom aspects（owner / pii / sla / sensitive / tag / business_term）-->
    <article v-if="asset && !loading" class="card p-4">
      <header class="mb-3 flex items-center gap-2">
        <Tag class="h-4 w-4 text-purple-600" />
        <h3 class="text-sm font-bold text-slate-800">{{ $t('pages.assetDetail.cardAspects') }}</h3>
        <span class="pill bg-purple-100 text-purple-700">{{ asset.aspects?.length || 0 }}</span>
        <button
          v-if="history.length"
          class="ml-auto text-[11px] text-slate-500 hover:text-primary"
          @click="historyOpen = !historyOpen"
          :title="historyOpen ? '收起变更历史' : '展开变更历史'"
        >
          {{ historyOpen ? '收起' : '历史' }} ({{ history.length }})
        </button>
        <button
          v-if="isEditor && !editing"
          class="btn btn-outline h-7 px-2 text-xs"
          :class="{ 'ml-auto': !history.length }"
          @click="openAdd"
        >
          <Plus class="h-3.5 w-3.5" /> 添加
        </button>
      </header>

      <!-- 变更历史 timeline -->
      <div v-if="historyOpen && history.length" class="mb-3 rounded-lg border border-slate-200 bg-slate-50/60 p-2 text-xs">
        <ol class="space-y-1.5">
          <li v-for="h in history" :key="h.id" class="flex items-start gap-2">
            <span
              class="mt-0.5 inline-block h-2 w-2 flex-shrink-0 rounded-full"
              :class="{
                'bg-emerald-500': h.action === 'insert',
                'bg-blue-500': h.action === 'update',
                'bg-rose-500': h.action === 'delete',
              }"
            ></span>
            <div class="min-w-0 flex-1">
              <div class="flex flex-wrap items-baseline gap-1.5">
                <strong class="text-slate-700">{{ h.changed_by || '—' }}</strong>
                <span class="muted">{{ h.action }}</span>
                <span :class="['rounded px-1.5 py-0.5 text-[10px]', aspectColor(h.aspect_type)]">
                  {{ aspectLabel(h.aspect_type) }}
                </span>
                <span class="muted ml-auto text-[10px]">{{ h.changed_at }}</span>
              </div>
              <p v-if="h.action === 'update'" class="mt-0.5 muted text-[10px]">
                <span class="line-through opacity-60">{{ JSON.stringify(h.old_value) }}</span>
                →
                <span class="font-medium text-slate-700">{{ JSON.stringify(h.new_value) }}</span>
              </p>
              <p v-else-if="h.action === 'insert'" class="mt-0.5 muted text-[10px]">
                + {{ JSON.stringify(h.new_value) }}
              </p>
              <p v-else class="mt-0.5 muted text-[10px]">
                <span class="line-through opacity-60">{{ JSON.stringify(h.old_value) }}</span>
              </p>
            </div>
          </li>
        </ol>
      </div>

      <ul v-if="asset.aspects?.length" class="flex flex-wrap items-center gap-2">
        <li
          v-for="a in asset.aspects"
          :key="`${a.aspect_type}_${a.project_id}`"
          class="group flex items-center gap-1 rounded-full px-2.5 py-1 text-xs"
          :class="aspectColor(a.aspect_type)"
        >
          <strong>{{ aspectLabel(a.aspect_type) }}</strong>
          <span v-if="previewValue(a)" class="opacity-80">· {{ previewValue(a) }}</span>
          <span v-if="a.project_id" class="ml-1 rounded bg-white/40 px-1 text-[10px]">
            project:{{ a.project_id }}
          </span>
          <template v-if="isEditor">
            <button
              class="ml-1 hidden rounded p-0.5 hover:bg-white/40 group-hover:inline-block"
              :title="`编辑 ${aspectLabel(a.aspect_type)}`"
              @click="openEdit(a)"
            >
              <Pencil class="h-3 w-3" />
            </button>
            <button
              class="hidden rounded p-0.5 hover:bg-white/40 group-hover:inline-block"
              :title="`删除 ${aspectLabel(a.aspect_type)}`"
              @click="removeAspect(a)"
            >
              <Trash2 class="h-3 w-3" />
            </button>
          </template>
        </li>
      </ul>
      <p v-else class="muted text-xs">
        尚未标注分类。
        <span v-if="isEditor">点击右上"添加"挂上 owner / PII / SLA / 业务标签等。</span>
        <span v-else>需要 editor+ 角色才能编辑。</span>
      </p>

      <!-- 编辑器 inline 表单 -->
      <div v-if="editing" class="mt-3 rounded-lg border border-purple-200 bg-purple-50/40 p-3 text-xs">
        <div class="mb-2 flex items-center gap-2">
          <strong class="text-slate-700">
            {{ editing.mode === 'add' ? '新增' : '编辑' }} aspect
          </strong>
          <button class="btn btn-ghost ml-auto h-6 px-1.5" @click="cancelEdit">
            <X class="h-3.5 w-3.5" />
          </button>
        </div>

        <div class="space-y-2">
          <label class="block">
            <span class="muted text-[11px]">类型</span>
            <select
              v-model="editing.aspect_type"
              class="mt-0.5 w-full"
              :disabled="editing.mode === 'edit'"
              @change="onTypeChange"
            >
              <option v-for="t in aspectTypes" :key="t.type" :value="t.type">
                {{ t.label }}（{{ t.type }}）
              </option>
            </select>
            <p v-if="editingTypeSpec?.description" class="muted mt-0.5 text-[10px]">
              {{ editingTypeSpec.description }}
            </p>
          </label>

          <!-- 动态字段：根据 schema 渲染 string / list / enum -->
          <template v-if="editingTypeSpec">
            <div
              v-for="(spec, fieldName) in editingTypeSpec.schema"
              :key="fieldName"
            >
              <label class="block">
                <span class="muted text-[11px]">
                  {{ fieldName }}
                  <span v-if="spec.required" class="text-status-error">*</span>
                  <span class="ml-1 text-[10px] uppercase opacity-60">{{ spec.type }}</span>
                </span>
                <select
                  v-if="spec.type === 'enum'"
                  v-model="editing.value[fieldName]"
                  class="mt-0.5 w-full"
                >
                  <option value="">—</option>
                  <option v-for="opt in (spec.values || [])" :key="opt" :value="opt">{{ opt }}</option>
                </select>
                <input
                  v-else-if="spec.type === 'list'"
                  type="text"
                  class="mt-0.5 w-full"
                  placeholder="逗号分隔，如 a, b, c"
                  :value="listValueAsText(fieldName)"
                  @input="(e) => setListValue(fieldName, e.target.value)"
                />
                <input
                  v-else
                  type="text"
                  class="mt-0.5 w-full"
                  v-model="editing.value[fieldName]"
                />
              </label>
            </div>
          </template>

          <p v-if="saveError" class="text-status-error">{{ saveError }}</p>

          <div class="flex items-center justify-end gap-2 pt-1">
            <button class="btn btn-outline h-7 px-3 text-xs" :disabled="saving" @click="cancelEdit">
              取消
            </button>
            <button class="btn btn-primary h-7 px-3 text-xs" :disabled="saving" @click="save">
              {{ saving ? '保存中…' : '保存' }}
            </button>
          </div>
        </div>
      </div>
    </article>

    <div v-if="asset && !loading" class="grid gap-4 md:grid-cols-2">
      <!-- 任务引用 -->
      <article class="card p-4">
        <header class="mb-2 flex items-center gap-2">
          <GitCompareArrows class="h-4 w-4 text-blue-600" />
          <h3 class="text-sm font-bold text-slate-800">{{ $t('pages.assetDetail.cardTasks') }}</h3>
          <span class="pill bg-blue-100 text-blue-700">{{ asset.references.tasks.length }}</span>
        </header>
        <ul v-if="asset.references.tasks.length" class="space-y-1.5">
          <li v-for="t in asset.references.tasks" :key="t.id">
            <button
              class="w-full rounded p-2 text-left text-sm hover:bg-slate-50"
              @click="gotoTask(t.id)"
            >
              <span class="font-medium">{{ t.name }}</span>
              <span class="muted ml-2 text-[11px]">{{ t.match_role }}</span>
            </button>
          </li>
        </ul>
        <p v-else class="muted text-xs">没有任务引用此表。</p>
      </article>

      <!-- 作业流引用 -->
      <article class="card p-4">
        <header class="mb-2 flex items-center gap-2">
          <Workflow class="h-4 w-4 text-purple-600" />
          <h3 class="text-sm font-bold text-slate-800">{{ $t('pages.assetDetail.cardWorkflows') }}</h3>
          <span class="pill bg-purple-100 text-purple-700">{{ asset.references.workflows.length }}</span>
        </header>
        <ul v-if="asset.references.workflows.length" class="space-y-1.5">
          <li v-for="w in asset.references.workflows" :key="w.id">
            <button
              class="w-full rounded p-2 text-left text-sm hover:bg-slate-50"
              @click="gotoWorkflow(w.id)"
            >
              <span class="font-medium">{{ w.name }}</span>
              <span class="muted ml-2 text-[11px]">{{ w.node_count }} 节点</span>
            </button>
          </li>
        </ul>
        <p v-else class="muted text-xs">没有作业流引用此表。</p>
      </article>

      <!-- 血缘脚本（来自 workflow run） -->
      <article class="card p-4">
        <header class="mb-2 flex items-center gap-2">
          <FileCode class="h-4 w-4 text-emerald-600" />
          <h3 class="text-sm font-bold text-slate-800">{{ $t('pages.assetDetail.cardLineageScripts') }}</h3>
          <span class="pill bg-emerald-100 text-emerald-700">{{ asset.references.lineage_scripts.length }}</span>
        </header>
        <ul v-if="asset.references.lineage_scripts.length" class="space-y-1.5">
          <li v-for="(s, i) in asset.references.lineage_scripts" :key="i">
            <button
              class="w-full rounded p-2 text-left text-sm hover:bg-slate-50"
              @click="gotoWorkflowRun(s.run_id)"
            >
              <span class="sql-font font-medium">{{ s.file_name }}</span>
              <span class="muted ml-2 text-[11px]">{{ s.match_role }} · run {{ s.run_id?.slice(0, 8) }}</span>
            </button>
          </li>
        </ul>
        <p v-else class="muted text-xs">最近的 workflow run 中没有血缘脚本引用此表。</p>
      </article>

      <!-- 历史 -->
      <article class="card p-4">
        <header class="mb-2 flex items-center gap-2">
          <HistoryIcon class="h-4 w-4 text-amber-600" />
          <h3 class="text-sm font-bold text-slate-800">{{ $t('pages.assetDetail.cardHistory') }}</h3>
          <span class="pill bg-amber-100 text-amber-700">{{ asset.references.history.length }}</span>
        </header>
        <ul v-if="asset.references.history.length" class="space-y-1.5">
          <li v-for="h in asset.references.history" :key="h.id" class="rounded p-2 text-sm">
            <span class="font-medium">{{ h.task_name }}</span>
            <span class="muted ml-2 text-[11px]">{{ h.started_at }} · {{ h.status }}</span>
          </li>
        </ul>
        <p v-else class="muted text-xs">没有相关执行历史。</p>
      </article>
    </div>

    <!-- 字段列表（来自最近 workflow_run 的 lineage insert_mappings）-->
    <article v-if="asset && !loading" class="card p-4">
      <header class="mb-3 flex flex-wrap items-center gap-2">
        <Columns3 class="h-4 w-4 text-emerald-600" />
        <h3 class="text-sm font-bold text-slate-800">{{ $t('pages.assetDetail.cardColumns') }}</h3>
        <span class="pill bg-emerald-100 text-emerald-700">
          {{ introspectMeta ? mergedColumns.length : columns.length }}
        </span>
        <span v-if="columnsLoading || introspectLoading" class="muted text-[11px]">加载中…</span>
        <span v-else-if="introspectMeta" class="muted text-[11px]">
          来源 <strong>{{ introspectMeta.datasource_name }}</strong>
          ({{ introspectMeta.db_type }}) + lineage merge
        </span>
        <span v-else-if="columns.length" class="muted text-[11px]">
          仅 lineage 反查（按热度倒序）。可拉真实 schema →
        </span>

        <!-- introspect 控制台 -->
        <div class="ml-auto flex items-center gap-1.5">
          <select
            v-model="introspectDsId"
            class="h-7 text-xs"
            :disabled="!datasources.length"
            title="选 datasource 拉真实字段"
          >
            <option value="">— 选数据源 —</option>
            <option v-for="ds in datasources" :key="ds.id" :value="ds.id">
              {{ ds.name }} ({{ ds.db_type }})
            </option>
          </select>
          <button
            class="btn btn-outline h-7 px-2 text-xs"
            :disabled="!introspectDsId || introspectLoading"
            @click="runIntrospect"
            title="从 information_schema / all_tab_columns 拉真实字段"
          >
            <Database class="h-3.5 w-3.5" />
            拉真实
          </button>
        </div>
      </header>
      <p
        v-if="introspectError"
        class="muted mb-2 rounded bg-status-error-bg/40 p-2 text-[11px] text-status-error"
      >
        introspect 失败：{{ introspectError }}
      </p>
      <div v-if="(introspectMeta ? mergedColumns : columns).length" class="overflow-x-auto">
        <table class="w-full text-xs">
          <thead class="text-slate-500">
            <tr class="border-b border-slate-100">
              <th class="py-1.5 pr-3 text-left font-semibold">字段名</th>
              <th v-if="introspectMeta" class="py-1.5 pr-3 text-left font-semibold">类型</th>
              <th class="py-1.5 pr-3 text-right font-semibold">
                <ArrowUpFromLine class="inline h-3 w-3 text-slate-400" /> 写
              </th>
              <th class="py-1.5 pr-3 text-right font-semibold">
                <ArrowDownToLine class="inline h-3 w-3 text-slate-400" /> 读
              </th>
              <th class="py-1.5 pr-3 text-left font-semibold">变换 transform</th>
              <th class="py-1.5 text-left font-semibold">最近出现</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="col in (introspectMeta ? mergedColumns : columns)" :key="col.name">
              <tr
                class="border-b border-slate-50 hover:bg-slate-50/60"
                :class="col.lineage_known === false && 'opacity-60'"
                :title="col.lineage_known === false ? '此字段在 lineage 里没出现过（dormant）' : ''"
              >
                <td class="py-1.5 pr-3">
                  <button
                    class="sql-font font-medium text-slate-800 hover:text-primary hover:underline"
                    @click="toggleColumnLineage(col.name)"
                    :title="expandedColumn === col.name ? '收起字段血缘' : '展开字段血缘'"
                  >
                    {{ expandedColumn === col.name ? '▾' : '▸' }} {{ col.name }}
                  </button>
                  <span
                    v-if="col.introspect_missing"
                    class="ml-1 rounded bg-rose-100 px-1 text-[9px] text-rose-700"
                    title="lineage 里有，introspect 拉不到 —— 字段可能已删除"
                  >已删除?</span>
                  <span
                    v-else-if="col.lineage_known === false"
                    class="ml-1 rounded bg-slate-100 px-1 text-[9px] text-slate-500"
                    title="表里有但 lineage 从来没动过"
                  >dormant</span>
                </td>
                <td v-if="introspectMeta" class="sql-font py-1.5 pr-3 text-[11px] text-slate-600">
                  {{ col.data_type || '—' }}
                  <span v-if="col.nullable === false" class="ml-1 text-[9px] text-rose-600">NN</span>
                </td>
                <td class="py-1.5 pr-3 text-right">
                  <span v-if="col.write_count" class="rounded bg-amber-100 px-1.5 py-0.5 font-semibold text-amber-700">
                    {{ col.write_count }}
                  </span>
                  <span v-else class="muted">—</span>
                </td>
                <td class="py-1.5 pr-3 text-right">
                  <span v-if="col.read_count" class="rounded bg-blue-100 px-1.5 py-0.5 font-semibold text-blue-700">
                    {{ col.read_count }}
                  </span>
                  <span v-else class="muted">—</span>
                </td>
                <td class="py-1.5 pr-3">
                  <span v-if="col.transforms?.length" class="muted">
                    {{ col.transforms.slice(0, 3).join(' / ') }}
                    <span v-if="col.transforms.length > 3" class="opacity-60">
                      +{{ col.transforms.length - 3 }}
                    </span>
                  </span>
                  <span v-else class="muted">—</span>
                </td>
                <td class="muted py-1.5 text-[10px]">
                  <button
                    v-if="col.last_seen_run_id"
                    class="text-primary hover:underline"
                    @click="gotoWorkflowRun(col.last_seen_run_id)"
                  >
                    run {{ col.last_seen_run_id.slice(0, 8) }}
                  </button>
                  <span v-else>—</span>
                </td>
              </tr>
              <!-- 展开行：上下游字段链 -->
              <tr v-if="expandedColumn === col.name" class="bg-slate-50/40">
                <td :colspan="introspectMeta ? 6 : 5" class="px-2 pb-2">
                  <div v-if="columnLineageLoading === col.name" class="muted text-[11px]">
                    查询字段血缘…
                  </div>
                  <div v-else class="flex flex-col gap-2 md:flex-row md:items-start">
                    <div class="flex-1">
                      <p class="muted mb-1 text-[10px] uppercase tracking-wider">
                        ← 上游字段（{{ columnLineageMap[col.name]?.upstream?.length || 0 }}）
                      </p>
                      <div v-if="columnLineageMap[col.name]?.upstream?.length"
                           class="flex flex-wrap gap-1">
                        <button
                          v-for="u in columnLineageMap[col.name].upstream"
                          :key="`up_${u.table}_${u.column}`"
                          class="rounded bg-blue-50 px-1.5 py-0.5 text-[11px] text-blue-700 hover:bg-blue-100"
                          @click="gotoColumn(u.table, u.column)"
                          :title="`跳到 ${u.table}.${u.column}（${u.count} 次）`"
                        >
                          <span class="sql-font">{{ u.table }}.{{ u.column }}</span>
                          <span class="ml-1 opacity-60">×{{ u.count }}</span>
                        </button>
                      </div>
                      <p v-else class="muted text-[11px]">没有上游字段。</p>
                    </div>
                    <div class="px-2 text-slate-300">→</div>
                    <div class="flex-1">
                      <p class="muted mb-1 text-[10px] uppercase tracking-wider">
                        下游字段 → （{{ columnLineageMap[col.name]?.downstream?.length || 0 }}）
                      </p>
                      <div v-if="columnLineageMap[col.name]?.downstream?.length"
                           class="flex flex-wrap gap-1">
                        <button
                          v-for="d in columnLineageMap[col.name].downstream"
                          :key="`dn_${d.table}_${d.column}`"
                          class="rounded bg-emerald-50 px-1.5 py-0.5 text-[11px] text-emerald-700 hover:bg-emerald-100"
                          @click="gotoColumn(d.table, d.column)"
                          :title="`跳到 ${d.table}.${d.column}（${d.count} 次）`"
                        >
                          <span class="sql-font">{{ d.table }}.{{ d.column }}</span>
                          <span class="ml-1 opacity-60">×{{ d.count }}</span>
                        </button>
                      </div>
                      <p v-else class="muted text-[11px]">没有下游字段。</p>
                    </div>
                  </div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
      <p v-else-if="!columnsLoading" class="muted text-xs">
        最近 50 个 workflow_run 的 lineage 输出里没找到此表的字段引用。
        跑一个含 INSERT/UPDATE/MERGE 此表的血缘任务后再回来看。
      </p>
    </article>

    <!-- 下一步留白卡 -->
    <div v-if="asset && !loading" class="card border-slate-200 bg-slate-50 p-3 text-xs text-slate-500">
      <strong>当前覆盖</strong>：反向引用（4 类）+ 全局索引元数据（role / refresh_mode /
      上下游计数 / 最近出现 run）+ classification aspects（owner / pii / sla /
      sensitive / tag / business_term）+ 字段列表（lineage 反查热度）。
      <span class="opacity-70">下一步：字段血缘热点（点字段名展开上下游字段链）+
      datasource introspection（拉真实 information_schema 列表，含没在 lineage
      里出现过的字段）。</span>
    </div>
  </section>
</template>
