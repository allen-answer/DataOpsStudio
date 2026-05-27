<script setup>
import { computed } from 'vue'
import { storeToRefs } from 'pinia'
import { AlertTriangle, Database, Eye, Info, KeyRound, ListChecks, Wand2 } from 'lucide-vue-next'
import { useTaskStore } from '../../stores/task'

const props = defineProps({
  title: { type: String, default: '已缓存字段' },
  hint: { type: String, default: '预览或提取字段后会缓存到这里，可在后续步骤继续选择主键和忽略字段。' },
  compact: { type: Boolean, default: false },
})

const taskStore = useTaskStore()
const { taskDraft } = taskStore         // reactive
const {
  sourcePreviewData, targetPreviewData,
  sourceFields, targetFields,
  schemaDiagnostics, fieldPickerRows, fieldPickerHasFields,
} = storeToRefs(taskStore)
const {
  toggleFieldIncluded, fieldPickerSelectAll, fieldPickerExcludeOneSided,
  recommendKey,
} = taskStore

const sourcePreviewRows = computed(() => sourcePreviewData.value?.rows?.length ?? 0)
const targetPreviewRows = computed(() => targetPreviewData.value?.rows?.length ?? 0)

// 主键支持多列 — 用 toggle 语义:已选 -> 移除, 未选 -> append.
// 之前 setPrimaryKey 是整体替换,导致点 A 再点 B 时 A 丢失,用户报 "替换原来的".
function togglePrimaryKey(name) {
  const current = (taskDraft.key_columns || '')
    .split(/\s*,\s*/)
    .map(s => s.trim())
    .filter(Boolean)
  const idx = current.findIndex(k => k.toLowerCase() === name.toLowerCase())
  if (idx >= 0) {
    current.splice(idx, 1)
  } else {
    current.push(name)
  }
  taskDraft.key_columns = current.join(', ')
}

// 该字段是否已是主键 — 给按钮 highlight 用 (computed 自动跟随 taskDraft.key_columns)
const keyColumnSet = computed(() => new Set(
  (taskDraft.key_columns || '')
    .split(/\s*,\s*/)
    .map(s => s.trim().toLowerCase())
    .filter(Boolean),
))
function isKeyColumn(name) {
  return keyColumnSet.value.has(name.toLowerCase())
}
</script>

<template>
  <div class="card space-y-3" :class="compact ? 'p-3' : ''">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h3 class="flex items-center gap-2 text-base font-semibold text-slate-800">
          <ListChecks class="h-4 w-4 text-primary" />
          {{ title }}
        </h3>
        <p class="muted text-[11px]">{{ hint }}</p>
      </div>
      <div class="flex flex-wrap gap-1.5">
        <button class="btn btn-ghost h-8 gap-1 px-2 text-[11px]" :disabled="!fieldPickerHasFields" @click="recommendKey">
          <Wand2 class="h-3.5 w-3.5" /> 推荐主键
        </button>
        <button class="btn btn-ghost h-8 gap-1 px-2 text-[11px]" :disabled="!fieldPickerHasFields" @click="fieldPickerSelectAll">
          全部参与
        </button>
        <button class="btn btn-ghost h-8 gap-1 px-2 text-[11px]" :disabled="!fieldPickerHasFields" @click="fieldPickerExcludeOneSided">
          只保留交集
        </button>
      </div>
    </div>

    <div class="grid gap-2 text-xs sm:grid-cols-2 lg:grid-cols-4">
      <div class="rounded-lg bg-slate-50 px-3 py-2">
        <div class="muted flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider">
          <Database class="h-3 w-3" /> Source 字段
        </div>
        <div class="mt-1 text-lg font-bold text-slate-900">{{ sourceFields.length }}</div>
      </div>
      <div class="rounded-lg bg-slate-50 px-3 py-2">
        <div class="muted flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider">
          <Database class="h-3 w-3" /> Target 字段
        </div>
        <div class="mt-1 text-lg font-bold text-slate-900">{{ targetFields.length }}</div>
      </div>
      <div class="rounded-lg bg-slate-50 px-3 py-2">
        <div class="muted flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider">
          <Eye class="h-3 w-3" /> Source 预览
        </div>
        <div class="mt-1 text-lg font-bold text-slate-900">{{ sourcePreviewRows }}</div>
      </div>
      <div class="rounded-lg bg-slate-50 px-3 py-2">
        <div class="muted flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider">
          <Eye class="h-3 w-3" /> Target 预览
        </div>
        <div class="mt-1 text-lg font-bold text-slate-900">{{ targetPreviewRows }}</div>
      </div>
    </div>

    <div v-if="!fieldPickerHasFields" class="rounded-lg border border-dashed border-slate-200 bg-slate-50 p-4 text-center text-xs text-slate-400">
      暂无缓存字段。请先在数据来源页点击“预览”或“提取字段”。
    </div>

    <div
      v-if="fieldPickerHasFields && schemaDiagnostics.warnings.length"
      class="rounded-lg border border-status-warning-bg/70 bg-status-warning-bg/20 p-3 text-xs text-slate-700"
    >
      <div class="mb-1 flex items-center gap-1.5 font-semibold text-status-warning">
        <AlertTriangle class="h-3.5 w-3.5" />
        Schema 提示
      </div>
      <ul class="space-y-1">
        <li v-for="(warning, i) in schemaDiagnostics.warnings.slice(0, 4)" :key="i" class="flex items-start gap-1.5">
          <Info class="mt-0.5 h-3 w-3 shrink-0 text-status-info" />
          <span>{{ warning.message }}</span>
        </li>
      </ul>
    </div>

    <div v-if="fieldPickerHasFields" class="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3">
      <div
        v-for="row in fieldPickerRows"
        :key="row.key"
        class="flex items-center justify-between gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm"
      >
        <label class="flex min-w-0 flex-1 cursor-pointer items-center gap-2">
          <input
            type="checkbox"
            :checked="row.included"
            class="h-4 w-4 rounded text-primary focus:ring-primary"
            @change="toggleFieldIncluded(row.name)"
          >
          <span class="truncate sql-font text-xs text-slate-800">{{ row.name }}</span>
        </label>
        <span
          class="shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold"
          :class="row.onSource && row.onTarget
            ? 'bg-status-success-bg text-status-success'
            : (row.onSource ? 'bg-tag-source-bg text-tag-source' : 'bg-tag-intermediate-bg text-tag-intermediate')"
        >
          {{ row.onSource && row.onTarget ? '双侧' : (row.onSource ? '仅源' : '仅目标') }}
        </span>
        <button
          class="grid h-6 w-6 shrink-0 place-items-center rounded-md transition"
          :class="isKeyColumn(row.name)
            ? 'bg-primary text-white hover:bg-primary-hover'
            : 'text-slate-400 hover:bg-primary-light hover:text-primary'"
          :title="isKeyColumn(row.name) ? '点击移除主键(可多列)' : '点击加入主键(支持多列组合)'"
          @click="togglePrimaryKey(row.name)"
        >
          <KeyRound class="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  </div>
</template>
