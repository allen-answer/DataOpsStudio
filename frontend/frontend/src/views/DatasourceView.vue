<script setup>
import { storeToRefs } from 'pinia'
import { useProjectStore } from '../stores/project'
import { useBootstrapStore } from '../stores/bootstrap'
import { useDatasourceStore } from '../stores/datasource'

const bootstrapStore = useBootstrapStore()
const { state } = bootstrapStore
const { driverItems } = storeToRefs(bootstrapStore)
const loadBootstrap = bootstrapStore.reload    // sidebar / 顶部按钮重新拉数据用

const datasourceStore = useDatasourceStore()
const { editingDatasourceId } = storeToRefs(datasourceStore)
const {
  datasourceDraft, editDraft,
  startEditDatasource, cancelEditDatasource,
  updateDatasource, deleteDatasource, createDatasource, testDatasource,
} = datasourceStore

const projectStore = useProjectStore()
const { projects } = storeToRefs(projectStore)

function projectName(id) {
  if (!id) return '全局'
  const p = projects.value.find(x => x.id === id)
  return p ? p.name : `(已删除 ${id.slice(0, 6)})`
}
</script>

<template>
  <section class="space-y-6">
    <div class="flex items-end justify-between">
      <div>
        <h2 class="text-2xl font-bold text-slate-800">{{ $t('pages.datasources.title') }}</h2>
        <p class="mt-1 text-sm text-slate-500">{{ $t('pages.datasources.subtitle') }}</p>
      </div>
      <button class="rounded-lg bg-blue-600 px-5 py-2 text-sm font-bold text-white transition hover:bg-blue-700" @click="loadBootstrap">{{ $t('common.refresh') }}</button>
    </div>

    <div class="grid grid-cols-1 gap-4 md:grid-cols-4">
      <div v-for="[name, info] in driverItems" :key="name" class="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
        <div class="mb-3 flex items-center justify-between">
          <strong class="text-slate-800">{{ name }}</strong>
          <span class="h-3 w-3 rounded-full" :class="info.available ? 'bg-green-500 shadow-[0_0_0_4px_rgba(34,197,94,.12)]' : 'bg-slate-300'"></span>
        </div>
        <p class="text-xs text-slate-500">{{ info.available ? `已安装：${info.installed_modules.join(', ')}` : `缺失：${info.candidate_modules.join(' / ')}` }}</p>
      </div>
    </div>

    <div class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
      <h3 class="mb-4 font-bold text-slate-800">新增数据源</h3>
      <div class="grid grid-cols-4 gap-3">
        <input v-model="datasourceDraft.name" class="border-none bg-slate-50" placeholder="名称">
        <select v-model="datasourceDraft.db_type" class="border-none bg-slate-50"><option v-for="type in state.dbTypes" :key="type">{{ type }}</option></select>
        <input v-model="datasourceDraft.host" class="border-none bg-slate-50" placeholder="Host">
        <input v-model="datasourceDraft.port" class="border-none bg-slate-50" type="number" placeholder="Port">
        <input v-model="datasourceDraft.database" class="border-none bg-slate-50" placeholder="数据库 / 服务名">
        <input v-model="datasourceDraft.username" class="border-none bg-slate-50" placeholder="用户名">
        <input v-model="datasourceDraft.password" class="border-none bg-slate-50" type="password" placeholder="密码">
        <button class="rounded-lg bg-blue-600 px-4 py-2 text-sm font-bold text-white transition hover:bg-blue-700" @click="createDatasource">添加数据源</button>
      </div>
    </div>

    <div class="grid grid-cols-1 gap-6 md:grid-cols-2 xl:grid-cols-3">
      <article v-for="item in state.datasources" :key="item.id" class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm transition hover:shadow-md">
        <template v-if="editingDatasourceId === item.id">
          <div class="mb-4 flex items-center justify-between">
            <span class="text-sm font-bold text-slate-700">编辑数据源</span>
            <button class="text-xs text-slate-400 hover:text-slate-700" @click="cancelEditDatasource">取消</button>
          </div>
          <div class="grid grid-cols-2 gap-2 text-sm">
            <input v-model="editDraft.name" class="col-span-2 border-none bg-slate-50 px-3 py-2" placeholder="名称">
            <select v-model="editDraft.db_type" class="border-none bg-slate-50 px-3 py-2"><option v-for="type in state.dbTypes" :key="type">{{ type }}</option></select>
            <input v-model="editDraft.port" class="border-none bg-slate-50 px-3 py-2" type="number" placeholder="Port">
            <input v-model="editDraft.host" class="col-span-2 border-none bg-slate-50 px-3 py-2" placeholder="Host">
            <input v-model="editDraft.database" class="col-span-2 border-none bg-slate-50 px-3 py-2" placeholder="数据库 / 服务名">
            <input v-model="editDraft.username" class="border-none bg-slate-50 px-3 py-2" placeholder="用户名">
            <input v-model="editDraft.password" class="border-none bg-slate-50 px-3 py-2" type="password" placeholder="密码（留空不修改）">
            <select v-model="editDraft.project_id" class="col-span-2 border-none bg-slate-50 px-3 py-2" title="所属项目">
              <option value="">全局（无项目）</option>
              <option v-for="p in projects" :key="p.id" :value="p.id">{{ p.name }}</option>
            </select>
          </div>
          <div class="mt-4 flex gap-2 border-t border-slate-100 pt-4">
            <button class="flex-1 rounded-lg bg-blue-600 py-2 text-xs font-bold text-white transition hover:bg-blue-700" @click="updateDatasource(item.id)">保存</button>
            <button class="flex-1 rounded-lg border border-slate-200 py-2 text-xs font-bold text-slate-600 transition hover:bg-slate-50" @click="cancelEditDatasource">取消</button>
          </div>
        </template>
        <template v-else>
          <div class="mb-4 flex items-start justify-between">
            <div class="grid h-12 w-12 place-items-center rounded-xl bg-slate-100 text-sm font-black text-slate-600">DS</div>
            <span class="rounded bg-green-100 px-2 py-1 text-[10px] font-bold uppercase tracking-widest text-green-700">已配置</span>
          </div>
          <h3 class="mb-1 font-bold text-slate-800">{{ item.name }}</h3>
          <p class="sql-font mb-2 text-xs text-slate-400">{{ item.db_type }} · {{ item.host }}:{{ item.port }} {{ item.database }}</p>
          <p class="mb-4 text-[10px] uppercase tracking-wider text-slate-400">项目：{{ projectName(item.project_id) }}</p>
          <div class="flex gap-2 border-t border-slate-100 pt-4">
            <button class="flex-1 rounded-lg border border-slate-200 py-2 text-xs font-bold text-slate-600 transition hover:bg-slate-50" @click="testDatasource(item.id)">测试连接</button>
            <button class="flex-1 rounded-lg border border-slate-200 py-2 text-xs font-bold text-slate-600 transition hover:bg-slate-50" @click="startEditDatasource(item)">编辑</button>
            <button class="rounded-lg border border-red-100 px-3 py-2 text-xs font-bold text-red-500 transition hover:bg-red-50" @click="deleteDatasource(item.id)">删除</button>
          </div>
        </template>
      </article>
    </div>
  </section>
</template>
