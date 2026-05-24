<script setup lang="ts">
/**
 * Phase 14 #3 — Operation Preview Modal
 *
 * 在 /sql-diagnosis 点"查看执行计划"前显示。比 confirm() 信息密度高:
 * - datasource 名称 + environment + db_type
 * - 将执行的动作(方言对应 EXPLAIN 命令)
 * - 是否修改业务数据 / 是否写诊断表
 * - 审计字段说明
 * - 用户必须勾选 confirm checkbox 才能继续
 *
 * 用法:
 *   const modal = ref(false)
 *   const resolver = ref<(v: boolean) => void>()
 *   function ask(): Promise<boolean> {
 *     return new Promise((res) => { resolver.value = res; modal.value = true })
 *   }
 *   // 在 modal close 时 resolver.value(userConfirmed)
 */
import { computed, ref, watch } from 'vue'
import { AlertTriangle, ShieldCheck } from 'lucide-vue-next'
import type { ApiDataSource } from '../../types/api'

const props = defineProps<{
  open: boolean
  datasource: ApiDataSource | null
}>()

const emit = defineEmits<{
  (e: 'close', confirmed: boolean): void
}>()

const acknowledged = ref(false)

// 每次打开重置 checkbox
watch(() => props.open, (v) => {
  if (v) acknowledged.value = false
})

const dbType = computed(() => String(props.datasource?.db_type || '').toLowerCase())
const env = computed(() => ((props.datasource as any)?.environment as string) || 'unknown')

// 将执行的动作
const action = computed(() => {
  if (dbType.value === 'mysql') return 'EXPLAIN SELECT'
  if (dbType.value === 'dm') return 'EXPLAIN SELECT'
  if (dbType.value === 'oracle') return 'EXPLAIN PLAN FOR + SELECT PLAN_TABLE'
  return '(未知方言)'
})

// 是否写诊断表
const writesDiagTable = computed(() => dbType.value === 'oracle')

// 风险颜色
const riskBgClass = computed(() => {
  if (env.value === 'prod') return 'border-status-error bg-status-error-bg/30'
  if (env.value === 'staging') return 'border-status-warning bg-status-warning-bg/30'
  return 'border-status-info bg-status-info-bg/30'
})

function onCancel() {
  emit('close', false)
}
function onConfirm() {
  if (!acknowledged.value) return
  emit('close', true)
}
</script>

<template>
  <!-- 模态 overlay — 简单实现,无第三方 modal lib(项目风格) -->
  <div
    v-if="open"
    class="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4"
    @click.self="onCancel"
  >
    <div class="card max-w-2xl w-full p-6 space-y-4 max-h-[90vh] overflow-y-auto">
      <!-- Header -->
      <div class="flex items-center gap-2 border-b border-slate-200 pb-3">
        <AlertTriangle v-if="env === 'prod' || env === 'staging'" class="h-6 w-6 text-status-error" />
        <ShieldCheck v-else class="h-6 w-6 text-status-info" />
        <h3 class="text-lg font-bold text-slate-800">即将执行操作 — 请确认</h3>
      </div>

      <!-- Datasource + env -->
      <div class="rounded p-3 border-2" :class="riskBgClass">
        <div class="grid grid-cols-3 gap-3 text-sm">
          <div>
            <div class="text-[10px] uppercase tracking-wider text-slate-500 font-bold">Datasource</div>
            <div class="font-mono text-slate-800">{{ datasource?.name || '—' }}</div>
          </div>
          <div>
            <div class="text-[10px] uppercase tracking-wider text-slate-500 font-bold">Environment</div>
            <div
              class="font-bold"
              :class="env === 'prod' ? 'text-status-error'
                    : env === 'staging' ? 'text-status-warning'
                    : env === 'sandbox' ? 'text-status-success'
                    : 'text-slate-500'"
            >
              {{ env.toUpperCase() }}
            </div>
          </div>
          <div>
            <div class="text-[10px] uppercase tracking-wider text-slate-500 font-bold">DB Type</div>
            <div class="font-mono text-slate-800">{{ dbType }}</div>
          </div>
        </div>
      </div>

      <!-- 将执行的动作 -->
      <div>
        <div class="text-xs uppercase tracking-wider text-slate-500 font-bold mb-1">将执行的动作</div>
        <pre class="px-3 py-2 bg-slate-900 text-green-300 rounded text-xs sql-font whitespace-pre-wrap">{{ action }}</pre>
      </div>

      <!-- 影响声明 -->
      <div class="space-y-2 text-sm">
        <div class="grid grid-cols-[140px_1fr] gap-2 items-center">
          <span class="text-slate-500">是否修改业务数据</span>
          <span class="font-bold text-status-success">否 — 纯只读 / EXPLAIN 不执行查询</span>
        </div>
        <div class="grid grid-cols-[140px_1fr] gap-2 items-center">
          <span class="text-slate-500">是否写诊断表</span>
          <span v-if="writesDiagTable" class="font-bold text-status-warning">
            是 — 会向 PLAN_TABLE 写一行临时记录(非业务表)
          </span>
          <span v-else class="font-bold text-status-success">否</span>
        </div>
        <div class="grid grid-cols-[140px_1fr] gap-2 items-start">
          <span class="text-slate-500 pt-0.5">审计</span>
          <div class="text-xs text-slate-700 leading-relaxed">
            本操作会记录审计事件 <code class="sql-font text-primary">sql.explain_*.allowed</code>,
            字段包含:user_id、datasource_id、environment、db_type、sql_hash(SHA-256,
            <b>不记录完整 SQL</b>)、request_id、timestamp。admin 可在审计页追溯。
          </div>
        </div>
      </div>

      <!-- 方言提示 -->
      <div
        v-if="dbType === 'dm'"
        class="rounded p-3 bg-status-info-bg/40 text-status-info text-xs leading-relaxed"
      >
        💡 DM 使用 <code class="sql-font">EXPLAIN SELECT</code> 查看执行计划,
        不修改业务数据,但会消耗优化器资源。
      </div>
      <div
        v-if="dbType === 'oracle'"
        class="rounded p-3 bg-status-warning-bg/40 text-slate-700 text-xs leading-relaxed"
      >
        ⚠ Oracle 使用 <code class="sql-font">EXPLAIN PLAN FOR</code> + 读 PLAN_TABLE。
        该操作会向 PLAN_TABLE 写一行临时记录(非业务表),不修改业务表。
        需要 DBA 授权该 datasource 的 <code class="sql-font">allow_oracle_plan_table=True</code>。
      </div>

      <!-- 确认 checkbox -->
      <label class="flex items-start gap-2 p-3 rounded border border-slate-200 hover:bg-slate-50 cursor-pointer text-sm">
        <input
          v-model="acknowledged"
          type="checkbox"
          class="mt-0.5"
        />
        <span class="flex-1">
          我已了解此操作的影响范围和审计记录,确认继续。
        </span>
      </label>

      <!-- Buttons -->
      <div class="flex justify-end gap-2 border-t border-slate-200 pt-4">
        <button class="btn btn-outline" @click="onCancel">取消</button>
        <button
          class="btn btn-primary"
          :disabled="!acknowledged"
          @click="onConfirm"
        >
          确认执行
        </button>
      </div>
    </div>
  </div>
</template>
