# Frontend Design System Rules (DataOps Studio SPA)

Rules for translating Figma designs into this codebase via the Figma MCP. Read alongside the root `CLAUDE.md`. Frontend-only — backend rules stay in the root file.

The Figma MCP returns React + Tailwind + design-token reference code. **Always adapt** to this project's stack: Vue 3 SFC, Tailwind v3 utility classes, the small set of `@layer components` shortcuts, and the existing inject('app') data flow. Never paste the MCP output verbatim.

---

## 1. Token definitions

All design tokens live in **`tailwind.config.js`** (`theme.extend`) and the `@layer base` / `@layer components` blocks of **`src/style.css`**. There is no Style Dictionary, no JSON token export, no CSS-vars layer — Tailwind's generated utilities are the source of truth.

### Project-specific palette (extend tokens)

```js
// tailwind.config.js — Phase 1+ token 表（详注释见文件本身）
colors: {
  // 旧 alias，保留向后兼容（旧 view 内仍在用）
  ink: '#1e293b', muted: '#64748b', line: '#e5e7ec',
  panel: '#ffffff', canvas: '#f8f9fb',
  brand: '#2563eb',  // 旧蓝主色，新组件用 primary

  // 新主色：紫蓝
  primary: { DEFAULT: '#7c3aed', hover: '#6d28d9', light: '#f5f3ff', fg: '#ffffff' },

  // 状态色 6 类（每类 base + bg）
  status: {
    success: '#10b981', 'success-bg': '#d1fae5',
    warning: '#f59e0b', 'warning-bg': '#fef3c7',
    error:   '#ef4444', 'error-bg':   '#fee2e2',
    info:    '#3b82f6', 'info-bg':    '#dbeafe',
    running: '#8b5cf6', 'running-bg': '#ede9fe',
    pending: '#6b7280', 'pending-bg': '#f3f4f6',
  },

  // 血缘表角色 tag 4 类
  tag: {
    source: '#1e40af', 'source-bg': '#dbeafe',
    target: '#065f46', 'target-bg': '#d1fae5',
    intermediate: '#92400e', 'intermediate-bg': '#fef3c7',
    reference:    '#3730a3', 'reference-bg':    '#e0e7ff',
  },

  // 深色 sidebar 配色
  sidebar: {
    DEFAULT: '#1a1d2e', fg: '#e5e7eb',
    accent: '#2d3142', 'accent-fg': '#ffffff', border: '#2d3142',
  },
}
boxShadow: {
  soft: '0 4px 12px rgba(15, 23, 42, 0.04)',
  ring: '0 0 0 3px rgba(124, 58, 237, 0.18)',  // 紫主色 focus ring
}
```

### Token mapping（Figma → Tailwind utility）

| Figma intent       | Use these classes                                            |
| ------------------ | ------------------------------------------------------------ |
| Primary text       | `text-slate-800` / `text-ink`                                |
| Secondary text     | `text-slate-500` / `text-muted`                              |
| Borders / dividers | `border-slate-200` / `border-line`                           |
| Page background    | `bg-canvas`                                                  |
| Panel / card       | `bg-white` / `bg-panel`                                      |
| **Primary action** | `bg-primary hover:bg-primary-hover` (新) / `bg-brand` (旧)   |
| Success            | `bg-status-success-bg text-status-success` 或 `.status-success` |
| Warning            | `bg-status-warning-bg text-status-warning` 或 `.status-warning` |
| Danger             | `bg-status-error-bg text-status-error` 或 `.status-error`    |
| Info               | `bg-status-info-bg text-status-info` 或 `.status-info`       |
| Running            | `bg-status-running-bg text-status-running` 或 `.status-running` |
| Pending            | `bg-status-pending-bg text-status-pending` 或 `.status-pending` |
| Tag: 来源表        | `.tag-base .tag-source`                                      |
| Tag: 目标表        | `.tag-base .tag-target`                                      |
| Tag: 中间表        | `.tag-base .tag-intermediate`                                |
| Tag: 参考表        | `.tag-base .tag-reference`                                   |
| Sidebar (dark)     | `bg-sidebar text-sidebar-fg border-sidebar-border`           |

