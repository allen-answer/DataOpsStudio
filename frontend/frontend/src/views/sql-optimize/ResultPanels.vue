<script setup lang="ts">
// materialize / record / verify / run-all 结果面板(Phase 14 P2 拆出)
import {
  CheckCircle2, Sparkles, ListChecks, ShieldCheck, Rocket, GitBranch,
} from 'lucide-vue-next'
import { useScenarioLabStore } from '../../stores/scenarioLab'
import type { MaterializeTableResult } from '../../types/sandbox'

const store = useScenarioLabStore()
</script>

<template>
  <!-- 一键链 banner -->
  <div
    v-if="store.runAllResult"
    class="card p-4 border-2"
    :class="store.runAllResult.ok ? 'border-status-success bg-status-success-bg' : 'border-status-error bg-status-error-bg'"
  >
    <div class="flex items-center gap-2 text-sm font-bold">
      <Rocket class="h-5 w-5" :class="store.runAllResult.ok ? 'text-status-success' : 'text-status-error'" />
      <span :class="store.runAllResult.ok ? 'text-status-success' : 'text-status-error'">
        {{ store.runAllResult.ok ? '一键链全套通过' : '一键链有失败步骤' }}
      </span>
    </div>
    <div class="mt-2 text-xs text-slate-700 flex flex-wrap gap-x-4 gap-y-1">
      <span v-if="store.runAllResult.ai_fill">
        AI 填充：{{ store.runAllResult.ai_fill.ok ? `${store.runAllResult.ai_fill.calls} 调用` : '跳过' }}
      </span>
      <span v-if="store.runAllResult.materialize">
        落库：{{ store.runAllResult.materialize.tables?.length || 0 }} 表
      </span>
      <span v-if="store.runAllResult.record">
        建任务：{{ store.runAllResult.record.tasks?.length || 0 }} 个
      </span>
      <span>
        运行：{{ store.runAllResult.runs.filter(r => r.ok).length }} / {{ store.runAllResult.runs.length }} ok
      </span>
      <span v-if="store.runAllResult.verify">
        校验：{{ store.runAllResult.verify.summary.pass }} pass · {{ store.runAllResult.verify.summary.fail }} fail · {{ store.runAllResult.verify.summary.skipped }} skipped
      </span>
    </div>
    <div v-if="store.runAllResult.error" class="mt-2 text-xs text-status-error">
      错误：{{ store.runAllResult.error }}
    </div>
    <div v-if="store.runAllResult.runs.some(r => !r.ok)" class="mt-2 text-xs">
      <div class="font-medium text-status-error mb-1">失败的 run：</div>
      <ul class="ml-2 space-y-0.5 text-slate-700">
        <li v-for="r in store.runAllResult.runs.filter(x => !x.ok)" :key="r.task_id">
          <span class="sql-font">{{ r.task_name }}</span> — {{ r.error }}
        </li>
      </ul>
    </div>
  </div>

  <!-- verify 回归校验结果 -->
  <div v-if="store.verifyResult" class="card border-slate-200 p-4">
    <div class="flex items-center justify-between flex-wrap gap-2 mb-3">
      <div class="text-sm font-bold text-slate-800 flex items-center gap-2">
        <ShieldCheck class="h-4 w-4 text-primary" />
        回归校验
      </div>
      <div class="flex gap-2 text-xs">
        <span class="pill bg-status-success-bg text-status-success">
          {{ store.verifyResult.summary.pass }} pass
        </span>
        <span class="pill bg-status-error-bg text-status-error">
          {{ store.verifyResult.summary.fail }} fail
        </span>
        <span class="pill bg-status-warning-bg text-status-warning">
          {{ store.verifyResult.summary.skipped }} skipped
        </span>
      </div>
    </div>
    <ul class="space-y-3">
      <li v-for="(r, i) in store.verifyResult.results" :key="i" class="rounded-lg border border-slate-200 p-3">
        <div class="flex items-center justify-between flex-wrap gap-2">
          <div class="flex items-center gap-2">
            <span class="pill text-xs" :class="store.statusBadgeClass(r.status)">
              {{ store.statusLabel(r.status) }}
            </span>
            <span class="font-medium text-slate-800 sql-font">{{ r.workload_name }}</span>
          </div>
          <button v-if="r.task_id" class="text-xs text-primary hover:underline" @click="store.gotoTask(r.task_id)">
            打开任务 →
          </button>
        </div>
        <div
          v-if="r.status === 'pass' || r.status === 'fail'"
          class="mt-2 grid grid-cols-2 md:grid-cols-4 gap-2 text-xs"
        >
          <div v-for="key in Object.keys(r.expected)" :key="key" class="rounded p-2 bg-slate-50">
            <div class="flex items-center justify-between">
              <span class="text-slate-500 sql-font">{{ key }}</span>
              <span
                v-if="(r.tolerance?.[key] || 0) > 0"
                class="px-1 rounded bg-slate-200 text-slate-600 sql-font text-[10px]"
                title="允许容差"
              >±{{ r.tolerance[key] }}</span>
            </div>
            <div class="mt-0.5 text-slate-700">
              expected {{ r.expected[key] }} →
              <span :class="Math.abs(r.deltas[key]) <= (r.tolerance?.[key] || 0) ? 'text-status-success font-medium' : 'text-status-error font-medium'">
                actual {{ r.actual[key] || 0 }}
                <span v-if="r.deltas[key] !== 0" class="sql-font">
                  ({{ r.deltas[key] > 0 ? '+' : '' }}{{ r.deltas[key] }})
                </span>
              </span>
            </div>
          </div>
        </div>
        <div v-else-if="r.status === 'no_run' && r.task_id" class="mt-2 text-xs text-slate-500">
          task <span class="sql-font">{{ r.task_name }}</span> 尚未跑过;
          <button class="text-primary hover:underline" @click="store.gotoTask(r.task_id)">
            去工作台运行 →
          </button>
        </div>
        <div v-else-if="r.status === 'no_task'" class="mt-2 text-xs text-slate-500">
          scenario 还没 record 对应的 CompareTask。点上方「建对比任务」补一次。
        </div>
        <div v-else-if="r.status === 'no_expected'" class="mt-2 text-xs text-slate-500">
          yml workload 没写 <code class="sql-font">expected:</code> 块;补上后即可纳入回归。
        </div>
      </li>
    </ul>
  </div>

  <!-- materialize result -->
  <div v-if="store.materializeResult" class="card border-status-success bg-status-success-bg p-4">
    <div class="flex items-center gap-2 text-status-success font-bold text-sm">
      <CheckCircle2 class="h-5 w-5" /> 数据已落库
    </div>
    <div
      v-if="store.materializeResult.ai_fill"
      class="mt-2 rounded bg-white p-2 text-xs flex items-start gap-2"
      :class="store.materializeResult.ai_fill.ok ? 'border border-primary' : 'border border-slate-200'"
    >
      <Sparkles class="h-3.5 w-3.5 mt-0.5 flex-shrink-0" :class="store.materializeResult.ai_fill.ok ? 'text-primary' : 'text-slate-400'" />
      <div class="flex-1">
        <div class="font-medium" :class="store.materializeResult.ai_fill.ok ? 'text-primary' : 'text-slate-500'">
          AI 填血肉
          <template v-if="store.materializeResult.ai_fill.ok">
            · {{ store.materializeResult.ai_fill.calls }} 个 LLM 调用 ·
            填了 {{ store.materializeResult.ai_fill.filled_columns.length }} 列样本池 +
            {{ (store.materializeResult.ai_fill.filled_distributions || []).length }} 列分布 +
            {{ store.materializeResult.ai_fill.filled_descriptions.length }} 表描述
          </template>
          <template v-else>· 跳过:{{ store.materializeResult.ai_fill.skipped_reason }}</template>
        </div>
        <div v-if="store.materializeResult.ai_fill.errors.length" class="mt-1 text-status-warning">
          ⚠ {{ store.materializeResult.ai_fill.errors.length }} 项失败:
          <span class="sql-font">{{ store.materializeResult.ai_fill.errors.join(' / ') }}</span>
        </div>
        <div v-if="store.materializeResult.ai_fill.filled_columns.length" class="mt-1 text-slate-500">
          样本池:<span class="sql-font">{{ store.materializeResult.ai_fill.filled_columns.join(', ') }}</span>
        </div>
        <div v-if="(store.materializeResult.ai_fill.filled_distributions || []).length" class="mt-1 text-slate-500">
          分布参数:<span class="sql-font">{{ store.materializeResult.ai_fill.filled_distributions.join(', ') }}</span>
        </div>
      </div>
    </div>
    <div class="mt-3 grid grid-cols-1 md:grid-cols-2 gap-2 text-xs">
      <div
        v-for="(rows, name) in (store.materializeResult.rows_generated || {})"
        :key="name"
        class="bg-white rounded p-2"
      >
        <div class="sql-font text-slate-800">{{ name }}</div>
        <div class="text-slate-500 mt-0.5">生成 {{ rows }} 行</div>
        <div
          v-for="t in store.materializeResult.tables.filter((x: MaterializeTableResult) => x.name === name)"
          :key="t.name"
          class="text-slate-500"
        >
          落库 {{ t.rows_inserted }} 行 · {{ t.indexes_created || 0 }} 索引
        </div>
      </div>
    </div>
    <div v-if="store.materializeResult.warnings?.length" class="mt-2 text-xs text-status-warning">
      warnings: {{ store.materializeResult.warnings.join(' / ') }}
    </div>
  </div>

  <!-- record result -->
  <div v-if="store.recordResult" class="card border-primary p-4">
    <div class="flex items-center gap-2 text-primary font-bold text-sm">
      <ListChecks class="h-5 w-5" /> 已创建 {{ store.recordResult.tasks.length }} 个对比任务
    </div>
    <ul v-if="store.recordResult.tasks.length" class="mt-3 space-y-1 text-sm">
      <li
        v-for="t in store.recordResult.tasks"
        :key="t.id"
        class="flex items-center justify-between bg-white rounded p-2"
      >
        <span class="sql-font text-slate-800">{{ t.name }}</span>
        <button class="text-xs text-primary hover:underline" @click="store.gotoTask(t.id)">
          打开任务 →
        </button>
      </li>
    </ul>
    <div v-if="store.recordResult.lineage_runs?.length" class="mt-3">
      <div class="flex items-center gap-2 text-sm font-medium text-slate-700 mb-1">
        <GitBranch class="h-4 w-4 text-primary" />
        血缘脚本入库({{ store.recordResult.lineage_runs.length }})
      </div>
      <ul class="space-y-1 text-sm">
        <li
          v-for="(r, i) in store.recordResult.lineage_runs"
          :key="i"
          class="flex items-center justify-between bg-white rounded p-2"
        >
          <div class="flex items-center gap-2 flex-1 min-w-0">
            <span
              class="pill text-[10px]"
              :class="r.ok ? 'bg-status-success-bg text-status-success' : 'bg-status-error-bg text-status-error'"
            >{{ r.ok ? '✓ 已分析' : '✗ 失败' }}</span>
            <span class="sql-font text-slate-800 truncate">{{ r.workload_name }}</span>
          </div>
          <div class="flex items-center gap-2">
            <code v-if="r.run_id" class="sql-font text-xs text-slate-400">{{ r.run_id.slice(-8) }}</code>
            <button
              v-if="r.ok"
              class="text-xs text-primary hover:underline whitespace-nowrap"
              @click="store.gotoHistory"
            >
              查看历史 →
            </button>
            <span v-else class="text-xs text-status-error">{{ r.error }}</span>
          </div>
        </li>
      </ul>
    </div>
    <div v-if="store.recordResult.warnings?.length" class="mt-3 text-xs text-status-warning">
      <div class="font-medium mb-1">⚠ 部分 workload 被跳过:</div>
      <ul class="ml-2 space-y-0.5">
        <li v-for="(w, idx) in store.recordResult.warnings" :key="idx">
          <code class="sql-font">{{ w.workload_name }}</code> — {{ w.reason }}
        </li>
      </ul>
    </div>
  </div>
</template>
