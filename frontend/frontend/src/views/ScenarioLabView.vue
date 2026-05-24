<script setup lang="ts">
/**
 * Phase 14 #3 — 场景测试沙盒 view
 *
 * 拆分自原 /sql-optimize template mode。本 view 承载所有测试沙盒能力:
 * - scenario yml 模板列表 + 详情
 * - 选 datasource + materialize / drop_first / ai_fill
 * - run-all 一键全套 / record 落 task / verify 回归校验
 * - ResultPanels
 *
 * 只允许 sandbox datasource 执行写入类动作(operation_policy 强制)。
 */
import { onMounted, computed } from 'vue'
import {
  FlaskConical, RefreshCw, Play, ListChecks, Database, Sparkles,
  ShieldCheck, Rocket, Variable, Microscope, FilePlus2,
} from 'lucide-vue-next'
import { useScenarioLabStore } from '../stores/scenarioLab'
import OperationRiskPanel from '../components/sql/OperationRiskPanel.vue'
import ImportDialog from './sql-optimize/ImportDialog.vue'
import ScenarioListPanel from './sql-optimize/ScenarioListPanel.vue'
import ResultPanels from './sql-optimize/ResultPanels.vue'

const store = useScenarioLabStore()

// 当前选中 ds(给风险面板用)
const selectedDs = computed(() => {
  const id = store.datasourceId || ''
  return (store.mysqlDatasources as any[]).find((d: any) => d.id === id) || null
})

onMounted(() => {
  // 进 view 时强制切到 template mode(共享 store 的 viewMode 字段)
  store.viewMode = 'template'
  store.loadList()
})
</script>

<template>
  <section class="space-y-6">
    <!-- 顶部:标题 + 主操作 -->
    <div class="flex items-end justify-between">
      <div>
        <h2 class="text-2xl font-bold text-slate-800 flex items-center gap-2">
          <FlaskConical class="h-7 w-7 text-primary" />
          场景测试沙盒
        </h2>
        <p class="mt-1 text-sm text-slate-500">
          yml 定义虚拟业务场景 → 一键造数据 + 跑对比 + 跑回归校验 — fixture 基建。
          <span class="text-status-error font-semibold">仅 sandbox 环境数据源可写入。</span>
        </p>
      </div>
      <div class="flex items-center gap-2">
        <!-- Phase 14 #3 Round 4 — 可视化新建场景入口 -->
        <a href="#/scenario-lab/builder" class="btn btn-primary">
          <FilePlus2 class="h-4 w-4" />
          + 新建场景(可视化)
        </a>
        <a href="#/scenario-lab/import" class="btn btn-outline">
          <Database class="h-4 w-4" />
          从 datasource 导入 schema
        </a>
        <button class="btn btn-outline" :disabled="store.loadingList" @click="store.loadList">
          <RefreshCw class="h-4 w-4" :class="{ 'animate-spin': store.loadingList }" />
          刷新列表
        </button>
      </div>
    </div>

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
          <FlaskConical class="h-12 w-12 mx-auto text-slate-300" />
          <p class="mt-3 text-sm">选一份 scenario 模板开始</p>
        </div>

        <template v-else>
          <!-- 头部 + datasource picker + risk panel + action buttons -->
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
              <label class="flex items-center gap-2 text-sm pb-1.5" title="先走 LLM 把 realistic 列填业务化样本池">
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

            <!-- 风险面板 — 嵌在 datasource picker 下方 -->
            <div class="mt-4">
              <OperationRiskPanel :datasource="selectedDs" context="scenario-lab" />
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
              >
                <Play class="h-4 w-4" :class="{ 'animate-pulse': store.materializing }" />
                {{ store.materializing ? '生成中…' : '仅生成数据' }}
              </button>
              <button
                class="btn btn-outline"
                :disabled="!store.datasourceId || store.recording || store.sandboxWriteLocked"
                @click="store.runRecord"
              >
                <ListChecks class="h-4 w-4" />
                {{ store.recording ? '建任务中…' : '建对比任务' }}
              </button>
              <button
                class="btn btn-outline"
                :disabled="store.verifying"
                @click="store.runVerify"
                title="对比 yml expected vs actual run summary"
              >
                <ShieldCheck class="h-4 w-4" />
                {{ store.verifying ? '校验中…' : '回归校验' }}
              </button>
            </div>

            <!-- 跳 SQL 诊断的链接 -->
            <div class="mt-3 text-xs text-slate-500">
              💡 仅想看执行计划?去
              <a href="#/sql-diagnosis" class="text-primary hover:underline inline-flex items-center gap-1">
                <Microscope class="h-3 w-3" /> SQL 诊断
              </a>
              直接粘 SQL 跑 EXPLAIN(不依赖 scenario 模板)
            </div>
          </div>

          <!-- 三栏:表 / 偏差 / 工作负载 -->
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
                  <li v-for="(value, name) in store.detail.variables" :key="name" class="flex items-center gap-2 sql-font">
                    <span class="text-primary font-medium">{{ name }}</span>
                    <span class="text-slate-400">→</span>
                    <span class="text-slate-700">{{ value }}</span>
                  </li>
                </ul>
              </div>
            </div>
          </div>

          <ResultPanels />
        </template>
      </div>
    </div>
  </section>
</template>