### Typography

System stack only (defined once in `src/style.css`):

```css
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
```

Monospace is exposed via the `.sql-font` component class. Don't import web fonts unless the design absolutely demands it — match closest system stack first.

### Spacing & radius

Use Tailwind defaults (`p-4`, `gap-3`, `space-y-6`, `rounded-lg`, `rounded-xl`, `rounded-2xl`). No custom spacing scale. The `.card` shortcut uses `rounded-[10px]` — that's the only arbitrary value worth keeping.

---

## 2. Component library

### Architecture

Vue 3 Single-File Components, `<script setup>` syntax everywhere. No JSX, no Storybook.
**Phase 1+** 引入 `vue-router` —— 详见 §3 路由表。

```
src/
├── App.vue                 # Owns ALL global state via provide('app', {...}). Renders <AppShell><router-view/></AppShell>.
├── main.js                 # createApp(App).use(router).mount('#app')
├── style.css               # Tailwind directives + base/components layers
├── api.js                  # Tiny fetch wrappers: apiGet, apiJson, apiForm
├── router/
│   └── index.js            # Hash-based router, 6 top-level routes
├── layouts/                # Shell layout components
│   ├── AppShell.vue        # Outer flex: <AppSidebar/><AppTopBar/><router-view/>
│   ├── AppSidebar.vue      # Dark sidebar w/ lucide nav icons + driver-detect block
│   └── AppTopBar.vue       # Breadcrumbs + global actions (config export / search / bell)
├── views/                  # Routed views — inject('app'), NEVER take props
│   ├── DatasourceView.vue
│   ├── WorkbenchView.vue
│   ├── WorkflowView.vue
│   ├── LineageView.vue
│   ├── BatchView.vue
│   ├── HistoryView.vue
│   └── workflow/           # Workflow sub-views (List/Detail/Run)
└── components/             # Reusable presentational pieces — use defineProps/defineEmits
    ├── LineageGraph.vue
    ├── SqlEditor.vue
    └── workflow/           # Workflow node editors + DAG canvas + panels
```

### State pattern (CRITICAL)

`App.vue` holds every reactive ref, every API call, and exposes them via `provide('app', {...})`. Sub-views call `inject('app')` and destructure what they need. **Pinia is installed but unused** (legacy dep). Don't add a Pinia store without asking — the team is deferring that decision.

```vue
<!-- views/DatasourceView.vue — canonical view shape -->
<script setup>
import { inject } from 'vue'
const { state, datasourceDraft, createDatasource, deleteDatasource } = inject('app')
</script>
<template>
  <section class="space-y-6"> ... </section>
</template>
```

Components inside `components/` use props + emits, not inject. They are presentational.

### Reusable class shortcuts (use these instead of inlining)

Defined in `@layer components` in `src/style.css`:

| Class          | Purpose                                                        |
| -------------- | -------------------------------------------------------------- |
| `.btn`         | Base button shape (height, padding, focus, disabled)           |
| `.btn-primary` | Blue solid action                                              |
| `.btn-outline` | White w/ slate border                                          |
| `.btn-danger`  | Red solid                                                      |
| `.card`        | White panel: rounded, soft shadow, slate border                |
| `.pill`        | Compact rounded badge — pair with a `bg-*-100 text-*-700` pair |
| `.muted`       | Slate-500 small text                                           |
| `.sql-font`    | Monospace family for SQL / hashes / paths                      |

When the Figma design implies a new repeated pattern, **add a class to `@layer components`** rather than copy-pasting the utility chain across files.

### Recurring inline patterns (recognize, don't re-invent)

