<script setup>
import { computed, onMounted, ref } from 'vue'
import { assets, assetEdges, assetGroups, assetKinds, sensorActivity } from '../mock/dagster'

// "Cartographer's Notebook" treatment of the asset graph. Same data as
// the Dagster prototype, completely different visual language.

const NODE_W = 232
const NODE_H = 70

// Earth-tone health palette — forest / mustard / oxblood / faded-blue / bone.
// Replaces the conventional emerald/amber/rose/cyan/slate palette so the
// page reads as a printed plate rather than a dashboard tile.
const healthMeta = {
  fresh:        { label: 'Fresh',    color: '#2F6B3F', tint: '#E4E9D9', italic: false },
  stale:        { label: 'Stale',    color: '#B58A1F', tint: '#EFE2C5', italic: true  },
  failed:       { label: 'Failed',   color: '#A6442C', tint: '#EFD9CF', italic: false },
  materializing:{ label: 'In flight',color: '#4A6B85', tint: '#D9DEE6', italic: true  },
  none:         { label: 'Never',    color: '#8A8273', tint: '#E2DECF', italic: true  },
}

const groupFilter = ref('all')
const healthFilter = ref('all')
const searchTerm = ref('')
const selectedKey = ref('analytics/daily_revenue')

const filteredAssets = computed(() => assets.filter((a) => {
  if (groupFilter.value !== 'all' && a.group !== groupFilter.value) return false
  if (healthFilter.value !== 'all' && a.health !== healthFilter.value) return false
  if (searchTerm.value && !a.key.toLowerCase().includes(searchTerm.value.toLowerCase())) return false
  return true
}))

const visibleKeys = computed(() => new Set(filteredAssets.value.map((a) => a.key)))
const visibleEdges = computed(() => assetEdges.filter((e) => visibleKeys.value.has(e.source) && visibleKeys.value.has(e.target)))

const assetByKey = computed(() => {
  const m = {}
  for (const a of assets) m[a.key] = a
  return m
})

const selected = computed(() => assetByKey.value[selectedKey.value])

const canvas = computed(() => {
  let mx = 0, my = 0
  for (const a of assets) {
    if (a.x + NODE_W > mx) mx = a.x + NODE_W
    if (a.y + NODE_H > my) my = a.y + NODE_H
  }
  return { width: mx + 80, height: my + 80 }
})

const edgePath = (edge) => {
  const s = assetByKey.value[edge.source]
  const t = assetByKey.value[edge.target]
  if (!s || !t) return ''
  const sx = s.x + NODE_W
  const sy = s.y + NODE_H / 2
  const tx = t.x
  const ty = t.y + NODE_H / 2
  const dx = Math.max(40, (tx - sx) * 0.5)
  return `M ${sx} ${sy} C ${sx + dx} ${sy}, ${tx - dx} ${ty}, ${tx} ${ty}`
}

const lanes = computed(() => {
  const out = []
  for (const group of assetGroups) {
    const inGroup = assets.filter((a) => a.group === group.id)
    if (!inGroup.length) continue
    const minY = Math.min(...inGroup.map((a) => a.y)) - 24
    const maxY = Math.max(...inGroup.map((a) => a.y + NODE_H)) + 24
    out.push({
      id: group.id,
      name: group.name,
      desc: group.desc,
      y: Math.max(0, minY),
      height: maxY - Math.max(0, minY),
      count: inGroup.length,
    })
  }
  return out
})

// Health distribution per group, shown as a tiny bar in the TOC.
const groupHealth = computed(() => {
  const out = {}
  for (const group of assetGroups) {
    const counts = { fresh: 0, stale: 0, failed: 0, materializing: 0, none: 0 }
    for (const a of assets.filter((x) => x.group === group.id)) counts[a.health]++
    out[group.id] = counts
  }
  return out
})

const totalCounts = computed(() => {
  const c = { fresh: 0, stale: 0, failed: 0, materializing: 0, none: 0 }
  for (const a of filteredAssets.value) c[a.health]++
  return c
})

// Editorial summary: spelled out as English prose with inline weight/color.
const editorialSummary = computed(() => {
  const c = totalCounts.value
  const en = (n) => ['none','one','two','three','four','five','six','seven','eight','nine','ten','eleven','twelve'][n] || String(n)
  return {
    headline: `${en(filteredAssets.value.length)} assets observed`,
    parts: [
      { count: c.fresh,        label: 'fresh',     color: healthMeta.fresh.color },
      { count: c.stale,        label: 'stale',     color: healthMeta.stale.color },
      { count: c.failed,       label: 'failed',    color: healthMeta.failed.color },
      { count: c.materializing,label: 'in flight', color: healthMeta.materializing.color },
      { count: c.none,         label: 'never run', color: healthMeta.none.color },
    ].filter((p) => p.count > 0),
  }
})

