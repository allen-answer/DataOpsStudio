<script setup>
import { computed, inject } from 'vue'
import { parameterTypeMeta } from '../../mock/workflow_meta'

// 详情页右侧元数据 sidebar：运行参数预览 + 描述/项目/状态/owner/cron/tags +
// 输入/输出资产编辑器 + 资产只读卡。workflowDraft 由父传入，子直接 mutate
// （保留旧行为：reactive proxy 经 prop 仍能被子组件改）。
const props = defineProps({
  workflowDraft: { type: Object, required: true },
  parameters:    { type: Array,  default: () => [] },   // 解析自 params 节点
  resolvedParams: { type: Object, default: () => ({}) },
})

const { state } = inject('app', { state: { datasources: [] } })

const openLineageTargets = computed(() => (props.workflowDraft.notifications || [])
  .filter((item) => ['openlineage', 'openlineage_webhook'].includes(String(item.type || '').toLowerCase())))

function addOpenLineageTarget() {
  ;(props.workflowDraft.notifications || (props.workflowDraft.notifications = [])).push({
    type: 'openlineage',
    enabled: true,
    url: '',
    events: ['all'],
    namespace: 'dataops-studio',
    timeout_seconds: 5,
  })
}

function removeNotification(target) {
  const index = (props.workflowDraft.notifications || []).indexOf(target)
  if (index >= 0) props.workflowDraft.notifications.splice(index, 1)
}

function addSensor(type = 'sql') {
  const next = (props.workflowDraft.sensors || []).length + 1
  ;(props.workflowDraft.sensors || (props.workflowDraft.sensors = [])).push({
    id: `${type}_${next}`,
    type,
    enabled: true,
    interval_seconds: 60,
    cooldown_seconds: 300,
    ...(type === 'sql' ? { datasource_id: '', sql: 'select 1 as ready' } : { url: '', expect_status: 200, json_path: '', equals: '' }),
  })
}
</script>