```vue
<!-- Tab/page header -->
<div class="flex items-end justify-between">
  <div>
    <h2 class="text-2xl font-bold text-slate-800">标题</h2>
    <p class="mt-1 text-sm text-slate-500">说明</p>
  </div>
  <button class="rounded-lg bg-blue-600 px-5 py-2 text-sm font-bold text-white hover:bg-blue-700">动作</button>
</div>

<!-- Status badge (use bg-*-100 + text-*-700 pairs) -->
<span class="rounded bg-green-100 px-2 py-1 text-[10px] font-bold uppercase tracking-widest text-green-700">已配置</span>

<!-- "Logo block" — text-only avatar -->
<div class="grid h-12 w-12 place-items-center rounded-xl bg-slate-100 text-sm font-black text-slate-600">DS</div>

<!-- Status dot -->
<span class="h-3 w-3 rounded-full" :class="ok ? 'bg-green-500' : 'bg-slate-300'"></span>

<!-- Card grid -->
<div class="grid grid-cols-1 gap-6 md:grid-cols-2 xl:grid-cols-3">
  <article class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm hover:shadow-md"> ... </article>
</div>
```

### Workflow component conventions (DAG / node editors)

If a Figma design touches the workflow editor, follow the existing split documented in the root CLAUDE.md:

- One `Workflow<Type>NodeEditor.vue` per node type — added via `v-if` dispatch in `WorkflowDetailView.vue`.
- DAG visualization → `WorkflowDagCanvas.vue` (SVG + auto layout, props: `nodes / latestRun / v-model:selectedNodeId`).
- Don't reach for a third-party DAG library — `@antv/g6` and `cytoscape` exist but are reserved for the **lineage** graph (`components/LineageGraph.vue` G6 + `components/LineageGraphCytoscape.vue` 实验中), not workflows. 数据派生在 `composables/useLineageGraphData.js`，两个引擎共享。

---

## 3. Frameworks & libraries

| Concern         | Choice                                                                |
| --------------- | --------------------------------------------------------------------- |
| Framework       | Vue 3.5 (`<script setup>`)                                            |
| Build / bundler | Vite 8 (`@vitejs/plugin-vue`)                                         |
| Styling         | Tailwind CSS 3.4 + autoprefixer + postcss                             |
| Graph viz       | `@antv/g6` 5（稳定） + `cytoscape` 3 + `cytoscape-dagre` 2（实验）—— lineage only |
| Code editor     | `@codemirror/*` 6 — SQL editor                                        |
| Utilities       | `@vueuse/core` (clipboard etc.); `@tanstack/vue-virtual` for big lists |
| Icons           | `lucide-vue-next` — controlled use only. See §5.                      |
| Routing         | `vue-router` 4 (hash mode). Top-level routes in `src/router/index.js` |
| State mgmt      | provide/inject. Pinia installed but unused — don't add stores         |
| Testing         | None on the frontend (e2e Playwright lives in repo `tests/e2e/`)     |

When MCP suggests Radix / shadcn / Headless UI / Heroicons: **don't install**. Replicate the shape with Tailwind utilities + the existing `.btn`/`.card`/`.pill`/`.status-badge`/`.tag-*` shortcuts. Lucide icons are now allowed (see §5).

### Routing (Phase 1+)

`src/router/index.js` registers six top-level routes via hash history (`createWebHashHistory`)
— FastAPI serves a single SPA index at `/spa`, hash routes mean we don't need a SPA fallback rule.

| Path                            | Component        | Notes                              |
| ------------------------------- | ---------------- | ---------------------------------- |
| `/datasources`                  | `DatasourceView` | redirected from `/`                |
| `/data-compare`                 | `WorkbenchView`  |                                    |
| `/workflows`                    | `WorkflowView`   | overview list                      |
| `/workflows/:id`                | `WorkflowView`   | detail (deep-link wired Phase 2/3) |
| `/workflow-runs/:runId`         | `WorkflowView`   | run detail                         |
| `/lineage`                      | `LineageView`    | single-script analysis             |
| `/batch-lineage`                | `BatchView`      | multi-script analysis              |
| `/history`                      | `HistoryView`    |                                    |

