<script setup lang="ts">
// SQL 优化沙盒 — 快速优化 mode(默认)。
// 不用 scenario 模板,直接选 datasource + 粘 SQL → EXPLAIN + AI 复核 + plan diff。
// Phase 14 修缮:解决用户反馈"必须选模板才能用"的 UX 问题。
import { Microscope, Sparkles, Play, AlertCircle, Database, ChevronRight, Trash2 } from 'lucide-vue-next'
import { useSqlDiagnosisStore } from '../../stores/sqlDiagnosis'

const store = useSqlDiagnosisStore()
</script>

<template>
  <div class="space-y-4">
    <!-- 顶部:简明引导 -->
    <div class="card p-4 border-primary bg-primary-light/10">
      <div class="text-sm font-bold text-primary flex items-center gap-2">
        <Microscope class="h-5 w-5" />
        快速 SQL 优化 · 不用配模板
      </div>
      <p class="text-xs text-slate-600 mt-1 leading-relaxed">
        选 datasource → 粘慢 SQL → 点分析。后端跑 EXPLAIN + 规则识别 + AI 复核;改写 SQL 后重跑会自动跟上次 plan 对比。
        历史按 (datasource, SQL 规范化 hash) 自动归组,改 SQL 内容(非格式)算新历史线。
      </p>
    </div>

    <!-- 配置区:datasource + SQL editor + 标签 -->
    <div class="card p-5 space-y-4">
      <div>
        <label class="block text-xs uppercase tracking-wider text-slate-500 font-bold mb-1">
          <Database class="h-3 w-3 inline" /> 目标 datasource
          <span class="text-[10px] font-normal normal-case tracking-normal text-slate-400">
            (MySQL / DM / Oracle,按方言查看执行计划)
          </span>
        </label>
        <select v-model="store.quickDatasourceId" class="w-full">
          <option value="" disabled>—— 选一个 ——</option>
          <option v-for="ds in store.diagnosableDatasources" :key="(ds as any).id" :value="(ds as any).id">
            {{ (ds as any).name }} · {{ (ds as any).db_type }} · {{ (ds as any).host }}:{{ (ds as any).port }}/{{ (ds as any).database }}
          </option>
        </select>
        <p v-if="!store.diagnosableDatasources.length" class="mt-1 text-xs text-status-warning">
          无可用 MySQL/DM/Oracle datasource —— 先去「数据源」页加一个。
        </p>
        <p class="mt-1 text-[11px] text-slate-500">
          选择 MySQL / DM / Oracle 数据源,系统将按方言查看执行计划。
          <span class="sql-font">MySQL/DM = EXPLAIN SELECT;Oracle = EXPLAIN PLAN FOR + PLAN_TABLE</span>
        </p>
      </div>

      <div>
        <label class="block text-xs uppercase tracking-wider text-slate-500 font-bold mb-1">
          慢 SQL(只读 SELECT;后端会拦 DML/DDL)
        </label>
        <textarea
          v-model="store.quickSql"
          rows="10"
          class="w-full sql-font text-sm"
          placeholder="-- 粘贴生产慢 SQL,例如:
SELECT o.id, o.amount, u.name
FROM orders o
JOIN users u ON u.id = o.user_id
WHERE o.status = 'pending'
  AND o.created_at > '2026-01-01'
