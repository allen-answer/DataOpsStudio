<script setup>
import { computed, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorkflowStore } from '../../stores/workflow'
import { useNoticeStore } from '../../stores/notice'

const emit = defineEmits(['open-detail'])

const workflowStore = useWorkflowStore()
const { workflowTemplates } = storeToRefs(workflowStore)
const {
  createWorkflowFromTemplate, deleteWorkflowTemplate, loadWorkflowTemplates,
} = workflowStore
const { setNotice } = useNoticeStore()

const categoryFilter = ref('all')
const searchTerm = ref('')

const templates = computed(() => Array.isArray(workflowTemplates?.value)
  ? workflowTemplates.value
  : (workflowTemplates || []))

const categories = computed(() => {
  const out = new Set()
  for (const item of templates.value) {
    const category = item.category || item.workflow?.project || ''
    if (category) out.add(category)
  }
  return [...out].sort()
})

const filtered = computed(() => templates.value.filter((item) => {
  const category = item.category || item.workflow?.project || ''
  if (categoryFilter.value !== 'all' && category !== categoryFilter.value) return false
  if (searchTerm.value) {
    const term = searchTerm.value.toLowerCase()
    const haystack = [
      item.name,
      item.description,
      category,
      ...(item.tags || []),
      item.workflow?.name,
      item.workflow?.description,
    ].join(' ').toLowerCase()
    if (!haystack.includes(term)) return false
  }
  return true
}))

const stats = computed(() => ({
  total: templates.value.length,
  nodes: templates.value.reduce((sum, item) => sum + (item.workflow?.nodes?.length || 0), 0),
  categories: categories.value.length,
}))

const instantiate = async (template) => {
  const workflow = await createWorkflowFromTemplate?.(template.id)
  if (workflow?.id) emit('open-detail', workflow.id)
}

const refresh = async () => {
  await loadWorkflowTemplates?.()
  setNotice?.('模板已刷新')
}
</script>

<template>
  <div class="space-y-3">
    <div class="grid grid-cols-1 gap-2 md:grid-cols-3">
      <div class="rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
        <p class="text-[11px] font-semibold uppercase tracking-wider text-slate-400">模板总数</p>
        <p class="mt-1 text-2xl font-bold tabular-nums text-slate-800">{{ stats.total }}</p>
        <p class="mt-0.5 text-[11px] text-slate-500">可复用作业流定义</p>
      </div>
      <div class="rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
        <p class="text-[11px] font-semibold uppercase tracking-wider text-slate-400">节点规模</p>
        <p class="mt-1 text-2xl font-bold tabular-nums text-violet-600">{{ stats.nodes }}</p>
        <p class="mt-0.5 text-[11px] text-slate-500">模板内累计节点</p>
      </div>
      <div class="rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
        <p class="text-[11px] font-semibold uppercase tracking-wider text-slate-400">分类</p>
        <p class="mt-1 text-2xl font-bold tabular-nums text-blue-600">{{ stats.categories }}</p>
        <p class="mt-0.5 text-[11px] text-slate-500">按项目 / 场景沉淀</p>
      </div>
    </div>

    <div class="flex flex-wrap items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2 shadow-sm">
      <div class="flex items-center gap-1.5 text-xs">
        <span class="text-[10px] font-semibold uppercase tracking-wider text-slate-400">分类</span>
        <select v-model="categoryFilter" class="h-8 rounded-lg border border-slate-200 bg-white px-2 text-xs text-slate-700 focus:border-violet-400 focus:outline-none">
          <option value="all">全部</option>
          <option v-for="category in categories" :key="category" :value="category">{{ category }}</option>
        </select>
      </div>
      <input
        v-model="searchTerm"
        placeholder="搜索模板名称 / 说明 / 标签..."
        class="h-8 min-w-[260px] flex-1 rounded-lg border border-slate-200 bg-white px-2 text-[12px] text-slate-700 placeholder:text-slate-400 focus:border-violet-400 focus:outline-none"
      >
      <button class="inline-flex h-8 items-center gap-1 rounded-lg border border-slate-200 bg-white px-2.5 text-[11px] font-semibold text-slate-700 transition hover:border-slate-300 hover:bg-slate-50" @click="refresh">
        刷新模板
      </button>
    </div>

    <div class="grid gap-3 lg:grid-cols-2">
      <article
        v-for="template in filtered"
        :key="template.id"
        class="rounded-xl border border-slate-200 bg-white p-4 shadow-sm transition hover:border-violet-200 hover:shadow-md"
      >
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <div class="flex flex-wrap items-center gap-2">
              <h3 class="truncate text-base font-bold text-slate-800">{{ template.name }}</h3>
              <span v-if="template.category || template.workflow?.project" class="rounded-full bg-violet-50 px-2 py-0.5 text-[11px] font-semibold text-violet-700 ring-1 ring-inset ring-violet-100">
                {{ template.category || template.workflow?.project }}
              </span>
            </div>
            <p class="mt-1 line-clamp-2 text-sm text-slate-500">
              {{ template.description || template.workflow?.description || '暂无说明' }}
            </p>
          </div>
          <div class="shrink-0 text-right">
            <p class="text-lg font-bold tabular-nums text-slate-800">{{ template.workflow?.nodes?.length || 0 }}</p>
            <p class="text-[10px] uppercase tracking-wider text-slate-400">nodes</p>
          </div>
        </div>

        <div class="mt-3 flex flex-wrap gap-1">
          <span v-for="tag in (template.tags || []).slice(0, 5)" :key="tag" class="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10.5px] text-slate-600">{{ tag }}</span>
          <span v-if="!template.tags?.length" class="text-[11px] text-slate-300">无标签</span>
        </div>

        <div class="mt-3 rounded-lg border border-slate-100 bg-slate-50 px-3 py-2">
          <p class="text-[11px] font-semibold uppercase tracking-wider text-slate-400">模板来源</p>
          <p class="mt-1 truncate text-sm font-semibold text-slate-700">{{ template.workflow?.name || '-' }}</p>
          <p class="mt-0.5 text-[11px] text-slate-500">
            {{ template.workflow?.owner || '未设置负责人' }} · {{ template.created_at ? template.created_at.slice(0, 10) : '未记录日期' }}
          </p>
        </div>

        <div class="mt-3 flex items-center justify-end gap-2">
          <button class="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-rose-700 transition hover:border-rose-200 hover:bg-rose-50" @click="deleteWorkflowTemplate?.(template.id)">
            删除
          </button>
          <button class="rounded-lg bg-violet-600 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-violet-700" @click="instantiate(template)">
            从模板创建
          </button>
        </div>
      </article>
    </div>

    <div v-if="!filtered.length" class="rounded-xl border border-dashed border-slate-200 bg-white p-12 text-center text-sm text-slate-400">
      {{ templates.length ? '没有匹配的模板' : '还没有模板：先进入作业流详情，点击“保存为模板”。' }}
    </div>
  </div>
</template>
