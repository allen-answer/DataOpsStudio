<script setup>
// 各血缘 panel 共用的筛选条壳子。
// 暴露：搜索框（v-model:search） + 任意自定义筛选控件（slot="filters"） +
// 当前/总数 计数徽章 + 清空按钮（emit('clear')）。
//
// 用法：
//   <LineageFilterBar
//     v-model:search="search"
//     search-placeholder="表名 / schema 搜索"
//     :total="total"
//     :visible="visible"
//     @clear="resetAll"
//   >
//     <template #filters>
//       <select v-model="schemaFilter">...</select>
//     </template>
//   </LineageFilterBar>
import { computed } from 'vue'
import { Search, X, Filter } from 'lucide-vue-next'

const props = defineProps({
  search: { type: String, default: '' },
  // 不在 prop default 处放中文 —— 模板里 fallback 到 $t('filterBar.searchDefault')
  searchPlaceholder: { type: String, default: '' },
  total: { type: Number, default: 0 },
  visible: { type: Number, default: 0 },
  // 是否显示"清空筛选"按钮 —— 默认看 active 是否为 true
  active: { type: Boolean, default: false },
})
defineEmits(['update:search', 'clear'])

// 命中数和总数不一致时更醒目
const filtered = computed(() => props.visible !== props.total)
</script>

<template>
  <div class="rounded-lg border border-slate-200 bg-slate-50/60 p-2.5">
    <div class="flex flex-wrap items-center gap-2">
      <!-- 搜索框 -->
      <div class="relative flex min-w-[200px] flex-1 items-center">
        <Search class="absolute left-2.5 h-3.5 w-3.5 text-slate-400" />
        <input
          :value="search"
          @input="$emit('update:search', $event.target.value)"
          :placeholder="searchPlaceholder || $t('filterBar.searchDefault')"
          class="w-full rounded-md border border-slate-200 bg-white py-1.5 pl-8 pr-7 text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary/30"
        />
        <button
          v-if="search"
          @click="$emit('update:search', '')"
          class="absolute right-1.5 grid h-5 w-5 place-items-center rounded text-slate-400 hover:bg-slate-100 hover:text-slate-600"
          :title="$t('filterBar.clearSearch')"
        >
          <X class="h-3 w-3" />
        </button>
      </div>

      <!-- 自定义筛选控件 slot -->
      <slot name="filters" />

      <!-- 计数徽章 + 清空 -->
      <div class="ml-auto flex items-center gap-2">
        <span
          class="rounded-md px-2 py-1 text-[11px] font-medium"
          :class="filtered ? 'bg-primary-light text-primary' : 'bg-slate-100 text-slate-500'"
        >
          <Filter v-if="filtered" class="mr-0.5 inline h-3 w-3 align-text-top" />
          {{ visible }} / {{ total }}
        </span>
        <button
          v-if="active"
          @click="$emit('clear')"
          class="flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2 py-1 text-[11px] font-medium text-slate-600 hover:bg-slate-50 hover:text-slate-800"
          :title="$t('filterBar.clearAllTooltip')"
        >
          <X class="h-3 w-3" />
          {{ $t('filterBar.clearFilters') }}
        </button>
      </div>
    </div>
  </div>
</template>
