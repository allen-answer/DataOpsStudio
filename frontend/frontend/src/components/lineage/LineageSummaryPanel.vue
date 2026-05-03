<script setup>
import { computed } from 'vue'
import { Inbox, Upload, Workflow, Network, Layers, AlertTriangle, FileCheck2, Files } from 'lucide-vue-next'

const props = defineProps({
  report: { type: Object, required: true },
})

const cards = computed(() => {
  const s = props.report.summary || {}
  return [
    { id: 'input',   label: '输入资产', value: s.input_count,        icon: Inbox,        tone: 'info' },
    { id: 'output',  label: '输出资产', value: s.output_count,       icon: Upload,       tone: 'success' },
    { id: 'process', label: '处理步骤', value: s.process_step_count, icon: Workflow,     tone: 'pending' },
    { id: 'tedge',   label: '表级边',   value: s.table_edge_count,   icon: Network,      tone: 'info' },
    { id: 'cedge',   label: '字段级边', value: s.column_edge_count,  icon: Layers,       tone: 'info' },
    { id: 'risk',    label: '风险点',   value: s.risk_count,         icon: AlertTriangle,tone: s.risk_count > 0 ? 'error' : 'pending' },
    { id: 'file',    label: '脚本数',   value: s.file_count,         icon: Files,        tone: 'pending' },
    { id: 'success', label: '解析成功', value: s.success_count,      icon: FileCheck2,   tone: 'success' },
  ]
})

const TONE = {
  success: { bg: 'bg-status-success-bg', text: 'text-status-success' },
  error:   { bg: 'bg-status-error-bg',   text: 'text-status-error' },
  warning: { bg: 'bg-status-warning-bg', text: 'text-status-warning' },
  info:    { bg: 'bg-status-info-bg',    text: 'text-status-info' },
  pending: { bg: 'bg-slate-100',         text: 'text-slate-700' },
}
</script>

<template>
  <section>
    <div class="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
      <div
        v-for="c in cards" :key="c.id"
        class="metric-card"
      >
        <div class="mb-2 flex items-center justify-between">
          <span class="muted text-[11px] font-semibold uppercase tracking-wider">{{ c.label }}</span>
          <component :is="c.icon" class="h-4 w-4" :class="TONE[c.tone].text" />
        </div>
        <div class="text-2xl font-bold text-slate-800">{{ c.value ?? '—' }}</div>
      </div>
    </div>
  </section>
</template>
