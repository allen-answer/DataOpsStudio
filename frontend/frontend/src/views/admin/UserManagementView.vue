<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { Plus, Trash2, RefreshCw, Save, X } from 'lucide-vue-next'
import { apiGet, apiJson } from '../../api'
import { useAuthStore } from '../../stores/auth'
import { useNoticeStore } from '../../stores/notice'

type Role = 'viewer' | 'editor' | 'admin'

interface UserItem {
  id: string
  username: string
  role: Role
  display_name?: string
}

const authStore = useAuthStore()
const noticeStore = useNoticeStore()
const { user: currentUser } = storeToRefs(authStore)

const users = ref<UserItem[]>([])
const loading = ref<boolean>(false)
const submitting = ref<boolean>(false)
const editingId = ref<string>('')

const draft = reactive({
  username: '',
  password: '',
  role: 'viewer' as Role,
  display_name: '',
})

const editDraft = reactive({
  password: '',
  role: 'viewer' as Role,
  display_name: '',
})

const ROLES: Array<{ value: Role; label: string }> = [
  { value: 'viewer', label: '只读 viewer' },
  { value: 'editor', label: '编辑 editor' },
  { value: 'admin',  label: '管理员 admin' },
]

const sortedUsers = computed<UserItem[]>(() =>
  [...users.value].sort((a, b) => a.username.localeCompare(b.username, 'zh-CN'))
)

async function reload(): Promise<void> {
  loading.value = true
  try {
    users.value = await apiGet<UserItem[]>('/api/users')
  } catch (err: any) {
    noticeStore.setNotice(`加载用户失败：${err?.message || err}`)
  } finally {
    loading.value = false
  }
}

function resetDraft(): void {
  draft.username = ''
  draft.password = ''
  draft.role = 'viewer'
  draft.display_name = ''
}

async function createUser(): Promise<void> {
  if (!draft.username.trim() || !draft.password.trim()) {
    noticeStore.setNotice('用户名和密码必填')
    return
  }
  submitting.value = true
  try {
    await apiJson('/api/users', 'POST', {
      username: draft.username.trim(),
      password: draft.password,
      role: draft.role,
      display_name: draft.display_name.trim(),
    })
    noticeStore.setNotice(`用户 ${draft.username} 已创建`)
    resetDraft()
    await reload()
  } catch (err: any) {
    noticeStore.setNotice(`创建失败：${err?.message || err}`)
  } finally {
    submitting.value = false
  }
}

function startEdit(user: UserItem): void {
  editingId.value = user.id
  editDraft.password = ''
  editDraft.role = user.role
  editDraft.display_name = user.display_name || ''
}

function cancelEdit(): void {
  editingId.value = ''
}

async function saveEdit(user: UserItem): Promise<void> {
  submitting.value = true
  const payload = {
    password: editDraft.password,
    role: editDraft.role,
    display_name: editDraft.display_name,
  }
  try {
    await apiJson(`/api/users/${user.id}`, 'PUT', payload)
    noticeStore.setNotice(`用户 ${user.username} 已更新`)
    editingId.value = ''
    await reload()
  } catch (err: any) {
    noticeStore.setNotice(`更新失败：${err?.message || err}`)
  } finally {
    submitting.value = false
  }
}

async function deleteUser(user: UserItem): Promise<void> {
  if (user.id === currentUser.value?.id) {
    noticeStore.setNotice('不能删除当前登录账号')
    return
  }
  if (!confirm(`确认删除用户 ${user.username}？此操作不可撤销。`)) return
  try {
    await apiJson(`/api/users/${user.id}`, 'DELETE')
    noticeStore.setNotice(`用户 ${user.username} 已删除`)
    await reload()
  } catch (err: any) {
    noticeStore.setNotice(`删除失败：${err?.message || err}`)
  }
}

onMounted(() => {
  reload()
})
</script>

