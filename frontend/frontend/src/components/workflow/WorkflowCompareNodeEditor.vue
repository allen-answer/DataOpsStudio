<script setup>
import { computed } from 'vue'
import { useBootstrapStore } from '../../stores/bootstrap'

// 节点对象作为 reactive prop 传入；本组件直接 mutate 字段（与 store 已有
// workflowDraft 模式一致）。这样写在 Vue 3 reactive 下是可行的——传过来的
// 是 parent 同一个 reactive proxy，mutation 自动响应。
const props = defineProps({
  node: { type: Object, required: true },
})

const { state } = useBootstrapStore()

// drill-in：根据 task_id 找到任务定义，展示原始 SQL 给用户作为覆盖参考。
const compareTaskOptions = computed(() => state.tasks.map((t) => ({ id: t.id, name: t.name })))
const compareTaskById = (id) => state.tasks.find((t) => t.id === id)

// SQL override 软校验：首词必须是 SELECT/WITH。后端硬校验，这里只是保存前
// 提示，避免用户填了 WHERE 片段才提交才被拒。
const overrideLooksInvalid = (sql) => {
  if (!sql || !sql.trim()) return false
  const stripped = sql.replace(/--[^\n]*\n?/g, '').replace(/\/\*[\s\S]*?\*\//g, '').trim()
  if (!stripped) return false
  const firstWord = stripped.match(/^[a-zA-Z_]+/)?.[0]?.toLowerCase()
  return firstWord !== 'select' && firstWord !== 'with'
}
</script>

<template>
  <div>
    <label>
      <span class="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-slate-400">{{ $t('workflowEditor.compare.refTask') }}</span>
      <select v-model="node.task_id" class="w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 text-xs">
        <option value="">— 选择 —</option>
        <option v-for="task in compareTaskOptions" :key="task.id" :value="task.id">{{ task.name }}</option>
      </select>
    </label>

    <!-- drill into 引用任务的 SQL，可以覆盖并注入 ${var} -->
    <div v-if="node.task_id" class="mt-3 rounded-lg border border-slate-200 bg-slate-50/40 p-3">
      <div class="mb-2 flex items-center justify-between gap-2">
        <span class="text-[11px] font-semibold uppercase tracking-wider text-slate-500">{{ $t('workflowEditor.compare.sectionTaskSql') }}</span>
      </div>
      <p class="mb-2 rounded border border-amber-200 bg-amber-50 px-2 py-1.5 text-[10.5px] leading-relaxed text-amber-900">
        ⚠ 覆盖必须是<strong>完整的 <code class="font-mono">SELECT</code> / <code class="font-mono">WITH</code> 查询</strong>，<strong>不能只填 WHERE 片段</strong>。常见做法：把左边任务原 SQL 复制到下面的覆盖框，再在 WHERE 里插入 <code class="rounded bg-white px-1 font-mono">${name}</code>。变量在执行前替换。留空则使用任务原始 SQL。
      </p>
      <div class="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <div>
          <span class="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-slate-400">{{ $t('workflowEditor.compare.sourceSqlDef') }}</span>
          <pre class="mb-1.5 max-h-32 overflow-auto rounded border border-slate-200 bg-white px-2 py-1.5 font-mono text-[11px] leading-relaxed text-slate-600">{{ compareTaskById(node.task_id)?.source_sql || '(无)' }}</pre>
          <div class="mb-1 flex items-center justify-between">
            <span class="block text-[10px] font-semibold uppercase tracking-wider text-slate-400">{{ $t('workflowEditor.compare.overrideOptional') }}</span>
            <button v-if="!node.source_sql_override?.trim()" type="button"
                    class="rounded border border-slate-200 bg-white px-1.5 py-0.5 text-[10px] text-slate-600 transition hover:border-blue-300 hover:bg-blue-50 hover:text-blue-700"
                    @click="node.source_sql_override = compareTaskById(node.task_id)?.source_sql || ''">
              ↗ 复制原 SQL
            </button>
          </div>
          <textarea v-model="node.source_sql_override"
                    class="block min-h-[70px] w-full rounded-md border bg-white px-2 py-1.5 font-mono text-[11.5px]"
                    :class="overrideLooksInvalid(node.source_sql_override) ? 'border-rose-300 bg-rose-50/30' : 'border-slate-200'"
                    placeholder="留空 = 不覆盖；非空则必须以 SELECT 或 WITH 开头"></textarea>
          <p v-if="overrideLooksInvalid(node.source_sql_override)" class="mt-1 text-[10.5px] text-rose-600">
            首词不是 SELECT/WITH —— 保存时会被服务端拒绝。
          </p>
        </div>
        <div>
          <span class="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-slate-400">{{ $t('workflowEditor.compare.targetSqlDef') }}</span>
          <pre class="mb-1.5 max-h-32 overflow-auto rounded border border-slate-200 bg-white px-2 py-1.5 font-mono text-[11px] leading-relaxed text-slate-600">{{ compareTaskById(node.task_id)?.target_sql || '(无)' }}</pre>
          <div class="mb-1 flex items-center justify-between">
            <span class="block text-[10px] font-semibold uppercase tracking-wider text-slate-400">{{ $t('workflowEditor.compare.overrideOptional') }}</span>
            <button v-if="!node.target_sql_override?.trim()" type="button"
                    class="rounded border border-slate-200 bg-white px-1.5 py-0.5 text-[10px] text-slate-600 transition hover:border-blue-300 hover:bg-blue-50 hover:text-blue-700"
                    @click="node.target_sql_override = compareTaskById(node.task_id)?.target_sql || ''">
              ↗ 复制原 SQL
            </button>
          </div>
          <textarea v-model="node.target_sql_override"
                    class="block min-h-[70px] w-full rounded-md border bg-white px-2 py-1.5 font-mono text-[11.5px]"
                    :class="overrideLooksInvalid(node.target_sql_override) ? 'border-rose-300 bg-rose-50/30' : 'border-slate-200'"
                    placeholder="留空 = 不覆盖；非空则必须以 SELECT 或 WITH 开头"></textarea>
          <p v-if="overrideLooksInvalid(node.target_sql_override)" class="mt-1 text-[10.5px] text-rose-600">
            首词不是 SELECT/WITH —— 保存时会被服务端拒绝。
          </p>
        </div>
      </div>
    </div>
  </div>
</template>
