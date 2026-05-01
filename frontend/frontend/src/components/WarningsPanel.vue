<script setup>
import { computed } from 'vue'

const props = defineProps({
  warnings: { type: Array, default: () => [] },
  dynamicSqlSegments: { type: Array, default: () => [] },
  parseErrors: { type: Array, default: () => [] },
})

const hasContent = computed(() => props.warnings.length || props.dynamicSqlSegments.length || props.parseErrors.length)
</script>

<template>
  <div
    v-if="hasContent"
    class="rounded-2xl border border-amber-200 bg-amber-50 p-6 text-sm text-amber-900 shadow-sm"
  >
    <h2 class="mb-4 text-xl font-bold text-amber-950">解析提示</h2>
    <div class="grid gap-4 xl:grid-cols-3">
      <div v-if="warnings.length">
        <h3 class="mb-2 font-bold">风险提示</h3>
        <ul class="space-y-2">
          <li v-for="item in warnings" :key="item.type + item.message + item.statement_index" class="rounded-xl bg-white/70 p-3">
            <strong>{{ item.type }}</strong>
            <span v-if="item.statement_index" class="ml-2 text-xs text-amber-700">语句 {{ item.statement_index }}</span>
            <p class="mt-1 text-amber-800">{{ item.message }}</p>
          </li>
        </ul>
      </div>
      <div v-if="dynamicSqlSegments.length">
        <h3 class="mb-2 font-bold">动态 SQL</h3>
        <div v-for="item in dynamicSqlSegments" :key="item.sql" class="mb-2 rounded-xl bg-white/70 p-3">
          <div class="mb-1 text-xs font-bold text-amber-700">{{ item.source }} · {{ item.confidence }}</div>
          <code class="break-all text-xs">{{ item.sql }}</code>
        </div>
      </div>
      <div v-if="parseErrors.length">
        <h3 class="mb-2 font-bold">解析失败片段</h3>
        <div v-for="item in parseErrors" :key="item.sql + item.error" class="mb-2 rounded-xl bg-white/70 p-3">
          <p class="text-amber-800">{{ item.error }}</p>
          <code class="mt-2 block break-all text-xs">{{ item.sql }}</code>
        </div>
      </div>
    </div>
  </div>
</template>
