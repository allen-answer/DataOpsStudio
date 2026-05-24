<script setup lang="ts">
/**
 * Phase 14 #3 Round 6 H.4 — Anomaly Editor
 *
 * 6 种 anomaly kind 动态字段:
 *   missing_rows / extra_rows / duplicate_pk — table + count/fraction
 *   value_drift                                — table + column + count/fraction + perturbation
 *   null_drift / type_mismatch                 — table + column + count/fraction
 */
import { Trash2 } from 'lucide-vue-next'
import type { AnomalyForm, AnomalyKind, TableForm } from '../../stores/scenarioBuilder'

const props = defineProps<{
  anomaly: AnomalyForm
  index: number
  allTables: TableForm[]
}>()

const emit = defineEmits<{
  (e: 'remove'): void
}>()

const KIND_OPTIONS: { value: AnomalyKind; label: string; hint: string }[] = [
  { value: 'missing_rows',  label: 'missing_rows (target 缺行)',     hint: 'target 缺一部分 source 的行 → only_source 增大' },
  { value: 'extra_rows',    label: 'extra_rows (target 多行)',       hint: 'target 多出 source 没有的行 → only_target 增大' },
  { value: 'value_drift',   label: 'value_drift (值偏移)',           hint: '数值列按 perturbation 比例偏移' },
  { value: 'null_drift',    label: 'null_drift (变 NULL)',           hint: '一部分行的某列变 NULL' },
  { value: 'duplicate_pk',  label: 'duplicate_pk (PK 重复)',         hint: 'PK 重复测系统对脏数据反应' },
  { value: 'type_mismatch', label: 'type_mismatch (类型不匹配)',     hint: 'DATETIME 存为 VARCHAR 等' },
]

// 哪些 kind 需要 column 字段
const KINDS_NEED_COLUMN = ['value_drift', 'null_drift', 'type_mismatch']
const needsColumn = () => KINDS_NEED_COLUMN.includes(props.anomaly.kind)
</script>

<template>
  <div class="rounded border border-slate-200 bg-slate-50 p-3 space-y-2">
    <!-- 头部:kind + table + 删除 -->
    <div class="flex items-center gap-2">
      <span class="text-xs font-bold text-slate-500">#{{ index + 1 }}</span>
      <select v-model="anomaly.kind" class="flex-1 text-xs">
        <option v-for="k in KIND_OPTIONS" :key="k.value" :value="k.value">{{ k.label }}</option>
      </select>
      <select v-model="anomaly.table" class="w-48 text-xs sql-font">
        <option value="">— 选表 —</option>
        <option v-for="t in allTables" :key="t.name" :value="t.name">{{ t.name }}</option>
      </select>
      <button class="text-status-error hover:text-status-error/70" @click="emit('remove')">
        <Trash2 class="h-3.5 w-3.5" />
      </button>
    </div>
    <p class="text-[10px] text-slate-400 pl-7">
      💡 {{ KIND_OPTIONS.find((k) => k.value === anomaly.kind)?.hint }}
    </p>

    <!-- 动态字段区 -->
    <div class="rounded bg-white p-2 border border-slate-200 space-y-2 ml-7">
      <!-- column(部分 kind 需要)-->
      <label v-if="needsColumn()" class="block text-xs">
        <span class="text-slate-500">目标列:</span>
        <select v-model="anomaly.column" class="w-full mt-0.5 sql-font">
          <option value="">— 选列 —</option>
          <option
            v-for="c in (allTables.find((t) => t.name === anomaly.table)?.columns || [])"
            :key="c.name"
            :value="c.name"
          >{{ c.name }} ({{ c.type }})</option>
        </select>
      </label>

      <!-- count vs fraction -->
      <div class="grid grid-cols-2 gap-2 text-xs">
        <label class="block">
          <span class="text-slate-500">数量模式:</span>
          <select v-model="anomaly.count_mode" class="w-full mt-0.5">
            <option value="fraction">fraction (按比例)</option>
            <option value="count">count (精确数)</option>
          </select>
        </label>
        <label v-if="anomaly.count_mode === 'count'" class="block">
          <span class="text-slate-500">count:</span>
          <input v-model.number="anomaly.count" type="number" min="0" class="w-full mt-0.5" />
        </label>
        <label v-else class="block">
          <span class="text-slate-500">fraction (0.0 ~ 1.0):</span>
          <input v-model.number="anomaly.fraction" type="number" step="0.01" min="0" max="1" class="w-full mt-0.5" />
        </label>
      </div>

      <!-- value_drift 专属 — perturbation -->
      <label v-if="anomaly.kind === 'value_drift'" class="block text-xs">
        <span class="text-slate-500">perturbation (偏移幅度):</span>
        <input
          v-model="anomaly.perturbation"
          class="w-full mt-0.5"
          placeholder='例 "±5%" / "5%" / "0.05"'
        />
        <span class="text-[10px] text-slate-400">数值列按此比例随机偏移</span>
      </label>
    </div>
  </div>
</template>