<template>
  <aside class="flex flex-col gap-3">
    <!-- 运行参数：参数驱动作业流的核心信息 -->
    <div class="rounded-xl border border-slate-200 bg-white shadow-sm">
      <div class="flex items-center justify-between border-b border-slate-200 px-3 py-2">
        <p class="text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">运行参数</p>
        <span class="text-[10.5px] text-slate-500">{{ parameters.length }} 个</span>
      </div>
      <ul class="divide-y divide-slate-100">
        <li v-for="param in parameters" :key="param.name" class="px-3 py-2.5">
          <div class="flex items-center gap-1.5">
            <span class="rounded px-1 py-0.5 text-[9.5px] font-bold uppercase ring-1 ring-inset" :class="parameterTypeMeta[param.type].accent">{{ parameterTypeMeta[param.type].glyph }} {{ parameterTypeMeta[param.type].label }}</span>
            <span class="font-mono text-[12px] font-semibold text-slate-800">{{ param.name }}</span>
            <span v-if="param.required" class="ml-auto text-[10px] font-semibold text-rose-600">必填</span>
            <span v-else class="ml-auto text-[10px] text-slate-400">可选</span>
          </div>
          <p class="mt-0.5 text-[11px] text-slate-500">{{ param.description }}</p>
          <div class="mt-1 flex items-baseline gap-1.5">
            <span class="text-[10px] uppercase tracking-wider text-slate-400">解析后</span>
            <span v-if="resolvedParams[param.name].kind === 'list'" class="font-mono text-[11px]">
              <span v-for="(v, i) in resolvedParams[param.name].value" :key="i" class="mr-1 rounded bg-slate-100 px-1.5 py-0.5 text-slate-700">{{ v }}</span>
            </span>
            <span v-else-if="resolvedParams[param.name].kind === 'pending'" class="rounded bg-emerald-50 px-1.5 py-0.5 font-mono text-[11px] text-emerald-700 ring-1 ring-emerald-200">{{ resolvedParams[param.name].value }}</span>
            <span v-else-if="resolvedParams[param.name].kind === 'derived'" class="rounded bg-blue-50 px-1.5 py-0.5 font-mono text-[11px] text-blue-700 ring-1 ring-blue-200">{{ resolvedParams[param.name].value }}</span>
            <span v-else class="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[11px] text-slate-700">{{ resolvedParams[param.name].value }}</span>
          </div>
        </li>
        <li v-if="!parameters.length" class="px-3 py-3 text-center text-[11px] text-slate-400">还没有参数定义 — 在画布添加 <code class="rounded bg-slate-100 px-1 font-mono">params</code> 节点，或保存后右上角直接添加</li>
      </ul>
      <div class="border-t border-slate-100 px-3 py-2 text-[10.5px] text-slate-500">
        可在 SQL / 文件名 / Sheet 名等位置用 <code class="rounded bg-slate-100 px-1 font-mono">${name}</code> 引用
      </div>
    </div>

    <!-- 元数据：可编辑，落到 Workflow 模型；保存后即生效 -->
    <div class="rounded-xl border border-slate-200 bg-white p-3 shadow-sm">
      <p class="mb-2 text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">元数据</p>
      <div class="space-y-2">
        <label class="block">
          <span class="mb-0.5 block text-[10px] font-semibold text-slate-500">描述</span>
          <textarea v-model="workflowDraft.description" rows="3" placeholder="一句话说清这个作业流的目的"
                    class="block w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 text-[12px] text-slate-700"></textarea>
        </label>
        <div class="grid grid-cols-2 gap-2">
          <label class="block">
            <span class="mb-0.5 block text-[10px] font-semibold text-slate-500">项目</span>
            <input v-model="workflowDraft.project" placeholder="如 dw / risk / growth"
                   class="block w-full rounded-md border border-slate-200 bg-white px-2 py-1 text-[12px] text-slate-700">
          </label>
          <label class="block">
            <span class="mb-0.5 block text-[10px] font-semibold text-slate-500">状态</span>
            <select v-model="workflowDraft.status" class="block w-full rounded-md border border-slate-200 bg-white px-2 py-1 text-[12px] text-slate-700">
              <option value="draft">草稿</option>
              <option value="active">已上线</option>
              <option value="paused">暂停</option>
              <option value="archived">归档</option>
            </select>
          </label>
        </div>
        <div class="grid grid-cols-2 gap-2">
          <label class="block">
            <span class="mb-0.5 block text-[10px] font-semibold text-slate-500">负责人</span>
            <input v-model="workflowDraft.owner" placeholder="如 alice@team"
                   class="block w-full rounded-md border border-slate-200 bg-white px-2 py-1 text-[12px] text-slate-700">
          </label>
          <label class="block">
            <span class="mb-0.5 block text-[10px] font-semibold text-slate-500">cron（可选）</span>
            <input v-model="workflowDraft.schedule_cron" placeholder="0 2 * * * 或留空"
                   class="block w-full rounded-md border border-slate-200 bg-white px-2 py-1 font-mono text-[11px] text-slate-700">
          </label>
        </div>
        <label class="block">
          <span class="mb-0.5 block text-[10px] font-semibold text-slate-500">标签（逗号分隔）</span>
          <input :value="(workflowDraft.tags || []).join(', ')"
                 @input="workflowDraft.tags = $event.target.value.split(',').map(s => s.trim()).filter(Boolean)"
                 placeholder="orders, daily, prod"
                 class="block w-full rounded-md border border-slate-200 bg-white px-2 py-1 font-mono text-[11px] text-slate-700">
        </label>

        <!-- Sensor 配置 -->
        <div class="rounded-lg border border-slate-200 bg-slate-50/60 p-2">
          <div class="mb-2 flex items-center justify-between">
            <span class="text-[10px] font-semibold text-slate-500">Sensor 触发器</span>
            <div class="flex gap-1">
              <button class="text-[10.5px] font-semibold text-blue-600 hover:underline" @click="addSensor('sql')">+ SQL</button>
              <button class="text-[10.5px] font-semibold text-blue-600 hover:underline" @click="addSensor('http')">+ HTTP</button>
            </div>
          </div>
          <ul class="space-y-2">
            <li v-for="(sensor, i) in workflowDraft.sensors" :key="sensor.id || i" class="rounded-lg border border-slate-200 bg-white p-2">
              <div class="grid grid-cols-[56px_minmax(0,1fr)_70px_24px] gap-1">
                <select v-model="sensor.type" class="rounded-md border border-slate-200 px-1 py-1 text-[11px]">
                  <option value="sql">SQL</option>
                  <option value="http">HTTP</option>
                </select>
                <input v-model="sensor.id" placeholder="sensor_id" class="rounded-md border border-slate-200 px-2 py-1 font-mono text-[11px]">
                <label class="flex items-center justify-center gap-1 text-[10.5px] text-slate-600">
                  <input v-model="sensor.enabled" type="checkbox"> 启用
                </label>
                <button class="rounded text-rose-600 hover:bg-rose-50" @click="workflowDraft.sensors.splice(i, 1)">×</button>
              </div>
              <div class="mt-1 grid grid-cols-2 gap-1">
                <input v-model.number="sensor.interval_seconds" type="number" min="5" placeholder="interval_seconds"
                       class="rounded-md border border-slate-200 px-2 py-1 font-mono text-[11px]">
                <input v-model.number="sensor.cooldown_seconds" type="number" min="0" placeholder="cooldown_seconds"
                       class="rounded-md border border-slate-200 px-2 py-1 font-mono text-[11px]">
              </div>
              <template v-if="sensor.type === 'sql'">
                <select v-model="sensor.datasource_id" class="mt-1 block w-full rounded-md border border-slate-200 px-2 py-1 text-[11px]">
                  <option value="">选择数据源</option>
                  <option v-for="ds in state.datasources" :key="ds.id" :value="ds.id">{{ ds.name }} · {{ ds.db_type }}</option>
                </select>
                <textarea v-model="sensor.sql" rows="2" placeholder="select count(*) > 0 as ready from ..."
                          class="mt-1 block w-full rounded-md border border-slate-200 px-2 py-1 font-mono text-[11px]"></textarea>
              </template>
              <template v-else>
                <input v-model="sensor.url" placeholder="https://example.com/ready"
                       class="mt-1 block w-full rounded-md border border-slate-200 px-2 py-1 font-mono text-[11px]">
                <div class="mt-1 grid grid-cols-3 gap-1">
                  <input v-model.number="sensor.expect_status" type="number" placeholder="200" class="rounded-md border border-slate-200 px-2 py-1 font-mono text-[11px]">
                  <input v-model="sensor.json_path" placeholder="$.ready" class="rounded-md border border-slate-200 px-2 py-1 font-mono text-[11px]">
                  <input v-model="sensor.equals" placeholder="equals 可选" class="rounded-md border border-slate-200 px-2 py-1 font-mono text-[11px]">
                </div>
              </template>
            </li>
          </ul>
          <p v-if="!workflowDraft.sensors?.length" class="text-[11px] text-slate-400">未配置 sensor；active 状态下可由 cron 或 sensor 自动触发。</p>
        </div>

        <!-- OpenLineage webhook 配置 -->
        <div class="rounded-lg border border-violet-100 bg-violet-50/50 p-2">
          <div class="mb-2 flex items-center justify-between">
            <span class="text-[10px] font-semibold text-violet-700">OpenLineage Webhook</span>
            <button class="text-[10.5px] font-semibold text-violet-700 hover:underline" @click="addOpenLineageTarget">+ 添加</button>
          </div>
          <ul class="space-y-2">
            <li v-for="target in openLineageTargets" :key="target.url || target.namespace" class="rounded-lg border border-violet-100 bg-white p-2">
              <div class="grid grid-cols-[minmax(0,1fr)_90px_24px] gap-1">
                <input v-model="target.url" placeholder="http://marquez:5000/api/v1/lineage"
                       class="rounded-md border border-slate-200 px-2 py-1 font-mono text-[11px]">
                <label class="flex items-center justify-center gap-1 text-[10.5px] text-slate-600">
                  <input v-model="target.enabled" type="checkbox"> 启用
                </label>
                <button class="rounded text-rose-600 hover:bg-rose-50" @click="removeNotification(target)">×</button>
              </div>
              <div class="mt-1 grid grid-cols-3 gap-1">
                <select :value="(target.events || ['all']).join(',')" class="rounded-md border border-slate-200 px-1 py-1 text-[11px]"
                        @change="target.events = $event.target.value === 'all' ? ['all'] : [$event.target.value]">
                  <option value="all">all</option>
                  <option value="START">START</option>
                  <option value="COMPLETE">COMPLETE</option>
                  <option value="FAIL">FAIL</option>
                </select>
                <input v-model="target.namespace" placeholder="dataops-studio" class="rounded-md border border-slate-200 px-2 py-1 font-mono text-[11px]">
                <input v-model.number="target.timeout_seconds" type="number" min="1" placeholder="5" class="rounded-md border border-slate-200 px-2 py-1 font-mono text-[11px]">
              </div>
            </li>
          </ul>
          <p v-if="!openLineageTargets.length" class="text-[11px] text-slate-400">未配置时可用 DATAOPS_OPENLINEAGE_WEBHOOK_URL 兜底。</p>
        </div>

        <!-- 输入资产编辑器 -->
        <div>
          <div class="mb-1 flex items-center justify-between">
            <span class="text-[10px] font-semibold text-slate-500">输入资产</span>
            <button class="text-[10.5px] font-semibold text-blue-600 hover:underline"
                    @click="workflowDraft.input_assets.push({ key: '', kind: 'table', description: '' })">+ 添加</button>
          </div>
          <ul class="space-y-1">
            <li v-for="(asset, i) in workflowDraft.input_assets" :key="i" class="grid grid-cols-[minmax(0,1fr)_80px_24px] gap-1">
              <input v-model="asset.key" placeholder="schema.table 或 路径"
                     class="rounded-md border border-slate-200 bg-white px-2 py-1 font-mono text-[11px] text-slate-700">
              <select v-model="asset.kind" class="rounded-md border border-slate-200 bg-white px-1 py-1 text-[11px] text-slate-700">
                <option value="table">表</option>
                <option value="file">文件</option>
                <option value="stream">流</option>
              </select>
              <button class="rounded text-rose-600 hover:bg-rose-50" title="删除"
                      @click="workflowDraft.input_assets.splice(i, 1)">×</button>
            </li>
          </ul>
        </div>

        <!-- 输出资产编辑器 -->
        <div>
          <div class="mb-1 flex items-center justify-between">
            <span class="text-[10px] font-semibold text-slate-500">输出资产</span>
            <button class="text-[10.5px] font-semibold text-blue-600 hover:underline"
                    @click="workflowDraft.output_assets.push({ key: '', kind: 'table', description: '' })">+ 添加</button>
          </div>
          <ul class="space-y-1">
            <li v-for="(asset, i) in workflowDraft.output_assets" :key="i" class="grid grid-cols-[minmax(0,1fr)_80px_24px] gap-1">
              <input v-model="asset.key" placeholder="schema.table 或 路径"
                     class="rounded-md border border-slate-200 bg-white px-2 py-1 font-mono text-[11px] text-slate-700">
              <select v-model="asset.kind" class="rounded-md border border-slate-200 bg-white px-1 py-1 text-[11px] text-slate-700">
                <option value="table">表</option>
                <option value="file">文件</option>
                <option value="stream">流</option>
              </select>
              <button class="rounded text-rose-600 hover:bg-rose-50" title="删除"
                      @click="workflowDraft.output_assets.splice(i, 1)">×</button>
            </li>
          </ul>
        </div>

        <p class="text-[10px] text-slate-400">这些字段保存后落 config/workflows.json，列表页和详情页都会读到。</p>
      </div>
    </div>

    <!-- 输入资产只读 chip 卡（编辑器在上面） -->
    <div class="rounded-xl border border-slate-200 bg-white p-3 shadow-sm">
      <p class="mb-2 text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">输入资产 ({{ workflowDraft.input_assets.length }})</p>
      <ul v-if="workflowDraft.input_assets.length" class="space-y-1.5">
        <li v-for="(asset, i) in workflowDraft.input_assets" :key="i" class="flex items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-[11.5px]"
            :title="asset.description">
          <span class="h-1.5 w-1.5 shrink-0 rounded-full bg-slate-400"></span>
          <span class="truncate font-mono text-slate-700">{{ asset.key }}</span>
          <span class="ml-auto rounded bg-white px-1.5 py-0.5 font-mono text-[9.5px] text-slate-500">{{ asset.kind }}</span>
        </li>
      </ul>
      <p v-else class="text-[11px] text-slate-400">还没有声明输入资产 — 在「基础设置」面板下方添加</p>
    </div>

    <div class="rounded-xl border border-slate-200 bg-white p-3 shadow-sm">
      <p class="mb-2 text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">输出资产 ({{ workflowDraft.output_assets.length }})</p>
      <ul v-if="workflowDraft.output_assets.length" class="space-y-1.5">
        <li v-for="(asset, i) in workflowDraft.output_assets" :key="i" class="flex items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-[11.5px]"
            :title="asset.description">
          <span class="h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-500"></span>
          <span class="truncate font-mono text-slate-700">{{ asset.key }}</span>
          <span class="ml-auto rounded bg-white px-1.5 py-0.5 font-mono text-[9.5px] text-slate-500">{{ asset.kind }}</span>
        </li>
      </ul>
      <p v-else class="text-[11px] text-slate-400">还没有声明输出资产 — 在「基础设置」面板下方添加</p>
    </div>
  </aside>
</template>
