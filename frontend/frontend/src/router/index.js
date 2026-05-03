/**
 * 顶级路由 + 工作流详情/运行的深链 + 登录页。
 *
 * Phase 1 决策：
 * - createWebHashHistory，避免后端 spa fallback 配置（/static/spa/ 永远命中
 *   index.html，hash 路由刷新不打 404）
 *
 * D-MVP（多项目空间）：
 * - 加 /login 路由，meta.public = true 跳过 auth 守卫
 * - beforeEach 守卫：未登录访问 protected 路由 → 跳 /login?redirect=...
 */
import { createRouter, createWebHashHistory } from 'vue-router'

import DatasourceView from '../views/DatasourceView.vue'
import WorkbenchView from '../views/WorkbenchView.vue'
import WorkflowView from '../views/WorkflowView.vue'
import LineageWorkbenchView from '../views/LineageWorkbenchView.vue'
import HistoryView from '../views/HistoryView.vue'
import LoginView from '../views/LoginView.vue'

const routes = [
  { path: '/', redirect: '/datasources' },
  { path: '/login', name: 'login', component: LoginView, meta: { public: true } },

  { path: '/datasources',   name: 'datasources',   component: DatasourceView },
  { path: '/data-compare',  name: 'data-compare',  component: WorkbenchView },

  { path: '/workflows',                 name: 'workflows',        component: WorkflowView },
  { path: '/workflows/:id',             name: 'workflow-detail',  component: WorkflowView, props: true },
  { path: '/workflow-runs/:runId',      name: 'workflow-run',     component: WorkflowView, props: true },

  { path: '/lineage',       name: 'lineage',       component: LineageWorkbenchView },
  { path: '/batch-lineage', name: 'batch-lineage', component: LineageWorkbenchView },
  { path: '/history',       name: 'history',       component: HistoryView },

  { path: '/:pathMatch(.*)*', redirect: '/datasources' },
]

export const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

// 全局 auth 守卫：未登录访问 protected 路由 → 跳 /login?redirect=<path>。
// 不在这里 import useAuthStore（router 在 main.js 里 use(pinia) 之前已经
// 实例化），改成读 localStorage —— 跟 api.js 一致的 SoT。
router.beforeEach((to, from, next) => {
  if (to.meta.public) return next()
  const token = localStorage.getItem('dataops.token') || ''
  if (!token) {
    return next({ path: '/login', query: { redirect: to.fullPath } })
  }
  next()
})

export default router
