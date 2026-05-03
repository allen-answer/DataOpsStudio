<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ChevronRight, Search, FileDown, ShieldAlert, LogOut, User as UserIcon } from 'lucide-vue-next'
import CommandPalette from '../components/CommandPalette.vue'
import NotificationPopover from '../components/NotificationPopover.vue'
import { useAuthStore } from '../stores/auth'

const authStore = useAuthStore()
const userMenuOpen = ref(false)

const props = defineProps({
  // 路由 path → 面包屑文案的 map；若 view 想自定义动态面包屑，可通过 slot 覆盖
  breadcrumbs: { type: Array, default: null },
  loading: { type: Boolean, default: false },
})

defineEmits(['confirm-include-passwords'])

const route = useRoute()

// 命令面板（搜索按钮 + Ctrl/Cmd+K）
const paletteOpen = ref(false)

function onGlobalKey(event) {
  if ((event.metaKey || event.ctrlKey) && (event.key === 'k' || event.key === 'K')) {
    event.preventDefault()
    paletteOpen.value = true
  }
}

onMounted(() => {
  window.addEventListener('keydown', onGlobalKey)
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onGlobalKey)
})

// 默认面包屑：从 route.matched 反推，失败回退路由 path
const ROUTE_LABELS = {
  '/admin/ai': 'AI 配置',
  '/datasources': '数据源',
  '/data-compare': '数据对比',
  '/workflows': '作业流',
  '/lineage': '血缘分析',
  '/batch-lineage': '血缘分析',
  '/history': '执行历史',
}

const computedCrumbs = computed(() => {
  if (props.breadcrumbs && props.breadcrumbs.length) return props.breadcrumbs
  const path = route.path
  for (const [prefix, label] of Object.entries(ROUTE_LABELS)) {
    if (path === prefix || path.startsWith(prefix + '/')) {
      return [{ label }]
    }
  }
  return [{ label: 'DataOps Studio' }]
})
</script>

<template>
  <header class="z-10 flex h-16 shrink-0 items-center justify-between border-b border-slate-200 bg-white px-6 shadow-sm">
    <!-- Breadcrumbs -->
    <div class="flex items-center gap-2 text-sm">
      <slot name="breadcrumbs">
        <template v-for="(crumb, i) in computedCrumbs" :key="i">
          <ChevronRight v-if="i > 0" class="h-4 w-4 text-slate-300" />
          <router-link v-if="crumb.to" :to="crumb.to" class="text-slate-500 transition hover:text-slate-800">
            {{ crumb.label }}
          </router-link>
          <span v-else class="font-medium text-slate-800">{{ crumb.label }}</span>
        </template>
      </slot>
      <span v-if="loading" class="status-badge status-info ml-2">加载中</span>
    </div>

    <!-- Page-specific actions slot + global actions -->
    <div class="flex items-center gap-2">
      <slot name="actions" />

      <!-- Config export shortcut: 安全分享版（密码脱敏） -->
      <a
        href="/config/export"
        title="导出数据源 + 任务配置（密码已脱敏，可放心分享）"
        class="btn btn-ghost h-9 gap-1.5 px-3 text-xs"
      >
        <FileDown class="h-4 w-4" />
        配置导出
      </a>

      <!-- Config export shortcut: 含明文密码版（高危） -->
      <a
        href="/config/export?include_passwords=true"
        title="导出文件包含明文密码 —— 仅自己备份用，不要分享给他人 / 提交到代码仓库"
        class="btn h-9 gap-1.5 border-status-error-bg bg-status-error-bg px-3 text-xs text-status-error hover:bg-rose-100"
        @click="$emit('confirm-include-passwords', $event)"
      >
        <ShieldAlert class="h-4 w-4" />
        含密码导出
      </a>

      <!-- 命令面板触发器（Ctrl/Cmd+K 全局快捷键） -->
      <button
        class="btn btn-ghost flex h-9 items-center gap-2 px-3 text-xs"
        title="搜索导航 / 数据源 / 任务（Ctrl+K）"
        @click="paletteOpen = true"
      >
        <Search class="h-4 w-4" />
        <span class="hidden md:inline text-slate-500">搜索</span>
        <kbd class="hidden md:inline rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 font-mono text-[10px] text-slate-500">
          Ctrl K
        </kbd>
      </button>

      <!-- 通知 popover：异步任务状态 -->
      <NotificationPopover />

      <!-- 用户菜单 —— 当前账号 / 角色徽章 / 注销 -->
      <div v-if="authStore.user" class="relative">
        <button
          class="btn btn-ghost flex h-9 items-center gap-2 px-2 text-xs"
          :title="`${authStore.user.username} (${authStore.user.role})`"
          @click="userMenuOpen = !userMenuOpen"
        >
          <span class="grid h-6 w-6 place-items-center rounded-full bg-primary-light text-[10px] font-bold text-primary">
            {{ (authStore.user.display_name || authStore.user.username).slice(0, 1).toUpperCase() }}
          </span>
          <span class="hidden md:inline font-medium text-slate-700">{{ authStore.user.display_name || authStore.user.username }}</span>
        </button>
        <div
          v-if="userMenuOpen"
          class="absolute right-0 top-full z-30 mt-1 w-48 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-xl"
        >
          <div class="border-b border-slate-100 px-3 py-2">
            <p class="text-xs font-bold text-slate-800">{{ authStore.user.display_name || authStore.user.username }}</p>
            <p class="muted text-[10px]">{{ authStore.user.username }} · {{ authStore.user.role }}</p>
          </div>
          <button
            class="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-slate-700 hover:bg-slate-50"
            @click="userMenuOpen = false; authStore.logout()"
          >
            <LogOut class="h-3.5 w-3.5" />
            注销
          </button>
        </div>
      </div>
    </div>

    <CommandPalette v-model:open="paletteOpen" />
  </header>
</template>
