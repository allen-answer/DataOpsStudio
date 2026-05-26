<script setup lang="ts">
/**
 * SQL 工作台 v0.5 ExplainPanel —— 把原本 inline 在 SqlWorkbenchView 底部
 * explain tab 的渲染抽出来,作为可单测的独立组件。
 *
 * 输入 explain 响应(可能为 null / unsupported / error / success 四态),
 * 加 hints 数组(来自后端 sql_hints,即使 unsupported 也会有内容),
 * 渲染表格 + 顶部规则提醒 + "复制 plan" 按钮(JSON / Text 两个格式)。
 */
import { ref, computed } from 'vue'
import { Copy, Check, Info } from 'lucide-vue-next'

interface ExplainRow extends Array<unknown> {}

interface ExplainResponse {
  success: boolean
  dialect: string
  columns: string[]
  rows: ExplainRow[]
  explain_sql: string
  elapsed_ms: number
  unsupported: boolean
  error?: string | null
  hints?: { code: string; severity: string; message: string }[]
}

const props = defineProps<{
  explain: ExplainResponse | null
}>()

const copyState = ref<'idle' | 'copied'>('idle')

const hints = computed(() => props.explain?.hints || [])

async function copyPlan(format: 'text' | 'json') {
  if (!props.explain) return
  let text = ''
  if (format === 'json') {
    text = JSON.stringify({
      dialect: props.explain.dialect,
      explain_sql: props.explain.explain_sql,
      columns: props.explain.columns,
      rows: props.explain.rows,
      hints: props.explain.hints,
    }, null, 2)
  } else {
    // text:Markdown 表格,粘贴到 IM / 文档里能直接看
    const cols = props.explain.columns
    const lines = [
      `-- ${props.explain.explain_sql}`,
      '',
      '| ' + cols.join(' | ') + ' |',
      '| ' + cols.map(() => '---').join(' | ') + ' |',
      ...props.explain.rows.map(r => '| ' + r.map(c => c === null ? 'NULL' : String(c)).join(' | ') + ' |'),
    ]
    text = lines.join('\n')
  }
  try {
    await navigator.clipboard.writeText(text)
    copyState.value = 'copied'
    setTimeout(() => { copyState.value = 'idle' }, 1500)
  } catch {
    // 旧浏览器降级:用 textarea + execCommand。再不行就让用户手选。
    const ta = document.createElement('textarea')
    ta.value = text
    document.body.appendChild(ta)
    ta.select()
    try { document.execCommand('copy') } catch { /* ignore */ }
    document.body.removeChild(ta)
    copyState.value = 'copied'
    setTimeout(() => { copyState.value = 'idle' }, 1500)
  }
}

function hintChipClass(severity: string): string {
  if (severity === 'error') return 'bg-status-error-bg text-status-error border-status-error/30'
  if (severity === 'warning') return 'bg-status-warning-bg text-status-warning border-status-warning/30'
  return 'bg-status-info-bg text-status-info border-status-info/30'
}
</script>

<template>
  <div class="flex flex-col h-full">
    <!-- 空态 -->
    <div v-if="!explain" class="px-4 py-10 text-center text-sm text-slate-400">
      点击「Explain」查看执行计划
    </div>

    <template v-else>
      <!-- v0.5 hints —— 无论 explain 成功失败,只要有 SQL 文本就给规则提示。
           顶部独立 banner,跟 plan 表格分离 -->
      <div
        v-if="hints.length"
        class="px-3 py-2 border-b border-slate-100 space-y-1 bg-slate-50"
      >
        <div class="text-[10px] font-bold uppercase tracking-wider text-slate-500 mb-0.5">
          静态规则提醒({{ hints.length }})
        </div>
        <div
          v-for="h in hints"
          :key="h.code"
          class="inline-flex items-start gap-1.5 mr-2 mb-1 rounded border px-2 py-1 text-[11px]"
          :class="hintChipClass(h.severity)"
          :data-hint-code="h.code"
        >
          <Info class="h-3 w-3 mt-0.5 shrink-0" />
          <span>{{ h.message }}</span>
        </div>
      </div>

      <!-- unsupported(Oracle / DM / DB2)—— 不假装成功,明确告诉用户原因 + 出路 -->
      <div
        v-if="explain.unsupported"
        class="m-3 rounded border border-status-warning/30 bg-status-warning-bg p-3 text-xs text-status-warning"
      >
        <div class="font-bold mb-1">⚠ {{ explain.dialect || '该方言' }} 暂不支持 EXPLAIN</div>
        <p class="text-slate-700 whitespace-pre-wrap">{{ explain.error }}</p>
      </div>

      <!-- 失败 -->
      <div
        v-else-if="!explain.success"
        class="m-3 rounded border border-status-error/30 bg-status-error-bg p-3 text-xs text-status-error"
      >
        <div class="font-bold mb-1">Explain 失败</div>
        <pre class="sql-font whitespace-pre-wrap break-all">{{ explain.error }}</pre>
      </div>

      <!-- 成功:plan 表格 + 复制按钮 -->
      <template v-else>
        <div class="flex items-center justify-between px-3 py-1.5 bg-slate-50 border-b border-slate-100">
          <div class="text-[11px] text-slate-500 sql-font truncate flex-1" :title="explain.explain_sql">
            {{ explain.explain_sql }}
          </div>
          <div class="flex items-center gap-1 shrink-0">
            <button
              class="inline-flex items-center gap-1 rounded border border-slate-200 bg-white px-2 py-0.5 text-[11px] text-slate-600 hover:bg-slate-50"
              title="复制为 Markdown 表格"
              @click="copyPlan('text')"
            >
              <component :is="copyState === 'copied' ? Check : Copy" class="h-3 w-3" />
              {{ copyState === 'copied' ? '已复制' : '复制' }}
            </button>
            <button
              class="inline-flex items-center gap-1 rounded border border-slate-200 bg-white px-2 py-0.5 text-[11px] text-slate-600 hover:bg-slate-50"
              title="复制为 JSON 结构(给 AI 喂)"
              @click="copyPlan('json')"
            >
              JSON
            </button>
          </div>
        </div>
        <div class="overflow-auto flex-1 min-h-0">
          <table class="text-xs w-full">
            <thead class="bg-slate-50 sticky top-0">
              <tr>
                <th v-for="col in explain.columns" :key="col" class="text-left whitespace-nowrap">{{ col }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, i) in explain.rows" :key="i" class="hover:bg-slate-50">
                <td v-for="(cell, j) in row" :key="j" class="sql-font whitespace-nowrap" :title="String(cell ?? '')">
                  <span v-if="cell === null" class="italic text-slate-400">NULL</span>
                  <template v-else>{{ cell }}</template>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>
    </template>
  </div>
</template>
