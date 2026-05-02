<script setup>
defineProps({
  node: { type: Object, required: true },
})
</script>

<template>
  <div class="grid grid-cols-1 gap-2 lg:grid-cols-[minmax(0,1fr)_140px]">
    <label>
      <span class="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-slate-400">SQL</span>
      <textarea v-model="node.sql" class="block min-h-[60px] w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 font-mono text-[12px]" placeholder="SELECT * FROM ..."></textarea>
    </label>
    <label>
      <span class="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-slate-400">方言</span>
      <input v-model="node.dialect" class="w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 text-xs" placeholder="mysql">
    </label>
  </div>
</template>