// Asset key formatting — italic Fraunces for the leaf, small-caps mono for the prefix.
const splitKey = (key) => {
  const ix = key.lastIndexOf('/')
  if (ix < 0) return { prefix: '', leaf: key }
  return { prefix: key.slice(0, ix), leaf: key.slice(ix + 1) }
}

const edgeHighlighted = (edge) => edge.source === selectedKey.value || edge.target === selectedKey.value
const isOnSelectedPath = (asset) => {
  if (asset.key === selectedKey.value) return true
  const sel = selected.value
  if (!sel) return false
  if (sel.upstream.includes(asset.key)) return true
  if (asset.upstream.includes(sel.key)) return true
  return false
}

// Animation: stagger fade-in by index when first mounted.
const mounted = ref(false)
onMounted(() => { setTimeout(() => { mounted.value = true }, 16) })

// Today's date for the dateline — use ISO so it reads like a print revision.
const today = new Date().toISOString().slice(0, 10)
</script>

<template>
  <!-- The whole page is scoped under .atlas so font-faces and tokens stay
       contained; the surrounding shell still reads as Tailwind.
       Negative margins push the cream canvas to the edges of the chrome. -->
  <section class="atlas -mx-8 -my-8 min-h-[calc(100vh-64px)]">
    <div class="atlas-paper">

      <!-- HEADER: book-spread title strip -->
      <header class="atlas-head">
        <div class="atlas-head-left">
          <p class="atlas-eyebrow">DataOps Studio · Plate I</p>
          <h1 class="atlas-title">Asset <em>Atlas</em></h1>
          <p class="atlas-dateline">
            <span>Snapshot of production data assets</span>
            <span class="atlas-dot">·</span>
            <span>Revised <span class="mono">{{ today }}</span></span>
            <span class="atlas-dot">·</span>
            <span class="italic">drawn from the warehouse</span>
          </p>
        </div>
        <div class="atlas-head-right">
          <input v-model="searchTerm" placeholder="find by call number…" class="atlas-search" />
          <div class="atlas-filter-row">
            <button
              v-for="key in ['all','fresh','stale','failed','materializing','none']" :key="key"
              class="atlas-chip"
              :class="{ 'atlas-chip-active': healthFilter === key }"
              :style="key !== 'all' && healthFilter === key ? { borderColor: healthMeta[key].color, color: healthMeta[key].color } : {}"
              @click="healthFilter = key"
            >
              <span v-if="key !== 'all'" class="atlas-chip-dot" :style="{ background: healthMeta[key].color }"></span>
              <span>{{ key === 'all' ? 'all' : healthMeta[key].label.toLowerCase() }}</span>
            </button>
          </div>
        </div>
      </header>

      <!-- Editorial summary — replaces stat tiles -->
      <p class="atlas-summary">
        <em>{{ editorialSummary.headline }}.</em>
        <template v-for="(p, idx) in editorialSummary.parts" :key="p.label">
          <span :style="{ color: p.color, fontWeight: 600 }">{{ p.count }} {{ p.label }}</span><span v-if="idx < editorialSummary.parts.length - 1">{{ idx === editorialSummary.parts.length - 2 ? ', and ' : ', ' }}</span><span v-else>.</span>
        </template>
        <span class="atlas-summary-trail">Latest revision picked up at <span class="mono">09:14 UTC</span>.</span>
      </p>

      <!-- WORKSPACE: 3 columns, very precise -->
      <div class="atlas-grid">

        <!-- LEFT: table of contents (groups) -->
        <aside class="atlas-toc">
          <p class="atlas-section-label"><span class="atlas-section-num">§ I</span> Sheets</p>
          <button
            class="atlas-toc-row"
            :class="{ 'atlas-toc-row-active': groupFilter === 'all' }"
            @click="groupFilter = 'all'"
          >
            <span class="atlas-toc-name">All territories</span>
            <span class="atlas-toc-count mono">{{ assets.length }}</span>
          </button>
          <button
            v-for="g in assetGroups" :key="g.id"
            class="atlas-toc-row"
            :class="{ 'atlas-toc-row-active': groupFilter === g.id }"
            @click="groupFilter = g.id"
          >
            <div>
              <span class="atlas-toc-name italic">{{ g.name }}</span>
              <p class="atlas-toc-desc">{{ g.desc }}</p>
            </div>
            <div class="atlas-toc-meta">
              <div class="atlas-healthbar">
                <span
                  v-for="(count, key) in groupHealth[g.id]" :key="key"
                  :style="{ background: healthMeta[key].color, flex: count }"
                  class="atlas-healthbar-seg"
                  :title="`${count} ${healthMeta[key].label.toLowerCase()}`"
                ></span>
              </div>
              <span class="atlas-toc-count mono">{{ g.count }}</span>
            </div>
          </button>

          <p class="atlas-section-label" style="margin-top: 32px"><span class="atlas-section-num">§ II</span> Sentinels</p>
          <ul class="atlas-sensor-list">
            <li v-for="(s, idx) in sensorActivity.recent_ticks" :key="idx" class="atlas-sensor-row">
              <span class="atlas-sensor-tick mono">{{ s.last_tick }}</span>
              <div>
                <span class="atlas-sensor-name italic">{{ s.name }}</span>
                <p class="atlas-sensor-note mono">{{ s.note }}</p>
              </div>
            </li>
          </ul>
        </aside>

        <!-- CENTER: the plate itself -->
        <div class="atlas-plate">
          <!-- plate header bar — like a drawing's title block -->
          <div class="atlas-plate-head">
            <span class="atlas-plate-num">FIG. 1</span>
            <span class="atlas-plate-title italic">Asset graph, by group, scale 1:1</span>
            <span class="atlas-plate-meta mono">{{ filteredAssets.length }} ASSETS · {{ visibleEdges.length }} EDGES</span>
          </div>

          <!-- canvas viewport with cream paper + ruled grid -->
          <div class="atlas-canvas-viewport">
            <div class="atlas-canvas" :style="{ width: canvas.width + 'px', height: canvas.height + 'px' }">

              <!-- Lane bands — translucent earth tints; group label in margin -->
              <div
                v-for="lane in lanes" :key="lane.id"
                class="atlas-lane"
                :style="{ top: lane.y + 'px', height: lane.height + 'px' }"
              >
                <span class="atlas-lane-rule"></span>
                <span class="atlas-lane-label">
                  <span class="italic">{{ lane.name }}</span>
                  <span class="atlas-lane-count mono">— {{ lane.count }} —</span>
                </span>
              </div>

              <!-- edges: ink curves, with rust highlight when adjacent to selected -->
              <svg class="atlas-edges" :width="canvas.width" :height="canvas.height" :viewBox="`0 0 ${canvas.width} ${canvas.height}`">
                <defs>
                  <marker id="atl-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                    <path d="M0,0 L10,5 L0,10 z" fill="#3A2E22"/>
                  </marker>
                  <marker id="atl-arrow-rust" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                    <path d="M0,0 L10,5 L0,10 z" fill="#A6442C"/>
                  </marker>
                </defs>
                <path
                  v-for="(edge, idx) in visibleEdges" :key="idx"
                  :d="edgePath(edge)"
                  fill="none"
                  :stroke="edgeHighlighted(edge) ? '#A6442C' : '#3A2E22'"
                  :stroke-width="edgeHighlighted(edge) ? 1.6 : 0.8"
                  :stroke-opacity="edgeHighlighted(edge) ? 1 : 0.55"
                  :marker-end="edgeHighlighted(edge) ? 'url(#atl-arrow-rust)' : 'url(#atl-arrow)'"
                />
              </svg>

              <!-- asset cards -->
              <button
                v-for="(a, idx) in filteredAssets" :key="a.key"
                class="atlas-card"
                :class="{
                  'atlas-card-selected': selectedKey === a.key,
                  'atlas-card-faded': selectedKey && !isOnSelectedPath(a),
                  'atlas-card-mounted': mounted,
                }"
                :style="{
                  left: a.x + 'px', top: a.y + 'px',
                  width: NODE_W + 'px', height: NODE_H + 'px',
                  '--health': healthMeta[a.health].color,
                  '--health-tint': healthMeta[a.health].tint,
                  animationDelay: (idx * 25) + 'ms',
                }"
                @click="selectedKey = a.key"
              >
                <span class="atlas-card-bar"></span>
                <span class="atlas-card-key">
                  <span class="atlas-key-prefix mono">{{ splitKey(a.key).prefix }}/</span>
                  <em class="atlas-key-leaf">{{ splitKey(a.key).leaf }}</em>
                </span>
                <span class="atlas-card-meta mono">
                  <span :style="{ color: healthMeta[a.health].color }" :class="{ italic: healthMeta[a.health].italic }">{{ healthMeta[a.health].label }}</span>
                  <span class="atlas-card-sep">·</span>
                  <span>{{ assetKinds[a.kind].label.toLowerCase() }}</span>
                  <span class="atlas-card-sep">·</span>
                  <span>{{ a.duration }}</span>
                </span>
                <span class="atlas-card-mat mono">{{ a.last_materialized ? a.last_materialized.slice(5, 16) : 'never observed' }}</span>
              </button>

              <!-- compass rose, top-right corner -->
              <div class="atlas-compass">
                <svg viewBox="-30 -30 60 60" width="60" height="60">
                  <circle r="28" fill="none" stroke="#7A6A55" stroke-width="0.5"/>
                  <circle r="22" fill="none" stroke="#A89B85" stroke-width="0.3" stroke-dasharray="1 2"/>
                  <path d="M 0,-26 L 3,0 L 0,26 L -3,0 z" fill="#3A2E22"/>
                  <path d="M -26,0 L 0,2 L 26,0 L 0,-2 z" fill="#7A6A55" opacity="0.4"/>
                  <text x="0" y="-19" font-family="JetBrains Mono" font-size="6" font-weight="700" fill="#3A2E22" text-anchor="middle">N</text>
                  <circle r="1.5" fill="#A6442C"/>
                </svg>
                <span class="atlas-compass-label mono">N — Upstream</span>
              </div>

              <!-- scale bar, bottom-left -->
              <div class="atlas-scale">
                <div class="atlas-scale-bar">
                  <span></span><span></span><span></span><span></span>
                </div>
                <span class="mono">0 — 200 — 400 px</span>
              </div>
            </div>
          </div>

          <!-- plate footer / legend -->
          <div class="atlas-plate-foot">
            <div class="atlas-legend">
              <span v-for="(meta, key) in healthMeta" :key="key" class="atlas-legend-item">
                <span class="atlas-legend-dot" :style="{ background: meta.color }"></span>
                <span :class="{ italic: meta.italic }">{{ meta.label }}</span>
              </span>
            </div>
            <span class="atlas-plate-cite mono">FIG. 1 — Snapshot of production data assets, {{ today }}</span>
          </div>
        </div>

        <!-- RIGHT: bibliographic-style inspector -->
        <aside class="atlas-inspector">
          <div v-if="selected" class="atlas-inspector-card">
            <p class="atlas-inspector-eyebrow">Selected · §III</p>
            <h2 class="atlas-inspector-title">
              <span class="atlas-key-prefix mono">{{ splitKey(selected.key).prefix }}/</span>
              <em class="atlas-key-leaf-lg">{{ splitKey(selected.key).leaf }}</em>
            </h2>
            <p class="atlas-inspector-byline">
              from the <em>{{ selected.group }}</em> territory · published by <span class="italic">{{ selected.owner }}</span>
            </p>

            <div class="atlas-health-row">
              <span class="atlas-health-pill" :style="{ background: healthMeta[selected.health].tint, color: healthMeta[selected.health].color, borderColor: healthMeta[selected.health].color }">
                <span class="atlas-card-bar atlas-health-bar-tiny" :style="{ background: healthMeta[selected.health].color }"></span>
                <span :class="{ italic: healthMeta[selected.health].italic }">{{ healthMeta[selected.health].label }}</span>
              </span>
              <span class="atlas-kind-pill mono" :style="{ borderColor: assetKinds[selected.kind].accent, color: assetKinds[selected.kind].accent }">{{ assetKinds[selected.kind].label }}</span>
            </div>

            <dl class="atlas-bib">
              <div><dt>Last materialized</dt><dd class="mono">{{ selected.last_materialized || '—' }}</dd></div>
              <div><dt>Run id</dt><dd class="mono">{{ selected.last_run_id ? selected.last_run_id.slice(0, 12) : '—' }}</dd></div>
              <div><dt>Rows</dt><dd class="mono">{{ selected.rows ? selected.rows.toLocaleString() : '—' }}</dd></div>
              <div><dt>Duration</dt><dd class="mono">{{ selected.duration }}</dd></div>
              <div><dt>Upstream</dt><dd class="mono">{{ selected.upstream.length }}</dd></div>
            </dl>

            <div v-if="selected.partitions" class="atlas-partition-block">
              <p class="atlas-section-label"><span class="atlas-section-num">§</span> Partitions, last 30 days</p>
              <p class="atlas-partition-line">
                <span :style="{ color: healthMeta.fresh.color, fontWeight: 600 }">{{ selected.partitions.fresh }} fresh</span>,
                <span :style="{ color: healthMeta.stale.color, fontWeight: 600 }">{{ selected.partitions.stale }} stale</span>,
                <span :style="{ color: healthMeta.failed.color, fontWeight: 600 }">{{ selected.partitions.failed }} failed</span>.
              </p>
            </div>

            <div class="atlas-actions">
              <button class="atlas-btn-ink"><span class="atlas-btn-mark">▶</span> Materialize</button>
              <button class="atlas-btn-line">Backfill window…</button>
              <button class="atlas-btn-line">Open run record</button>
            </div>

            <p class="atlas-inspector-foot italic">
              Page revised {{ today }} — every materialization is a single ink stamp on the record.
            </p>
          </div>
        </aside>
      </div>

      <!-- FOOTER: page-bottom citation strip -->
      <footer class="atlas-foot">
        <span class="italic">DataOps Studio</span>
        <span class="atlas-dot">·</span>
        <span>Plate I of an ongoing series on the data warehouse</span>
        <span class="atlas-foot-spacer"></span>
        <span class="mono">RENDERED {{ today }} · DATAOPS-STUDIO</span>
      </footer>

    </div>
  </section>
