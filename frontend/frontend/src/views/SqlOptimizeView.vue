<script setup lang="ts">
// SQL 优化沙盒(Phase 12 → P0-1 重定位 → P2 完整版 view 拆分)。
//
// shell 职责:加载 + 顶部 step bar + 3 列布局 + import 对话框 + scenario 详情
// (header/datasource picker/action buttons/schema breakdown) + 嵌入子组件。
// state + actions 全在 stores/sandbox.ts;1689 行老 view 拆成 5 个子文件 + 这个
// shell(~250 行)。
//
// 后端 API + 沙盒能力不变。
import { onMounted } from 'vue'
import {
  Microscope, RefreshCw, Play, ListChecks, Database, Sparkles,
  ShieldCheck, Rocket, Variable, ChevronDown, ChevronRight,
} from 'lucide-vue-next'
import { useSandboxStore } from '../stores/sandbox'
import type { StepId } from '../types/sandbox'

import ImportDialog from './sql-optimize/ImportDialog.vue'
import ScenarioListPanel from './sql-optimize/ScenarioListPanel.vue'
import SlowSqlCards from './sql-optimize/SlowSqlCards.vue'
import ResultPanels from './sql-optimize/ResultPanels.vue'

const store = useSandboxStore()

// step bar 显示用 —— 当前 step 由 store.currentStep 启发推断
const STEPS: { id: StepId; label: string; desc: string }[] = [
  { id: 'schema', label: '1. Schema', desc: '从生产 SHOW CREATE 导入 yml 或选既有 scenario' },
  { id: 'data',   label: '2. 生成数据', desc: 'Faker/AI 填业务样本 + materialize 到 demo DB + ANALYZE' },
  { id: 'sql',    label: '3. SQL 优化',  desc: '跑慢 SQL → EXPLAIN → AI 复核 → 改写 → 重跑对比 plan' },
  { id: 'verify', label: '4. 回归校验',  desc: '改完 SQL 跑回归确认数据没改坏 + 性能改善' },
]

onMounted(async () => {
  await store.loadList()
  if (store.validScenarios.length && !store.selectedId) {
    const firstId = store.validScenarios[0].id
    if (firstId) await store.selectScenario(firstId)
  }
  if (store.mysqlDatasources.length && !store.datasourceId) {
    store.datasourceId = (store.mysqlDatasources[0] as any).id
  }
})
</script>

