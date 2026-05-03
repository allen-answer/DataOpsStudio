<script setup>
const props = defineProps({
  enrichment: { type: Object, default: () => ({}) },
})

const list = (value) => Array.isArray(value) ? value : []
</script>

<template>
  <section class="card space-y-4">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h3 class="text-base font-semibold text-slate-800">AI 辅助判断</h3>
        <p class="muted text-xs">AI 只做解释、归纳和风险提示，不覆盖系统确定性血缘结果。</p>
      </div>
      <span
        class="rounded-full px-2.5 py-1 text-xs font-semibold ring-1 ring-inset"
        :class="enrichment.enabled
          ? enrichment.status === 'success'
            ? 'bg-violet-50 text-violet-700 ring-violet-200'
            : enrichment.status === 'pending'
              ? 'bg-amber-50 text-amber-700 ring-amber-200'
              : 'bg-rose-50 text-rose-700 ring-rose-200'
          : 'bg-slate-100 text-slate-500 ring-slate-200'"
      >
        {{ enrichment.enabled ? enrichment.status : 'disabled' }}
      </span>
    </div>

    <div v-if="!enrichment.enabled" class="rounded-xl border border-dashed border-slate-200 bg-slate-50 p-8 text-center">
      <p class="text-sm font-semibold text-slate-600">AI 未启用</p>
      <p class="mt-1 text-xs text-slate-500">离线规则分析仍会正常输出；需要时在分析入口勾选“AI 辅助分析”，并配置 provider 环境变量。</p>
    </div>

    <div v-else-if="enrichment.status === 'pending'" class="rounded-xl border border-amber-200 bg-amber-50 p-4">
      <p class="text-sm font-semibold text-amber-800">AI 辅助分析正在后台执行</p>
      <p class="mt-1 text-xs text-amber-700">规则血缘结果已经可用，AI 完成后本页会自动刷新。</p>
      <div class="mt-3 h-2 overflow-hidden rounded-full bg-white">
        <div class="h-full w-1/2 rounded-full bg-amber-500 motion-safe:animate-pulse"></div>
      </div>
    </div>

    <div v-else-if="enrichment.status === 'error'" class="rounded-xl border border-rose-200 bg-rose-50 p-4">
      <p class="text-sm font-semibold text-rose-700">AI 调用失败，已自动降级</p>
      <p class="mt-1 break-all font-mono text-xs text-rose-900">{{ enrichment.error || 'unknown error' }}</p>
    </div>

    <div v-else class="space-y-4">
      <div class="grid gap-2 md:grid-cols-3">
        <div class="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2">
          <p class="text-[10px] font-semibold uppercase tracking-wider text-slate-400">Provider</p>
          <p class="mt-1 font-mono text-sm font-bold text-slate-800">{{ enrichment.provider || '-' }}</p>
        </div>
        <div class="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2">
          <p class="text-[10px] font-semibold uppercase tracking-wider text-slate-400">Model</p>
          <p class="mt-1 font-mono text-sm font-bold text-slate-800">{{ enrichment.model || '-' }}</p>
        </div>
        <div class="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2">
          <p class="text-[10px] font-semibold uppercase tracking-wider text-slate-400">耗时</p>
          <p class="mt-1 font-mono text-sm font-bold text-slate-800">{{ enrichment.elapsed_seconds || 0 }}s</p>
        </div>
      </div>

      <div v-if="enrichment.summary" class="rounded-xl border border-violet-100 bg-violet-50/60 p-4">
        <p class="text-[11px] font-bold uppercase tracking-wider text-violet-700">摘要</p>
        <p class="mt-2 text-sm leading-6 text-slate-700">{{ enrichment.summary }}</p>
      </div>

      <div class="grid gap-3 lg:grid-cols-3">
        <div class="rounded-xl border border-slate-200 bg-white p-3">
          <p class="text-[11px] font-bold uppercase tracking-wider text-slate-500">建议 ({{ list(enrichment.suggestions).length }})</p>
          <ul class="mt-2 space-y-2 text-sm text-slate-700">
            <li v-for="(item, i) in list(enrichment.suggestions)" :key="i" class="rounded-lg bg-slate-50 px-3 py-2">
              {{ item.message || item.text || item.summary || JSON.stringify(item) }}
            </li>
            <li v-if="!list(enrichment.suggestions).length" class="text-xs text-slate-400">暂无建议</li>
          </ul>
        </div>
        <div class="rounded-xl border border-slate-200 bg-white p-3">
          <p class="text-[11px] font-bold uppercase tracking-wider text-slate-500">风险 ({{ list(enrichment.risks).length }})</p>
          <ul class="mt-2 space-y-2 text-sm text-slate-700">
            <li v-for="(item, i) in list(enrichment.risks)" :key="i" class="rounded-lg bg-amber-50 px-3 py-2 text-amber-900">
              {{ item.message || item.text || item.summary || JSON.stringify(item) }}
            </li>
            <li v-if="!list(enrichment.risks).length" class="text-xs text-slate-400">暂无风险补充</li>
          </ul>
        </div>
        <div class="rounded-xl border border-slate-200 bg-white p-3">
          <p class="text-[11px] font-bold uppercase tracking-wider text-slate-500">字段提示 ({{ list(enrichment.column_hints).length }})</p>
          <ul class="mt-2 space-y-2 text-sm text-slate-700">
            <li v-for="(item, i) in list(enrichment.column_hints)" :key="i" class="rounded-lg bg-blue-50 px-3 py-2 text-blue-900">
              {{ item.message || item.text || item.column || JSON.stringify(item) }}
            </li>
            <li v-if="!list(enrichment.column_hints).length" class="text-xs text-slate-400">暂无字段提示</li>
          </ul>
        </div>
      </div>
    </div>
  </section>
</template>