Active highlight in `AppSidebar.vue` matches via `route.path` prefix — see `NAV_ITEMS.matchPaths`.
`provide('app', {...})` in `App.vue` is unchanged; views still pull state via `inject('app')`.

### Build wiring (don't break this)

```js
// vite.config.js
base: '/static/spa/',
build: { outDir: '../../static/spa', emptyOutDir: true },
proxy: { '/api': 'http://app:8000', /* and friends */ },
```

The bundle is served by FastAPI. The `base: '/static/spa/'` is non-negotiable — changing it breaks asset URLs in production. `index.html` is served at `/spa` with `Cache-Control: no-cache`; the hashed assets in `assets/` are immutable.

---

## 4. Asset management

```
frontend/frontend/
├── public/                 # Served as-is at site root
│   ├── favicon.svg         # Referenced by index.html as /favicon.svg
│   └── icons.svg           # ⚠ UNUSED Vite-template leftover (Bluesky/Discord/X). Do not link to it.
└── src/assets/             # Imported by Vue components — Vite hashes & inlines
    ├── hero.png
    ├── vite.svg
    └── vue.svg
```

- Component-referenced art → `src/assets/`, then `import logo from '../assets/logo.svg'`.
- Truly static resources fetched by URL → `public/` (rare).
- No image optimizer / CDN. Keep PNGs small; prefer inline SVG for icons.
- The Vite default base is `/static/spa/`, so asset URLs in production resolve to `/static/spa/assets/<hash>.<ext>` automatically.

---

## 5. Icon system

**Allowed library: `lucide-vue-next`** — controlled use only. Phase 1+ (DataOps 控制台改造)
放开了原来"无图标库"的限制 —— 现代科技控制台需要导航/动作/状态层面的图标语义。
但要避免滥用：图标按钮必须有 `title` / 文本 label，不要靠图标本身传业务含义。

### When to use lucide

| 场景                                 | 用法                                                         |
| ------------------------------------ | ------------------------------------------------------------ |
| Sidebar 导航                         | 见 `AppSidebar.vue` `NAV_ITEMS` —— 每项一个 lucide 图标      |
| TopBar 全局动作                      | 配置导出 / 搜索 / 通知 等                                    |
| 页面主操作（"执行" / "新建" / "删除"） | 文字 + 前置图标（`<Play class="h-4 w-4" /> 执行`）           |
| 状态徽章 / DAG 节点类型              | 视情况配图标，但不要用图标替代 status-badge 的语义色         |

### When NOT to use

1. **正文内联**：表格 cell / `<p>` 段落 / SQL 编辑器等内容区，仍优先用文字徽章 / 颜色点。
2. **重复装饰**：同一行多个图标（动作菜单 + 状态 + 类型）会让信息密度变低 —— 留一个最关键的。
3. **图标按钮无 label**：必须有 `title` 或可访问性文本，纯 icon 按钮在中文工具栏里不够直观。
4. **替代文字 logo 块**：`<div class="grid h-10 w-10 place-items-center ...">DS</div>` 仍是首选，
   除非该位置在 Figma 设计稿明确是图标位（如 sidebar 顶部 logo）。

### Imports

```vue
<script setup>
import { Database, Workflow, GitBranch, Play } from 'lucide-vue-next'
</script>

<template>
  <Database class="h-5 w-5" />
  <button class="btn btn-primary">
    <Play class="h-4 w-4" /> 执行
  </button>
</template>
```

- Tree-shaken：只 import 用到的图标，bundle 影响小（每个 ~0.3 KB）。
- 尺寸：`h-4 w-4`（小）/ `h-5 w-5`（标准）/ `h-6 w-6`（大）。
- 颜色：跟随 `currentColor` —— 通过父级 `text-*` 控制。

