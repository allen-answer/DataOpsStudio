<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { ChevronRight, Bell, Search, FileDown, ShieldAlert } from 'lucide-vue-next'

const props = defineProps({
  // 路由 path → 面包屑文案的 map；若 view 想自定义动态面包屑，可通过 slot 覆盖
  breadcrumbs: { type: Array, default: null },
  loading: { type: Boolean, default: false },
})

defineEmits(['confirm-include-passwords'])

const route = useRoute()

// 默认面包屑：从 route.matched 反推，失败回退路由 path
const ROUTE_LABELS = {
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

      <!-- Reserved: search / notifications（点击当前无功能，保持视觉一致即可） -->
      <button class="btn btn-ghost h-9 w-9 px-0" title="搜索（暂未实现）">
        <Search class="h-4 w-4" />
      </button>
      <button class="btn btn-ghost relative h-9 w-9 px-0" title="通知（暂未实现）">
        <Bell class="h-4 w-4" />
      </button>
    </div>
  </header>
</template>
