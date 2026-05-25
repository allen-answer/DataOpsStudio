<script setup lang="ts">
/**
 * Phase 14 #3 Round 4 — 列编辑器(核心)
 *
 * 显示一列的所有字段 + 按 gen 类型动态出 knob。
 * 7 种 generator + 各自参数:
 *   sequence    → prefix
 *   uuid_short  → 无
 *   random_int  → range [min, max] + zipf 偏斜
 *   realistic   → values 样本池 + dist_params(可选 4 种分布)
 *   enum        → values + distribution 权重
 *   timestamp   → range [start, end]
 *   constant    → values[0]
 */
import { Trash2, ChevronDown, ChevronRight } from 'lucide-vue-next'
import { ref } from 'vue'
import type { ColumnForm, GeneratorKind } from '../../stores/scenarioBuilder'

import type { TableForm } from '../../stores/scenarioBuilder'

const props = defineProps<{
  column: ColumnForm
  index: number
  // Round 6 — 给 foreign_key gen 用,显示引用表/列下拉
  allTables?: TableForm[]
  currentTableName?: string
}>()

const emit = defineEmits<{
  (e: 'remove'): void
}>()

// Phase 14 #3 Round 5:默认折叠 — 用户只关心关键列的 generator,
// 其它列保持默认 realistic + 类型嗅探 fallback 就行,不需要打开
const expanded = ref(false)

const GEN_OPTIONS: { value: GeneratorKind; label: string; hint: string }[] = [
  { value: 'sequence',    label: 'sequence (序列号)',     hint: 'JJ00000001 / JJ00000002 ... 适合主键' },
  { value: 'uuid_short',  label: 'uuid_short (短 UUID)',  hint: '8 字符随机' },
  { value: 'random_int',  label: 'random_int (随机整数)', hint: '指定 range,可选 zipf 偏斜' },
  { value: 'realistic',   label: 'realistic (业务化)',    hint: '从 values 抽样 / 按 dist_params 分布生成 / Faker provider' },
  { value: 'enum',        label: 'enum (枚举)',           hint: 'values + 可选 distribution 权重' },
  { value: 'timestamp',   label: 'timestamp (时间)',      hint: 'range [start, end] 日期区间' },
  { value: 'constant',    label: 'constant (常量)',       hint: '所有行同值' },
  { value: 'foreign_key', label: 'foreign_key (外键引用)', hint: '从另一表的列值池抽样,保证 JOIN 匹配' },
]

const FAKER_PROVIDERS = [
  { value: '', label: '(无 - 走默认 fallback)' },
  { value: 'chinese_id', label: 'chinese_id (18 位身份证)' },
  { value: 'mobile_phone', label: 'mobile_phone (1xx 11 位)' },
  { value: 'chinese_name_individual', label: 'chinese_name_individual (个人姓名)' },
  { value: 'chinese_name_org', label: 'chinese_name_org (机构名)' },
  { value: 'fund_acc_no', label: 'fund_acc_no (JJ+8位)' },
  { value: 'shareholder_acc_sh', label: 'shareholder_acc_sh (沪 A 股东账户 A+9位)' },
  { value: 'shareholder_acc_sz', label: 'shareholder_acc_sz (深 A 股东账户 0+9位)' },
  { value: 'securities_code_sh', label: 'securities_code_sh (沪 6 位 60xxxx)' },
  { value: 'securities_code_sz', label: 'securities_code_sz (深 6 位 00xxxx)' },
  { value: 'branch_code', label: 'branch_code (营业部 4 位)' },
  { value: 'bank_card', label: 'bank_card (16-19 位银行卡)' },
  { value: 'address_cn', label: 'address_cn (中文地址)' },
]

const DIST_OPTIONS = [
  { value: 'lognormal',   label: 'lognormal (对数正态)', hint: '金额 / 时长 — 右偏长尾' },
  { value: 'normal',      label: 'normal (正态)',         hint: '年龄 / 评分 — 对称分布' },
  { value: 'uniform',     label: 'uniform (均匀)',        hint: '[min, max] 等概率' },
  { value: 'exponential', label: 'exponential (指数)',   hint: '间隔 / 事件等待时间' },
]
</script>

