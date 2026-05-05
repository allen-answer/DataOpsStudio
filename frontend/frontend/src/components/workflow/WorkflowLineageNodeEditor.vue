<script setup>
import { computed, ref } from 'vue'
import { apiForm } from '../../api'

const props = defineProps({
  node: { type: Object, required: true },
})

if (!props.node.input_mode) {
  props.node.input_mode = props.node.script_path ? 'uploaded_file' : 'inline_sql'
}

const uploading = ref(false)
const uploadError = ref('')

const accept = computed(() => props.node.input_mode === 'uploaded_zip' ? '.zip' : '.sql,.txt')
// uploadHint 走 i18n —— 模板里直接 $t() 取，避免 setup 里强依赖 i18n 实例

function setMode(mode) {
  props.node.input_mode = mode
  uploadError.value = ''
}

function clearUploadedScript() {
  props.node.script_path = ''
  props.node.script_filename = ''
  props.node.script_kind = ''
}

async function uploadLineageFile(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  uploadError.value = ''
  uploading.value = true
  try {
    const form = new FormData()
    form.append('file', file)
    const result = await apiForm('/api/uploads/lineage-script', form)
    props.node.script_path = result.path
    props.node.script_filename = result.filename
    props.node.script_kind = result.kind
    props.node.input_mode = result.kind === 'zip' ? 'uploaded_zip' : 'uploaded_file'
  } catch (error) {
    uploadError.value = error?.message || String(error || '上传失败')
  } finally {
    uploading.value = false
  }
}
</script>

<template>
  <div class="space-y-3">
    <div class="grid grid-cols-1 gap-2 lg:grid-cols-[minmax(0,1fr)_140px]">
      <label>
        <span class="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-slate-400">{{ $t('workflowEditor.lineage.inputMode') }}</span>
        <div class="inline-flex rounded-lg border border-slate-200 bg-slate-50 p-0.5 text-xs font-semibold">
          <button
            type="button"
            class="rounded-md px-2.5 py-1 transition"
            :class="node.input_mode === 'inline_sql' ? 'bg-white text-violet-700 shadow-sm' : 'text-slate-500 hover:text-slate-700'"
            @click="setMode('inline_sql')"
          >{{ $t('workflowEditor.lineage.modeInline') }}</button>
          <button
            type="button"
            class="rounded-md px-2.5 py-1 transition"
            :class="node.input_mode === 'uploaded_file' ? 'bg-white text-violet-700 shadow-sm' : 'text-slate-500 hover:text-slate-700'"
            @click="setMode('uploaded_file')"
          >{{ $t('workflowEditor.lineage.modeFile') }}</button>
          <button
            type="button"
            class="rounded-md px-2.5 py-1 transition"
            :class="node.input_mode === 'uploaded_zip' ? 'bg-white text-violet-700 shadow-sm' : 'text-slate-500 hover:text-slate-700'"
            @click="setMode('uploaded_zip')"
          >{{ $t('workflowEditor.lineage.modeZip') }}</button>
        </div>
      </label>
      <label>
        <span class="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-slate-400">{{ $t('workflowEditor.lineage.dialect') }}</span>
        <input v-model="node.dialect" class="w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 text-xs" placeholder="mysql / oracle / dm">
      </label>
    </div>

    <label class="inline-flex items-center gap-2 rounded-lg border border-violet-100 bg-violet-50/70 px-2.5 py-2 text-xs text-slate-700">
      <input v-model="node.ai_enabled" type="checkbox" class="h-3.5 w-3.5 rounded border-slate-300 text-violet-600">
      <span class="font-semibold">{{ $t('workflowEditor.lineage.aiAssist') }}</span>
      <span class="text-[11px] text-slate-500">{{ $t('workflowEditor.lineage.aiAssistHint') }}</span>
    </label>

    <label v-if="node.input_mode === 'inline_sql'">
      <span class="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-slate-400">{{ $t('workflowEditor.common.sql') }}</span>
      <textarea v-model="node.sql" class="block min-h-[76px] w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 font-mono text-[12px]" placeholder="SELECT * FROM ..."></textarea>
    </label>

    <div v-else class="rounded-lg border border-dashed border-slate-300 bg-slate-50 px-3 py-3">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <div class="min-w-0">
          <p class="text-xs font-semibold text-slate-700">{{ node.input_mode === 'uploaded_zip' ? $t('workflowEditor.lineage.labelZip') : $t('workflowEditor.lineage.labelFile') }}</p>
          <p class="mt-0.5 text-[11px] text-slate-500">{{ node.input_mode === 'uploaded_zip' ? $t('workflowEditor.lineage.uploadHintZip') : $t('workflowEditor.lineage.uploadHintFile') }}</p>
        </div>
        <label class="inline-flex h-8 cursor-pointer items-center rounded-lg border border-slate-200 bg-white px-3 text-xs font-semibold text-slate-700 transition hover:border-violet-300 hover:text-violet-700">
          {{ uploading ? $t('workflowEditor.lineage.uploading') : $t('workflowEditor.lineage.pickFile') }}
          <input type="file" :accept="accept" class="hidden" :disabled="uploading" @change="uploadLineageFile">
        </label>
      </div>

      <div v-if="node.script_path" class="mt-2 flex flex-wrap items-center gap-2 rounded-lg border border-emerald-200 bg-white px-2.5 py-2 text-xs">
        <span class="rounded bg-emerald-50 px-1.5 py-0.5 font-mono text-[10px] font-bold uppercase text-emerald-700">{{ node.script_kind || (node.input_mode === 'uploaded_zip' ? 'zip' : 'file') }}</span>
        <span class="font-semibold text-slate-700">{{ node.script_filename || node.script_path }}</span>
        <span class="min-w-0 truncate font-mono text-[10.5px] text-slate-400">{{ node.script_path }}</span>
        <button type="button" class="ml-auto rounded border border-slate-200 bg-white px-2 py-0.5 text-[10px] font-semibold text-slate-500 hover:bg-slate-100" @click="clearUploadedScript">{{ $t('workflowEditor.lineage.clearFile') }}</button>
      </div>
      <p v-if="uploadError" class="mt-2 rounded border border-rose-200 bg-rose-50 px-2 py-1.5 text-[11px] text-rose-700">{{ uploadError }}</p>
    </div>
  </div>
</template>
