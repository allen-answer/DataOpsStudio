<script setup>
import { inject } from 'vue'

const {
  state,
  selectedHistory,
  selectedSheets,
  selectedHistoryTaskId,
  historyActiveTab,
  historyTaskOptions,
  filteredHistory,
  compareHistoryCount,
  lineageHistoryCount,
  loadHistory,
  exportHistory,
  deleteHistory,
  historyItemTaskLabel,
  summaryValue,
} = inject('app')
</script>

<template>
  <section class="space-y-6">
    <div class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
      <div class="mb-5 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h2 class="text-2xl font-bold text-slate-800">执行历史</h2>
          <p class="mt-1 text-sm text-slate-500">数据对比与血缘分析结果分开展示，支持多结果合并导出。</p>
        </div>
        <div class="flex flex-wrap gap-2">
          <button class="rounded-lg border border-slate-200 px-4 py-2 text-sm font-bold text-slate-700 transition hover:bg-slate-50" @click="loadHistory">刷新历史</button>
          <button class="rounded-lg bg-blue-600 px-5 py-2 text-sm font-bold text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50" :disabled="!selectedHistory.size" @click="exportHistory">导出所选历史</button>
        </div>
      </div>
      <div class="mb-5 flex flex-wrap gap-2">
        <button class="rounded-xl px-4 py-2 text-sm font-bold transition" :class="historyActiveTab === 'compare' ? 'bg-blue-600 text-white shadow-lg shadow-blue-100' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'" @click="historyActiveTab = 'compare'">数据对比 ({{ compareHistoryCount }})</button>
        <button class="rounded-xl px-4 py-2 text-sm font-bold transition" :class="historyActiveTab === 'lineage' ? 'bg-blue-600 text-white shadow-lg shadow-blue-100' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'" @click="historyActiveTab = 'lineage'">血缘分析 ({{ lineageHistoryCount }})</button>
      </div>
      <div v-if="historyActiveTab === 'compare'" class="mb-5 grid gap-4 xl:grid-cols-[320px_minmax(0,1fr)]">
        <label>
          <span class="mb-2 block text-xs font-bold uppercase tracking-wider text-slate-400">任务筛选</span>
          <select v-model="selectedHistoryTaskId" class="border-none bg-slate-50 px-4 py-3 focus:ring-2 focus:ring-blue-500">
            <option value="">全部历史</option>
            <option v-for="task in historyTaskOptions" :key="task.id" :value="task.id">{{ task.name }}</option>
          </select>
        </label>
        <div class="grid grid-cols-3 gap-3">
          <div class="rounded-2xl bg-slate-50 p-4"><strong class="block text-2xl text-slate-800">{{ historyTaskOptions.length }}</strong><span class="text-xs font-semibold text-slate-500">关联任务</span></div>
          <div class="rounded-2xl bg-slate-50 p-4"><strong class="block text-2xl text-slate-800">{{ filteredHistory.length }}</strong><span class="text-xs font-semibold text-slate-500">当前历史</span></div>
          <div class="rounded-2xl bg-slate-50 p-4"><strong class="block text-2xl text-slate-800">{{ selectedHistory.size }}</strong><span class="text-xs font-semibold text-slate-500">已选择</span></div>
        </div>
      </div>
      <div v-if="historyActiveTab === 'lineage'" class="mb-5 grid grid-cols-2 gap-3">
        <div class="rounded-2xl bg-slate-50 p-4"><strong class="block text-2xl text-slate-800">{{ lineageHistoryCount }}</strong><span class="text-xs font-semibold text-slate-500">血缘分析历史</span></div>
      </div>
      <div v-if="historyActiveTab === 'compare'" class="mb-4 flex flex-wrap gap-4"><label v-for="sheet in state.historySheets" :key="sheet" class="flex items-center gap-2 rounded-xl bg-slate-50 px-3 py-2 text-sm"><input class="w-auto" type="checkbox" :checked="selectedSheets.has(sheet)" @change="$event.target.checked ? selectedSheets.add(sheet) : selectedSheets.delete(sheet)">{{ sheet }}</label></div>

      <template v-if="historyActiveTab === 'compare'">
        <div class="grid grid-cols-[44px_1.2fr_1.3fr_repeat(4,90px)_160px] gap-2 rounded-t-2xl border border-b-0 border-slate-200 bg-slate-50 px-3 py-3 text-xs font-black uppercase tracking-wider text-slate-400">
          <span></span><span>运行 ID</span><span>任务</span><span>Diff</span><span>Same</span><span>源行数</span><span>目标行数</span><span>下载</span>
        </div>
      </template>
      <template v-if="historyActiveTab === 'lineage'">
        <div class="grid grid-cols-[1.2fr_1.3fr_90px_90px_repeat(4,90px)_160px] gap-2 rounded-t-2xl border border-b-0 border-slate-200 bg-slate-50 px-3 py-3 text-xs font-black uppercase tracking-wider text-slate-400">
          <span>运行 ID</span><span>时间</span><span>文件数</span><span>成功/失败</span><span>读表</span><span>写表</span><span>表边</span><span>脚本边</span><span>警告</span><span>下载</span>
        </div>
      </template>
      <div class="h-[620px] overflow-auto rounded-b-2xl border border-slate-200 bg-white">
        <div v-if="!filteredHistory.length" class="grid h-full place-items-center px-6 text-center text-sm text-slate-400">
          <div>
            <p class="text-slate-500">{{ historyActiveTab === 'compare' ? '还没有对比历史' : '还没有血缘分析历史' }}</p>
            <p class="mt-1 text-[12px] text-slate-400">
              {{ historyActiveTab === 'compare' ? '去「数据对比」执行一次任务，结果会落到这里。' : '去「血缘分析」上传脚本批量分析，结果会落到这里。' }}
            </p>
          </div>
        </div>
        <template v-if="historyActiveTab === 'compare'">
          <div v-for="(item, idx) in filteredHistory" :key="item.run_id || idx" class="grid w-full grid-cols-[44px_1.2fr_1.3fr_repeat(4,90px)_160px_40px] items-center gap-2 border-b border-slate-100 px-3 py-2 text-sm">
            <input class="w-auto" type="checkbox" :value="item.run_id" :checked="selectedHistory.has(item.run_id)" @change="$event.target.checked ? selectedHistory.add(item.run_id) : selectedHistory.delete(item.run_id)">
            <code>{{ item.run_id }}</code>
            <span>{{ historyItemTaskLabel(item) }}</span>
            <span>{{ summaryValue(item, 'diff') }}</span>
            <span>{{ summaryValue(item, 'same') }}</span>
            <span>{{ item.source_rows }}</span>
            <span>{{ item.target_rows }}</span>
            <span class="flex gap-2"><a class="font-semibold text-blue-600" v-if="item.excel_filename" :href="`/results/${item.excel_filename}`">Excel</a><a class="font-semibold text-blue-600" :href="`/results/${item.result_filename}`">JSON</a></span>
            <button class="text-slate-300 transition hover:text-red-500" title="删除" @click="deleteHistory(item.run_id)">✕</button>
          </div>
        </template>
        <template v-if="historyActiveTab === 'lineage'">
          <div v-for="(item, idx) in filteredHistory" :key="item.run_id || idx" class="grid w-full grid-cols-[1.2fr_1.3fr_90px_90px_repeat(4,90px)_160px_40px] items-center gap-2 border-b border-slate-100 px-3 py-2 text-sm">
            <code>{{ item.run_id }}</code>
            <span class="text-xs text-slate-500">{{ item.started_at }}</span>
            <span>{{ summaryValue(item, 'files') }}</span>
            <span>{{ summaryValue(item, 'success_files') }} / {{ summaryValue(item, 'failed_files') }}</span>
            <span>{{ summaryValue(item, 'read_tables') }}</span>
            <span>{{ summaryValue(item, 'write_tables') }}</span>
            <span>{{ summaryValue(item, 'table_edges') }}</span>
            <span>{{ summaryValue(item, 'script_edges') }}</span>
            <span>{{ summaryValue(item, 'warnings') }}</span>
            <span class="flex gap-2"><a class="font-semibold text-blue-600" v-if="item.excel_filename" :href="`/results/${item.excel_filename}`">Excel</a><a class="font-semibold text-blue-600" :href="`/results/${item.result_filename}`">JSON</a></span>
            <button class="text-slate-300 transition hover:text-red-500" title="删除" @click="deleteHistory(item.run_id)">✕</button>
          </div>
        </template>
      </div>
    </div>
  </section>
</template>
