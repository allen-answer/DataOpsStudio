<script setup>
import { computed, defineAsyncComponent, ref } from 'vue'
import LineageFilterBar from './lineage/LineageFilterBar.vue'

// 业务分组 DAG 视图懒加载（用 G6，会进 g6-vendor chunk —— 已经在血缘图模块加载了）
const BusinessGroupDag = defineAsyncComponent(() => import('./BusinessGroupDag.vue'))

const props = defineProps({
  semantic: { type: Object, default: () => ({}) },
})

// 业务分组的两种视图：卡片（适合少量分组浏览）/ 图（适合"哪些业务流向哪些业务"）
const groupViewMode = ref('cards') // 'cards' | 'graph'

// ---------------- 目标表 / 存储过程的筛选 ----------------
const targetSearch = ref('')
const targetRoleFilter = ref('all')
const procSearch = ref('')
const procOnlyUnsupported = ref(false)

const targetRoleOptions = computed(() => {
  const s = new Set()
  for (const t of props.semantic.targets || []) {
    for (const r of (t.roles || [])) s.add(r)
  }
  return Array.from(s).sort()
})

const filteredTargets = computed(() => {
  const kw = targetSearch.value.trim().toLowerCase()
  return (props.semantic.targets || []).filter(t => {
    if (kw) {
      const titles = (t.titles || []).join(' ')
      const hay = `${t.table || ''} ${titles}`.toLowerCase()
      if (!hay.includes(kw)) return false
    }
    if (targetRoleFilter.value !== 'all') {
      if (!(t.roles || []).includes(targetRoleFilter.value)) return false
    }
    return true
  })
})

const filteredProcedures = computed(() => {
  const kw = procSearch.value.trim().toLowerCase()
  return (props.semantic.procedures || []).filter(p => {
    if (kw && !(p.name || '').toLowerCase().includes(kw)) return false
    if (procOnlyUnsupported.value && !p.unsupported_count) return false
    return true
  })
})

const targetFilterActive = computed(() => !!targetSearch.value || targetRoleFilter.value !== 'all')
const procFilterActive = computed(() => !!procSearch.value || procOnlyUnsupported.value)
const resetTargetFilters = () => { targetSearch.value = ''; targetRoleFilter.value = 'all' }
const resetProcFilters = () => { procSearch.value = ''; procOnlyUnsupported.value = false }

const hasContent = computed(() =>
  (props.semantic.targets?.length || 0)
  + (props.semantic.observations?.length || 0)
  + (props.semantic.risks?.length || 0)
  + (props.semantic.procedures?.length || 0)
  + (props.semantic.business_groups?.length || 0) > 0
)

const GROUP_PALETTE = [
  'border-blue-200 bg-blue-50',
  'border-emerald-200 bg-emerald-50',
  'border-violet-200 bg-violet-50',
  'border-amber-200 bg-amber-50',
  'border-cyan-200 bg-cyan-50',
  'border-rose-200 bg-rose-50',
  'border-slate-200 bg-slate-50',
]

function groupTone(index) {
  return GROUP_PALETTE[index % GROUP_PALETTE.length]
}

const ROLE_PILL = {
  target: 'bg-blue-100 text-blue-700',
  intermediate: 'bg-violet-100 text-violet-700',
  source_fact: 'bg-slate-100 text-slate-600',
  remote_dblink: 'bg-orange-100 text-orange-700',
  config: 'bg-amber-100 text-amber-700',
  reference: 'bg-cyan-100 text-cyan-700',
  dimension: 'bg-emerald-100 text-emerald-700',
  filter: 'bg-rose-100 text-rose-700',
}

