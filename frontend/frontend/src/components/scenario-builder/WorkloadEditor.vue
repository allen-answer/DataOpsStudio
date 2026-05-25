<script setup lang="ts">
/**
 * Phase 14 #3 Round 6 H.4 — Workload Editor
 *
 * 3 种 workload kind 动态字段:
 *   slow_query     — sql + intentional_issues[] + expected_optimizations[]
 *   compare_task   — source_sql + target_sql + key_columns + expected{...}
 *   lineage_script — sql
 */
import { Trash2, Plus } from 'lucide-vue-next'
import type { WorkloadForm, WorkloadKind } from '../../stores/scenarioBuilder'

const props = defineProps<{
  workload: WorkloadForm
  index: number
}>()

const emit = defineEmits<{
  (e: 'remove'): void
}>()

const KIND_OPTIONS: { value: WorkloadKind; label: string; hint: string }[] = [
  { value: 'slow_query',     label: 'slow_query (慢 SQL 优化)',  hint: '跑 EXPLAIN + AI 复核,需要 sql + 期望优化点' },
  { value: 'compare_task',   label: 'compare_task (数据对比)',   hint: '双 SQL 对比 + key_columns + 预期 only_source/only_target/diff/same' },
  { value: 'lineage_script', label: 'lineage_script (血缘分析)', hint: 'sql 喂给 lineage analyzer 解析 read/write 表' },
]

function addIssue() { props.workload.intentional_issues.push('') }
function removeIssue(i: number) { props.workload.intentional_issues.splice(i, 1) }
function addExpected() { props.workload.expected_optimizations.push('') }
function removeExpected(i: number) { props.workload.expected_optimizations.splice(i, 1) }
</script>

<template>
  <div class="rounded border border-slate-200 bg-slate-50 p-3 space-y-2">
    <!-- 头部:kind + name + 删除 -->
    <div class="flex items-center gap-2">
      <span class="text-xs font-bold text-slate-500">#{{ index + 1 }}</span>
      <select v-model="workload.kind" class="w-56 text-xs">
        <option v-for="k in KIND_OPTIONS" :key="k.value" :value="k.value">{{ k.label }}</option>
      </select>
      <input
        v-model="workload.name"
        class="flex-1 sql-font text-sm"
        placeholder="workload 名称 (如 fund-acc-open-etl)"
      />
      <button class="text-status-error hover:text-status-error/70" @click="emit('remove')">
        <Trash2 class="h-3.5 w-3.5" />
      </button>
    </div>
    <p class="text-[10px] text-slate-400 pl-7">
      💡 {{ KIND_OPTIONS.find((k) => k.value === workload.kind)?.hint }}
    </p>

    <!-- 动态字段区 -->
    <div class="rounded bg-white p-2 border border-slate-200 space-y-2 ml-7">

      <!-- slow_query -->
      <template v-if="workload.kind === 'slow_query'">
        <label class="block text-xs">
          <span class="text-slate-500">SQL (慢查询语句):</span>
          <textarea
            v-model="workload.sql"
            rows="6"
            class="w-full sql-font text-xs mt-0.5"
            placeholder="SELECT ... FROM ... JOIN ... WHERE ..."
          />
        </label>
        <!-- intentional_issues -->
        <div>
          <div class="flex items-center justify-between text-xs">
            <span class="text-slate-500 font-semibold">设计的问题 (intentional_issues)</span>
            <button class="text-primary hover:underline" @click="addIssue">
              <Plus class="h-3 w-3 inline" /> 添加
            </button>
          </div>
          <div v-for="(it, i) in workload.intentional_issues" :key="i" class="flex items-center gap-1.5 text-xs mt-1">
            <input v-model="workload.intentional_issues[i]" class="flex-1" placeholder="如 TRIM 包列让索引失效" />
            <button class="text-status-error" @click="removeIssue(i)"><Trash2 class="h-3 w-3" /></button>
          </div>
          <p v-if="!workload.intentional_issues.length" class="text-[10px] text-slate-400 italic mt-1">
            暂无 — yml 里这块帮 LLM 复核 + 你后续看 plan 时校对
          </p>
        </div>
        <!-- expected_optimizations -->
        <div>
          <div class="flex items-center justify-between text-xs">
            <span class="text-slate-500 font-semibold">期望优化 (expected_optimizations)</span>
            <button class="text-primary hover:underline" @click="addExpected">
              <Plus class="h-3 w-3 inline" /> 添加
            </button>
          </div>
          <div v-for="(it, i) in workload.expected_optimizations" :key="i" class="flex items-center gap-1.5 text-xs mt-1">
            <input v-model="workload.expected_optimizations[i]" class="flex-1" placeholder="如 加 (data_dt, branch_code) 复合索引" />
            <button class="text-status-error" @click="removeExpected(i)"><Trash2 class="h-3 w-3" /></button>
          </div>
        </div>
      </template>

      <!-- compare_task -->
      <template v-else-if="workload.kind === 'compare_task'">
        <label class="block text-xs">
          <span class="text-slate-500">source_sql (源端):</span>
          <textarea
            v-model="workload.sql"
            rows="4"
            class="w-full sql-font text-xs mt-0.5"
            placeholder="SELECT ... FROM source_table WHERE ..."
          />
        </label>
        <label class="block text-xs">
          <span class="text-slate-500">target_sql (目标端,留空 = 用 source_sql 同表对比):</span>
          <textarea
            v-model="workload.target_sql"
            rows="3"
            class="w-full sql-font text-xs mt-0.5"
            placeholder="(可选) SELECT ... FROM target_table WHERE ..."
          />
        </label>
        <label class="block text-xs">
          <span class="text-slate-500">key_columns (主键列,逗号分隔):</span>
          <input v-model="workload.key_columns" class="w-full sql-font text-xs mt-0.5" placeholder="如 order_id 或 order_id, line_no" />
        </label>
        <div class="text-xs">
          <span class="text-slate-500 font-semibold">expected (回归预期对比结果):</span>
          <div class="grid grid-cols-4 gap-1.5 mt-1">
            <label class="block">
              <span class="text-[10px] text-slate-500">only_source</span>
              <input v-model.number="workload.expected_only_source" type="number" min="0" class="w-full text-xs" />
            </label>
            <label class="block">
              <span class="text-[10px] text-slate-500">only_target</span>
              <input v-model.number="workload.expected_only_target" type="number" min="0" class="w-full text-xs" />
            </label>
            <label class="block">
              <span class="text-[10px] text-slate-500">diff</span>
              <input v-model.number="workload.expected_diff" type="number" min="0" class="w-full text-xs" />
            </label>
            <label class="block">
              <span class="text-[10px] text-slate-500">same</span>
              <input v-model.number="workload.expected_same" type="number" min="0" class="w-full text-xs" />
            </label>
          </div>
          <p class="text-[10px] text-slate-400 mt-1">verify 时拿这个跟实际 run 的 summary 对比</p>
        </div>
      </template>

      <!-- lineage_script -->
      <template v-else-if="workload.kind === 'lineage_script'">
        <label class="block text-xs">
          <span class="text-slate-500">SQL (送 lineage analyzer 解析):</span>
          <textarea
            v-model="workload.sql"
            rows="5"
            class="w-full sql-font text-xs mt-0.5"
            placeholder="INSERT INTO dwd.xxx SELECT ... FROM ods.xxx ..."
          />
          <p class="text-[10px] text-slate-400 mt-1">
            支持 INSERT / UPDATE / MERGE / DELETE / CTAS,会生成 read/write 表血缘
          </p>
        </label>
      </template>
    </div>
  </div>
</template>
