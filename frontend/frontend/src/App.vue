<script setup>
// S2.B 收尾：所有 view / component 直接 useXxxStore()，不再走 inject('app')。
// App.vue 只剩 shell 编排：登录守卫 / bootstrap 拉数据 / 路由切换清轮询 /
// AI 翻译事件 → noticeStore。
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import AppShell from './layouts/AppShell.vue'
import { useNoticeStore } from './stores/notice'
import { useDatasourceStore } from './stores/datasource'
import { useTaskStore } from './stores/task'
import { useWorkflowStore } from './stores/workflow'
import { useBootstrapStore } from './stores/bootstrap'
import { useAuthStore } from './stores/auth'
import { useProjectStore } from './stores/project'

const noticeStore = useNoticeStore()
const datasourceStore = useDatasourceStore()
const taskStore = useTaskStore()
const workflowStore = useWorkflowStore()
const bootstrapStore = useBootstrapStore()
const authStore = useAuthStore()
const projectStore = useProjectStore()

const route = useRoute()
const loading = ref(false)

// loadBootstrap：跨 store 联动 —— bootstrap.reload 之后给默认 db_type / 默认
// datasource / 自动选第一个任务。是 App.vue 唯一保留的"业务联动"，因为它
// 跨 datasource + task + bootstrap 三个 store，放任一 store 都不合适。
async function loadBootstrap({ keepTaskSelection = false } = {}) {
  const previousTaskId = taskStore.selectedTaskId
  loading.value = true
  try {
    await bootstrapStore.reload()
    workflowStore.workflowTemplates = bootstrapStore.state.workflowTemplates || []
    const { state } = bootstrapStore
    if (state.dbTypes.length) datasourceStore.datasourceDraft.db_type = state.dbTypes[0]
    if (state.datasources.length) {
      taskStore.taskDraft.source_id = state.datasources[0].id
      taskStore.taskDraft.target_id = state.datasources[0].id
    }
    if (state.tasks.length && !keepTaskSelection) {
      taskStore.selectTask(state.tasks[0].id)
    } else if (!state.tasks.length && !keepTaskSelection) {
      taskStore.selectTask('new')
    } else if (keepTaskSelection && previousTaskId !== 'new') {
      const refreshedTask = state.tasks.find((task) => task.id === previousTaskId)
      if (refreshedTask) {
        taskStore.selectedTaskId = previousTaskId
        taskStore.fillDraft(refreshedTask)
      }
    }
  } finally {
    loading.value = false
  }
}

// 含密码导出 —— 二次确认弹窗，避免误点把明文密码导出去。AppShell emit 上来。
function confirmIncludePasswords(event) {
  if (!confirm('导出文件将包含明文数据库密码。仅自用备份请确认；不要分享或提交到代码仓库。')) {
    event.preventDefault()
  }
}

// 切路由时停掉所有轮询定时器，避免离开页面后还在打 API
watch(() => route.path, () => {
  taskStore.stopAsyncPoll()
  workflowStore.stopWorkflowAsyncPoll()
})

// AI 错误翻译事件：api.js 在 5xx / 长 4xx 错误时 dispatch；塞进 noticeStore，
// AppShell 弹醒目卡片（区别于普通 4 秒 toast）。
function _onAITranslated(event) {
  if (event?.detail?.translation) {
    noticeStore.setAITranslation(event.detail)
  }
}

// 已登录才拉 bootstrap 数据 + refresh user；未登录走 LoginView 全屏（router 守卫已跳）
onMounted(async () => {
  window.addEventListener('dataops:error-translated', _onAITranslated)
  if (!authStore.isLoggedIn) return
  await authStore.refreshMe()  // 验证 token + 刷新 user 信息（过期则 401 自动跳 login）
  if (authStore.isLoggedIn) {
    await projectStore.reload()  // 项目列表先就位 —— sidebar dropdown / bootstrap 过滤都靠它
    await loadBootstrap()
  }
})
onUnmounted(() => {
  window.removeEventListener('dataops:error-translated', _onAITranslated)
  taskStore.stopAsyncPoll()
  workflowStore.stopWorkflowAsyncPoll()
})
</script>

<template>
  <!-- 登录页全屏渲染，不套 sidebar / topbar 壳 -->
  <router-view v-if="route.meta.public" />
  <AppShell v-else :loading="loading" @confirm-include-passwords="confirmIncludePasswords">
    <div class="px-6 py-6">
      <router-view />
    </div>
  </AppShell>
</template>
