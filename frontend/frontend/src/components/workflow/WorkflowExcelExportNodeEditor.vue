<script setup>
import { ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorkflowStore } from '../../stores/workflow'

const props = defineProps({
  node: { type: Object, required: true },
})

// workflowDraft.nodes：跨节点查 candidate / dataset preset 用
// allWorkflowRuns / loadAllWorkflowRuns：history_run 模式选历史 run
// addExportSheet / removeExportSheet / moveExportSheet：跟 store 中
//   全局 reactive 同步（部分逻辑会触发 expand 默认值或 id 自增）
const workflowStore = useWorkflowStore()
const { workflowDraft } = workflowStore                       // reactive 直接拿
const { allWorkflowRuns } = storeToRefs(workflowStore)        // ref 经 storeToRefs
const {
  loadAllWorkflowRuns,
  addExportSheet, removeExportSheet, moveExportSheet,
} = workflowStore

// Sheet 模板 / 来源中文标签 —— 对应 compare 节点 dataset 短名
const sheetTemplateIds = ['summary', 'diff', 'only_source', 'only_target', 'same']
const sheetSourceLabel = {
  summary:     '汇总对照',
  diff:        '差异明细',
  only_source: '仅源端',
  only_target: '仅目标',
  same:        '一致行',
}

// 各节点类型可用的 dataset 预设（驱动下拉）。空 dataset 也允许用户手输（lineage
// 节点可能想拿 'tables' / 'edges' 等顶层字段）。
const datasetPresetsByType = {
  compare: ['summary', 'diff', 'only_source', 'only_target', 'same'],
  lineage: ['sources', 'targets', 'edges', 'table_edges', 'table_groups', 'warnings', 'field_mappings', 'insert_mappings', 'report.summary'],
  params:  [],
  http:    ['body', 'json', 'headers'],
}
const datasetPresetsForNode = (workflowNodes, nodeId) => {
  const target = (workflowNodes || []).find((n) => n.id === nodeId)
  if (!target) return []
  return datasetPresetsByType[target.type] || []
}

// excel_export sheet 数据源候选：作业流里所有能产出 dict 输出的节点，排除自身和 http
const candidateSourceNodes = (currentNode) =>
  workflowDraft.nodes.filter((n) =>
    n.id !== currentNode.id &&
    ['compare', 'lineage', 'params'].includes(n.type)
  )

// 选了 source_node → 自动加进 depends_on，避免还要手动去依赖列表勾选
const ensureSheetDependency = (node, sheet) => {
  if (!sheet.node_id) return
  if (!Array.isArray(node.depends_on)) node.depends_on = []
  if (!node.depends_on.includes(sheet.node_id)) {
    node.depends_on.push(sheet.node_id)
  }
}

// expandedSheets：当前组件本地 state，每个 excel_export 节点的子组件实例
// 自己管自己的展开状态，不再用 parent 的 node-idx 拼 key
const expandedSheets = ref({})
const toggleSheet = (idx) => {
  expandedSheets.value[idx] = !expandedSheets.value[idx]
}
</script>

