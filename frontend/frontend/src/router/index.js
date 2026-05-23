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
 *
 * Phase 10 后期：路由级 lazy loading —— 所有 view 改 () => import('...')，
 * vite 自动按 chunk 拆分。bundle 主入口只剩 router + auth + sidebar，首屏
 * 仅加载当前命中的 route chunk；admin views / WorkflowView 这些大组件
 * （含 G6 / Cytoscape / DAG canvas / CodeMirror）只在用户跳进去时才下载。
 *
 * Vite 给每个 dynamic import 自动生成独立 chunk，无需 webpack-style
 * `webpackChunkName`. 同 view 的多个 import 共享同一 chunk。
 */
import { createRouter, createWebHashHistory } from 'vue-router'

const routes = [
  { path: '/', redirect: '/datasources' },
  { path: '/login', name: 'login', component: () => import('../views/LoginView.vue'), meta: { public: true } },

  { path: '/datasources',   name: 'datasources',   component: () => import('../views/DatasourceView.vue') },
  { path: '/data-compare',  name: 'data-compare',  component: () => import('../views/WorkbenchView.vue') },

  // Workflow chain —— 三条 path 共享同一个 chunk（同 import 路径）
  { path: '/workflows',                 name: 'workflows',        component: () => import('../views/WorkflowView.vue') },
  { path: '/workflows/:id',             name: 'workflow-detail',  component: () => import('../views/WorkflowView.vue'), props: true },
  { path: '/workflow-runs/:runId',      name: 'workflow-run',     component: () => import('../views/WorkflowView.vue'), props: true },

  // Lineage —— 单脚本 + 批量都走 LineageWorkbenchView，含 G6 / Cytoscape，
  // chunk 体积大（~1.4MB G6 + ~534KB cytoscape），lazy 后首屏不加载
  { path: '/lineage',       name: 'lineage',       component: () => import('../views/LineageWorkbenchView.vue') },
  { path: '/batch-lineage', name: 'batch-lineage', component: () => import('../views/LineageWorkbenchView.vue') },
  { path: '/history',       name: 'history',       component: () => import('../views/HistoryView.vue') },

  // 账号安全 / MFA —— 所有登录用户都能进（自己管自己的 MFA）
  { path: '/account/security', name: 'account-security', component: () => import('../views/account/SecurityView.vue') },

  // Phase 10 #4：表资产详情页 —— 反向查找谁引用此表
  // :name 用 :pathMatch 接受含点号 / 斜杠的表名（如 ods.t_users）
  { path: '/assets/table/:name(.*)', name: 'asset-table', component: () => import('../views/AssetDetailView.vue'), props: true },

  // Admin —— 仅 admin 可访问，sidebar 也只在 admin role 下显示。lazy load 节省
  // 普通用户的首屏带宽（admin 占总人数 < 5% 的场景下尤其值得）
  { path: '/admin/users',     name: 'admin-users',     component: () => import('../views/admin/UserManagementView.vue'),    meta: { adminOnly: true } },
  { path: '/admin/audit',     name: 'admin-audit',     component: () => import('../views/admin/AuditLogView.vue'),           meta: { adminOnly: true } },
  { path: '/admin/projects',  name: 'admin-projects',  component: () => import('../views/admin/ProjectManagementView.vue'),  meta: { adminOnly: true } },
  { path: '/admin/ai',        name: 'admin-ai',        component: () => import('../views/admin/AIConfigView.vue'),          meta: { adminOnly: true } },
  { path: '/admin/scheduler', name: 'admin-scheduler', component: () => import('../views/admin/SchedulerMonitorView.vue'),  meta: { adminOnly: true } },
  { path: '/admin/governance', name: 'admin-governance', component: () => import('../views/admin/AspectGovernanceView.vue'), meta: { adminOnly: true } },
  { path: '/admin/sandbox',    name: 'admin-sandbox',    component: () => import('../views/admin/ScenarioSandboxView.vue'),  meta: { adminOnly: true } },

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
