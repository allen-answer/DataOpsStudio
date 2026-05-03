<script setup>
import { inject } from 'vue'
import { CheckSquare, Filter, ArrowRight } from 'lucide-vue-next'

const {
  taskDraft, sourceFields, targetFields,
  fieldPickerRows, fieldPickerHasFields,
  toggleFieldIncluded, fieldPickerSelectAll, fieldPickerExcludeOneSided,
} = inject('app')
</script>

<template>
  <section class="space-y-4">
    <!-- 字段映射文本框 -->
    <div class="card">
      <div class="mb-3 flex items-center justify-between">
        <div>
          <h3 class="text-base font-semibold text-slate-800">字段映射</h3>
          <p class="muted text-[11px]">源字段名与目标字段名不一致时，每行写一条 `source_col -&gt; target_col`</p>
        </div>
      </div>
      <textarea
        v-model="taskDraft.column_mappings"
        class="min-h-[100px] bg-slate-50 sql-font text-sm"
        placeholder="例：&#10;src_id -&gt; tgt_id&#10;create_time -&gt; created_at"
      />
    </div>

    <!-- 字段筛选器 -->
    <div class="card">
      <div class="mb-3 flex items-center justify-between">
        <div>
          <h3 class="text-base font-semibold text-slate-800">字段参与对比筛选</h3>
          <p class="muted text-[11px]">
            勾选 = 参与对比；取消 = 加入「忽略字段」。
            <span v-if="!fieldPickerHasFields" class="text-status-warning">
              请先在第 1 步「数据来源」点击两侧的「提取字段」加载列名
            </span>
          </p>
        </div>
        <div v-if="fieldPickerHasFields" class="flex gap-1.5">
          <button class="btn btn-ghost h-7 gap-1 px-2 text-[11px]" @click="fieldPickerSelectAll">
            <CheckSquare class="h-3 w-3" /> 全选
          </button>
          <button class="btn btn-ghost h-7 gap-1 px-2 text-[11px]" @click="fieldPickerExcludeOneSided">
            <Filter class="h-3 w-3" /> 仅交集
          </button>
        </div>
      </div>

      <div v-if="!fieldPickerHasFields" class="rounded-lg border border-dashed border-slate-200 p-6 text-center">
        <ArrowRight class="mx-auto h-5 w-5 -rotate-90 text-slate-300" />
        <p class="muted mt-2 text-xs">两侧字段都未提取，无法配置筛选</p>
        <p class="muted text-[11px]">回到「数据来源」点击「提取字段」</p>
      </div>

      <div v-else class="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
        <label
          v-for="row in fieldPickerRows"
          :key="row.key"
          class="flex cursor-pointer items-center justify-between gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm transition hover:border-primary/50"
        >
          <span class="flex min-w-0 items-center gap-2">
            <input
              type="checkbox"
              :checked="row.included"
              class="h-4 w-4 rounded text-primary focus:ring-primary"
              @change="toggleFieldIncluded(row.name)"
            >
            <span class="truncate sql-font text-xs text-slate-800">{{ row.name }}</span>
          </span>
          <span
            class="shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold"
            :class="row.onSource && row.onTarget
              ? 'bg-status-success-bg text-status-success'
              : (row.onSource ? 'bg-tag-source-bg text-tag-source' : 'bg-tag-intermediate-bg text-tag-intermediate')"
          >
            {{ row.onSource && row.onTarget ? '双侧' : (row.onSource ? '仅源' : '仅目标') }}
          </span>
        </label>
      </div>

      <div v-if="fieldPickerHasFields" class="muted mt-3 text-[11px]">
        <span class="text-slate-500">源 {{ sourceFields.length }} 列 · 目标 {{ targetFields.length }} 列</span>
        <span v-if="fieldPickerRows.length" class="ml-2">· 共 {{ fieldPickerRows.length }} 个字段，{{ fieldPickerRows.filter(r => r.included).length }} 个参与对比</span>
      </div>
    </div>
  </section>
</template>
