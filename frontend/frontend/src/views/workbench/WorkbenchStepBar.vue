<script setup>
import { computed } from 'vue'
import { Database, Sliders, ArrowLeftRight, PlayCircle, Check } from 'lucide-vue-next'

const props = defineProps({
  current: { type: String, required: true },
  // 每步是否"已就绪"——让步骤条上显示对勾或灰色
  completion: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['change'])

const STEPS = [
  { id: 'source',  label: '数据来源', icon: Database,        hint: '数据源 / SQL 或 Excel' },
  { id: 'rules',   label: '对比规则', icon: Sliders,         hint: '主键 / 忽略字段 / 类型' },
  { id: 'mapping', label: '字段映射', icon: ArrowLeftRight,  hint: '字段筛选 / 列对齐' },
  { id: 'result',  label: '执行结果', icon: PlayCircle,      hint: '运行 / 汇总 / 下载' },
]

const currentIndex = computed(() => STEPS.findIndex(s => s.id === props.current))

function goStep(index) {
  emit('change', STEPS[index].id)
}
</script>

<template>
  <div class="flex items-center justify-between rounded-xl border border-slate-200 bg-white px-3 py-3 shadow-soft">
    <div class="flex flex-1 items-center">
      <template v-for="(step, idx) in STEPS" :key="step.id">
        <button
          type="button"
          class="group flex flex-1 items-center gap-2.5 rounded-lg px-2 py-1 text-left transition"
          :class="idx === currentIndex
            ? 'bg-primary-light'
            : 'hover:bg-slate-50'"
          @click="goStep(idx)"
        >
          <span
            class="grid h-8 w-8 shrink-0 place-items-center rounded-full text-xs font-bold transition"
            :class="idx < currentIndex
              ? 'bg-status-success text-white'
              : idx === currentIndex
                ? 'bg-primary text-white shadow-sm'
                : completion[step.id]
                  ? 'bg-status-success-bg text-status-success'
                  : 'bg-slate-100 text-slate-400'"
          >
            <!-- 已经走过的步骤显示对勾；当前步骤显示自身 icon；未到达的步骤
                 即使 completion 标记 ready 也显示自身 icon（避免误以为已完成） -->
            <Check v-if="idx < currentIndex" class="h-4 w-4" />
            <component :is="step.icon" v-else class="h-4 w-4" />
          </span>
          <div class="min-w-0">
            <div class="truncate text-sm font-semibold"
              :class="idx === currentIndex ? 'text-primary' : 'text-slate-700'">
              {{ idx + 1 }}. {{ step.label }}
            </div>
            <div class="truncate text-[11px] text-slate-400">{{ step.hint }}</div>
          </div>
        </button>
        <div
          v-if="idx < STEPS.length - 1"
          class="mx-1 h-px w-6 shrink-0"
          :class="idx < currentIndex ? 'bg-status-success' : 'bg-slate-200'"
        />
      </template>
    </div>
  </div>
</template>
