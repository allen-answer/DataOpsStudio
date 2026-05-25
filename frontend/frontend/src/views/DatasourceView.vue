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

// Phase 14 #1 合规防御 — 环境标签四态徽章(unknown / sandbox / staging / prod)
// 后端默认 environment="unknown"(fail-safe),admin 必须显式选过才解锁写入
function envBadgeClass(env?: string): string {
  if (env === 'prod') return 'bg-status-error-bg text-status-error'
  if (env === 'staging') return 'bg-status-warning-bg text-status-warning'
  if (env === 'sandbox') return 'bg-status-success-bg text-status-success'
  return 'bg-status-pending-bg text-status-pending'  // unknown / undefined — 灰
}

function envBadgeTitle(env?: string): string {
  if (env === 'prod') return '生产环境 — 写入端点(materialize / run-all / record)已锁定'
  if (env === 'staging') return '预发环境 — 写入端点已锁定'
  if (env === 'sandbox') return '沙盒环境 — 可造数据 / 跑模拟流程'
  return '未确认环境 — admin 选择具体环境后才能解锁操作(fail-safe 默认)'
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

// Phase 14 #3 Round 6 N — 8 个 allow_* flag 按语义分 3 组,UI 不再挤在一行
// 让用户一眼看清"这个数据源开了哪类操作"
type AllowKey =
  | 'allow_select' | 'allow_explain' | 'allow_dm_explain'
  | 'allow_oracle_plan_table' | 'allow_schema_import'
  | 'allow_schema_save' | 'allow_scenario_write' | 'allow_record_task'

interface AllowFlag { key: AllowKey; label: string; hint: string }
interface AllowGroup { id: string; title: string; icon: string; tone: string; flags: AllowFlag[] }

const ALLOW_GROUPS: AllowGroup[] = [
  {
    id: 'query', title: '查询权限', icon: '🔍', tone: 'border-status-info-bg bg-status-info-bg/30',
    flags: [
      { key: 'allow_select',     label: 'allow_select',     hint: 'SELECT 查询(任何环境基本能力)' },
      { key: 'allow_explain',    label: 'allow_explain',    hint: 'MySQL EXPLAIN 执行计划' },
      { key: 'allow_dm_explain', label: 'allow_dm_explain', hint: 'DM EXPLAIN(纯只读)' },
    ],
  },
  {
    id: 'diag', title: '诊断权限', icon: '🩺', tone: 'border-status-warning-bg bg-status-warning-bg/30',
    flags: [
      { key: 'allow_oracle_plan_table', label: 'allow_oracle_plan_table', hint: 'Oracle PLAN_TABLE 写入(诊断用)' },
      { key: 'allow_schema_import',     label: 'allow_schema_import',     hint: '读 information_schema 元数据' },
    ],
  },
  {
    id: 'write', title: '写入权限', icon: '✏️', tone: 'border-status-error-bg bg-status-error-bg/30',
    flags: [
      { key: 'allow_schema_save',    label: 'allow_schema_save',    hint: '保存 yml schema 到本地' },
      { key: 'allow_scenario_write', label: 'allow_scenario_write', hint: '造数据 materialize(写真表)' },
      { key: 'allow_record_task',    label: 'allow_record_task',    hint: 'record 落 CompareTask' },
    ],
  },
]
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

    <!-- 新增数据源 — 基本信息 + 高级配置(3 组分组) -->
    <div class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
      <div class="mb-4 flex items-center justify-between">
        <h3 class="font-bold text-slate-800">新增数据源</h3>
        <span class="text-[11px] text-slate-400">基本信息 → 选环境 → 视需求调高级配置</span>
      </div>
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
          <option value="unknown">⚪ unknown 未确认(默认,写入端点全锁)</option>
          <option value="sandbox">🟢 sandbox 沙盒(可造数据)</option>
          <option value="staging">🟡 staging 预发(只读)</option>
          <option value="prod">🔴 prod 生产(只读 / 严禁造数据)</option>
        </select>
      </div>

      <!-- 高级配置:3 组分组(查询 / 诊断 / 写入)+ 折叠 -->
      <details class="mt-4 text-xs">
        <summary class="cursor-pointer rounded-lg bg-slate-50 px-3 py-2 text-sm font-semibold text-slate-700 transition hover:bg-slate-100 select-none">
          ⚙ 高级配置:操作权限 allow_* (按环境预填,可手动微调)
        </summary>
        <div class="mt-3 grid grid-cols-1 gap-3 md:grid-cols-3">
          <div
            v-for="group in ALLOW_GROUPS"
            :key="group.id"
            class="rounded-xl border p-3"
            :class="group.tone"
          >
            <div class="mb-2 flex items-center gap-1.5 text-sm font-semibold text-slate-700">
              <span>{{ group.icon }}</span>
              <span>{{ group.title }}</span>
            </div>
            <div class="space-y-1.5">
              <label v-for="f in group.flags" :key="f.key" class="flex items-start gap-2 cursor-pointer rounded px-1 py-0.5 hover:bg-white/50">
                <input
                  type="checkbox"
                  class="mt-0.5"
                  :checked="!!(datasourceDraft as any)[f.key]"
                  @change="(datasourceDraft as any)[f.key] = ($event.target as HTMLInputElement).checked"
                />
                <span class="min-w-0 flex-1">
                  <span class="sql-font text-[11px] font-semibold text-slate-800">{{ f.label }}</span>
                  <span class="block text-[10px] text-slate-500 leading-tight">{{ f.hint }}</span>
                </span>
              </label>
            </div>
          </div>
        </div>
        <p class="mt-3 text-[11px] text-slate-400 leading-relaxed">
          💡 选环境时会自动预填:sandbox = 全开,staging / prod = 仅 allow_select 开。
          单独勾某项可定向放权(如 prod 数据源仅开 allow_dm_explain 让 DBA 查 plan)。
        </p>
      </details>

      <div class="mt-4 flex justify-end">
        <button class="rounded-lg bg-blue-600 px-5 py-2 text-sm font-bold text-white transition hover:bg-blue-700" @click="createDatasource">
          添加数据源
        </button>
      </div>
    </div>

    <!-- 已配置数据源 — 列表(table) -->
    <div class="rounded-2xl border border-slate-200 bg-white shadow-sm overflow-hidden">
      <div class="flex items-center justify-between border-b border-slate-100 px-5 py-3">
        <h3 class="font-bold text-slate-800">已配置数据源 <span class="ml-2 text-xs font-normal text-slate-400">{{ (state.datasources as any[]).length }} 个</span></h3>
      </div>

      <!-- 空状态 -->
      <div v-if="!(state.datasources as any[]).length" class="px-5 py-12 text-center text-sm text-slate-400">
        还没有数据源 — 在上面表单创建第一个
      </div>

      <!-- table -->
      <div v-else class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead class="bg-slate-50/70 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
            <tr>
              <th class="px-5 py-2.5 text-left">名称</th>
              <th class="px-3 py-2.5 text-left">类型</th>
              <th class="px-3 py-2.5 text-left">地址</th>
              <th class="px-3 py-2.5 text-left">数据库</th>
              <th class="px-3 py-2.5 text-left">项目</th>
              <th class="px-3 py-2.5 text-left">环境</th>
              <th class="px-5 py-2.5 text-right">操作</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-slate-100">
            <template v-for="item in (state.datasources as any[])" :key="item.id">
              <tr class="hover:bg-slate-50/50 transition">
                <td class="px-5 py-3 font-semibold text-slate-800">{{ item.name }}</td>
                <td class="px-3 py-3 sql-font text-xs text-slate-600">{{ item.db_type }}</td>
                <td class="px-3 py-3 sql-font text-xs text-slate-500">{{ item.host }}:{{ item.port }}</td>
                <td class="px-3 py-3 sql-font text-xs text-slate-500">{{ item.database }}</td>
                <td class="px-3 py-3 text-xs text-slate-500">{{ projectName(item.project_id) }}</td>
                <td class="px-3 py-3">
                  <span
                    :class="envBadgeClass(item.environment)"
                    class="text-[10px] px-1.5 py-0.5 rounded font-bold uppercase tracking-wider"
                    :title="envBadgeTitle(item.environment)"
                  >{{ item.environment || 'unknown' }}</span>
                  <span
                    v-if="!item.environment_verified"
                    class="ml-1 text-[10px] px-1.5 py-0.5 rounded font-bold bg-status-warning-bg text-status-warning"
                    title="admin 尚未确认环境标签 — 写入端点全部 403"
                  >⚠ 未验证</span>
                </td>
                <td class="px-5 py-3 text-right">
                  <div class="inline-flex gap-1">
                    <button class="rounded px-2 py-1 text-xs font-semibold text-slate-600 transition hover:bg-slate-100 hover:text-slate-900" @click="testDatasource(item.id)">测试</button>
                    <button class="rounded px-2 py-1 text-xs font-semibold text-primary transition hover:bg-primary-light" @click="startEditDatasource(item)">
                      {{ editingDatasourceId === item.id ? '收起' : '编辑' }}
                    </button>
                    <button class="rounded px-2 py-1 text-xs font-semibold text-status-error transition hover:bg-status-error-bg" @click="deleteDatasource(item.id)">删除</button>
                  </div>
                </td>
              </tr>
              <!-- 编辑行(展开) -->
              <tr v-if="editingDatasourceId === item.id" class="bg-slate-50/50">
                <td colspan="7" class="px-5 py-4">
                  <div class="rounded-xl border border-primary/20 bg-white p-4 shadow-soft">
                    <div class="mb-3 flex items-center justify-between">
                      <span class="text-sm font-bold text-slate-700">编辑 · {{ item.name }}</span>
                      <button class="text-xs text-slate-400 hover:text-slate-700" @click="cancelEditDatasource">取消</button>
                    </div>
                    <div class="grid grid-cols-4 gap-2 text-sm">
                      <input v-model="editDraft.name" class="border-none bg-slate-50 px-3 py-2" placeholder="名称">
                      <select v-model="editDraft.db_type" class="border-none bg-slate-50 px-3 py-2"><option v-for="type in state.dbTypes" :key="type">{{ type }}</option></select>
                      <input v-model="editDraft.host" class="border-none bg-slate-50 px-3 py-2" placeholder="Host">
                      <input v-model="editDraft.port" class="border-none bg-slate-50 px-3 py-2" type="number" placeholder="Port">
                      <input v-model="editDraft.database" class="border-none bg-slate-50 px-3 py-2 col-span-2" placeholder="数据库 / 服务名">
                      <input v-model="editDraft.username" class="border-none bg-slate-50 px-3 py-2" placeholder="用户名">
                      <input v-model="editDraft.password" class="border-none bg-slate-50 px-3 py-2" type="password" placeholder="密码(留空不修改)">
                      <select v-model="editDraft.project_id" class="col-span-2 border-none bg-slate-50 px-3 py-2" title="所属项目">
                        <option value="">全局(无项目)</option>
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

                    <details class="mt-4 text-xs" open>
                      <summary class="cursor-pointer rounded-lg bg-slate-50 px-3 py-2 text-sm font-semibold text-slate-700 transition hover:bg-slate-100 select-none">
                        ⚙ 高级配置:操作权限 allow_*
                      </summary>
                      <div class="mt-3 grid grid-cols-1 gap-3 md:grid-cols-3">
                        <div
                          v-for="group in ALLOW_GROUPS"
                          :key="group.id"
                          class="rounded-xl border p-3"
                          :class="group.tone"
                        >
                          <div class="mb-2 flex items-center gap-1.5 text-sm font-semibold text-slate-700">
                            <span>{{ group.icon }}</span>
                            <span>{{ group.title }}</span>
                          </div>
                          <div class="space-y-1.5">
                            <label v-for="f in group.flags" :key="f.key" class="flex items-start gap-2 cursor-pointer rounded px-1 py-0.5 hover:bg-white/50">
                              <input
                                type="checkbox"
                                class="mt-0.5"
                                :checked="!!(editDraft as any)[f.key]"
                                @change="(editDraft as any)[f.key] = ($event.target as HTMLInputElement).checked"
                              />
                              <span class="min-w-0 flex-1">
                                <span class="sql-font text-[11px] font-semibold text-slate-800">{{ f.label }}</span>
                                <span class="block text-[10px] text-slate-500 leading-tight">{{ f.hint }}</span>
                              </span>
                            </label>
                          </div>
                        </div>
                      </div>
                    </details>

                    <div class="mt-4 flex justify-end gap-2 border-t border-slate-100 pt-4">
                      <button class="rounded-lg border border-slate-200 px-4 py-2 text-xs font-bold text-slate-600 transition hover:bg-slate-50" @click="cancelEditDatasource">取消</button>
                      <button class="rounded-lg bg-blue-600 px-4 py-2 text-xs font-bold text-white transition hover:bg-blue-700" @click="updateDatasource(item.id)">保存</button>
                    </div>
                  </div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </div>
  </section>
</template>