</template>

<style scoped>
/* === Cartographer's Notebook === */

.atlas {
  --paper:        #F4EEE3;
  --paper-soft:   #FBF7EE;
  --paper-deep:   #ECE3D2;
  --ink:          #1A1612;
  --ink-soft:     #3A2E22;
  --rule:         #B0A893;
  --rule-soft:    #D7CFB8;
  --rust:         #A6442C;
  --rust-deep:    #7A2D1A;
  --serif:        'Fraunces', 'EB Garamond', Georgia, serif;
  --mono:         'JetBrains Mono', 'IBM Plex Mono', 'SFMono-Regular', monospace;

  font-family: var(--serif);
  font-feature-settings: "ss01", "ss02", "kern";
  color: var(--ink);
  background: var(--paper);
}

.atlas-paper {
  /* layered background: cream + faint sepia ruled grid + tiny vignette */
  background:
    radial-gradient(ellipse at 50% 0%, rgba(255,255,255,0.5), transparent 60%),
    repeating-linear-gradient(0deg,  transparent 0 79px, rgba(176,168,147,0.08) 79px 80px),
    repeating-linear-gradient(90deg, transparent 0 79px, rgba(176,168,147,0.08) 79px 80px),
    var(--paper);
  padding: 32px 40px 24px;
  min-height: 100%;
}

