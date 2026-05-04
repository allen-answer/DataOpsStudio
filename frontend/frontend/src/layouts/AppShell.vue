<script setup>
import { inject } from 'vue'
import { storeToRefs } from 'pinia'
import { Sparkles, X, AlertCircle } from 'lucide-vue-next'
import AppSidebar from './AppSidebar.vue'
import AppTopBar from './AppTopBar.vue'
import { useNoticeStore } from '../stores/notice'

defineProps({
  loading: { type: Boolean, default: false },
})

defineEmits(['confirm-include-passwords'])

// 全局 notice 由 App.vue 通过 provide 共享，所有 view 复用同一通知槽位
const { notice } = inject('app')
const noticeStore = useNoticeStore()
const { aiTranslation } = storeToRefs(noticeStore)
</script>

<template>
  <div class="flex h-screen overflow-hidden bg-canvas text-slate-900">
    <AppSidebar />

    <main class="flex min-w-0 flex-1 flex-col">
      <AppTopBar
        :loading="loading"
        @confirm-include-passwords="(e) => $emit('confirm-include-passwords', e)"
      >
        <template #actions>
          <slot name="actions" />
        </template>
      </AppTopBar>

      <div class="min-h-0 flex-1 overflow-y-auto bg-canvas">
        <!-- Global notice slot —— 蓝色提示条，跨 view 共享 -->
        <div
          v-if="notice"
          class="mx-6 mt-4 rounded-xl border border-status-info-bg bg-status-info-bg/50 px-4 py-2.5 text-sm font-medium text-status-info"
        >
          {{ notice }}
        </div>

        <!-- AI 错误翻译卡片：5xx / 长 4xx 报错时由 api.js → noticeStore 推送 -->
        <div
          v-if="aiTranslation"
          class="mx-6 mt-4 rounded-xl border-l-4 border-l-purple-500 border border-purple-200 bg-purple-50/60 p-4 shadow-sm"
        >
          <div class="flex items-start gap-3">
            <div class="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-purple-100 text-purple-700">
              <Sparkles class="h-4.5 w-4.5" />
            </div>
            <div class="flex-1 space-y-2 min-w-0">
              <div class="flex items-center justify-between gap-2">
                <p class="flex items-center gap-1.5 text-sm font-bold text-purple-900">
                  <AlertCircle class="h-3.5 w-3.5" />
                  AI 错误翻译
                </p>
                <button
                  class="shrink-0 rounded p-1 text-purple-400 hover:bg-purple-100 hover:text-purple-700"
                  title="关闭"
                  @click="noticeStore.dismissAITranslation()"
                >
                  <X class="h-3.5 w-3.5" />
                </button>
              </div>
              <p class="text-[13px] leading-relaxed text-slate-800">{{ aiTranslation.translation }}</p>
              <ul v-if="aiTranslation.suggestions?.length" class="space-y-1 text-[12px] text-slate-700">
                <li v-for="(s, i) in aiTranslation.suggestions" :key="i" class="flex items-start gap-1.5">
                  <span class="muted shrink-0 text-purple-600">•</span>
                  <span>{{ s }}</span>
                </li>
              </ul>
              <details class="text-[11px]">
                <summary class="muted cursor-pointer hover:text-slate-700">查看原错误</summary>
                <pre class="sql-font mt-1 max-h-32 overflow-auto rounded bg-white/60 p-2 text-[11px] text-slate-600">{{ aiTranslation.original }}</pre>
              </details>
            </div>
          </div>
        </div>

        <!-- Page content -->
        <slot />
      </div>
    </main>
  </div>
</template>
