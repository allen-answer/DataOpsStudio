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
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { Play, Plus, X, Save, ChevronDown, ChevronRight, Database, History as HistoryIcon, Table2, FolderTree, RefreshCw, Send, GitBranch, GitCompareArrows, Microscope, Sparkles, BarChart3, Square, FileText, Search, Columns3, Eye, Info, Bookmark, BookmarkPlus, Upload, Download, Trash2, Pencil, Asterisk } from 'lucide-vue-next'
import { useSqlWorkbenchStore } from '../stores/sqlWorkbench'
import { useSqlTemplatesStore, type SQLTemplate } from '../stores/sqlTemplates'
import { useBootstrapStore } from '../stores/bootstrap'
import { useNoticeStore } from '../stores/notice'
import SqlEditor from '../components/SqlEditor.vue'
import ExplainPanel from '../components/sql/ExplainPanel.vue'
import { setSqlTransfer } from '../utils/sqlTransfer'

const router = useRouter()

const store = useSqlWorkbenchStore()
const templatesStore = useSqlTemplatesStore()
const bootstrap = useBootstrapStore()
const notice = useNoticeStore()
const { consoles, activeConsole, activeConsoleId, results, running, history, metadataByDs, currentExecutionId, explainResults, searchResults, searchLoading, exporting, reloadProgress } = storeToRefs(store)
const { templates, loading: templatesLoading, filters: templateFilters } = storeToRefs(templatesStore)
const { state: bootstrapState } = bootstrap

const ACTIVE_ID_KEY = 'dataops.sqlWorkbench.activeId'
const bottomTab = ref<'result' | 'history' | 'metadata' | 'explain' | 'templates'>('result')
const maxRows = ref(1000)
// v0.5:单查询超时(秒)。到时后端自动 cancel + 标 reason='timeout'。
// 持久到 localStorage,刷新页面仍保留。
const TIMEOUT_KEY = 'dataops.sqlWorkbench.timeoutSeconds'
const timeoutSeconds = ref<number>(Number(localStorage.getItem(TIMEOUT_KEY)) || 300)
watch(timeoutSeconds, (v) => {
  if (v && v > 0) localStorage.setItem(TIMEOUT_KEY, String(v))
})

// metadata:切 datasource 自动拉一次 + 后台预热所有 schemas 的 tables + columns
// 让 SQL 编辑器键入 `schema.` / `table.` 时立即有 table / column 补全。
async function _prewarmSchemaTables(dsId: string) {
  const meta = metadataByDs.value[dsId]
  if (!meta?.schemas?.length) return
  // 并发但加 throttle:最多 4 个同时跑,避免几十个 schema 时打爆后端
  const concurrency = 4
  const queue = meta.schemas.filter(s => s.tables === undefined).map(s => s.name)
  async function worker() {
    while (queue.length) {
      const name = queue.shift()!
      try { await store.loadTables(dsId, name) } catch { /* tolerate */ }
    }
  }
  await Promise.all(Array.from({ length: concurrency }, worker))
  // tables 拉完后,后台静默拉 columns bulk —— 一次 SQL 拉一个 schema 全部字段(N→1)
  // 不阻塞 UI(没 await,fire-and-forget),拉完后 completionSchema reactive 自动更新
  _prewarmSchemaColumns(dsId)
}

async function _prewarmSchemaColumns(dsId: string) {
  /**
   * Bulk 拉每个 schema 的字段(给 SQL 编辑器字段补全做预热)。
   * 写 SQL 时键入 `users.` 立即有字段提示,不再要求先点 metadata 里的表。
   *
   * 跟逐表拉相比 — 1000 张表的 schema 从 1000 HTTP + 1000 information_schema 查询
   * 减为 1 HTTP + 1 SQL。
   */
  const meta = metadataByDs.value[dsId]
  if (!meta?.schemas?.length) return
  const concurrency = 2  // 字段量大,并发别开太高
  const queue = meta.schemas
    .filter(s => Array.isArray(s.tables) && s.tables.length > 0)
    .map(s => s.name)
  async function worker() {
    while (queue.length) {
      const name = queue.shift()!
      try { await store.loadColumnsBulk(dsId, name) } catch { /* tolerate */ }
    }
  }
  await Promise.all(Array.from({ length: concurrency }, worker))
}

watch(() => activeConsole.value?.datasource_id, async (dsId) => {
  if (!dsId) return
  // 没缓存才拉(避免每次切 tab 都重打)
  if (!metadataByDs.value[dsId]) {
    try { await store.loadSchemas(dsId) } catch { /* tolerate */ }
  }
  // 总是尝试预热未加载的 schema tables —— loadTables 内置去重,已加载过的不会
  // 重打后端(走 metadata cache);只首次切到 ds 时真正触发网络
  _prewarmSchemaTables(dsId)
}, { immediate: true })

function refreshMetadata() {
  const dsId = activeConsole.value?.datasource_id
  if (!dsId) return
  // v0.3:走后端 cache 失效接口,然后重拉。本地内存里的 schemas 不立即清,
  // store.refreshAllMetadata 内部会保留树展开状态,刷出新 cached_at。
  store.refreshAllMetadata(dsId).catch(() => {})
}

function reloadAllObjects() {
  /** DataGrip 风格全量重新加载 — 清 cache + 拉全部 schemas + tables + columns.
   *  完成后 search / 字段补全立刻可用,不再要用户手动展开每个 schema. */
  const dsId = activeConsole.value?.datasource_id
  if (!dsId) return
  if (reloadProgress.value[dsId]?.active) return  // 已在加载中,防重复点
  notice.setNotice('开始重新加载所有对象...')
  store.reloadAllMetadata(dsId).catch((e: Error) => {
    notice.setNotice('重新加载失败: ' + (e?.message || String(e)))
  })
}

// 当前 ds 的 reload 进度,UI 通过 v-if 控制进度条显示
const currentReloadProgress = computed(() => {
  const dsId = activeConsole.value?.datasource_id
  return dsId ? (reloadProgress.value[dsId] || null) : null
})

// 格式化缓存时间:ISO → 本地 HH:MM,跨日加日期
function formatCacheTime(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  const now = new Date()
  const sameDay = d.toDateString() === now.toDateString()
  if (sameDay) return d.toTimeString().slice(0, 5)
  return d.toISOString().slice(5, 16).replace('T', ' ')
}

// ─── 对象搜索 ─────────────────────────────────────────────────────────
const searchQuery = ref('')
let searchTimer: ReturnType<typeof setTimeout> | null = null

watch(searchQuery, (q) => {
  const dsId = activeConsole.value?.datasource_id
  if (!dsId) return
  if (searchTimer) clearTimeout(searchTimer)
  if (!q.trim()) {
    // 清空搜索结果
    if (searchResults.value[dsId]) searchResults.value[dsId] = []
    return
  }
  searchTimer = setTimeout(() => {
    store.searchMetadata(dsId, q).catch(() => {})
  }, 200)
})

// 切 ds 清空搜索
watch(() => activeConsole.value?.datasource_id, () => {
  searchQuery.value = ''
})

const currentSearchResults = computed<any[]>(() => {
  const dsId = activeConsole.value?.datasource_id || ''
  return dsId ? (searchResults.value[dsId] || []) : []
})

const currentSearchLoading = computed<boolean>(() => {
  const dsId = activeConsole.value?.datasource_id || ''
  return dsId ? !!searchLoading.value[dsId] : false
})

// 判断当前 ds 是否已有 tables cache —— 否则搜索注定空,提示用户去展开
const hasAnyTableCache = computed<boolean>(() => {
  const meta = currentMeta.value
  if (!meta?.schemas?.length) return false
  return meta.schemas.some(s => Array.isArray(s.tables) && s.tables.length > 0)
})

// 聚焦搜索框时,若 schemas 还没拉过 → lazy 触发一次,让用户在搜索时至少有 schemas 可见
function onSearchFocus() {
  const dsId = activeConsole.value?.datasource_id
  if (!dsId) return
  const meta = metadataByDs.value[dsId]
  if (!meta || !meta.schemas.length) {
    store.loadSchemas(dsId).catch(() => {})
  }
}

