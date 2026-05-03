<script setup>
import { computed, inject } from 'vue'
import { AlertTriangle, Info } from 'lucide-vue-next'
import FieldCachePanel from './FieldCachePanel.vue'

const {
  taskDraft, sourceFields, targetFields,
  fieldPickerRows, fieldPickerHasFields,
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
    <FieldCachePanel
      title="字段缓存与参与范围"
      hint="这里复用第 1 步 SQL/Excel 预览得到的字段缓存；勾选状态会同步到忽略字段，主键设置也会同步到规则页。"
      compact
    />
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
            两侧字段没有同名交集 —— 未配置字段映射时会退回按位置映射。
            请确认左右 SELECT 字段顺序一致，或用「字段映射」明确对齐
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

  </section>
</template>
