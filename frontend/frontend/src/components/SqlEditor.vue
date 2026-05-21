<script setup>
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { basicSetup, EditorView } from 'codemirror'
import { EditorState } from '@codemirror/state'
import { sql } from '@codemirror/lang-sql'
import { oneDark } from '@codemirror/theme-one-dark'
import { placeholder as cmPlaceholder } from '@codemirror/view'
import { Maximize2, Minimize2 } from 'lucide-vue-next'

const props = defineProps({
  modelValue: { type: String, default: '' },
  placeholder: { type: String, default: '' },
  // 普通模式下编辑器固定可视高度。内容超出走编辑器内部滚动，不再撑高整页
  // —— 数据对比工作台和血缘分析工作台共用此组件，长 SQL 都不会顶飞页面布局。
  height: { type: String, default: '300px' },
})
const emit = defineEmits(['update:modelValue'])

const container = ref(null)
const fullscreen = ref(false)
let view = null

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

function onKeydown(e) {
  if (e.key === 'Escape' && fullscreen.value) fullscreen.value = false
}

onMounted(() => {
  const extensions = [
    basicSetup,
    sql(),
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

// 全屏时锁背景滚动，退出 / 卸载时还原。
watch(fullscreen, (on) => {
  document.body.style.overflow = on ? 'hidden' : ''
})
</script>

<template>
  <!-- 全屏时整体提到 fixed 顶层覆盖视口；普通时 relative 让全屏按钮浮在右上角。
       !m-0 抵消父级 space-y-* 给本 div 的 margin-top —— 否则 fixed 层会被顶下来一截。 -->
  <div :class="fullscreen ? 'fixed inset-0 z-50 !m-0 flex flex-col bg-slate-900/85 p-3 backdrop-blur-sm' : 'relative'">
    <div v-if="fullscreen" class="mb-2 px-1">
      <span class="text-xs font-semibold text-slate-300">SQL 编辑器 · 全屏（Esc 退出）</span>
    </div>

    <div
      ref="container"
      class="overflow-hidden rounded-2xl border-4 border-slate-900"
      :class="fullscreen ? 'min-h-0 flex-1' : ''"
      :style="fullscreen ? undefined : `height:${height}`"
    ></div>

    <!-- 全屏切换按钮：浮在编辑器右上角 -->
    <button
      type="button"
      class="absolute z-10 flex items-center gap-1 rounded-md bg-slate-700/90 px-2 py-1 text-[11px] font-semibold text-slate-100 shadow-sm ring-1 ring-slate-600 transition hover:bg-slate-600"
      :class="fullscreen ? 'right-5 top-5' : 'right-3 top-3'"
      :title="fullscreen ? '退出全屏 (Esc)' : '全屏编辑 SQL'"
      @click="fullscreen = !fullscreen"
    >
      <component :is="fullscreen ? Minimize2 : Maximize2" class="h-3.5 w-3.5" />
      {{ fullscreen ? '退出全屏' : '全屏' }}
    </button>
  </div>
</template>
