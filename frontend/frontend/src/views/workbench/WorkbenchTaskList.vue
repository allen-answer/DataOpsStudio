<script setup>
import { inject, computed } from 'vue'
import { Plus, Search, FileSpreadsheet, FileCode } from 'lucide-vue-next'

const { state, selectedTaskId, selectTask } = inject('app')

const props = defineProps({
  search: { type: String, default: '' },
})

const emit = defineEmits(['update:search'])

const filteredTasks = computed(() => {
  const q = props.search.trim().toLowerCase()
  if (!q) return state.tasks
  return state.tasks.filter(t =>
    t.name.toLowerCase().includes(q)
    || (t.key_columns || []).join(' ').toLowerCase().includes(q)
  )
})

function taskIcon(task) {
  // 看任务的 source_kind / target_kind 推断主要用什么数据源种类
  if (task.source_kind === 'excel' || task.target_kind === 'excel') return FileSpreadsheet
  return FileCode
}
</script>

<template>
  <aside class="card sticky top-4 flex h-[calc(100vh-9rem)] flex-col">
    <div class="mb-3 flex items-center justify-between">
      <div>
        <h3 class="text-base font-bold text-slate-800">对比任务</h3>
        <p class="text-xs text-slate-500">{{ state.tasks.length }} 个任务 · {{ state.datasources.length }} 数据源</p>
      </div>
      <button class="btn btn-primary h-8 gap-1 px-3 text-xs" @click="selectTask('new')">
        <Plus class="h-3.5 w-3.5" />新建
      </button>
    </div>

    <div class="relative mb-2">
      <Search class="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
      <input
        :value="search"
        class="w-full rounded-lg border border-slate-200 bg-slate-50 py-1.5 pl-8 pr-2 text-xs"
        placeholder="搜索任务名 / 主键"
        @input="emit('update:search', $event.target.value)"
      >
    </div>

    <div class="flex-1 space-y-1.5 overflow-auto pr-0.5">
      <button
        v-for="task in filteredTasks"
        :key="task.id"
        class="group block w-full rounded-lg border p-2.5 text-left transition"
        :class="selectedTaskId === task.id
          ? 'border-primary bg-primary-light'
          : 'border-slate-100 bg-white hover:border-slate-200 hover:bg-slate-50'"
        @click="selectTask(task.id)"
      >
        <div class="flex items-center gap-2">
          <component :is="taskIcon(task)" class="h-3.5 w-3.5 shrink-0"
            :class="selectedTaskId === task.id ? 'text-primary' : 'text-slate-400'" />
          <strong class="truncate text-sm" :class="selectedTaskId === task.id ? 'text-primary' : 'text-slate-800'">{{ task.name }}</strong>
        </div>
        <p class="mt-0.5 truncate text-[11px] text-slate-500">
          {{ task.sql_mode === 'single' ? '单 SQL' : '双 SQL' }}
          <span v-if="task.key_columns?.length" class="text-slate-400"> · keys: {{ task.key_columns.join(', ') }}</span>
        </p>
      </button>
      <p v-if="!filteredTasks.length" class="rounded-lg border border-dashed border-slate-200 p-3 text-center text-xs text-slate-400">
        {{ search ? '无匹配任务' : '暂无任务，点击新建开始' }}
      </p>
    </div>
  </aside>
</template>
