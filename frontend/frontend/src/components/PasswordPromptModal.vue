<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { ShieldCheck, X } from 'lucide-vue-next'
import { cancelPassword, confirmPassword, usePasswordPromptState } from '../composables/usePasswordPrompt'

const state = usePasswordPromptState()
const password = ref('')
const showPassword = ref(false)
const inputRef = ref<HTMLInputElement | null>(null)

watch(() => state.value.open, async (open) => {
  if (open) {
    password.value = ''
    showPassword.value = false
    await nextTick()
    inputRef.value?.focus()
  }
})

function onConfirm(): void {
  if (!password.value) return
  confirmPassword(password.value)
}

function onCancel(): void {
  cancelPassword()
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="state.open"
      class="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 backdrop-blur-sm"
      @click.self="onCancel"
    >
      <div class="w-full max-w-sm rounded-xl bg-white p-6 shadow-2xl">
        <div class="mb-3 flex items-start gap-3">
          <div class="grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-primary-light text-primary">
            <ShieldCheck class="h-5 w-5" />
          </div>
          <div class="flex-1 min-w-0">
            <h3 class="text-base font-bold text-slate-800">需要重新确认密码</h3>
            <p class="mt-0.5 text-xs leading-relaxed text-slate-500">{{ state.message }}</p>
          </div>
          <button
            type="button"
            class="shrink-0 rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
            title="取消"
            @click="onCancel"
          >
            <X class="h-4 w-4" />
          </button>
        </div>

        <div class="relative">
          <input
            ref="inputRef"
            v-model="password"
            :type="showPassword ? 'text' : 'password'"
            placeholder="当前账号密码"
            class="w-full rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 pr-14 text-sm font-mono focus:border-primary focus:bg-white focus:outline-none focus:ring-2 focus:ring-primary/20"
            @keydown.enter="onConfirm"
            @keydown.esc="onCancel"
          >
          <button
            type="button"
            class="absolute right-2 top-1/2 -translate-y-1/2 rounded px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider text-slate-400 hover:text-slate-700"
            @click="showPassword = !showPassword"
          >{{ showPassword ? '隐藏' : '显示' }}</button>
        </div>

        <div class="mt-5 flex justify-end gap-2">
          <button type="button" class="btn btn-outline h-9 px-4 text-xs" @click="onCancel">取消</button>
          <button type="button" class="btn btn-primary h-9 px-4 text-xs" :disabled="!password" @click="onConfirm">
            确认 (Enter)
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
