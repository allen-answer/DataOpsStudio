<script setup lang="ts">
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

function projectName(id: string): string {
  if (!id) return '全局'
  const p = projects.value.find(x => x.id === id)
  return p ? p.name : `(已删除 ${id.slice(0, 6)})`
}

// Phase 14 #1 合规防御 — 环境标签三色徽章 + prod 二次确认
function envBadgeClass(env?: string): string {
  if (env === 'prod') return 'bg-status-error-bg text-status-error'
  if (env === 'staging') return 'bg-status-warning-bg text-status-warning'
  return 'bg-status-success-bg text-status-success'  // sandbox (default)
}

function envBadgeTitle(env?: string): string {
  if (env === 'prod') return '生产环境 — 写入端点(materialize / run-all / record)已锁定'
  if (env === 'staging') return '预发环境 — 写入端点已锁定'
  return '沙盒环境 — 可造数据 / 跑模拟流程'
}

// Phase 14 #3 Round 3 — 环境预设:选环境时自动填 8 个 allow_* flag。
// sandbox = 全开 / staging+prod = 全关 / unknown = 全关。
// 用户可在「高级配置」折叠区手动调单个 flag。
type DsDraftWithFlags = {
  environment?: string
  environment_verified?: boolean
  allow_select?: boolean
  allow_explain?: boolean
  allow_dm_explain?: boolean
  allow_oracle_plan_table?: boolean
  allow_schema_import?: boolean
  allow_schema_save?: boolean
  allow_scenario_write?: boolean
  allow_record_task?: boolean
}

function applyEnvPreset(draft: DsDraftWithFlags, env: string) {
  // sandbox = 全开;其它环境 = 全关(让用户手动按需勾)
  const open = env === 'sandbox'
  draft.allow_select = true   // SELECT 任何环境都开(纯只读基本能力)
  draft.allow_explain = open
  draft.allow_dm_explain = open
  draft.allow_oracle_plan_table = open
  draft.allow_schema_import = open
  draft.allow_schema_save = open
  draft.allow_scenario_write = open
  draft.allow_record_task = open
  draft.environment_verified = env !== 'unknown'  // 用户显式选过 = 已验证
}

