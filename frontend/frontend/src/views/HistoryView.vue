<script setup>
import { storeToRefs } from 'pinia'
import { useBootstrapStore } from '../stores/bootstrap'
import { useHistoryStore } from '../stores/history'
import { useNoticeStore } from '../stores/notice'

const { state } = useBootstrapStore()

const historyStore = useHistoryStore()
const {
  selectedHistory, selectedSheets, selectedHistoryTaskId, historyActiveTab,
  historyTaskOptions, filteredHistory, compareHistoryCount, lineageHistoryCount,
} = storeToRefs(historyStore)
const { loadHistory, exportHistory, deleteHistory } = historyStore

const { historyItemTaskLabel, summaryValue } = useNoticeStore()
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

      <div v-if="historyActiveTab === 'compare'" class="mb-4 flex flex-wrap gap-4">
        <label v-for="sheet in state.historySheets" :key="sheet" class="flex items-center gap-2 rounded-xl bg-slate-50 px-3 py-2 text-sm">
          <input class="w-auto" type="checkbox" :checked="selectedSheets.has(sheet)" @change="$event.target.checked ? selectedSheets.add(sheet) : selectedSheets.delete(sheet)">
          {{ sheet }}
        </label>
      </div>

      <div class="overflow-hidden rounded-2xl border border-slate-200 bg-white">
        <div class="h-[620px] overflow-auto">
          <div v-if="!filteredHistory.length" class="grid h-full place-items-center px-6 text-center text-sm text-slate-400">
            <div>
              <p class="text-slate-500">{{ historyActiveTab === 'compare' ? '还没有对比历史' : '还没有血缘分析历史' }}</p>
              <p class="mt-1 text-[12px] text-slate-400">
                {{ historyActiveTab === 'compare' ? '去「数据对比」执行一次任务，结果会落到这里。' : '去「血缘分析」上传脚本批量分析，结果会落到这里。' }}
              </p>
            </div>
          </div>

          <table v-else-if="historyActiveTab === 'compare'" class="w-full min-w-[1180px] table-fixed border-collapse text-left text-sm text-slate-700">
            <colgroup>
              <col style="width: 44px;">
              <col style="width: 260px;">
              <col>
              <col style="width: 88px;">
              <col style="width: 88px;">
              <col style="width: 100px;">
              <col style="width: 100px;">
              <col style="width: 132px;">
              <col style="width: 44px;">
            </colgroup>
            <thead class="sticky top-0 z-10 bg-slate-50 text-xs font-black uppercase tracking-wider text-slate-400">
              <tr>
                <th class="px-3 py-3"></th>
                <th class="whitespace-nowrap px-3 py-3 text-left">运行 ID</th>
                <th class="whitespace-nowrap px-3 py-3 text-left">任务</th>
                <th class="whitespace-nowrap px-3 py-3 text-right tabular-nums">Diff</th>
                <th class="whitespace-nowrap px-3 py-3 text-right tabular-nums">Same</th>
                <th class="whitespace-nowrap px-3 py-3 text-right tabular-nums">源行数</th>
                <th class="whitespace-nowrap px-3 py-3 text-right tabular-nums">目标行数</th>
                <th class="whitespace-nowrap px-3 py-3 text-left">下载</th>
                <th class="px-3 py-3"></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(item, idx) in filteredHistory" :key="item.run_id || idx" class="border-t border-slate-100 transition hover:bg-slate-50">
                <td class="px-3 py-3 align-top">
                  <input class="w-auto" type="checkbox" :value="item.run_id" :checked="selectedHistory.has(item.run_id)" @change="$event.target.checked ? selectedHistory.add(item.run_id) : selectedHistory.delete(item.run_id)">
                </td>
                <td class="px-3 py-3 align-top"><code class="block truncate text-[12px] leading-5 text-slate-700" :title="item.run_id">{{ item.run_id }}</code></td>
                <td class="px-3 py-3 align-top"><span class="line-clamp-2">{{ historyItemTaskLabel(item) }}</span></td>
                <td class="px-3 py-3 text-right align-top font-mono tabular-nums">{{ summaryValue(item, 'diff') }}</td>
                <td class="px-3 py-3 text-right align-top font-mono tabular-nums">{{ summaryValue(item, 'same') }}</td>
                <td class="px-3 py-3 text-right align-top font-mono tabular-nums">{{ item.source_rows }}</td>
                <td class="px-3 py-3 text-right align-top font-mono tabular-nums">{{ item.target_rows }}</td>
                <td class="px-3 py-3 align-top">
                  <span class="flex flex-wrap gap-2">
                    <a v-if="item.excel_filename" class="font-semibold text-blue-600" :href="`/results/${item.excel_filename}`">Excel</a>
                    <a class="font-semibold text-blue-600" :href="`/results/${item.result_filename}`">JSON</a>
                  </span>
                </td>
                <td class="px-3 py-3 text-center align-top">
                  <button class="text-slate-300 transition hover:text-red-500" title="删除" @click="deleteHistory(item.run_id)">✕</button>
                </td>
              </tr>
            </tbody>
          </table>

          <table v-else class="w-full min-w-[1220px] table-fixed border-collapse text-left text-sm text-slate-700">
            <colgroup>
              <col style="width: 240px;">
              <col style="width: 180px;">
              <col style="width: 86px;">
              <col style="width: 108px;">
              <col style="width: 86px;">
              <col style="width: 86px;">
              <col style="width: 86px;">
              <col style="width: 98px;">
              <col style="width: 86px;">
              <col style="width: 132px;">
              <col style="width: 44px;">
            </colgroup>
            <thead class="sticky top-0 z-10 bg-slate-50 text-xs font-black uppercase tracking-wider text-slate-400">
              <tr>
                <th class="whitespace-nowrap px-3 py-3 text-left">运行 ID</th>
                <th class="whitespace-nowrap px-3 py-3 text-left">时间</th>
                <th class="whitespace-nowrap px-3 py-3 text-right tabular-nums">文件数</th>
                <th class="whitespace-nowrap px-3 py-3 text-right tabular-nums">成功/失败</th>
                <th class="whitespace-nowrap px-3 py-3 text-right tabular-nums">读表</th>
                <th class="whitespace-nowrap px-3 py-3 text-right tabular-nums">写表</th>
                <th class="whitespace-nowrap px-3 py-3 text-right tabular-nums">表边</th>
                <th class="whitespace-nowrap px-3 py-3 text-right tabular-nums">脚本边</th>
                <th class="whitespace-nowrap px-3 py-3 text-right tabular-nums">警告</th>
                <th class="whitespace-nowrap px-3 py-3 text-left">下载</th>
                <th class="px-3 py-3"></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(item, idx) in filteredHistory" :key="item.run_id || idx" class="border-t border-slate-100 transition hover:bg-slate-50">
                <td class="px-3 py-3 align-top"><code class="block truncate text-[12px] leading-5 text-slate-700" :title="item.run_id">{{ item.run_id }}</code></td>
                <td class="px-3 py-3 align-top text-xs text-slate-500">{{ item.started_at }}</td>
                <td class="px-3 py-3 text-right align-top font-mono tabular-nums">{{ summaryValue(item, 'files') }}</td>
                <td class="px-3 py-3 text-right align-top font-mono tabular-nums">{{ summaryValue(item, 'success_files') }} / {{ summaryValue(item, 'failed_files') }}</td>
                <td class="px-3 py-3 text-right align-top font-mono tabular-nums">{{ summaryValue(item, 'read_tables') }}</td>
                <td class="px-3 py-3 text-right align-top font-mono tabular-nums">{{ summaryValue(item, 'write_tables') }}</td>
                <td class="px-3 py-3 text-right align-top font-mono tabular-nums">{{ summaryValue(item, 'table_edges') }}</td>
                <td class="px-3 py-3 text-right align-top font-mono tabular-nums">{{ summaryValue(item, 'script_edges') }}</td>
                <td class="px-3 py-3 text-right align-top font-mono tabular-nums">{{ summaryValue(item, 'warnings') }}</td>
                <td class="px-3 py-3 align-top">
                  <span class="flex flex-wrap gap-2">
                    <a v-if="item.excel_filename" class="font-semibold text-blue-600" :href="`/results/${item.excel_filename}`">Excel</a>
                    <a class="font-semibold text-blue-600" :href="`/results/${item.result_filename}`">JSON</a>
                  </span>
                </td>
                <td class="px-3 py-3 text-center align-top">
                  <button class="text-slate-300 transition hover:text-red-500" title="删除" @click="deleteHistory(item.run_id)">✕</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </section>
</template>
