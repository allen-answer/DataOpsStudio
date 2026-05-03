<script setup>
// 图谱 wrapper —— 包装现有 G6 LineageGraph 组件。
// LineageGraph 接受 groups + edges 两个数组（不是 result）。这里直接转发。
import { defineAsyncComponent } from 'vue'

const LineageGraph = defineAsyncComponent(() => import('../LineageGraph.vue'))

defineProps({
  groups: { type: Array, default: () => [] },
  edges:  { type: Array, default: () => [] },
})
</script>

<template>
  <section class="card overflow-hidden p-0">
    <div class="border-b border-slate-200 bg-slate-50 px-4 py-3">
      <h3 class="text-base font-semibold text-slate-800">表级血缘图</h3>
      <p class="muted text-xs">G6 拓扑：节点 = 表，边 = 写入关系；hover 节点查看角色 / 业务分组</p>
    </div>
    <div class="p-2">
      <LineageGraph :groups="groups" :edges="edges" />
    </div>
  </section>
</template>
