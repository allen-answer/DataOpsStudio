<script setup>
import { ShieldCheck, AlertTriangle, AlertCircle } from 'lucide-vue-next'

const props = defineProps({
  risks: { type: Array, default: () => [] },
})

const RISK_TONE = {
  high:   { card: 'border-status-error-bg bg-status-error-bg/40', text: 'text-status-error', icon: AlertCircle },
  medium: { card: 'border-status-warning-bg bg-status-warning-bg/40', text: 'text-status-warning', icon: AlertTriangle },
  low:    { card: 'border-slate-200 bg-slate-50', text: 'text-slate-600', icon: AlertTriangle },
}
</script>

<template>
  <section class="card">
    <div class="mb-3 flex items-center justify-between">
      <div>
        <h3 class="text-base font-semibold text-slate-800">风险与告警</h3>
        <p class="muted text-xs">{{ risks.length }} 条 · 按 high / medium / low 分级</p>
      </div>
    </div>

    <div v-if="!risks.length" class="rounded-lg border border-dashed border-slate-200 py-8 text-center">
      <ShieldCheck class="mx-auto mb-2 h-8 w-8 text-status-success" />
      <p class="text-sm font-medium text-slate-700">未检测到风险</p>
      <p class="muted text-xs">所有解析路径均成功，无低置信动态 SQL</p>
    </div>

    <ul v-else class="space-y-2">
      <li
        v-for="(risk, i) in risks" :key="i"
        class="rounded-lg border p-3 text-sm"
        :class="RISK_TONE[risk.level]?.card || RISK_TONE.low.card"
      >
        <div class="flex items-start gap-2">
          <component :is="RISK_TONE[risk.level]?.icon || AlertTriangle" class="mt-0.5 h-4 w-4 shrink-0" :class="RISK_TONE[risk.level]?.text || RISK_TONE.low.text" />
          <div class="flex-1 min-w-0">
            <div class="flex flex-wrap items-center gap-2">
              <span
                class="rounded px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-widest"
                :class="risk.level === 'high' ? 'bg-status-error text-white'
                       : risk.level === 'medium' ? 'bg-status-warning text-white'
                       : 'bg-slate-200 text-slate-700'"
              >{{ risk.level }}</span>
              <span class="font-medium" :class="RISK_TONE[risk.level]?.text || RISK_TONE.low.text">{{ risk.type }}</span>
              <span v-if="risk.file_name" class="muted sql-font text-[11px]">{{ risk.file_name }}</span>
            </div>
            <p class="mt-1 break-words text-slate-700">{{ risk.message }}</p>
          </div>
        </div>
      </li>
    </ul>
  </section>
</template>
