<script setup>
import { inject } from 'vue'
import { useRoute } from 'vue-router'
import {
  Database,
  GitCompareArrows,
  Workflow,
  GitBranch,
  Layers,
  History as HistoryIcon,
} from 'lucide-vue-next'

// 导航项：order 决定 sidebar 上下顺序，icon 来自 lucide。
// matchPaths 用于 active 高亮 —— 当前 route 以这些前缀任一开头即认为命中。
const NAV_ITEMS = [
  { id: 'datasources',   label: '数据源',       icon: Database,         path: '/datasources',   matchPaths: ['/datasources'] },
  { id: 'data-compare',  label: '数据对比',     icon: GitCompareArrows, path: '/data-compare',  matchPaths: ['/data-compare'] },
  { id: 'workflows',     label: '作业流',       icon: Workflow,         path: '/workflows',     matchPaths: ['/workflows', '/workflow-runs'] },
  { id: 'lineage',       label: '单脚本血缘',   icon: GitBranch,        path: '/lineage',       matchPaths: ['/lineage'] },
  { id: 'batch-lineage', label: '多脚本分析',   icon: Layers,           path: '/batch-lineage', matchPaths: ['/batch-lineage'] },
  { id: 'history',       label: '执行历史',     icon: HistoryIcon,      path: '/history',       matchPaths: ['/history'] },
]

const route = useRoute()

// app context（driverItems 是 App.vue 暴露的 computed ref，模板自动解包；
// loadBootstrap 是 async 函数）
const { driverItems, loadBootstrap } = inject('app', { driverItems: [], loadBootstrap: () => {} })

function isActive(item) {
  return item.matchPaths.some(p => route.path === p || route.path.startsWith(p + '/'))
}
</script>

<template>
  <aside class="flex h-screen w-64 shrink-0 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-fg">
    <!-- Logo -->
    <div class="flex h-16 shrink-0 items-center gap-3 border-b border-sidebar-border px-6">
      <div class="grid h-9 w-9 place-items-center rounded-lg bg-primary text-white">
        <Database class="h-5 w-5" />
      </div>
      <div class="min-w-0">
        <div class="truncate text-sm font-semibold text-white">DataOps Studio</div>
        <div class="truncate text-[11px] text-sidebar-fg/60">数据运维平台</div>
      </div>
    </div>

    <!-- Nav -->
    <nav class="flex-1 space-y-1 overflow-y-auto px-3 py-4">
      <router-link
        v-for="item in NAV_ITEMS"
        :key="item.id"
        :to="item.path"
        class="group flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition"
        :class="isActive(item)
          ? 'bg-primary text-white shadow-sm'
          : 'text-sidebar-fg/80 hover:bg-sidebar-accent hover:text-white'"
      >
        <component :is="item.icon" class="h-5 w-5 shrink-0" :class="isActive(item) ? 'text-white' : 'text-sidebar-fg/60 group-hover:text-white'" />
        <span class="truncate">{{ item.label }}</span>
      </router-link>
    </nav>

    <!-- Driver detection -->
    <div class="m-3 rounded-xl border border-sidebar-border/80 bg-sidebar-accent/40 p-3">
      <div class="mb-2 flex items-center justify-between">
        <span class="text-[10px] font-bold uppercase tracking-wider text-sidebar-fg/50">数据库驱动</span>
        <button
          class="text-[10px] font-bold text-sidebar-fg/50 transition hover:text-white"
          @click="loadBootstrap"
        >
          刷新
        </button>
      </div>
      <div class="grid grid-cols-2 gap-1.5 text-[11px]">
        <div v-for="[name, info] in driverItems" :key="name" class="flex items-center truncate">
          <span class="mr-1.5 h-1.5 w-1.5 shrink-0 rounded-full" :class="info.available ? 'bg-status-success' : 'bg-sidebar-fg/30'"></span>
          <span :class="info.available ? 'text-sidebar-fg' : 'text-sidebar-fg/40'">{{ name }}</span>
        </div>
      </div>
    </div>
  </aside>
</template>
