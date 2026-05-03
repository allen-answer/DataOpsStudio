<script setup>
import { computed } from 'vue'
import { Inbox, Upload, Workflow, Network, Layers, AlertTriangle, FileCheck2, Files } from 'lucide-vue-next'

const props = defineProps({
  report: { type: Object, required: true },
})

// 卡片可点击：父 LineageReportView 监听 navigate 事件，自动切 tab 并把
// preset 写到目标 panel 里（"点风险点 5" → 风险 tab + levelFilter 自动设置）。
// 没有跳转目标的卡片 (file_count / success_count) 仍可显示，只是不可点。
const emit = defineEmits(['navigate'])

const cards = computed(() => {
  const s = props.report.summary || {}
  return [
    { id: 'input',   label: '输入资产', value: s.input_count,        icon: Inbox,        tone: 'info',     to: 'inputs' },
    { id: 'output',  label: '输出资产', value: s.output_count,       icon: Upload,       tone: 'success',  to: 'outputs' },
    { id: 'process', label: '处理步骤', value: s.process_step_count, icon: Workflow,     tone: 'pending',  to: 'process' },
    { id: 'tedge',   label: '表级边',   value: s.table_edge_count,   icon: Network,      tone: 'info',     to: 'table' },
    { id: 'cedge',   label: '字段级边', value: s.column_edge_count,  icon: Layers,       tone: 'info',     to: 'column' },
    {
      id: 'risk',    label: '风险点',   value: s.risk_count,         icon: AlertTriangle,
      tone: s.risk_count > 0 ? 'error' : 'pending',
      to: 'risks',
      // 有 high 风险时点击预设 levelFilter=high；否则不预设
      preset: () => {
        const risks = props.report.risks || []
        const hasHigh = risks.some(r => r.level === 'high')
        return hasHigh ? { levelFilter: 'high' } : {}
      },
    },
    { id: 'file',    label: '脚本数',   value: s.file_count,         icon: Files,        tone: 'pending', to: null },
    { id: 'success', label: '解析成功', value: s.success_count,      icon: FileCheck2,   tone: 'success', to: null },
  ]
})

const TONE = {
  success: { bg: 'bg-status-success-bg', text: 'text-status-success' },
  error:   { bg: 'bg-status-error-bg',   text: 'text-status-error' },
  warning: { bg: 'bg-status-warning-bg', text: 'text-status-warning' },
  info:    { bg: 'bg-status-info-bg',    text: 'text-status-info' },
  pending: { bg: 'bg-slate-100',         text: 'text-slate-700' },
}

function onCardClick(card) {
  if (!card.to) return
  const preset = typeof card.preset === 'function' ? card.preset() : null
  emit('navigate', { tab: card.to, preset })
}
</script>

<template>
  <section>
    <div class="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
      <component
        :is="c.to ? 'button' : 'div'"
        v-for="c in cards" :key="c.id"
        type="button"
        class="metric-card text-left transition"
        :class="c.to
          ? 'cursor-pointer hover:border-primary/40 hover:shadow-md'
          : 'cursor-default'"
        :title="c.to ? `跳转到「${c.label}」详情` : ''"
        @click="onCardClick(c)"
      >
        <div class="mb-2 flex items-center justify-between">
          <span class="muted text-[11px] font-semibold uppercase tracking-wider">{{ c.label }}</span>
          <component :is="c.icon" class="h-4 w-4" :class="TONE[c.tone].text" />
        </div>
        <div class="text-2xl font-bold text-slate-800">{{ c.value ?? '—' }}</div>
      </component>
    </div>
  </section>
</template>
