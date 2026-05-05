<script setup>
import { computed, watch } from 'vue'
import { Wand2, AlertTriangle } from 'lucide-vue-next'
import FieldCachePanel from './FieldCachePanel.vue'
import { useTaskStore } from '../../stores/task'

const taskStore = useTaskStore()
const { taskDraft } = taskStore         // reactive
const { recommendKey } = taskStore

// 任一边是 Excel 时不能流式分块（Excel reader 不保证按主键有序）。开关需要 disable
// 并提示，同时一旦切回去自动复位 stream_compare=false 避免后端校验时才报错。
const hasExcelSide = computed(
  () => taskDraft.source_kind === 'excel' || taskDraft.target_kind === 'excel'
)

watch(hasExcelSide, (val) => {
  if (val && taskDraft.stream_compare) taskDraft.stream_compare = false
})

const RULE_TOGGLES = computed(() => [
  { key: 'trim_strings',    label: '字符串去空格', hint: '比较前对两侧字符串 trim()' },
  { key: 'case_insensitive', label: '忽略大小写',   hint: '字符串比较忽略大小写' },
  { key: 'empty_as_null',   label: '空字符串视为空值', hint: '"" 与 NULL 等价' },
  {
    key: 'stream_compare',
    label: '流式分块对比',
    hint: hasExcelSide.value
      ? '⚠ Excel 端不支持流式：Excel reader 不保证按主键有序，需要两侧均为按主键排序的 SQL'
      : '不全量加载内存，要求两侧 SQL 已按主键排序',
    disabled: hasExcelSide.value,
  },
])
</script>

<template>
  <section class="space-y-4">
    <FieldCachePanel
      title="来自数据预览的字段缓存"
      hint="第 1 步预览/提取到的字段会保留在这里；选择主键、忽略字段后立即写入当前规则。"
      compact
    />
    <!-- 主键 / 忽略字段 -->
    <div class="card">
      <h3 class="mb-3 text-base font-semibold text-slate-800">{{ $t('workbench.rules.sectionKeyIgnore') }}</h3>
      <div class="grid grid-cols-1 gap-3 md:grid-cols-2">
        <div>
          <label>
            <span class="muted mb-1 block text-[10px] font-bold uppercase tracking-wider">{{ $t('workbench.rules.keyColumns') }}</span>
            <div class="flex gap-2">
              <input v-model="taskDraft.key_columns" :placeholder="$t('workbench.rules.keyPlaceholder')" class="flex-1 bg-slate-50 sql-font text-sm">
              <button class="btn btn-ghost h-10 gap-1.5 px-3 text-xs" @click="recommendKey">
                <Wand2 class="h-3.5 w-3.5" /> 自动推荐
              </button>
            </div>
          </label>
          <p class="muted mt-1 text-[11px]">{{ $t('workbench.rules.keyHint') }}</p>
        </div>
        <label>
          <span class="muted mb-1 block text-[10px] font-bold uppercase tracking-wider">{{ $t('workbench.rules.ignoreColumns') }}</span>
          <input v-model="taskDraft.ignore_columns" :placeholder="$t('workbench.rules.ignorePlaceholder')" class="bg-slate-50 sql-font text-sm">
          <p class="muted mt-1 text-[11px]">{{ $t('workbench.rules.ignoreHint') }}</p>
        </label>
      </div>
      <div class="mt-3 max-w-md">
        <label>
          <span class="muted mb-1 block text-[10px] font-bold uppercase tracking-wider">{{ $t('workbench.rules.schemaPolicy') }}</span>
          <select v-model="taskDraft.schema_policy" class="bg-slate-50 text-sm">
            <option value="warn">{{ $t('workbench.rules.schemaWarn') }}</option>
            <option value="strict">{{ $t('workbench.rules.schemaStrict') }}</option>
          </select>
        </label>
        <p class="muted mt-1 text-[11px]">{{ $t('workbench.rules.schemaHint') }}</p>
      </div>
    </div>

    <!-- 对比开关 4 个 -->
    <div class="card">
      <h3 class="mb-3 text-base font-semibold text-slate-800">{{ $t('workbench.rules.sectionTypeNormalize') }}</h3>
      <div class="grid grid-cols-1 gap-2 md:grid-cols-2">
        <label
          v-for="r in RULE_TOGGLES" :key="r.key"
          class="flex items-start gap-3 rounded-lg border border-slate-200 bg-slate-50 p-3 transition"
          :class="r.disabled
            ? 'cursor-not-allowed opacity-60'
            : 'cursor-pointer hover:border-primary/50 hover:bg-white'"
          :title="r.disabled ? r.hint : ''"
        >
          <input
            v-model="taskDraft[r.key]"
            type="checkbox"
            :disabled="r.disabled"
            class="mt-0.5 h-4 w-4 rounded text-primary focus:ring-primary"
          >
          <div class="min-w-0">
            <div class="flex items-center gap-1 text-sm font-medium text-slate-800">
              <AlertTriangle v-if="r.disabled" class="h-3.5 w-3.5 text-status-warning" />
              {{ r.label }}
            </div>
            <div class="muted text-[11px]">{{ r.hint }}</div>
          </div>
        </label>
      </div>
    </div>

    <!-- 数值容忍 / 行数 / 分块 -->
    <div class="card">
      <h3 class="mb-3 text-base font-semibold text-slate-800">{{ $t('workbench.rules.sectionLimits') }}</h3>
      <div class="grid grid-cols-2 gap-3 md:grid-cols-4">
        <label>
          <span class="muted mb-1 block text-[10px] font-bold uppercase tracking-wider">{{ $t('workbench.rules.numericTolerance') }}</span>
          <input v-model="taskDraft.numeric_tolerance" type="number" step="any" placeholder="0" class="bg-slate-50">
        </label>
        <label>
          <span class="muted mb-1 block text-[10px] font-bold uppercase tracking-wider">{{ $t('workbench.rules.maxRows') }}</span>
          <input v-model="taskDraft.max_rows" type="number" placeholder="100000" class="bg-slate-50">
        </label>
        <label>
          <span class="muted mb-1 block text-[10px] font-bold uppercase tracking-wider">{{ $t('workbench.rules.exportMaxRows') }}</span>
          <input v-model="taskDraft.export_max_rows" type="number" placeholder="100000" class="bg-slate-50">
        </label>
        <label>
          <span class="muted mb-1 block text-[10px] font-bold uppercase tracking-wider">{{ $t('workbench.rules.fetchChunkSize') }}</span>
          <input v-model="taskDraft.fetch_chunk_size" type="number" placeholder="5000" class="bg-slate-50">
        </label>
      </div>
    </div>
  </section>
</template>
