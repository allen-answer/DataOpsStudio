<script setup>
// Phase 10 #4：表资产详情页 MVP。
// 路由 /assets/table/:name —— 拿表名反向查找谁在引用：tasks / workflows /
// lineage_scripts / history。下个 sprint 接全局 lineage 索引后会补：role /
// refresh_mode / 字段列表 / classification (PII/SLA/owner) 等。
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ChevronLeft, Database, GitCompareArrows, Workflow, FileCode, History as HistoryIcon, AlertCircle } from 'lucide-vue-next'
import { apiGet } from '../api'

const route = useRoute()
const router = useRouter()

const loading = ref(false)
const error = ref('')
const asset = ref(null)

const tableName = computed(() => route.params.name || '')

async function load() {
  if (!tableName.value) return
  loading.value = true
  error.value = ''
  try {
    asset.value = await apiGet(`/api/assets/table/${encodeURIComponent(tableName.value)}`)
  } catch (e) {
    error.value = `加载失败：${e.message || e}`
    asset.value = null
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(() => route.params.name, load)

function gotoTask(taskId) {
  router.push({ path: '/data-compare', query: { task: taskId } })
}
function gotoWorkflow(id) {
  router.push(`/workflows/${id}`)
}
function gotoWorkflowRun(runId) {
  router.push(`/workflow-runs/${runId}`)
}
</script>

<template>
  <section class="space-y-4">
    <!-- 顶部 -->
    <div class="flex items-start gap-3">
      <button class="btn btn-ghost h-8 px-2" @click="router.back()">
        <ChevronLeft class="h-4 w-4" />
      </button>
      <div class="flex-1">
        <p class="muted text-[11px] uppercase tracking-wider">表资产</p>
        <h2 class="sql-font text-2xl font-bold text-slate-800">{{ tableName }}</h2>
        <p v-if="asset" class="muted mt-0.5 text-xs">
          schema: <strong>{{ asset.schema }}</strong> · basename:
          <strong>{{ asset.basename }}</strong>
          <span v-if="asset.stats?.total_references != null" class="ml-2">
            · 共 <strong class="text-primary">{{ asset.stats.total_references }}</strong> 处引用
          </span>
        </p>
      </div>
    </div>

    <!-- Phase 10 #3 v1：从全局 lineage 索引拉来的元数据（role / refresh_mode / 上下游） -->
    <div v-if="asset && (asset.primary_role || asset.refresh_mode || asset.upstream_count || asset.downstream_count)" class="card flex flex-wrap items-center gap-3 p-4">
      <span v-if="asset.primary_role" class="pill bg-blue-100 text-blue-700">
        role: {{ asset.primary_role }}
      </span>
      <span v-if="asset.refresh_mode" class="pill bg-emerald-100 text-emerald-700">
        refresh: {{ asset.refresh_mode }}
        <span v-if="asset.refresh_modes?.length > 1" class="ml-1 text-[10px] opacity-70">
          (+{{ asset.refresh_modes.length - 1 }} 其它)
        </span>
      </span>
      <span class="pill bg-slate-100 text-slate-700">
        上游 <strong>{{ asset.upstream_count }}</strong>
      </span>
      <span class="pill bg-slate-100 text-slate-700">
        下游 <strong>{{ asset.downstream_count }}</strong>
      </span>
      <span v-if="asset.last_seen_at" class="muted ml-auto text-[11px]">
        上次出现 {{ asset.last_seen_at }}
      </span>
    </div>

    <div v-if="loading" class="card p-4 text-sm text-slate-500">加载中…</div>
    <div v-if="error" class="card border-status-error-bg bg-status-error-bg/40 p-3 text-sm text-status-error">
      <AlertCircle class="mr-1 inline h-4 w-4" /> {{ error }}
    </div>

    <div v-if="asset && !loading" class="grid gap-4 md:grid-cols-2">
      <!-- 任务引用 -->
      <article class="card p-4">
        <header class="mb-2 flex items-center gap-2">
          <GitCompareArrows class="h-4 w-4 text-blue-600" />
          <h3 class="text-sm font-bold text-slate-800">对比任务</h3>
          <span class="pill bg-blue-100 text-blue-700">{{ asset.references.tasks.length }}</span>
        </header>
        <ul v-if="asset.references.tasks.length" class="space-y-1.5">
          <li v-for="t in asset.references.tasks" :key="t.id">
            <button
              class="w-full rounded p-2 text-left text-sm hover:bg-slate-50"
              @click="gotoTask(t.id)"
            >
              <span class="font-medium">{{ t.name }}</span>
              <span class="muted ml-2 text-[11px]">{{ t.match_role }}</span>
            </button>
          </li>
        </ul>
        <p v-else class="muted text-xs">没有任务引用此表。</p>
      </article>

      <!-- 作业流引用 -->
      <article class="card p-4">
        <header class="mb-2 flex items-center gap-2">
          <Workflow class="h-4 w-4 text-purple-600" />
          <h3 class="text-sm font-bold text-slate-800">作业流</h3>
          <span class="pill bg-purple-100 text-purple-700">{{ asset.references.workflows.length }}</span>
        </header>
        <ul v-if="asset.references.workflows.length" class="space-y-1.5">
          <li v-for="w in asset.references.workflows" :key="w.id">
            <button
              class="w-full rounded p-2 text-left text-sm hover:bg-slate-50"
              @click="gotoWorkflow(w.id)"
            >
              <span class="font-medium">{{ w.name }}</span>
              <span class="muted ml-2 text-[11px]">{{ w.node_count }} 节点</span>
            </button>
          </li>
        </ul>
        <p v-else class="muted text-xs">没有作业流引用此表。</p>
      </article>

      <!-- 血缘脚本（来自 workflow run） -->
      <article class="card p-4">
        <header class="mb-2 flex items-center gap-2">
          <FileCode class="h-4 w-4 text-emerald-600" />
          <h3 class="text-sm font-bold text-slate-800">血缘脚本</h3>
          <span class="pill bg-emerald-100 text-emerald-700">{{ asset.references.lineage_scripts.length }}</span>
        </header>
        <ul v-if="asset.references.lineage_scripts.length" class="space-y-1.5">
          <li v-for="(s, i) in asset.references.lineage_scripts" :key="i">
            <button
              class="w-full rounded p-2 text-left text-sm hover:bg-slate-50"
              @click="gotoWorkflowRun(s.run_id)"
            >
              <span class="sql-font font-medium">{{ s.file_name }}</span>
              <span class="muted ml-2 text-[11px]">{{ s.match_role }} · run {{ s.run_id?.slice(0, 8) }}</span>
            </button>
          </li>
        </ul>
        <p v-else class="muted text-xs">最近的 workflow run 中没有血缘脚本引用此表。</p>
      </article>

      <!-- 历史 -->
      <article class="card p-4">
        <header class="mb-2 flex items-center gap-2">
          <HistoryIcon class="h-4 w-4 text-amber-600" />
          <h3 class="text-sm font-bold text-slate-800">执行历史</h3>
          <span class="pill bg-amber-100 text-amber-700">{{ asset.references.history.length }}</span>
        </header>
        <ul v-if="asset.references.history.length" class="space-y-1.5">
          <li v-for="h in asset.references.history" :key="h.id" class="rounded p-2 text-sm">
            <span class="font-medium">{{ h.task_name }}</span>
            <span class="muted ml-2 text-[11px]">{{ h.started_at }} · {{ h.status }}</span>
          </li>
        </ul>
        <p v-else class="muted text-xs">没有相关执行历史。</p>
      </article>
    </div>

    <!-- 下个 sprint：classification / 字段列表 -->
    <div v-if="asset && !loading" class="card border-slate-200 bg-slate-50 p-3 text-xs text-slate-500">
      <strong>下个 sprint 计划</strong>：classification (PII / SLA / owner) +
      字段列表 + 字段血缘热点。当前已支持：反向引用（4 类）+ 全局索引元数据
      （role / refresh_mode / 上下游计数 / 最近出现 run）。
    </div>
  </section>
</template>
