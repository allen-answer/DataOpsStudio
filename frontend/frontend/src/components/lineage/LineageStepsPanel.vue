<script setup>
import { computed } from 'vue'
import { Workflow, FileText } from 'lucide-vue-next'

const props = defineProps({
  steps: { type: Array, default: () => [] },
})

// 按 procedure_name + file_name 分组（多脚本的 process_steps 可能跨文件）
const grouped = computed(() => {
  const map = new Map()
  for (const step of props.steps) {
    const key = (step.file_name || '') + '|' + (step.procedure_name || '<top-level>')
    if (!map.has(key)) {
      map.set(key, {
        file_name: step.file_name || '',
        procedure_name: step.procedure_name || '',
        kind: step.kind || '',
        steps: [],
      })
    }
    map.get(key).steps.push(step)
  }
  return Array.from(map.values())
})

const DML_PILL = {
  INSERT: 'bg-status-success-bg text-status-success',
  UPDATE: 'bg-status-info-bg text-status-info',
  MERGE: 'bg-status-info-bg text-status-info',
  DELETE: 'bg-status-error-bg text-status-error',
  TRUNCATE: 'bg-status-error-bg text-status-error',
  SELECT: 'bg-slate-100 text-slate-600',
  WITH: 'bg-slate-100 text-slate-600',
  REPLACE: 'bg-status-success-bg text-status-success',
  CREATE: 'bg-status-running-bg text-status-running',
}

const PARSE_LABEL = {
  parsed: '已解析',
  unsupported: '未支持',
  unknown: '—',
}
const PARSE_PILL = {
  parsed: 'status-success',
  unsupported: 'status-warning',
  unknown: 'status-pending',
}
</script>

<template>
  <section class="card">
    <div class="mb-3 flex items-center justify-between">
      <div>
        <h3 class="text-base font-semibold text-slate-800">处理过程</h3>
        <p class="muted text-xs">{{ steps.length }} 段 DML（按存储过程 / 文件折叠）</p>
      </div>
    </div>

    <div v-if="!steps.length" class="rounded-lg border border-dashed border-slate-200 py-8 text-center text-slate-400">
      <Workflow class="mx-auto mb-2 h-8 w-8 text-slate-300" />
      <p class="text-sm">没有抽取到处理步骤</p>
      <p class="muted text-xs">单个 INSERT/UPDATE 语句不计入；包/过程内的 DML 才会聚合</p>
    </div>

    <div v-else class="space-y-3">
      <details
        v-for="g in grouped" :key="(g.file_name || '') + '|' + (g.procedure_name || '')"
        class="rounded-lg border border-slate-200 bg-slate-50"
      >
        <summary class="flex cursor-pointer items-center gap-2 px-3 py-2 text-sm">
          <FileText class="h-3.5 w-3.5 text-slate-400" />
          <span v-if="g.file_name" class="sql-font text-xs text-slate-500">{{ g.file_name }}</span>
          <span v-if="g.file_name && g.procedure_name" class="text-slate-300">/</span>
          <span class="sql-font font-medium text-slate-800">{{ g.procedure_name || '顶层语句' }}</span>
          <span v-if="g.kind" class="muted text-[11px]">{{ g.kind }}</span>
          <span class="status-badge status-info ml-auto">{{ g.steps.length }} 段</span>
        </summary>
        <div class="border-t border-slate-200 bg-white px-3 py-2">
          <table class="w-full text-xs">
            <thead>
              <tr class="text-left text-[10px] font-bold uppercase tracking-wider text-slate-400">
                <th class="py-1 pr-3">行号</th>
                <th class="py-1 pr-3">操作</th>
                <th class="py-1 pr-3">业务标题</th>
                <th class="py-1">解析</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="step in g.steps" :key="step.segment_index" class="border-t border-slate-100">
                <td class="py-1 pr-3 sql-font text-slate-500">
                  {{ step.line_start ?? '—' }}<span v-if="step.line_end && step.line_end !== step.line_start">–{{ step.line_end }}</span>
                </td>
                <td class="py-1 pr-3">
                  <span
                    v-if="step.dml_keyword"
                    class="rounded px-1.5 py-0.5 text-[10px] font-bold"
                    :class="DML_PILL[step.dml_keyword] || 'bg-slate-100 text-slate-700'"
                  >{{ step.dml_keyword }}</span>
                </td>
                <td class="py-1 pr-3 break-words text-slate-700">{{ step.preceding_comment || '—' }}</td>
                <td class="py-1">
                  <span class="status-badge" :class="PARSE_PILL[step.parse_status] || 'status-pending'">
                    {{ PARSE_LABEL[step.parse_status] || step.parse_status }}
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </details>
    </div>
  </section>
</template>
