<script setup lang="ts">
// 场景管理(Scenarios)— 独立一级菜单,从 SqlOptimizeView 抽出来。
//
// 职责 = scenario 生命周期: 选 yml → 配 datasource → 生成数据 / record /
// 校验 / 一键全套 → 3 个跳转 CTA(SQL 优化 / 数据对比 / 血缘脚本)。
//
// State + actions 全在 stores/sandbox.ts(跨 view 共享, store-singleton)。
// 子组件复用 sql-optimize/ 下已有的 ScenarioListPanel / ImportDialog /
// ResultPanels —— 不重复造轮子。slow_query analyze + AI 复核 + plan diff 都
// 在 SQL 优化页(/sql-optimize),用 CTA "去 SQL 优化" 跳过去带 scenario_id 上下文。
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import {
  FlaskConical, RefreshCw, Play, ListChecks, Database, Sparkles,
  ShieldCheck, Rocket, Variable, ArrowRight,
  Microscope, GitCompareArrows, GitBranch,
} from 'lucide-vue-next'
import { useSandboxStore } from '../stores/sandbox'

import ImportDialog from './sql-optimize/ImportDialog.vue'
import ScenarioListPanel from './sql-optimize/ScenarioListPanel.vue'
import ResultPanels from './sql-optimize/ResultPanels.vue'

const store = useSandboxStore()
const router = useRouter()

onMounted(() => {
  store.loadList()
})

// 跳转 CTA — 带 scenarioId / datasourceId / project_id 上下文
function goSqlOptimize() {
  router.push({
    path: '/sql-optimize',
    query: {
      scenario_id: store.selectedId || '',
      datasource_id: store.datasourceId || '',
      project_id: store.projectId || '',
    },
  })
}

function goDataCompare() {
  // 数据对比页通过 task_id 加载 — record 后的 task 列表可让用户挑
  // 简化:先跳过去,工作台会列出最近 task,用户自己挑(后续可深 link 到第一个 task)
  router.push({ path: '/data-compare' })
}

function goLineage() {
  // lineage_script workload 的 run 落 history,跳 history 看
  router.push({ path: '/history', query: { type: 'lineage' } })
}

// 是否已成功生成数据 — 决定 3 个 CTA 是否高亮可点
const hasMaterialized = () => !!store.materializeResult || !!store.runAllResult
</script>

<template>
  <section class="space-y-6">
    <!-- Header -->
    <div class="flex items-end justify-between">
      <div>
        <h2 class="text-2xl font-bold text-slate-800 flex items-center gap-2">
          <FlaskConical class="h-7 w-7 text-primary" />
          {{ $t('nav.scenarios') }}
        </h2>
        <p class="mt-1 text-sm text-slate-500">
          yml 定义虚拟业务场景 → 一键造数据 / 跑对比 / 跑血缘 / 跑慢 SQL —— 给
          回归测试 / 演示 / 真实案例用的 fixture 基建
        </p>
      </div>
      <div class="flex gap-2">
        <button class="btn btn-primary" @click="store.openImportDialog">
          <RefreshCw class="h-4 w-4" />
          从真实库导入 yml
        </button>
        <button class="btn btn-outline" @click="store.loadList">
          <RefreshCw class="h-4 w-4" />
          刷新列表
        </button>
      </div>
    </div>

    <!-- Import dialog -->
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

    <!-- 两列布局:左场景列表 + 右主区 -->
    <div class="grid grid-cols-1 lg:grid-cols-[280px_1fr] gap-6">
      <ScenarioListPanel />

      <div class="space-y-6">
        <div v-if="store.loadingDetail" class="card p-6 muted text-center">加载详情中…</div>
        <div v-else-if="!store.detail" class="card p-12 text-center text-slate-500">
          <FlaskConical class="h-12 w-12 mx-auto text-slate-300" />
          <p class="mt-3 text-sm">选一份 scenario 模板开始</p>
        </div>

        <template v-else>
          <!-- 场景头部 + datasource picker + 4 个 action 按钮 -->
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
                <!-- 环境标签 banner -->
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
                    只读分析(🛡 校验)不受影响。
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

          <!-- 数据生成后 — 3 个跳转 CTA,带场景上下文 -->
          <div
            v-if="hasMaterialized()"
            class="card p-6 border-2 border-primary bg-primary-light/10"
          >
            <div class="text-sm font-bold text-slate-800 mb-3 flex items-center gap-2">
              ✅ 数据已就位,接下来去哪用?
            </div>
            <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
              <button
                class="btn btn-primary flex flex-col items-start gap-1 h-auto py-3 px-4"
                @click="goSqlOptimize"
              >
                <div class="flex items-center gap-2 text-base font-bold">
                  <Microscope class="h-5 w-5" />
                  去 SQL 优化
                  <ArrowRight class="h-4 w-4 ml-auto" />
                </div>
                <div class="text-xs font-normal opacity-90 text-left">
                  跑 slow_query workload → EXPLAIN → ✨ AI 复核 → 改写 SQL
                </div>
              </button>
              <button
                class="btn btn-outline flex flex-col items-start gap-1 h-auto py-3 px-4"
                @click="goDataCompare"
              >
                <div class="flex items-center gap-2 text-base font-bold">
                  <GitCompareArrows class="h-5 w-5" />
                  去数据对比
                  <ArrowRight class="h-4 w-4 ml-auto" />
                </div>
                <div class="text-xs font-normal opacity-90 text-left">
                  打开 record 创建的 compare task,跑双端对比 + 看 diff
                </div>
              </button>
              <button
                class="btn btn-outline flex flex-col items-start gap-1 h-auto py-3 px-4"
                @click="goLineage"
              >
                <div class="flex items-center gap-2 text-base font-bold">
                  <GitBranch class="h-5 w-5" />
                  去血缘分析
                  <ArrowRight class="h-4 w-4 ml-auto" />
                </div>
                <div class="text-xs font-normal opacity-90 text-left">
                  看 lineage_script workload 的结果(history 里 type=lineage)
                </div>
              </button>
            </div>
          </div>

          <!-- 三栏:表 / 偏差 / 工作负载概览(不含 SQL 分析交互 — 那在 /sql-optimize) -->
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

          <!-- 结果面板:run-all / verify / materialize / record -->
          <ResultPanels />
        </template>
      </div>
    </div>
  </section>
</template>
