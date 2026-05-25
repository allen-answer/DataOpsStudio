<script setup lang="ts">
/**
 * SQL Workbench v0.1 主视图(Phase 2)。
 *
 * 三栏布局(简化版,Phase 3 加 metadata tree 在最左侧):
 *   - 顶部: tab list(可关闭/新建)+ datasource picker + Run + Save 按钮
 *   - 中间: SqlEditor(复用)
 *   - 下面: 标签页切 Result / History
 *
 * 持久化: 用户每改 SQL / 切 datasource → debounced PUT 到后端 console。
 *         刷新页面 → loadConsoles() 恢复全部 tab + 上次激活的 tab(本地存)。
 */
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { Play, Plus, X, Save, ChevronDown, ChevronRight, Database, History as HistoryIcon, Table2, FolderTree, RefreshCw, Send, GitBranch, GitCompareArrows, Microscope } from 'lucide-vue-next'
import { useSqlWorkbenchStore } from '../stores/sqlWorkbench'
import { useBootstrapStore } from '../stores/bootstrap'
import { useNoticeStore } from '../stores/notice'
import SqlEditor from '../components/SqlEditor.vue'
import { setSqlTransfer } from '../utils/sqlTransfer'

const router = useRouter()

const store = useSqlWorkbenchStore()
const bootstrap = useBootstrapStore()
const notice = useNoticeStore()
const { consoles, activeConsole, activeConsoleId, results, running, history, metadataByDs } = storeToRefs(store)
const { state: bootstrapState } = bootstrap

const ACTIVE_ID_KEY = 'dataops.sqlWorkbench.activeId'
const bottomTab = ref<'result' | 'history' | 'metadata'>('result')
const maxRows = ref(1000)

// metadata:切 datasource 自动拉一次
watch(() => activeConsole.value?.datasource_id, (dsId) => {
  if (!dsId) return
  // 没缓存才拉(避免每次切 tab 都重打)
  if (!metadataByDs.value[dsId]) {
    store.loadSchemas(dsId).catch(() => {})
  }
}, { immediate: true })

function refreshMetadata() {
  const dsId = activeConsole.value?.datasource_id
  if (!dsId) return
  // 清缓存重拉
  delete metadataByDs.value[dsId]
  store.loadSchemas(dsId).catch(() => {})
}

function onTableClick(schema: string, table: string) {
  const c = activeConsole.value
  if (!c) return
  const qualified = schema ? `${schema}.${table}` : table
  const snippet = `SELECT *\nFROM ${qualified}\nLIMIT 100;`
  // 简化:追加到当前 SQL 后面(用户可见上下文,且不破坏现有草稿)
  c.sql = c.sql ? `${c.sql}\n\n${snippet}` : snippet
  bottomTab.value = 'result'
  nextTick(() => _scheduleSave())
}

const currentMeta = computed(() => {
  const dsId = activeConsole.value?.datasource_id || ''
  return dsId ? metadataByDs.value[dsId] : null
})

// ─── lifecycle ─────────────────────────────────────────────────────────────

onMounted(async () => {
  await store.loadConsoles()
  // 恢复上次激活的 tab
  const saved = localStorage.getItem(ACTIVE_ID_KEY) || ''
  if (saved && consoles.value.some(c => c.id === saved)) {
    store.setActive(saved)
  }
  if (!consoles.value.length) {
    await store.createConsole()
  }
  store.loadHistory().catch(() => {})
})

watch(activeConsoleId, (id) => {
  if (id) localStorage.setItem(ACTIVE_ID_KEY, id)
})

// ─── debounced save ─────────────────────────────────────────────────────────

let saveTimer: ReturnType<typeof setTimeout> | null = null
let saveSql = ''
let saveDs = ''

function _scheduleSave() {
  const c = activeConsole.value
  if (!c) return
  if (c.sql === saveSql && c.datasource_id === saveDs) return
  saveSql = c.sql
  saveDs = c.datasource_id
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(() => {
    if (!c) return
    store.updateConsole(c.id, { sql: c.sql, datasource_id: c.datasource_id }).catch(() => {
      // 静默失败 —— 用户下次操作再试
    })
  }, 800)
}

// 监听 sql 变化触发 save
watch(
  () => activeConsole.value?.sql,
  () => _scheduleSave(),
)
watch(
  () => activeConsole.value?.datasource_id,
  () => _scheduleSave(),
)

