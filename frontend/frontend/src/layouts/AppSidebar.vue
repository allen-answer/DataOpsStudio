<script setup>
import { useRoute } from 'vue-router'
import {
  Database,
  Network,
  GitCompareArrows,
  Workflow,
  GitBranch,
  History as HistoryIcon,
  FolderOpen,
  Users,
  ScrollText,
  Bot,
  Activity,
  Tag,
  Microscope,
  FlaskConical,
} from 'lucide-vue-next'
import { storeToRefs } from 'pinia'
import { useAuthStore } from '../stores/auth'
import { useProjectStore } from '../stores/project'
import { useBootstrapStore } from '../stores/bootstrap'

// 导航项：order 决定 sidebar 上下顺序，icon 来自 lucide。
// matchPaths 用于 active 高亮 —— 当前 route 以这些前缀任一开头即认为命中。
// Phase 4：单脚本血缘 + 多脚本分析合并为"血缘分析"，path 默认 /lineage；
// /batch-lineage 仍是合法路径（保留外部链接兼容），同样高亮该项。
// label 走 i18n —— labelKey 是 messages 里的 key，模板用 $t(item.labelKey)
const NAV_ITEMS = [
  { id: 'datasources',   labelKey: 'nav.datasources',  icon: Database,         path: '/datasources',   matchPaths: ['/datasources'] },
  { id: 'scenarios',     labelKey: 'nav.scenarios',    icon: FlaskConical,     path: '/scenarios',     matchPaths: ['/scenarios'] },
  { id: 'data-compare',  labelKey: 'nav.dataCompare',  icon: GitCompareArrows, path: '/data-compare',  matchPaths: ['/data-compare'] },
  { id: 'sql-optimize',  labelKey: 'nav.sqlOptimize',  icon: Microscope,       path: '/sql-optimize',  matchPaths: ['/sql-optimize', '/admin/sandbox'] },
  { id: 'workflows',     labelKey: 'nav.workflows',    icon: Workflow,         path: '/workflows',     matchPaths: ['/workflows', '/workflow-runs'] },
  { id: 'lineage',       labelKey: 'nav.lineage',      icon: GitBranch,        path: '/lineage',       matchPaths: ['/lineage', '/batch-lineage'] },
  { id: 'history',       labelKey: 'nav.history',      icon: HistoryIcon,      path: '/history',       matchPaths: ['/history'] },
]

// admin-only nav 项：仅 admin role 可见。Phase 14 P0-1:sandbox 移出 admin
// 升级到顶级 NAV_ITEMS(SQL 优化是数据工程师日常,不是 admin 特权)
const ADMIN_NAV_ITEMS = [
  { id: 'ai',         labelKey: 'adminNav.ai',         icon: Bot,        path: '/admin/ai',         matchPaths: ['/admin/ai'] },
  { id: 'users',      labelKey: 'adminNav.users',      icon: Users,      path: '/admin/users',      matchPaths: ['/admin/users'] },
  { id: 'audit',      labelKey: 'adminNav.audit',      icon: ScrollText, path: '/admin/audit',      matchPaths: ['/admin/audit'] },
  { id: 'projects',   labelKey: 'adminNav.projects',   icon: FolderOpen, path: '/admin/projects',   matchPaths: ['/admin/projects'] },
  { id: 'scheduler',  labelKey: 'adminNav.scheduler',  icon: Activity,   path: '/admin/scheduler',  matchPaths: ['/admin/scheduler'] },
  { id: 'governance', labelKey: 'adminNav.governance', icon: Tag,        path: '/admin/governance', matchPaths: ['/admin/governance'] },
]

const route = useRoute()
const authStore = useAuthStore()
const projectStore = useProjectStore()
const bootstrapStore = useBootstrapStore()
const { isAdmin } = storeToRefs(authStore)
const { currentProjectId, projects } = storeToRefs(projectStore)
const { driverItems } = storeToRefs(bootstrapStore)
// 切项目后用 bootstrapStore.reload 重拉数据；不需要 App.vue 的 loadBootstrap
// 那一层"自动选第一个任务"副作用 —— 只刷列表，让用户保持当前选择
const reloadBootstrap = bootstrapStore.reload

function isActive(item) {
  return item.matchPaths.some(p => route.path === p || route.path.startsWith(p + '/'))
}

async function onProjectChange(event) {
  projectStore.setProject(event.target.value || '')
  await reloadBootstrap()
}
</script>

<template>
  <aside class="flex h-screen w-64 shrink-0 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-fg">
    <!-- Logo -->
    <div class="flex h-16 shrink-0 items-center gap-3 border-b border-sidebar-border px-6">
      <div class="grid h-9 w-9 place-items-center rounded-lg bg-primary text-white">
        <Network class="h-5 w-5" />
      </div>
      <div class="min-w-0">
        <div class="truncate text-sm font-semibold text-white">DataOps Studio</div>
        <div class="truncate text-[11px] text-sidebar-fg/60">数据运维平台</div>
      </div>
    </div>

    <!-- 项目切换 dropdown：值为空 = "全部项目（包括未关联）" -->
    <div class="border-b border-sidebar-border px-3 py-3">
      <label class="mb-1.5 flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wider text-sidebar-fg/50">
        <FolderOpen class="h-3 w-3" />
        当前项目
      </label>
      <select
        :value="currentProjectId"
        class="w-full rounded-lg border-0 bg-sidebar-accent px-2.5 py-1.5 text-xs text-white focus:outline-none focus:ring-2 focus:ring-primary"
        @change="onProjectChange"
      >
        <option value="">全部项目</option>
        <option v-for="p in projects" :key="p.id" :value="p.id">{{ p.name }}</option>
      </select>
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
        <span class="truncate">{{ $t(item.labelKey) }}</span>
      </router-link>

      <!-- Admin nav 区段（仅 admin） -->
      <template v-if="isAdmin">
        <div class="mt-4 px-3 py-1 text-[10px] font-bold uppercase tracking-wider text-sidebar-fg/40">
          {{ $t('adminNav.sectionLabel') }}
        </div>
        <router-link
          v-for="item in ADMIN_NAV_ITEMS"
          :key="item.id"
          :to="item.path"
          class="group flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition"
          :class="isActive(item)
            ? 'bg-primary text-white shadow-sm'
            : 'text-sidebar-fg/80 hover:bg-sidebar-accent hover:text-white'"
        >
          <component :is="item.icon" class="h-5 w-5 shrink-0" :class="isActive(item) ? 'text-white' : 'text-sidebar-fg/60 group-hover:text-white'" />
          <span class="truncate">{{ $t(item.labelKey) }}</span>
        </router-link>
      </template>
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
