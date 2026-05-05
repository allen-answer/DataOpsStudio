<script setup>
// Phase 10 enhancement #2：Aspect 反查 / classification governance dashboard。
// admin 用 —— 选 aspect_type 看哪些资产标了它，再按 value 子字段过滤
// （pii.level=high / sla.tier=t0 / owner.username=alice）。
//
// 数据走 GET /api/assets/aspects/search?aspect_type=pii&asset_kind=table —— 已就绪
// 前端不做新 API。点资产卡跳 /assets/table/<name> 详情页。
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Tag, RefreshCw, Filter, AlertCircle, Search, History as HistoryIcon } from 'lucide-vue-next'
import { apiGet } from '../../api'
import { useNoticeStore } from '../../stores/notice'
import { useProjectStore } from '../../stores/project'

const router = useRouter()
const noticeStore = useNoticeStore()
const projectStore = useProjectStore()

// tab 切换：search（按 type 反查资产）/ history（变更日志）
const activeTab = ref('search')

const types = ref([])             // [{type,label,description,schema,color}, ...]
const selectedType = ref('')
const assetKind = ref('table')    // 目前后端只 owner table，但留扩展位
const valueFilter = ref({})        // 按 value 子字段过滤；ref 是 plain dict
const limit = ref(200)
const loading = ref(false)
const error = ref('')
const records = ref([])           // /api/assets/aspects/search 返回

// S1.A：变更日志 tab 状态
const historyFilter = ref({ aspect_type: '', changed_by: '' })
const historyRecords = ref([])
const historyLoading = ref(false)

// aspect type → tailwind 颜色（跟 AssetDetailView 同一份映射）
const ASPECT_COLOR_MAP = {
  blue: 'bg-blue-100 text-blue-700',
  red: 'bg-red-100 text-red-700',
  amber: 'bg-amber-100 text-amber-700',
  emerald: 'bg-emerald-100 text-emerald-700',
  slate: 'bg-slate-100 text-slate-700',
  purple: 'bg-purple-100 text-purple-700',
}

const selectedSpec = computed(() =>
  types.value.find((t) => t.type === selectedType.value) || null
)

function colorFor(type) {
  const spec = types.value.find((t) => t.type === type)
  return ASPECT_COLOR_MAP[spec?.color || 'slate'] || ASPECT_COLOR_MAP.slate
}

// 客户端二级过滤：用 valueFilter 子字段筛 records
const filteredRecords = computed(() => {
  if (!records.value.length) return []
  const filters = Object.entries(valueFilter.value).filter(([, v]) => v !== '' && v != null)
  if (!filters.length) return records.value
  return records.value.filter((rec) => {
    const v = rec.value || {}
    return filters.every(([key, want]) => {
      const got = v[key]
      if (Array.isArray(got)) {
        // list 字段 → contains 匹配
        return got.some((x) => String(x).toLowerCase().includes(String(want).toLowerCase()))
      }
      return String(got || '').toLowerCase().includes(String(want).toLowerCase())
    })
  })
})

async function loadTypes() {
  try {
    const data = await apiGet('/api/assets/aspects/types')
    types.value = Array.isArray(data) ? data : []
    // 默认选 pii（最有意义的 governance 入口）
    if (!selectedType.value && types.value.length) {
      const pii = types.value.find((t) => t.type === 'pii')
      selectedType.value = pii ? pii.type : types.value[0].type
    }
  } catch (e) {
    error.value = `加载 aspect types 失败：${e.message || e}`
  }
}

async function reload() {
  if (!selectedType.value) {
    records.value = []
    return
  }
  loading.value = true
  error.value = ''
  try {
    const params = new URLSearchParams({
      aspect_type: selectedType.value,
      asset_kind: assetKind.value,
      limit: String(limit.value),
    })
    if (projectStore.currentProjectId) params.set('project_id', projectStore.currentProjectId)
    records.value = await apiGet(`/api/assets/aspects/search?${params.toString()}`)
  } catch (e) {
    error.value = `加载失败：${e.message || e}`
    noticeStore.setNotice(`Aspect 反查失败：${e.message || e}`)
    records.value = []
  } finally {
    loading.value = false
  }
}