// 点搜索结果:切到 metadata tab + 跳转高亮(复用之前的 jump 函数);
// 如果是 table/view 结果,顺便打开表详情让用户立刻看字段
function onPickSearchResult(r: any) {
  jumpToSearchResult(r)
  if (r.kind === 'table' || r.kind === 'view') {
    openTableDetail(r.schema, r.table)
  }
  searchQuery.value = ''
}

async function jumpToSearchResult(r: any) {
  const dsId = activeConsole.value?.datasource_id
  if (!dsId) return
  bottomTab.value = 'metadata'
  // 确保 schema 已展开 + tables 已加载,这样目标节点可见
  const meta = metadataByDs.value[dsId]
  if (!meta) return
  const sch = meta.schemas.find(s => s.name === r.schema)
  if (sch) {
    if (!sch.expanded) sch.expanded = true
    if (sch.tables === undefined) {
      await store.loadTables(dsId, r.schema).catch(() => {})
    }
  }
  // 滚动 / 高亮在 template 通过 :data-table-key + 类名实现
  highlightedTableKey.value = `${r.schema}::${r.table}`
  // 4 秒后清高亮
  setTimeout(() => {
    if (highlightedTableKey.value === `${r.schema}::${r.table}`) {
      highlightedTableKey.value = ''
    }
  }, 4000)
  // 列结果:顺手加载列,供后续 SQL 编辑器补全
  if (r.kind === 'column') {
    store.loadColumns(dsId, r.schema, r.table).catch(() => {})
  }
}

const highlightedTableKey = ref('')

// ─── 表详情 drawer ─────────────────────────────────────────────────────
const tableDetail = ref<null | {
  schema: string
  table: string
  loading: boolean
  columns: any[]
  indexes: any[]
  ddl: string | null
  ddlSupported: boolean
  detailTab: 'columns' | 'indexes' | 'ddl'
}>(null)

async function openTableDetail(schema: string, table: string) {
  const dsId = activeConsole.value?.datasource_id
  if (!dsId) return
  tableDetail.value = {
    schema, table, loading: true,
    columns: [], indexes: [], ddl: null, ddlSupported: false,
    detailTab: 'columns',
  }
  try {
    const data = await store.loadTableDetail(dsId, schema, table)
    if (tableDetail.value && tableDetail.value.schema === schema && tableDetail.value.table === table) {
      tableDetail.value.columns = data.columns
      tableDetail.value.indexes = data.indexes
      tableDetail.value.ddl = data.ddl
      tableDetail.value.ddlSupported = data.ddlSupported
    }
  } finally {
    if (tableDetail.value) tableDetail.value.loading = false
  }
}

function closeTableDetail() {
  tableDetail.value = null
}

// ─── 模板库(v0.4) ───────────────────────────────────────────────────
// 「保存为模板」modal 状态
const showSaveTemplateModal = ref(false)
const saveTemplateDraft = ref<{
  id?: string  // 编辑现有时填,新建时空
  name: string
  description: string
  tagsText: string   // UI 用逗号分隔字符串,提交时切成数组
  db_typesText: string
  risk_level: 'low' | 'medium' | 'high'
  sql: string
}>({ name: '', description: '', tagsText: '', db_typesText: 'all', risk_level: 'low', sql: '' })

function openSaveTemplateModal() {
  const c = activeConsole.value
  if (!c || !c.sql.trim()) {
    notice.setNotice('当前 SQL 为空,无法保存为模板')
    return
  }
  // 默认拿当前 ds 的 db_type 预填
  const ds = (datasources.value || []).find((d: any) => d.id === c.datasource_id)
  saveTemplateDraft.value = {
    name: '', description: '', tagsText: '',
    db_typesText: ds?.db_type || 'all',
    risk_level: 'low',
    sql: c.sql,
  }
  showSaveTemplateModal.value = true
}

function openEditTemplateModal(t: SQLTemplate) {
  if (t.builtin) {
    notice.setNotice('内置模板不可编辑,可以点"克隆"按钮另存为新模板')
    return
  }
  saveTemplateDraft.value = {
    id: t.id,
    name: t.name,
    description: t.description,
    tagsText: (t.tags || []).join(', '),
    db_typesText: (t.db_types || ['all']).join(', '),
    risk_level: t.risk_level,
    sql: t.sql,
  }
  showSaveTemplateModal.value = true
}

function cloneTemplateToDraft(t: SQLTemplate) {
  saveTemplateDraft.value = {
    name: t.name + ' (副本)',
    description: t.description,
    tagsText: (t.tags || []).join(', '),
    db_typesText: (t.db_types || ['all']).join(', '),
    risk_level: t.risk_level,
    sql: t.sql,
  }
  showSaveTemplateModal.value = true
}

function _parseCsv(s: string): string[] {
  return s.split(',').map(x => x.trim()).filter(Boolean)
}

async function onSubmitSaveTemplate() {
  const d = saveTemplateDraft.value
  if (!d.name.trim()) { notice.setNotice('请填模板名'); return }
  if (!d.sql.trim()) { notice.setNotice('SQL 不能为空'); return }
  const payload = {
    name: d.name.trim(),
    description: d.description,
    tags: _parseCsv(d.tagsText),
    db_types: _parseCsv(d.db_typesText) || ['all'],
    risk_level: d.risk_level,
    sql: d.sql,
  }
  try {
    if (d.id) {
      await templatesStore.updateTemplate(d.id, payload)
      notice.setNotice('模板已更新')
    } else {
      await templatesStore.createTemplate(payload)
      notice.setNotice('模板已保存')
    }
    showSaveTemplateModal.value = false
  } catch (e: any) {
    notice.setNotice('保存模板失败: ' + (e?.message || String(e)))
  }
}

function insertTemplateToConsole(t: SQLTemplate) {
  const c = activeConsole.value
  if (!c) { notice.setNotice('请先打开一个 Console'); return }
  // 跟"点表名插 SELECT *"一个套路:append 而不是 replace,保护用户当前编辑
  c.sql = c.sql.trim() ? `${c.sql}\n\n${t.sql}` : t.sql
  bottomTab.value = 'result'
  notice.setNotice(`已插入模板「${t.name}」`)
}

async function deleteTemplateConfirm(t: SQLTemplate) {
  if (t.builtin) {
    notice.setNotice('内置模板不可删除')
    return
  }
  if (!confirm(`确定删除模板「${t.name}」?该操作不可恢复。`)) return
  try {
    await templatesStore.deleteTemplate(t.id)
    notice.setNotice('已删除')
  } catch (e: any) {
    notice.setNotice('删除失败: ' + (e?.message || String(e)))
  }
}

// 导入 JSON:用 file input 选本地文件
function onImportClick() {
  const input = document.createElement('input')
  input.type = 'file'
  input.accept = '.json,application/json'
  input.onchange = async () => {
    const file = input.files?.[0]
    if (!file) return
    try {
      const text = await file.text()
      const parsed = JSON.parse(text)
      // 兼容两种格式:{templates: [...]} 或直接 [...]
      const items = Array.isArray(parsed) ? parsed : parsed.templates
      if (!Array.isArray(items)) {
        notice.setNotice('JSON 格式错误:期望 {templates: [...]} 或直接数组')
        return
      }
      const overwrite = confirm(`检测到 ${items.length} 个模板。同名模板要覆盖现有的吗?\n\n确定 = 覆盖同名 / 取消 = 跳过同名`)
      const report = await templatesStore.importTemplates(items, overwrite)
      notice.setNotice(`导入完成:新建 ${report.created} · 跳过 ${report.skipped} · 错误 ${report.errors}`)
    } catch (e: any) {
      notice.setNotice('导入失败: ' + (e?.message || String(e)))
    }
  }
  input.click()
}

// 导出:把数据 dump 成 .json 文件触发浏览器下载
async function onExportClick(includeBuiltin: boolean = false) {
  try {
    const items = await templatesStore.exportTemplates(includeBuiltin)
    const blob = new Blob(
      [JSON.stringify({ templates: items, count: items.length, exported_at: new Date().toISOString() }, null, 2)],
      { type: 'application/json' },
    )
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `sql-templates-${new Date().toISOString().slice(0, 10)}.json`
    a.click()
    URL.revokeObjectURL(url)
    notice.setNotice(`已导出 ${items.length} 个模板`)
  } catch (e: any) {
    notice.setNotice('导出失败: ' + (e?.message || String(e)))
  }
}

