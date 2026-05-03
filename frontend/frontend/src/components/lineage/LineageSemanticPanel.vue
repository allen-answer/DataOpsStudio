<script setup>
// 语义血缘 wrapper —— 沿用现有 components/SemanticLineagePanel.vue 的实现，
// Phase 3 给它套个统一的 Lineage* 命名让 LineageView/BatchView 可以一致引用。
import SemanticLineagePanel from '../SemanticLineagePanel.vue'

defineProps({
  semantic: { type: Object, default: () => ({}) },
})
</script>

<template>
  <SemanticLineagePanel :semantic="semantic" />
</template>
