<script setup>
import { computed } from 'vue'

const props = defineProps({
  semantic: { type: Object, default: () => ({}) },
})

const hasContent = computed(() =>
  (props.semantic.targets?.length || 0)
  + (props.semantic.observations?.length || 0)
  + (props.semantic.risks?.length || 0)
  + (props.semantic.procedures?.length || 0) > 0
)

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

    <div v-if="semantic.targets?.length" class="mb-6">
      <h3 class="mb-2 text-xs font-bold uppercase tracking-wider text-slate-400">
        目标表 ({{ semantic.targets.length }})
      </h3>
      <div class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead>
            <tr class="border-b border-slate-200 text-left text-xs font-bold uppercase tracking-wider text-slate-400">
              <th class="py-2 pr-4 font-bold">表名</th>
              <th class="py-2 pr-4 font-bold">角色</th>
              <th class="py-2 pr-4 font-bold">写入模式</th>
              <th class="py-2 pr-4 font-bold">操作计数</th>
              <th class="py-2 font-bold">业务标题</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="t in semantic.targets" :key="t.table" class="border-b border-slate-100 align-top">
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
      <div class="flex flex-wrap gap-2">
        <div
          v-for="proc in semantic.procedures"
          :key="proc.name"
          class="flex items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm"
        >
          <span class="sql-font font-medium text-slate-800">{{ proc.name }}</span>
          <span class="muted text-xs">{{ proc.kind }}</span>
          <span class="rounded bg-blue-100 px-1.5 py-0.5 text-[10px] font-bold text-blue-700">
            {{ proc.segment_count }} 段
          </span>
        </div>
      </div>
    </div>
  </div>
</template>
