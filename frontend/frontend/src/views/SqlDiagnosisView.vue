<script setup lang="ts">
/**
 * Phase 14 #3 — SQL 诊断 view
 *
 * 拆分自原 /sql-optimize。本 view 只承载真正 SQL 诊断:
 * - datasource 选择 + 操作风险面板
 * - SQL 编辑器 + preflight + analyze
 * - AI enrich / plan history / plan diff
 *
 * 不允许出现 materialize / DROP / run-all / record / 造数据等按钮。
 * 那些能力去 /scenario-lab。
 */
import { onMounted, computed, ref } from 'vue'
import { Microscope, FlaskConical, ArrowLeft, Database, Clock } from 'lucide-vue-next'
import { useSqlDiagnosisStore } from '../stores/sqlDiagnosis'
import OperationRiskPanel from '../components/sql/OperationRiskPanel.vue'
import OperationPreviewModal from '../components/sql/OperationPreviewModal.vue'
import QuickOptimizeMode from './sql-optimize/QuickOptimizeMode.vue'
import { takeSqlTransfer, type SqlTransfer } from '../utils/sqlTransfer'

const store = useSqlDiagnosisStore()

// 当前选中 ds — 用 diagnosableDatasources(MySQL/DM/Oracle 全支持)
const selectedDs = computed(() => {
  const id = store.quickDatasourceId || ''
  return (store.diagnosableDatasources as any[]).find((d: any) => d.id === id) || null
})

// Phase 14 #3 — OperationPreviewModal Promise-based 状态
const modalOpen = ref(false)
let modalResolver: ((v: boolean) => void) | null = null

function requestConfirm(): Promise<boolean> {
  return new Promise((resolve) => {
    modalResolver = resolve
    modalOpen.value = true
  })
}

function onModalClose(confirmed: boolean) {
  modalOpen.value = false
  modalResolver?.(confirmed)
  modalResolver = null
}

// v0.5 来源信息卡 —— 仅在用户从 SQL 工作台 / 历史"发送过来"时显示。
// 让用户在优化工作台清楚"我现在分析的是哪条 SQL,从哪个 console / datasource 来,
// 上次执行多慢"。手工进入这个 view 直接输入 SQL 的不显示。
const transferOrigin = ref<SqlTransfer | null>(null)

function clearOrigin() {
  transferOrigin.value = null
}

function formatElapsed(ms: number | undefined): string {
  if (ms == null) return ''
  if (ms < 1000) return `${ms} ms`
  return `${(ms / 1000).toFixed(2)} s`
}

onMounted(() => {
  store.loadList()
  // 注册到 store —— runQuickAnalyze 走 store.confirmAnalyzePromise 拿确认
  store.confirmAnalyzePromise = requestConfirm

  // Phase 4 (sql-workbench): 接收来自 SqlWorkbench 的 SQL transfer
  const transfer = takeSqlTransfer()
  if (transfer?.sql) {
    store.quickSql = transfer.sql
    if (transfer.datasourceId) store.quickDatasourceId = transfer.datasourceId
    // v0.5:有 source 字段才显示来源卡(避免老 transfer payload 触发空内容卡)
    if (transfer.source) transferOrigin.value = transfer
  }
})
</script>

<template>
  <section class="space-y-6">
    <div class="flex items-end justify-between">
      <div>
        <h2 class="text-2xl font-bold text-slate-800 flex items-center gap-2">
          <Microscope class="h-7 w-7 text-primary" />
          SQL 诊断
        </h2>
        <p class="mt-1 text-sm text-slate-500">
          静态 preflight + EXPLAIN 执行计划 + AI 复核 + plan history / diff。
          不修改业务数据。
        </p>
      </div>
      <div class="text-xs text-slate-500">
        想要造数据 / 跑回归校验? 去
        <a href="#/scenario-lab" class="text-primary hover:underline inline-flex items-center gap-1">
          <FlaskConical class="h-3 w-3" /> 场景测试沙盒
        </a>
      </div>
    </div>

    <!-- v0.5 来源信息卡 —— 仅当从 SQL 工作台/历史 sendTo 过来时显示 -->
    <div
      v-if="transferOrigin"
      class="rounded-lg border-2 border-primary/30 bg-primary-light/50 p-3 flex items-center gap-3"
    >
      <ArrowLeft class="h-4 w-4 text-primary shrink-0" />
      <div class="flex-1 text-xs text-slate-700 flex items-center gap-3 flex-wrap">
        <span>
          来源:<strong>{{ transferOrigin.source === 'sql-workbench-history' ? 'SQL 工作台历史' : 'SQL 工作台' }}</strong>
        </span>
        <span v-if="transferOrigin.consoleName" class="flex items-center gap-1">
          · console:<strong class="sql-font">{{ transferOrigin.consoleName }}</strong>
        </span>
        <span v-if="transferOrigin.datasourceName" class="flex items-center gap-1">
          <Database class="h-3 w-3 text-slate-400" />
          {{ transferOrigin.datasourceName }}
          <span v-if="transferOrigin.datasourceDbType" class="text-[10px] text-slate-400">({{ transferOrigin.datasourceDbType }})</span>
        </span>
        <span v-if="transferOrigin.elapsedMs != null" class="flex items-center gap-1">
          <Clock class="h-3 w-3 text-slate-400" />
          上次执行 <strong>{{ formatElapsed(transferOrigin.elapsedMs) }}</strong>
          <span
            v-if="transferOrigin.elapsedMs >= 3000"
            class="rounded bg-status-warning-bg text-status-warning px-1 py-0.5 text-[9px] font-bold ml-1"
          >⚡SLOW</span>
        </span>
      </div>
      <button
        class="text-xs text-slate-500 hover:text-slate-800 px-2 py-0.5 rounded hover:bg-white"
        title="清除来源标记(SQL 不会清,继续保留在编辑器里)"
        @click="clearOrigin"
      >
        ✕
      </button>
    </div>

    <!-- 操作风险面板:显示选中 ds 的环境 + allow_* 状态 + 红线提示 -->
    <OperationRiskPanel :datasource="selectedDs" context="sql-diagnosis" />

    <!-- QuickOptimizeMode 包含整个粘 SQL / preflight / analyze / enrich / plan diff 流程 -->
    <QuickOptimizeMode />

    <!-- Operation Preview Modal — analyze 前显示 -->
    <OperationPreviewModal
      :open="modalOpen"
      :datasource="selectedDs"
      @close="onModalClose"
    />
  </section>
</template>