<template>
  <div class="rounded border border-slate-200 bg-slate-50 p-3 space-y-2">
    <!-- 头部:展开收起 + 列名 + gen badge + pk 标识 + 删除 -->
    <div class="flex items-center gap-2 cursor-pointer" @click="expanded = !expanded">
      <component :is="expanded ? ChevronDown : ChevronRight" class="h-4 w-4 text-slate-400" />
      <span class="text-xs font-bold text-slate-500">#{{ index + 1 }}</span>
      <input
        v-model="column.name"
        class="flex-1 sql-font text-sm"
        placeholder="列名 (如 cptl_acc_num)"
        @click.stop
      />
      <input
        v-model="column.type"
        class="w-40 sql-font text-xs"
        placeholder="VARCHAR(100)"
        @click.stop
      />
      <span
        v-if="column.pk"
        class="pill bg-status-warning-bg text-status-warning text-[9px]"
        title="主键"
      >PK</span>
      <span
        v-if="!column.nullable"
        class="pill bg-slate-100 text-slate-600 text-[9px]"
        title="NOT NULL"
      >NN</span>
      <span class="pill bg-primary-light text-primary text-[9px]" :title="GEN_OPTIONS.find((g) => g.value === column.gen)?.hint || ''">
        {{ column.gen }}
      </span>
      <button
        class="text-status-error hover:text-status-error/70"
        title="删除此列"
        @click.stop="emit('remove')"
      >
        <Trash2 class="h-3.5 w-3.5" />
      </button>
    </div>

    <div v-if="expanded" class="space-y-3 pl-6">
      <!-- pk / nullable / gen 同一行 -->
      <div class="flex items-center gap-4 text-xs">
        <label class="flex items-center gap-1">
          <input type="checkbox" v-model="column.pk" />
          <span>主键 (pk)</span>
        </label>
        <label class="flex items-center gap-1">
          <input type="checkbox" v-model="column.nullable" />
          <span>允许 NULL</span>
        </label>
        <div class="flex items-center gap-1 flex-1">
          <span class="text-slate-500 font-semibold">生成器:</span>
          <select v-model="column.gen" class="flex-1 sql-font">
            <option v-for="g in GEN_OPTIONS" :key="g.value" :value="g.value">
              {{ g.label }}
            </option>
          </select>
        </div>
      </div>
      <p class="text-[10px] text-slate-400 pl-1">
        💡 {{ GEN_OPTIONS.find((g) => g.value === column.gen)?.hint || '' }}
      </p>

      <!-- per-gen knob: dynamic 区 -->
      <div class="rounded bg-white p-2 border border-slate-200 space-y-2">
        <!-- sequence: prefix -->
        <template v-if="column.gen === 'sequence'">
          <label class="block text-xs">
            <span class="text-slate-500">前缀(可选,序列号将拼为 prefix + 数字):</span>
            <input v-model="column.prefix" class="w-full sql-font text-sm mt-1" placeholder="如 JJ" />
          </label>
        </template>

        <!-- uuid_short: 无 -->
        <template v-else-if="column.gen === 'uuid_short'">
          <p class="text-xs text-slate-400 italic">无可调参数</p>
        </template>

        <!-- random_int: range + zipf -->
        <template v-else-if="column.gen === 'random_int'">
          <div class="grid grid-cols-3 gap-2 text-xs">
            <label class="block">
              <span class="text-slate-500">range min</span>
              <input v-model.number="column.range_min" type="number" class="w-full mt-0.5" placeholder="如 1000" />
            </label>
            <label class="block">
              <span class="text-slate-500">range max</span>
              <input v-model.number="column.range_max" type="number" class="w-full mt-0.5" placeholder="如 9999" />
            </label>
            <label class="block">
              <span class="text-slate-500" title="幂律偏斜系数,0=均匀, >1 越大头部越集中">zipf (可选)</span>
              <input v-model.number="column.zipf" type="number" step="0.1" class="w-full mt-0.5" placeholder="留空=均匀" />
            </label>
          </div>
        </template>

        <!-- realistic: values 池 + dist_params -->
        <template v-else-if="column.gen === 'realistic'">
          <label class="block text-xs">
            <span class="text-slate-500">业务样本池(逗号分隔,可选):</span>
            <input
              v-model="column.values_text"
              class="w-full sql-font text-sm mt-0.5"
              placeholder="如 张三, 李四, 王五  (留空走 AI 填血肉 / 类型嗅探 fallback)"
            />
          </label>
          <label class="block text-xs">
            <input type="checkbox" v-model="column.dist_params_enabled" class="mr-1.5" />
            <span class="text-slate-700 font-semibold">数值列分布参数 (dist_params)</span>
            <span class="text-[10px] text-slate-400 ml-1">— 金额 / 数值列勾这个</span>
          </label>
          <div v-if="column.dist_params_enabled" class="ml-5 grid grid-cols-2 gap-2 text-xs border-l-2 border-slate-200 pl-3">
            <label class="block col-span-2">
              <span class="text-slate-500">分布族:</span>
              <select v-model="column.dist_params.kind" class="w-full mt-0.5">
                <option v-for="d in DIST_OPTIONS" :key="d.value" :value="d.value">{{ d.label }}</option>
              </select>
              <p class="text-[10px] text-slate-400 mt-0.5">
                💡 {{ DIST_OPTIONS.find((d) => d.value === column.dist_params.kind)?.hint }}
              </p>
            </label>
            <label v-if="column.dist_params.kind !== 'uniform' && column.dist_params.kind !== 'exponential'" class="block">
              <span class="text-slate-500" title="对数正态/正态的均值参数">mu (均值)</span>
              <input v-model.number="column.dist_params.mu" type="number" step="0.1" class="w-full mt-0.5" />
            </label>
            <label v-if="column.dist_params.kind !== 'uniform' && column.dist_params.kind !== 'exponential'" class="block">
              <span class="text-slate-500" title="对数正态/正态的标准差,越大尾巴越长">sigma (标准差)</span>
              <input v-model.number="column.dist_params.sigma" type="number" step="0.1" class="w-full mt-0.5" />
            </label>
            <label v-if="column.dist_params.kind === 'exponential'" class="block col-span-2">
              <span class="text-slate-500">lambda (速率)</span>
              <input v-model.number="column.dist_params.lambda" type="number" step="0.01" class="w-full mt-0.5" />
            </label>
            <label class="block">
              <span class="text-slate-500">min (clamp 下界)</span>
              <input v-model.number="column.dist_params.min" type="number" class="w-full mt-0.5" />
            </label>
            <label class="block">
              <span class="text-slate-500">max (clamp 上界)</span>
              <input v-model.number="column.dist_params.max" type="number" class="w-full mt-0.5" placeholder="可选" />
            </label>
          </div>
        </template>

        <!-- enum: values + distribution -->
        <template v-else-if="column.gen === 'enum'">
          <label class="block text-xs">
            <span class="text-slate-500">枚举值(逗号分隔,必填):</span>
            <input
              v-model="column.values_text"
              class="w-full sql-font text-sm mt-0.5"
              placeholder="如 jzjy,jgkh,rzrq,opt"
            />
          </label>
          <label class="block text-xs">
            <span class="text-slate-500" title="权重数,数量须跟 values 个数一致,自动归一化">distribution 权重 (可选,默认等概率):</span>
            <input
              v-model="column.distribution_text"
              class="w-full sql-font text-sm mt-0.5"
              placeholder="如 0.55,0.25,0.15,0.05  (生产偏斜分布)"
            />
          </label>
        </template>

        <!-- timestamp: range -->
        <template v-else-if="column.gen === 'timestamp'">
          <div class="grid grid-cols-2 gap-2 text-xs">
            <label class="block">
              <span class="text-slate-500">开始日期</span>
              <input v-model="column.ts_start" type="date" class="w-full mt-0.5" />
            </label>
            <label class="block">
              <span class="text-slate-500">结束日期</span>
              <input v-model="column.ts_end" type="date" class="w-full mt-0.5" />
            </label>
          </div>
        </template>

        <!-- constant: values[0] -->
        <template v-else-if="column.gen === 'constant'">
          <label class="block text-xs">
            <span class="text-slate-500">固定值(所有行同此值):</span>
            <input v-model="column.values_text" class="w-full sql-font text-sm mt-0.5" placeholder='如 "CNY"' />
          </label>
        </template>

        <!-- foreign_key (Round 6): 引用其它表的列,保证 JOIN 拿到匹配 -->
        <template v-else-if="column.gen === 'foreign_key'">
          <p class="text-[10px] text-slate-400 mb-1">
            🔗 从另一张表的列实际生成的值池抽样,保证 JOIN 拿到匹配行
          </p>
          <div class="grid grid-cols-2 gap-2 text-xs">
            <label class="block">
              <span class="text-slate-500">引用表:</span>
              <select v-model="column.fk_ref_table" class="w-full mt-0.5 sql-font">
                <option value="">— 选 —</option>
                <option
                  v-for="t in (allTables || []).filter((t) => t.name !== currentTableName)"
                  :key="t.name"
                  :value="t.name"
                >{{ t.name }}</option>
              </select>
            </label>
            <label class="block">
              <span class="text-slate-500">引用列(通常是 PK):</span>
              <select v-model="column.fk_ref_column" class="w-full mt-0.5 sql-font">
                <option value="">— 选 —</option>
                <option
                  v-for="c in (allTables || []).find((t) => t.name === column.fk_ref_table)?.columns || []"
                  :key="c.name"
                  :value="c.name"
                >{{ c.name }}{{ c.pk ? ' (PK)' : '' }}</option>
              </select>
            </label>
          </div>
          <div class="mt-2 space-y-2 text-xs">
            <label class="block">
              <div class="flex justify-between">
                <span class="text-slate-500">匹配率: {{ (column.fk_match_rate * 100).toFixed(0) }}%</span>
                <span class="text-[10px] text-slate-400">
                  100% = 全部匹配;&lt;100% = 模拟脏数据 / LEFT JOIN miss
                </span>
              </div>
              <input
                v-model.number="column.fk_match_rate"
                type="range" min="0.5" max="1.0" step="0.05"
                class="w-full mt-0.5"
              />
            </label>
            <div class="grid grid-cols-2 gap-2">
              <label class="flex items-center gap-1">
                <input type="checkbox" v-model="column.fk_unique" />
                <span>fk_unique (1:1 关系)</span>
              </label>
              <label class="block">
                <span class="text-slate-500">抽样分布:</span>
                <select v-model="column.fk_distribution" class="w-full mt-0.5">
                  <option value="uniform">uniform (均匀)</option>
                  <option value="zipf">zipf (头部偏斜)</option>
                </select>
              </label>
            </div>
            <label v-if="column.fk_distribution === 'zipf'" class="block">
              <span class="text-slate-500">zipf alpha (越大头部越集中):</span>
              <input v-model.number="column.fk_zipf_alpha" type="number"
                     step="0.1" min="1.0" max="5.0" class="w-full mt-0.5" />
            </label>
          </div>
        </template>

        <!-- Faker provider 选项 — 任何 gen 都可叠加(主要 realistic 用) -->
        <div v-if="column.gen === 'realistic'" class="border-t border-slate-200 pt-2 mt-2">
          <label class="block text-xs">
            <span class="text-slate-500 font-semibold">💎 Faker provider (金融行业域):</span>
            <select v-model="column.faker_provider" class="w-full mt-0.5">
              <option v-for="p in FAKER_PROVIDERS" :key="p.value" :value="p.value">{{ p.label }}</option>
            </select>
            <p class="text-[10px] text-slate-400 mt-0.5">
              选了 provider 后会覆盖 values / dist_params,直接用该域 generator 产数据
            </p>
          </label>
        </div>
      </div>
    </div>
  </div>
</template>
