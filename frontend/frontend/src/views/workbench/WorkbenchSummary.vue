<script setup>
import { computed } from 'vue'
import { storeToRefs } from 'pinia'
import { CheckCircle2, AlertCircle, AlertTriangle, Database, FileSpreadsheet, ArrowRight, Play, Square, Save, Copy, Trash2 } from 'lucide-vue-next'
import { useBootstrapStore } from '../../stores/bootstrap'
import { useTaskStore } from '../../stores/task'

const { state } = useBootstrapStore()
const taskStore = useTaskStore()
const { taskDraft } = taskStore         // reactive
const {
  selectedTaskId, isSavedTask, currentTask,
  compareResult, asyncJob, asyncStatus,
  taskValidationIssues, canSaveTask,
} = storeToRefs(taskStore)
const {
  saveTask, runTask, runAsync, cancelAsync, copyTask, deleteTask,
} = taskStore

const errorIssues = computed(() => taskValidationIssues.value.filter(i => i.level === 'error'))

function dsName(id) {
  return state.datasources.find(d => d.id === id)?.name || ''
}

const sourceLabel = computed(() => {
  if (taskDraft.source_kind === 'excel') return taskDraft.source_excel_filename || '未选 Excel'
  return dsName(taskDraft.source_id) || '未选数据源'
})
const targetLabel = computed(() => {
  if (taskDraft.target_kind === 'excel') return taskDraft.target_excel_filename || '未选 Excel'
  return dsName(taskDraft.target_id) || '未选数据源'
})

const keyColumns = computed(() => {
  const v = taskDraft.key_columns
  if (Array.isArray(v)) return v.join(', ')
  return v || ''
})

const mappingCount = computed(() => {
  const raw = (taskDraft.column_mappings || '').trim()
  if (!raw) return 0
  return raw.split(/\n+/).filter(line => line.trim() && line.includes('->')).length
})

const ignoreCount = computed(() => {
  const v = taskDraft.ignore_columns
  if (Array.isArray(v)) return v.length
  if (!v) return 0
  return v.split(',').map(s => s.trim()).filter(Boolean).length
})

const lastRun = computed(() => {
  if (!compareResult) return null
  const s = compareResult.summary || {}
  return {
    same: s.same || 0,
    diff: s.diff || 0,
    only_source: s.only_source || 0,
    only_target: s.only_target || 0,
  }
})
</script>

