<script setup>
import { inject, ref } from 'vue'
import WorkflowListView from './workflow/WorkflowListView.vue'
import WorkflowDetailView from './workflow/WorkflowDetailView.vue'
import WorkflowRunView from './workflow/WorkflowRunView.vue'

const { selectWorkflow, loadWorkflowRunDetail, runWorkflowAsync } = inject('app')

const subPage = ref('list')      // list / detail / run

const goDetail = (workflowId) => {
  selectWorkflow(workflowId)
  subPage.value = 'detail'
}
const goRun = async (runId) => {
  await loadWorkflowRunDetail(runId)
  subPage.value = 'run'
}
const goList = () => { subPage.value = 'list' }
const goDetailFromRun = (workflowId) => {
  if (workflowId) selectWorkflow(workflowId)
  subPage.value = 'detail'
}

// 列表页"立即运行"：先选中再跳到详情页跑，保留可观测性
const runFromList = (workflowId) => {
  selectWorkflow(workflowId)
  subPage.value = 'detail'
  runWorkflowAsync()
}

const subnav = [
  { id: 'list',   label: '作业流总览', hint: '健康度 / 调度 / 影响' },
  { id: 'detail', label: '作业流详情', hint: 'DAG / 配置 / 历史' },
  { id: 'run',    label: '运行详情',   hint: '时间线 / 节点日志' },
]
</script>

<template>
  <section class="space-y-3">
    <!-- 子导航：tab 风格的页面切换 -->
    <nav class="flex items-center gap-1 rounded-xl border border-slate-200 bg-white p-1 shadow-sm">
      <button v-for="page in subnav" :key="page.id"
              class="flex flex-col items-start rounded-lg px-3 py-1.5 text-left transition"
              :class="subPage === page.id ? 'bg-blue-600 text-white' : 'text-slate-600 hover:bg-slate-50'"
              @click="subPage = page.id">
        <span class="text-[12.5px] font-semibold">{{ page.label }}</span>
        <span class="text-[10.5px]" :class="subPage === page.id ? 'text-blue-100' : 'text-slate-400'">{{ page.hint }}</span>
      </button>
      <div class="ml-auto px-3 text-[10.5px] text-slate-400">
        作业流 / DataOps 控制台
      </div>
    </nav>

    <WorkflowListView v-if="subPage === 'list'" @open-detail="goDetail" @run="runFromList" />
    <WorkflowDetailView v-else-if="subPage === 'detail'" @back="goList" @open-run="goRun" />
    <WorkflowRunView v-else-if="subPage === 'run'" @back="subPage = 'detail'" @open-detail="goDetailFromRun" />
  </section>
</template>