ORDER BY o.created_at DESC
LIMIT 100"
        ></textarea>
      </div>

      <div class="flex items-center gap-3 text-xs">
        <label class="block flex-1">
          <span class="text-slate-500 font-semibold">归组标签(可选)</span>
          <input
            v-model="store.quickTagScenarioId"
            placeholder="给 plan history 打个标签,如 orders-prod-perf"
            class="mt-1 w-full"
          />
        </label>
        <span class="text-slate-400 mt-4">
          留空就只按 (datasource, SQL hash) 归组
        </span>
      </div>

      <div class="flex flex-wrap gap-2 border-t border-slate-100 pt-4">
        <button
          class="btn btn-primary"
          :disabled="!store.quickDatasourceId || !store.quickSql.trim() || store.quickAnalyzing"
          @click="store.runQuickAnalyze"
        >
          <Microscope class="h-4 w-4" :class="{ 'animate-pulse': store.quickAnalyzing }" />
          {{ store.quickAnalyzing ? '分析中…' : '🔬 跑 EXPLAIN + 规则' }}
        </button>
        <button
          v-if="store.quickResult"
          class="btn btn-outline"
          :disabled="store.quickEnriching"
          @click="store.runQuickEnrich"
        >
          <Sparkles class="h-4 w-4" :class="{ 'animate-pulse': store.quickEnriching }" />
          {{ store.quickEnriching ? 'AI 复核中…' : '✨ AI 复核' }}
        </button>
        <button
          v-if="store.quickResult?.history_id"
          class="btn btn-outline"
          :disabled="store.quickPlanDiffLoading"
          @click="store.runQuickPlanDiff"
          title="跟最近一次相同 SQL 的 plan 对比"
        >
          <Play class="h-4 w-4" />
          {{ store.quickPlanDiffLoading ? '对比中…' : '📊 跟上次对比' }}
        </button>
        <button
          v-if="store.quickResult"
          class="btn btn-outline text-status-error"
          @click="store.clearQuickAnalysis"
        >
          <Trash2 class="h-3.5 w-3.5" /> 清空结果
        </button>
      </div>
    </div>

    <!-- 错误 -->
    <div v-if="store.quickError" class="card border-status-error bg-status-error-bg p-3 text-sm text-status-error flex items-start gap-2">
      <AlertCircle class="h-4 w-4 mt-0.5 flex-shrink-0" />
      <span>{{ store.quickError }}</span>
    </div>

    <!-- 分析结果 + AI 复核 + plan diff -->
    <template v-if="store.quickResult">
      <!-- EXPLAIN + rule issues + suggestions -->
      <div class="card p-4">
        <div class="text-sm font-bold text-slate-800 mb-2 flex items-center gap-2">
          🔬 EXPLAIN 实测
          <span v-if="store.quickResult.sql_hash" class="ml-auto text-[10px] text-slate-400 sql-font">
            sql_hash:{{ store.quickResult.sql_hash.slice(0, 12) }}…
          </span>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div class="rounded bg-slate-50 p-3">
            <div class="text-xs font-bold text-slate-600 mb-1">规则发现({{ store.quickResult.issues.length }})</div>
            <div v-if="!store.quickResult.issues.length" class="text-xs text-slate-500">✓ 规则层未发现问题</div>
            <ul v-else class="space-y-1 text-xs text-slate-700">
              <li v-for="(it, i) in store.quickResult.issues" :key="i">
                <span class="pill bg-status-warning-bg text-status-warning text-[10px]">{{ it.code }}</span>
                {{ it.message }}
              </li>
            </ul>
          </div>
          <div class="rounded bg-slate-50 p-3">
            <div class="text-xs font-bold text-slate-600 mb-1">优化建议({{ store.quickResult.suggestions.length }})</div>
            <div v-if="!store.quickResult.suggestions.length" class="text-xs text-slate-500">无</div>
            <ul v-else class="space-y-1 text-xs text-slate-700">
              <li v-for="(s, i) in store.quickResult.suggestions" :key="i">✨ {{ s.message }}</li>
            </ul>
          </div>
        </div>

        <!-- EXPLAIN raw rows -->
        <div v-if="store.quickResult.plan?.length" class="mt-3 rounded border border-slate-200 overflow-x-auto">
          <div class="px-3 py-1.5 text-xs font-bold text-slate-600 border-b border-slate-200 bg-slate-50">
            EXPLAIN 输出({{ store.quickResult.plan.length }} 行)
          </div>
          <table class="w-full text-xs sql-font">
            <thead>
              <tr class="bg-slate-50">
                <th v-for="col in store.planColumns(store.quickResult.plan)" :key="col"
                    class="text-left px-2 py-1.5 font-medium text-slate-600 border-b border-slate-200">
                  {{ col }}
                </th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, ri) in store.quickResult.plan" :key="ri" class="border-b border-slate-100 hover:bg-slate-50">
                <td v-for="col in store.planColumns(store.quickResult.plan)" :key="col"
                    class="px-2 py-1.5 text-slate-700">
                  {{ row[col] == null ? '·' : String(row[col]) }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- AI 复核 -->
      <div v-if="store.quickEnrichResult" class="card border-2 border-primary p-4 space-y-3">
        <div class="text-sm font-bold text-primary flex items-center gap-2">
          <Sparkles class="h-4 w-4" />
          AI 复核 · <span class="sql-font text-xs text-slate-500">{{ store.quickEnrichResult.provider }}/{{ store.quickEnrichResult.model }}</span>
        </div>
        <div v-if="!store.quickEnrichResult.ok" class="text-xs text-slate-500 italic">
          {{ store.quickEnrichResult.error || 'AI provider 未启用,admin → AI 配置中开启' }}
        </div>
        <template v-else>
          <div v-if="store.quickEnrichResult.summary" class="text-sm text-slate-700">
            {{ store.quickEnrichResult.summary }}
          </div>
          <div v-if="store.quickEnrichResult.issue_review.length" class="space-y-1">
            <div class="text-xs font-bold text-slate-600">规则 issue 复核:</div>
            <ul class="space-y-1 text-xs">
              <li v-for="(rev, i) in store.quickEnrichResult.issue_review" :key="i" class="flex items-start gap-2">
                <span class="pill text-[10px]" :class="store.verdictBadgeClass(rev.verdict)">{{ rev.verdict }}</span>
                <span class="text-slate-700"><span class="sql-font text-slate-500">{{ rev.code }}</span> — {{ rev.rationale }}</span>
              </li>
            </ul>
          </div>
          <div v-if="store.quickEnrichResult.extra_suggestions.length" class="space-y-1">
            <div class="text-xs font-bold text-slate-600">AI 补充建议:</div>
            <ul class="space-y-2 text-xs">
              <li v-for="(ex, i) in store.quickEnrichResult.extra_suggestions" :key="i">
                <div class="flex items-start gap-2">
                  <span class="pill text-[10px]" :class="store.confidenceBadgeClass(ex.confidence)">{{ ex.confidence || '—' }}</span>
                  <span class="text-slate-700">{{ ex.message }}</span>
                </div>
                <pre v-if="ex.sql" class="mt-1 ml-12 px-2 py-1 bg-slate-50 border border-slate-200 rounded text-[11px] sql-font text-slate-700 whitespace-pre-wrap">{{ ex.sql }}</pre>
              </li>
            </ul>
          </div>
        </template>
      </div>

      <!-- Plan diff -->
      <div v-if="store.quickPlanDiffError || store.quickPlanDiff" class="card border border-primary-light bg-primary-light/10 p-3 text-xs">
        <div class="font-semibold text-primary mb-2">📊 plan diff</div>
        <div v-if="store.quickPlanDiffError" class="text-status-warning">{{ store.quickPlanDiffError }}</div>
        <div v-if="store.quickPlanDiff" class="space-y-2">
          <div class="font-bold rounded p-2"
               :class="store.isPlanDiffImproved(store.quickPlanDiff)
                 ? 'bg-status-success-bg text-status-success'
                 : store.isPlanDiffRegressed(store.quickPlanDiff)
                 ? 'bg-status-error-bg text-status-error'
                 : 'bg-slate-100 text-slate-700'">
            {{ store.quickPlanDiff.diff.summary }}
          </div>
          <div class="grid grid-cols-3 gap-2 text-center">
            <div class="rounded bg-slate-100 p-2">
              <div class="font-bold text-slate-700">{{ store.quickPlanDiff.diff.rows_delta.a.toLocaleString() }}</div>
              <div class="muted text-[10px]">上次 max-rows</div>
            </div>
            <div class="rounded bg-slate-100 p-2">
              <div class="font-bold text-slate-700">{{ store.quickPlanDiff.diff.rows_delta.b.toLocaleString() }}</div>
              <div class="muted text-[10px]">本次 max-rows</div>
            </div>
            <div class="rounded p-2"
                 :class="store.quickPlanDiff.diff.rows_delta.change < 0 ? 'bg-status-success-bg text-status-success' : store.quickPlanDiff.diff.rows_delta.change > 0 ? 'bg-status-error-bg text-status-error' : 'bg-slate-100 text-slate-700'">
              <div class="font-bold">{{ store.quickPlanDiff.diff.rows_delta.change > 0 ? '+' : '' }}{{ store.quickPlanDiff.diff.rows_delta.change.toLocaleString() }}</div>
              <div class="text-[10px]">差值</div>
            </div>
          </div>
          <div v-if="store.quickPlanDiff.diff.issues_resolved.length || store.quickPlanDiff.diff.issues_introduced.length" class="grid grid-cols-2 gap-2">
            <div v-if="store.quickPlanDiff.diff.issues_resolved.length" class="rounded bg-status-success-bg p-2">
              <div class="text-status-success font-bold mb-0.5">✓ 修复({{ store.quickPlanDiff.diff.issues_resolved.length }})</div>
              <ul class="text-slate-700">
                <li v-for="c in store.quickPlanDiff.diff.issues_resolved" :key="c">• {{ c }}</li>
              </ul>
            </div>
            <div v-if="store.quickPlanDiff.diff.issues_introduced.length" class="rounded bg-status-error-bg p-2">
              <div class="text-status-error font-bold mb-0.5">✗ 新引入({{ store.quickPlanDiff.diff.issues_introduced.length }})</div>
              <ul class="text-slate-700">
                <li v-for="c in store.quickPlanDiff.diff.issues_introduced" :key="c">• {{ c }}</li>
              </ul>
            </div>
          </div>
        </div>
      </div>

      <!-- Plan history(同 SQL 历次跑过的列表) -->
      <div v-if="store.quickPlanHistory.length > 1" class="card p-3">
        <div class="text-xs font-bold text-slate-600 mb-2">
          📋 同 SQL plan history({{ store.quickPlanHistory.length }} 条)
        </div>
        <ul class="space-y-1 text-xs">
          <li v-for="(h, i) in store.quickPlanHistory" :key="h.id"
              class="flex items-center justify-between bg-slate-50 rounded p-2">
            <span class="flex items-center gap-2">
              <span class="pill text-[10px] bg-slate-200 text-slate-700">#{{ h.id }}</span>
              <span class="sql-font text-slate-500">{{ h.ts }}</span>
              <span v-if="i === 0" class="text-primary font-medium">← 本次</span>
            </span>
            <span class="text-slate-500 text-[11px]">
              {{ (h.issues || []).length }} issues · plan {{ (h.plan || []).length }} steps
            </span>
          </li>
        </ul>
      </div>
    </template>
  </div>
</template>