// 切到模板 tab 时 lazy 拉一次列表(首次)
watch(bottomTab, (tab) => {
  if (tab === 'templates' && !templates.value.length && !templatesLoading.value) {
    templatesStore.loadTemplates().catch(() => {})
  }
})

// 过滤变化 → 重拉(简单方案,debounce 300ms)
let templateFilterTimer: ReturnType<typeof setTimeout> | null = null
watch(templateFilters, () => {
  if (templateFilterTimer) clearTimeout(templateFilterTimer)
  templateFilterTimer = setTimeout(() => {
    if (bottomTab.value === 'templates') {
      templatesStore.loadTemplates().catch(() => {})
    }
  }, 300)
}, { deep: true })

function onTableClick(schema: string, table: string) {
  const c = activeConsole.value
  if (!c) return
  const qualified = schema ? `${schema}.${table}` : table
  const snippet = `SELECT *\nFROM ${qualified}\nLIMIT 100;`
  // 简化:追加到当前 SQL 后面(用户可见上下文,且不破坏现有草稿)
  c.sql = c.sql ? `${c.sql}\n\n${snippet}` : snippet
  bottomTab.value = 'result'
  // 顺便拉一次该表的 columns,让 SQL 编辑器补全立刻能用 — 失败静默(补全降级到表级)。
  if (c.datasource_id) {
    store.loadColumns(c.datasource_id, schema, table).catch(() => {})
  }
  nextTick(() => _scheduleSave())
}

const currentMeta = computed(() => {
  const dsId = activeConsole.value?.datasource_id || ''
  return dsId ? metadataByDs.value[dsId] : null
})

// 把 metadata tree 派生成 CodeMirror lang-sql 需要的形状:
//   { 'schema.table': ['col1', 'col2', ...], 'schema.table2': [...], ... }
// 表 columns 还没拉时给空数组 —— lang-sql 仍能补表名,只是补不了列。
// 用户点击 metadata table 时(onTableClick)会顺势 loadColumns,这时本 computed
// reactive 重算,SqlEditor 通过 watch 重新配置语言扩展。
const completionSchema = computed<Record<string, string[]>>(() => {
  const meta = currentMeta.value
  if (!meta?.schemas?.length) return {}
  const result: Record<string, string[]> = {}
  for (const s of meta.schemas) {
    for (const t of (s.tables || [])) {
      const key = s.name ? `${s.name}.${t.name}` : t.name
      result[key] = Array.isArray(t.columns) ? t.columns : []
    }
  }
  return result
})

// 当前数据源的 db_type —— SqlEditor 用它路由 CodeMirror 方言
const currentDbType = computed<string>(() => {
  const dsId = activeConsole.value?.datasource_id
  if (!dsId) return ''
  const ds = (datasources.value || []).find((d: any) => d.id === dsId)
  return (ds?.db_type as string) || ''
})

// ─── lifecycle ─────────────────────────────────────────────────────────────

// 草稿恢复提示:console 加载完成后,若 localStorage 里有跟后端 sql 不同的草稿,
// 提示用户决定恢复还是丢弃。一个 console 一条记录,值是草稿 SQL。
const pendingDrafts = ref<Record<string, string>>({})

function detectDrafts() {
  const found: Record<string, string> = {}
  for (const c of consoles.value) {
    const draft = store.loadDraft(c.id)
    if (draft && draft !== c.sql) found[c.id] = draft
  }
  pendingDrafts.value = found
}

function restoreDraft(consoleId: string) {
  const draft = pendingDrafts.value[consoleId]
  if (!draft) return
  const c = consoles.value.find(x => x.id === consoleId)
  if (c) c.sql = draft
  delete pendingDrafts.value[consoleId]
  notice.setNotice('已恢复未保存草稿')
}

function discardDraft(consoleId: string) {
  store.clearDraft(consoleId)
  delete pendingDrafts.value[consoleId]
}

function saveAsDraft() {
  const c = activeConsole.value
  if (!c) return
  store.saveDraft(c.id, c.sql)
  notice.setNotice('已保存为本地草稿')
}

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
  detectDrafts()
  store.loadHistory().catch(() => {})
})

// 草稿自动保存:SQL 变化 → debounce 300ms 写 localStorage。
// 跟后端 _scheduleSave 是两条并行通道(后端走 800ms debounce)。
let draftTimer: ReturnType<typeof setTimeout> | null = null
watch(
  () => activeConsole.value?.sql,
  (sql) => {
    const c = activeConsole.value
    if (!c) return
    if (draftTimer) clearTimeout(draftTimer)
    draftTimer = setTimeout(() => {
      store.saveDraft(c.id, sql || '')
    }, 300)
  },
)

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
      timeout_seconds: timeoutSeconds.value,
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

// Ctrl/Cmd+Enter Run / Alt+Shift+F 格式化 快捷键
function onKeydown(e: KeyboardEvent) {
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
    e.preventDefault()
    onRun()
    return
  }
  // Alt + Shift + F:格式化(浏览器 Alt 在 Windows 默认菜单冲突,但 SQL 编辑器内部 focus 时通常 OK)
  if (e.altKey && e.shiftKey && (e.key === 'F' || e.key === 'f')) {
    e.preventDefault()
    onFormat()
  }
}

// ─── 格式化 / 停止 / Explain (v0.2) ─────────────────────────────────────

async function onFormat() {
  const c = activeConsole.value
  if (!c?.sql?.trim()) return
  try {
    const resp = await store.formatSql(c.sql, c.datasource_id)
    if (resp.success && resp.formatted_sql) {
      c.sql = resp.formatted_sql
      notice.setNotice(`格式化完成 (${resp.dialect})`)
      nextTick(() => _scheduleSave())
    } else {
      notice.setNotice('格式化失败: ' + (resp.error || '未知错误'))
    }
  } catch (e: unknown) {
    notice.setNotice('格式化失败: ' + ((e as Error)?.message || String(e)))
  }
}

async function onExpandStar() {
  const c = activeConsole.value
  if (!c?.sql?.trim() || !c.datasource_id) {
    notice.setNotice('请先选数据源 + 输入 SQL')
    return
  }
  try {
    const resp = await store.expandStar(c.sql, c.datasource_id)
    if (resp.changed) {
      c.sql = resp.sql
      notice.setNotice('已展开 * 为完整列名')
      nextTick(() => _scheduleSave())
    } else {
      // 没改 — 可能是 no_star 或所有 cache miss
      const reason = resp.warnings?.[0]
      if (reason?.code === 'no_star') {
        notice.setNotice('SQL 里没有 * 可展开')
      } else if (reason?.code === 'table_not_in_cache') {
        notice.setNotice('字段缓存不全 — 先点 sidebar 的 [全量] 按钮重新加载')
      } else {
        notice.setNotice('展开失败: ' + (reason?.message || '未知'))
      }
    }
  } catch (e: unknown) {
    notice.setNotice('展开失败: ' + ((e as Error)?.message || String(e)))
  }
}

async function onStop() {
  const c = activeConsole.value
  if (!c) return
  try {
    await store.cancelExecution(c.id)
    notice.setNotice('已请求取消 —— 等待当前查询返回后丢弃结果')
  } catch (e: unknown) {
    notice.setNotice('取消失败: ' + ((e as Error)?.message || String(e)))
  }
}

async function onExplain() {
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
    await store.explain(c.id, { datasource_id: c.datasource_id, sql: c.sql })
    bottomTab.value = 'explain'
  } catch (e: unknown) {
    notice.setNotice('Explain 失败: ' + ((e as Error)?.message || String(e)))
  }
}

const currentExplain = computed(() => (activeConsole.value ? explainResults.value[activeConsole.value.id] : null))

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

// v0.5:慢 SQL 阈值。超过即给 banner / chip 提示用户"考虑优化"。
// 3 秒是经验值 —— 单条 OLTP SELECT 跑 > 3s 通常说明缺索引或全表扫。
const SLOW_THRESHOLD_MS = 3000

// v0.5:统计当前有几个 console 在跑(running map 中 true 的数量)
const runningCount = computed<number>(() => {
  return Object.values(running.value).filter(v => v).length
})

