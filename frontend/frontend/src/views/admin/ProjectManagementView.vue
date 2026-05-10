<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { Plus, Trash2, RefreshCw, FolderOpen, Users, X } from 'lucide-vue-next'
import { apiGet } from '../../api'
import { useProjectStore } from '../../stores/project'
import { useNoticeStore } from '../../stores/notice'

interface UserItem {
  id: string
  username: string
  display_name?: string
}

interface ProjectItem {
  id: string
  name: string
  description?: string
  members?: string[]
}

const projectStore = useProjectStore()
const noticeStore = useNoticeStore()
const { projects, currentProjectId } = storeToRefs(projectStore)

const submitting = ref<boolean>(false)
const draft = reactive({ name: '', description: '' })

// 成员管理 panel：当前展开哪个项目 + 编辑中的 members
const expandedId = ref<string>('')
const memberDraft = reactive<{ name: string; description: string; members: string[] }>({
  name: '', description: '', members: [],
})
const allUsers = ref<UserItem[]>([])  // /api/users (admin only)

const userById = computed<Record<string, UserItem>>(() => {
  const map: Record<string, UserItem> = {}
  allUsers.value.forEach(u => { map[u.id] = u })
  return map
})

const candidateUsers = computed<UserItem[]>(() =>
  allUsers.value.filter(u => !memberDraft.members.includes(u.id))
)

async function reload(): Promise<void> {
  await projectStore.reload()
  // admin only —— /api/users 也只 admin 可见，与本页一致
  try {
    allUsers.value = await apiGet<UserItem[]>('/api/users')
  } catch {
    allUsers.value = []
  }
}

async function createProject(): Promise<void> {
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
  } catch (err: any) {
    noticeStore.setNotice(`创建失败：${err?.message || err}`)
  } finally {
    submitting.value = false
  }
}

async function deleteProject(p: ProjectItem): Promise<void> {
  if (!confirm(`确认删除项目 ${p.name}？关联的资源不会被删除，但会变为"无项目"状态。`)) return
  try {
    await projectStore.deleteProject(p.id)
    noticeStore.setNotice(`项目 ${p.name} 已删除`)
  } catch (err: any) {
    noticeStore.setNotice(`删除失败：${err?.message || err}`)
  }
}

function switchTo(p: ProjectItem): void {
  projectStore.setProject(p.id)
  noticeStore.setNotice(`已切换到项目 ${p.name}`)
}

function expandMembers(p: ProjectItem): void {
  if (expandedId.value === p.id) {
    expandedId.value = ''
    return
  }
  expandedId.value = p.id
  memberDraft.name = p.name
  memberDraft.description = p.description || ''
  memberDraft.members = [...(p.members || [])]
}

function addMember(userId: string): void {
  if (!userId) return
  if (memberDraft.members.includes(userId)) return
  memberDraft.members.push(userId)
}

function removeMember(userId: string, ownerId: string): void {
  if (userId === ownerId) {
    noticeStore.setNotice('不能移除项目 owner（owner 永远是成员）')
    return
  }
  memberDraft.members = memberDraft.members.filter(id => id !== userId)
}

async function saveMembers(p: ProjectItem): Promise<void> {
  submitting.value = true
  try {
    await projectStore.updateProject(p.id, {
      name: memberDraft.name,
      description: memberDraft.description,
      members: memberDraft.members,
    })
    noticeStore.setNotice(`项目 ${p.name} 成员已更新`)
    expandedId.value = ''
  } catch (err: any) {
    noticeStore.setNotice(`更新失败：${err?.message || err}`)
  } finally {
    submitting.value = false
  }
}

function userLabel(userId: string): string {
  const u = userById.value[userId]
  if (!u) return `(已删除 ${userId.slice(0, 6)})`
  return u.display_name ? `${u.display_name} (${u.username})` : u.username
}

onMounted(() => {
  reload()
})
</script>

<template>
  <section class="space-y-6">
    <header class="flex items-end justify-between">
      <div>
        <h2 class="text-2xl font-bold text-slate-800">{{ $t('admin.projectsTitle') }}</h2>
        <p class="mt-1 text-sm text-slate-500">管理项目空间 + 成员 —— 数据源 / 任务 / 作业流可关联到项目，列表默认按当前项目筛选</p>
      </div>
      <button class="btn btn-outline gap-1.5" @click="reload">
        <RefreshCw class="h-4 w-4" />
        {{ $t('common.refresh') }}
      </button>
    </header>

    <!-- 新建项目 -->
    <div class="card p-5">
      <h3 class="mb-3 text-sm font-bold text-slate-700">{{ $t('admin.projectsCreate') }}</h3>
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
          <template v-for="p in projects" :key="p.id">
            <tr class="text-sm">
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
                  <button class="btn btn-outline h-8 gap-1 px-2 text-xs" @click="expandMembers(p)">
                    <Users class="h-3.5 w-3.5" />
                    {{ expandedId === p.id ? '收起' : '成员' }}
                  </button>
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
            <!-- 成员管理面板（行内展开） -->
            <tr v-if="expandedId === p.id">
              <td colspan="5" class="bg-slate-50 px-6 py-4">
                <div class="space-y-3">
                  <div>
                    <p class="mb-1 text-[10px] font-bold uppercase tracking-wider text-slate-500">基本信息</p>
                    <div class="grid grid-cols-1 gap-2 md:grid-cols-2">
                      <input v-model="memberDraft.name" placeholder="项目名" />
                      <input v-model="memberDraft.description" placeholder="描述" />
                    </div>
                  </div>
                  <div>
                    <p class="mb-1 text-[10px] font-bold uppercase tracking-wider text-slate-500">
                      当前成员 ({{ memberDraft.members.length }}) —— owner: {{ userLabel(p.owner_id) }}
                    </p>
                    <ul class="flex flex-wrap gap-2">
                      <li v-for="uid in memberDraft.members" :key="uid"
                          class="flex items-center gap-1.5 rounded-full bg-white px-3 py-1 text-xs text-slate-700 ring-1 ring-slate-200">
                        <span>{{ userLabel(uid) }}</span>
                        <span v-if="uid === p.owner_id" class="pill bg-rose-100 text-rose-700 text-[10px]">owner</span>
                        <button v-else class="ml-1 text-slate-400 hover:text-rose-500" @click="removeMember(uid, p.owner_id)">
                          <X class="h-3 w-3" />
                        </button>
                      </li>
                      <li v-if="!memberDraft.members.length" class="text-xs text-slate-400">无</li>
                    </ul>
                  </div>
                  <div>
                    <p class="mb-1 text-[10px] font-bold uppercase tracking-wider text-slate-500">添加成员</p>
                    <div class="flex gap-2">
                      <select class="flex-1" @change="addMember($event.target.value); $event.target.value = ''">
                        <option value="">-- 选择用户 --</option>
                        <option v-for="u in candidateUsers" :key="u.id" :value="u.id">
                          {{ userLabel(u.id) }} · {{ u.role }}
                        </option>
                      </select>
                    </div>
                    <p v-if="!candidateUsers.length" class="mt-1 text-[11px] text-slate-400">所有用户都已在成员列表里</p>
                  </div>
                  <div class="flex justify-end gap-2">
                    <button class="btn btn-outline h-8 px-3 text-xs" @click="expandedId = ''">取消</button>
                    <button class="btn btn-primary h-8 px-3 text-xs" :disabled="submitting" @click="saveMembers(p)">
                      保存
                    </button>
                  </div>
                </div>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </section>
</template>
