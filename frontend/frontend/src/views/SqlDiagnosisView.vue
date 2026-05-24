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
import { Microscope, FlaskConical } from 'lucide-vue-next'
import { useSqlDiagnosisStore } from '../stores/sqlDiagnosis'
import OperationRiskPanel from '../components/sql/OperationRiskPanel.vue'
import OperationPreviewModal from '../components/sql/OperationPreviewModal.vue'
import QuickOptimizeMode from './sql-optimize/QuickOptimizeMode.vue'

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

onMounted(() => {
  store.loadList()
  // 注册到 store —— runQuickAnalyze 走 store.confirmAnalyzePromise 拿确认
  store.confirmAnalyzePromise = requestConfirm
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