<template>
  <div class="space-y-3">
    <div class="rounded-lg border border-slate-200 bg-white">
      <div class="flex items-center justify-between border-b border-slate-200 px-3 py-2">
        <span class="text-[11px] font-semibold uppercase tracking-wider text-slate-500">Sheet 列表（{{ (node.sheets || []).length }}）</span>
        <div class="flex items-center gap-1">
          <select class="h-6 rounded border border-slate-200 bg-white px-1.5 text-[10.5px] text-slate-700"
                  @change="addExportSheet(node, $event.target.value); $event.target.value = ''">
            <option value="" disabled selected>+ 添加 Sheet</option>
            <option v-for="t in sheetTemplateIds" :key="t" :value="t">{{ sheetSourceLabel[t] }}</option>
          </select>
        </div>
      </div>

      <div v-if="!(node.sheets || []).length" class="px-3 py-6 text-center text-[11px] text-slate-400">还没有 Sheet，从上方下拉添加</div>

      <ul v-else class="divide-y divide-slate-100">
        <li v-for="(sheet, sIdx) in node.sheets" :key="sheet.id" class="px-3 py-2">
          <!-- 折叠态 -->
          <div class="flex items-center gap-2">
            <input type="checkbox" v-model="sheet.enabled" class="h-3.5 w-3.5 rounded text-blue-600" :title="sheet.enabled ? '已启用' : '已禁用'">
            <button class="rounded border border-slate-200 px-1.5 py-0.5 text-[10px] font-mono text-slate-600 transition hover:bg-slate-50"
                    @click="toggleSheet(sIdx)">
              {{ expandedSheets[sIdx] ? '▾' : '▸' }}
            </button>
            <input v-model="sheet.sheet_name" class="flex-1 rounded-md border border-slate-200 bg-white px-2 py-1 font-mono text-xs" placeholder="Sheet 名">
            <span class="font-mono text-[10.5px] text-slate-500">
              <span v-if="sheet.source_type === 'history_run'" class="rounded bg-purple-50 px-1 py-0.5 text-purple-700 ring-1 ring-inset ring-purple-200">历史</span>
              {{ sheet.node_id || '默认' }}<span class="text-slate-300">.</span>{{ sheet.dataset || '*' }}
            </span>
            <button class="rounded border border-slate-200 px-1.5 py-0.5 text-[10px] text-slate-500 transition hover:bg-slate-50 disabled:opacity-30" :disabled="sIdx === 0" @click="moveExportSheet(node, sIdx, -1)">↑</button>
            <button class="rounded border border-slate-200 px-1.5 py-0.5 text-[10px] text-slate-500 transition hover:bg-slate-50 disabled:opacity-30" :disabled="sIdx === node.sheets.length - 1" @click="moveExportSheet(node, sIdx, 1)">↓</button>
            <button class="rounded border border-rose-200 bg-rose-50 px-1.5 py-0.5 text-[10px] font-bold text-rose-700 transition hover:bg-rose-100" @click="removeExportSheet(node, sIdx)">×</button>
          </div>

          <!-- 展开态：详细配置 -->
          <div v-if="expandedSheets[sIdx]" class="mt-2 rounded-md bg-slate-50/60 p-2.5 space-y-2">
            <!-- 数据源类型切换：节点输出 vs 历史运行 -->
            <div class="flex items-center gap-2">
              <span class="text-[10px] font-semibold uppercase tracking-wider text-slate-400">数据源</span>
              <div class="inline-flex rounded-md border border-slate-200 bg-white p-0.5 text-[10.5px]">
                <button type="button"
                        class="rounded px-2 py-0.5 transition"
                        :class="sheet.source_type !== 'history_run' ? 'bg-blue-600 text-white' : 'text-slate-600'"
                        @click="sheet.source_type = 'node_output'; sheet.run_id = ''">
                  节点输出
                </button>
                <button type="button"
                        class="rounded px-2 py-0.5 transition"
                        :class="sheet.source_type === 'history_run' ? 'bg-purple-600 text-white' : 'text-slate-600'"
                        @click="sheet.source_type = 'history_run'">
                  历史运行
                </button>
              </div>
            </div>

            <!-- node_output 模式 -->
            <div v-if="sheet.source_type !== 'history_run'" class="grid grid-cols-1 gap-2 lg:grid-cols-3">
              <label>
                <span class="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-slate-400">节点</span>
                <select v-model="sheet.node_id"
                        @change="ensureSheetDependency(node, sheet)"
                        class="w-full rounded-md border border-slate-200 bg-white px-2 py-1 font-mono text-xs">
                  <option value="">默认（depends_on 第一个）</option>
                  <option v-for="cand in candidateSourceNodes(node)" :key="cand.id" :value="cand.id">
                    {{ cand.id }}（{{ cand.type }}）{{ cand.name ? ' · ' + cand.name : '' }}
                  </option>
                </select>
              </label>
              <label>
                <span class="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-slate-400">数据集（dataset）</span>
                <input v-model="sheet.dataset"
                       :list="`dataset-presets-${node.id}-${sIdx}`"
                       class="w-full rounded-md border border-slate-200 bg-white px-2 py-1 font-mono text-xs"
                       placeholder="summary / diff / only_source / ...">
                <datalist :id="`dataset-presets-${node.id}-${sIdx}`">
                  <option v-for="d in datasetPresetsForNode(workflowDraft.nodes, sheet.node_id)" :key="d" :value="d" />
                </datalist>
              </label>
              <label>
                <span class="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-slate-400">最大行数</span>
                <input v-model="sheet.max_rows" type="number" class="w-full rounded-md border border-slate-200 bg-white px-2 py-1 text-xs">
              </label>
            </div>

            <!-- history_run 模式 -->
            <div v-else class="grid grid-cols-1 gap-2 lg:grid-cols-3">
              <label class="lg:col-span-3">
                <span class="mb-1 flex items-center justify-between text-[10px] font-semibold uppercase tracking-wider text-slate-400">
                  历史运行（run_id）
                  <button type="button" class="text-[10px] font-mono normal-case text-blue-600 hover:underline" @click="loadAllWorkflowRuns">↻ 刷新列表</button>
                </span>
                <select v-model="sheet.run_id" class="w-full rounded-md border border-slate-200 bg-white px-2 py-1 font-mono text-xs">
                  <option value="">— 选择一次历史 run —</option>
                  <option v-for="r in (allWorkflowRuns || [])" :key="r.run_id" :value="r.run_id">
                    {{ r.workflow_name }} · {{ r.run_id.slice(0, 8) }} · {{ r.started_at?.slice(5, 16) || '' }} · {{ r.status }}
                  </option>
                </select>
              </label>
              <label>
                <span class="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-slate-400">该 run 的节点 id</span>
                <input v-model="sheet.node_id" class="w-full rounded-md border border-slate-200 bg-white px-2 py-1 font-mono text-xs" placeholder="例如 n1">
              </label>
              <label>
                <span class="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-slate-400">数据集（dataset）</span>
                <input v-model="sheet.dataset" class="w-full rounded-md border border-slate-200 bg-white px-2 py-1 font-mono text-xs" placeholder="summary / diff / ...">
              </label>
              <label>
                <span class="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-slate-400">最大行数</span>
                <input v-model="sheet.max_rows" type="number" class="w-full rounded-md border border-slate-200 bg-white px-2 py-1 text-xs">
              </label>
            </div>

            <p class="text-[10.5px] text-slate-500">
              <template v-if="sheet.source_type === 'history_run'">
                从指定的历史 run 拿 <code class="rounded bg-white px-1 font-mono text-[10px]">nodes[node_id].output[dataset]</code>。
                run_id 是某次执行的唯一标识，不是任务定义。
              </template>
              <template v-else>
                从本次运行的 <code class="rounded bg-white px-1 font-mono text-[10px]">outputs[node_id][dataset]</code> 拿数据。
                节点留空 = 用 depends_on 第一个；选中节点会自动加入 depends_on。
                compare 节点 dataset 用短名（summary / diff / only_source / only_target / same），
                runner 自动映射到 samples.* 字段。
              </template>
            </p>
          </div>
        </li>
      </ul>
    </div>
  </div>
</template>