/* monospace utility */
:deep(.mono), .mono { font-family: var(--mono); font-feature-settings: "tnum"; letter-spacing: -0.01em; }
.italic { font-style: italic; }

/* === HEADER === */
.atlas-head {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 24px;
  align-items: end;
  padding-bottom: 16px;
  border-bottom: 0.5px solid var(--rule);
}
.atlas-head-left { min-width: 0; }
.atlas-eyebrow {
  font-family: var(--mono);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--rule);
  margin-bottom: 8px;
}
.atlas-title {
  font-family: var(--serif);
  font-variation-settings: "opsz" 144, "SOFT" 30, "WONK" 0;
  font-size: clamp(48px, 6vw, 84px);
  font-weight: 600;
  line-height: 0.92;
  letter-spacing: -0.02em;
  margin: 0;
  color: var(--ink);
}
.atlas-title em {
  font-family: var(--serif);
  font-variation-settings: "opsz" 144, "SOFT" 100, "WONK" 1;
  font-style: italic;
  font-weight: 400;
  color: var(--rust);
}
.atlas-dateline {
  margin-top: 10px;
  font-family: var(--serif);
  font-size: 14px;
  color: var(--ink-soft);
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: baseline;
}
.atlas-dateline .mono { font-size: 12.5px; color: var(--ink); }
.atlas-dot { color: var(--rule); }

