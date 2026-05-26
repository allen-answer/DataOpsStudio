<script setup>
import { onMounted, onUnmounted, ref, watch, computed } from 'vue'
import { basicSetup, EditorView } from 'codemirror'
import { EditorState, Compartment } from '@codemirror/state'
import { sql, MySQL, PLSQL, StandardSQL } from '@codemirror/lang-sql'
import { autocompletion, snippetCompletion } from '@codemirror/autocomplete'
import { oneDark } from '@codemirror/theme-one-dark'
import { placeholder as cmPlaceholder } from '@codemirror/view'
import { Maximize2, Minimize2 } from 'lucide-vue-next'

const props = defineProps({
  modelValue: { type: String, default: '' },
  placeholder: { type: String, default: '' },
  // 普通模式下编辑器固定可视高度。内容超出走编辑器内部滚动，不再撑高整页
  // —— 数据对比工作台和血缘分析工作台共用此组件，长 SQL 都不会顶飞页面布局。
  height: { type: String, default: '300px' },
  // SQL Workbench v0.2 起增加的可选补全/snippet 配置。这些 prop **不设默认值时
  // 一律 no-op**,确保 LineageView / WorkbenchView / WorkflowDetailView 等老调用点
  // 行为不变。
  // dialect: 上游业务的数据库方言名(mysql/oracle/dm/dameng/ob_mysql/ob_oracle)。
  //   内部按 CLAUDE.md 的方言映射规则路由到 lang-sql 的 MySQL / PLSQL。
  dialect: { type: String, default: '' },
  // completionSchema: { 'schema.table': ['col1','col2', ...] } 或 { table: [...] }。
  //   直接喂给 lang-sql 的 sql({schema:...}) 让它做 schema/table/column 补全。
  completionSchema: { type: Object, default: null },
  // snippets: true 时注册 6 个常用 SQL 片段(SELECT * / COUNT / WHERE / JOIN /
  //   GROUP BY / ORDER BY)到 autocomplete。
  snippets: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue'])

const container = ref(null)
const fullscreen = ref(false)
let view = null
// Compartment 允许在 dialect/schema 变化时不 destroy/recreate 整个 EditorView。
const languageCompartment = new Compartment()

// height:100% 让 .cm-editor 撑满外层容器；外层容器高度决定可视区，
// .cm-scroller overflow:auto 让超长 SQL 在编辑器内部滚动而非撑高页面。
const editorTheme = EditorView.theme({
  '&': { height: '100%', fontSize: '13px' },
  '.cm-scroller': {
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
    overflow: 'auto',
  },
  '.cm-content': { padding: '20px' },
  '.cm-focused': { outline: 'none' },
})

// 6 个 SQL 片段。${N} / ${N:default} 是 CodeMirror snippetCompletion 的 tabstop 语法。
const SQL_SNIPPETS = [
  snippetCompletion('SELECT *\nFROM ${table}\nWHERE ${1};', {
    label: 'SELECT *', detail: '查询全部列', type: 'snippet', boost: 5,
  }),
  snippetCompletion('COUNT(${1:*})', {
    label: 'COUNT', detail: '行数统计', type: 'snippet', boost: 4,
  }),
  snippetCompletion('WHERE ${1}', {
    label: 'WHERE', detail: '条件过滤', type: 'snippet', boost: 4,
  }),
  snippetCompletion('JOIN ${table} ON ${1}', {
    label: 'JOIN', detail: '关联表', type: 'snippet', boost: 4,
  }),
  snippetCompletion('GROUP BY ${1}', {
    label: 'GROUP BY', detail: '分组', type: 'snippet', boost: 4,
  }),
  snippetCompletion('ORDER BY ${1}', {
    label: 'ORDER BY', detail: '排序', type: 'snippet', boost: 4,
  }),
]

function snippetSource(context) {
  // 仅在用户主动触发或正在敲单词时弹片段,避免空白处误干扰。
  const word = context.matchBefore(/\w*/)
  if (!word || (word.from === word.to && !context.explicit)) return null
  return { from: word.from, options: SQL_SNIPPETS, validFor: /^\w*$/ }
}

// dialect 名 → CodeMirror lang-sql dialect 实例。规则跟根 CLAUDE.md 一致:
//   mysql / ob_mysql / oceanbase  → MySQL
//   oracle / dm / dameng / ob_oracle → PLSQL(Oracle 方言)
//   其他空值 → StandardSQL
function resolveCmDialect(name) {
  const n = String(name || '').toLowerCase()
  if (n === 'mysql' || n === 'ob_mysql' || n === 'oceanbase') return MySQL
  if (n === 'oracle' || n === 'dm' || n === 'dameng' || n === 'ob_oracle') return PLSQL
  return StandardSQL
}

function buildLanguageExtension() {
  const dialect = resolveCmDialect(props.dialect)
  const sqlConfig = { dialect, upperCaseKeywords: true }
  if (props.completionSchema && typeof props.completionSchema === 'object') {
    sqlConfig.schema = props.completionSchema
  }
  const exts = [sql(sqlConfig)]
  if (props.snippets) {
    // 把 snippet source 接到 autocomplete facet 上。@codemirror/lang-sql 已经
    // 通过 language data 注册了 keyword + schema 的 source,我们这里追加一条
    // 独立 source 而不 override,让两者共存。
    exts.push(autocompletion({ override: null, defaultKeymap: true, activateOnTyping: true }))
    exts.push(EditorState.languageData.of(() => [{ autocomplete: snippetSource }]))
  }
  return exts
}

function onKeydown(e) {
  if (e.key === 'Escape' && fullscreen.value) fullscreen.value = false
}

onMounted(() => {
  const extensions = [
    basicSetup,
    languageCompartment.of(buildLanguageExtension()),
    oneDark,
    editorTheme,
    EditorView.lineWrapping,
    EditorView.updateListener.of((update) => {
      if (update.docChanged) {
        emit('update:modelValue', update.state.doc.toString())
      }
    }),
  ]
  if (props.placeholder) extensions.push(cmPlaceholder(props.placeholder))

  view = new EditorView({
    state: EditorState.create({ doc: props.modelValue, extensions }),
    parent: container.value,
  })
  document.addEventListener('keydown', onKeydown)
})

onUnmounted(() => {
  view?.destroy()
  document.removeEventListener('keydown', onKeydown)
  document.body.style.overflow = ''
})

watch(() => props.modelValue, (val) => {
  if (view && val !== view.state.doc.toString()) {
    view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: val } })
  }
})

