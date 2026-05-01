<script setup>
defineProps({
  columns: { type: Array, default: () => [] },
  insertMappings: { type: Array, default: () => [] },
})
</script>

<template>
  <div class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
    <h2 class="mb-4 text-xl font-bold text-slate-800">字段血缘</h2>
    <div class="overflow-auto">
      <table>
        <thead><tr><th>输出字段</th><th>来源字段</th><th>来源表</th><th>可信度</th><th>变量</th><th>表达式</th></tr></thead>
        <tbody>
          <tr v-for="item in columns" :key="item.output_column + item.expression">
            <td>{{ item.output_column }}</td>
            <td>{{ item.source_columns.join(', ') }}</td>
            <td>{{ item.source_tables.join(', ') }}</td>
            <td>{{ item.confidence || 'high' }}</td>
            <td>{{ item.variables.join(', ') }}</td>
            <td>
              <code>{{ item.expression }}</code>
              <p v-if="item.warnings?.length" class="mt-1 text-xs text-amber-600">{{ item.warnings.map(w => w.type).join(', ') }}</p>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
  <div class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
    <h2 class="mb-4 text-xl font-bold text-slate-800">落表字段映射</h2>
    <div class="overflow-auto">
      <table>
        <thead><tr><th>目标表</th><th>目标字段</th><th>来源字段</th><th>来源表</th><th>可信度</th><th>处理逻辑</th></tr></thead>
        <tbody>
          <tr v-for="item in insertMappings" :key="item.target_table + item.target_column + item.position">
            <td>{{ item.target_table }}</td>
            <td>{{ item.target_column }}</td>
            <td>{{ item.source_columns.join(', ') }}</td>
            <td>{{ item.source_tables.join(', ') }}</td>
            <td>{{ item.confidence || 'high' }}</td>
            <td>
              {{ item.transform }}
              <p v-if="item.warnings?.length" class="mt-1 text-xs text-amber-600">{{ item.warnings.map(w => w.type).join(', ') }}</p>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