function onEnvChange(draft: DsDraftWithFlags) {
  // 选 prod 要二次确认(防误点)
  if (draft.environment === 'prod') {
    const ok = confirm(
      '⚠ 你正在把这个数据源标记为「生产环境」。\n\n'
      + '后续在此 ds 上调用沙盒写入端点(造数据 / 一键全套 / record 落 task)将被 403 拒绝。\n'
      + '只读分析(🔬 慢 SQL / ✨ AI 复核 / 🛡 校验)按 allow_explain / allow_dm_explain / '
      + 'allow_oracle_plan_table 单独控制 — 默认全关,需要时去「高级配置」打开。\n\n'
      + '确定改成 prod 吗?'
    )
    if (!ok) {
      draft.environment = 'sandbox'
      applyEnvPreset(draft, 'sandbox')
      return
    }
  }
  applyEnvPreset(draft, draft.environment || 'sandbox')
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
        <select
          v-model="(datasourceDraft as any).environment"
          class="border-none bg-slate-50"
          title="环境标签:沙盒 = 可造数据 / 跑模拟流程;预发 / 生产 = 写入端点拒绝(防误灌假数据)"
          @change="onEnvChange(datasourceDraft as any)"
        >
          <option value="sandbox">🟢 sandbox 沙盒(可造数据)</option>
          <option value="staging">🟡 staging 预发(只读)</option>
          <option value="prod">🔴 prod 生产(只读 / 严禁造数据)</option>
        </select>
        <button class="rounded-lg bg-blue-600 px-4 py-2 text-sm font-bold text-white transition hover:bg-blue-700" @click="createDatasource">添加数据源</button>
      </div>
      <!-- Phase 14 #3 Round 3 — 高级配置折叠 8 个 allow_* flag -->
      <details class="mt-3 text-xs">
        <summary class="cursor-pointer text-slate-500 hover:text-slate-700 select-none">
          ⚙ 高级配置:操作权限 allow_* (按环境预填,可手动微调)
        </summary>
        <div class="mt-3 grid grid-cols-2 md:grid-cols-4 gap-2 text-slate-700">
          <label class="flex items-center gap-1.5"><input type="checkbox" v-model="(datasourceDraft as any).allow_select" /><span>allow_select<br/><span class="text-[10px] text-slate-400">SELECT 查询</span></span></label>
          <label class="flex items-center gap-1.5"><input type="checkbox" v-model="(datasourceDraft as any).allow_explain" /><span>allow_explain<br/><span class="text-[10px] text-slate-400">MySQL EXPLAIN</span></span></label>
          <label class="flex items-center gap-1.5"><input type="checkbox" v-model="(datasourceDraft as any).allow_dm_explain" /><span>allow_dm_explain<br/><span class="text-[10px] text-slate-400">DM EXPLAIN</span></span></label>
          <label class="flex items-center gap-1.5"><input type="checkbox" v-model="(datasourceDraft as any).allow_oracle_plan_table" /><span>allow_oracle_plan_table<br/><span class="text-[10px] text-slate-400">Oracle PLAN_TABLE 诊断写入</span></span></label>
          <label class="flex items-center gap-1.5"><input type="checkbox" v-model="(datasourceDraft as any).allow_schema_import" /><span>allow_schema_import<br/><span class="text-[10px] text-slate-400">读 information_schema</span></span></label>
          <label class="flex items-center gap-1.5"><input type="checkbox" v-model="(datasourceDraft as any).allow_schema_save" /><span>allow_schema_save<br/><span class="text-[10px] text-slate-400">保存 yml 到本地</span></span></label>
          <label class="flex items-center gap-1.5"><input type="checkbox" v-model="(datasourceDraft as any).allow_scenario_write" /><span>allow_scenario_write<br/><span class="text-[10px] text-slate-400">造数据 materialize</span></span></label>
          <label class="flex items-center gap-1.5"><input type="checkbox" v-model="(datasourceDraft as any).allow_record_task" /><span>allow_record_task<br/><span class="text-[10px] text-slate-400">record CompareTask</span></span></label>
        </div>
        <p class="mt-2 text-[11px] text-slate-400 leading-relaxed">
          💡 选环境时会自动预填:sandbox = 全开,staging / prod = 仅 allow_select 开。
          可在此手动调整(如 prod 数据源只开 allow_dm_explain 让 DBA 查 plan)。
        </p>
      </details>
    </div>

    <div class="grid grid-cols-1 gap-6 md:grid-cols-2 xl:grid-cols-3">
      <article v-for="item in (state.datasources as any[])" :key="item.id" class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm transition hover:shadow-md">
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
            <select
              v-model="(editDraft as any).environment"
              class="col-span-2 border-none bg-slate-50 px-3 py-2"
              title="环境标签:沙盒可造数据;预发 / 生产严禁 materialize / record"
              @change="onEnvChange(editDraft as any)"
            >
              <option value="sandbox">🟢 sandbox 沙盒(可造数据)</option>
              <option value="staging">🟡 staging 预发(只读)</option>
              <option value="prod">🔴 prod 生产(只读 / 严禁造数据)</option>
            </select>
          </div>
          <!-- Phase 14 #3 Round 3 — 编辑表单也带高级配置折叠 -->
          <details class="mt-3 text-xs">
            <summary class="cursor-pointer text-slate-500 hover:text-slate-700 select-none">
              ⚙ 高级配置:操作权限 allow_*
            </summary>
            <div class="mt-3 grid grid-cols-2 gap-2 text-slate-700">
              <label class="flex items-center gap-1.5 text-[11px]"><input type="checkbox" v-model="(editDraft as any).allow_select" /><span>allow_select</span></label>
              <label class="flex items-center gap-1.5 text-[11px]"><input type="checkbox" v-model="(editDraft as any).allow_explain" /><span>allow_explain<br/><span class="text-[9px] text-slate-400">MySQL EXPLAIN</span></span></label>
              <label class="flex items-center gap-1.5 text-[11px]"><input type="checkbox" v-model="(editDraft as any).allow_dm_explain" /><span>allow_dm_explain<br/><span class="text-[9px] text-slate-400">DM EXPLAIN</span></span></label>
              <label class="flex items-center gap-1.5 text-[11px]"><input type="checkbox" v-model="(editDraft as any).allow_oracle_plan_table" /><span>allow_oracle_plan_table<br/><span class="text-[9px] text-slate-400">Oracle PLAN_TABLE</span></span></label>
              <label class="flex items-center gap-1.5 text-[11px]"><input type="checkbox" v-model="(editDraft as any).allow_schema_import" /><span>allow_schema_import<br/><span class="text-[9px] text-slate-400">读 information_schema</span></span></label>
              <label class="flex items-center gap-1.5 text-[11px]"><input type="checkbox" v-model="(editDraft as any).allow_schema_save" /><span>allow_schema_save<br/><span class="text-[9px] text-slate-400">保存 yml</span></span></label>
              <label class="flex items-center gap-1.5 text-[11px]"><input type="checkbox" v-model="(editDraft as any).allow_scenario_write" /><span>allow_scenario_write<br/><span class="text-[9px] text-slate-400">造数据</span></span></label>
              <label class="flex items-center gap-1.5 text-[11px]"><input type="checkbox" v-model="(editDraft as any).allow_record_task" /><span>allow_record_task<br/><span class="text-[9px] text-slate-400">record task</span></span></label>
            </div>
          </details>
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
          <h3 class="mb-1 font-bold text-slate-800 flex items-center gap-2">
            {{ item.name }}
            <span
              :class="envBadgeClass(item.environment)"
              class="text-[10px] px-1.5 py-0.5 rounded font-bold uppercase tracking-wider"
              :title="envBadgeTitle(item.environment)"
            >{{ item.environment || 'sandbox' }}</span>
          </h3>
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