<template>
  <section class="space-y-6">
    <header class="flex items-end justify-between">
      <div>
        <h2 class="text-2xl font-bold text-slate-800">{{ $t('admin.usersTitle') }}</h2>
        <p class="mt-1 text-sm text-slate-500">管理账号 / role / 密码 —— 仅 admin 可见</p>
      </div>
      <button class="btn btn-outline gap-1.5" @click="reload">
        <RefreshCw class="h-4 w-4" :class="loading ? 'animate-spin' : ''" />
        {{ $t('common.refresh') }}
      </button>
    </header>

    <!-- 新建用户 -->
    <div class="card p-5">
      <h3 class="mb-3 text-sm font-bold text-slate-700">{{ $t('admin.usersCreate') }}</h3>
      <div class="grid grid-cols-1 gap-3 md:grid-cols-4">
        <input v-model="draft.username" placeholder="用户名" />
        <input v-model="draft.password" type="password" placeholder="初始密码（≥4 字符）" />
        <input v-model="draft.display_name" placeholder="显示名（可选）" />
        <select v-model="draft.role">
          <option v-for="r in ROLES" :key="r.value" :value="r.value">{{ r.label }}</option>
        </select>
      </div>
      <div class="mt-3 flex justify-end">
        <button class="btn btn-primary gap-1.5" :disabled="submitting" @click="createUser">
          <Plus class="h-4 w-4" />
          创建用户
        </button>
      </div>
    </div>

    <!-- 用户列表 -->
    <div class="card overflow-hidden">
      <table class="w-full">
        <thead class="bg-slate-50">
          <tr class="text-left text-xs uppercase tracking-wider text-slate-500">
            <th class="px-4 py-3 font-bold">用户名</th>
            <th class="px-4 py-3 font-bold">显示名</th>
            <th class="px-4 py-3 font-bold">Role</th>
            <th class="px-4 py-3 font-bold">创建时间</th>
            <th class="px-4 py-3 text-right font-bold">操作</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-slate-100">
          <tr v-if="!sortedUsers.length">
            <td colspan="5" class="px-4 py-8 text-center text-sm text-slate-400">
              暂无用户
            </td>
          </tr>
          <tr v-for="u in sortedUsers" :key="u.id" class="text-sm">
            <td class="px-4 py-3 font-medium text-slate-800">
              {{ u.username }}
              <span v-if="u.id === currentUser?.id" class="pill bg-status-info-bg text-status-info ml-2">当前账号</span>
            </td>
            <template v-if="editingId === u.id">
              <td class="px-4 py-3">
                <input v-model="editDraft.display_name" placeholder="显示名" />
              </td>
              <td class="px-4 py-3">
                <select v-model="editDraft.role">
                  <option v-for="r in ROLES" :key="r.value" :value="r.value">{{ r.label }}</option>
                </select>
              </td>
              <td class="px-4 py-3 text-slate-500">
                <input v-model="editDraft.password" type="password" placeholder="留空 = 不改密码" class="w-full" />
              </td>
              <td class="px-4 py-3 text-right">
                <div class="flex justify-end gap-1.5">
                  <button class="btn btn-primary h-8 gap-1 px-2 text-xs" :disabled="submitting" @click="saveEdit(u)">
                    <Save class="h-3.5 w-3.5" /> 保存
                  </button>
                  <button class="btn btn-outline h-8 gap-1 px-2 text-xs" @click="cancelEdit">
                    <X class="h-3.5 w-3.5" /> 取消
                  </button>
                </div>
              </td>
            </template>
            <template v-else>
              <td class="px-4 py-3 text-slate-700">{{ u.display_name || '-' }}</td>
              <td class="px-4 py-3">
                <span class="pill" :class="{
                  'bg-rose-100 text-rose-700': u.role === 'admin',
                  'bg-blue-100 text-blue-700': u.role === 'editor',
                  'bg-slate-100 text-slate-600': u.role === 'viewer',
                }">{{ u.role }}</span>
              </td>
              <td class="px-4 py-3 text-xs text-slate-500">{{ u.created_at || '-' }}</td>
              <td class="px-4 py-3 text-right">
                <div class="flex justify-end gap-1.5">
                  <button class="btn btn-outline h-8 px-2 text-xs" @click="startEdit(u)">编辑</button>
                  <button
                    class="btn btn-danger h-8 gap-1 px-2 text-xs"
                    :disabled="u.id === currentUser?.id"
                    @click="deleteUser(u)"
                  >
                    <Trash2 class="h-3.5 w-3.5" /> 删除
                  </button>
                </div>
              </td>
            </template>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