<template>
  <section class="space-y-6">
    <!-- 顶部:标题 + 主操作 -->
    <div class="flex items-end justify-between">
      <div>
        <h2 class="text-2xl font-bold text-slate-800 flex items-center gap-2">
          <Microscope class="h-7 w-7 text-primary" />
          SQL 优化沙盒
        </h2>
        <p class="mt-1 text-sm text-slate-500">
          不连生产做 SQL 性能诊断 + 优化验证。从生产 schema 翻 yml → Faker/AI 填业务数据 → demo DB 跑 EXPLAIN → 改 SQL/加索引 → 对比 plan。
        </p>
      </div>
      <div class="flex items-center gap-2">
        <button class="btn btn-primary" @click="store.openImportDialog">
          <Database class="h-4 w-4" />
          从 datasource 导入
        </button>
        <button class="btn btn-outline" :disabled="store.loadingList" @click="store.loadList">
          <RefreshCw class="h-4 w-4" :class="{ 'animate-spin': store.loadingList }" />
          刷新列表
        </button>
      </div>
    </div>

    <!-- Step bar 视觉导航 -->
    <div class="card p-3 flex items-stretch gap-2 overflow-x-auto">
      <div
        v-for="s in STEPS"
        :key="s.id"
        class="flex-1 min-w-[180px] rounded-lg border px-3 py-2 transition"
        :class="store.currentStep === s.id
          ? 'border-primary bg-primary-light text-primary'
          : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300'"
      >
        <div class="font-bold text-xs">{{ s.label }}</div>
        <div class="text-[11px] mt-0.5 leading-snug" :class="store.currentStep === s.id ? '' : 'text-slate-500'">{{ s.desc }}</div>
        <div v-if="store.currentStep === s.id" class="text-[10px] mt-1 text-primary/80">← 当前步骤</div>
      </div>
    </div>

    <!-- Import dialog(条件渲染) -->
    <ImportDialog />

    <!-- 错误 / 坏 scenario 提示 -->
    <div v-if="store.lastError" class="card border-status-error bg-status-error-bg p-4">
      <div class="text-sm text-status-error">{{ store.lastError }}</div>
    </div>
    <div v-if="store.brokenScenarios.length" class="card border-status-warning bg-status-warning-bg p-4">
      <div class="text-sm font-medium text-status-warning">
        有 {{ store.brokenScenarios.length }} 份 scenario yml 解析失败
      </div>
      <ul class="mt-2 space-y-1 text-xs text-status-warning">
        <li v-for="b in store.brokenScenarios" :key="b.path">
          <code class="sql-font">{{ b.path }}</code> — {{ b.error }}
        </li>
      </ul>
    </div>

    <!-- 两列:左 scenario 列表 + 右主工作区 -->
    <div class="grid grid-cols-1 lg:grid-cols-[280px_1fr] gap-6">
      <ScenarioListPanel />

      <div class="space-y-6">
        <div v-if="store.loadingDetail" class="card p-6 muted text-center">加载详情中…</div>
        <div v-else-if="!store.detail" class="card p-12 text-center text-slate-500">
          <Microscope class="h-12 w-12 mx-auto text-slate-300" />
          <p class="mt-3 text-sm">选一份 scenario 模板开始</p>
        </div>

        <template v-else>
          <!-- 头部 + 操作 -->
          <div class="card p-6">
            <div class="flex items-start justify-between gap-4 mb-4">
              <div>
                <h3 class="text-xl font-bold text-slate-800">{{ store.detail.name }}</h3>
                <p class="mt-1 text-sm text-slate-500">{{ store.detail.description }}</p>
                <div class="mt-2 text-xs text-slate-400 sql-font">
                  {{ store.detailPath }} · seed={{ store.detail.seed }} · {{ store.totalRows(store.detail) }} 行预计生成
                </div>
              </div>
              <div class="flex flex-wrap gap-1">
                <span v-for="t in (store.detail.tags || [])" :key="t" class="pill bg-slate-100 text-slate-600">
                  {{ t }}
                </span>
              </div>
            </div>

            <div class="grid grid-cols-1 md:grid-cols-[1fr_auto_auto] gap-3 items-end border-t border-slate-200 pt-4">
              <div>
                <label class="block text-xs uppercase tracking-wider text-slate-500 font-bold mb-1">
                  <Database class="h-3 w-3 inline" /> 目标 datasource(MySQL)
                </label>
                <select v-model="store.datasourceId" class="w-full">
                  <option value="" disabled>—— 选一个 ——</option>
                  <option
                    v-for="ds in store.mysqlDatasources"
                    :key="(ds as any).id"
                    :value="(ds as any).id"
                  >
                    {{ (ds as any).name }} · {{ (ds as any).host }}:{{ (ds as any).port }}
                  </option>
                </select>
                <p v-if="!store.mysqlDatasources.length" class="mt-1 text-xs text-status-warning">
                  无可用 MySQL datasource —— 先去「数据源」页加一个。
                </p>
              </div>
              <label class="flex items-center gap-2 text-sm pb-1.5">
                <input type="checkbox" v-model="store.dropFirst" />
                <span>DROP 已存在</span>
              </label>
              <label class="flex items-center gap-2 text-sm pb-1.5" title="先走 LLM 把 realistic 列填业务化样本池,再生成数据">
                <input type="checkbox" v-model="store.aiFill" />
                <span class="flex items-center gap-1">
                  <Sparkles class="h-3.5 w-3.5 text-primary" />
                  AI 填血肉
                </span>
              </label>
              <div>
                <label class="block text-xs uppercase tracking-wider text-slate-500 font-bold mb-1">
                  项目空间(可选)
                </label>
                <input v-model="store.projectId" placeholder="留空 = 默认" class="w-32" />
              </div>
            </div>

            <div class="mt-4 flex flex-wrap gap-3">
              <button
                class="btn btn-primary"
                :disabled="!store.datasourceId || store.runningAll"
                @click="store.runAll"
                title="fill → generate → materialize → record → run tasks → verify 一气呵成"
              >
                <Rocket class="h-4 w-4" :class="{ 'animate-pulse': store.runningAll }" />
                {{ store.runningAll ? '一键链跑中…' : '🚀 一键全套' }}
              </button>
              <button
                class="btn btn-outline"
                :disabled="!store.datasourceId || store.materializing"
                @click="store.runMaterialize"
              >
                <Play class="h-4 w-4" :class="{ 'animate-pulse': store.materializing }" />
                {{ store.materializing ? '生成中…' : '仅生成数据' }}
              </button>
              <button
                class="btn btn-outline"
                :disabled="!store.datasourceId || store.recording"
                @click="store.runRecord"
              >
                <ListChecks class="h-4 w-4" />
                {{ store.recording ? '建任务中…' : '建对比任务' }}
              </button>
              <button
                class="btn btn-outline"
                :disabled="store.verifying"
                @click="store.runVerify"
                title="对比 yml expected vs actual run summary,把 scenario 当回归 fixture 用"
              >
                <ShieldCheck class="h-4 w-4" />
                {{ store.verifying ? '校验中…' : '回归校验' }}
              </button>
            </div>
          </div>

          <!-- 三栏 schema breakdown -->
          <div class="grid grid-cols-1 lg:grid-cols-3 gap-4">
            <div class="card p-4">
              <div class="text-xs uppercase tracking-wider text-slate-500 font-bold mb-3">
                表({{ store.detail.tables.length }})
              </div>
              <ul class="space-y-2">
                <li v-for="t in store.detail.tables" :key="t.name" class="text-sm">
                  <div class="flex items-center gap-2">
                    <span class="font-medium sql-font text-slate-800">{{ t.name }}</span>
                    <span class="pill bg-tag-source-bg text-tag-source">{{ t.role }}</span>
                  </div>
                  <div class="text-xs text-slate-500 mt-0.5">
                    {{ t.rows }} 行
                    <span v-if="t.derives_from"> · 派生自 {{ t.derives_from }}</span>
                    <span v-if="t.columns?.length"> · {{ t.columns.length }} 列</span>
                  </div>
                </li>
              </ul>
            </div>

            <div class="card p-4">
              <div class="text-xs uppercase tracking-wider text-slate-500 font-bold mb-3">
                偏差({{ store.detail.anomalies.length }})
              </div>
              <ul class="space-y-2">
                <li v-for="(a, idx) in store.detail.anomalies" :key="idx" class="text-sm">
                  <div class="flex items-center gap-2">
                    <span class="pill bg-status-warning-bg text-status-warning">{{ a.kind }}</span>
                    <span class="sql-font text-slate-600">{{ a.table }}</span>
                  </div>
                  <div class="text-xs text-slate-500 mt-0.5">{{ store.anomalyLabel(a) }}</div>
                </li>
                <li v-if="!store.detail.anomalies.length" class="text-sm text-slate-400">无偏差注入</li>
              </ul>
            </div>

            <div class="card p-4">
              <div class="text-xs uppercase tracking-wider text-slate-500 font-bold mb-3">
                工作负载({{ store.detail.workloads.length }})
              </div>
              <ul class="space-y-2">
                <li v-for="(w, idx) in store.detail.workloads" :key="idx" class="text-sm">
                  <div class="flex items-center gap-2 flex-wrap">
                    <span class="pill bg-primary-light text-primary">{{ w.kind }}</span>
                    <span class="text-slate-800">{{ w.name || '—' }}</span>
                    <div v-if="w.kind === 'slow_query' && w.sql" class="ml-auto flex items-center gap-2">
                      <button
                        class="text-xs text-primary hover:underline flex items-center gap-0.5 disabled:text-slate-400"
                        :disabled="!store.datasourceId || store.slowSqlAnalyzing[idx]"
                        @click="store.runSlowSqlAnalysis(idx, w)"
                      >
                        <Microscope class="h-3.5 w-3.5" :class="{ 'animate-pulse': store.slowSqlAnalyzing[idx] }" />
                        {{ store.slowSqlAnalyzing[idx] ? '分析中…' : '分析' }}
                      </button>
                      <button
                        v-if="store.slowSqlResults[idx]"
                        class="text-xs text-primary hover:underline flex items-center gap-0.5 disabled:text-slate-400"
                        :disabled="store.enrichLoading[idx]"
                        @click="store.runAiEnrich(idx, w)"
                      >
                        <Sparkles class="h-3.5 w-3.5" :class="{ 'animate-pulse': store.enrichLoading[idx] }" />
                        {{ store.enrichLoading[idx] ? 'AI 复核中…' : 'AI 复核' }}
                      </button>
                    </div>
                    <button
                      v-else-if="w.kind === 'slow_query' && store.slowSqlResults[idx]"
                      class="ml-auto text-xs text-slate-500 flex items-center gap-0.5"
                      @click="store.toggleSlowSqlExpansion(idx)"
                    >
                      <component :is="store.slowSqlExpanded[idx] ? ChevronDown : ChevronRight" class="h-3.5 w-3.5" />
                      {{ store.slowSqlExpanded[idx] ? '收起' : '展开' }}
                    </button>
                  </div>
                </li>
                <li v-if="!store.detail.workloads.length" class="text-sm text-slate-400">无工作负载</li>
              </ul>

              <div
                v-if="store.detail.variables && Object.keys(store.detail.variables).length"
                class="mt-4 pt-3 border-t border-line"
              >
                <div class="text-xs uppercase tracking-wider text-slate-500 font-bold mb-2 flex items-center gap-1.5">
                  <Variable class="h-3.5 w-3.5" />
                  模板变量
                  <span class="ml-1 text-[10px] font-normal normal-case tracking-normal text-slate-400">
                    workload.sql 里 <code class="sql-font">&#123;&#123;name&#125;&#125;</code> 占位符会渲染成此处值
                  </span>
                </div>
                <ul class="space-y-1 text-xs">
                  <li
                    v-for="(value, name) in store.detail.variables"
                    :key="name"
                    class="flex items-center gap-2 sql-font"
                  >
                    <span class="text-primary font-medium">{{ name }}</span>
                    <span class="text-slate-400">→</span>
                    <span class="text-slate-700">{{ value }}</span>
                  </li>
                </ul>
              </div>
            </div>
          </div>

          <!-- 嵌入子组件:run-all / verify / materialize / record 各结果面板 -->
          <ResultPanels />

          <!-- 嵌入子组件:slow-sql 分析卡片(per workload) -->
          <SlowSqlCards />
        </template>
      </div>
    </div>
  </section>
</template>