const REFRESH_PILL = {
  truncate_insert: 'bg-rose-100 text-rose-700',
  delete_insert: 'bg-rose-100 text-rose-700',
  delete_insert_partial: 'bg-amber-100 text-amber-700',
  merge: 'bg-blue-100 text-blue-700',
  update: 'bg-cyan-100 text-cyan-700',
  append: 'bg-emerald-100 text-emerald-700',
  mixed: 'bg-violet-100 text-violet-700',
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

const RISK_TONE = {
  high: 'border-rose-300 bg-rose-50 text-rose-900',
  medium: 'border-amber-300 bg-amber-50 text-amber-900',
  low: 'border-slate-300 bg-slate-50 text-slate-700',
}

const RISK_BADGE = {
  high: 'bg-rose-200 text-rose-900',
  medium: 'bg-amber-200 text-amber-900',
  low: 'bg-slate-200 text-slate-800',
}

const DML_PILL = {
  INSERT: 'bg-emerald-100 text-emerald-700',
  UPDATE: 'bg-cyan-100 text-cyan-700',
  MERGE: 'bg-blue-100 text-blue-700',
  DELETE: 'bg-rose-100 text-rose-700',
  TRUNCATE: 'bg-rose-100 text-rose-700',
  SELECT: 'bg-slate-100 text-slate-600',
  WITH: 'bg-slate-100 text-slate-600',
  REPLACE: 'bg-emerald-100 text-emerald-700',
  CREATE: 'bg-violet-100 text-violet-700',
}

const PARSE_STATUS_PILL = {
  parsed: 'bg-emerald-100 text-emerald-700',
  unsupported: 'bg-amber-100 text-amber-800',
  unknown: 'bg-slate-100 text-slate-500',
}

const PARSE_STATUS_LABEL = {
  parsed: '已解析',
  unsupported: '未支持',
  unknown: '—',
}
</script>

<template>
  <div v-if="hasContent" class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
    <div class="mb-5">
      <h2 class="text-xl font-bold text-slate-800">语义血缘</h2>
      <p class="mt-1 text-sm text-slate-500">规则分析派生的"脚本干啥的"摘要 —— 写入目标 / 角色 / 重刷模式 / 风险</p>
    </div>

    <div v-if="semantic.observations?.length" class="mb-6">
      <h3 class="mb-2 text-xs font-bold uppercase tracking-wider text-slate-400">观察</h3>
      <ul class="space-y-1.5">
        <li
          v-for="(obs, i) in semantic.observations"
          :key="i"
          class="flex items-start gap-2 text-sm text-slate-700"
        >
          <span class="mt-1.5 h-1.5 w-1.5 flex-none rounded-full bg-blue-500"></span>
          <span>{{ obs }}</span>
        </li>
      </ul>
    </div>

    <div v-if="semantic.risks?.length" class="mb-6">
      <h3 class="mb-2 text-xs font-bold uppercase tracking-wider text-slate-400">风险</h3>
      <ul class="space-y-2">
        <li
          v-for="(risk, i) in semantic.risks"
          :key="i"
          class="rounded-lg border px-3 py-2 text-sm"
          :class="RISK_TONE[risk.level] || RISK_TONE.low"
        >
          <div class="flex items-center gap-2">
            <span
              class="rounded px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-widest"
              :class="RISK_BADGE[risk.level] || RISK_BADGE.low"
            >{{ risk.level }}</span>
            <span class="font-medium">{{ risk.type }}</span>
          </div>
          <p class="mt-1 break-words">{{ risk.message }}</p>
        </li>
      </ul>
    </div>

    <div v-if="semantic.business_groups?.length" class="mb-6">
      <div class="mb-2 flex items-center justify-between">
        <h3 class="text-xs font-bold uppercase tracking-wider text-slate-400">
          业务分组 ({{ semantic.business_groups.length }})
        </h3>
        <div class="flex rounded-md bg-slate-100 p-0.5 text-xs font-medium text-slate-600">
          <button
            class="rounded px-2 py-1"
            :class="groupViewMode === 'cards' ? 'bg-white text-violet-600 shadow-sm' : ''"
            @click="groupViewMode = 'cards'"
          >卡片</button>
          <button
            class="rounded px-2 py-1"
            :class="groupViewMode === 'graph' ? 'bg-white text-violet-600 shadow-sm' : ''"
            @click="groupViewMode = 'graph'"
            title="DAG 视图：业务分组级数据流向"
          >图</button>
        </div>
      </div>

      <BusinessGroupDag
        v-if="groupViewMode === 'graph'"
        :groups="semantic.business_groups"
        :grouped-edges="semantic.grouped_edges || []"
      />

      <div v-else class="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
        <div
          v-for="(group, gi) in semantic.business_groups"
          :key="group.name"
          class="rounded-lg border p-3"
          :class="groupTone(gi)"
        >
          <div class="flex items-baseline justify-between gap-2">
            <div class="font-bold text-slate-800">{{ group.name }}</div>
            <div class="text-xs text-slate-600 whitespace-nowrap">
              {{ group.table_count }} 张
              <span v-if="group.target_count" class="text-slate-700">/ {{ group.target_count }} 写入</span>
            </div>
          </div>
          <p v-if="group.description" class="mt-1 text-xs text-slate-500">{{ group.description }}</p>
          <div class="mt-2 flex flex-wrap gap-1">
            <span
              v-for="t in group.tables.slice(0, 6)"
              :key="t"
              class="rounded bg-white/70 px-1.5 py-0.5 text-[11px] sql-font text-slate-700"
            >{{ t }}</span>
            <span
              v-if="group.tables.length > 6"
              class="rounded bg-white/70 px-1.5 py-0.5 text-[11px] text-slate-500"
            >+{{ group.tables.length - 6 }}</span>
          </div>
        </div>
      </div>

      <div v-if="groupViewMode === 'cards' && semantic.grouped_edges?.length" class="mt-3">
        <h4 class="mb-1.5 text-[10px] font-bold uppercase tracking-wider text-slate-400">
          跨分组依赖
        </h4>
        <ul class="flex flex-wrap gap-1.5">
          <li
            v-for="ge in semantic.grouped_edges"
            :key="`${ge.source_group}__${ge.target_group}`"
            class="rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-xs text-slate-700"
          >
            <span class="font-medium">{{ ge.source_group }}</span>
            <span class="mx-1 text-slate-400">→</span>
            <span class="font-medium">{{ ge.target_group }}</span>
            <span class="ml-1.5 text-slate-500">×{{ ge.edge_count }}</span>
          </li>
        </ul>
      </div>
    </div>

    <div v-if="semantic.targets?.length" class="mb-6">
      <h3 class="mb-2 text-xs font-bold uppercase tracking-wider text-slate-400">
        目标表 ({{ semantic.targets.length }})
      </h3>
      <LineageFilterBar
        v-model:search="targetSearch"
        search-placeholder="目标表 / 业务标题搜索"
        :total="semantic.targets.length"
        :visible="filteredTargets.length"
        :active="targetFilterActive"
        @clear="resetTargetFilters"
        class="mb-2"
      >
        <template #filters>
          <select v-if="targetRoleOptions.length > 1" v-model="targetRoleFilter" class="filter-select">
            <option value="all">全部角色</option>
            <option v-for="r in targetRoleOptions" :key="r" :value="r">{{ r }}</option>
          </select>
        </template>
      </LineageFilterBar>
      <div v-if="!filteredTargets.length" class="rounded-lg border border-dashed border-slate-200 py-6 text-center text-slate-400">
        <p class="text-sm">没有命中的目标表</p>
      </div>
      <div v-else class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead>
            <tr class="border-b border-slate-200 text-left text-xs font-bold uppercase tracking-wider text-slate-400">
              <th class="py-2 pr-4 font-bold">表名</th>
              <th class="py-2 pr-4 font-bold">角色</th>
              <th class="py-2 pr-4 font-bold">写入模式</th>
              <th class="py-2 pr-4 font-bold">由谁写入</th>
              <th class="py-2 pr-4 font-bold">操作计数</th>
              <th class="py-2 font-bold">业务标题</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="t in filteredTargets" :key="t.table" class="border-b border-slate-100 align-top">
              <td class="whitespace-nowrap py-3 pr-4 font-medium sql-font text-slate-800">{{ t.table }}</td>
              <td class="py-3 pr-4">
                <div class="flex flex-wrap gap-1">
                  <span
                    v-for="role in t.roles"
                    :key="role"
                    class="rounded px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider"
                    :class="ROLE_PILL[role] || ROLE_PILL.source_fact"
                  >{{ role }}</span>
                </div>
              </td>
              <td class="py-3 pr-4">
                <span
                  v-if="t.refresh_mode"
                  class="rounded px-2 py-0.5 text-xs font-bold"
                  :class="REFRESH_PILL[t.refresh_mode] || 'bg-slate-100 text-slate-700'"
                >{{ REFRESH_LABEL[t.refresh_mode] || t.refresh_mode }}</span>
                <span v-else class="text-slate-300">—</span>
              </td>
              <td class="py-3 pr-4">
                <!-- procedure_origins：哪些过程内部写过本表。空 list 表示纯顶层 DML，
                     这种情况下显示 "顶层" pill；有 origin 时把过程名/匿名块标出来。 -->
                <div v-if="t.procedure_origins?.length" class="flex flex-wrap gap-1">
                  <span
                    v-for="origin in t.procedure_origins"
                    :key="origin"
                    class="rounded bg-violet-100 px-2 py-0.5 text-[11px] font-medium text-violet-700"
                    :title="`由 ${origin} 写入`"
                  >
                    <span v-if="origin === '&lt;anonymous&gt;'">匿名块</span>
                    <span v-else class="sql-font">{{ origin }}</span>
                  </span>
                </div>
                <span v-else class="rounded bg-slate-100 px-2 py-0.5 text-[11px] text-slate-600">顶层</span>
              </td>
              <td class="py-3 pr-4 text-xs text-slate-600">
                <span v-if="t.counts.insert" class="mr-2">INS×{{ t.counts.insert }}</span>
                <span v-if="t.counts.update" class="mr-2">UPD×{{ t.counts.update }}</span>
                <span v-if="t.counts.merge" class="mr-2">MRG×{{ t.counts.merge }}</span>
                <span v-if="t.counts.delete" class="mr-2 text-rose-600 font-medium">DEL×{{ t.counts.delete }}</span>
                <span v-if="t.counts.truncate" class="mr-2 text-rose-600 font-medium">TRC×{{ t.counts.truncate }}</span>
              </td>
              <td class="min-w-[12rem] py-3">
                <ul v-if="t.titles?.length" class="space-y-0.5">
                  <li v-for="(title, i) in t.titles" :key="i" class="break-words text-slate-700">{{ title }}</li>
                </ul>
                <span v-else class="text-slate-300">—</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div v-if="semantic.procedures?.length">
      <h3 class="mb-2 text-xs font-bold uppercase tracking-wider text-slate-400">存储过程</h3>
      <LineageFilterBar
        v-model:search="procSearch"
        search-placeholder="存储过程名搜索"
        :total="semantic.procedures.length"
        :visible="filteredProcedures.length"
        :active="procFilterActive"
        @clear="resetProcFilters"
        class="mb-2"
      >
        <template #filters>
          <label class="inline-flex items-center gap-1 text-[11px] text-slate-600">
            <input v-model="procOnlyUnsupported" type="checkbox" class="h-3.5 w-3.5" />
            仅看含未解析段
          </label>
        </template>
      </LineageFilterBar>
      <div v-if="!filteredProcedures.length" class="rounded-lg border border-dashed border-slate-200 py-6 text-center text-slate-400">
        <p class="text-sm">没有命中的存储过程</p>
      </div>
      <div v-else class="space-y-2">
        <details
          v-for="proc in filteredProcedures"
          :key="proc.name"
          class="rounded-lg border border-slate-200 bg-slate-50"
        >
          <summary class="flex cursor-pointer items-center gap-2 px-3 py-2 text-sm">
            <span class="sql-font font-medium text-slate-800">{{ proc.name }}</span>
            <span class="muted text-xs">{{ proc.kind }}</span>
            <span class="rounded bg-blue-100 px-1.5 py-0.5 text-[10px] font-bold text-blue-700">
              {{ proc.segment_count }} 段
            </span>
            <span
              v-if="proc.unsupported_count"
              class="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-bold text-amber-800"
              :title="`${proc.unsupported_count} 段 sqlglot 无法解析`"
            >
              {{ proc.unsupported_count }} 未解析
            </span>
          </summary>
          <div v-if="proc.steps?.length" class="border-t border-slate-200 px-3 py-2">
            <table class="w-full text-xs">
              <thead>
                <tr class="text-left text-[10px] font-bold uppercase tracking-wider text-slate-400">
                  <th class="py-1 pr-3">行</th>
                  <th class="py-1 pr-3">操作</th>
                  <th class="py-1 pr-3">业务标题</th>
                  <th class="py-1">解析</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="step in proc.steps" :key="step.segment_index" class="border-t border-slate-100">
                  <td class="py-1 pr-3 sql-font text-slate-500">
                    {{ step.line_start }}<span v-if="step.line_end && step.line_end !== step.line_start">–{{ step.line_end }}</span>
                  </td>
                  <td class="py-1 pr-3">
                    <span
                      v-if="step.dml_keyword"
                      class="rounded px-1.5 py-0.5 text-[10px] font-bold"
                      :class="DML_PILL[step.dml_keyword] || 'bg-slate-200 text-slate-700'"
                    >{{ step.dml_keyword }}</span>
                  </td>
                  <td class="py-1 pr-3 text-slate-700 break-words">{{ step.preceding_comment || '—' }}</td>
                  <td class="py-1">
                    <span
                      class="rounded px-1.5 py-0.5 text-[10px] font-bold"
                      :class="PARSE_STATUS_PILL[step.parse_status] || PARSE_STATUS_PILL.unknown"
                    >{{ PARSE_STATUS_LABEL[step.parse_status] || step.parse_status }}</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </details>
      </div>
    </div>
  </div>
</template>
