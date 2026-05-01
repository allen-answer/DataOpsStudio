<script setup>
import { ref } from 'vue'
import OpsListView from './operations/OpsListView.vue'
import OpsDagView from './operations/OpsDagView.vue'
import OpsRunView from './operations/OpsRunView.vue'

// Internal navigation between the three sub-views. Real product would use
// vue-router; here a local ref keeps the prototype self-contained.
const activePage = ref('list')   // list / dag / run
const focalWorkflowId = ref('')

const goDag = (workflowId) => { focalWorkflowId.value = workflowId; activePage.value = 'dag' }
const goRuns = (workflowId) => { focalWorkflowId.value = workflowId; activePage.value = 'run' }
const goList = () => { activePage.value = 'list' }

const subnav = [
  { id: 'list', label: '流程列表',     hint: '工作台首屏' },
  { id: 'dag',  label: 'DAG 编排',     hint: '画布 / 属性' },
  { id: 'run',  label: '运行实例 / 日志', hint: '诊断与重跑' },
]
</script>

<template>
  <section class="flex flex-col gap-3">
    <!-- sub-nav: clarifies the prototype has 3 pages, lets reviewers jump between them -->
    <nav class="flex items-center gap-1 rounded-md border border-slate-200 bg-white p-1">
      <button
        v-for="page in subnav" :key="page.id"
        class="flex flex-col items-start rounded px-3 py-1.5 text-left transition"
        :class="activePage === page.id ? 'bg-slate-900 text-white' : 'text-slate-600 hover:bg-slate-50'"
        @click="activePage = page.id"
      >
        <span class="text-[12.5px] font-semibold">{{ page.label }}</span>
        <span class="text-[10.5px]" :class="activePage === page.id ? 'text-slate-300' : 'text-slate-400'">{{ page.hint }}</span>
      </button>
      <div class="ml-auto flex items-center gap-2 px-3 text-[11px] text-slate-400">
        <span class="hidden md:inline">原型基于模拟数据 · 风格参考 DolphinScheduler / Airflow</span>
      </div>
    </nav>

    <OpsListView v-if="activePage === 'list'" @open-dag="goDag" @open-runs="goRuns" />
    <OpsDagView  v-else-if="activePage === 'dag'" @back="goList" @open-runs="goRuns" />
    <OpsRunView  v-else-if="activePage === 'run'" @back="goList" @open-dag="goDag" />
  </section>
</template>
