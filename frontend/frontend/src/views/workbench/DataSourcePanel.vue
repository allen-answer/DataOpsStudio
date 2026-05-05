<script setup>
import { defineAsyncComponent, computed } from 'vue'
import { storeToRefs } from 'pinia'
import { Code2, FileSpreadsheet, Sparkles, Eye, ListTree } from 'lucide-vue-next'
import { useTaskStore } from '../../stores/task'
import { useNoticeStore } from '../../stores/notice'

// 源 / 目标 数据来源面板：SQL 编辑器或 Excel 上传 + 字段提取 + 预览
const SqlEditor = defineAsyncComponent(() => import('../../components/SqlEditor.vue'))

const props = defineProps({
  // 'source' or 'target'
  side: { type: String, required: true },
})

const taskStore = useTaskStore()
const { taskDraft } = taskStore         // reactive
const {
  sourcePreviewData, targetPreviewData,
  sourceFields, targetFields,
  sourceFieldWarnings, targetFieldWarnings,
} = storeToRefs(taskStore)
const { extractFields, previewTask, formatSql, uploadExcel } = taskStore
const { copyField } = useNoticeStore()

// 一组以 side 派生的字段访问器
const kind   = computed({ get: () => taskDraft[`${props.side}_kind`], set: v => taskDraft[`${props.side}_kind`] = v })
const sql    = computed({ get: () => taskDraft[`${props.side}_sql`],  set: v => taskDraft[`${props.side}_sql`]  = v })
const sheet  = computed({ get: () => taskDraft[`${props.side}_sheet`], set: v => taskDraft[`${props.side}_sheet`] = v })
const headerRow = computed({
  get: () => taskDraft[`${props.side}_header_row`],
  set: v => taskDraft[`${props.side}_header_row`] = v,
})
const excelFilename = computed(() => taskDraft[`${props.side}_excel_filename`])
const excelSheets   = computed(() => taskDraft[`${props.side}_excel_sheets`] || [])
const fields        = computed(() => props.side === 'source' ? sourceFields.value : targetFields.value)
const fieldWarnings = computed(() => props.side === 'source' ? sourceFieldWarnings.value : targetFieldWarnings.value)
const previewData   = computed(() => props.side === 'source' ? sourcePreviewData.value : targetPreviewData.value)
const previewColumns = computed(() => previewData.value?.columns?.length ? previewData.value.columns : Object.keys(previewData.value?.rows?.[0] ?? {}))

const isSql = computed(() => kind.value === 'sql')

const accentClass = computed(() => props.side === 'source' ? 'bg-blue-500' : 'bg-orange-500')
const sideLabel = computed(() => props.side === 'source' ? 'SOURCE' : 'TARGET')
</script>

