<script setup lang="ts">
/**
 * Phase 14 #3 — Operation Risk Panel
 *
 * 选中 datasource 后展示其 environment + db_type + 8 个 allow_* flag 状态,
 * 给用户即时风险提示。仅 UX 层提示;后端 operation_policy 才是强制层。
 *
 * 用在 /sql-diagnosis(展示 SQL EXPLAIN 风险)和 /scenario-lab(展示写入红线)。
 *
 * Props:
 *   datasource: ApiDataSource | null  当前选中的 ds(null = 未选)
 *   context: 'sql-diagnosis' | 'scenario-lab' | 'schema-import'
 *           决定显示哪些 allow_* 状态 + 哪些禁止项
 */
import { computed } from 'vue'
import { AlertTriangle, ShieldCheck, ShieldAlert, Info } from 'lucide-vue-next'
import type { ApiDataSource } from '../../types/api'

const props = defineProps<{
  datasource: ApiDataSource | null
  context: 'sql-diagnosis' | 'scenario-lab' | 'schema-import'
}>()

const env = computed(() => {
  const e = ((props.datasource as any)?.environment as string) || 'unknown'
  return e
})

const dbType = computed(() => String(props.datasource?.db_type || '').toLowerCase())

const envBadgeClass = computed(() => {
  if (env.value === 'prod') return 'bg-status-error-bg text-status-error border-status-error'
  if (env.value === 'staging') return 'bg-status-warning-bg text-status-warning border-status-warning'
  if (env.value === 'sandbox') return 'bg-status-success-bg text-status-success border-status-success'
  return 'bg-status-pending-bg text-status-pending border-status-pending'
})

const envLabel = computed(() => {
  if (env.value === 'prod') return '🔴 PROD 生产'
  if (env.value === 'staging') return '🟡 STAGING 预发'
  if (env.value === 'sandbox') return '🟢 SANDBOX 沙盒'
  return '⚪ UNKNOWN 未确认环境'
})

// 用 any 是因为 ApiDataSource 的 allow_* 是后加的可选字段(types/api.ts augment)
function flag(name: string): boolean {
  return !!(props.datasource as any)?.[name]
}

// 当前 context 下显示的 allow_* 列表
const relevantFlags = computed(() => {
  if (props.context === 'sql-diagnosis') {
    const items = [{ key: 'allow_select', label: 'SELECT 查询(读)' }]
    if (dbType.value === 'mysql') items.push({ key: 'allow_explain', label: 'MySQL EXPLAIN' })
    else if (dbType.value === 'dm') items.push({ key: 'allow_dm_explain', label: 'DM EXPLAIN' })
    else if (dbType.value === 'oracle') items.push({ key: 'allow_oracle_plan_table', label: 'Oracle PLAN_TABLE 诊断写入' })
    return items
  }
  if (props.context === 'scenario-lab') {
    return [
      { key: 'allow_scenario_write', label: 'materialize / run-all 造数据' },
      { key: 'allow_record_task', label: 'record:CompareTask 落库' },
    ]
  }
  // schema-import
  return [
    { key: 'allow_schema_import', label: '读 information_schema 元数据' },
    { key: 'allow_schema_save', label: '保存 yml 到 config/scenarios' },
  ]
})

// 是否在 prod / staging — 显示禁止项 / 红线提示
const isProductionLike = computed(() => env.value === 'prod' || env.value === 'staging')

// 上下文相关的禁止项文案
const forbiddenItems = computed(() => {
  if (!isProductionLike.value) return []
  if (props.context === 'sql-diagnosis') {
    return [
      '业务 DML — INSERT / UPDATE / DELETE / MERGE',
      '业务 DDL — DROP / ALTER / TRUNCATE / CREATE',
      '事务 / 调用 — CALL / EXEC / BEGIN',
      'SELECT FOR UPDATE(会加行锁)',
      'scenario materialize / run-all / record',
    ]
  }
  if (props.context === 'scenario-lab') {
    return [
      'scenario materialize 造数据(无条件拒)',
      'run-all 全套(无条件拒)',
      'drop_first(无条件拒)',
      'record 落 CompareTask(无条件拒)',
    ]
  }
  return ['schema yml save(仅 sandbox 允许)']
})

