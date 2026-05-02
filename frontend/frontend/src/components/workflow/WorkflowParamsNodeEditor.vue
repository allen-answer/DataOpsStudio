<script setup>
import { inject, ref } from 'vue'

const props = defineProps({
  node: { type: Object, required: true },
})

// 全局共享状态从父注入：state.datasources（sql_result 类型参数选数据源用）+
// addParameter / removeParameter（这俩在 App.vue 里，会同时维护 node.parameters
// 数组并触发其他副作用，所以不在子组件里 inline 实现）
const { state, addParameter, removeParameter } = inject('app')

// 参数类型可选项（与 mock/workflow_meta.parameterTypeMeta 对齐）
const paramTypeOptions = [
  { id: 'fixed',         label: '固定值' },
  { id: 'date',          label: '日期' },
  { id: 'relative_date', label: '相对日期' },
  { id: 'multi_value',   label: '多值' },
  { id: 'sql_result',    label: 'SQL 结果' },
  { id: 'json',          label: 'JSON' },
]
const relativeDateSources = [
  { id: 'today',      label: '今天 today' },
  { id: 'yesterday',  label: '昨天 yesterday' },
  { id: 'last_month', label: '上月 last_month' },
  { id: 'now',        label: '当前时间 now' },
]

// 速查表展开/收起：本组件局部 state，不需要 lift 到 parent
const showCheatsheet = ref(false)
</script>

<template>
  <div class="rounded-lg border border-slate-200 bg-white">
    <div class="flex items-center justify-between border-b border-slate-200 px-3 py-2">
      <span class="text-[11px] font-semibold uppercase tracking-wider text-slate-500">参数列表（{{ (node.parameters || []).length }}）</span>
      <div class="flex items-center gap-1.5">
        <button class="rounded border border-slate-200 bg-white px-2 py-0.5 text-[10.5px] font-semibold text-slate-600 transition hover:border-blue-300 hover:bg-blue-50 hover:text-blue-700"
                @click="showCheatsheet = !showCheatsheet">
          ? 引用语法速查
        </button>
        <select class="h-6 rounded border border-slate-200 bg-white px-1.5 text-[10.5px] text-slate-700"
                @change="addParameter(node, $event.target.value); $event.target.value = ''">
          <option value="" disabled selected>+ 新增参数</option>
          <option v-for="t in paramTypeOptions" :key="t.id" :value="t.id">{{ t.label }}</option>
        </select>
      </div>
    </div>

    <!-- 速查表：一眼看懂如何在 SQL / 文件名 / 任意字符串字段里引用参数 -->
    <div v-if="showCheatsheet" class="border-b border-slate-200 bg-slate-50/60 px-3 py-3 text-[12px] leading-relaxed">
      <p class="mb-2 text-[11px] font-semibold uppercase tracking-wider text-slate-500">参数引用语法速查 · 完整文档见 <code class="rounded bg-white px-1 py-0.5 font-mono text-[10.5px] text-slate-700">docs/PARAMETERS.md</code></p>
      <table class="w-full border-collapse text-[11.5px]">
        <thead>
          <tr class="border-b border-slate-200">
            <th class="py-1.5 pr-3 text-left font-semibold text-slate-500 w-[44%]">写法</th>
            <th class="py-1.5 pr-3 text-left font-semibold text-slate-500 w-[34%]">解析后</th>
            <th class="py-1.5 text-left font-semibold text-slate-500">用于</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-slate-100">
          <tr>
            <td class="py-1.5 pr-3 font-mono text-blue-700">${user_id}</td>
            <td class="py-1.5 pr-3 font-mono text-slate-600">42</td>
            <td class="py-1.5 text-slate-600">单值参数</td>
          </tr>
          <tr>
            <td class="py-1.5 pr-3 font-mono text-blue-700">'${biz_date}'</td>
            <td class="py-1.5 pr-3 font-mono text-slate-600">'2026-05-01'</td>
            <td class="py-1.5 text-slate-600">字符串/日期，注意手动加引号</td>
          </tr>
          <tr>
            <td class="py-1.5 pr-3 font-mono text-blue-700">${ids | sql_in}</td>
            <td class="py-1.5 pr-3 font-mono text-slate-600">1, 5, 9</td>
            <td class="py-1.5 text-slate-600">多值 → IN 子句体（数字保持原样）</td>
          </tr>
          <tr>
            <td class="py-1.5 pr-3 font-mono text-blue-700">${names | sql_in}</td>
            <td class="py-1.5 pr-3 font-mono text-slate-600">'a', 'b'</td>
            <td class="py-1.5 text-slate-600">多值字符串，自动单引号 + 转义</td>
          </tr>
          <tr>
            <td class="py-1.5 pr-3 font-mono text-blue-700">${nodes.x.summary.diff}</td>
            <td class="py-1.5 pr-3 font-mono text-slate-600">7</td>
            <td class="py-1.5 text-slate-600">上游节点输出（要 depends_on）</td>
          </tr>
          <tr>
            <td class="py-1.5 pr-3 font-mono text-blue-700">${nodes.params.ids.0}</td>
            <td class="py-1.5 pr-3 font-mono text-slate-600">1</td>
            <td class="py-1.5 text-slate-600">取 list 第 N 项</td>
          </tr>
        </tbody>
      </table>
      <div class="mt-3 grid grid-cols-1 gap-2 lg:grid-cols-2">
        <div class="rounded border border-slate-200 bg-white p-2">
          <p class="text-[10px] font-semibold uppercase tracking-wider text-slate-400">典型用法 · 单值条件</p>
          <pre class="mt-1 overflow-x-auto font-mono text-[11px] text-slate-700">SELECT * FROM orders