<template>
  <div class="card space-y-3 p-4">
    <div class="flex items-center justify-between">
      <div class="flex items-center gap-2">
        <span class="h-2 w-2 rounded-full" :class="accentClass" />
        <span class="text-xs font-bold uppercase tracking-wider text-slate-600">{{ sideLabel }}</span>

        <!-- SQL / Excel 切换 -->
        <div class="ml-2 inline-flex rounded-md bg-slate-100 p-0.5 text-[11px] font-bold">
          <button
            class="flex items-center gap-1 rounded px-2 py-1 transition"
            :class="isSql ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-500 hover:text-slate-700'"
            @click="kind = 'sql'"
          >
            <Code2 class="h-3 w-3" /> SQL
          </button>
          <button
            class="flex items-center gap-1 rounded px-2 py-1 transition"
            :class="!isSql ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-500 hover:text-slate-700'"
            @click="kind = 'excel'"
          >
            <FileSpreadsheet class="h-3 w-3" /> Excel
          </button>
        </div>
      </div>

      <div class="flex items-center gap-1.5">
        <button
          v-if="isSql"
          class="btn btn-ghost h-7 gap-1 px-2 text-[11px]"
          title="格式化 SQL"
          @click="formatSql(side)"
        >
          <Sparkles class="h-3 w-3" /> 格式化
        </button>
        <button
          class="btn btn-ghost h-7 gap-1 px-2 text-[11px]"
          title="提取字段名"
          @click="extractFields(side)"
        >
          <ListTree class="h-3 w-3" /> 提取字段
        </button>
        <button
          v-if="isSql"
          class="btn btn-primary h-7 gap-1 px-2 text-[11px]"
          title="按当前草稿预览前 10 行，不需要先保存任务"
          @click="previewTask(side)"
        >
          <Eye class="h-3 w-3" /> 预览
        </button>
      </div>
    </div>

    <!-- SQL 模式 -->
    <template v-if="isSql">
      <SqlEditor v-model="sql" :placeholder="side === 'target' ? '双 SQL 模式填写，单 SQL 留空' : '请输入查询 SQL'" />
    </template>

    <!-- Excel 模式 -->
    <template v-else>
      <div class="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-3 text-sm">
        <input
          type="file"
          accept=".xlsx,.xlsm"
          class="block w-full text-xs"
          @change="uploadExcel(side, $event.target.files[0])"
        >
        <p v-if="excelFilename" class="mt-2 text-xs text-slate-600">
          已上传：<strong class="text-slate-800">{{ excelFilename }}</strong>
        </p>
        <p v-else class="mt-2 text-xs text-slate-400">支持 .xlsx / .xlsm；上传后自动列出 sheet。</p>
      </div>
      <div class="grid grid-cols-2 gap-2.5">
        <label>
          <span class="muted mb-1 block text-[10px] font-bold uppercase tracking-wider">Sheet</span>
          <select v-model="sheet" class="bg-slate-50">
            <option value="">默认（第一个）</option>
            <option v-for="s in excelSheets" :key="s" :value="s">{{ s }}</option>
          </select>
        </label>
        <label>
          <span class="muted mb-1 block text-[10px] font-bold uppercase tracking-wider">表头行</span>
          <input v-model="headerRow" type="number" min="1" class="bg-slate-50">
        </label>
      </div>
    </template>

    <!-- 字段 chips -->
    <div v-if="fields.length" class="flex flex-wrap gap-1">
      <span
        v-for="col in fields"
        :key="col"
        class="cursor-pointer rounded-full bg-slate-700 px-2 py-0.5 text-[11px] sql-font text-slate-200 transition hover:bg-primary"
        :title="'点击复制：' + col"
        @click="copyField(col)"
      >{{ col }}</span>
    </div>

    <div v-if="fieldWarnings.length" class="rounded-lg border border-status-warning-bg/70 bg-status-warning-bg/20 p-2 text-[11px] text-status-warning">
      <p v-for="(warning, i) in fieldWarnings" :key="i">{{ warning.message }}</p>
    </div>

    <!-- 预览结果（SQL 模式） -->
    <div v-if="isSql && previewData" class="text-xs">
      <p v-if="previewData.loading" class="text-slate-400">预览中...</p>
      <p v-else-if="previewData.error" class="status-badge status-error">{{ previewData.error }}</p>
      <template v-else>
        <p class="muted mb-1 text-[11px]">
          前 {{ previewData.rows?.length ?? 0 }} 行
          <span v-if="previewData.truncated" class="ml-1 text-amber-600">已截断，仅用于字段选择</span>
        </p>
        <p v-if="!previewData.rows?.length" class="rounded-lg border border-dashed border-slate-200 p-3 text-slate-400">
          查询成功，但没有返回行。
        </p>
        <div class="overflow-x-auto rounded-lg border border-slate-200">
          <table v-if="previewData.rows?.length" class="w-full">
            <thead>
              <tr class="border-b border-slate-200 bg-slate-50">
                <th v-for="col in previewColumns" :key="col" class="px-3 py-2 text-left font-bold text-slate-600">{{ col }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, i) in previewData.rows" :key="i" class="border-b border-slate-100 last:border-0 hover:bg-slate-50">
                <td v-for="col in previewColumns" :key="col" class="px-3 py-2 text-slate-700">{{ row[col] ?? '' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>
    </div>
  </div>
</template>