### 仍然不允许的

- `@heroicons/vue` / `@iconify` / `@fortawesome` —— 一个图标库够了，不要混用。
- `public/icons.svg` —— 那是 Vite 模板的 Bluesky/Discord/X 残留，与设计系统无关。
- 大量自绘 SVG —— 业务有特殊形状（如 DAG 连接箭头）才内联 SVG，单图标走 lucide。

---

## 6. Styling approach

- **Utility-first Tailwind**, no CSS Modules, no styled-components, no scoped styles in SFCs (no `<style scoped>`).
- Global resets and element defaults live in `@layer base` (`src/style.css`) — including styled `input`, `select`, `textarea`, `table`, `th`, `td`, `code`. So a bare `<input>` already looks right; don't re-style every form field.
- Reusable patterns go in `@layer components` (the `.btn`/`.card`/`.pill`/etc. shortcuts).
- Responsive: Tailwind breakpoints (`md:`, `lg:`, `xl:`). The shell uses fixed-width sidebar (`w-64`) + flexible main; most card grids are `grid-cols-1 md:grid-cols-2 xl:grid-cols-3`.
- Dark mode: not implemented. The dark sidebar (`bg-sidebar` = `#1a1d2e`) is intentional contrast, not a theme switch.
- Animation: keep to Tailwind utilities (`transition`, `animate-pulse`). No Motion / GSAP.
- Arbitrary values are fine when occasional (`text-[11px]`, `rounded-[10px]`) — don't add tokens for one-offs.

---

## 7. Project structure recap

```
frontend/frontend/             ← yes, doubly nested. Don't "fix" it.
├── index.html
├── package.json
├── vite.config.js
├── tailwind.config.js
├── postcss.config.js
├── public/
└── src/
    ├── main.js
    ├── App.vue                ← all reactive state + provide('app', {...})
    ├── style.css              ← @tailwind + @layer base + @layer components
    ├── api.js                 ← apiGet / apiJson / apiForm
    ├── assets/                ← bundled images
    ├── mock/                  ← workflow_meta.js (status colors, layout helper) — keep design-system-ish constants here
    ├── components/            ← reusable; defineProps/defineEmits
    └── views/                 ← page-level; inject('app')
```

Build output → `../../static/spa/` → committed-but-gitignored, served by FastAPI at `/static/spa/`.

---

## Figma MCP workflow checklist

When given a Figma URL or asked to implement a Figma design:

1. **Parse the URL** — `figma.com/design/:fileKey/...?node-id=:nodeId`. Convert `-` → `:` in `nodeId`.
2. **Call `mcp__figma__get_design_context`** with `fileKey` + `nodeId`. Inspect screenshot + code + hints.
3. **Re-target the stack**:
   - React JSX → Vue SFC `<script setup>` + `<template>`.
   - `className` → `class`. Conditional classes → `:class="..."`.
   - State hooks → `ref` / `reactive` / `computed`.
   - Inline event handlers stay similar (`onClick` → `@click`).
4. **Re-target the styling**:
   - Replace MCP design tokens / CSS vars with the closest Tailwind utility from §1.
   - Replace icon imports with the patterns in §5.
   - Drop bespoke shadcn/Radix component imports — re-build with `<button class="btn btn-primary">` etc.
5. **Wire to the inject('app') context** if the component lives under `views/`. New top-level state goes in `App.vue` and gets exposed through `provide('app', {...})`.
6. **Add new repeated patterns to `@layer components`** (`src/style.css`) instead of duplicating utility chains.
7. **Build & verify**: `npm run build` (outputs to `../../static/spa/`), then load `http://localhost:8010` in a browser. Per root CLAUDE.md: type-checking does not equal feature correctness — actually click through the new UI.
