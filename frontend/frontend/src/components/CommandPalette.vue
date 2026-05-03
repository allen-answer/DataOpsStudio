<script setup>
import { computed, inject, nextTick, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  Database, GitCompareArrows, Workflow, GitBranch, History as HistoryIcon,
  Search, X, ChevronRight,
} from 'lucide-vue-next'

// 全局命令面板。AppTopBar 通过 v-model:open 控制；快捷键 Ctrl/Cmd+K 也可触发。
// 搜索范围：sidebar 5 项 + 数据源 + 任务，分组显示，键盘上下移动 + 回车跳转。
const props = defineProps({
  open: { type: Boolean, default: false },
})
const emit = defineEmits(['update:open'])

const router = useRouter()
const { state } = inject('app', { state: { datasources: [], tasks: [] } })

const NAV_ITEMS = [
  { id: 'datasources',   label: '数据源',     icon: Database,         path: '/datasources' },
  { id: 'data-compare',  label: '数据对比',   icon: GitCompareArrows, path: '/data-compare' },
  { id: 'workflows',     label: '作业流',     icon: Workflow,         path: '/workflows' },
  { id: 'lineage',       label: '血缘分析',   icon: GitBranch,        path: '/lineage' },
  { id: 'history',       label: '执行历史',   icon: HistoryIcon,      path: '/history' },
]

const query = ref('')
const activeIndex = ref(0)
const inputEl = ref(null)

const groups = computed(() => {
  const kw = query.value.trim().toLowerCase()
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

  const dsHits = (state?.datasources || [])
    .filter(d => !kw || (d.name || '').toLowerCase().includes(kw) || (d.host || '').toLowerCase().includes(kw))
    .slice(0, 8)
    .map(d => ({
      kind: 'datasource',
      key: `ds:${d.id}`,
      label: d.name,
      hint: `${d.db_type || 'DB'} · ${d.host || ''}`,
      icon: Database,
      path: '/datasources',
    }))

  const taskHits = (state?.tasks || [])
    .filter(t => !kw || (t.name || '').toLowerCase().includes(kw))
    .slice(0, 8)
    .map(t => ({
      kind: 'task',
      key: `task:${t.id}`,
      label: t.name,
      hint: '数据对比任务',
      icon: GitCompareArrows,
      path: '/data-compare',
      taskId: t.id,
    }))

  const list = []
  if (navHits.length) list.push({ title: '导航', items: navHits })
  if (dsHits.length) list.push({ title: '数据源', items: dsHits })
  if (taskHits.length) list.push({ title: '任务', items: taskHits })
  return list
})

// flat list 用于键盘上下移动
const flatItems = computed(() => groups.value.flatMap(g => g.items))

watch(() => props.open, async (val) => {
  if (val) {
    query.value = ''
    activeIndex.value = 0
    await nextTick()
    inputEl.value?.focus()
  }
})

watch(query, () => {
  activeIndex.value = 0
})

function close() {
  emit('update:open', false)
}

function pick(item) {
  if (!item) return
  if (item.kind === 'task' && item.taskId) {
    // 跳到对比页 + 通过 query 提示 selectedTaskId（App.vue 监听 route.query 还没接，
    // 暂时只跳路由，用户在 /data-compare 里手动点；后续可以接）
    router.push({ path: item.path, query: { task: item.taskId } })
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
            placeholder="搜索导航 / 数据源 / 任务"
            class="flex-1 border-0 bg-transparent p-0 text-sm focus:outline-none focus:ring-0"
            @keydown="onKeyDown"
          />
          <kbd class="rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-[10px] font-mono text-slate-500">Esc</kbd>
          <button class="grid h-6 w-6 place-items-center rounded text-slate-400 hover:bg-slate-100" @click="close">
            <X class="h-3.5 w-3.5" />
          </button>
        </div>

        <!-- 结果 -->
        <div class="max-h-[60vh] overflow-auto py-1">
          <div v-if="!groups.length" class="px-4 py-8 text-center text-sm text-slate-400">
            没有匹配结果
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
              <span class="muted text-[11px]" :class="isActive(item) ? 'text-primary/70' : ''">{{ item.hint }}</span>
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