// 浏览器原生关页 / 刷新拦截。only 在有 in-flight 查询时触发,
// 否则用户每次离开都被骚扰会很烦。
function onBeforeUnload(e: BeforeUnloadEvent) {
  if (runningCount.value > 0) {
    e.preventDefault()
    // 大多数浏览器会用自己固定的"未保存"文案,不会用我们 returnValue 的字符串,
    // 但仍需要 set 非空让 prompt 显示
    e.returnValue = `还有 ${runningCount.value} 个查询正在执行,离开会丢失结果。`
    return e.returnValue
  }
}

// Vue Router 内部跳转拦截(切到别的 view)
onBeforeRouteLeave((to, from, next) => {
  if (runningCount.value > 0) {
    const ok = confirm(`还有 ${runningCount.value} 个查询正在执行,离开 SQL 工作台会让客户端丢失结果(后端仍会跑完,但结果不再展示)。是否继续?`)
    next(ok)
  } else {
    next()
  }
})

onMounted(() => {
  window.addEventListener('beforeunload', onBeforeUnload)
})

onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', onBeforeUnload)
})

function isSlow(ms: number | undefined | null): boolean {
  return !!ms && ms >= SLOW_THRESHOLD_MS
}

// ─── v0.5+ 结果导出 ────────────────────────────────────────────────
const showExportMenu = ref(false)

// 全表扫描风险确认弹窗状态(包 A.2)。后端检测到 SELECT * 无 WHERE 无 LIMIT 等
// 风险时返 requires_confirmation,前端弹框让用户决定是否继续。confirm 时带
// confirm_full_scan=true 重新提交;cancel 直接放弃导出。
const fullScanConfirm = ref<{
  visible: boolean
  format: 'csv' | 'excel' | 'json' | 'sql' | null
  warnings: string[]
}>({ visible: false, format: null, warnings: [] })

async function exportAs(format: 'csv' | 'excel' | 'json' | 'sql', confirmFullScan = false) {
  const c = activeConsole.value
  if (!c || !c.datasource_id || !c.sql.trim()) {
    notice.setNotice('请先选数据源 + 输入 SQL')
    return
  }
  showExportMenu.value = false
  notice.setNotice(`正在导出为 ${format.toUpperCase()}...`)
  const r = await store.exportResult(c.id, {
    datasource_id: c.datasource_id,
    sql: c.sql,
    format,
    title: c.name || '',
    max_rows: 100_000,
    confirm_full_scan: confirmFullScan,
  })

  // 全表扫描风险 —— 弹确认框,用户点"继续"后带 confirm_full_scan=true 重提
  if (r.requires_confirmation) {
    fullScanConfirm.value = {
      visible: true,
      format,
      warnings: r.warnings || [],
    }
    return
  }

  if (r.ok && r.download_url) {
    // 关键 fix:不用 window.location.href(浏览器原生导航不带 Bearer token 直接 401)。
    // 改走 apiDownload —— fetch + Authorization header 拿 blob,然后 a.click() 触发下载。
    try {
      const { apiDownload } = await import('../api')
      await apiDownload(r.download_url, r.file_name || `export.${format === 'excel' ? 'xlsx' : format}`)
      notice.setNotice(`导出完成(${r.row_count ?? '?'} 行),开始下载`)
    } catch (e) {
      notice.setNotice(`下载失败: ${(e as Error)?.message || String(e)}`)
    }
  } else {
    notice.setNotice(`导出失败: ${r.error || '未知错误'}`)
  }
}

function confirmFullScanExport() {
  const fmt = fullScanConfirm.value.format
  fullScanConfirm.value.visible = false
  if (fmt) {
    exportAs(fmt, true)
  }
}

function cancelFullScanExport() {
  fullScanConfirm.value.visible = false
  fullScanConfirm.value.format = null
  fullScanConfirm.value.warnings = []
  notice.setNotice('已取消导出')
}

const isExporting = computed<boolean>(() => {
  const c = activeConsole.value
  return c ? !!exporting.value[c.id] : false
})

// ─── Phase 4:发送当前 SQL 到血缘 / 对比 / 诊断 ─────────────────────────

const showSendMenu = ref(false)

