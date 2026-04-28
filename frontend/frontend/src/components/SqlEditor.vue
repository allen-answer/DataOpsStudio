<script setup>
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { basicSetup, EditorView } from 'codemirror'
import { EditorState } from '@codemirror/state'
import { sql } from '@codemirror/lang-sql'
import { oneDark } from '@codemirror/theme-one-dark'
import { placeholder as cmPlaceholder } from '@codemirror/view'

const props = defineProps({
  modelValue: { type: String, default: '' },
  placeholder: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue'])

const container = ref(null)
let view = null

const editorTheme = EditorView.theme({
  '&': { borderRadius: '1rem', fontSize: '13px' },
  '.cm-scroller': {
    minHeight: '300px',
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
    overflowX: 'auto',
  },
  '.cm-content': { padding: '20px' },
  '.cm-focused': { outline: 'none' },
})

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
})

onUnmounted(() => view?.destroy())

watch(() => props.modelValue, (val) => {
  if (view && val !== view.state.doc.toString()) {
    view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: val } })
  }
})
</script>

<template>
  <div ref="container" class="overflow-hidden rounded-2xl border-4 border-slate-900"></div>
</template>
