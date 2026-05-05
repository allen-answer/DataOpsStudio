<script setup>
import { computed, ref, watch } from 'vue'
import { Inbox } from 'lucide-vue-next'
import LineageFilterBar from './LineageFilterBar.vue'

// 资产面板：渲染输入或输出表列表，根据 kind prop 决定标题 + 是否显示 refresh/titles
const props = defineProps({
  kind: { type: String, required: true }, // 'inputs' | 'outputs'
  assets: { type: Array, default: () => [] },
  preset: { type: Object, default: null },  // 外部跳转时预设筛选
})

const isOutput = computed(() => props.kind === 'outputs')
const title = computed(() => isOutput.value ? '输出资产（被写入）' : '输入资产（被读取）')

// role 配色（沿用 .tag-* class）
const ROLE_TAG_CLASS = {
  target: 'tag-target',
  intermediate: 'tag-intermediate',
  source_fact: 'tag-source',
  remote_dblink: 'tag-source',
  config: 'tag-reference',
  reference: 'tag-reference',
  dimension: 'tag-source',
  filter: 'tag-intermediate',
}

const REFRESH_LABEL = {
  truncate_insert: 'TRUNCATE+INSERT',
  delete_insert: 'DELETE+INSERT',
  delete_insert_partial: 'DELETE+INSERT 增量',
  merge: 'MERGE',
  update: 'UPDATE',
  append: '追加',
  mixed: '混合',
}

// ---------------- 筛选 ----------------
const search = ref('')
const schemaFilter = ref('all')
const roleFilter = ref('all')
const groupFilter = ref('all')

const schemaOptions = computed(() => {
  const s = new Set()
  for (const a of props.assets) if (a.schema) s.add(a.schema)
  return Array.from(s).sort()
})
const roleOptions = computed(() => {
  const s = new Set()
  for (const a of props.assets) {
    for (const r of (a.roles || [a.primary_role])) if (r) s.add(r)
  }
  return Array.from(s).sort()
})
const groupOptions = computed(() => {
  const s = new Set()
  for (const a of props.assets) for (const g of (a.groups || [])) s.add(g)
  return Array.from(s).sort()
})

const filtered = computed(() => {
  const kw = search.value.trim().toLowerCase()
  return props.assets.filter(a => {
    if (kw) {
      const hay = `${a.name || ''} ${a.basename || ''} ${a.schema || ''}`.toLowerCase()
      if (!hay.includes(kw)) return false
    }
    if (schemaFilter.value !== 'all' && a.schema !== schemaFilter.value) return false
    if (roleFilter.value !== 'all') {
      const roles = a.roles || (a.primary_role ? [a.primary_role] : [])
      if (!roles.includes(roleFilter.value)) return false
    }
    if (groupFilter.value !== 'all') {
      if (!(a.groups || []).includes(groupFilter.value)) return false
    }
    return true
  })
})

const isFilterActive = computed(() =>
  !!search.value
  || schemaFilter.value !== 'all'
  || roleFilter.value !== 'all'
  || groupFilter.value !== 'all'
)

function resetFilters() {
  search.value = ''
  schemaFilter.value = 'all'
  roleFilter.value = 'all'
  groupFilter.value = 'all'
}

watch(
  () => props.preset,
  (val) => {
    if (!val) return
    if (val.search != null) search.value = val.search
    if (val.schemaFilter != null) schemaFilter.value = val.schemaFilter
    if (val.roleFilter != null) roleFilter.value = val.roleFilter
    if (val.groupFilter != null) groupFilter.value = val.groupFilter
  },
  { immediate: true },
)
</script>