// dialect / completionSchema / snippets 变化时,reconfigure 语言扩展而不重建编辑器。
watch(() => [props.dialect, props.completionSchema, props.snippets], () => {
  if (!view) return
  view.dispatch({ effects: languageCompartment.reconfigure(buildLanguageExtension()) })
}, { deep: true })

// 全屏时锁背景滚动，退出 / 卸载时还原。
watch(fullscreen, (on) => {
  document.body.style.overflow = on ? 'hidden' : ''
})
</script>

<template>
  <!-- 全屏时整体 fixed 覆盖视口;普通时正常流。全屏按钮挪到顶部细工具栏,
       不再 absolute 浮在编辑器右上角(避免遮挡 SQL 第一行内容)。
       !m-0 抵消父级 space-y-* 给本 div 的 margin-top —— 否则 fixed 层会被顶下来。 -->
  <div :class="fullscreen ? 'fixed inset-0 z-50 !m-0 flex flex-col bg-slate-900/85 p-3 backdrop-blur-sm' : ''">
    <!-- 顶部工具栏:左侧 label,右侧全屏切换。普通模式只 24px 高,不挤页面 -->
    <div
      class="flex items-center justify-between rounded-t-2xl border-x-4 border-t-4 border-slate-900 bg-slate-900 px-2.5 py-1"
      :class="fullscreen ? '' : ''"
    >
      <span class="text-[11px] font-semibold text-slate-300">
        <template v-if="fullscreen">SQL 编辑器 · 全屏(Esc 退出)</template>
        <template v-else>SQL</template>
      </span>
      <button
        type="button"
        class="flex items-center gap-1 rounded px-2 py-0.5 text-[11px] font-semibold text-slate-200 transition hover:bg-slate-700 hover:text-white"
        :title="fullscreen ? '退出全屏 (Esc)' : '全屏编辑 SQL'"
        @click="fullscreen = !fullscreen"
      >
        <component :is="fullscreen ? Minimize2 : Maximize2" class="h-3.5 w-3.5" />
        {{ fullscreen ? '退出全屏' : '全屏' }}
      </button>
    </div>

    <div
      ref="container"
      class="overflow-hidden rounded-b-2xl border-x-4 border-b-4 border-slate-900"
      :class="fullscreen ? 'min-h-0 flex-1' : ''"
      :style="fullscreen ? undefined : `height:${height}`"
    ></div>
  </div>
</template>
