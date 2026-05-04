<script setup>
import { computed, inject, nextTick, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  Database, GitCompareArrows, Workflow, GitBranch, History as HistoryIcon,
  Search, X, ChevronRight, FileCode, Loader2,
} from 'lucide-vue-next'
import { apiGet } from '../api'

// 全局命令面板。AppTopBar 通过 v-model:open 控制；快捷键 Ctrl/Cmd+K 也可触发。
//
// Phase 10 第 2 项：搜索范围扩到平台级 ——
// - 本地 nav（5 条 sidebar 入口）—— 即时
// - 后端 /api/search?q=... —— 跨数据源 / 任务 / 作业流 / 历史 / 血缘脚本，
//   按表名 / SQL body / tag / node config 反向索引（200ms debounce）
// 跟 DataHub / Atlan 平台级搜索对齐，让"搜用户表 → 所有引用的 ETL 一击命中"成立。
const props = defineProps({
  open: { type: Boolean, default: false },
})
const emit = defineEmits(['update:open'])

const router = useRouter()
inject('app', { state: { datasources: [], tasks: [] } })  // 兼容老 inject，不依赖

const NAV_ITEMS = [
  { id: 'datasources',   label: '数据源',     icon: Database,         path: '/datasources' },
  { id: 'data-compare',  label: '数据对比',   icon: GitCompareArrows, path: '/data-compare' },
  { id: 'workflows',     label: '作业流',     icon: Workflow,         path: '/workflows' },
  { id: 'lineage',       label: '血缘分析',   icon: GitBranch,        path: '/lineage' },
  { id: 'history',       label: '执行历史',   icon: HistoryIcon,      path: '/history' },
]

// kind → (icon, group_title, target_path_resolver)
const KIND_META = {
  datasource:     { icon: Database,         title: '数据源',     path: () => '/datasources' },
  task:           { icon: GitCompareArrows, title: '对比任务',   path: (h) => ({ path: '/data-compare', query: { task: h.id } }) },
  workflow:       { icon: Workflow,         title: '作业流',     path: (h) => `/workflows/${h.id}` },
  history:        { icon: HistoryIcon,      title: '执行历史',   path: () => '/history' },
  lineage_script: { icon: FileCode,         title: '血缘脚本',   path: (h) => h.metadata?.run_id ? `/workflow-runs/${h.metadata.run_id}` : '/lineage' },
}

const query = ref('')
const activeIndex = ref(0)
const inputEl = ref(null)
const backendHits = ref([])
const loading = ref(false)
let debounceTimer = null
let activeFetchToken = 0

watch(() => props.open, async (val) => {
  if (val) {
    query.value = ''
    activeIndex.value = 0
    backendHits.value = []
    await nextTick()
    inputEl.value?.focus()
  }
})

watch(query, (val) => {
  activeIndex.value = 0
  if (debounceTimer) clearTimeout(debounceTimer)
  const trimmed = val.trim()
  if (!trimmed) {
    backendHits.value = []
    loading.value = false
    return
  }
  loading.value = true
  debounceTimer = setTimeout(() => fetchBackend(trimmed), 200)
})

async function fetchBackend(q) {
  const token = ++activeFetchToken
  try {
    const data = await apiGet(`/api/search?q=${encodeURIComponent(q)}&limit=30`)
    if (token !== activeFetchToken) return  // 过时响应丢掉
    backendHits.value = Array.isArray(data?.hits) ? data.hits : []
  } catch (e) {
    if (token !== activeFetchToken) return
    backendHits.value = []
  } finally {
    if (token === activeFetchToken) loading.value = false
  }
}

const groups = computed(() => {
  const kw = query.value.trim().toLowerCase()

  // 本地 nav 过滤（即时）
  const navHits = NAV_ITEMS
    .filter(item => !kw || item.label.toLowerCase().includes(kw) || item.path.includes(kw))
    .map(item => ({
      kind: 'nav',
      key: `nav:${item.id}`,
      label: item.label,
      hint: item.path,
      icon: item.icon,
      path: item.path,
    }))

  // 后端 hits 按 kind 分组
  const byKind = {}
  for (const hit of backendHits.value) {
    if (!byKind[hit.kind]) byKind[hit.kind] = []
    byKind[hit.kind].push(hit)
  }

  const list = []
  if (navHits.length) list.push({ title: '导航', items: navHits })

  // 按固定顺序展示：datasource / task / workflow / history / lineage_script
  for (const kind of ['datasource', 'task', 'workflow', 'history', 'lineage_script']) {
    const hits = byKind[kind] || []
    if (!hits.length) continue
    const meta = KIND_META[kind]
    list.push({
      title: meta.title,
      items: hits.map(h => ({
        kind,
        key: `${kind}:${h.id}`,
        label: h.name || h.id,
        hint: h.snippet || h.match_path || '',
        icon: meta.icon,
        score: h.score,
        path: meta.path(h),
        rawHit: h,
      })),
    })
  }

  return list
})