// ─── tabs ───────────────────────────────────────────────────────────────────

async function onAddTab() {
  // 新 tab 默认继承当前 ds(用户用同一个 ds 写多条 SQL 是常用场景)
  await store.createConsole({ datasource_id: activeConsole.value?.datasource_id || '' })
}

async function onCloseTab(id: string, evt: Event) {
  evt.stopPropagation()
  if (consoles.value.length === 1) {
    // 关最后一个时自动新建一个,保持至少一个 tab
    await store.createConsole()
  }
  await store.deleteConsole(id)
}

async function onRenameTab(id: string) {
  const c = consoles.value.find(x => x.id === id)
  if (!c) return
  const newName = prompt('Tab 名称', c.name)
  if (newName && newName.trim() && newName.trim() !== c.name) {
    await store.updateConsole(id, { name: newName.trim() })
  }
}

// ─── execute ────────────────────────────────────────────────────────────────

async function onRun() {
  const c = activeConsole.value
  if (!c) return
  if (!c.datasource_id) {
    notice.setNotice('请先选择数据源')
    return
  }
  if (!c.sql.trim()) {
    notice.setNotice('SQL 为空')
    return
  }
  try {
    await store.execute(c.id, {
      datasource_id: c.datasource_id,
      sql: c.sql,
      max_rows: maxRows.value,
    })
    bottomTab.value = 'result'
  } catch (e: unknown) {
    notice.setNotice('执行出错: ' + ((e as Error)?.message || String(e)))
  }
}

async function onSave() {
  const c = activeConsole.value
  if (!c) return
  await store.updateConsole(c.id, { sql: c.sql, datasource_id: c.datasource_id })
  notice.setNotice('已保存')
}

// Ctrl/Cmd+Enter Run 快捷键
function onKeydown(e: KeyboardEvent) {
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
    e.preventDefault()
    onRun()
  }
}

onMounted(() => {
  document.addEventListener('keydown', onKeydown)
})

// 切 tab 重置 bottomTab 到 result(每个 tab 独立 result)
watch(activeConsoleId, () => {
  bottomTab.value = 'result'
})

// ─── computed ───────────────────────────────────────────────────────────────

const currentResult = computed(() => (activeConsole.value ? results.value[activeConsole.value.id] : null))
const isRunning = computed(() => (activeConsole.value ? !!running.value[activeConsole.value.id] : false))

const datasources = computed<any[]>(() => bootstrapState.datasources as any[] || [])

// ─── 历史一行点击 → 复制 SQL 到当前 console ─────────────────────────────
function loadHistoryEntry(entry: any) {
  const c = activeConsole.value
  if (!c) return
  c.sql = entry.sql
  if (entry.datasource_id && datasources.value.some(d => d.id === entry.datasource_id)) {
    c.datasource_id = entry.datasource_id
  }
  bottomTab.value = 'result'
  nextTick(() => _scheduleSave())
}

function formatElapsed(ms: number): string {
  if (ms < 1000) return `${ms} ms`
  return `${(ms / 1000).toFixed(2)} s`
}

// ─── Phase 4:发送当前 SQL 到血缘 / 对比 / 诊断 ─────────────────────────

const showSendMenu = ref(false)

function sendTo(target: 'lineage' | 'compare' | 'diagnosis', sql?: string, dsId?: string) {
  const finalSql = (sql ?? activeConsole.value?.sql ?? '').trim()
  if (!finalSql) {
    notice.setNotice('SQL 为空,无法发送')
    return
  }
  const finalDs = dsId ?? activeConsole.value?.datasource_id ?? ''
  setSqlTransfer({ sql: finalSql, datasourceId: finalDs, source: 'sql-workbench' })
  showSendMenu.value = false
  const path = {
    lineage: '/lineage',
    compare: '/data-compare',
    diagnosis: '/sql-diagnosis',
  }[target]
  router.push(path)
}

function sendHistoryEntry(entry: any, target: 'lineage' | 'compare' | 'diagnosis', evt: Event) {
  evt.stopPropagation()  // 不要触发行的 "load 到当前 console"
  sendTo(target, entry.sql, entry.datasource_id)
}
</script>