<template>
  <aside class="card sticky top-4 space-y-4">
    <!-- 任务摘要 -->
    <div>
      <div class="mb-2 flex items-start justify-between gap-2">
        <h3 class="truncate text-base font-bold text-slate-800">{{ taskDraft.name || '未命名任务' }}</h3>
        <span
          class="status-badge"
          :class="isSavedTask ? 'status-success' : 'status-pending'"
        >
          {{ isSavedTask ? '已保存' : '草稿' }}
        </span>
      </div>
      <p class="text-xs text-slate-500">
        {{ taskDraft.sql_mode === 'single' ? '单 SQL 模式' : '双 SQL 模式' }}
        <span v-if="currentTask" class="text-slate-400"> · ID {{ selectedTaskId.slice(0, 8) }}</span>
      </p>
    </div>

    <!-- 数据流向 -->
    <div class="rounded-lg border border-slate-100 bg-slate-50 p-3 text-xs">
      <div class="mb-2 font-semibold text-slate-700">数据流向</div>
      <div class="space-y-1.5">
        <div class="flex items-center gap-1.5">
          <component :is="taskDraft.source_kind === 'excel' ? FileSpreadsheet : Database" class="h-3.5 w-3.5 shrink-0 text-slate-400" />
          <span class="muted text-[11px]">源</span>
          <span class="ml-1 truncate text-slate-700">{{ sourceLabel }}</span>
        </div>
        <ArrowRight class="ml-1 h-3 w-3 text-slate-300" />
        <div class="flex items-center gap-1.5">
          <component :is="taskDraft.target_kind === 'excel' ? FileSpreadsheet : Database" class="h-3.5 w-3.5 shrink-0 text-slate-400" />
          <span class="muted text-[11px]">目标</span>
          <span class="ml-1 truncate text-slate-700">{{ targetLabel }}</span>
        </div>
      </div>
    </div>

    <!-- 校验问题 -->
    <div
      v-if="errorIssues.length"
      class="rounded-lg border border-status-error-bg bg-status-error-bg/40 p-3 text-xs"
    >
      <div class="mb-2 flex items-center gap-1.5 text-status-error">
        <AlertTriangle class="h-3.5 w-3.5" />
        <span class="font-semibold">{{ errorIssues.length }} 项配置问题</span>
      </div>
      <ul class="space-y-1 text-status-error">
        <li
          v-for="(issue, i) in errorIssues" :key="i"
          class="flex items-start gap-1.5"
        >
          <span class="mt-0.5 inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-status-error" />
          <span class="break-words">{{ issue.message }}</span>
        </li>
      </ul>
    </div>

    <!-- 规则摘要 -->
    <div class="rounded-lg border border-slate-100 bg-slate-50 p-3 text-xs">
      <div class="mb-2 font-semibold text-slate-700">规则摘要</div>
      <dl class="space-y-1 text-slate-600">
        <div class="flex justify-between gap-2">
          <dt class="muted text-[11px]">主键</dt>
          <dd class="truncate font-mono text-slate-700">{{ keyColumns || '—' }}</dd>
        </div>
        <div class="flex justify-between gap-2">
          <dt class="muted text-[11px]">字段映射</dt>
          <dd class="text-slate-700">{{ mappingCount }} 条</dd>
        </div>
        <div class="flex justify-between gap-2">
          <dt class="muted text-[11px]">忽略字段</dt>
          <dd class="text-slate-700">{{ ignoreCount }} 条</dd>
        </div>
        <div class="flex justify-between gap-2">
          <dt class="muted text-[11px]">数值容忍</dt>
          <dd class="text-slate-700">{{ taskDraft.numeric_tolerance ?? '—' }}</dd>
        </div>
      </dl>
    </div>

    <!-- 最近一次执行 -->
    <div v-if="lastRun" class="rounded-lg border border-slate-100 bg-white p-3">
      <div class="mb-2 flex items-center justify-between">
        <span class="text-xs font-semibold text-slate-700">最近执行</span>
        <CheckCircle2 class="h-3.5 w-3.5 text-status-success" />
      </div>
      <div class="grid grid-cols-2 gap-1.5 text-[11px]">
        <div class="rounded bg-status-success-bg px-2 py-1">
          <div class="text-status-success font-bold">{{ lastRun.same }}</div>
          <div class="text-slate-500">same</div>
        </div>
        <div class="rounded bg-status-error-bg px-2 py-1">
          <div class="text-status-error font-bold">{{ lastRun.diff }}</div>
          <div class="text-slate-500">diff</div>
        </div>
        <div class="rounded bg-status-warning-bg px-2 py-1">
          <div class="text-status-warning font-bold">{{ lastRun.only_source }}</div>
          <div class="text-slate-500">only_src</div>
        </div>
        <div class="rounded bg-status-warning-bg px-2 py-1">
          <div class="text-status-warning font-bold">{{ lastRun.only_target }}</div>
          <div class="text-slate-500">only_tgt</div>
        </div>
      </div>
    </div>

    <!-- 异步任务状态 -->
    <div v-if="asyncJob" class="rounded-lg border border-status-info-bg bg-status-info-bg/30 p-3 text-xs">
      <div class="mb-1 flex items-center gap-1.5 text-status-info">
        <AlertCircle class="h-3.5 w-3.5" />
        <span class="font-semibold">后台任务运行中</span>
      </div>
      <p class="muted text-[11px]">{{ asyncStatus?.status || '排队中' }}</p>
      <button class="btn btn-danger mt-2 h-7 w-full px-2 text-[11px]" @click="cancelAsync">
        <Square class="h-3 w-3" /> 取消
      </button>
    </div>

    <!-- 主操作按钮组 -->
    <div class="space-y-1.5 border-t border-slate-100 pt-3">
      <button
        class="btn btn-primary w-full"
        :disabled="!isSavedTask || !canSaveTask"
        :title="errorIssues.length ? `修复 ${errorIssues.length} 项配置问题后才能执行` : ''"
        @click="runTask"
      >
        <Play class="h-4 w-4" /> 执行对比
      </button>
      <div class="grid grid-cols-2 gap-1.5">
        <button
          class="btn btn-outline h-9 text-xs"
          :disabled="!canSaveTask"
          :title="errorIssues.length ? `修复 ${errorIssues.length} 项配置问题后才能保存` : ''"
          @click="saveTask"
        >
          <Save class="h-3.5 w-3.5" /> {{ isSavedTask ? '保存修改' : '保存任务' }}
        </button>
        <button
          class="btn btn-outline h-9 text-xs"
          :disabled="!isSavedTask || !canSaveTask"
          @click="runAsync"
        >
          <Play class="h-3.5 w-3.5" /> 后台执行
        </button>
      </div>
      <div class="grid grid-cols-2 gap-1.5">
        <button class="btn btn-outline h-9 text-xs" :disabled="!isSavedTask" @click="copyTask">
          <Copy class="h-3.5 w-3.5" /> 复制
        </button>
        <button class="btn btn-danger h-9 text-xs" :disabled="!isSavedTask" @click="deleteTask">
          <Trash2 class="h-3.5 w-3.5" /> 删除
        </button>
      </div>
    </div>
  </aside>
</template>