const flatItems = computed(() => groups.value.flatMap(g => g.items))

function close() {
  emit('update:open', false)
}

function pick(item) {
  if (!item) return
  if (typeof item.path === 'string') {
    router.push(item.path)
  } else {
    router.push(item.path)
  }
  close()
}

function onKeyDown(event) {
  if (!props.open) return
  const total = flatItems.value.length
  if (event.key === 'Escape') {
    event.preventDefault()
    close()
  } else if (event.key === 'ArrowDown') {
    event.preventDefault()
    if (total) activeIndex.value = (activeIndex.value + 1) % total
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    if (total) activeIndex.value = (activeIndex.value - 1 + total) % total
  } else if (event.key === 'Enter') {
    event.preventDefault()
    pick(flatItems.value[activeIndex.value])
  }
}

function isActive(item) {
  return flatItems.value[activeIndex.value]?.key === item.key
}

function setActive(item) {
  const idx = flatItems.value.findIndex(it => it.key === item.key)
  if (idx >= 0) activeIndex.value = idx
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="fixed inset-0 z-50 flex items-start justify-center bg-slate-900/40 backdrop-blur-sm pt-[12vh]"
      @click="close"
      @keydown="onKeyDown"
      tabindex="-1"
    >
      <div
        class="w-[min(92vw,640px)] overflow-hidden rounded-xl border border-slate-200 bg-white shadow-2xl"
        @click.stop
      >
        <!-- 搜索框 -->
        <div class="flex items-center gap-2 border-b border-slate-100 px-4 py-3">
          <Search class="h-4 w-4 text-slate-400" />
          <input
            ref="inputEl"
            v-model="query"
            type="text"
            placeholder="搜索导航 / 数据源 / 任务 / 作业流 / 历史 / 血缘脚本"
            class="flex-1 border-0 bg-transparent p-0 text-sm focus:outline-none focus:ring-0"
            @keydown="onKeyDown"
          />
          <Loader2 v-if="loading" class="h-3.5 w-3.5 animate-spin text-slate-400" />
          <kbd class="rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-[10px] font-mono text-slate-500">Esc</kbd>
          <button class="grid h-6 w-6 place-items-center rounded text-slate-400 hover:bg-slate-100" @click="close">
            <X class="h-3.5 w-3.5" />
          </button>
        </div>

        <!-- 结果 -->
        <div class="max-h-[60vh] overflow-auto py-1">
          <div v-if="!groups.length" class="px-4 py-8 text-center text-sm text-slate-400">
            <span v-if="loading">搜索中…</span>
            <span v-else-if="query.trim()">没有匹配结果</span>
            <span v-else>输入关键词搜索 —— 跨数据源 / 任务 / 作业流 / 历史 / 血缘脚本</span>
          </div>
          <div v-for="g in groups" :key="g.title" class="py-1">
            <div class="px-4 pb-1 pt-2 text-[10px] font-bold uppercase tracking-wider text-slate-400">
              {{ g.title }}
            </div>
            <button
              v-for="item in g.items" :key="item.key"
              type="button"
              class="flex w-full items-center gap-3 px-4 py-2 text-left text-sm transition"
              :class="isActive(item) ? 'bg-primary-light text-primary' : 'hover:bg-slate-50 text-slate-700'"
              @mouseenter="setActive(item)"
              @click="pick(item)"
            >
              <component :is="item.icon" class="h-4 w-4 shrink-0" :class="isActive(item) ? 'text-primary' : 'text-slate-400'" />
              <span class="flex-1 truncate">{{ item.label }}</span>
              <span class="muted truncate text-[11px]" :class="isActive(item) ? 'text-primary/70' : ''">
                {{ item.hint }}
              </span>
              <ChevronRight class="h-3.5 w-3.5 shrink-0" :class="isActive(item) ? 'text-primary' : 'text-slate-300'" />
            </button>
          </div>
        </div>

        <!-- 提示 -->
        <div class="flex items-center justify-between border-t border-slate-100 px-4 py-2 text-[11px] text-slate-400">
          <span>
            <kbd class="rounded border border-slate-200 bg-slate-50 px-1 py-0.5 font-mono">↑↓</kbd>
            移动
            <kbd class="ml-2 rounded border border-slate-200 bg-slate-50 px-1 py-0.5 font-mono">Enter</kbd>
            跳转
          </span>
          <span>{{ flatItems.length }} 项</span>
        </div>
      </div>
    </div>
  </Teleport>
</template>
