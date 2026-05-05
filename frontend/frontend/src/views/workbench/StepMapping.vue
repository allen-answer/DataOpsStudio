<script setup>
import { computed, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { AlertTriangle, Info, Sparkles, Check, X } from 'lucide-vue-next'
import FieldCachePanel from './FieldCachePanel.vue'
import { apiJson } from '../../api'
import { useNoticeStore } from '../../stores/notice'
import { useTaskStore } from '../../stores/task'

const taskStore = useTaskStore()
const { taskDraft } = taskStore
const { sourceFields, targetFields, fieldPickerRows, fieldPickerHasFields } = storeToRefs(taskStore)

const noticeStore = useNoticeStore()
const aiSuggesting = ref(false)
const aiSuggestions = ref([])  // [{source, target, confidence, reason, accepted}]
const aiUnmatched = ref([])

async function fetchAIMappingSuggestions() {
  if (!sourceFields.value.length || !targetFields.value.length) {
    noticeStore.setNotice('请先在第 1 步预览或提取字段')
    return
  }
  aiSuggesting.value = true
  try {
    const data = await apiJson('/api/ai/suggest-column-mapping', 'POST', {
      source_fields: sourceFields.value,
      target_fields: targetFields.value,
    })
    if (!data.ok) {
      noticeStore.setNotice(data.error || '推荐失败 —— 请确认 admin → AI 配置已启用 provider')
      aiSuggestions.value = []
      aiUnmatched.value = []
    } else {
      aiSuggestions.value = (data.mappings || []).map(m => ({ ...m, accepted: false }))
      aiUnmatched.value = data.unmatched || []
      if (!aiSuggestions.value.length) {
        noticeStore.setNotice('AI 没找到合适的映射 —— 字段命名差异太大或 unmatched 字段不在两侧交集里')
      }
    }
  } catch (err) {
    noticeStore.setNotice(`推荐失败：${err.message || err}`)
  } finally {
    aiSuggesting.value = false
  }
}

function applyAISuggestion(item) {
  const line = `${item.source} -> ${item.target}`
  const existing = String(taskDraft.column_mappings || '').split(/\n+/).map(l => l.trim())
  if (existing.includes(line)) {
    item.accepted = true
    return
  }
  taskDraft.column_mappings = [
    ...existing.filter(l => l && !l.startsWith(`${item.source} ->`)),
    line,
  ].join('\n')
  item.accepted = true
}

function applyAllAISuggestions() {
  aiSuggestions.value.forEach(applyAISuggestion)
  noticeStore.setNotice(`已应用 ${aiSuggestions.value.length} 条推荐`)
}

function dismissAISuggestion(item) {
  aiSuggestions.value = aiSuggestions.value.filter(x => x !== item)
}

function aiConfidenceClass(c) {
  return ({
    high:   'bg-status-success-bg text-status-success',
    medium: 'bg-status-info-bg text-status-info',
    low:    'bg-amber-100 text-amber-700',
  })[c] || 'bg-slate-100 text-slate-600'
}

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
        <h3 class="text-sm font-semibold text-status-warning">{{ $t('workbench.mapping.mixedInputWarning') }}</h3>
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
      <div class="mb-3 flex items-center justify-between gap-2">
        <div>
          <h3 class="text-base font-semibold text-slate-800">{{ $t('workbench.mapping.title') }}</h3>
          <p class="muted text-[11px]">源字段名与目标字段名不一致时，每行写一条 `source_col -&gt; target_col`</p>
        </div>
        <button
          class="btn btn-outline gap-1.5 h-9 px-3 text-xs"
          :disabled="aiSuggesting || !fieldPickerHasFields"
          :title="fieldPickerHasFields ? '让 AI 根据字段命名 + 样本值推荐映射' : '请先在第 1 步提取字段'"
          @click="fetchAIMappingSuggestions"
        >
          <Sparkles class="h-3.5 w-3.5" :class="aiSuggesting ? 'animate-pulse' : ''" />
          AI 推荐映射
        </button>
      </div>
      <textarea
        v-model="taskDraft.column_mappings"
        class="min-h-[100px] bg-slate-50 sql-font text-sm"
        :placeholder="$t('workbench.mapping.placeholder')"
      />
      <!-- 映射条目列名不在已提取字段里时的实时提示 -->
      <ul v-if="mappingIssues.length" class="mt-2 space-y-1 rounded-lg border border-status-warning-bg/60 bg-status-warning-bg/30 p-2 text-[11px] text-status-warning">
        <li v-for="(msg, i) in mappingIssues" :key="i" class="flex items-start gap-1.5">
          <AlertTriangle class="mt-0.5 h-3 w-3 shrink-0" />
          <span>{{ msg }}</span>
        </li>
      </ul>

      <!-- AI 推荐结果列表（紫色边卡片，逐条接受 / 全部应用） -->
      <div v-if="aiSuggestions.length" class="mt-3 rounded-lg border border-purple-200 bg-purple-50/40 p-3">
        <div class="mb-2 flex items-center justify-between">
          <div class="flex items-center gap-1.5">
            <Sparkles class="h-3.5 w-3.5 text-purple-600" />
            <span class="text-xs font-bold text-purple-800">AI 推荐 ({{ aiSuggestions.length }} 条)</span>
            <span v-if="aiUnmatched.length" class="muted text-[10px]">· 未匹配 {{ aiUnmatched.length }}</span>
          </div>
          <button class="text-[11px] text-purple-700 hover:underline" @click="applyAllAISuggestions">
            全部应用
          </button>
        </div>
        <ul class="space-y-1.5">
          <li
            v-for="(item, i) in aiSuggestions" :key="i"
            class="flex items-start gap-2 rounded-md bg-white px-2 py-1.5 text-[12px] ring-1 ring-purple-200"
            :class="item.accepted ? 'opacity-50' : ''"
          >
            <span class="sql-font shrink-0 rounded bg-slate-100 px-1.5 py-0.5">{{ item.source }}</span>
            <span class="muted shrink-0">→</span>
            <span class="sql-font shrink-0 rounded bg-purple-50 px-1.5 py-0.5 text-purple-800">{{ item.target }}</span>
            <span class="pill shrink-0 text-[9px]" :class="aiConfidenceClass(item.confidence)">{{ item.confidence }}</span>
            <span class="muted flex-1 text-[11px]">{{ item.reason }}</span>
            <button
              v-if="!item.accepted"
              class="shrink-0 rounded p-0.5 text-status-success hover:bg-status-success-bg"
              title="应用此推荐"
              @click="applyAISuggestion(item)"
            >
              <Check class="h-3.5 w-3.5" />
            </button>
            <span v-else class="text-[10px] text-status-success">✓ 已应用</span>
            <button
              class="shrink-0 rounded p-0.5 text-slate-400 hover:bg-slate-100 hover:text-rose-500"
              title="忽略"
              @click="dismissAISuggestion(item)"
            >
              <X class="h-3.5 w-3.5" />
            </button>
          </li>
        </ul>
        <p v-if="aiUnmatched.length" class="mt-2 text-[10.5px] text-purple-700">
          未匹配 source 字段：
          <span class="sql-font">{{ aiUnmatched.join(', ') }}</span>
        </p>
      </div>
    </div>

  </section>
</template>
