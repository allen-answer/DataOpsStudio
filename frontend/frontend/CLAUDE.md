# Frontend Design System Rules (DataOps Studio SPA)

Rules for translating Figma designs into this codebase via the Figma MCP. Read alongside the root `CLAUDE.md`. Frontend-only — backend rules stay in the root file.

The Figma MCP returns React + Tailwind + design-token reference code. **Always adapt** to this project's stack: Vue 3 SFC, Tailwind v3 utility classes, the small set of `@layer components` shortcuts, and the existing inject('app') data flow. Never paste the MCP output verbatim.

---

## 1. Token definitions

All design tokens live in **`tailwind.config.js`** (`theme.extend`) and the `@layer base` / `@layer components` blocks of **`src/style.css`**. There is no Style Dictionary, no JSON token export, no CSS-vars layer — Tailwind's generated utilities are the source of truth.

### Project-specific palette (extend tokens)

```js
// tailwind.config.js
colors: {
  ink:    '#1e293b',  // primary text — same as slate-800
  muted:  '#64748b',  // secondary text — same as slate-500
  line:   '#e5e7ec',  // borders / dividers
  panel:  '#ffffff',  // surfaces
  canvas: '#f8f9fb',  // page background
  brand:  '#2563eb',  // primary action — same as blue-600
}
boxShadow: {
  soft: '0 4px 12px rgba(15, 23, 42, 0.04)',
}
```

### Standard Tailwind palette (no custom alias)

The codebase uses raw Tailwind utility colors directly. Map Figma colors to these — do not invent new color names in the config.

| Figma intent       | Use these classes                                      |
| ------------------ | ------------------------------------------------------ |
| Primary text       | `text-slate-800` / `text-ink`                          |
| Secondary text     | `text-slate-500` / `text-muted`                        |
| Borders / dividers | `border-slate-200` / `border-slate-100` / `border-line` |
| Page background    | `bg-slate-50` / `bg-canvas`                            |
| Panel / card       | `bg-white` / `bg-panel`                                |
| Primary action     | `bg-blue-600 hover:bg-blue-700` / `bg-brand`           |
| Success            | `text-emerald-600` `bg-green-100 text-green-700`       |
| Warning            | `text-amber-600` `bg-amber-50 border-amber-300`        |
| Danger             | `bg-red-600 hover:bg-red-700` / `text-rose-500`        |
| Info / running     | `bg-blue-50 text-blue-600`                             |
| Sidebar (dark)     | `bg-slate-900 text-slate-300 border-slate-800`         |

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

Vue 3 Single-File Components, `<script setup>` syntax everywhere. No JSX, no Storybook, no Vue Router (single-page tab switcher in `App.vue`).

```
src/
├── App.vue                 # Shell: sidebar + header + view router. Owns ALL global state.
├── main.js                 # createApp(App).mount('#app')
├── style.css               # Tailwind directives + base/components layers
├── api.js                  # Tiny fetch wrappers: apiGet, apiJson, apiForm
├── views/                  # Top-level tab views — inject('app'), NEVER take props
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
- Don't reach for a third-party DAG library — `@antv/g6` exists but is reserved for the **lineage** graph (`components/LineageGraph.vue`), not workflows.

---

## 3. Frameworks & libraries

| Concern         | Choice                                                                |
| --------------- | --------------------------------------------------------------------- |
| Framework       | Vue 3.5 (`<script setup>`)                                            |
| Build / bundler | Vite 8 (`@vitejs/plugin-vue`)                                         |
| Styling         | Tailwind CSS 3.4 + autoprefixer + postcss                             |
| Graph viz       | `@antv/g6` 5 — lineage only                                           |
| Code editor     | `@codemirror/*` 6 — SQL editor                                        |
| Utilities       | `@vueuse/core` (clipboard etc.); `@tanstack/vue-virtual` for big lists |
| Icons           | None. See §5.                                                         |
| Routing         | None. Tab state is `activeView` ref in `App.vue`                      |
| State mgmt      | provide/inject. Pinia installed but unused — don't add stores         |
| Testing         | None on the frontend (e2e Playwright lives in repo `tests/e2e/`)     |

When MCP suggests Radix / shadcn / Headless UI / Heroicons / lucide: **don't install**. Replicate the shape with Tailwind utilities + the existing `.btn`/`.card`/`.pill` shortcuts.

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

**There is no icon library.** UI affordances are conveyed through:

1. **Text glyphs** — short Chinese labels: `刷新`, `保存`, `删除`, `执行`.
2. **Colored dots** — `<span class="h-3 w-3 rounded-full bg-green-500" />` for status.
3. **Letter badges** — `<div class="grid h-10 w-10 place-items-center rounded-lg bg-blue-600 ... text-white">DB</div>` (1–2 letter "logo block", see `App.vue` sidebar).
4. **Pill / chip** — `class="pill bg-blue-50 text-blue-600"` for tags.

When Figma uses real icons (Heroicons / Phosphor / Material), **prefer translating to the patterns above**. If an icon is truly load-bearing and can't be replaced with text:

- Inline an SVG `<svg viewBox="..."><path d="..." /></svg>` directly in the component.
- Size via Tailwind: `class="h-4 w-4"`. Color via `currentColor` + `text-*` utility.
- **Do not** introduce `lucide-vue-next`, `@heroicons/vue`, etc. without first asking the user.
- **Do not** wire the leftover `public/icons.svg` symbol sprite — it ships unused boilerplate icons that are not part of the design system.

---

## 6. Styling approach

- **Utility-first Tailwind**, no CSS Modules, no styled-components, no scoped styles in SFCs (no `<style scoped>`).
- Global resets and element defaults live in `@layer base` (`src/style.css`) — including styled `input`, `select`, `textarea`, `table`, `th`, `td`, `code`. So a bare `<input>` already looks right; don't re-style every form field.
- Reusable patterns go in `@layer components` (the `.btn`/`.card`/`.pill`/etc. shortcuts).
- Responsive: Tailwind breakpoints (`md:`, `lg:`, `xl:`). The shell uses fixed-width sidebar (`w-64`) + flexible main; most card grids are `grid-cols-1 md:grid-cols-2 xl:grid-cols-3`.
- Dark mode: not implemented. The dark sidebar (`bg-slate-900`) is intentional contrast, not a theme switch.
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
