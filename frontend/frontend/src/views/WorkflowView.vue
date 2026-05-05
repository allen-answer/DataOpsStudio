<script setup>
import { ref, watch, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { storeToRefs } from 'pinia'
import WorkflowListView from './workflow/WorkflowListView.vue'
import WorkflowDetailView from './workflow/WorkflowDetailView.vue'
import WorkflowRunView from './workflow/WorkflowRunView.vue'
import WorkflowTemplateView from './workflow/WorkflowTemplateView.vue'
import { useBootstrapStore } from '../stores/bootstrap'
import { useWorkflowStore } from '../stores/workflow'
import { useNoticeStore } from '../stores/notice'

const { state } = useBootstrapStore()
const workflowStore = useWorkflowStore()
const { selectedWorkflowId, workflowResult } = storeToRefs(workflowStore)
const { selectWorkflow, loadWorkflowRunDetail, runWorkflowAsync } = workflowStore
const { setNotice } = useNoticeStore()

const route = useRoute()
const subPage = ref('list')      // list / detail / run / templates

// 深链支持：/workflows/:id → 详情；/workflow-runs/:runId → 运行详情。
// 不接 route.params 的话，浏览器直接输 URL 进来永远停留在总览（之前的 bug）。
async function syncFromRoute() {
  const wfId = route.params.id
  const runId = route.params.runId
  if (runId) {
    try {
      await loadWorkflowRunDetail(runId)
      subPage.value = 'run'
    } catch {
      subPage.value = 'list'
    }
    return
  }
  if (wfId) {
    selectWorkflow(wfId)
    subPage.value = 'detail'
    return
  }
  subPage.value = 'list'
}

onMounted(syncFromRoute)
watch(() => [route.params.id, route.params.runId], syncFromRoute)

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

// 顶部 subnav 切换：单纯切 subPage 不够 ——「详情」/「运行详情」对状态有依赖。
// - 点详情：没选中过任何作业流（仍是初始 'new' 态）+ 库里有作业流 → 自动选第一个，避免半初始化空白
// - 点详情：库里没作业流 → 仍允许进入，进去后会引导新建（'new' 态）
// - 点运行详情：没有 workflowResult（没看过任何 run）→ 提示先去详情选一个 run
const goSubPage = (id) => {
  if (id === 'detail') {
    if (selectedWorkflowId.value === 'new' && state.workflows?.length) {
      selectWorkflow(state.workflows[0].id)
    }
  } else if (id === 'run') {
    if (!workflowResult.value) {
      setNotice?.('请先在「作业流详情」选择一次运行记录查看')
      return
    }
  }
  subPage.value = id
}

const subnav = [
  { id: 'list',   label: '作业流总览', hint: '健康度 / 调度 / 影响' },
  { id: 'templates', label: '作业流模板', hint: '复用 / 创建' },
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
              @click="goSubPage(page.id)">
        <span class="text-[12.5px] font-semibold">{{ page.label }}</span>
        <span class="text-[10.5px]" :class="subPage === page.id ? 'text-blue-100' : 'text-slate-400'">{{ page.hint }}</span>
      </button>
      <div class="ml-auto px-3 text-[10.5px] text-slate-400">
        作业流 / DataOps 控制台
      </div>
    </nav>

    <WorkflowListView v-if="subPage === 'list'" @open-detail="goDetail" @run="runFromList" />
    <WorkflowTemplateView v-else-if="subPage === 'templates'" @open-detail="goDetail" />
    <WorkflowDetailView v-else-if="subPage === 'detail'" @back="goList" @open-run="goRun" />
    <WorkflowRunView v-else-if="subPage === 'run'" @back="subPage = 'detail'" @open-detail="goDetailFromRun" />
  </section>
</template>
