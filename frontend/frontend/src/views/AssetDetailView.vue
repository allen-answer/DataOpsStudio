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
    // 字段列表并行拉（独立 endpoint，慢一点不阻塞主面板）
    loadColumns()
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
        <p class="muted text-[11px] uppercase tracking-wider">表资产</p>
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
        <h3 class="text-sm font-bold text-slate-800">分类与所属</h3>
        <span class="pill bg-purple-100 text-purple-700">{{ asset.aspects?.length || 0 }}</span>
        <button
          v-if="isEditor && !editing"
          class="btn btn-outline ml-auto h-7 px-2 text-xs"
          @click="openAdd"
        >
          <Plus class="h-3.5 w-3.5" /> 添加
        </button>
      </header>

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
          <h3 class="text-sm font-bold text-slate-800">对比任务</h3>
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
          <h3 class="text-sm font-bold text-slate-800">作业流</h3>
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
          <h3 class="text-sm font-bold text-slate-800">血缘脚本</h3>
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
          <h3 class="text-sm font-bold text-slate-800">执行历史</h3>
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
      <header class="mb-3 flex items-center gap-2">
        <Columns3 class="h-4 w-4 text-emerald-600" />
        <h3 class="text-sm font-bold text-slate-800">字段（lineage 反查）</h3>
        <span class="pill bg-emerald-100 text-emerald-700">{{ columns.length }}</span>
        <span v-if="columnsLoading" class="muted text-[11px]">加载中…</span>
        <span v-else-if="columns.length" class="muted text-[11px]">
          按热度排序（写次数 + 读次数）
        </span>
      </header>
      <div v-if="columns.length" class="overflow-x-auto">
        <table class="w-full text-xs">
          <thead class="text-slate-500">
            <tr class="border-b border-slate-100">
              <th class="py-1.5 pr-3 text-left font-semibold">字段名</th>
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
            <tr
              v-for="col in columns"
              :key="col.name"
              class="border-b border-slate-50 hover:bg-slate-50/60"
            >
              <td class="sql-font py-1.5 pr-3 font-medium text-slate-800">{{ col.name }}</td>
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