function onTypeChange() {
  // 切 type 重置子字段过滤（不同 type 字段不同，留旧值会无意义）
  valueFilter.value = {}
  reload()
}

function gotoAsset(name) {
  router.push(`/assets/table/${encodeURIComponent(name)}`)
}

// 简短 value 预览（pill 旁的灰字）—— 跟 AssetDetailView 同一逻辑简化版
function previewValue(v) {
  if (!v) return ''
  if (v.level) return `level=${v.level}`
  if (v.tier) return `tier=${v.tier}`
  if (v.username) return v.username + (v.team ? ` (${v.team})` : '')
  if (Array.isArray(v.values) && v.values.length) return v.values.slice(0, 4).join(', ')
  if (Array.isArray(v.categories) && v.categories.length) return v.categories.slice(0, 4).join(', ')
  if (v.glossary_key) return v.glossary_key
  if (v.reason) return v.reason.slice(0, 40)
  return JSON.stringify(v).slice(0, 60)
}

async function loadHistory() {
  historyLoading.value = true
  try {
    const params = new URLSearchParams({ limit: '200' })
    if (historyFilter.value.aspect_type) params.set('aspect_type', historyFilter.value.aspect_type)
    if (historyFilter.value.changed_by) params.set('changed_by', historyFilter.value.changed_by)
    historyRecords.value = await apiGet(`/api/assets/aspects/history?${params.toString()}`) || []
  } catch (e) {
    noticeStore.setNotice(`变更日志加载失败：${e.message || e}`)
    historyRecords.value = []
  } finally {
    historyLoading.value = false
  }
}

watch(activeTab, (val) => {
  if (val === 'history' && !historyRecords.value.length) loadHistory()
})

function onAssetClick(rec) {
  if (rec.asset_kind === 'table') gotoAsset(rec.asset_name)
}

onMounted(async () => {
  await loadTypes()
  if (selectedType.value) await reload()
})
watch(() => projectStore.currentProjectId, reload)
</script>

