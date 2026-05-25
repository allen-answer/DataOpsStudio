<script setup lang="ts">
// 慢 SQL 分析结果(分析 + AI 复核 + plan diff + EXPLAIN 表)的 per-workload 卡片
// (Phase 14 P2 拆出 —— 这是 view 里最大一块)
import {
  Microscope, AlertCircle, Sparkles,
} from 'lucide-vue-next'
import { useScenarioLabStore } from '../../stores/scenarioLab'

const store = useScenarioLabStore()
</script>

<template>
  <template v-if="store.detail">
    <template v-for="(w, idx) in store.detail.workloads" :key="`slow-${idx}`">
      <div
        v-if="w.kind === 'slow_query' && (store.slowSqlResults[idx] || store.slowSqlErrors[idx]) && store.slowSqlExpanded[idx]"
        class="card p-4 space-y-4"
      >
        <div class="flex items-center justify-between">
          <div class="text-sm font-bold text-slate-800 flex items-center gap-2">
            <Microscope class="h-4 w-4 text-primary" />
            慢 SQL 分析 · <span class="sql-font">{{ w.name }}</span>
          </div>
          <button
            class="text-xs text-slate-500 hover:text-slate-700"
            @click="store.toggleSlowSqlExpansion(idx)"
          >
            收起
          </button>
        </div>

        <!-- 错误态 -->
        <div
          v-if="store.slowSqlErrors[idx]"
          class="rounded-lg border border-status-error bg-status-error-bg p-3 text-sm text-status-error flex items-start gap-2"
        >
          <AlertCircle class="h-4 w-4 mt-0.5 flex-shrink-0" />
          <span>{{ store.slowSqlErrors[idx] }}</span>
        </div>

        <!-- 成功态:左 expected / 右 actual -->
        <div v-if="store.slowSqlResults[idx]" class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div class="rounded-lg border border-slate-200 bg-slate-50 p-3">
            <div class="text-xs uppercase tracking-wider text-slate-500 font-bold mb-2">
              🎯 期望发现(来自 yml)
            </div>
            <div v-if="w.intentional_issues?.length">
              <div class="text-xs font-medium text-slate-600 mb-1">设计的问题:</div>
              <ul class="space-y-1 text-xs text-slate-700">
                <li v-for="(it, i) in w.intentional_issues" :key="`ii-${i}`">• {{ it }}</li>
              </ul>
            </div>
            <div v-if="w.expected_optimizations?.length" class="mt-3">
              <div class="text-xs font-medium text-slate-600 mb-1">期望优化:</div>
              <ul class="space-y-1 text-xs text-slate-700">
                <li v-for="(it, i) in w.expected_optimizations" :key="`eo-${i}`">• {{ it }}</li>
              </ul>
            </div>
            <div v-if="!w.intentional_issues?.length && !w.expected_optimizations?.length"
              class="text-xs text-slate-400">
              workload 未声明 intentional_issues / expected_optimizations
            </div>
          </div>

          <div class="rounded-lg border border-primary bg-primary-light/30 p-3">
            <div class="text-xs uppercase tracking-wider text-primary font-bold mb-2">
              🔬 EXPLAIN 实测
            </div>
            <div v-if="store.slowSqlResults[idx].issues.length">
              <div class="text-xs font-medium text-slate-700 mb-1">实测发现问题:</div>
              <ul class="space-y-1 text-xs text-slate-700">
                <li v-for="(it, i) in store.slowSqlResults[idx].issues" :key="`is-${i}`">
                  <span class="pill bg-status-warning-bg text-status-warning text-[10px]">{{ it.code }}</span>
                  {{ it.message }}
                </li>
              </ul>
            </div>
            <div v-else class="text-xs text-slate-500">✓ 规则层未发现问题</div>
            <div v-if="store.slowSqlResults[idx].suggestions.length" class="mt-3">
              <div class="text-xs font-medium text-slate-700 mb-1">优化建议:</div>
              <ul class="space-y-1 text-xs text-slate-700">
                <li v-for="(s, i) in store.slowSqlResults[idx].suggestions" :key="`sg-${i}`">
                  ✨ {{ s.message }}
                </li>
              </ul>
            </div>
          </div>
        </div>

        <!-- 实质数据 schema_context:表行数 / 现有索引 / WHERE+JOIN 列 / 具体 CREATE INDEX DDL
             不依赖 LLM,后端 sqlglot 解析 SQL + introspect schema 产出,给 actionable 数据 -->
        <div
          v-if="store.slowSqlResults[idx]?.schema_context?.length"
          class="rounded-lg border border-status-info bg-status-info-bg/30 p-3 space-y-3"
        >
          <div class="text-sm font-bold text-status-info flex items-center gap-1.5">
            📊 schema 上下文 + 具体 DDL 候选
            <span class="text-[10px] font-normal text-slate-500 normal-case">
              规则建议的实质化扩展(无 LLM)
            </span>
          </div>
          <div
            v-for="(ctx, i) in store.slowSqlResults[idx].schema_context"
            :key="`ctx-${i}`"
            class="rounded border border-slate-200 bg-white p-3 space-y-2"
          >
            <div class="flex items-center gap-2 flex-wrap text-sm">
              <span class="pill bg-primary-light text-primary sql-font">
                {{ ctx.schema ? `${ctx.schema}.${ctx.table}` : ctx.table }}
              </span>
              <span v-if="ctx.table_row_count !== null" class="text-xs text-slate-600">
                ≈ {{ ctx.table_row_count.toLocaleString() }} 行
              </span>
              <span class="text-xs text-slate-500">
                · {{ ctx.existing_indexes.length }} 个现有索引
              </span>
            </div>
            <div v-if="ctx.rationale" class="text-xs text-slate-600 leading-relaxed">
              {{ ctx.rationale }}
            </div>
            <div v-if="ctx.existing_indexes.length" class="text-xs">
              <span class="text-slate-500 font-medium">现有索引:</span>
              <span
                v-for="(idx, j) in ctx.existing_indexes"
                :key="`idx-${i}-${j}`"
                class="ml-1 inline-block pill bg-slate-100 text-slate-700 text-[10px] sql-font"
                :title="idx.unique ? 'UNIQUE' : '非唯一'"
              >
                {{ idx.name }}({{ idx.columns.join(', ') }})
              </span>
            </div>
            <div v-if="ctx.ddl_candidates.length" class="space-y-1">
              <div class="text-xs font-medium text-slate-700">📝 建议 DDL(可直接执行):</div>
              <pre
                v-for="(ddl, j) in ctx.ddl_candidates"
                :key="`ddl-${i}-${j}`"
                class="px-2 py-1.5 bg-slate-900 text-green-300 rounded text-[11px] sql-font whitespace-pre-wrap break-all"
              >{{ ddl }}</pre>
            </div>
            <div
              v-else-if="ctx.uncovered_columns.length"
              class="text-xs text-status-warning italic"
            >
              ⚠ 关键列被函数包(CASE/TRIM 等),普通 BTree 索引无效;需改写 SQL
              或用方言函数索引(Oracle/DM 支持)
            </div>

            <!-- Phase 14 #2 lineage 影响:加索引会拖慢谁 / 受益于谁 -->
            <details
              v-if="ctx.ddl_candidates.length && (ctx.writers_affected?.length || ctx.readers_helped?.length)"
              class="text-xs"
            >
              <summary class="cursor-pointer font-medium text-slate-700 hover:text-primary">
                <span v-if="ctx.writers_affected?.length" class="text-status-warning">
                  ⚠ 影响 {{ ctx.writers_affected.length }} 个 ETL 写入
                </span>
                <span v-if="ctx.writers_affected?.length && ctx.readers_helped?.length"> · </span>
                <span v-if="ctx.readers_helped?.length" class="text-status-success">
                  ✓ 受益于 {{ ctx.readers_helped.length }} 个读取脚本
                </span>
                <span v-if="ctx.refresh_mode" class="ml-2 text-slate-500">
                  (refresh_mode: <span class="sql-font">{{ ctx.refresh_mode }}</span>)
                </span>
              </summary>
              <div class="mt-2 grid grid-cols-1 md:grid-cols-2 gap-2 pl-3">
                <!-- writers -->
                <div v-if="ctx.writers_affected?.length" class="space-y-1">
                  <div class="text-status-warning font-bold text-[11px]">
                    ⚠ 加索引会拖慢 ({{ ctx.writers_affected.length }}):
                  </div>
                  <ul class="space-y-0.5">
                    <li
                      v-for="(w, k) in ctx.writers_affected"
                      :key="`w-${i}-${k}`"
                      class="text-[11px] text-slate-700"
                    >
                      <span class="pill bg-status-warning-bg text-status-warning text-[9px]">
                        {{ w.kind === 'lineage_script' ? '脚本' : w.kind }}
                      </span>
                      <a
                        v-if="w.run_id"
                        :href="`#/workflow-runs/${w.run_id}`"
                        class="ml-1 text-primary hover:underline sql-font"
                      >{{ w.name }} →</a>
                      <span v-else class="ml-1 sql-font">{{ w.name }}</span>
                    </li>
                  </ul>
                </div>
                <!-- readers -->
                <div v-if="ctx.readers_helped?.length" class="space-y-1">
                  <div class="text-status-success font-bold text-[11px]">
                    ✓ 可能受益 ({{ ctx.readers_helped.length }}):
                  </div>
                  <ul class="space-y-0.5">
                    <li
                      v-for="(r, k) in ctx.readers_helped"
                      :key="`r-${i}-${k}`"
                      class="text-[11px] text-slate-700"
                    >
                      <span class="pill bg-status-success-bg text-status-success text-[9px]">
                        {{ r.kind === 'lineage_script' ? '脚本'
                           : r.kind === 'compare_task' ? '对比'
                           : r.kind === 'workflow' ? '作业流' : r.kind }}
                      </span>
                      <a
                        v-if="r.run_id"
                        :href="`#/workflow-runs/${r.run_id}`"
                        class="ml-1 text-primary hover:underline sql-font"
                      >{{ r.name }} →</a>
                      <a
                        v-else-if="r.workflow_id"
                        :href="`#/workflows/${r.workflow_id}`"
                        class="ml-1 text-primary hover:underline sql-font"
                      >{{ r.name }} →</a>
                      <span v-else class="ml-1 sql-font">{{ r.name }}</span>
                    </li>
                  </ul>
                </div>
              </div>
              <p
                v-if="ctx.writers_affected?.length && ctx.refresh_mode === 'truncate_insert'"
                class="mt-2 text-[10px] text-slate-500 italic pl-3"
              >
                💡 refresh_mode=truncate_insert: 每次 ETL 先 TRUNCATE 再 INSERT,
                索引会在 INSERT 阶段重建,影响相对小。可考虑保留此索引。
              </p>
              <p
                v-else-if="ctx.writers_affected?.length && ctx.refresh_mode === 'append'"
                class="mt-2 text-[10px] text-slate-500 italic pl-3"
              >
                💡 refresh_mode=append: 增量追加写入,每条新 INSERT 都要维护索引,
                高频写入场景需评估写入吞吐影响。
              </p>
            </details>
          </div>
        </div>

        <!-- AI enrichment 结果 -->
        <div v-if="store.enrichResults[idx]" class="rounded-lg border-2 border-primary p-3 space-y-3 bg-white">
          <div class="flex items-center justify-between">
            <div class="text-sm font-bold text-primary flex items-center gap-1.5">
              <Sparkles class="h-4 w-4" />
              AI 复核 · <span class="sql-font text-xs text-slate-500">{{ store.enrichResults[idx].provider }}/{{ store.enrichResults[idx].model }}</span>
            </div>
            <span
              v-if="store.enrichResults[idx].ok && store.enrichResults[idx].expected_coverage.coverage_pct > 0"
              class="pill"
              :class="store.enrichResults[idx].expected_coverage.coverage_pct >= 80 ? 'bg-status-success-bg text-status-success' : store.enrichResults[idx].expected_coverage.coverage_pct >= 40 ? 'bg-status-warning-bg text-status-warning' : 'bg-status-error-bg text-status-error'"
            >
              覆盖 {{ store.enrichResults[idx].expected_coverage.coverage_pct }}%
            </span>
          </div>
          <div v-if="!store.enrichResults[idx].ok" class="text-xs text-slate-500 italic">
            {{ store.enrichResults[idx].error || 'AI provider 未启用,请在 admin → AI 配置中开启' }}
          </div>
          <template v-else>
            <div v-if="store.enrichResults[idx].summary" class="text-sm text-slate-700">
              {{ store.enrichResults[idx].summary }}
            </div>
            <div v-if="store.enrichResults[idx].issue_review.length" class="space-y-1">
              <div class="text-xs font-bold text-slate-600">规则 issue 复核:</div>
              <ul class="space-y-1 text-xs">
                <li v-for="(rev, i) in store.enrichResults[idx].issue_review" :key="`rev-${i}`" class="flex items-start gap-2">
                  <span class="pill text-[10px]" :class="store.verdictBadgeClass(rev.verdict)">{{ rev.verdict }}</span>
                  <span class="text-slate-700"><span class="sql-font text-slate-500">{{ rev.code }}</span> — {{ rev.rationale }}</span>
                </li>
              </ul>
            </div>
            <div v-if="store.enrichResults[idx].extra_suggestions.length" class="space-y-1">
              <div class="text-xs font-bold text-slate-600">AI 补充建议:</div>
              <ul class="space-y-2 text-xs">
                <li v-for="(ex, i) in store.enrichResults[idx].extra_suggestions" :key="`extra-${i}`">
                  <div class="flex items-start gap-2">
                    <span class="pill text-[10px]" :class="store.confidenceBadgeClass(ex.confidence)">{{ ex.confidence || '—' }}</span>
                    <span class="text-slate-700">{{ ex.message }}</span>
                  </div>
                  <pre
                    v-if="ex.sql"
                    class="mt-1 ml-12 px-2 py-1 bg-slate-50 border border-slate-200 rounded text-[11px] sql-font text-slate-700 whitespace-pre-wrap"
                  >{{ ex.sql }}</pre>
                </li>
              </ul>
            </div>
            <div
              v-if="store.enrichResults[idx].expected_coverage.matched.length || store.enrichResults[idx].expected_coverage.missing.length"
              class="grid grid-cols-1 md:grid-cols-2 gap-2 text-xs"
            >
              <div v-if="store.enrichResults[idx].expected_coverage.matched.length">
                <div class="font-bold text-status-success mb-0.5">✓ 命中期望({{ store.enrichResults[idx].expected_coverage.matched.length }})</div>
                <ul class="space-y-0.5 text-slate-700">
                  <li v-for="(m, i) in store.enrichResults[idx].expected_coverage.matched" :key="`m-${i}`">• {{ m }}</li>
                </ul>
              </div>
              <div v-if="store.enrichResults[idx].expected_coverage.missing.length">
                <div class="font-bold text-status-error mb-0.5">✗ 仍有遗漏({{ store.enrichResults[idx].expected_coverage.missing.length }})</div>
                <ul class="space-y-0.5 text-slate-700">
                  <li v-for="(m, i) in store.enrichResults[idx].expected_coverage.missing" :key="`mi-${i}`">• {{ m }}</li>
                </ul>
              </div>
            </div>
          </template>
        </div>

        <!-- Plan diff -->
        <div v-if="store.slowSqlResults[idx]?.history_id" class="rounded-lg border border-primary-light bg-primary-light/10 p-3 text-xs">
          <div class="flex items-center justify-between mb-2">
            <span class="flex items-center gap-1.5 font-semibold text-primary">📊 plan diff</span>
            <button
              class="btn btn-outline h-7 px-2 text-[11px]"
              :disabled="store.planDiffLoading[idx]"
              @click="store.runPlanDiff(idx)"
            >
              {{ store.planDiffLoading[idx] ? '对比中…' : '跟上次对比' }}
            </button>
          </div>
          <div v-if="store.planDiffErrors[idx]" class="text-status-warning">
            {{ store.planDiffErrors[idx] }}
          </div>
          <div v-if="store.planDiffs[idx]" class="space-y-2">
            <div
              class="font-bold rounded p-2"
              :class="store.isPlanDiffImproved(store.planDiffs[idx]!)
                ? 'bg-status-success-bg text-status-success'
                : store.isPlanDiffRegressed(store.planDiffs[idx]!)
                ? 'bg-status-error-bg text-status-error'
                : 'bg-slate-100 text-slate-700'"
            >
              {{ store.planDiffs[idx]!.diff.summary }}
            </div>
            <div class="grid grid-cols-3 gap-2 text-center">
              <div class="rounded bg-slate-100 p-2">
                <div class="font-bold text-slate-700">{{ store.planDiffs[idx]!.diff.rows_delta.a.toLocaleString() }}</div>
                <div class="muted text-[10px]">上次 max-rows</div>
              </div>
              <div class="rounded bg-slate-100 p-2">
                <div class="font-bold text-slate-700">{{ store.planDiffs[idx]!.diff.rows_delta.b.toLocaleString() }}</div>
                <div class="muted text-[10px]">本次 max-rows</div>
              </div>
              <div class="rounded p-2"
                   :class="store.planDiffs[idx]!.diff.rows_delta.change < 0 ? 'bg-status-success-bg text-status-success' : store.planDiffs[idx]!.diff.rows_delta.change > 0 ? 'bg-status-error-bg text-status-error' : 'bg-slate-100 text-slate-700'">
                <div class="font-bold">{{ store.planDiffs[idx]!.diff.rows_delta.change > 0 ? '+' : '' }}{{ store.planDiffs[idx]!.diff.rows_delta.change.toLocaleString() }}</div>
                <div class="text-[10px]">差值</div>
              </div>
            </div>
            <div v-if="store.planDiffs[idx]!.diff.issues_resolved.length || store.planDiffs[idx]!.diff.issues_introduced.length" class="grid grid-cols-2 gap-2">
              <div v-if="store.planDiffs[idx]!.diff.issues_resolved.length" class="rounded bg-status-success-bg p-2">
                <div class="text-status-success font-bold mb-0.5">✓ 修复({{ store.planDiffs[idx]!.diff.issues_resolved.length }})</div>
                <ul class="text-slate-700">
                  <li v-for="c in store.planDiffs[idx]!.diff.issues_resolved" :key="c">• {{ c }}</li>
                </ul>
              </div>
              <div v-if="store.planDiffs[idx]!.diff.issues_introduced.length" class="rounded bg-status-error-bg p-2">
                <div class="text-status-error font-bold mb-0.5">✗ 新引入({{ store.planDiffs[idx]!.diff.issues_introduced.length }})</div>
                <ul class="text-slate-700">
                  <li v-for="c in store.planDiffs[idx]!.diff.issues_introduced" :key="c">• {{ c }}</li>
                </ul>
              </div>
            </div>
            <div v-if="store.planDiffs[idx]!.diff.type_changes.length || store.planDiffs[idx]!.diff.extra_changes.length" class="rounded bg-slate-50 p-2">
              <div v-if="store.planDiffs[idx]!.diff.type_changes.length" class="mb-1">
                <span class="font-semibold text-slate-700">type 变化:</span>
                <span v-for="(t, i) in store.planDiffs[idx]!.diff.type_changes" :key="`tc-${i}`" class="ml-2 text-[11px]">
                  step{{ t.idx }} <span class="text-status-error line-through">{{ t.from }}</span> → <span class="text-status-success">{{ t.to }}</span>
                </span>
              </div>
              <div v-if="store.planDiffs[idx]!.diff.extra_changes.length">
                <span class="font-semibold text-slate-700">Extra 变化:</span>
                <div v-for="(e, i) in store.planDiffs[idx]!.diff.extra_changes" :key="`ec-${i}`" class="ml-2 text-[11px]">
                  step{{ e.idx }}:
                  <span v-if="e.removed.length" class="text-status-success">- {{ e.removed.join(', ') }}</span>
                  <span v-if="e.added.length" class="text-status-error ml-1">+ {{ e.added.join(', ') }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- EXPLAIN plan 原始行 -->
        <div v-if="store.slowSqlResults[idx]?.plan?.length" class="rounded-lg border border-slate-200 bg-white overflow-x-auto">
          <div class="px-3 py-2 text-xs font-bold text-slate-600 border-b border-slate-200">
            EXPLAIN 输出({{ store.slowSqlResults[idx].plan.length }} 行)
          </div>
          <table class="w-full text-xs sql-font">
            <thead>
              <tr class="bg-slate-50">
                <th
                  v-for="col in store.planColumns(store.slowSqlResults[idx].plan)"
                  :key="col"
                  class="text-left px-2 py-1.5 font-medium text-slate-600 border-b border-slate-200"
                >
                  {{ col }}
                </th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="(row, ri) in store.slowSqlResults[idx].plan"
                :key="ri"
                class="border-b border-slate-100 hover:bg-slate-50"
              >
                <td
                  v-for="col in store.planColumns(store.slowSqlResults[idx].plan)"
                  :key="col"
                  class="px-2 py-1.5 text-slate-700"
                >
                  {{ row[col] == null ? '·' : String(row[col]) }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>
  </template>
</template>
