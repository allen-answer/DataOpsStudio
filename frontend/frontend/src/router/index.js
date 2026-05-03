/**
 * 6 个顶级路由 + 工作流详情/运行的深链。
 *
 * Phase 1 决策：
 * - 用 createWebHashHistory，避免后端 fastapi static 还要单独配 spa fallback
 *   （现在 /static/spa/ 是一个 SPA bundle，hash 路由刷新永远命中根 index.html）
 * - WorkflowView 当前内部用 selectedWorkflowId/selectedRunId 切 list/detail/run，
 *   Phase 1 不强制深链复原选中状态——刷新后回到列表也算可接受。后续 Phase 2/3
 *   再做 :id / :runId → 内部状态的 watch 同步。
 */
import { createRouter, createWebHashHistory } from 'vue-router'

import DatasourceView from '../views/DatasourceView.vue'
import WorkbenchView from '../views/WorkbenchView.vue'
import WorkflowView from '../views/WorkflowView.vue'
// Phase 4：合并入口 LineageWorkbenchView 接管 /lineage 和 /batch-lineage 两条路径
// 旧 LineageView / BatchView 文件保留在 views/ 但路由不再引用（git 历史可回滚）
import LineageWorkbenchView from '../views/LineageWorkbenchView.vue'
import HistoryView from '../views/HistoryView.vue'

const routes = [
  { path: '/', redirect: '/datasources' },

  { path: '/datasources',   name: 'datasources',   component: DatasourceView },
  { path: '/data-compare',  name: 'data-compare',  component: WorkbenchView },

  { path: '/workflows',                 name: 'workflows',        component: WorkflowView },
  { path: '/workflows/:id',             name: 'workflow-detail',  component: WorkflowView, props: true },
  { path: '/workflow-runs/:runId',      name: 'workflow-run',     component: WorkflowView, props: true },

  // Phase 4：两个 path 共用 LineageWorkbenchView，view 内部根据 route.path
  // 决定默认 mode（/lineage → paste，/batch-lineage → multi）
  { path: '/lineage',       name: 'lineage',       component: LineageWorkbenchView },
  { path: '/batch-lineage', name: 'batch-lineage', component: LineageWorkbenchView },
  { path: '/history',       name: 'history',       component: HistoryView },

  // Catch-all → 数据源（首屏）
  { path: '/:pathMatch(.*)*', redirect: '/datasources' },
]

export const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

export default router
