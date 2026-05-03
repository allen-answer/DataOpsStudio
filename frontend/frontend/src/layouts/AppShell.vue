<script setup>
import { inject } from 'vue'
import AppSidebar from './AppSidebar.vue'
import AppTopBar from './AppTopBar.vue'

defineProps({
  loading: { type: Boolean, default: false },
})

defineEmits(['confirm-include-passwords'])

// 全局 notice 由 App.vue 通过 provide 共享，所有 view 复用同一通知槽位
const { notice } = inject('app')
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

        <!-- Page content -->
        <slot />
      </div>
    </main>
  </div>
</template>