.atlas-head-right { display: flex; flex-direction: column; gap: 10px; align-items: flex-end; }
.atlas-search {
  font-family: var(--serif);
  font-style: italic;
  font-size: 14px;
  background: transparent;
  border: none;
  border-bottom: 0.5px solid var(--ink);
  padding: 4px 4px 4px 24px;
  width: 280px;
  color: var(--ink);
  background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16' fill='none' stroke='%231A1612' stroke-width='1'><circle cx='7' cy='7' r='4'/><path d='M10 10l4 4'/></svg>");
  background-repeat: no-repeat;
  background-position: 0 center;
  background-size: 16px;
}
.atlas-search:focus { outline: none; border-bottom-color: var(--rust); }
.atlas-search::placeholder { color: var(--rule); font-style: italic; }

.atlas-filter-row { display: flex; gap: 4px; }
.atlas-chip {
  font-family: var(--mono);
  font-size: 10.5px;
  letter-spacing: 0.05em;
  text-transform: lowercase;
  color: var(--ink-soft);
  background: transparent;
  border: 0.5px solid var(--rule);
  padding: 2px 8px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  transition: border-color 0.15s ease, color 0.15s ease, background 0.15s ease;
}
.atlas-chip:hover { border-color: var(--ink); color: var(--ink); }
.atlas-chip-active { border-color: var(--ink); background: var(--ink); color: var(--paper); }
.atlas-chip-dot { width: 5px; height: 5px; border-radius: 50%; display: inline-block; }

