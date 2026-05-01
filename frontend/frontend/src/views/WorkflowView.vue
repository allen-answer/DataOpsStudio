<script setup>
import { computed, inject } from 'vue'

const {
  state,
  workflowDraft,
  selectedWorkflowId,
  isSavedWorkflow,
  workflowResult,
  workflowAsyncJob,
  workflowAsyncStatus,
  selectWorkflow,
  saveWorkflow,
  deleteWorkflow,
  runWorkflow,
  runWorkflowAsync,
  cancelWorkflowAsync,
  addWorkflowNode,
  removeWorkflowNode,
  moveWorkflowNode,
} = inject('app')

const compareTaskOptions = computed(() => state.tasks.map((task) => ({ id: task.id, name: task.name })))

const otherNodeIds = (currentId) => workflowDraft.nodes.map((n) => n.id).filter((id) => id && id !== currentId)

const lastRun = computed(() => {
  // Show the async run state if it's still active or just finished;
  // otherwise fall back to the synchronous run result. Both share the
  // same WorkflowRun shape, so the same panel renders either.
  if (workflowAsyncStatus.value && workflowAsyncStatus.value.result) return workflowAsyncStatus.value.result
  return workflowResult.value
})

const nodeStatusClass = (status) => {
  switch (status) {
    case 'success': return 'bg-emerald-100 text-emerald-700'
    case 'running': return 'bg-blue-100 text-blue-700'
    case 'failed':  return 'bg-red-100 text-red-700'
    case 'skipped': return 'bg-slate-200 text-slate-500'
    default:        return 'bg-slate-100 text-slate-500'
  }
}

const taskNameById = (id) => state.tasks.find((task) => task.id === id)?.name || id || '(未选择)'
</script>

