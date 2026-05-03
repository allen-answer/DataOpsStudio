<script setup>
import { computed, inject } from 'vue'
import { CheckSquare, Filter, ArrowRight, AlertTriangle, Info } from 'lucide-vue-next'

const {
  taskDraft, sourceFields, targetFields,
  fieldPickerRows, fieldPickerHasFields,
  toggleFieldIncluded, fieldPickerSelectAll, fieldPickerExcludeOneSided,
} = inject('app')

// ─── 跨模式（SQL/Excel/Excel/Excel/SQL）字段对齐风险检测 ──────────────────
// fieldPickerRows 已经把两侧字段 union 显示并标了 onSource / onTarget。
// 这里基于它派生几个高层警告：
const intersectionRows = computed(() => fieldPickerRows.value.filter(r => r.onSource && r.onTarget))
const sourceOnlyRows = computed(() => fieldPickerRows.value.filter(r => r.onSource && !r.onTarget))
const targetOnlyRows = computed(() => fieldPickerRows.value.filter(r => !r.onSource && r.onTarget))

// 命名风格检测：CamelCase vs snake_case 在 SQL ↔ Excel 跨混合时常见
function detectNamingStyle(names) {
  let camel = 0
  let snake = 0
  for (const n of names) {
    if (/[a-z][A-Z]/.test(n)) camel += 1
    if (/_[a-z]/.test(n)) snake += 1
  }
  if (camel > snake * 2) return 'camel'
  if (snake > camel * 2) return 'snake'
  return 'mixed'
}

const namingMismatch = computed(() => {
  if (!fieldPickerHasFields.value) return false
  const s = detectNamingStyle(sourceFields.value)
  const t = detectNamingStyle(targetFields.value)
  return s !== 'mixed' && t !== 'mixed' && s !== t
})

const hasMixedExcelSql = computed(
  () => taskDraft.source_kind !== taskDraft.target_kind
)

// 解析"src -> tgt"映射，校验左/右侧列名是否真存在 —— 避免用户写错列名
// 在 schema 都已提取后才做（否则误报）
const mappingIssues = computed(() => {
  if (!fieldPickerHasFields.value) return []
  const lines = String(taskDraft.column_mappings || '').split(/\n+/).map(l => l.trim()).filter(Boolean)
  const sourceSet = new Set(sourceFields.value.map(c => c.toLowerCase()))
  const targetSet = new Set(targetFields.value.map(c => c.toLowerCase()))
  const issues = []
  for (const line of lines) {
    if (!line.includes('->')) continue
    const [left, right] = line.split('->').map(s => s.trim())
    if (left && !sourceSet.has(left.toLowerCase())) {
      issues.push(`「${left}」不在源字段 —— 请检查 source 端的提取结果`)
    }
    if (right && !targetSet.has(right.toLowerCase())) {
      issues.push(`「${right}」不在目标字段 —— 请检查 target 端的提取结果`)
    }
  }
  return issues
})
</script>