/* === EDITORIAL SUMMARY === */
.atlas-summary {
  font-family: var(--serif);
  font-variation-settings: "opsz" 24, "SOFT" 50;
  font-size: 17px;
  line-height: 1.55;
  color: var(--ink-soft);
  max-width: 78ch;
  margin: 18px 0 22px;
  letter-spacing: -0.005em;
}
.atlas-summary em {
  font-style: italic;
  font-weight: 500;
  color: var(--ink);
  margin-right: 4px;
}
.atlas-summary-trail { margin-left: 6px; color: var(--rule); }

/* === GRID === */
.atlas-grid {
  display: grid;
  grid-template-columns: 240px minmax(0, 1fr) 320px;
  gap: 28px;
  min-height: 0;
}

/* === TOC === */
.atlas-section-label {
  font-family: var(--serif);
  font-style: italic;
  font-size: 13px;
  color: var(--rule);
  text-transform: lowercase;
  letter-spacing: 0.04em;
  margin: 0 0 12px;
  padding-bottom: 6px;
  border-bottom: 0.5px solid var(--rule);
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.atlas-section-num {
  font-family: var(--mono);
  font-style: normal;
  font-size: 10.5px;
  font-weight: 700;
  color: var(--rust);
  letter-spacing: 0.1em;
}
.atlas-toc-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  background: transparent;
  border: none;
  border-bottom: 0.5px solid var(--rule-soft);
  padding: 9px 0;
  cursor: pointer;
  font-family: var(--serif);
  text-align: left;
  color: var(--ink);
  transition: background 0.15s ease;
}
.atlas-toc-row:hover { background: rgba(166,68,44,0.05); }
.atlas-toc-row-active { background: rgba(166,68,44,0.08); }
.atlas-toc-row-active .atlas-toc-name { color: var(--rust); }
.atlas-toc-name { font-size: 14.5px; font-weight: 500; }
.atlas-toc-name.italic { font-style: italic; }
.atlas-toc-desc {
  font-family: var(--serif);
  font-style: italic;
  font-size: 11.5px;
  color: var(--rule);
  margin: 2px 0 0;
  line-height: 1.35;
  max-width: 18ch;
}
.atlas-toc-meta { display: flex; flex-direction: column; align-items: flex-end; gap: 4px; min-width: 60px; }
.atlas-toc-count { font-size: 12.5px; font-weight: 600; color: var(--ink); }
.atlas-healthbar { display: flex; height: 3px; width: 60px; background: var(--paper-deep); border: 0.5px solid var(--rule-soft); }
.atlas-healthbar-seg { display: block; height: 100%; }

.atlas-sensor-list { list-style: none; padding: 0; margin: 0; }
.atlas-sensor-row {
  display: flex;
  gap: 10px;
  padding: 8px 0;
  border-bottom: 0.5px solid var(--rule-soft);
}
.atlas-sensor-tick {
  font-size: 10.5px;
  color: var(--rust);
  white-space: nowrap;
  padding-top: 2px;
}
.atlas-sensor-name { font-size: 13.5px; }
.atlas-sensor-note { font-size: 10.5px; color: var(--rule); margin: 1px 0 0; }

/* === CENTER PLATE === */
.atlas-plate {
  display: flex;
  flex-direction: column;
  border: 0.5px solid var(--rule);
  background: var(--paper-soft);
  min-width: 0;
  position: relative;
}
.atlas-plate::before, .atlas-plate::after {
  content: '';
  position: absolute;
  width: 12px; height: 12px;
  border: 0.5px solid var(--ink);
}
.atlas-plate::before { top: -6px; left: -6px; border-right: none; border-bottom: none; }
.atlas-plate::after  { bottom: -6px; right: -6px; border-left: none; border-top: none; }

.atlas-plate-head {
  display: flex;
  align-items: baseline;
  gap: 14px;
  padding: 8px 14px;
  border-bottom: 0.5px solid var(--rule);
  background: var(--paper-deep);
}
.atlas-plate-num {
  font-family: var(--mono);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.1em;
  color: var(--rust);
}
.atlas-plate-title {
  font-family: var(--serif);
  font-style: italic;
  font-size: 14px;
  color: var(--ink);
  flex: 1;
}
.atlas-plate-meta {
  font-size: 10px;
  color: var(--ink-soft);
  letter-spacing: 0.06em;
}

