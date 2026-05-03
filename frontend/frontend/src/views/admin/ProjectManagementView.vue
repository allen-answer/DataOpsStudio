<script setup>
import { onMounted, reactive, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { Plus, Trash2, RefreshCw, FolderOpen } from 'lucide-vue-next'
import { useProjectStore } from '../../stores/project'
import { useNoticeStore } from '../../stores/notice'

const projectStore = useProjectStore()
const noticeStore = useNoticeStore()
const { projects, currentProjectId } = storeToRefs(projectStore)

const submitting = ref(false)
const draft = reactive({ name: '', description: '' })

async function reload() {
  await projectStore.reload()
}

async function createProject() {
  if (!draft.name.trim()) {
    noticeStore.setNotice('项目名称必填')
    return
  }
  submitting.value = true
  try {
    await projectStore.createProject(draft.name.trim(), draft.description.trim())
    noticeStore.setNotice(`项目 ${draft.name} 已创建`)
    draft.name = ''
    draft.description = ''
  } catch (err) {
    noticeStore.setNotice(`创建失败：${err.message || err}`)
  } finally {
    submitting.value = false
  }
}

async function deleteProject(p) {
  if (!confirm(`确认删除项目 ${p.name}？关联的资源不会被删除，但会变为"无项目"状态。`)) return
  try {
    await projectStore.deleteProject(p.id)
    noticeStore.setNotice(`项目 ${p.name} 已删除`)
  } catch (err) {
    noticeStore.setNotice(`删除失败：${err.message || err}`)
  }
}

function switchTo(p) {
  projectStore.setProject(p.id)
  noticeStore.setNotice(`已切换到项目 ${p.name}`)
}

onMounted(() => {
  reload()
})
</script>

<template>
  <section class="space-y-6">
    <header class="flex items-end justify-between">
      <div>
        <h2 class="text-2xl font-bold text-slate-800">项目管理</h2>
        <p class="mt-1 text-sm text-slate-500">管理项目空间 —— 数据源 / 任务 / 作业流可关联到项目，列表默认按当前项目筛选</p>
      </div>
      <button class="btn btn-outline gap-1.5" @click="reload">
        <RefreshCw class="h-4 w-4" />
        刷新
      </button>
    </header>

    <!-- 新建项目 -->
    <div class="card p-5">
      <h3 class="mb-3 text-sm font-bold text-slate-700">新建项目</h3>
      <div class="grid grid-cols-1 gap-3 md:grid-cols-3">
        <input v-model="draft.name" placeholder="项目名（必填）" />
        <input v-model="draft.description" placeholder="描述（可选）" class="md:col-span-2" />
      </div>
      <div class="mt-3 flex justify-end">
        <button class="btn btn-primary gap-1.5" :disabled="submitting" @click="createProject">
          <Plus class="h-4 w-4" />
          创建项目
        </button>
      </div>
    </div>

    <!-- 项目列表 -->
    <div class="card overflow-hidden">
      <table class="w-full">
        <thead class="bg-slate-50">
          <tr class="text-left text-xs uppercase tracking-wider text-slate-500">
            <th class="px-4 py-3 font-bold">名称</th>
            <th class="px-4 py-3 font-bold">描述</th>
            <th class="px-4 py-3 font-bold">成员数</th>
            <th class="px-4 py-3 font-bold">创建时间</th>
            <th class="px-4 py-3 text-right font-bold">操作</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-slate-100">
          <tr v-if="!projects.length">
            <td colspan="5" class="px-4 py-8 text-center text-sm text-slate-400">暂无项目</td>
          </tr>
          <tr v-for="p in projects" :key="p.id" class="text-sm">
            <td class="px-4 py-3 font-medium text-slate-800">
              <FolderOpen class="mr-1.5 inline h-4 w-4 text-primary" />
              {{ p.name }}
              <span v-if="p.id === currentProjectId" class="pill bg-status-info-bg text-status-info ml-2">当前</span>
            </td>
            <td class="px-4 py-3 text-slate-600">{{ p.description || '-' }}</td>
            <td class="px-4 py-3 text-slate-500">{{ p.members?.length || 0 }}</td>
            <td class="px-4 py-3 text-xs text-slate-500">{{ p.created_at || '-' }}</td>
            <td class="px-4 py-3 text-right">
              <div class="flex justify-end gap-1.5">
                <button
                  class="btn btn-outline h-8 px-2 text-xs"
                  :disabled="p.id === currentProjectId"
                  @click="switchTo(p)"
                >
                  切换到
                </button>
                <button class="btn btn-danger h-8 gap-1 px-2 text-xs" @click="deleteProject(p)">
                  <Trash2 class="h-3.5 w-3.5" /> 删除
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
