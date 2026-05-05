<script setup>
import { computed } from 'vue'
import { Database, Sliders, ArrowLeftRight, PlayCircle, Check, AlertTriangle } from 'lucide-vue-next'

const props = defineProps({
  current: { type: String, required: true },
  // 每步是否"已就绪"——让步骤条上显示对勾或灰色
  completion: { type: Object, default: () => ({}) },
  // 每步的错误数 —— 命中时显示红色徽章
  errorCounts: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['change'])

// labelKey / hintKey 走 i18n —— S4.C PR2 用 $t() 渲染
const STEPS = [
  { id: 'source',  labelKey: 'pages.workbench.stepSource',  hintKey: 'pages.workbench.stepSourceHint',  icon: Database },
  { id: 'rules',   labelKey: 'pages.workbench.stepRules',   hintKey: 'pages.workbench.stepRulesHint',   icon: Sliders },
  { id: 'mapping', labelKey: 'pages.workbench.stepMapping', hintKey: 'pages.workbench.stepMappingHint', icon: ArrowLeftRight },
  { id: 'result',  labelKey: 'pages.workbench.stepResult',  hintKey: 'pages.workbench.stepResultHint',  icon: PlayCircle },
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
            class="relative grid h-8 w-8 shrink-0 place-items-center rounded-full text-xs font-bold transition"
            :class="errorCounts[step.id]
              ? 'bg-status-error-bg text-status-error ring-2 ring-status-error/40'
              : idx < currentIndex
                ? 'bg-status-success text-white'
                : idx === currentIndex
                  ? 'bg-primary text-white shadow-sm'
                  : completion[step.id]
                    ? 'bg-status-success-bg text-status-success'
                    : 'bg-slate-100 text-slate-400'"
          >
            <!-- 命中错误优先显示警告图标；其次走过的步骤显示对勾；当前 / 未到达
                 显示自身 icon —— 避免 completion ready 但未走到时误以为已完成 -->
            <AlertTriangle v-if="errorCounts[step.id]" class="h-4 w-4" />
            <Check v-else-if="idx < currentIndex" class="h-4 w-4" />
            <component :is="step.icon" v-else class="h-4 w-4" />
            <!-- 错误数右上角小红点 -->
            <span
              v-if="errorCounts[step.id] && errorCounts[step.id] > 1"
              class="absolute -right-1 -top-1 grid h-4 min-w-4 place-items-center rounded-full bg-status-error px-1 text-[10px] font-bold text-white"
            >{{ errorCounts[step.id] }}</span>
          </span>
          <div class="min-w-0">
            <div class="truncate text-sm font-semibold"
              :class="idx === currentIndex ? 'text-primary' : 'text-slate-700'">
              {{ idx + 1 }}. {{ $t(step.labelKey) }}
            </div>
            <div class="truncate text-[11px] text-slate-400">{{ $t(step.hintKey) }}</div>
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
