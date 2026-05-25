<script setup lang="ts">
/**
 * Phase 14 #3 (Round 2) — Operation Risk Panel
 *
 * 重构:默认显示业务语义(允许/禁止动作 + 方言说明 + 审计),技术 allow_*
 * 字段折到「高级展开」让 admin / 排错用。普通用户看默认就够。
 *
 * 仅 UX 层提示;后端 operation_policy 才是强制层。
 */
import { computed } from 'vue'
import { AlertTriangle, ShieldCheck, ShieldAlert, Info } from 'lucide-vue-next'
import type { ApiDataSource } from '../../types/api'

const props = defineProps<{
  datasource: ApiDataSource | null
  context: 'sql-diagnosis' | 'scenario-lab' | 'schema-import'
}>()

const env = computed<string>(() => ((props.datasource as any)?.environment as string) || 'unknown')
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

function flag(name: string): boolean {
  return !!(props.datasource as any)?.[name]
}

// ─── 业务语义层:此环境本次可做什么 / 不可做什么 ──────────────────────

const allowedActions = computed<string[]>(() => {
  if (!props.datasource || env.value === 'unknown') return []
  const list: string[] = []
  if (props.context === 'sql-diagnosis') {
    list.push('静态 SQL 检查(preflight,不连数据库)')
    list.push('AI 复核(LLM 提示,纯静态)')
    // 按方言 + flag 决定
    if (dbType.value === 'mysql' && (env.value === 'sandbox' || flag('allow_explain'))) {
      list.push('查看执行计划(MySQL EXPLAIN SELECT,纯只读)')
    }
    if (dbType.value === 'dm' && (env.value === 'sandbox' || flag('allow_dm_explain') || flag('allow_explain'))) {
      list.push('查看执行计划(DM EXPLAIN SELECT,纯只读)')
    }
    if (dbType.value === 'oracle' && (env.value === 'sandbox' || flag('allow_oracle_plan_table'))) {
      list.push('查看执行计划(Oracle EXPLAIN PLAN FOR,⚠ 写诊断 PLAN_TABLE)')
    }
    list.push('Plan history / Plan diff(本地比对,不连库)')
  } else if (props.context === 'scenario-lab') {
    if (env.value === 'sandbox' && flag('allow_scenario_write')) {
      list.push('造数据(materialize)')
      list.push('一键全套(run-all)')
    }
    if (env.value === 'sandbox' && flag('allow_record_task')) {
      list.push('建对比任务(record)')
    }
    list.push('回归校验(verify,只读)')
  } else {
    // schema-import
    if (env.value === 'sandbox' || flag('allow_schema_import')) {
      list.push('读 information_schema 元数据生成 yml(纯只读)')
    }
    if (env.value === 'sandbox' && flag('allow_schema_save')) {
      list.push('保存 yml 到 config/scenarios')
    }
  }
  return list
})

const forbiddenActions = computed<string[]>(() => {
  if (!props.datasource) return []
  if (env.value === 'unknown') {
    return ['❗ 任何高风险操作都被拒绝 — 请到数据源管理页确认环境标签']
  }
  if (env.value === 'sandbox') return []  // sandbox 内部禁止项不展示(降噪)
  // prod / staging
  const list: string[] = []
  if (props.context === 'sql-diagnosis') {
    list.push('业务 DML — INSERT / UPDATE / DELETE / MERGE')
    list.push('业务 DDL — DROP / ALTER / TRUNCATE / CREATE')
    list.push('事务 / 调用 — CALL / EXEC / BEGIN')
    list.push('SELECT FOR UPDATE(加行锁)')
    list.push('scenario 造数据 / record / run-all')
  } else if (props.context === 'scenario-lab') {
    list.push('造数据 materialize(无条件拒,即使翻开 allow_scenario_write)')
    list.push('一键全套 run-all(无条件拒)')
    list.push('record 落 CompareTask(无条件拒)')
    list.push('DROP 已存在(无条件拒)')
  } else {
    list.push('保存 yml 到 config/scenarios(仅 sandbox 允许)')
  }
  return list
})

// 方言说明
const dialectNote = computed(() => {
  if (props.context !== 'sql-diagnosis') return ''
  if (env.value !== 'prod' && env.value !== 'staging') return ''
  if (dbType.value === 'dm') {
    return 'DM 使用 EXPLAIN SELECT 查看执行计划,不修改业务数据,但会消耗优化器资源,并记录审计。'
  }
  if (dbType.value === 'oracle') {
    return 'Oracle 使用 EXPLAIN PLAN FOR 查看执行计划,会写诊断 PLAN_TABLE,不修改业务表,并记录审计。'
  }
  if (dbType.value === 'mysql') {
    return 'MySQL 使用 EXPLAIN SELECT 查看执行计划,纯只读,不修改业务数据,并记录审计。'
  }
  return ''
})

// 审计说明文案
const auditNote = computed(() => {
  if (!props.datasource || env.value === 'unknown') return ''
  return '所有操作记录审计:user_id / datasource_id / environment / operation / sql_hash(不存完整 SQL) / request_id。'
})

