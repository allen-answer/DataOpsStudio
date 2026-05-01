<script setup>
import { defineAsyncComponent, inject } from 'vue'

const SqlEditor = defineAsyncComponent(() => import('../components/SqlEditor.vue'))

const {
  state,
  taskDraft,
  selectedTaskId,
  currentTask,
  isSavedTask,
  sourcePreviewData,
  targetPreviewData,
  sourceFields,
  targetFields,
  compareResult,
  asyncJob,
  asyncStatus,
  previewOutput,
  actionStatus,
  compareBuckets,
  selectTask,
  saveTask,
  deleteTask,
  copyTask,
  runTask,
  runAsync,
  cancelAsync,
  previewTask,
  extractFields,
  recommendKey,
  formatSql,
  copyField,
  uploadExcel,
} = inject('app')
</script>

<template>
  <section class="space-y-6">
    <div class="grid grid-cols-[340px_minmax(0,1fr)] gap-6">
      <aside class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
        <div class="mb-6 flex items-end justify-between">
          <div>
            <h2 class="text-2xl font-bold text-slate-800">对比任务</h2>
            <p class="mt-1 text-sm text-slate-500">{{ state.tasks.length }} 个任务 · {{ state.datasources.length }} 个数据源</p>
          </div>
          <button class="rounded-lg bg-blue-600 px-4 py-2 text-sm font-bold text-white transition hover:bg-blue-700" @click="selectTask('new')">新建</button>
        </div>
        <div class="max-h-[calc(100vh-220px)] space-y-2 overflow-auto pr-1">
          <button
            v-for="task in state.tasks"
            :key="task.id"
            class="w-full rounded-xl border p-4 text-left transition"
            :class="selectedTaskId === task.id ? 'border-blue-300 bg-blue-50 shadow-sm' : 'border-transparent bg-slate-50 hover:border-slate-200 hover:bg-white'"
            @click="selectTask(task.id)"
          >
            <strong class="block text-slate-800">{{ task.name }}</strong>
            <span class="mt-1 block text-sm text-slate-500">{{ task.sql_mode === 'single' ? '单 SQL' : '双 SQL' }} · keys: {{ task.key_columns.join(', ') }}</span>
          </button>
          <p v-if="!state.tasks.length" class="rounded-xl border border-dashed border-slate-200 p-4 text-sm text-slate-400">暂无任务，点击新建开始。</p>
        </div>
      </aside>

      <section class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
        <div class="mb-6 flex items-start justify-between gap-4">
          <div>
            <h2 class="text-2xl font-bold text-slate-800">{{ currentTask?.name || '新增对比任务' }}</h2>
            <p class="mt-1 text-sm text-slate-500">智能 SQL 工作台 · 预览 / 异步执行 / 字段辅助</p>
          </div>
          <div class="flex flex-wrap justify-end gap-2">
            <button class="rounded-lg bg-blue-600 px-4 py-2 text-sm font-bold text-white transition hover:bg-blue-700" @click="saveTask">{{ isSavedTask ? '保存修改' : '保存任务' }}</button>
            <button class="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-bold text-white transition hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50" :disabled="!isSavedTask" @click="runTask">执行</button>
            <button class="rounded-lg border border-slate-200 px-4 py-2 text-sm font-bold text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50" :disabled="!isSavedTask" @click="runAsync">后台执行</button>
            <button class="rounded-lg border border-slate-200 px-4 py-2 text-sm font-bold text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50" :disabled="!isSavedTask" @click="copyTask">复制</button>
            <button class="rounded-lg bg-red-600 px-4 py-2 text-sm font-bold text-white transition hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-50" :disabled="!isSavedTask" @click="deleteTask">删除</button>
          </div>
        </div>

        <div class="grid gap-6">
          <div
            class="rounded-2xl border p-4"
            :class="{
              'border-slate-200 bg-slate-50 text-slate-700': actionStatus.type === 'idle' || actionStatus.type === 'ready',
              'border-blue-200 bg-blue-50 text-blue-800': actionStatus.type === 'running',
              'border-emerald-200 bg-emerald-50 text-emerald-800': actionStatus.type === 'success',
              'border-red-200 bg-red-50 text-red-800': actionStatus.type === 'error',
            }"
          >
            <div class="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p class="text-sm font-black">{{ actionStatus.title }}</p>
                <p v-if="actionStatus.message" class="mt-1 text-sm opacity-80">{{ actionStatus.message }}</p>
              </div>
              <span v-if="!isSavedTask" class="rounded-full bg-white/70 px-3 py-1 text-xs font-bold">请先保存任务</span>
            </div>
          </div>

          <div class="grid grid-cols-4 gap-4">
            <label><span class="mb-2 block text-xs font-bold uppercase tracking-wider text-slate-400">任务名称</span><input v-model="taskDraft.name" class="border-none bg-slate-50 px-4 py-3 focus:ring-2 focus:ring-blue-500"></label>
            <label><span class="mb-2 block text-xs font-bold uppercase tracking-wider text-slate-400">源数据源</span><select v-model="taskDraft.source_id" class="border-none bg-slate-50 px-4 py-3 focus:ring-2 focus:ring-blue-500"><option v-for="item in state.datasources" :key="item.id" :value="item.id">{{ item.name }}</option></select></label>
            <label><span class="mb-2 block text-xs font-bold uppercase tracking-wider text-slate-400">目标数据源</span><select v-model="taskDraft.target_id" class="border-none bg-slate-50 px-4 py-3 focus:ring-2 focus:ring-blue-500"><option v-for="item in state.datasources" :key="item.id" :value="item.id">{{ item.name }}</option></select></label>
            <label><span class="mb-2 block text-xs font-bold uppercase tracking-wider text-slate-400">SQL 模式</span><select v-model="taskDraft.sql_mode" class="border-none bg-slate-50 px-4 py-3 focus:ring-2 focus:ring-blue-500"><option value="single">单 SQL</option><option value="double">双 SQL</option></select></label>
          </div>

          <div class="grid grid-cols-1 gap-6 xl:grid-cols-2">
            <div class="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
              <div class="mb-4 flex items-center justify-between">
                <div class="flex items-center gap-2">
                  <span class="h-2 w-2 rounded-full bg-blue-500"></span>
                  <span class="text-sm font-bold uppercase tracking-wider text-slate-600">Source</span>
                  <div class="ml-2 inline-flex rounded-lg bg-slate-100 p-0.5 text-[11px] font-bold">
                    <button class="rounded-md px-2 py-1 transition" :class="taskDraft.source_kind === 'sql' ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-500 hover:text-slate-700'" @click="taskDraft.source_kind = 'sql'">SQL</button>
                    <button class="rounded-md px-2 py-1 transition" :class="taskDraft.source_kind === 'excel' ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-500 hover:text-slate-700'" @click="taskDraft.source_kind = 'excel'">Excel</button>
                  </div>
                </div>
                <div v-if="taskDraft.source_kind === 'sql'" class="flex gap-2"><button class="rounded-lg bg-slate-700/90 px-3 py-1.5 text-[11px] font-bold text-white transition hover:bg-blue-600" @click="formatSql('source')">格式化</button><button class="rounded-lg bg-slate-700/90 px-3 py-1.5 text-[11px] font-bold text-white transition hover:bg-blue-600" @click="extractFields('source')">提取字段</button><button class="rounded-lg bg-emerald-600 px-3 py-1.5 text-[11px] font-bold text-white transition hover:bg-emerald-700 disabled:opacity-40" :disabled="!isSavedTask" @click="previewTask('source')">预览</button></div>
              </div>
              <template v-if="taskDraft.source_kind === 'sql'">
                <SqlEditor v-model="taskDraft.source_sql" />
                <div v-if="sourceFields.length" class="mt-3 flex flex-wrap gap-1.5">
                  <span
                    v-for="col in sourceFields" :key="col"
                    class="cursor-pointer select-all rounded-full bg-slate-700 px-2.5 py-1 text-[11px] font-mono text-slate-200 transition hover:bg-blue-600"
                    :title="'点击复制：' + col"
                    @click="copyField(col)"
                  >{{ col }}</span>
                </div>
                <div v-if="sourcePreviewData" class="mt-3">
                  <p v-if="sourcePreviewData.loading" class="text-xs text-slate-400">预览中...</p>
                  <p v-else-if="sourcePreviewData.error" class="rounded-lg bg-red-50 p-2 text-xs text-red-600">{{ sourcePreviewData.error }}</p>
                  <template v-else>
                    <p class="mb-2 text-xs text-slate-400">前 {{ sourcePreviewData.rows?.length ?? 0 }} 行</p>
                    <div class="overflow-x-auto rounded-xl border border-slate-200">
                      <table class="w-full text-xs">
                        <thead><tr class="border-b border-slate-200 bg-slate-50"><th v-for="col in Object.keys(sourcePreviewData.rows?.[0] ?? {})" :key="col" class="px-3 py-2 text-left font-bold text-slate-600">{{ col }}</th></tr></thead>
                        <tbody><tr v-for="(row, i) in sourcePreviewData.rows" :key="i" class="border-b border-slate-100 last:border-0 hover:bg-slate-50"><td v-for="col in Object.keys(sourcePreviewData.rows[0])" :key="col" class="px-3 py-2 text-slate-700">{{ row[col] ?? '' }}</td></tr></tbody>
                      </table>
                    </div>
                  </template>
                </div>
              </template>
              <template v-else>
                <div class="rounded-xl border border-dashed border-slate-300 bg-slate-50 p-4 text-sm text-slate-600">
                  <input type="file" accept=".xlsx,.xlsm" class="block w-full text-xs" @change="uploadExcel('source', $event.target.files[0])">
                  <p v-if="taskDraft.source_excel_filename" class="mt-2 text-xs text-slate-500">已上传：<strong>{{ taskDraft.source_excel_filename }}</strong></p>
                  <p v-else class="mt-2 text-xs text-slate-400">支持 .xlsx / .xlsm；上传后自动列出 sheet。</p>
                </div>
                <div class="mt-3 grid grid-cols-2 gap-3">
                  <label>
                    <span class="mb-2 block text-xs font-bold uppercase tracking-wider text-slate-400">Sheet</span>
                    <select v-model="taskDraft.source_sheet" class="border-none bg-slate-50">
                      <option value="">默认（第一个）</option>
                      <option v-for="sheet in taskDraft.source_excel_sheets" :key="sheet" :value="sheet">{{ sheet }}</option>
                    </select>
                  </label>
                  <label>
                    <span class="mb-2 block text-xs font-bold uppercase tracking-wider text-slate-400">表头行</span>
                    <input v-model="taskDraft.source_header_row" type="number" min="1" class="border-none bg-slate-50">
                  </label>
                </div>
              </template>
            </div>
            <div class="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
              <div class="mb-4 flex items-center justify-between">
                <div class="flex items-center gap-2">
                  <span class="h-2 w-2 rounded-full bg-orange-500"></span>
                  <span class="text-sm font-bold uppercase tracking-wider text-slate-600">Target</span>
                  <div class="ml-2 inline-flex rounded-lg bg-slate-100 p-0.5 text-[11px] font-bold">
                    <button class="rounded-md px-2 py-1 transition" :class="taskDraft.target_kind === 'sql' ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-500 hover:text-slate-700'" @click="taskDraft.target_kind = 'sql'">SQL</button>
                    <button class="rounded-md px-2 py-1 transition" :class="taskDraft.target_kind === 'excel' ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-500 hover:text-slate-700'" @click="taskDraft.target_kind = 'excel'">Excel</button>
                  </div>
                </div>
                <div v-if="taskDraft.target_kind === 'sql'" class="flex gap-2"><button class="rounded-lg bg-slate-700/90 px-3 py-1.5 text-[11px] font-bold text-white transition hover:bg-blue-600" @click="formatSql('target')">格式化</button><button class="rounded-lg bg-slate-700/90 px-3 py-1.5 text-[11px] font-bold text-white transition hover:bg-blue-600" @click="extractFields('target')">提取字段</button><button class="rounded-lg bg-emerald-600 px-3 py-1.5 text-[11px] font-bold text-white transition hover:bg-emerald-700 disabled:opacity-40" :disabled="!isSavedTask" @click="previewTask('target')">预览</button></div>
              </div>
              <template v-if="taskDraft.target_kind === 'sql'">
                <SqlEditor v-model="taskDraft.target_sql" placeholder="双 SQL 模式填写" />
                <div v-if="targetFields.length" class="mt-3 flex flex-wrap gap-1.5">
                  <span
                    v-for="col in targetFields" :key="col"
                    class="cursor-pointer select-all rounded-full bg-slate-700 px-2.5 py-1 text-[11px] font-mono text-slate-200 transition hover:bg-blue-600"
                    :title="'点击复制：' + col"
                    @click="copyField(col)"
                  >{{ col }}</span>
                </div>
                <div v-if="targetPreviewData" class="mt-3">
                  <p v-if="targetPreviewData.loading" class="text-xs text-slate-400">预览中...</p>
                  <p v-else-if="targetPreviewData.error" class="rounded-lg bg-red-50 p-2 text-xs text-red-600">{{ targetPreviewData.error }}</p>
                  <template v-else>
                    <p class="mb-2 text-xs text-slate-400">前 {{ targetPreviewData.rows?.length ?? 0 }} 行</p>
                    <div class="overflow-x-auto rounded-xl border border-slate-200">
                      <table class="w-full text-xs">
                        <thead><tr class="border-b border-slate-200 bg-slate-50"><th v-for="col in Object.keys(targetPreviewData.rows?.[0] ?? {})" :key="col" class="px-3 py-2 text-left font-bold text-slate-600">{{ col }}</th></tr></thead>
                        <tbody><tr v-for="(row, i) in targetPreviewData.rows" :key="i" class="border-b border-slate-100 last:border-0 hover:bg-slate-50"><td v-for="col in Object.keys(targetPreviewData.rows[0])" :key="col" class="px-3 py-2 text-slate-700">{{ row[col] ?? '' }}</td></tr></tbody>
                      </table>
                    </div>
                  </template>
                </div>
              </template>
              <template v-else>
                <div class="rounded-xl border border-dashed border-slate-300 bg-slate-50 p-4 text-sm text-slate-600">
                  <input type="file" accept=".xlsx,.xlsm" class="block w-full text-xs" @change="uploadExcel('target', $event.target.files[0])">
                  <p v-if="taskDraft.target_excel_filename" class="mt-2 text-xs text-slate-500">已上传：<strong>{{ taskDraft.target_excel_filename }}</strong></p>
                  <p v-else class="mt-2 text-xs text-slate-400">支持 .xlsx / .xlsm；上传后自动列出 sheet。</p>
                </div>
                <div class="mt-3 grid grid-cols-2 gap-3">
                  <label>
                    <span class="mb-2 block text-xs font-bold uppercase tracking-wider text-slate-400">Sheet</span>
                    <select v-model="taskDraft.target_sheet" class="border-none bg-slate-50">
                      <option value="">默认（第一个）</option>
                      <option v-for="sheet in taskDraft.target_excel_sheets" :key="sheet" :value="sheet">{{ sheet }}</option>
                    </select>
                  </label>
                  <label>
                    <span class="mb-2 block text-xs font-bold uppercase tracking-wider text-slate-400">表头行</span>
                    <input v-model="taskDraft.target_header_row" type="number" min="1" class="border-none bg-slate-50">
                  </label>
                </div>
              </template>
            </div>
          </div>

          <div class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
            <div class="mb-6 flex items-center gap-3">
              <span class="grid h-9 w-9 place-items-center rounded-lg bg-slate-100 text-xs font-black text-slate-400">R</span>
              <h3 class="text-lg font-bold text-slate-700">对比规则设定</h3>
            </div>
            <div class="grid grid-cols-1 gap-6 xl:grid-cols-4">
              <div>
                <label class="block text-xs font-bold uppercase tracking-wider text-slate-400">主键列</label>
                <div class="mt-3 flex gap-2">
                  <input v-model="taskDraft.key_columns" class="flex-1 border-none bg-slate-50 px-4 py-3 focus:ring-2 focus:ring-blue-500" placeholder="ID, ORDER_NO">
                  <button class="shrink-0 rounded-lg bg-blue-600 px-3 py-2 text-xs font-bold text-white transition hover:bg-blue-700" @click="recommendKey">自动推荐</button>
                </div>
                <label class="mt-4 block text-xs font-bold uppercase tracking-wider text-slate-400">忽略字段</label>
                <input v-model="taskDraft.ignore_columns" class="mt-3 border-none bg-slate-50 px-4 py-3 focus:ring-2 focus:ring-blue-500" placeholder="etl_time">
              </div>
              <div class="grid grid-cols-2 gap-4 rounded-2xl bg-slate-50 p-4 xl:col-span-2">
                <label class="flex cursor-pointer items-center rounded-xl border border-transparent bg-white p-3 shadow-sm transition hover:border-blue-200"><input v-model="taskDraft.trim_strings" class="mr-3 h-4 w-4 rounded text-blue-600" type="checkbox"><span class="text-sm font-medium text-slate-600">字符串去空格</span></label>
                <label class="flex cursor-pointer items-center rounded-xl border border-transparent bg-white p-3 shadow-sm transition hover:border-blue-200"><input v-model="taskDraft.case_insensitive" class="mr-3 h-4 w-4 rounded text-blue-600" type="checkbox"><span class="text-sm font-medium text-slate-600">忽略大小写</span></label>
                <label class="flex cursor-pointer items-center rounded-xl border border-transparent bg-white p-3 shadow-sm transition hover:border-blue-200"><input v-model="taskDraft.empty_as_null" class="mr-3 h-4 w-4 rounded text-blue-600" type="checkbox"><span class="text-sm font-medium text-slate-600">空字符串视为空值</span></label>
                <label class="flex cursor-pointer items-center rounded-xl border border-transparent bg-white p-3 shadow-sm transition hover:border-blue-200"><input v-model="taskDraft.stream_compare" class="mr-3 h-4 w-4 rounded text-blue-600" type="checkbox"><span class="text-sm font-medium text-slate-600">流式分块对比</span></label>
              </div>
              <div class="grid gap-3">
                <button class="rounded-2xl bg-blue-600 py-4 font-bold text-white shadow-lg shadow-blue-200 transition hover:-translate-y-0.5 hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50" @click="runTask" :disabled="!isSavedTask">开始执行对比</button>
                <button class="rounded-2xl border border-slate-200 py-3 text-sm font-bold text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50" @click="copyTask" :disabled="!isSavedTask">复制当前任务</button>
                <button class="rounded-2xl border border-slate-200 py-3 text-sm font-bold text-slate-700 transition hover:bg-slate-50" @click="saveTask">{{ selectedTaskId === 'new' ? '保存任务' : '保存修改' }}</button>
              </div>
            </div>
            <div class="mt-6 grid grid-cols-4 gap-3">
              <label><span class="mb-2 block text-xs font-bold text-slate-400">数值容忍</span><input v-model="taskDraft.numeric_tolerance" type="number" step="any" class="border-none bg-slate-50"></label>
              <label><span class="mb-2 block text-xs font-bold text-slate-400">最大行数</span><input v-model="taskDraft.max_rows" type="number" class="border-none bg-slate-50"></label>
              <label><span class="mb-2 block text-xs font-bold text-slate-400">导出行数</span><input v-model="taskDraft.export_max_rows" type="number" class="border-none bg-slate-50"></label>
              <label><span class="mb-2 block text-xs font-bold text-slate-400">分块行数</span><input v-model="taskDraft.fetch_chunk_size" type="number" class="border-none bg-slate-50"></label>
            </div>
            <label class="mt-5 block"><span class="mb-2 block text-xs font-bold uppercase tracking-wider text-slate-400">字段映射</span><textarea v-model="taskDraft.column_mappings" class="min-h-[90px] border-none bg-slate-50 font-mono text-sm" placeholder="source_col -> target_col"></textarea></label>
          </div>

          <div class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
            <div class="mb-5 flex flex-wrap items-end justify-between gap-3">
              <div>
                <h3 class="text-lg font-bold text-slate-800">对比结果预览</h3>
                <p class="mt-1 text-sm text-slate-500">执行完成后展示汇总、样例明细和下载入口。</p>
              </div>
              <div v-if="compareResult" class="flex gap-2">
                <a class="rounded-lg border border-slate-200 px-4 py-2 text-sm font-bold text-slate-700 transition hover:bg-slate-50" :href="`/results/${compareResult.excel_filename}`">下载 Excel</a>
                <a class="rounded-lg border border-slate-200 px-4 py-2 text-sm font-bold text-slate-700 transition hover:bg-slate-50" :href="`/results/${compareResult.result_filename}`">下载 JSON</a>
              </div>
            </div>
            <div v-if="compareResult" class="space-y-5">
              <div class="grid grid-cols-2 gap-3 xl:grid-cols-6">
                <div class="rounded-2xl bg-slate-50 p-4"><strong class="block text-2xl text-slate-800">{{ compareResult.summary.only_source }}</strong><span class="text-xs font-semibold text-slate-500">only_source</span></div>
                <div class="rounded-2xl bg-slate-50 p-4"><strong class="block text-2xl text-slate-800">{{ compareResult.summary.only_target }}</strong><span class="text-xs font-semibold text-slate-500">only_target</span></div>
                <div class="rounded-2xl bg-red-50 p-4"><strong class="block text-2xl text-red-700">{{ compareResult.summary.diff }}</strong><span class="text-xs font-semibold text-red-500">diff</span></div>
                <div class="rounded-2xl bg-emerald-50 p-4"><strong class="block text-2xl text-emerald-700">{{ compareResult.summary.same }}</strong><span class="text-xs font-semibold text-emerald-600">same</span></div>
                <div class="rounded-2xl bg-slate-50 p-4"><strong class="block text-2xl text-slate-800">{{ compareResult.source_rows }}</strong><span class="text-xs font-semibold text-slate-500">源行数</span></div>
                <div class="rounded-2xl bg-slate-50 p-4"><strong class="block text-2xl text-slate-800">{{ compareResult.target_rows }}</strong><span class="text-xs font-semibold text-slate-500">目标行数</span></div>
              </div>
              <div class="grid gap-4 xl:grid-cols-2">
                <div v-for="bucket in compareBuckets" :key="bucket.id" class="rounded-2xl border border-slate-200 p-4">
                  <div class="mb-3 flex items-center justify-between"><h4 class="font-bold text-slate-700">{{ bucket.label }}</h4><span class="rounded-full bg-slate-100 px-2 py-1 text-xs font-bold text-slate-500">样例 {{ compareResult.samples[bucket.id]?.length || 0 }}</span></div>
                  <pre class="max-h-56 overflow-auto rounded-xl bg-slate-950 p-3 text-xs text-slate-100">{{ JSON.stringify(compareResult.samples[bucket.id] || [], null, 2) }}</pre>
                </div>
              </div>
            </div>
            <pre v-else-if="previewOutput || asyncStatus" class="max-h-[420px] resize-y overflow-auto rounded-2xl bg-slate-950 p-5 text-xs text-slate-100">{{ asyncStatus ? JSON.stringify(asyncStatus, null, 2) : previewOutput }}</pre>
            <div v-else class=”rounded-2xl border border-dashed border-slate-200 p-8 text-center text-sm text-slate-400”>暂无结果。保存任务后点击”开始执行对比”。</div>
          </div>
          <button v-if="asyncJob" class="w-fit rounded-lg bg-red-600 px-4 py-2 text-sm font-bold text-white transition hover:bg-red-700" @click="cancelAsync">取消后台任务</button>
        </div>
      </section>
    </div>
  </section>
</template>