<template>
  <section class="space-y-6">
    <header class="flex items-end justify-between">
      <div>
        <h2 class="text-2xl font-bold text-slate-800">{{ $t('pages.governance.title') }}</h2>
        <p class="mt-1 text-sm text-slate-500">
          {{ $t('pages.governance.subtitle') }}
        </p>
      </div>
      <button
        class="btn btn-outline gap-1.5"
        :disabled="activeTab === 'search' ? loading : historyLoading"
        @click="activeTab === 'search' ? reload() : loadHistory()"
      >
        <RefreshCw class="h-4 w-4" :class="(activeTab === 'search' ? loading : historyLoading) && 'animate-spin'" />
        {{ $t('common.refresh') }}
      </button>
    </header>

    <!-- Tab 切换 -->
    <div class="flex border-b border-slate-200">
      <button
        class="px-4 py-2 text-sm font-medium"
        :class="activeTab === 'search'
          ? 'border-b-2 border-primary text-primary'
          : 'text-slate-500 hover:text-slate-700'"
        @click="activeTab = 'search'"
      >
        <Search class="mr-1 inline h-3.5 w-3.5" /> {{ $t('pages.governance.tabSearch') }}
      </button>
      <button
        class="px-4 py-2 text-sm font-medium"
        :class="activeTab === 'history'
          ? 'border-b-2 border-primary text-primary'
          : 'text-slate-500 hover:text-slate-700'"
        @click="activeTab = 'history'"
      >
        <HistoryIcon class="mr-1 inline h-3.5 w-3.5" /> {{ $t('pages.governance.tabHistory') }}
      </button>
    </div>

    <!-- ─── Tab 1：反查资产 ─── -->
    <template v-if="activeTab === 'search'">

    <!-- 顶部过滤栏 -->
    <article class="card p-4">
      <div class="flex flex-wrap items-end gap-3">
        <label class="flex flex-col text-xs text-slate-600">
          Aspect 类型
          <select
            v-model="selectedType"
            class="mt-1 min-w-[180px]"
            :disabled="!types.length"
            @change="onTypeChange"
          >
            <option v-for="t in types" :key="t.type" :value="t.type">
              {{ t.label }}（{{ t.type }}）
            </option>
          </select>
        </label>

        <label class="flex flex-col text-xs text-slate-600">
          资产种类
          <select v-model="assetKind" class="mt-1 w-32" @change="reload">
            <option value="table">table</option>
            <option value="task">task</option>
            <option value="field">field</option>
          </select>
        </label>

        <label class="flex flex-col text-xs text-slate-600">
          条数上限
          <select v-model.number="limit" class="mt-1 w-24" @change="reload">
            <option :value="50">50</option>
            <option :value="200">200</option>
            <option :value="500">500</option>
          </select>
        </label>

        <!-- 动态 value 子字段过滤（按选中 type 的 schema 渲染）-->
        <template v-if="selectedSpec?.schema">
          <label
            v-for="(spec, fieldName) in selectedSpec.schema"
            :key="fieldName"
            class="flex flex-col text-xs text-slate-600"
          >
            {{ fieldName }}
            <span class="text-[10px] uppercase opacity-50">{{ spec.type }}</span>
            <select
              v-if="spec.type === 'enum'"
              v-model="valueFilter[fieldName]"
              class="mt-1 min-w-[120px]"
            >
              <option value="">— 任意 —</option>
              <option v-for="opt in (spec.values || [])" :key="opt" :value="opt">{{ opt }}</option>
            </select>
            <input
              v-else
              type="text"
              class="mt-1 min-w-[120px]"
              :placeholder="spec.type === 'list' ? '包含...' : '匹配...'"
              v-model="valueFilter[fieldName]"
            />
          </label>
        </template>

        <p v-if="selectedSpec?.description" class="muted ml-auto self-center text-[11px]">
          <Filter class="inline h-3 w-3" />
          {{ selectedSpec.description }}
        </p>
      </div>
    </article>

    <!-- 错误 / 加载 -->
    <div v-if="error" class="card border-status-error-bg bg-status-error-bg/40 p-3 text-sm text-status-error">
      <AlertCircle class="mr-1 inline h-4 w-4" /> {{ error }}
    </div>
    <div v-if="loading" class="card p-4 text-sm text-slate-500">加载中…</div>

    <!-- 结果 -->
    <article v-if="!loading && !error" class="card p-4">
      <header class="mb-3 flex items-center gap-2 border-b border-slate-100 pb-2">
        <Tag class="h-4 w-4 text-purple-600" />
        <h3 class="text-sm font-bold text-slate-800">命中资产</h3>
        <span class="pill bg-purple-100 text-purple-700">{{ filteredRecords.length }}</span>
        <span v-if="filteredRecords.length !== records.length" class="muted text-[11px]">
          / 服务端返回 {{ records.length }}
        </span>
        <span class="muted ml-auto text-[11px]">
          点资产卡跳详情页
        </span>
      </header>

      <ul v-if="filteredRecords.length" class="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
        <li
          v-for="rec in filteredRecords"
          :key="`${rec.asset_kind}_${rec.asset_name}_${rec.project_id}`"
          class="group cursor-pointer rounded-lg border border-slate-200 bg-white p-3 hover:border-purple-300 hover:shadow-sm"
          @click="gotoAsset(rec.asset_name)"
        >
          <div class="flex items-start justify-between gap-2">
            <div class="min-w-0 flex-1">
              <p class="muted text-[10px] uppercase tracking-wider">{{ rec.asset_kind }}</p>
              <p class="sql-font truncate text-sm font-bold text-slate-800 group-hover:text-purple-700">
                {{ rec.asset_name }}
              </p>
            </div>
            <Search class="h-4 w-4 text-slate-300 group-hover:text-purple-500" />
          </div>
          <div class="mt-2 flex flex-wrap items-center gap-1">
            <span :class="['rounded-full px-2 py-0.5 text-[11px]', colorFor(rec.aspect_type)]">
              {{ previewValue(rec.value) || rec.aspect_type }}
            </span>
            <span v-if="rec.project_id" class="muted text-[10px]">
              project: {{ rec.project_id }}
            </span>
          </div>
          <p class="muted mt-1 text-[10px]">
            by {{ rec.updated_by || '—' }} · {{ rec.updated_at || '—' }}
          </p>
        </li>
      </ul>
      <p v-else class="muted text-sm">
        没有命中资产。
        <span v-if="records.length"> 调整子字段过滤试试。</span>
        <span v-else>该 aspect 还没人打过；去任意表详情页加上一条。</span>
      </p>
    </article>

    <!-- 帮助卡 -->
    <article class="card border-slate-200 bg-slate-50 p-3 text-xs text-slate-500">
      <strong>用法</strong>：选 <code>pii</code> + level=high → 看所有标了高敏感的表；
      选 <code>owner</code> + 输入 username → 看某人负责的资产；
      选 <code>sla</code> + tier=t0 → 看核心 SLA 表。点资产卡跳详情页看反向引用 / 元数据。
      新加 aspect type 改 <code>config/asset_aspects.yml</code>。
    </article>

    </template><!-- /search tab -->

    <!-- ─── Tab 2：变更日志 ─── -->
    <template v-if="activeTab === 'history'">
      <article class="card p-4">
        <div class="flex flex-wrap items-end gap-3 border-b border-slate-100 pb-3">
          <label class="flex flex-col text-xs text-slate-600">
            Aspect 类型
            <select
              v-model="historyFilter.aspect_type"
              class="mt-1 min-w-[160px]"
              @change="loadHistory"
            >
              <option value="">— 全部 —</option>
              <option v-for="t in types" :key="t.type" :value="t.type">
                {{ t.label }}（{{ t.type }}）
              </option>
            </select>
          </label>
          <label class="flex flex-col text-xs text-slate-600">
            变更人 username
            <input
              type="text"
              class="mt-1 min-w-[160px]"
              placeholder="精确匹配 username"
              v-model="historyFilter.changed_by"
              @keyup.enter="loadHistory"
              @blur="loadHistory"
            />
          </label>
          <p class="muted ml-auto self-center text-[11px]">
            最近 200 条；append-only；时间倒序
          </p>
        </div>

        <div v-if="historyLoading" class="muted py-4 text-sm">加载中…</div>
        <ol v-else-if="historyRecords.length" class="mt-3 space-y-2">
          <li
            v-for="h in historyRecords"
            :key="h.id"
            class="flex items-start gap-3 rounded-lg border border-slate-100 p-2 hover:bg-slate-50/60"
          >
            <span
              class="mt-1 h-2.5 w-2.5 flex-shrink-0 rounded-full"
              :class="{
                'bg-emerald-500': h.action === 'insert',
                'bg-blue-500': h.action === 'update',
                'bg-rose-500': h.action === 'delete',
              }"
              :title="h.action"
            ></span>
            <div class="min-w-0 flex-1 text-xs">
              <div class="flex flex-wrap items-baseline gap-1.5">
                <strong class="text-slate-800">{{ h.changed_by || '—' }}</strong>
                <span class="muted">{{ h.action }}</span>
                <span :class="['rounded px-1.5 py-0.5 text-[10px]', colorFor(h.aspect_type)]">
                  {{ h.aspect_type }}
                </span>
                <button
                  class="sql-font text-[11px] text-primary hover:underline"
                  @click="onAssetClick(h)"
                  :title="h.asset_kind === 'table' ? '跳详情页' : '当前 MVP 仅 table 跳转'"
                >
                  {{ h.asset_name }}
                </button>
                <span v-if="h.project_id" class="muted text-[10px]">project: {{ h.project_id }}</span>
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
        <p v-else class="muted py-4 text-sm">
          没有变更记录。<span v-if="historyFilter.aspect_type || historyFilter.changed_by">放宽过滤试试。</span>
        </p>
      </article>

      <article class="card border-slate-200 bg-slate-50 p-3 text-xs text-slate-500">
        <strong>合规审计</strong>：所有 aspect 的 insert / update / delete 操作都落 SQLite
        <code>asset_aspect_history</code>，append-only。回答"谁把 PII 等级从 high 改成 low 了"
        类问题。no-op update（value 没变）不污染日志。
      </article>
    </template><!-- /history tab -->

  </section>
</template>
