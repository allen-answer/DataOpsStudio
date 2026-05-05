<script setup>
// Phase 10 enhancement #2：Aspect 反查 / classification governance dashboard。
// admin 用 —— 选 aspect_type 看哪些资产标了它，再按 value 子字段过滤
// （pii.level=high / sla.tier=t0 / owner.username=alice）。
//
// 数据走 GET /api/assets/aspects/search?aspect_type=pii&asset_kind=table —— 已就绪
// 前端不做新 API。点资产卡跳 /assets/table/<name> 详情页。
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Tag, RefreshCw, Filter, AlertCircle, Search } from 'lucide-vue-next'
import { apiGet } from '../../api'
import { useNoticeStore } from '../../stores/notice'
import { useProjectStore } from '../../stores/project'

const router = useRouter()
const noticeStore = useNoticeStore()
const projectStore = useProjectStore()

const types = ref([])             // [{type,label,description,schema,color}, ...]
const selectedType = ref('')
const assetKind = ref('table')    // 目前后端只 owner table，但留扩展位
const valueFilter = ref({})        // 按 value 子字段过滤；ref 是 plain dict
const limit = ref(200)
const loading = ref(false)
const error = ref('')
const records = ref([])           // /api/assets/aspects/search 返回

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
        <h2 class="text-2xl font-bold text-slate-800">分类治理（Aspect Governance）</h2>
        <p class="mt-1 text-sm text-slate-500">
          按 aspect 反查资产 —— 哪些表标了 PII？谁是 owner？哪些表是 t0 SLA？
          数据来源：所有 editor+ 在表详情页打的 aspect 标签。
        </p>
      </div>
      <button class="btn btn-outline gap-1.5" :disabled="loading" @click="reload">
        <RefreshCw class="h-4 w-4" :class="loading && 'animate-spin'" />
        刷新
      </button>
    </header>

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
  </section>
</template>