<template>
  <section class="space-y-6">
    <div class="grid grid-cols-[340px_minmax(0,1fr)] gap-6">
      <aside class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
        <div class="mb-6 flex items-end justify-between">
          <div>
            <h2 class="text-2xl font-bold text-slate-800">作业流</h2>
            <p class="mt-1 text-sm text-slate-500">{{ state.workflows.length }} 个作业流 · {{ state.tasks.length }} 个对比任务</p>
          </div>
          <button class="rounded-lg bg-blue-600 px-4 py-2 text-sm font-bold text-white transition hover:bg-blue-700" @click="selectWorkflow('new')">新建</button>
        </div>
        <div class="max-h-[calc(100vh-220px)] space-y-2 overflow-auto pr-1">
          <button
            v-for="wf in state.workflows"
            :key="wf.id"
            class="w-full rounded-xl border p-4 text-left transition"
            :class="selectedWorkflowId === wf.id ? 'border-blue-300 bg-blue-50 shadow-sm' : 'border-transparent bg-slate-50 hover:border-slate-200 hover:bg-white'"
            @click="selectWorkflow(wf.id)"
          >
            <strong class="block text-slate-800">{{ wf.name }}</strong>
            <span class="mt-1 block text-sm text-slate-500">{{ wf.nodes.length }} 个节点</span>
          </button>
          <p v-if="!state.workflows.length" class="rounded-xl border border-dashed border-slate-200 p-4 text-sm text-slate-400">暂无作业流，点击新建开始。</p>
        </div>
      </aside>

      <section class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
        <div class="mb-6 flex items-start justify-between gap-4">
          <div>
            <h2 class="text-2xl font-bold text-slate-800">{{ workflowDraft.name || '新增作业流' }}</h2>
            <p class="mt-1 text-sm text-slate-500">线性顺序执行；变量用 <code class="rounded bg-slate-100 px-1.5 py-0.5 text-[11px]">${'$'}{var}</code> 引用</p>
          </div>
          <div class="flex flex-wrap justify-end gap-2">
            <button class="rounded-lg bg-blue-600 px-4 py-2 text-sm font-bold text-white transition hover:bg-blue-700" @click="saveWorkflow">{{ isSavedWorkflow ? '保存修改' : '保存作业流' }}</button>
            <button class="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-bold text-white transition hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50" :disabled="!isSavedWorkflow" @click="runWorkflow">同步执行</button>
            <button class="rounded-lg border border-slate-200 px-4 py-2 text-sm font-bold text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50" :disabled="!isSavedWorkflow" @click="runWorkflowAsync">后台执行</button>
            <button class="rounded-lg bg-red-600 px-4 py-2 text-sm font-bold text-white transition hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-50" :disabled="!isSavedWorkflow" @click="deleteWorkflow">删除</button>
          </div>
        </div>

        <div class="grid gap-6">
          <div class="grid grid-cols-1 gap-4 xl:grid-cols-2">
            <label>
              <span class="mb-2 block text-xs font-bold uppercase tracking-wider text-slate-400">作业流名称</span>
              <input v-model="workflowDraft.name" class="w-full border-none bg-slate-50 px-4 py-3 focus:ring-2 focus:ring-blue-500" placeholder="日终对比 + 导出">
            </label>
            <label>
              <span class="mb-2 block text-xs font-bold uppercase tracking-wider text-slate-400">默认变量（每行 key=value）</span>
              <textarea v-model="workflowDraft.default_variables" class="min-h-[80px] w-full border-none bg-slate-50 px-4 py-3 font-mono text-sm focus:ring-2 focus:ring-blue-500" placeholder="biz_date=2026-05-01&#10;schema=dataops"></textarea>
            </label>
          </div>

          <div class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
            <div class="mb-5 flex items-center justify-between">
              <h3 class="text-lg font-bold text-slate-700">节点</h3>
              <button class="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-bold text-white transition hover:bg-blue-700" @click="addWorkflowNode">+ 新增节点</button>
            </div>
            <div v-if="!workflowDraft.nodes.length" class="rounded-xl border border-dashed border-slate-200 p-6 text-center text-sm text-slate-400">暂无节点。点击「新增节点」开始。</div>
            <div v-else class="space-y-3">
              <div v-for="(node, index) in workflowDraft.nodes" :key="node.id" class="rounded-xl border border-slate-200 bg-slate-50 p-4">
                <div class="mb-3 flex items-center justify-between gap-2">
                  <div class="flex items-center gap-2">
                    <span class="grid h-7 w-7 place-items-center rounded-lg bg-blue-600 text-xs font-bold text-white">{{ index + 1 }}</span>
                    <input v-model="node.id" class="w-24 rounded-md border-none bg-white px-2 py-1 text-xs font-mono focus:ring-2 focus:ring-blue-500" placeholder="node id">
                  </div>
                  <div class="flex items-center gap-1">
                    <button class="rounded-md bg-white px-2 py-1 text-[10px] font-bold text-slate-600 shadow-sm transition hover:bg-blue-50 disabled:opacity-30" :disabled="index === 0" @click="moveWorkflowNode(index, -1)">↑</button>
                    <button class="rounded-md bg-white px-2 py-1 text-[10px] font-bold text-slate-600 shadow-sm transition hover:bg-blue-50 disabled:opacity-30" :disabled="index === workflowDraft.nodes.length - 1" @click="moveWorkflowNode(index, 1)">↓</button>
                    <button class="rounded-md bg-red-50 px-2 py-1 text-[10px] font-bold text-red-700 shadow-sm transition hover:bg-red-100" @click="removeWorkflowNode(index)">删除</button>
                  </div>
                </div>
                <div class="grid grid-cols-1 gap-3 lg:grid-cols-3">
                  <label>
                    <span class="mb-1 block text-[10px] font-bold uppercase tracking-wider text-slate-400">类型</span>
                    <select v-model="node.type" class="w-full border-none bg-white px-3 py-2 text-sm">
                      <option value="compare">数据对比 (compare)</option>
                    </select>
                  </label>
                  <label>
                    <span class="mb-1 block text-[10px] font-bold uppercase tracking-wider text-slate-400">显示名称</span>
                    <input v-model="node.name" class="w-full border-none bg-white px-3 py-2 text-sm" placeholder="可选">
                  </label>
                  <label v-if="node.type === 'compare'">
                    <span class="mb-1 block text-[10px] font-bold uppercase tracking-wider text-slate-400">引用对比任务</span>
                    <select v-model="node.task_id" class="w-full border-none bg-white px-3 py-2 text-sm">
                      <option value="">— 选择任务 —</option>
                      <option v-for="task in compareTaskOptions" :key="task.id" :value="task.id">{{ task.name }}</option>
                    </select>
                  </label>
                </div>
                <div v-if="otherNodeIds(node.id).length" class="mt-3">
                  <span class="mb-1 block text-[10px] font-bold uppercase tracking-wider text-slate-400">依赖（depends_on）</span>
                  <div class="flex flex-wrap gap-2">
                    <label v-for="otherId in otherNodeIds(node.id)" :key="otherId" class="flex cursor-pointer items-center gap-1.5 rounded-md bg-white px-2.5 py-1 text-xs shadow-sm transition hover:bg-blue-50">
                      <input type="checkbox" :value="otherId" v-model="node.depends_on" class="h-3.5 w-3.5 rounded text-blue-600">
                      <span class="font-mono text-slate-700">{{ otherId }}</span>
                    </label>
                  </div>
                  <p class="mt-1 text-[10px] text-slate-400">勾选必须先完成的节点；输出可用 <code class="rounded bg-slate-100 px-1 py-0.5 text-[10px]">${'$'}{nodes.{{ node.id }}.summary.diff}</code> 这种引用</p>
                </div>
              </div>
            </div>
          </div>

          <div class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
            <div class="mb-4 flex items-center justify-between gap-3">
              <div>
                <h3 class="text-lg font-bold text-slate-700">运行变量（覆盖默认）</h3>
                <p class="mt-1 text-xs text-slate-500">仅本次执行生效；空白时使用默认变量</p>
              </div>
            </div>
            <textarea v-model="workflowDraft.runtime_variables" class="min-h-[80px] w-full border-none bg-slate-50 px-4 py-3 font-mono text-sm focus:ring-2 focus:ring-blue-500" placeholder="biz_date=2026-05-02"></textarea>
          </div>

          <div class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
            <div class="mb-5 flex items-center justify-between gap-3">
              <h3 class="text-lg font-bold text-slate-800">执行结果</h3>
              <div v-if="workflowAsyncJob" class="flex items-center gap-2">
                <span class="rounded-full bg-blue-100 px-2 py-1 text-[11px] font-bold text-blue-700">job: {{ workflowAsyncJob.job_id?.slice(0, 8) }}</span>
                <button v-if="workflowAsyncStatus && !['success', 'failed', 'cancelled'].includes(workflowAsyncStatus.status)" class="rounded-lg bg-red-600 px-3 py-1.5 text-xs font-bold text-white transition hover:bg-red-700" @click="cancelWorkflowAsync">取消</button>
              </div>
            </div>
            <div v-if="workflowAsyncStatus" class="mb-4 rounded-xl border p-4" :class="{
              'border-blue-200 bg-blue-50': !['success','failed','cancelled'].includes(workflowAsyncStatus.status),
              'border-emerald-200 bg-emerald-50': workflowAsyncStatus.status === 'success',
              'border-red-200 bg-red-50': workflowAsyncStatus.status === 'failed',
              'border-slate-200 bg-slate-50': workflowAsyncStatus.status === 'cancelled',
            }">
              <strong class="block text-sm">状态：{{ workflowAsyncStatus.status }}</strong>
              <span class="text-xs text-slate-600">{{ workflowAsyncStatus.message || '' }}</span>
              <p v-if="workflowAsyncStatus.error" class="mt-1 text-xs text-red-600">{{ workflowAsyncStatus.error }}</p>
            </div>
            <div v-if="lastRun" class="space-y-4">
              <div class="grid grid-cols-2 gap-3 xl:grid-cols-5">
                <div class="rounded-2xl bg-slate-50 p-4"><strong class="block text-xl text-slate-800">{{ lastRun.status }}</strong><span class="text-xs font-semibold text-slate-500">总状态</span></div>
                <div class="rounded-2xl bg-slate-50 p-4"><strong class="block text-xl text-slate-800">{{ lastRun.elapsed_seconds }}s</strong><span class="text-xs font-semibold text-slate-500">总耗时</span></div>
                <div class="rounded-2xl bg-emerald-50 p-4"><strong class="block text-xl text-emerald-700">{{ lastRun.nodes.filter(n => n.status === 'success').length }}</strong><span class="text-xs font-semibold text-emerald-600">success</span></div>
                <div class="rounded-2xl bg-red-50 p-4"><strong class="block text-xl text-red-700">{{ lastRun.nodes.filter(n => n.status === 'failed').length }}</strong><span class="text-xs font-semibold text-red-500">failed</span></div>
                <div class="rounded-2xl bg-slate-100 p-4"><strong class="block text-xl text-slate-600">{{ lastRun.nodes.filter(n => n.status === 'skipped').length }}</strong><span class="text-xs font-semibold text-slate-500">skipped</span></div>
              </div>
              <div class="space-y-2">
                <div v-for="node in lastRun.nodes" :key="node.node_id" class="rounded-xl border border-slate-200 p-4">
                  <div class="flex items-center justify-between gap-2">
                    <div class="flex items-center gap-2">
                      <span class="rounded-full px-2 py-0.5 text-[10px] font-bold" :class="nodeStatusClass(node.status)">{{ node.status }}</span>
                      <strong class="text-sm text-slate-700">{{ node.name || node.node_id }}</strong>
                      <span class="text-[11px] text-slate-400">{{ node.type }}</span>
                    </div>
                    <span class="text-[11px] text-slate-500">{{ node.elapsed_seconds }}s</span>
                  </div>
                  <p v-if="node.error" class="mt-2 text-xs text-red-600">{{ node.error }}</p>
                  <details v-if="node.output && Object.keys(node.output).length" class="mt-2">
                    <summary class="cursor-pointer text-[11px] text-slate-500 hover:text-slate-700">输出 ({{ Object.keys(node.output).length }} keys)</summary>
                    <pre class="mt-2 max-h-48 overflow-auto rounded-lg bg-slate-950 p-3 text-[11px] text-slate-100">{{ JSON.stringify(node.output, null, 2) }}</pre>
                  </details>
                </div>
              </div>
              <p v-if="lastRun.error" class="rounded-xl bg-red-50 p-3 text-sm text-red-700">{{ lastRun.error }}</p>
            </div>
            <div v-else class="rounded-2xl border border-dashed border-slate-200 p-8 text-center text-sm text-slate-400">暂无运行结果。保存后点击「同步执行」或「后台执行」。</div>
          </div>
        </div>
      </section>
    </div>
  </section>
</template>