.atlas-canvas-viewport {
  flex: 1;
  overflow: auto;
  min-height: 480px;
  background:
    /* subtle paper grain */
    radial-gradient(rgba(176,168,147,0.06) 1px, transparent 1px) 0 0 / 4px 4px,
    /* lat/lon ticks */
    repeating-linear-gradient(0deg,  transparent 0 79px, rgba(176,168,147,0.16) 79px 80px),
    repeating-linear-gradient(90deg, transparent 0 79px, rgba(176,168,147,0.16) 79px 80px),
    var(--paper-soft);
}
.atlas-canvas { position: relative; }

/* lane bands — translucent earth tint, italic small-caps margin label */
.atlas-lane {
  position: absolute;
  left: 0; right: 0;
  pointer-events: none;
  background: linear-gradient(90deg, rgba(176,168,147,0.10) 0, rgba(176,168,147,0) 240px);
  border-top: 0.5px dashed var(--rule);
  border-bottom: 0.5px dashed var(--rule);
}
.atlas-lane-rule { display: none; }
.atlas-lane-label {
  position: absolute;
  top: 8px;
  left: 14px;
  font-family: var(--serif);
  font-size: 13px;
  color: var(--ink-soft);
  text-transform: uppercase;
  letter-spacing: 0.18em;
  font-feature-settings: "smcp", "c2sc";
  display: inline-flex;
  gap: 8px;
  align-items: baseline;
}
.atlas-lane-label .italic { font-style: italic; font-feature-settings: "smcp", "c2sc"; font-weight: 500; }
.atlas-lane-count { font-size: 9.5px; color: var(--rule); letter-spacing: 0.05em; }

.atlas-edges { position: absolute; inset: 0; pointer-events: none; }

/* asset cards */
.atlas-card {
  position: absolute;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 10px 8px 14px;
  background: var(--paper-soft);
  border: 0.5px solid var(--ink-soft);
  cursor: pointer;
  text-align: left;
  font-family: inherit;
  color: inherit;
  opacity: 0;
  transform: translateY(4px);
  transition: opacity 0.4s ease, transform 0.4s ease, border-color 0.15s ease, box-shadow 0.15s ease, filter 0.2s ease;
}
.atlas-card-mounted { opacity: 1; transform: translateY(0); }
.atlas-card:hover { box-shadow: 2px 3px 0 rgba(58,46,34,0.18); border-color: var(--ink); }
.atlas-card-selected {
  border: 1px solid var(--ink);
  box-shadow: 3px 3px 0 var(--ink);
  background: #FFFCF4;
}
.atlas-card-faded { filter: saturate(0.4); opacity: 0.55; }

/* health bar on the left edge — the only saturated mark on the card */
.atlas-card-bar {
  position: absolute;
  left: 0; top: 0; bottom: 0;
  width: 4px;
  background: var(--health);
}

.atlas-card-key {
  display: flex;
  align-items: baseline;
  gap: 1px;
  line-height: 1.05;
  font-family: var(--serif);
  font-size: 16px;
  color: var(--ink);
}
.atlas-key-prefix {
  font-size: 11px;
  color: var(--rule);
  letter-spacing: 0.02em;
  text-transform: lowercase;
}
.atlas-key-leaf {
  font-style: italic;
  font-variation-settings: "opsz" 24, "SOFT" 50, "WONK" 0;
  font-weight: 500;
}
.atlas-key-leaf-lg {
  font-style: italic;
  font-family: var(--serif);
  font-variation-settings: "opsz" 96, "SOFT" 30, "WONK" 1;
  font-size: 36px;
  font-weight: 500;
  line-height: 1;
  color: var(--ink);
}
.atlas-card-meta {
  font-size: 10.5px;
  color: var(--ink-soft);
  display: flex;
  gap: 4px;
  align-items: baseline;
}
.atlas-card-sep { color: var(--rule); }
.atlas-card-mat {
  font-size: 10.5px;
  color: var(--rule);
  position: absolute;
  right: 10px;
  bottom: 6px;
}

/* compass + scale */
.atlas-compass {
  position: absolute;
  top: 16px; right: 24px;
  display: flex;
  flex-direction: column;
  align-items: center;
  pointer-events: none;
  opacity: 0.85;
}
.atlas-compass-label {
  font-size: 9px;
  letter-spacing: 0.08em;
  color: var(--ink-soft);
  margin-top: 2px;
}
.atlas-scale {
  position: absolute;
  bottom: 16px; left: 24px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 9px;
  color: var(--ink-soft);
  letter-spacing: 0.06em;
}
.atlas-scale-bar {
  display: flex; height: 6px; width: 100px;
  border: 0.5px solid var(--ink);
}
.atlas-scale-bar span { flex: 1; }
.atlas-scale-bar span:nth-child(odd)  { background: var(--ink); }
.atlas-scale-bar span:nth-child(even) { background: transparent; }