<template>
  <section class="card space-y-3">
    <div class="flex items-center justify-between">
      <div>
        <h3 class="text-base font-semibold text-slate-800">{{ title }}</h3>
        <p class="muted text-xs">{{ assets.length }} 张表</p>
      </div>
    </div>

    <LineageFilterBar
      v-if="assets.length"
      v-model:search="search"
      :search-placeholder="`表名 / Schema 搜索`"
      :total="assets.length"
      :visible="filtered.length"
      :active="isFilterActive"
      @clear="resetFilters"
    >
      <template #filters>
        <select v-if="schemaOptions.length" v-model="schemaFilter" class="filter-select">
          <option value="all">全部 Schema</option>
          <option v-for="s in schemaOptions" :key="s" :value="s">{{ s }}</option>
        </select>
        <select v-if="roleOptions.length" v-model="roleFilter" class="filter-select">
          <option value="all">{{ $t('lineagePanel.common.filterAllRoles') }}</option>
          <option v-for="r in roleOptions" :key="r" :value="r">{{ r }}</option>
        </select>
        <select v-if="groupOptions.length" v-model="groupFilter" class="filter-select">
          <option value="all">{{ $t('lineagePanel.common.filterAllGroups') }}</option>
          <option v-for="g in groupOptions" :key="g" :value="g">{{ g }}</option>
        </select>
      </template>
    </LineageFilterBar>

    <div v-if="!assets.length" class="rounded-lg border border-dashed border-slate-200 py-8 text-center text-slate-400">
      <Inbox class="mx-auto mb-2 h-8 w-8 text-slate-300" />
      <p class="text-sm">无{{ isOutput ? '输出' : '输入' }}资产</p>
    </div>

    <div v-else-if="!filtered.length" class="rounded-lg border border-dashed border-slate-200 py-8 text-center text-slate-400">
      <p class="text-sm">{{ $t('lineagePanel.asset.noMatch') }}</p>
      <p class="muted text-xs">调整搜索词或筛选条件，或点击"清空筛选"恢复</p>
    </div>

    <div v-else class="overflow-x-auto">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-slate-200 bg-slate-50">
            <th class="px-3 py-2 text-left text-xs font-semibold text-slate-500">{{ $t('lineagePanel.common.tableName') }}</th>
            <th class="px-3 py-2 text-left text-xs font-semibold text-slate-500">Schema</th>
            <th class="px-3 py-2 text-left text-xs font-semibold text-slate-500">{{ $t('lineagePanel.common.role') }}</th>
            <th v-if="isOutput" class="px-3 py-2 text-left text-xs font-semibold text-slate-500">{{ $t('lineagePanel.common.writeMode') }}</th>
            <th v-if="isOutput" class="px-3 py-2 text-left text-xs font-semibold text-slate-500">{{ $t('lineagePanel.common.title') }}</th>
            <th class="px-3 py-2 text-left text-xs font-semibold text-slate-500">{{ $t('lineagePanel.common.group') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="asset in filtered" :key="asset.name" class="border-t border-slate-100">
            <td class="px-3 py-2 sql-font font-medium text-slate-800">{{ asset.basename || asset.name }}</td>
            <td class="px-3 py-2 sql-font text-slate-500">{{ asset.schema || '—' }}</td>
            <td class="px-3 py-2">
              <div class="flex flex-wrap gap-1">
                <span
                  v-for="role in (asset.roles || [asset.primary_role])" :key="role"
                  class="tag-base"
                  :class="ROLE_TAG_CLASS[role] || 'tag-source'"
                >{{ role }}</span>
              </div>
            </td>
            <td v-if="isOutput" class="px-3 py-2">
              <span
                v-if="asset.refresh_mode"
                class="status-badge status-info"
              >{{ REFRESH_LABEL[asset.refresh_mode] || asset.refresh_mode }}</span>
              <span v-else class="text-slate-300">—</span>
            </td>
            <td v-if="isOutput" class="px-3 py-2 text-slate-700">
              <span v-if="!asset.titles?.length" class="text-slate-300">—</span>
              <ul v-else class="space-y-0.5 text-xs">
                <li v-for="(t, i) in asset.titles.slice(0, 3)" :key="i" class="break-words">{{ t }}</li>
                <li v-if="asset.titles.length > 3" class="muted">+{{ asset.titles.length - 3 }} 条</li>
              </ul>
            </td>
            <td class="px-3 py-2 text-xs">
              <div v-if="asset.groups?.length" class="flex flex-wrap gap-1">
                <span v-for="g in asset.groups" :key="g" class="rounded bg-primary-light px-1.5 py-0.5 text-[10px] font-medium text-primary">
                  {{ g }}
                </span>
              </div>
              <span v-else class="text-slate-300">—</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