function sendTo(target: 'lineage' | 'compare' | 'diagnosis', sql?: string, dsId?: string) {
  const finalSql = (sql ?? activeConsole.value?.sql ?? '').trim()
  if (!finalSql) {
    notice.setNotice('SQL 为空,无法发送')
    return
  }
  const finalDs = dsId ?? activeConsole.value?.datasource_id ?? ''
  // v0.5:把 console / 数据源 / 上次执行耗时 一并打包,优化工作台 UI
  // 顶部展示"来源信息卡",用户知道这条 SQL 从哪里来 + 历史执行多慢
  const c = activeConsole.value
  const ds = (datasources.value || []).find((d: any) => d.id === finalDs)
  const result = c ? results.value[c.id] : null
  setSqlTransfer({
    sql: finalSql,
    datasourceId: finalDs,
    datasourceName: ds?.name,
    datasourceDbType: ds?.db_type,
    source: 'sql-workbench',
    consoleId: c?.id,
    consoleName: c?.name,
    elapsedMs: result?.elapsed_ms,
    executedAt: result?.success ? new Date().toISOString() : undefined,
  })
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
  // 来自 history 的发送也带上 history 行已知的元数据(耗时 / 执行时间)
  const ds = (datasources.value || []).find((d: any) => d.id === entry.datasource_id)
  setSqlTransfer({
    sql: entry.sql,
    datasourceId: entry.datasource_id,
    datasourceName: ds?.name || entry.datasource_name,
    datasourceDbType: ds?.db_type,
    source: 'sql-workbench-history',
    elapsedMs: entry.elapsed_ms,
    executedAt: entry.executed_at,
  })
  const path = {
    lineage: '/lineage',
    compare: '/data-compare',
    diagnosis: '/sql-diagnosis',
  }[target]
  router.push(path)
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
        <!-- v0.5 超时(秒)。到时后端自动 cancel + reason='timeout' -->
        <div class="flex items-center gap-1 text-[11px] text-slate-500" title="单查询超时,到时自动取消(秒,1-3600)">
          timeout
          <input v-model.number="timeoutSeconds" type="number" min="1" max="3600" class="w-14 text-xs" />
        </div>
        <!-- v0.2 运行 / 停止 双态按钮 —— 执行中显示停止 -->
        <button
          v-if="!isRunning"
          class="inline-flex items-center gap-1 rounded-md bg-primary px-3 py-1.5 text-xs font-bold text-white hover:bg-primary-hover"
          title="Ctrl/Cmd+Enter"
          @click="onRun"
        >
          <Play class="h-3.5 w-3.5" />
          运行
        </button>
        <button
          v-else
          class="inline-flex items-center gap-1 rounded-md bg-status-error px-3 py-1.5 text-xs font-bold text-white hover:opacity-90"
          title="请求取消(底层驱动可能不支持中途中断,完成后会丢弃结果)"
          @click="onStop"
        >
          <Square class="h-3.5 w-3.5" />
          停止
        </button>
        <!-- v0.2 格式化按钮 —— Alt+Shift+F -->
        <button
          class="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-xs text-slate-600 hover:bg-slate-50"
          title="格式化 SQL (Alt+Shift+F)"
          @click="onFormat"
        >
          <Sparkles class="h-3.5 w-3.5" />
          格式化
        </button>
        <!-- v0.2 Explain 按钮 -->
        <button
          class="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-xs text-slate-600 hover:bg-slate-50"
          title="查看执行计划 (MySQL/OB MySQL 完整支持,Oracle/DM 暂不支持)"
          @click="onExplain"
        >
          <BarChart3 class="h-3.5 w-3.5" />
          Explain
        </button>
        <!-- v0.7 展开 * 按钮 -->
        <button
          class="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-xs text-slate-600 hover:bg-slate-50"
          title="把 SELECT * 展开成完整列名(从字段缓存读取;cache miss 会自动拉一次)"
          @click="onExpandStar"
        >
          <Asterisk class="h-3.5 w-3.5" />
          展开 *
        </button>
        <button
          class="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-xs text-slate-600 hover:bg-slate-50"
          title="保存当前 SQL 到 console"
          @click="onSave"
        >
          <Save class="h-3.5 w-3.5" />
          保存
        </button>
        <!-- 本地草稿:即使无网络 / 后端报错也能保留当前编辑,刷新页面后会提示恢复 -->
        <button
          class="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-xs text-slate-600 hover:bg-slate-50"
          title="把当前 SQL 存进浏览器本地草稿(不依赖服务器)"
          @click="saveAsDraft"
        >
          <FileText class="h-3.5 w-3.5" />
          另存草稿
        </button>
        <!-- 保存为模板(v0.4)—— 把当前 SQL 入模板库,跨 console / 跨用户复用 -->
        <button
          class="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-xs text-slate-600 hover:bg-slate-50"
          title="把当前 SQL 保存到模板库,跨 console 复用"
          @click="openSaveTemplateModal"
        >
          <BookmarkPlus class="h-3.5 w-3.5" />
          存为模板
        </button>
        <!-- 导出(v0.5+)—— 下拉菜单 4 个格式 -->
        <div class="relative">
          <button
            class="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-xs text-slate-600 hover:bg-slate-50"
            :disabled="isExporting"
            :title="isExporting ? '导出中...' : '导出结果为 CSV / Excel / JSON / SQL Insert'"
            @click="showExportMenu = !showExportMenu"
          >
            <Download class="h-3.5 w-3.5" :class="{ 'animate-pulse': isExporting }" />
            {{ isExporting ? '导出中…' : '导出' }}
            <ChevronDown class="h-3 w-3" />
          </button>
          <div
            v-if="showExportMenu && !isExporting"
            class="absolute right-0 top-full mt-1 z-20 min-w-32 rounded-md border border-slate-200 bg-white shadow-md py-1"
            @mouseleave="showExportMenu = false"
          >
            <button class="flex items-center gap-2 w-full px-3 py-1.5 text-xs hover:bg-primary-light text-slate-700" @click="exportAs('csv')">📄 CSV</button>
            <button class="flex items-center gap-2 w-full px-3 py-1.5 text-xs hover:bg-primary-light text-slate-700" @click="exportAs('excel')">📊 Excel</button>
            <button class="flex items-center gap-2 w-full px-3 py-1.5 text-xs hover:bg-primary-light text-slate-700" @click="exportAs('json')">{ } JSON</button>
            <button class="flex items-center gap-2 w-full px-3 py-1.5 text-xs hover:bg-primary-light text-slate-700" @click="exportAs('sql')">📋 SQL Insert</button>
          </div>
        </div>
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

    <!-- 全局对象搜索条(v0.3)—— 跨 table/column/view 搜元数据,不依赖底部 tab。
         结果以浮层 dropdown 出现在 input 下方,点击跳到元数据树并展开高亮。 -->
    <div v-if="activeConsole?.datasource_id" class="relative mx-3">
      <div class="relative">
        <Search class="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-400" />
        <input
          v-model="searchQuery"
          type="text"
          placeholder="搜表 / 列 / 视图 —— 空格分隔 AND 命中(基于元数据缓存,先展开 schema 触发)"
          class="w-full pl-9 pr-9 text-xs sql-font"
          @focus="onSearchFocus"
        />
        <button
          v-if="searchQuery"
          class="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-700"
          title="清空"
          @click="searchQuery = ''"
        >
          <X class="h-3.5 w-3.5" />
        </button>
      </div>
      <!-- dropdown -->
      <div
        v-if="searchQuery.trim()"
        class="absolute left-0 right-0 top-full mt-1 z-20 max-h-80 overflow-y-auto rounded-md border border-slate-200 bg-white shadow-lg"
      >
        <div v-if="currentSearchLoading" class="py-4 text-center text-[11px] text-slate-400">搜索中…</div>
        <div v-else-if="!currentSearchResults.length" class="py-4 text-center text-[11px] text-slate-400 px-3">
          <div>无匹配。</div>
          <div v-if="!hasAnyTableCache" class="mt-2 text-status-warning">
            ⚠ 元数据 cache 为空 —— 请先点底部「元数据」tab,展开几个 schema 触发缓存后再搜。
            <button class="ml-1 underline hover:text-primary" @click="bottomTab = 'metadata'">打开元数据 →</button>
          </div>
        </div>
        <button
          v-for="r in currentSearchResults"
          :key="r.kind + '::' + r.schema + '::' + r.table + '::' + (r.column || r.view || '')"
          class="flex items-center gap-2 w-full px-3 py-1.5 text-left hover:bg-primary-light/40 group"
          @click="onPickSearchResult(r)"
        >
          <span
            class="rounded px-1 py-0.5 text-[9px] font-bold uppercase shrink-0"
            :class="{
              'bg-tag-source-bg text-tag-source': r.kind === 'table',
              'bg-tag-intermediate-bg text-tag-intermediate': r.kind === 'column',
              'bg-tag-reference-bg text-tag-reference': r.kind === 'view',
            }"
          >{{ r.kind }}</span>
          <span class="sql-font text-xs text-slate-700 truncate flex-1" :title="r.snippet">{{ r.snippet }}</span>
          <span v-if="r.data_type" class="text-[10px] text-slate-400 sql-font shrink-0">{{ r.data_type }}</span>
        </button>
      </div>
    </div>

    <!-- 草稿恢复提示条(仅当 active console 有未恢复草稿时显示) -->
    <div
      v-if="activeConsole && pendingDrafts[activeConsole.id]"
      class="mx-3 flex items-center justify-between gap-3 rounded-md border border-status-warning/40 bg-status-warning-bg px-3 py-1.5 text-xs"
    >
      <span class="text-slate-700">
        <FileText class="inline h-3.5 w-3.5 -mt-0.5 text-status-warning" />
        检测到本地未保存草稿,跟服务器版本不同。
      </span>
      <span class="flex items-center gap-2">
        <button
          class="rounded bg-primary px-2 py-0.5 text-white hover:bg-primary-hover"
          @click="restoreDraft(activeConsole.id)"
        >
          恢复草稿
        </button>
        <button
          class="rounded border border-slate-300 bg-white px-2 py-0.5 text-slate-600 hover:bg-slate-50"
          @click="discardDraft(activeConsole.id)"
        >
          丢弃
        </button>
      </span>
    </div>

    <!-- SQL 编辑器 -->
    <div v-if="activeConsole" class="px-3">
      <SqlEditor
        v-model="activeConsole.sql"
        height="280px"
        placeholder="-- SELECT ... FROM ...   (Ctrl/Cmd+Enter 运行)"
        :dialect="currentDbType"
        :completion-schema="completionSchema"
        :snippets="true"
      />
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
          class="inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-xs"
          :class="bottomTab === 'explain' ? 'bg-primary-light text-primary font-semibold' : 'text-slate-500 hover:bg-slate-50'"
          @click="bottomTab = 'explain'"
        >
          <BarChart3 class="h-3 w-3" /> Explain
          <span v-if="currentExplain?.success" class="text-[10px] text-slate-400">
            · {{ currentExplain.rows.length }} 行 · {{ formatElapsed(currentExplain.elapsed_ms) }}
          </span>
        </button>
        <button
          class="inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-xs"
          :class="bottomTab === 'templates' ? 'bg-primary-light text-primary font-semibold' : 'text-slate-500 hover:bg-slate-50'"
          @click="bottomTab = 'templates'"
        >
          <Bookmark class="h-3 w-3" /> 模板
          <span v-if="templates.length" class="text-[10px] text-slate-400">· {{ templates.length }}</span>
        </button>
        <!-- metadata 缓存时间徽章 + 刷新按钮 -->
        <span
          v-if="bottomTab === 'metadata' && activeConsole?.datasource_id"
          class="ml-auto flex items-center gap-2"
        >
          <span
            v-if="currentMeta?.schemasCachedAt"
            class="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500 sql-font"
            :title="'缓存于 ' + currentMeta.schemasCachedAt"
          >
            缓存于 {{ formatCacheTime(currentMeta.schemasCachedAt) }}
          </span>
          <span
            v-else
            class="rounded bg-status-warning-bg px-1.5 py-0.5 text-[10px] text-status-warning"
            title="未缓存,首次加载会从数据库拉"
          >
            未缓存
          </span>
          <button
            class="inline-flex items-center gap-1 rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
            title="刷新元数据(只刷 schemas 列表,tables 留给展开时按需拉 - 老行为)"
            @click="refreshMetadata"
          >
            <RefreshCw class="h-3 w-3" />
          </button>
          <button
            class="inline-flex items-center gap-1 rounded p-1 text-primary hover:bg-primary-light"
            title="重新加载所有对象 (清缓存 + 拉所有 schemas/tables/字段,DataGrip 风格)"
            :disabled="currentReloadProgress?.active"
            @click="reloadAllObjects"
          >
            <RefreshCw class="h-3 w-3" :class="{ 'animate-spin': currentReloadProgress?.active }" />
            <span class="text-[10px] font-bold">全量</span>
          </button>
        </span>
      </div>

      <!-- 全量重新加载进度条 -->
      <div
        v-if="currentReloadProgress?.active"
        class="mb-2 rounded-md border border-primary/30 bg-primary-light/40 p-2 text-[11px]"
      >
        <div class="flex items-center justify-between gap-2 mb-1">
          <span class="font-semibold text-primary">
            正在重新加载 ({{ currentReloadProgress.done }}/{{ currentReloadProgress.total }})
          </span>
          <span class="text-primary/70 truncate">{{ currentReloadProgress.currentSchema }}</span>
        </div>
        <div class="h-1.5 w-full overflow-hidden rounded-full bg-primary/15">
          <div
            class="h-full bg-primary transition-all"
            :style="{ width: currentReloadProgress.total ? `${Math.round(currentReloadProgress.done / currentReloadProgress.total * 100)}%` : '0%' }"
          ></div>
        </div>
        <div v-if="currentReloadProgress.failedSchemas.length" class="mt-1 text-[10px] text-status-warning">
          失败: {{ currentReloadProgress.failedSchemas.join(', ') }}
        </div>
      </div>

      <!-- result -->
      <div v-if="bottomTab === 'result'" class="flex-1 min-h-0 overflow-auto">
        <!-- v0.5 慢 SQL 提示条:执行成功 + 耗时 ≥ 3s 才显示 -->
        <div
          v-if="currentResult?.success && isSlow(currentResult.elapsed_ms)"
          class="mx-3 mt-3 flex items-center justify-between gap-3 rounded-md border border-status-warning/40 bg-status-warning-bg px-3 py-2 text-xs"
        >
          <span class="text-slate-700">
            ⚡ 本次执行耗时 <strong>{{ formatElapsed(currentResult.elapsed_ms) }}</strong>,SQL 可能需要优化。
          </span>
          <button
            class="inline-flex items-center gap-1 rounded bg-primary px-2.5 py-1 text-white text-[11px] hover:bg-primary-hover"
            @click="sendTo('diagnosis')"
          >
            <Microscope class="h-3 w-3" />
            发送到优化工作台 →
          </button>
        </div>
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

      <!-- explain panel(v0.5 抽到独立组件,加 hints + 复制按钮) -->
      <div v-else-if="bottomTab === 'explain'" class="flex-1 min-h-0 overflow-auto">
        <ExplainPanel :explain="currentExplain" />
      </div>

      <!-- metadata tree -->
      <div v-else-if="bottomTab === 'metadata'" class="flex-1 min-h-0 overflow-auto p-3">
        <div v-if="!activeConsole?.datasource_id" class="py-10 text-center text-sm text-slate-400">
          请先选择数据源
        </div>
        <template v-else>
          <!-- 搜索框(v0.3)-->
          <div class="relative mb-3">
            <Search class="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-slate-400" />
            <input
              v-model="searchQuery"
              type="text"
              placeholder="搜表 / 列 / 视图(空格分隔多关键字 AND 命中)"
              class="w-full pl-7 pr-7 text-xs sql-font"
            />
            <button
              v-if="searchQuery"
              class="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-700"
              title="清空"
              @click="searchQuery = ''"
            >
              <X class="h-3 w-3" />
            </button>
          </div>

          <!-- 搜索结果列表(searchQuery 非空时盖在树上) -->
          <div v-if="searchQuery.trim()" class="space-y-0.5">
            <div v-if="currentSearchLoading" class="py-4 text-center text-[11px] text-slate-400">搜索中…</div>
            <div v-else-if="!currentSearchResults.length" class="py-4 text-center text-[11px] text-slate-400">
              无匹配。<span v-if="!currentMeta?.schemasCachedAt">先展开几个 schema,搜索基于缓存。</span>
            </div>
            <button
              v-for="r in currentSearchResults"
              :key="r.kind + '::' + r.schema + '::' + r.table + '::' + (r.column || r.view || '')"
              class="flex items-center gap-2 w-full rounded px-1.5 py-1 text-left hover:bg-primary-light/40 group"
              @click="jumpToSearchResult(r)"
            >
              <span
                class="rounded px-1 py-0.5 text-[9px] font-bold uppercase"
                :class="{
                  'bg-tag-source-bg text-tag-source': r.kind === 'table',
                  'bg-tag-intermediate-bg text-tag-intermediate': r.kind === 'column',
                  'bg-tag-reference-bg text-tag-reference': r.kind === 'view',
                }"
              >{{ r.kind }}</span>
              <span class="sql-font text-xs text-slate-700 truncate flex-1" :title="r.snippet">{{ r.snippet }}</span>
              <span v-if="r.data_type" class="text-[10px] text-slate-400 sql-font">{{ r.data_type }}</span>
            </button>
          </div>

          <!-- 树状视图(无搜索时)-->
          <template v-else>
            <div v-if="currentMeta?.loading" class="py-10 text-center text-sm text-slate-400">
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
                  <span v-if="s.tablesCachedAt" class="ml-1 text-[10px] text-slate-300 sql-font" :title="'tables 缓存于 ' + s.tablesCachedAt">
                    · {{ formatCacheTime(s.tablesCachedAt) }}
                  </span>
                </button>
                <div v-if="s.expanded" class="ml-5 mt-0.5 space-y-0.5">
                  <div v-if="s.loading" class="text-[11px] text-slate-400 py-1">加载表…</div>
                  <div v-else-if="!s.tables?.length" class="text-[11px] text-slate-400 py-1 italic">空 schema</div>
                  <div
                    v-for="t in s.tables"
                    :key="t.name"
                    class="flex items-center gap-1.5 w-full rounded px-1.5 py-0.5 hover:bg-primary-light/40 text-left group transition"
                    :class="highlightedTableKey === s.name + '::' + t.name ? 'bg-primary-light ring-1 ring-primary' : ''"
                  >
                    <button
                      class="flex items-center gap-1.5 flex-1 text-left"
                      :title="'点击插入 SELECT * FROM ' + s.name + '.' + t.name"
                      @click="onTableClick(s.name, t.name)"
                    >
                      <Table2 class="h-3 w-3 text-slate-400 group-hover:text-primary" />
                      <span class="sql-font text-slate-700 group-hover:text-primary">{{ t.name }}</span>
                    </button>
                    <button
                      class="opacity-0 group-hover:opacity-100 p-0.5 rounded hover:bg-slate-200"
                      :title="'打开表详情(字段/索引/DDL)'"
                      @click.stop="openTableDetail(s.name, t.name)"
                    >
                      <Info class="h-3 w-3 text-slate-400 hover:text-primary" />
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </template>
        </template>
      </div>

      <!-- templates(v0.4 SQL 模板库) -->
      <div v-else-if="bottomTab === 'templates'" class="flex-1 min-h-0 overflow-auto p-3">
        <!-- 工具栏:过滤 + 导入 / 导出 -->
        <div class="flex flex-wrap items-center gap-2 mb-3">
          <div class="relative flex-1 min-w-40">
            <Search class="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-slate-400" />
            <input
              v-model="templateFilters.q"
              type="text"
              placeholder="搜模板(名称 / 描述 / SQL 内容)"
              class="w-full pl-7 pr-3 text-xs"
            />
          </div>
          <input
            v-model="templateFilters.tag"
            type="text"
            placeholder="标签(逗号分隔)"
            class="text-xs w-32"
            title="多个标签 AND 命中"
          />
          <select v-model="templateFilters.db_type" class="text-xs w-28">
            <option value="">所有方言</option>
            <option value="mysql">MySQL</option>
            <option value="oracle">Oracle</option>
            <option value="dm">DM 达梦</option>
            <option value="ob_mysql">OB MySQL</option>
            <option value="ob_oracle">OB Oracle</option>
            <option value="db2">DB2</option>
          </select>
          <button
            class="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-50"
            title="从 JSON 文件导入模板"
            @click="onImportClick"
          >
            <Upload class="h-3 w-3" /> 导入
          </button>
          <button
            class="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-50"
            title="把所有用户模板导出为 JSON 文件"
            @click="onExportClick(false)"
          >
            <Download class="h-3 w-3" /> 导出
          </button>
        </div>

        <!-- list -->
        <div v-if="templatesLoading" class="py-10 text-center text-sm text-slate-400">
          加载中…
        </div>
        <div v-else-if="!templates.length" class="py-10 text-center text-sm text-slate-400">
          <div>无匹配模板。</div>
          <div class="mt-2 text-[11px]">点 SQL 编辑器上方的「存为模板」按钮把当前 SQL 保存。</div>
        </div>
        <div v-else class="space-y-1.5">
          <div
            v-for="t in templates"
            :key="t.id"
            class="rounded-md border border-slate-200 p-2.5 hover:border-primary hover:bg-primary-light/20 transition group"
          >
            <div class="flex items-start gap-2">
              <div class="flex-1 min-w-0">
                <div class="flex items-center gap-2 flex-wrap mb-1">
                  <span class="text-sm font-semibold text-slate-800 truncate">{{ t.name }}</span>
                  <span
                    v-if="t.builtin"
                    class="rounded bg-primary-light text-primary px-1.5 py-0.5 text-[9px] font-bold uppercase"
                  >内置</span>
                  <span
                    v-if="t.risk_level === 'high'"
                    class="rounded bg-status-error-bg text-status-error px-1.5 py-0.5 text-[9px] font-bold uppercase"
                  >高风险</span>
                  <span
                    v-else-if="t.risk_level === 'medium'"
                    class="rounded bg-status-warning-bg text-status-warning px-1.5 py-0.5 text-[9px] font-bold uppercase"
                  >中风险</span>
                  <span
                    v-for="tag in t.tags"
                    :key="tag"
                    class="rounded bg-slate-100 text-slate-600 px-1.5 py-0.5 text-[10px]"
                  >{{ tag }}</span>
                  <span v-if="t.db_types.length" class="text-[10px] text-slate-400 sql-font">
                    {{ t.db_types.join(' / ') }}
                  </span>
                </div>
                <p v-if="t.description" class="text-xs text-slate-500 mb-1">{{ t.description }}</p>
                <pre class="sql-font text-[11px] text-slate-600 bg-slate-50 rounded p-1.5 whitespace-pre-wrap break-all max-h-20 overflow-y-auto">{{ t.sql }}</pre>
              </div>
              <div class="flex flex-col gap-1 shrink-0 opacity-0 group-hover:opacity-100 transition">
                <button
                  class="inline-flex items-center gap-1 rounded bg-primary text-white px-2 py-1 text-[11px] hover:bg-primary-hover"
                  title="插入到当前 Console"
                  @click="insertTemplateToConsole(t)"
                >
                  <Plus class="h-3 w-3" /> 插入
                </button>
                <button
                  v-if="!t.builtin"
                  class="inline-flex items-center gap-1 rounded border border-slate-200 bg-white px-2 py-1 text-[11px] text-slate-600 hover:bg-slate-50"
                  title="编辑该模板"
                  @click="openEditTemplateModal(t)"
                >
                  <Pencil class="h-3 w-3" /> 编辑
                </button>
                <button
                  v-else
                  class="inline-flex items-center gap-1 rounded border border-slate-200 bg-white px-2 py-1 text-[11px] text-slate-600 hover:bg-slate-50"
                  title="基于该内置模板克隆一个新模板"
                  @click="cloneTemplateToDraft(t)"
                >
                  <Pencil class="h-3 w-3" /> 克隆
                </button>
                <button
                  v-if="!t.builtin"
                  class="inline-flex items-center gap-1 rounded border border-status-error/30 bg-white px-2 py-1 text-[11px] text-status-error hover:bg-status-error-bg"
                  title="删除该模板"
                  @click="deleteTemplateConfirm(t)"
                >
                  <Trash2 class="h-3 w-3" />
                </button>
              </div>
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
              <td class="text-slate-500">
                <span :class="isSlow(h.elapsed_ms) ? 'text-status-warning font-semibold' : ''">
                  {{ formatElapsed(h.elapsed_ms) }}
                </span>
                <span
                  v-if="isSlow(h.elapsed_ms)"
                  class="ml-1 rounded bg-status-warning-bg text-status-warning px-1 py-0.5 text-[9px] font-bold"
                  title="慢 SQL — 耗时 ≥ 3 秒"
                >⚡SLOW</span>
              </td>
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

    <!-- 表详情 drawer(v0.3)—— 右侧浮层,显示字段 / 索引 / DDL 三 tab -->
    <div
      v-if="tableDetail"
      class="fixed inset-0 z-30 bg-slate-900/30"
      @click.self="closeTableDetail"
    >
      <div class="absolute right-0 top-0 bottom-0 w-[640px] max-w-full bg-white shadow-2xl flex flex-col">
        <!-- header -->
        <div class="flex items-center justify-between border-b border-slate-200 px-4 py-2.5">
          <div class="flex items-center gap-2">
            <Table2 class="h-4 w-4 text-primary" />
            <span class="sql-font font-semibold text-slate-800">
              {{ tableDetail.schema ? tableDetail.schema + '.' : '' }}{{ tableDetail.table }}
            </span>
          </div>
          <button class="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700" @click="closeTableDetail">
            <X class="h-4 w-4" />
          </button>
        </div>

        <!-- tabs -->
        <div class="flex items-center gap-1 border-b border-slate-100 px-3 py-1.5">
          <button
            class="inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-xs"
            :class="tableDetail.detailTab === 'columns' ? 'bg-primary-light text-primary font-semibold' : 'text-slate-500 hover:bg-slate-50'"
            @click="tableDetail.detailTab = 'columns'"
          >
            <Columns3 class="h-3 w-3" /> 字段 ({{ tableDetail.columns.length }})
          </button>
          <button
            class="inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-xs"
            :class="tableDetail.detailTab === 'indexes' ? 'bg-primary-light text-primary font-semibold' : 'text-slate-500 hover:bg-slate-50'"
            @click="tableDetail.detailTab = 'indexes'"
          >
            <Eye class="h-3 w-3" /> 索引 ({{ tableDetail.indexes.length }})
          </button>
          <button
            class="inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-xs"
            :class="tableDetail.detailTab === 'ddl' ? 'bg-primary-light text-primary font-semibold' : 'text-slate-500 hover:bg-slate-50'"
            @click="tableDetail.detailTab = 'ddl'"
          >
            <FileText class="h-3 w-3" /> DDL
          </button>
        </div>

        <!-- body -->
        <div class="flex-1 min-h-0 overflow-auto">
          <div v-if="tableDetail.loading" class="py-10 text-center text-sm text-slate-400">
            加载中…
          </div>

          <!-- columns tab -->
          <table v-else-if="tableDetail.detailTab === 'columns'" class="text-xs w-full">
            <thead class="bg-slate-50 sticky top-0">
              <tr>
                <th class="text-left whitespace-nowrap w-8">#</th>
                <th class="text-left whitespace-nowrap">字段名</th>
                <th class="text-left whitespace-nowrap">类型</th>
                <th class="text-left whitespace-nowrap w-16">可空</th>
                <th class="text-left whitespace-nowrap">注释</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="!tableDetail.columns.length"><td colspan="5" class="text-center py-6 text-slate-400">无字段信息</td></tr>
              <tr v-for="(c, i) in tableDetail.columns" :key="c.name + i" class="hover:bg-slate-50">
                <td class="text-slate-400 sql-font">{{ c.ordinal || i + 1 }}</td>
                <td class="sql-font text-slate-700 font-semibold">{{ c.name }}</td>
                <td class="sql-font text-slate-600">{{ c.data_type }}</td>
                <td class="text-slate-500">{{ c.nullable === 'Y' || c.nullable === 'YES' ? '是' : '否' }}</td>
                <td class="text-slate-500 max-w-xs truncate" :title="c.comment">{{ c.comment }}</td>
              </tr>
            </tbody>
          </table>

          <!-- indexes tab -->
          <div v-else-if="tableDetail.detailTab === 'indexes'">
            <table class="text-xs w-full">
              <thead class="bg-slate-50 sticky top-0">
                <tr>
                  <th class="text-left whitespace-nowrap">索引名</th>
                  <th class="text-left whitespace-nowrap">列</th>
                  <th class="text-left whitespace-nowrap w-12">序</th>
                  <th class="text-left whitespace-nowrap w-16">唯一</th>
                  <th class="text-left whitespace-nowrap">类型</th>
                </tr>
              </thead>
              <tbody>
                <tr v-if="!tableDetail.indexes.length"><td colspan="5" class="text-center py-6 text-slate-400">无索引信息(或方言不支持)</td></tr>
                <tr v-for="(idx, i) in tableDetail.indexes" :key="idx.index_name + idx.column_name + i" class="hover:bg-slate-50">
                  <td class="sql-font text-slate-700">{{ idx.index_name }}</td>
                  <td class="sql-font text-slate-700 font-semibold">{{ idx.column_name }}</td>
                  <td class="text-slate-500">{{ idx.seq_in_index }}</td>
                  <td>
                    <span
                      class="rounded px-1.5 py-0.5 text-[10px] font-bold"
                      :class="idx.non_unique === 0 ? 'bg-status-success-bg text-status-success' : 'bg-slate-100 text-slate-500'"
                    >
                      {{ idx.non_unique === 0 ? 'UNIQUE' : '普通' }}
                    </span>
                  </td>
                  <td class="text-slate-500 sql-font">{{ idx.index_type }}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <!-- ddl tab -->
          <div v-else class="p-3">
            <div v-if="!tableDetail.ddlSupported" class="rounded border border-status-warning/30 bg-status-warning-bg p-3 text-xs text-status-warning">
              该方言暂不支持 DDL 抽取。MySQL 走 SHOW CREATE TABLE,Oracle/DM 需 DBA 权限走 DBMS_METADATA(后续切片)。
            </div>
            <pre v-else class="sql-font text-xs text-slate-700 bg-slate-50 rounded p-3 whitespace-pre-wrap break-all">{{ tableDetail.ddl }}</pre>
          </div>
        </div>
      </div>
    </div>

    <!-- 保存为模板 modal (v0.4) —— 同时给"新建"和"编辑"用 -->
    <div
      v-if="showSaveTemplateModal"
      class="fixed inset-0 z-40 bg-slate-900/40 flex items-center justify-center p-4"
      @click.self="showSaveTemplateModal = false"
    >
      <div class="bg-white rounded-xl shadow-2xl w-[640px] max-w-full max-h-[90vh] flex flex-col">
        <div class="flex items-center justify-between border-b border-slate-200 px-4 py-3">
          <div class="flex items-center gap-2">
            <BookmarkPlus class="h-4 w-4 text-primary" />
            <h3 class="font-semibold text-slate-800">{{ saveTemplateDraft.id ? '编辑模板' : '保存为模板' }}</h3>
          </div>
          <button class="rounded p-1 text-slate-400 hover:bg-slate-100" @click="showSaveTemplateModal = false">
            <X class="h-4 w-4" />
          </button>
        </div>
        <div class="flex-1 min-h-0 overflow-auto p-4 space-y-3">
          <div>
            <label class="block text-xs font-semibold text-slate-700 mb-1">名称 <span class="text-status-error">*</span></label>
            <input v-model="saveTemplateDraft.name" type="text" class="w-full text-sm" placeholder="如:大客户订单 Top 10" />
          </div>
          <div>
            <label class="block text-xs font-semibold text-slate-700 mb-1">描述</label>
            <textarea v-model="saveTemplateDraft.description" class="w-full text-xs" rows="2" placeholder="一句话说明用途 / 注意事项"></textarea>
          </div>
          <div class="grid grid-cols-2 gap-3">
            <div>
              <label class="block text-xs font-semibold text-slate-700 mb-1">标签</label>
              <input v-model="saveTemplateDraft.tagsText" type="text" class="w-full text-xs" placeholder="逗号分隔,如:统计, 日报" />
            </div>
            <div>
              <label class="block text-xs font-semibold text-slate-700 mb-1">数据库类型</label>
              <input v-model="saveTemplateDraft.db_typesText" type="text" class="w-full text-xs sql-font" placeholder="mysql, oracle 或 all" />
            </div>
          </div>
          <div>
            <label class="block text-xs font-semibold text-slate-700 mb-1">风险等级</label>
            <div class="flex gap-3 text-xs">
              <label class="flex items-center gap-1 cursor-pointer">
                <input type="radio" v-model="saveTemplateDraft.risk_level" value="low" /> 低 <span class="text-slate-400">(纯查询)</span>
              </label>
              <label class="flex items-center gap-1 cursor-pointer">
                <input type="radio" v-model="saveTemplateDraft.risk_level" value="medium" /> 中 <span class="text-slate-400">(大表扫描)</span>
              </label>
              <label class="flex items-center gap-1 cursor-pointer">
                <input type="radio" v-model="saveTemplateDraft.risk_level" value="high" /> 高 <span class="text-slate-400">(慎用)</span>
              </label>
            </div>
          </div>
          <div>
            <label class="block text-xs font-semibold text-slate-700 mb-1">SQL <span class="text-status-error">*</span></label>
            <textarea v-model="saveTemplateDraft.sql" class="w-full text-xs sql-font" rows="8"></textarea>
          </div>
        </div>
        <div class="flex items-center justify-end gap-2 border-t border-slate-200 px-4 py-3">
          <button class="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50" @click="showSaveTemplateModal = false">
            取消
          </button>
          <button class="inline-flex items-center gap-1 rounded-md bg-primary text-white px-3 py-1.5 text-xs font-bold hover:bg-primary-hover" @click="onSubmitSaveTemplate">
            <Save class="h-3 w-3" />
            {{ saveTemplateDraft.id ? '更新' : '保存' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 包 A.2:全表扫描风险确认弹窗 —— 后端检测到 SELECT * 无 WHERE 无 LIMIT
         等情况时返 requires_confirmation,弹此框让用户决定是否继续。继续 → 带
         confirm_full_scan=true 重提;取消 → 放弃 export -->
    <div
      v-if="fullScanConfirm.visible"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      @click.self="cancelFullScanExport"
    >
      <div class="bg-white rounded-lg shadow-xl max-w-md w-full mx-4 p-5">
        <div class="flex items-start gap-3 mb-4">
          <div class="flex-shrink-0 w-10 h-10 rounded-full bg-amber-100 flex items-center justify-center">
            <span class="text-amber-600 text-xl">⚠</span>
          </div>
          <div class="flex-1">
            <h3 class="text-base font-bold text-slate-900 mb-1">全表扫描风险确认</h3>
            <p class="text-xs text-slate-600">
              系统检测到该 SQL 可能扫描全表,导出数据量可能非常大,确认继续?
            </p>
          </div>
        </div>
        <div class="bg-amber-50 border border-amber-200 rounded-md p-3 mb-4">
          <ul class="text-xs text-amber-800 space-y-1">
            <li v-for="(w, i) in fullScanConfirm.warnings" :key="i">• {{ w }}</li>
          </ul>
        </div>
        <p class="text-xs text-slate-500 mb-4">
          建议:加 <code class="bg-slate-100 px-1 rounded">WHERE</code> 过滤或
          <code class="bg-slate-100 px-1 rounded">LIMIT N</code> 限行后再导出。
        </p>
        <div class="flex justify-end gap-2">
          <button
            class="px-3 py-1.5 text-xs rounded-md border border-slate-300 text-slate-700 hover:bg-slate-50"
            @click="cancelFullScanExport"
          >
            取消
          </button>
          <button
            class="px-3 py-1.5 text-xs rounded-md bg-amber-600 text-white font-bold hover:bg-amber-700"
            @click="confirmFullScanExport"
          >
            继续导出
          </button>
        </div>
      </div>
    </div>
  </section>
</template>