.atlas-plate-foot {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  padding: 8px 14px;
  border-top: 0.5px solid var(--rule);
  background: var(--paper-deep);
  flex-wrap: wrap;
}
.atlas-legend { display: flex; flex-wrap: wrap; gap: 16px; }
.atlas-legend-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-family: var(--serif);
  font-size: 12px;
  color: var(--ink-soft);
}
.atlas-legend-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
.atlas-plate-cite {
  font-size: 9.5px;
  color: var(--ink-soft);
  letter-spacing: 0.06em;
}

/* === RIGHT INSPECTOR === */
.atlas-inspector { min-width: 0; }
.atlas-inspector-card {
  border: 0.5px solid var(--rule);
  background: var(--paper-soft);
  padding: 22px 22px 24px;
}
.atlas-inspector-eyebrow {
  font-family: var(--mono);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--rust);
  margin: 0 0 8px;
}
.atlas-inspector-title {
  font-family: var(--serif);
  font-size: 28px;
  font-weight: 500;
  margin: 0 0 6px;
  line-height: 1;
  letter-spacing: -0.01em;
  display: flex; align-items: baseline;
}
.atlas-inspector-byline {
  font-family: var(--serif);
  font-size: 13.5px;
  color: var(--ink-soft);
  margin: 0 0 16px;
  line-height: 1.45;
}
.atlas-inspector-byline em { font-weight: 500; color: var(--ink); }

.atlas-health-row { display: flex; gap: 6px; margin-bottom: 18px; flex-wrap: wrap; }
.atlas-health-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-family: var(--serif);
  font-size: 12px;
  padding: 3px 10px 3px 4px;
  border: 0.5px solid;
  letter-spacing: 0.02em;
  position: relative;
}
.atlas-health-bar-tiny {
  position: static;
  width: 4px;
  height: 14px;
}
.atlas-kind-pill {
  font-size: 10px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  font-weight: 600;
  padding: 3px 8px;
  border: 0.5px solid;
  background: transparent;
}

.atlas-bib {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0 0 18px;
  border-top: 0.5px solid var(--rule-soft);
  padding-top: 12px;
}
.atlas-bib > div {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 12px;
  font-size: 13px;
}
.atlas-bib dt {
  font-family: var(--serif);
  font-style: italic;
  color: var(--rule);
  flex-shrink: 0;
}
.atlas-bib dd { margin: 0; color: var(--ink); }

.atlas-partition-block { margin: 18px 0; }
.atlas-partition-line {
  font-family: var(--serif);
  font-size: 13px;
  line-height: 1.5;
  color: var(--ink);
  margin: 0;
}

.atlas-actions { display: flex; flex-direction: column; gap: 8px; margin: 18px 0 14px; }
.atlas-btn-ink {
  background: var(--ink);
  color: var(--paper);
  border: none;
  padding: 10px 14px;
  font-family: var(--serif);
  font-size: 14px;
  font-weight: 500;
  letter-spacing: 0.03em;
  cursor: pointer;
  text-align: left;
  display: flex; align-items: center; gap: 8px;
  transition: background 0.15s ease;
}
.atlas-btn-ink:hover { background: var(--rust); }
.atlas-btn-mark {
  font-family: var(--mono);
  font-size: 11px;
}
.atlas-btn-line {
  background: transparent;
  color: var(--ink);
  border: 0.5px solid var(--ink);
  padding: 7px 14px;
  font-family: var(--serif);
  font-style: italic;
  font-size: 13px;
  cursor: pointer;
  text-align: left;
  transition: background 0.15s ease;
}
.atlas-btn-line:hover { background: var(--paper-deep); }

.atlas-inspector-foot {
  font-family: var(--serif);
  font-size: 11.5px;
  color: var(--rule);
  margin: 14px 0 0;
  padding-top: 10px;
  border-top: 0.5px dashed var(--rule-soft);
  line-height: 1.45;
}

/* === FOOTER === */
.atlas-foot {
  margin-top: 24px;
  padding-top: 14px;
  border-top: 0.5px solid var(--rule);
  display: flex;
  align-items: baseline;
  gap: 10px;
  font-family: var(--serif);
  font-size: 12.5px;
  color: var(--ink-soft);
}
.atlas-foot-spacer { flex: 1; }
.atlas-foot .mono { font-size: 10.5px; color: var(--rule); letter-spacing: 0.08em; }
</style>