// 方言特殊提示(DM EXPLAIN SELECT / Oracle PLAN_TABLE)
const dialectNote = computed(() => {
  if (props.context !== 'sql-diagnosis' || !isProductionLike.value) return ''
  if (dbType.value === 'mysql') {
    return 'MySQL 使用 EXPLAIN SELECT 查看执行计划,纯只读,不修改业务数据。本操作会记录审计。'
  }
  if (dbType.value === 'dm') {
    return 'DM 使用 EXPLAIN SELECT 查看执行计划,不修改业务数据,但会消耗优化器资源。本操作会记录审计。'
  }
  if (dbType.value === 'oracle') {
    return 'Oracle 使用 EXPLAIN PLAN FOR 查看执行计划,会向诊断表 PLAN_TABLE 写一行临时记录(非业务表)。本操作需要 DBA 允许 PLAN_TABLE 诊断写入,并会记录审计。'
  }
  return ''
})
</script>

<template>
  <div v-if="datasource" class="card p-4 space-y-3 border-2" :class="envBadgeClass.split(' ')[2]">
    <!-- 头部:环境标签 + 基础信息 -->
    <div class="flex items-center justify-between gap-3 flex-wrap">
      <div class="flex items-center gap-2">
        <ShieldCheck v-if="env === 'sandbox'" class="h-5 w-5 text-status-success" />
        <ShieldAlert v-else-if="env === 'unknown'" class="h-5 w-5 text-status-pending" />
        <AlertTriangle v-else class="h-5 w-5" :class="env === 'prod' ? 'text-status-error' : 'text-status-warning'" />
        <span class="font-bold" :class="envBadgeClass.split(' ').slice(0, 2).join(' ')">{{ envLabel }}</span>
        <span class="text-xs text-slate-500 sql-font">{{ dbType }}</span>
        <span v-if="(datasource as any).project_id" class="text-xs text-slate-500">
          · project: {{ (datasource as any).project_id.slice(0, 8) }}…
        </span>
        <span
          v-if="(datasource as any).environment_verified"
          class="pill bg-slate-100 text-slate-600 text-[10px]"
          title="admin 已确认此 datasource 的环境标签"
        >
          ✓ env verified
        </span>
        <span
          v-else
          class="pill bg-status-warning-bg text-status-warning text-[10px]"
          title="此 datasource 环境标签尚未被 admin 确认"
        >
          ⚠ env 未确认
        </span>
      </div>
    </div>

    <!-- unknown 环境硬阻止提示 -->
    <div
      v-if="env === 'unknown'"
      class="rounded p-3 bg-status-pending-bg text-slate-700 text-sm"
    >
      <div class="font-bold mb-1">⚠ 此数据源环境未确认</div>
      <p class="text-xs leading-relaxed">
        fail-safe 设计:未知环境的高风险操作一律拒绝。
        请到 <a href="#/datasources" class="text-primary hover:underline">数据源管理</a>
        确认环境标签 (sandbox / staging / prod) 后再操作。
      </p>
    </div>

    <!-- 方言特定说明 (DM EXPLAIN SELECT / Oracle PLAN_TABLE) -->
    <div
      v-if="dialectNote"
      class="rounded p-3 text-xs leading-relaxed"
      :class="env === 'prod' ? 'bg-status-error-bg/50 text-status-error' : 'bg-status-warning-bg/50 text-slate-700'"
    >
      <Info class="h-3.5 w-3.5 inline mr-1" />
      {{ dialectNote }}
    </div>

    <!-- allow_* 状态网格 -->
    <div v-if="env !== 'unknown'" class="grid grid-cols-1 sm:grid-cols-2 gap-2">
      <div
        v-for="f in relevantFlags"
        :key="f.key"
        class="flex items-center gap-2 text-xs rounded p-2"
        :class="flag(f.key)
          ? 'bg-status-success-bg/40 text-status-success'
          : 'bg-status-error-bg/40 text-status-error'"
      >
        <span class="text-base">{{ flag(f.key) ? '✓' : '✗' }}</span>
        <span class="font-mono text-[10px] text-slate-500">{{ f.key }}</span>
        <span class="flex-1">{{ f.label }}</span>
      </div>
    </div>

    <!-- prod/staging 禁止项 -->
    <details
      v-if="forbiddenItems.length"
      class="rounded bg-status-error-bg/30 p-3 text-xs"
    >
      <summary class="cursor-pointer font-bold text-status-error">
        🚫 此环境禁止 ({{ forbiddenItems.length }} 项)
      </summary>
      <ul class="mt-2 space-y-0.5 ml-4 list-disc text-slate-700">
        <li v-for="(item, i) in forbiddenItems" :key="i">{{ item }}</li>
      </ul>
    </details>
  </div>

  <div v-else class="card p-4 text-sm text-slate-500 italic">
    选一个 datasource 查看操作风险面板。
  </div>
</template>
