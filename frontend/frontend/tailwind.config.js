/**
 * DataOps Studio 设计 token。
 *
 * 设计方向：现代科技控制台。主色 violet-600（#7c3aed），辅以 6 类语义状态色
 * 和 4 类血缘 tag 色。同时保留旧 alias（brand/ink/muted/line/panel/canvas）
 * 让 Phase 1 不破坏现有页面 —— 后续可逐步替换。
 *
 * 用法：
 *   - 主操作 / 链接 / 选中态 → bg-primary / text-primary
 *   - DataOps 状态徽章 → bg-status-success-bg text-status-success（6 种）
 *   - 血缘表角色 tag → bg-tag-source-bg text-tag-source（4 种）
 *   - 旧 alias 仍可用 —— 别一次全替换，按 view 渐进迁移
 */
export default {
  content: ['./index.html', './src/**/*.{vue,js}'],
  theme: {
    extend: {
      colors: {
        // ─── 旧 alias，保持兼容 ─────────────────────────────────────
        ink: '#1e293b',
        muted: '#64748b',
        line: '#e5e7ec',
        panel: '#ffffff',
        canvas: '#f8f9fb',
        brand: '#2563eb',  // 旧蓝主色 —— 旧组件用；新组件用 primary

        // ─── 新主色（紫蓝）────────────────────────────────────────
        primary: {
          DEFAULT: '#7c3aed',
          hover: '#6d28d9',
          light: '#f5f3ff',
          fg: '#ffffff',
        },

        // ─── 状态色：6 类，每类 base + bg ─────────────────────────
        status: {
          success: '#10b981',
          'success-bg': '#d1fae5',
          warning: '#f59e0b',
          'warning-bg': '#fef3c7',
          error: '#ef4444',
          'error-bg': '#fee2e2',
          info: '#3b82f6',
          'info-bg': '#dbeafe',
          running: '#8b5cf6',
          'running-bg': '#ede9fe',
          pending: '#6b7280',
          'pending-bg': '#f3f4f6',
        },

        // ─── 血缘表角色 tag：4 类 ─────────────────────────────────
        tag: {
          source: '#1e40af',
          'source-bg': '#dbeafe',
          target: '#065f46',
          'target-bg': '#d1fae5',
          intermediate: '#92400e',
          'intermediate-bg': '#fef3c7',
          reference: '#3730a3',
          'reference-bg': '#e0e7ff',
        },

        // ─── 侧边栏（深色）────────────────────────────────────────
        sidebar: {
          DEFAULT: '#1a1d2e',
          fg: '#e5e7eb',
          accent: '#2d3142',
          'accent-fg': '#ffffff',
          border: '#2d3142',
        },
      },
      boxShadow: {
        soft: '0 4px 12px rgba(15, 23, 42, 0.04)',
        // 紫主色 ring，给 focus / 选中态用
        ring: '0 0 0 3px rgba(124, 58, 237, 0.18)',
      },
    },
  },
  plugins: [],
}
