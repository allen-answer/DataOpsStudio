<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import { storeToRefs } from 'pinia'
import { ChevronLeft, ChevronRight, Play } from 'lucide-vue-next'
import WorkbenchTaskList from './workbench/WorkbenchTaskList.vue'
import WorkbenchStepBar from './workbench/WorkbenchStepBar.vue'
import WorkbenchSummary from './workbench/WorkbenchSummary.vue'
import StepSource from './workbench/StepSource.vue'
import StepRules from './workbench/StepRules.vue'
import StepMapping from './workbench/StepMapping.vue'
import StepResult from './workbench/StepResult.vue'
import { useTaskStore } from '../stores/task'

// Phase 2 重构：步骤式工作台 + 任务列表 + 摘要面板
// 4 步：source → rules → mapping → result
// 每步可点击步骤条跳转，下一步按钮线性前进。

type StepId = 'source' | 'rules' | 'mapping' | 'result'
const STEP_IDS: StepId[] = ['source', 'rules', 'mapping', 'result']

const currentStep = ref<StepId>('source')

const taskStore = useTaskStore()
const { taskDraft } = taskStore  // reactive 直接拿
const {
  selectedTaskId, isSavedTask, taskValidationIssues, canSaveTask,
} = storeToRefs(taskStore)
const { runTask } = taskStore

const search = ref('')

// 切换任务时回到第 1 步（避免在新任务的第 4 步空着结果区）
watch(selectedTaskId, () => { currentStep.value = 'source' })

// 步骤完成度：用于步骤条上显示对勾。简单启发——
//   source ready：源数据源 + SQL 或 Excel 至少一项填了
//   rules  ready：主键不为空
//   mapping ready：始终算"未阻塞"（mapping 是可选）
//   result ready：有 compareResult 不在这里展示
const completion = computed(() => {
  const hasSource = taskDraft.source_kind === 'excel'
    ? !!taskDraft.source_excel_path
    : !!taskDraft.source_id && !!taskDraft.source_sql?.trim()
  const hasKey = (Array.isArray(taskDraft.key_columns)
    ? taskDraft.key_columns.length
    : (taskDraft.key_columns || '').trim().length) > 0
  return {
    source: hasSource && !!taskDraft.name?.trim(),
    rules: hasKey,
    mapping: true,
    result: false,
  }
})

const currentIndex = computed(() => STEP_IDS.indexOf(currentStep.value))
const canPrev = computed(() => currentIndex.value > 0)
const canNext = computed(() => currentIndex.value < STEP_IDS.length - 1)

// 各 step 的错误 issue 数 —— 给步骤条标红用
const stepErrorCounts = computed<Record<StepId, number>>(() => {
  const counts: Record<StepId, number> = { source: 0, rules: 0, mapping: 0, result: 0 }
  for (const issue of taskValidationIssues.value as Array<{ level: string; step: StepId }>) {
    if (issue.level !== 'error') continue
    counts[issue.step] = (counts[issue.step] || 0) + 1
  }
  return counts
})

function goPrev() { if (canPrev.value) currentStep.value = STEP_IDS[currentIndex.value - 1] }
function goNext() { if (canNext.value) currentStep.value = STEP_IDS[currentIndex.value + 1] }
</script>

<template>
  <div class="grid gap-4 lg:grid-cols-[260px_minmax(0,1fr)_300px]">
    <!-- 左：任务列表 -->
    <WorkbenchTaskList v-model:search="search" />

    <!-- 中：步骤条 + 当前步骤主区 + 上下步导航 -->
    <main class="space-y-4">
      <WorkbenchStepBar
        :current="currentStep"
        :completion="completion"
        :error-counts="stepErrorCounts"
        @change="(s) => currentStep = s"
      />

      <StepSource v-if="currentStep === 'source'" />
      <StepRules v-else-if="currentStep === 'rules'" />
      <StepMapping v-else-if="currentStep === 'mapping'" />
      <StepResult v-else-if="currentStep === 'result'" />

      <!-- 步骤导航：上一步 / 下一步 / 最后一步直接执行 -->
      <div class="flex items-center justify-between rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-soft">
        <button class="btn btn-outline" :disabled="!canPrev" @click="goPrev">
          <ChevronLeft class="h-4 w-4" /> 上一步
        </button>
        <p class="muted text-[11px]">
          {{ currentIndex + 1 }} / {{ STEP_IDS.length }}
          <span v-if="!canSaveTask" class="ml-2 text-status-error">· 配置不完整，无法保存</span>
          <span v-else-if="!isSavedTask" class="ml-2 text-status-warning">· 任务未保存，先保存再执行</span>
        </p>
        <button v-if="canNext" class="btn btn-primary" @click="goNext">
          下一步 <ChevronRight class="h-4 w-4" />
        </button>
        <button v-else class="btn btn-primary" :disabled="!isSavedTask || !canSaveTask" @click="runTask">
          <Play class="h-4 w-4" /> 执行对比
        </button>
      </div>
    </main>

    <!-- 右：摘要面板 -->
    <WorkbenchSummary />
  </div>
</template>