// 高级展开的技术字段
const allFlagsList = computed(() => [
  { key: 'environment_verified', label: 'admin 已确认环境标签' },
  { key: 'allow_select', label: '普通 SELECT 查询' },
  { key: 'allow_explain', label: 'MySQL EXPLAIN' },
  { key: 'allow_dm_explain', label: 'DM EXPLAIN' },
  { key: 'allow_oracle_plan_table', label: 'Oracle EXPLAIN PLAN + PLAN_TABLE' },
  { key: 'allow_schema_import', label: 'schema 元数据反查' },
  { key: 'allow_schema_save', label: '保存 yml 到本地' },
  { key: 'allow_scenario_write', label: 'scenario 写表数据' },
  { key: 'allow_record_task', label: 'record:CompareTask 落库' },
])
</script>

<template>
  <div v-if="datasource" class="card p-4 space-y-3 border-2" :class="envBadgeClass.split(' ')[2]">
    <!-- 头部:环境标签 + db_type -->
    <div class="flex items-center gap-2 flex-wrap">
      <ShieldCheck v-if="env === 'sandbox'" class="h-5 w-5 text-status-success" />
      <ShieldAlert v-else-if="env === 'unknown'" class="h-5 w-5 text-status-pending" />
      <AlertTriangle v-else class="h-5 w-5" :class="env === 'prod' ? 'text-status-error' : 'text-status-warning'" />
      <span class="font-bold" :class="envBadgeClass.split(' ').slice(0, 2).join(' ')">{{ envLabel }}</span>
      <span class="text-xs text-slate-500 sql-font">{{ dbType }}</span>
      <span v-if="(datasource as any).project_id" class="text-xs text-slate-500">
        · project: {{ (datasource as any).project_id.slice(0, 8) }}…
      </span>
    </div>

    <!-- unknown 警告 -->
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

    <!-- 业务语义层:本次允许 / 本次禁止 -->
    <div v-if="env !== 'unknown'" class="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
      <div class="rounded p-3 bg-status-success-bg/30">
        <div class="font-bold text-status-success text-xs uppercase tracking-wider mb-2">
          ✓ 本次允许 ({{ allowedActions.length }})
        </div>
        <ul v-if="allowedActions.length" class="space-y-1 text-xs text-slate-700">
          <li v-for="(a, i) in allowedActions" :key="i" class="flex items-start gap-1.5">
            <span class="text-status-success">•</span>
            <span>{{ a }}</span>
          </li>
        </ul>
        <p v-else class="text-xs text-slate-500 italic">
          此 datasource 未开任何对应 allow_* flag — admin 需先翻开
        </p>
      </div>
      <div class="rounded p-3 bg-status-error-bg/30">
        <div class="font-bold text-status-error text-xs uppercase tracking-wider mb-2">
          🚫 本次禁止 ({{ forbiddenActions.length }})
        </div>
        <ul v-if="forbiddenActions.length" class="space-y-1 text-xs text-slate-700">
          <li v-for="(a, i) in forbiddenActions" :key="i" class="flex items-start gap-1.5">
            <span class="text-status-error">×</span>
            <span>{{ a }}</span>
          </li>
        </ul>
        <p v-else class="text-xs text-slate-500 italic">
          {{ env === 'sandbox' ? '沙盒环境无产品红线禁止项' : '无' }}
        </p>
      </div>
    </div>

    <!-- 方言说明 -->
    <div
      v-if="dialectNote"
      class="rounded p-3 text-xs leading-relaxed flex items-start gap-2"
      :class="env === 'prod' ? 'bg-status-error-bg/40 text-slate-700' : 'bg-status-warning-bg/40 text-slate-700'"
    >
      <Info class="h-3.5 w-3.5 flex-shrink-0 mt-0.5" />
      <span>{{ dialectNote }}</span>
    </div>

    <!-- 审计说明 -->
    <div v-if="auditNote" class="text-[11px] text-slate-500 leading-relaxed border-t border-slate-100 pt-2">
      🛡 {{ auditNote }}
    </div>

    <!-- 高级展开:技术 allow_* 字段 -->
    <details v-if="env !== 'unknown'" class="text-xs">
      <summary class="cursor-pointer text-slate-500 hover:text-slate-700 select-none">
        ⚙ 高级:技术配置详情(admin / 排错用)
      </summary>
      <div class="mt-2 grid grid-cols-1 sm:grid-cols-2 gap-1.5">
        <div
          v-for="f in allFlagsList"
          :key="f.key"
          class="flex items-center gap-2 rounded p-1.5"
          :class="flag(f.key)
            ? 'bg-status-success-bg/40 text-status-success'
            : 'bg-slate-50 text-slate-500'"
        >
          <span>{{ flag(f.key) ? '✓' : '✗' }}</span>
          <span class="font-mono text-[10px] text-slate-500">{{ f.key }}</span>
          <span class="flex-1 text-[11px] text-slate-700">{{ f.label }}</span>
        </div>
      </div>
    </details>
  </div>

  <div v-else class="card p-4 text-sm text-slate-500 italic">
    选一个 datasource 查看操作风险面板。
  </div>
</template>