<template>
  <section class="space-y-4">
    <!-- 跨模式预警面板 —— 只在交叉模式 (Excel↔SQL) 或字段命名风格不一致时显示 -->
    <div
      v-if="fieldPickerHasFields && (hasMixedExcelSql || namingMismatch || !intersectionRows.length)"
      class="card border-status-warning-bg/60 bg-status-warning-bg/20"
    >
      <div class="mb-2 flex items-center gap-2">
        <AlertTriangle class="h-4 w-4 text-status-warning" />
        <h3 class="text-sm font-semibold text-status-warning">混合输入预警</h3>
      </div>
      <ul class="space-y-1.5 text-[12px] text-slate-700">
        <li v-if="hasMixedExcelSql" class="flex items-start gap-2">
          <Info class="mt-0.5 h-3.5 w-3.5 shrink-0 text-status-info" />
          <span>
            源 / 目标分别是
            <span class="font-mono">{{ taskDraft.source_kind }}</span>
            和
            <span class="font-mono">{{ taskDraft.target_kind }}</span>
            —— 跨数据源对比；建议把字段映射 / 主键先做对齐再执行
          </span>
        </li>
        <li v-if="namingMismatch" class="flex items-start gap-2">
          <Info class="mt-0.5 h-3.5 w-3.5 shrink-0 text-status-warning" />
          <span>
            两侧字段命名风格不一致（CamelCase vs snake_case）—— 跨 Excel/SQL 时常见。
            如果两侧的列其实是同一个意义，请用「字段映射」对齐，或开启「忽略大小写」
          </span>
        </li>
        <li v-if="!intersectionRows.length" class="flex items-start gap-2">
          <AlertTriangle class="mt-0.5 h-3.5 w-3.5 shrink-0 text-status-error" />
          <span class="text-status-error">
            两侧字段没有同名交集 —— 当前会被全部当作"仅源/仅目标"列。
            必须用「字段映射」做对齐，否则没法做值比较
          </span>
        </li>
        <li v-else class="flex items-start gap-2">
          <Info class="mt-0.5 h-3.5 w-3.5 shrink-0 text-status-success" />
          <span>
            字段交集 <span class="font-mono">{{ intersectionRows.length }}</span> 个
            （仅源 {{ sourceOnlyRows.length }} · 仅目标 {{ targetOnlyRows.length }}）
          </span>
        </li>
      </ul>
    </div>

    <!-- 字段映射文本框 -->
    <div class="card">
      <div class="mb-3 flex items-center justify-between">
        <div>
          <h3 class="text-base font-semibold text-slate-800">字段映射</h3>
          <p class="muted text-[11px]">源字段名与目标字段名不一致时，每行写一条 `source_col -&gt; target_col`</p>
        </div>
      </div>
      <textarea
        v-model="taskDraft.column_mappings"
        class="min-h-[100px] bg-slate-50 sql-font text-sm"
        placeholder="例：&#10;src_id -&gt; tgt_id&#10;create_time -&gt; created_at"
      />
      <!-- 映射条目列名不在已提取字段里时的实时提示 -->
      <ul v-if="mappingIssues.length" class="mt-2 space-y-1 rounded-lg border border-status-warning-bg/60 bg-status-warning-bg/30 p-2 text-[11px] text-status-warning">
        <li v-for="(msg, i) in mappingIssues" :key="i" class="flex items-start gap-1.5">
          <AlertTriangle class="mt-0.5 h-3 w-3 shrink-0" />
          <span>{{ msg }}</span>
        </li>
      </ul>
    </div>

    <!-- 字段筛选器 -->
    <div class="card">
      <div class="mb-3 flex items-center justify-between">
        <div>
          <h3 class="text-base font-semibold text-slate-800">字段参与对比筛选</h3>
          <p class="muted text-[11px]">
            勾选 = 参与对比；取消 = 加入「忽略字段」。
            <span v-if="!fieldPickerHasFields" class="text-status-warning">
              请先在第 1 步「数据来源」点击两侧的「提取字段」加载列名
            </span>
          </p>
        </div>
        <div v-if="fieldPickerHasFields" class="flex gap-1.5">
          <button class="btn btn-ghost h-7 gap-1 px-2 text-[11px]" @click="fieldPickerSelectAll">
            <CheckSquare class="h-3 w-3" /> 全选
          </button>
          <button class="btn btn-ghost h-7 gap-1 px-2 text-[11px]" @click="fieldPickerExcludeOneSided">
            <Filter class="h-3 w-3" /> 仅交集
          </button>
        </div>
      </div>

      <div v-if="!fieldPickerHasFields" class="rounded-lg border border-dashed border-slate-200 p-6 text-center">
        <ArrowRight class="mx-auto h-5 w-5 -rotate-90 text-slate-300" />
        <p class="muted mt-2 text-xs">两侧字段都未提取，无法配置筛选</p>
        <p class="muted text-[11px]">回到「数据来源」点击「提取字段」</p>
      </div>

      <div v-else class="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
        <label
          v-for="row in fieldPickerRows"
          :key="row.key"
          class="flex cursor-pointer items-center justify-between gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm transition hover:border-primary/50"
        >
          <span class="flex min-w-0 items-center gap-2">
            <input
              type="checkbox"
              :checked="row.included"
              class="h-4 w-4 rounded text-primary focus:ring-primary"
              @change="toggleFieldIncluded(row.name)"
            >
            <span class="truncate sql-font text-xs text-slate-800">{{ row.name }}</span>
          </span>
          <span
            class="shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold"
            :class="row.onSource && row.onTarget
              ? 'bg-status-success-bg text-status-success'
              : (row.onSource ? 'bg-tag-source-bg text-tag-source' : 'bg-tag-intermediate-bg text-tag-intermediate')"
          >
            {{ row.onSource && row.onTarget ? '双侧' : (row.onSource ? '仅源' : '仅目标') }}
          </span>
        </label>
      </div>

      <div v-if="fieldPickerHasFields" class="muted mt-3 text-[11px]">
        <span class="text-slate-500">源 {{ sourceFields.length }} 列 · 目标 {{ targetFields.length }} 列</span>
        <span v-if="fieldPickerRows.length" class="ml-2">· 共 {{ fieldPickerRows.length }} 个字段，{{ fieldPickerRows.filter(r => r.included).length }} 个参与对比</span>
      </div>
    </div>
  </section>
</template>
