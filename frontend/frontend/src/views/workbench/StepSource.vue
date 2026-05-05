<script setup>
import { storeToRefs } from 'pinia'
import DataSourcePanel from './DataSourcePanel.vue'
import FieldCachePanel from './FieldCachePanel.vue'
import { useProjectStore } from '../../stores/project'
import { useBootstrapStore } from '../../stores/bootstrap'
import { useTaskStore } from '../../stores/task'

const { state } = useBootstrapStore()
const { taskDraft } = useTaskStore()
const { projects } = storeToRefs(useProjectStore())
</script>

<template>
  <section class="space-y-4">
    <!-- 任务基础字段 -->
    <div class="card">
      <h3 class="mb-3 text-base font-semibold text-slate-800">{{ $t('workbench.source.sectionBasic') }}</h3>
      <div class="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
        <label>
          <span class="muted mb-1 block text-[10px] font-bold uppercase tracking-wider">{{ $t('workbench.source.taskName') }}</span>
          <input v-model="taskDraft.name" :placeholder="$t('workbench.source.taskNamePlaceholder')" class="bg-slate-50">
        </label>
        <label>
          <span class="muted mb-1 block text-[10px] font-bold uppercase tracking-wider">{{ $t('workbench.source.sourceDatasource') }}</span>
          <select v-model="taskDraft.source_id" class="bg-slate-50">
            <option value="">{{ $t('workbench.source.pickDatasource') }}</option>
            <option v-for="item in state.datasources" :key="item.id" :value="item.id">{{ item.name }}</option>
          </select>
        </label>
        <label>
          <span class="muted mb-1 block text-[10px] font-bold uppercase tracking-wider">{{ $t('workbench.source.targetDatasource') }}</span>
          <select v-model="taskDraft.target_id" class="bg-slate-50">
            <option value="">{{ $t('workbench.source.pickDatasource') }}</option>
            <option v-for="item in state.datasources" :key="item.id" :value="item.id">{{ item.name }}</option>
          </select>
        </label>
        <label>
          <span class="muted mb-1 block text-[10px] font-bold uppercase tracking-wider">{{ $t('workbench.source.sqlMode') }}</span>
          <select v-model="taskDraft.sql_mode" class="bg-slate-50">
            <option value="single">{{ $t('workbench.source.sqlModeSingle') }}</option>
            <option value="double">{{ $t('workbench.source.sqlModeDouble') }}</option>
          </select>
        </label>
        <label>
          <span class="muted mb-1 block text-[10px] font-bold uppercase tracking-wider">{{ $t('workbench.source.relatedProject') }}</span>
          <select v-model="taskDraft.project_id" class="bg-slate-50">
            <option value="">{{ $t('workbench.source.globalProject') }}</option>
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
      :title="$t('workbench.fieldCache.titlePreview')"
      hint="SQL 预览或提取字段后，列名会缓存到这里；可以直接设主键和忽略字段，也会带到后续步骤。"
      compact
    />
  </section>
</template>
