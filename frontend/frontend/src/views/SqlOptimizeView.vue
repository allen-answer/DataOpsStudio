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
import QuickOptimizeMode from './sql-optimize/QuickOptimizeMode.vue'

const store = useSandboxStore()

// step bar 显示用 —— 当前 step 由 store.currentStep 启发推断
const STEPS: { id: StepId; label: string; desc: string }[] = [
  { id: 'schema', label: '1. Schema', desc: '从生产 SHOW CREATE 导入 yml 或选既有 scenario' },
  { id: 'data',   label: '2. 生成数据', desc: 'Faker/AI 填业务样本 + materialize 到 demo DB + ANALYZE' },
  { id: 'sql',    label: '3. SQL 优化',  desc: '跑慢 SQL → EXPLAIN → AI 复核 → 改写 → 重跑对比 plan' },
  { id: 'verify', label: '4. 回归校验',  desc: '改完 SQL 跑回归确认数据没改坏 + 性能改善' },
]

// 点 step bar 任一步滚到对应区域。step 1 = scenario 列表(左栏);
// 2 = datasource picker + action 按钮;3 = workload 列表 + slow-sql 分析;
// 4 = verify result 卡片。各区域 template 上加 ref,这里 scrollIntoView。
function jumpToStep(stepId: StepId): void {
  const el = document.querySelector(`[data-step-anchor="${stepId}"]`)
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

onMounted(async () => {
  await store.loadList()
  // **不自动选 scenario** —— Phase 14 P2 完整版后让用户主动选,
  // 否则 currentStep 直接跳 step 2,看起来"第一步选不了"
  // 同样不自动选 datasource —— 让用户在 step 2 显式选
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
          不连生产做 SQL 性能诊断 + 优化验证。快速优化模式直接粘 SQL 跑 EXPLAIN;场景模板模式复现千万行规模 + 数据偏斜场景。
        </p>
      </div>
      <div class="flex items-center gap-2">
        <button v-if="store.viewMode === 'template'" class="btn btn-primary" @click="store.openImportDialog">
          <Database class="h-4 w-4" />
          从 datasource 导入
        </button>
        <button v-if="store.viewMode === 'template'" class="btn btn-outline" :disabled="store.loadingList" @click="store.loadList">
          <RefreshCw class="h-4 w-4" :class="{ 'animate-spin': store.loadingList }" />
          刷新列表
        </button>
      </div>
    </div>

    <!-- Mode tab:快速优化(默认 / 日常)vs 场景模板(advanced / 复现生产规模) -->
    <div class="flex border-b border-slate-200">
      <button
        type="button"
        class="px-5 py-2.5 text-sm font-medium border-b-2 -mb-px transition"
        :class="store.viewMode === 'quick'
          ? 'border-primary text-primary'
          : 'border-transparent text-slate-500 hover:text-slate-800'"
        @click="store.viewMode = 'quick'"
      >
        ⚡ 快速优化
        <span class="ml-1 text-[10px] text-slate-400">直接粘 SQL,无需模板</span>
      </button>
      <button
        type="button"
        class="px-5 py-2.5 text-sm font-medium border-b-2 -mb-px transition"
        :class="store.viewMode === 'template'
          ? 'border-primary text-primary'
          : 'border-transparent text-slate-500 hover:text-slate-800'"
        @click="store.viewMode = 'template'"
      >
        📦 场景模板
        <span class="ml-1 text-[10px] text-slate-400">复现千万行 + 偏斜 + 回归校验</span>
      </button>
    </div>

    <!-- Quick mode 内容 -->
    <QuickOptimizeMode v-if="store.viewMode === 'quick'" />

    <!-- Template mode 内容(原 UI) -->
    <template v-else>
    <!-- Step bar:点任一步滚到对应区域(高亮 = 当前完成进度推断)-->
    <div class="card p-3 space-y-2">
      <div class="text-[10px] uppercase tracking-wider text-slate-400 font-bold px-1">
        优化流程 · 点任一步跳到对应区域
      </div>
      <div class="flex items-stretch gap-2 overflow-x-auto">
        <button
          v-for="s in STEPS"
          :key="s.id"
          type="button"
          class="flex-1 min-w-[180px] rounded-lg border px-3 py-2 transition text-left cursor-pointer"
          :class="store.currentStep === s.id
            ? 'border-primary bg-primary-light text-primary'
            : 'border-slate-200 bg-white text-slate-600 hover:border-primary hover:bg-primary-light/40'"
          @click="jumpToStep(s.id)"
        >
          <div class="font-bold text-xs">{{ s.label }}</div>
          <div class="text-[11px] mt-0.5 leading-snug" :class="store.currentStep === s.id ? '' : 'text-slate-500'">{{ s.desc }}</div>
          <div class="text-[10px] mt-1" :class="store.currentStep === s.id ? 'text-primary/80' : 'text-slate-400'">
            {{ store.currentStep === s.id ? '← 当前进度' : '👆 点击跳转' }}
          </div>
        </button>
      </div>
      <!-- 一句话指引:告诉用户当前 step 下该点哪个按钮 -->
      <div class="text-[11px] text-slate-600 px-1 pt-1 border-t border-slate-100">
        💡
        <template v-if="store.currentStep === 'schema'">
          左侧选一份 scenario 模板,或右上点
          <span class="font-semibold text-primary">「从 datasource 导入」</span>
          从生产 schema 反向生成新 yml
        </template>
        <template v-else-if="store.currentStep === 'data'">
          选好「目标 datasource」后点
          <span class="font-semibold text-primary">「🚀 一键全套」</span>
          (推荐,跑通整个链路)或「仅生成数据」
        </template>
        <template v-else-if="store.currentStep === 'sql'">
          在「工作负载」栏找 <code class="sql-font">slow_query</code> 类型的行,点行右边
          <span class="font-semibold text-primary">「🔬 分析」</span>
          → 出 EXPLAIN 后点
          <span class="font-semibold text-primary">「✨ AI 复核」</span>
          → 改写 SQL 后重跑用
          <span class="font-semibold text-primary">「📊 plan diff」</span>
          对比改善
        </template>
        <template v-else-if="store.currentStep === 'verify'">
          顶部点
          <span class="font-semibold text-primary">「🛡 回归校验」</span>
          确认数据对得上 + 性能改善没破坏 actual diff 结果
        </template>
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

    <!-- 两列:左 scenario 列表(对应 step 1 schema 选模板)+ 右主工作区 -->
    <div data-step-anchor="schema" class="grid grid-cols-1 lg:grid-cols-[280px_1fr] gap-6">
      <ScenarioListPanel />

      <div class="space-y-6">
        <div v-if="store.loadingDetail" class="card p-6 muted text-center">加载详情中…</div>
        <div v-else-if="!store.detail" class="card p-12 text-center text-slate-500">
          <Microscope class="h-12 w-12 mx-auto text-slate-300" />
          <p class="mt-3 text-sm">选一份 scenario 模板开始</p>
        </div>

        <template v-else>
          <!-- 头部 + datasource picker + action buttons(对应 step 2 生成数据) -->
          <div data-step-anchor="data" class="card p-6">
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
                <!-- Phase 14 #1 环境标签 banner -->
                <div
                  v-if="store.selectedDsEnvironment && store.selectedDsEnvironment !== 'sandbox'"
                  class="mt-2 rounded-lg border-2 p-2 text-xs flex items-start gap-2"
                  :class="store.selectedDsEnvironment === 'prod'
                    ? 'border-status-error bg-status-error-bg text-status-error'
                    : 'border-status-warning bg-status-warning-bg text-status-warning'"
                >
                  <span class="font-bold">
                    {{ store.selectedDsEnvironment === 'prod' ? '🔴 PROD' : '🟡 STAGING' }}
                  </span>
                  <span>
                    此 datasource 标签为 <b>{{ store.selectedDsEnvironment }}</b>,
                    沙盒写入端点(一键全套 / 生成数据 / 建任务)<b>已锁定</b>。
                    只读分析(🔬 慢 SQL / ✨ AI 复核 / 🛡 校验)不受影响。
                  </span>
                </div>
                <div
                  v-else-if="store.selectedDsEnvironment === 'sandbox'"
                  class="mt-2 rounded-lg border border-status-success bg-status-success-bg text-status-success p-2 text-xs flex items-center gap-2"
                >
                  <span class="font-bold">🟢 SANDBOX</span>
                  <span>此 datasource 是沙盒环境,可放心造数据 / 跑模拟流程</span>
                </div>
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
                :disabled="!store.datasourceId || store.runningAll || store.sandboxWriteLocked"
                @click="store.runAll"
                :title="store.sandboxWriteLocked
                  ? '此 datasource 是 ' + store.selectedDsEnvironment + ' 环境,造数据已锁定'
                  : 'fill → generate → materialize → record → run tasks → verify 一气呵成'"
              >
                <Rocket class="h-4 w-4" :class="{ 'animate-pulse': store.runningAll }" />
                {{ store.runningAll ? '一键链跑中…' : '🚀 一键全套' }}
              </button>
              <button
                class="btn btn-outline"
                :disabled="!store.datasourceId || store.materializing || store.sandboxWriteLocked"
                @click="store.runMaterialize"
                :title="store.sandboxWriteLocked ? '非 sandbox 环境,造数据已锁定' : ''"
              >
                <Play class="h-4 w-4" :class="{ 'animate-pulse': store.materializing }" />
                {{ store.materializing ? '生成中…' : '仅生成数据' }}
              </button>
              <button
                class="btn btn-outline"
                :disabled="!store.datasourceId || store.recording || store.sandboxWriteLocked"
                @click="store.runRecord"
                :title="store.sandboxWriteLocked ? '非 sandbox 环境,建任务已锁定' : ''"
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

          <!-- 三栏 schema breakdown(workload 列里是 step 3 SQL 优化入口:点 slow_query 行的「分析」按钮)-->
          <div data-step-anchor="sql" class="grid grid-cols-1 lg:grid-cols-3 gap-4">
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

          <!-- 嵌入子组件:run-all / verify / materialize / record 各结果面板(verify 是 step 4 入口)-->
          <div data-step-anchor="verify">
            <ResultPanels />
          </div>

          <!-- 嵌入子组件:slow-sql 分析卡片(per workload) -->
          <SlowSqlCards />
        </template>
      </div>
    </div>
    </template>
  </section>
</template>