WHERE dt = '${biz_date}'
  AND user_id = ${user_id}</pre>
        </div>
        <div class="rounded border border-slate-200 bg-white p-2">
          <p class="text-[10px] font-semibold uppercase tracking-wider text-slate-400">典型用法 · IN 子句</p>
          <pre class="mt-1 overflow-x-auto font-mono text-[11px] text-slate-700">SELECT * FROM orders
WHERE user_id IN (${vip_users | sql_in})</pre>
        </div>
      </div>
      <p class="mt-2 text-[10.5px] text-slate-500">解析顺序：运行时变量 → 工作流默认 → params 节点输出 → 内置（today/now/...）。同名以前者覆盖后者。引用未定义变量节点会 FAILED，详见文档。</p>
    </div>

    <div v-if="!(node.parameters || []).length" class="px-3 py-6 text-center text-[11px] text-slate-400">
      还没有参数。从右上方添加第一个参数。
    </div>

    <ul v-else class="divide-y divide-slate-100">
      <li v-for="(p, pIdx) in node.parameters" :key="pIdx" class="px-3 py-2.5">
        <div class="grid grid-cols-1 gap-2 lg:grid-cols-[120px_minmax(0,1fr)_minmax(0,1fr)_60px]">
          <label>
            <span class="mb-0.5 block text-[10px] font-semibold uppercase tracking-wider text-slate-400">类型</span>
            <select v-model="p.type" class="w-full rounded-md border border-slate-200 bg-white px-2 py-1 text-xs">
              <option v-for="t in paramTypeOptions" :key="t.id" :value="t.id">{{ t.label }}</option>
            </select>
          </label>
          <label>
            <span class="mb-0.5 block text-[10px] font-semibold uppercase tracking-wider text-slate-400">名称</span>
            <input v-model="p.name" class="w-full rounded-md border border-slate-200 bg-white px-2 py-1 font-mono text-xs" placeholder="例如 biz_date">
          </label>
          <label>
            <span class="mb-0.5 block text-[10px] font-semibold uppercase tracking-wider text-slate-400">说明</span>
            <input v-model="p.description" class="w-full rounded-md border border-slate-200 bg-white px-2 py-1 text-xs" placeholder="给协作者的简短说明">
          </label>
          <div class="flex items-end justify-end gap-1">
            <label class="flex cursor-pointer items-center gap-1 text-[10.5px] text-slate-600"><input type="checkbox" v-model="p.required" class="h-3 w-3 rounded text-blue-600">必填</label>
            <button class="rounded border border-rose-200 bg-rose-50 px-1.5 py-0.5 text-[10px] font-bold text-rose-700 transition hover:bg-rose-100" @click="removeParameter(node, pIdx)">×</button>
          </div>
        </div>

        <!-- 类型相关字段 -->
        <div class="mt-2">
          <label v-if="p.type === 'fixed' || p.type === 'date' || p.type === 'json'" class="block">
            <span class="mb-0.5 block text-[10px] font-semibold uppercase tracking-wider text-slate-400">默认值</span>
            <input v-if="p.type !== 'json'" v-model="p.default" class="w-full rounded-md border border-slate-200 bg-white px-2 py-1 font-mono text-xs" :placeholder="p.type === 'date' ? '2026-05-01' : '默认值'">
            <textarea v-else v-model="p.default" class="block min-h-[50px] w-full rounded-md border border-slate-200 bg-white px-2 py-1 font-mono text-xs" placeholder='{"key": "value"}'></textarea>
          </label>

          <label v-if="p.type === 'relative_date'" class="block">
            <span class="mb-0.5 block text-[10px] font-semibold uppercase tracking-wider text-slate-400">相对来源</span>
            <select v-model="p.source" class="w-full rounded-md border border-slate-200 bg-white px-2 py-1 text-xs">
              <option v-for="src in relativeDateSources" :key="src.id" :value="src.id">{{ src.label }}</option>
            </select>
          </label>

          <label v-if="p.type === 'multi_value'" class="block">
            <span class="mb-0.5 block text-[10px] font-semibold uppercase tracking-wider text-slate-400">默认值（每行一个）</span>
            <textarea
              :value="Array.isArray(p.default) ? p.default.join('\n') : (p.default || '')"
              @input="p.default = $event.target.value.split('\n').map(s => s.trim()).filter(Boolean)"
              class="block min-h-[50px] w-full rounded-md border border-slate-200 bg-white px-2 py-1 font-mono text-xs" placeholder="A\nB\nC"></textarea>
          </label>

          <div v-if="p.type === 'sql_result'" class="grid grid-cols-1 gap-2 lg:grid-cols-[160px_minmax(0,1fr)]">
            <label>
              <span class="mb-0.5 block text-[10px] font-semibold uppercase tracking-wider text-slate-400">数据源</span>
              <select v-model="p.datasource" class="w-full rounded-md border border-slate-200 bg-white px-2 py-1 text-xs">
                <option value="">— 选择 —</option>
                <option v-for="ds in state.datasources" :key="ds.id" :value="ds.id">{{ ds.name }}</option>
              </select>
            </label>
            <label>
              <span class="mb-0.5 block text-[10px] font-semibold uppercase tracking-wider text-slate-400">SQL（取第一列作为参数值）</span>
              <textarea v-model="p.sql" class="block min-h-[50px] w-full rounded-md border border-slate-200 bg-white px-2 py-1 font-mono text-xs" placeholder="SELECT id FROM ..."></textarea>
            </label>
          </div>
        </div>
      </li>
    </ul>

    <div class="border-t border-slate-100 px-3 py-2 text-[10.5px] text-slate-500">
      解析后的参数会注入到 workflow 变量域，下游节点可用 <code class="rounded bg-slate-100 px-1 font-mono">${name}</code> 引用
    </div>
  </div>
</template>
