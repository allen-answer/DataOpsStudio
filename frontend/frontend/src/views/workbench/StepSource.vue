<script setup>
import { inject } from 'vue'
import { storeToRefs } from 'pinia'
import DataSourcePanel from './DataSourcePanel.vue'
import FieldCachePanel from './FieldCachePanel.vue'
import { useProjectStore } from '../../stores/project'

const { state, taskDraft } = inject('app')
const { projects } = storeToRefs(useProjectStore())
</script>

<template>
  <section class="space-y-4">
    <!-- 任务基础字段 -->
    <div class="card">
      <h3 class="mb-3 text-base font-semibold text-slate-800">基础信息</h3>
      <div class="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
        <label>
          <span class="muted mb-1 block text-[10px] font-bold uppercase tracking-wider">任务名称</span>
          <input v-model="taskDraft.name" placeholder="例：MySQL 用户表对比" class="bg-slate-50">
        </label>
        <label>
          <span class="muted mb-1 block text-[10px] font-bold uppercase tracking-wider">源数据源</span>
          <select v-model="taskDraft.source_id" class="bg-slate-50">
            <option value="">选择数据源</option>
            <option v-for="item in state.datasources" :key="item.id" :value="item.id">{{ item.name }}</option>
          </select>
        </label>
        <label>
          <span class="muted mb-1 block text-[10px] font-bold uppercase tracking-wider">目标数据源</span>
          <select v-model="taskDraft.target_id" class="bg-slate-50">
            <option value="">选择数据源</option>
            <option v-for="item in state.datasources" :key="item.id" :value="item.id">{{ item.name }}</option>
          </select>
        </label>
        <label>
          <span class="muted mb-1 block text-[10px] font-bold uppercase tracking-wider">SQL 模式</span>
          <select v-model="taskDraft.sql_mode" class="bg-slate-50">
            <option value="single">单 SQL（源/目标共用同一段 SQL）</option>
            <option value="double">双 SQL（源/目标分别填写）</option>
          </select>
        </label>
        <label>
          <span class="muted mb-1 block text-[10px] font-bold uppercase tracking-wider">关联项目空间</span>
          <select v-model="taskDraft.project_id" class="bg-slate-50">
            <option value="">全局（无项目）</option>
            <option v-for="p in projects" :key="p.id" :value="p.id">{{ p.name }}</option>
          </select>
        </label>
      </div>
    </div>

    <!-- 源 / 目标 -->
    <div class="grid grid-cols-1 gap-4 xl:grid-cols-2">
      <DataSourcePanel side="source" />
      <DataSourcePanel side="target" />
    </div>

    <FieldCachePanel
      title="预览字段缓存"
      hint="SQL 预览或提取字段后，列名会缓存到这里；可以直接设主键和忽略字段，也会带到后续步骤。"
      compact
    />
  </section>
</template>
