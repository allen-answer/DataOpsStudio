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
import AssetDetailView from '../views/AssetDetailView.vue'
import LoginView from '../views/LoginView.vue'
import UserManagementView from '../views/admin/UserManagementView.vue'
import AuditLogView from '../views/admin/AuditLogView.vue'
import ProjectManagementView from '../views/admin/ProjectManagementView.vue'
import AIConfigView from '../views/admin/AIConfigView.vue'
import SchedulerMonitorView from '../views/admin/SchedulerMonitorView.vue'

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

  // Phase 10 #4：表资产详情页 —— 反向查找谁引用此表
  // :name 用 :pathMatch 接受含点号 / 斜杠的表名（如 ods.t_users）
  { path: '/assets/table/:name(.*)', name: 'asset-table', component: AssetDetailView, props: true },

  // Admin —— 仅 admin 可访问，sidebar 也只在 admin role 下显示
  { path: '/admin/users',    name: 'admin-users',    component: UserManagementView,    meta: { adminOnly: true } },
  { path: '/admin/audit',    name: 'admin-audit',    component: AuditLogView,           meta: { adminOnly: true } },
  { path: '/admin/projects', name: 'admin-projects', component: ProjectManagementView,  meta: { adminOnly: true } },
  { path: '/admin/ai',         name: 'admin-ai',         component: AIConfigView,          meta: { adminOnly: true } },
  { path: '/admin/scheduler',  name: 'admin-scheduler',  component: SchedulerMonitorView,  meta: { adminOnly: true } },

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
  // adminOnly 守卫：非 admin 访问 admin 页面 → 跳数据源页（接口侧也会 403 兜底）
  if (to.meta.adminOnly) {
    let role = ''
    try {
      const raw = localStorage.getItem('dataops.user') || ''
      role = raw ? JSON.parse(raw).role : ''
    } catch {
      role = ''
    }
    if (role !== 'admin') return next({ path: '/datasources' })
  }
  next()
})

export default router