<template>
  <section class="flex h-[calc(100vh-120px)] flex-col gap-3">
    <!-- 顶部 tab 条 + 工具 -->
    <div class="flex items-center gap-2 border-b border-slate-200 bg-white px-3 py-2 rounded-t-xl">
      <!-- Tab list -->
      <div class="flex flex-1 items-center gap-1 overflow-x-auto">
        <div
          v-for="c in consoles"
          :key="c.id"
          class="group flex items-center gap-1.5 rounded-t-md border border-b-0 px-2.5 py-1.5 text-xs cursor-pointer transition shrink-0"
          :class="c.id === activeConsoleId
            ? 'border-primary bg-primary-light text-primary font-semibold'
            : 'border-slate-200 bg-slate-50 text-slate-600 hover:bg-slate-100'"
          @click="store.setActive(c.id)"
          @dblclick="onRenameTab(c.id)"
        >
          <span>{{ c.name }}</span>
          <button
            class="opacity-0 group-hover:opacity-100 transition hover:bg-status-error-bg hover:text-status-error rounded p-0.5"
            :title="'关闭 ' + c.name"
            @click="onCloseTab(c.id, $event)"
          >
            <X class="h-3 w-3" />
          </button>
        </div>
        <button
          class="rounded-md border border-slate-200 bg-white px-2 py-1.5 text-xs text-slate-500 hover:bg-slate-50 hover:text-slate-800 shrink-0"
          title="新 Console"
          @click="onAddTab"
        >
          <Plus class="h-3.5 w-3.5" />
        </button>
      </div>

      <!-- 顶部 datasource picker + Run + Save -->
      <div v-if="activeConsole" class="flex items-center gap-2 shrink-0">
        <div class="flex items-center gap-1.5">
          <Database class="h-3.5 w-3.5 text-slate-400" />
          <select v-model="activeConsole.datasource_id" class="text-xs sql-font min-w-40 max-w-60">
            <option value="">— 选数据源 —</option>
            <option v-for="d in datasources" :key="d.id" :value="d.id">
              {{ d.name }} ({{ d.db_type }})
            </option>
          </select>
        </div>
        <div class="flex items-center gap-1 text-[11px] text-slate-500">
          limit
          <input v-model.number="maxRows" type="number" min="1" max="10000" class="w-16 text-xs" />
        </div>
        <button
          class="inline-flex items-center gap-1 rounded-md bg-primary px-3 py-1.5 text-xs font-bold text-white hover:bg-primary-hover disabled:opacity-50"
          :disabled="isRunning"
          title="Ctrl/Cmd+Enter"
          @click="onRun"
        >
          <Play class="h-3.5 w-3.5" />
          {{ isRunning ? '执行中…' : '运行' }}
        </button>
        <button
          class="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-xs text-slate-600 hover:bg-slate-50"
          title="保存当前 SQL 到 console"
          @click="onSave"
        >
          <Save class="h-3.5 w-3.5" />
          保存
        </button>
        <!-- 发送到 ▾ —— Phase 4 跟其它工作台打通 -->
        <div class="relative">
          <button
            class="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-xs text-slate-600 hover:bg-slate-50"
            title="把当前 SQL 发送到其它工作台"
            @click="showSendMenu = !showSendMenu"
          >
            <Send class="h-3.5 w-3.5" />
            发送到
            <ChevronDown class="h-3 w-3" />
          </button>
          <div
            v-if="showSendMenu"
            class="absolute right-0 top-full mt-1 z-20 min-w-44 rounded-md border border-slate-200 bg-white shadow-md py-1"
            @mouseleave="showSendMenu = false"
          >
            <button class="flex items-center gap-2 w-full px-3 py-1.5 text-xs hover:bg-primary-light text-slate-700" @click="sendTo('lineage')">
              <GitBranch class="h-3.5 w-3.5 text-primary" /> 血缘分析
            </button>
            <button class="flex items-center gap-2 w-full px-3 py-1.5 text-xs hover:bg-primary-light text-slate-700" @click="sendTo('compare')">
              <GitCompareArrows class="h-3.5 w-3.5 text-primary" /> 数据对比
            </button>
            <button class="flex items-center gap-2 w-full px-3 py-1.5 text-xs hover:bg-primary-light text-slate-700" @click="sendTo('diagnosis')">
              <Microscope class="h-3.5 w-3.5 text-primary" /> SQL 诊断
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- SQL 编辑器 -->
    <div v-if="activeConsole" class="px-3">
      <SqlEditor v-model="activeConsole.sql" height="280px" placeholder="-- SELECT ... FROM ...   (Ctrl/Cmd+Enter 运行)" />
    </div>

    <!-- 底部 result / history -->
    <div class="flex-1 min-h-0 flex flex-col bg-white rounded-xl border border-slate-200 mx-3 mb-3 overflow-hidden">
      <!-- bottom tab 切换 -->
      <div class="flex items-center gap-1 border-b border-slate-100 px-3 py-1.5">
        <button
          class="inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-xs"
          :class="bottomTab === 'result' ? 'bg-primary-light text-primary font-semibold' : 'text-slate-500 hover:bg-slate-50'"
          @click="bottomTab = 'result'"
        >
          <Table2 class="h-3 w-3" /> 结果
          <span v-if="currentResult?.success" class="text-[10px] text-slate-400">
            · {{ currentResult.row_count }} 行 · {{ formatElapsed(currentResult.elapsed_ms) }}
            <span v-if="currentResult.truncated" class="text-status-warning">· 已截断</span>
          </span>
        </button>
        <button
          class="inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-xs"
          :class="bottomTab === 'history' ? 'bg-primary-light text-primary font-semibold' : 'text-slate-500 hover:bg-slate-50'"
          @click="bottomTab = 'history'; store.loadHistory()"
        >
          <HistoryIcon class="h-3 w-3" /> 历史
        </button>
        <button
          class="inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-xs"
          :class="bottomTab === 'metadata' ? 'bg-primary-light text-primary font-semibold' : 'text-slate-500 hover:bg-slate-50'"
          @click="bottomTab = 'metadata'"
        >
          <FolderTree class="h-3 w-3" /> 元数据
        </button>
        <button
          v-if="bottomTab === 'metadata' && activeConsole?.datasource_id"
          class="ml-auto inline-flex items-center gap-1 rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
          title="刷新元数据"
          @click="refreshMetadata"
        >
          <RefreshCw class="h-3 w-3" />
        </button>
      </div>

      <!-- result -->
      <div v-if="bottomTab === 'result'" class="flex-1 min-h-0 overflow-auto">
        <div v-if="!currentResult" class="px-4 py-10 text-center text-sm text-slate-400">
          点击「运行」执行 SQL,结果将显示在这里
        </div>
        <div v-else-if="!currentResult.success" class="m-3 rounded border border-status-error/30 bg-status-error-bg p-3 text-xs text-status-error">
          <div class="font-bold mb-1">执行失败</div>
          <pre class="sql-font whitespace-pre-wrap break-all">{{ currentResult.error }}</pre>
        </div>
        <table v-else-if="currentResult.columns.length" class="text-xs">
          <thead class="bg-slate-50 sticky top-0">
            <tr>
              <th v-for="col in currentResult.columns" :key="col" class="text-left whitespace-nowrap">{{ col }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, i) in currentResult.rows" :key="i" class="hover:bg-slate-50">
              <td v-for="(cell, j) in row" :key="j" class="sql-font whitespace-nowrap" :title="String(cell ?? '')">
                <span v-if="cell === null" class="italic text-slate-400">NULL</span>
                <template v-else>{{ cell }}</template>
              </td>
            </tr>
          </tbody>
        </table>
        <div v-else class="px-4 py-10 text-center text-sm text-slate-400">
          {{ currentResult.row_count }} 行(无列输出)
        </div>
      </div>

      <!-- metadata tree -->
      <div v-else-if="bottomTab === 'metadata'" class="flex-1 min-h-0 overflow-auto p-3">
        <div v-if="!activeConsole?.datasource_id" class="py-10 text-center text-sm text-slate-400">
          请先选择数据源
        </div>
        <div v-else-if="currentMeta?.loading" class="py-10 text-center text-sm text-slate-400">
          加载中…
        </div>
        <div v-else-if="currentMeta?.error" class="rounded border border-status-error/30 bg-status-error-bg p-3 text-xs text-status-error">
          <div class="font-bold mb-1">元数据加载失败</div>
          <pre class="sql-font whitespace-pre-wrap break-all">{{ currentMeta.error }}</pre>
        </div>
        <div v-else-if="!currentMeta?.schemas?.length" class="py-10 text-center text-sm text-slate-400">
          无 schema(或当前数据库不支持元数据查询)
        </div>
        <div v-else class="text-xs space-y-0.5">
          <div v-for="s in currentMeta.schemas" :key="s.name">
            <button
              class="flex items-center gap-1.5 w-full rounded px-1.5 py-1 hover:bg-slate-100 text-left"
              @click="store.toggleSchema(activeConsole.datasource_id, s.name)"
            >
              <ChevronDown v-if="s.expanded" class="h-3 w-3 text-slate-400" />
              <ChevronRight v-else class="h-3 w-3 text-slate-400" />
              <FolderTree class="h-3.5 w-3.5 text-amber-500" />
              <span class="sql-font font-semibold text-slate-700">{{ s.name }}</span>
              <span v-if="s.tables" class="text-[10px] text-slate-400">({{ s.tables.length }})</span>
            </button>
            <div v-if="s.expanded" class="ml-5 mt-0.5 space-y-0.5">
              <div v-if="s.loading" class="text-[11px] text-slate-400 py-1">加载表…</div>
              <div v-else-if="!s.tables?.length" class="text-[11px] text-slate-400 py-1 italic">空 schema</div>
              <button
                v-for="t in s.tables"
                :key="t.name"
                class="flex items-center gap-1.5 w-full rounded px-1.5 py-0.5 hover:bg-primary-light/40 text-left group"
                :title="'点击插入 SELECT * FROM ' + s.name + '.' + t.name"
                @click="onTableClick(s.name, t.name)"
              >
                <Table2 class="h-3 w-3 text-slate-400 group-hover:text-primary" />
                <span class="sql-font text-slate-700 group-hover:text-primary">{{ t.name }}</span>
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- history -->
      <div v-else class="flex-1 min-h-0 overflow-auto">
        <div v-if="!history.length" class="px-4 py-10 text-center text-sm text-slate-400">
          还没有执行历史
        </div>
        <table v-else class="text-xs">
          <thead class="bg-slate-50 sticky top-0">
            <tr>
              <th class="text-left whitespace-nowrap w-32">时间</th>
              <th class="text-left whitespace-nowrap w-24">数据源</th>
              <th class="text-left whitespace-nowrap w-12">结果</th>
              <th class="text-left whitespace-nowrap w-16">耗时</th>
              <th class="text-left whitespace-nowrap w-12">行数</th>
              <th class="text-left">SQL</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="h in history"
              :key="h.id"
              class="hover:bg-primary-light/40 cursor-pointer group"
              :title="'点击复制 SQL 到当前 Console'"
              @click="loadHistoryEntry(h)"
            >
              <td class="text-slate-500 sql-font">{{ h.executed_at.slice(0, 19).replace('T', ' ') }}</td>
              <td class="sql-font text-slate-700">{{ h.datasource_name || h.datasource_id.slice(0, 6) }}</td>
              <td>
                <span
                  :class="h.success ? 'bg-status-success-bg text-status-success' : 'bg-status-error-bg text-status-error'"
                  class="rounded px-1.5 py-0.5 text-[10px] font-bold"
                >{{ h.success ? '✓' : '✗' }}</span>
              </td>
              <td class="text-slate-500">{{ formatElapsed(h.elapsed_ms) }}</td>
              <td class="text-slate-500">{{ h.row_count }}</td>
              <td class="sql-font text-slate-700 max-w-xl truncate relative">
                {{ h.sql }}
                <span class="absolute right-2 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 transition flex gap-0.5">
                  <button class="p-1 rounded hover:bg-primary-light text-slate-400 hover:text-primary" title="发送到血缘分析" @click="sendHistoryEntry(h, 'lineage', $event)"><GitBranch class="h-3 w-3" /></button>
                  <button class="p-1 rounded hover:bg-primary-light text-slate-400 hover:text-primary" title="发送到数据对比" @click="sendHistoryEntry(h, 'compare', $event)"><GitCompareArrows class="h-3 w-3" /></button>
                  <button class="p-1 rounded hover:bg-primary-light text-slate-400 hover:text-primary" title="发送到 SQL 诊断" @click="sendHistoryEntry(h, 'diagnosis', $event)"><Microscope class="h-3 w-3" /></button>
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </section>
</template>
