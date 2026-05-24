<script setup lang="ts">
/**
 * Phase 14 #3 Round 4 — 可视化 Scenario Builder
 *
 * 替代手写 yml,完全表单驱动。本轮 MVP:
 * - 元数据(id/name/dialect/seed/description/variables)
 * - 表 + 列编辑(7 种 generator + 各自 knob 完整)
 * - index 子列表
 * - 实时 yml 预览面板
 * - 保存 → POST /api/scenarios/save-yml 落盘到 config/scenarios
 *
 * 下一轮(H.4):anomaly + workload 可视化编辑器。本轮这两块留空,
 * advanced 用户可在生成 yml 后手动编辑 .yml 文件加 anomalies / workloads
 * (后续 yml 文本编辑器也可考虑提供)。
 */
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import {
  FlaskConical, FilePlus2, Save, ChevronLeft, Plus, Trash2, Copy,
  AlertCircle, CheckCircle2, FileText,
} from 'lucide-vue-next'
import { useScenarioBuilderStore } from '../stores/scenarioBuilder'
import { useNoticeStore } from '../stores/notice'
import ColumnEditor from '../components/scenario-builder/ColumnEditor.vue'

const store = useScenarioBuilderStore()
const noticeStore = useNoticeStore()
const router = useRouter()

const form = store.form

async function onSave(overwrite = false) {
  const ok = await store.save(overwrite)
  if (ok) {
    // 保存成功 → 跳回 scenario-lab 让用户选这条新 scenario
    setTimeout(() => router.push('/scenario-lab'), 800)
  }
}

async function copyYml() {
  try {
    await navigator.clipboard.writeText(store.ymlPreview)
    noticeStore.setNotice('yml 已复制到剪贴板')
  } catch {
    noticeStore.setNotice('复制失败,请手动选中复制')
  }
}

const tableCount = computed(() => form.tables.length)
const totalColumns = computed(
  () => form.tables.reduce((sum, t) => sum + t.columns.length, 0),
)
const totalRows = computed(
  () => form.tables.reduce((sum, t) => sum + (Number(t.rows) || 0), 0),
)
</script>

<template>
  <section class="space-y-4">
    <!-- 面包屑 + 头部 -->
    <nav class="text-xs text-slate-500 flex items-center gap-1">
      <a href="#/scenario-lab" class="hover:text-primary inline-flex items-center gap-1">
        <FlaskConical class="h-3 w-3" /> 场景测试沙盒
      </a>
      <span class="text-slate-400">/</span>
      <span class="text-slate-700 font-medium">新建场景(可视化)</span>
    </nav>

    <div class="flex items-start justify-between gap-4">
      <div>
        <h2 class="text-2xl font-bold text-slate-800 flex items-center gap-2">
          <FilePlus2 class="h-7 w-7 text-primary" />
          可视化新建场景
        </h2>
        <p class="mt-1 text-sm text-slate-500 leading-relaxed">
          不用手写 yaml — 填表创建 scenario。完成后可在
          <a href="#/scenario-lab" class="text-primary hover:underline">场景测试沙盒</a>
          看到并运行一键全套。<br/>
          <span class="text-[11px] text-slate-400">
            🚧 MVP 版本支持:元数据 + 表 + 列(7 种 generator + dist_params)。
            anomalies / workloads 下一轮做可视化,本轮如需添加请保存后手动编辑 .yml。
          </span>
        </p>
      </div>
      <a href="#/scenario-lab" class="btn btn-outline">
        <ChevronLeft class="h-4 w-4" /> 取消返回
      </a>
    </div>

    <!-- 两列:左表单 + 右实时 yml 预览 -->
    <div class="grid grid-cols-1 lg:grid-cols-[1.5fr_1fr] gap-6">
      <!-- ─── 左:表单 ───────────────────────────────────────────── -->
      <div class="space-y-5">

        <!-- 元数据 -->
        <div class="card p-5 space-y-3">
          <h3 class="text-sm font-bold text-slate-800 mb-2">① 元数据</h3>
          <div class="grid grid-cols-2 gap-3">
            <label class="block text-xs">
              <span class="text-slate-500 font-semibold">Scenario ID (必填)</span>
              <input
                v-model="form.id"
                class="w-full sql-font text-sm mt-0.5"
                placeholder="如 my-fixture-v1"
              />
              <p class="text-[10px] text-slate-400 mt-0.5">只允许字母 / 数字 / _ / -,会用作文件名</p>
            </label>
            <label class="block text-xs">
              <span class="text-slate-500 font-semibold">名称</span>
              <input
                v-model="form.name"
                class="w-full text-sm mt-0.5"
                placeholder="留空 = 用 ID"
              />
            </label>
            <label class="block text-xs col-span-2">
              <span class="text-slate-500 font-semibold">描述</span>
              <input v-model="form.description" class="w-full text-sm mt-0.5" placeholder="可选" />
            </label>
            <label class="block text-xs">
              <span class="text-slate-500 font-semibold">方言</span>
              <select v-model="form.dialect" class="w-full mt-0.5">
                <option value="mysql">MySQL</option>
                <option value="dm">DM 达梦</option>
                <option value="oracle">Oracle</option>
                <option value="db2">DB2</option>
              </select>
            </label>
            <label class="block text-xs">
              <span class="text-slate-500 font-semibold">seed (随机种子)</span>
              <input v-model.number="form.seed" type="number" class="w-full text-sm mt-0.5" />
            </label>
          </div>

          <!-- 模板变量 -->
          <div class="pt-2 border-t border-slate-100">
            <div class="flex items-center justify-between mb-1">
              <span class="text-xs font-semibold text-slate-500">模板变量(可选)</span>
              <button class="text-xs text-primary hover:underline" @click="store.addVariable">
                <Plus class="h-3 w-3 inline" /> 添加
              </button>
            </div>
            <p class="text-[10px] text-slate-400 mb-1">workload.sql 里 <code class="sql-font">&#123;&#123;name&#125;&#125;</code> 占位符会渲染成此处值</p>
            <div v-for="(v, i) in form.variables" :key="i" class="flex items-center gap-1.5 text-xs mb-1">
              <input v-model="v.name" class="w-32 sql-font text-xs" placeholder="变量名" />
              <span class="text-slate-400">=</span>
              <input v-model="v.value" class="flex-1 sql-font text-xs" placeholder="变量值" />
              <button class="text-status-error" @click="store.removeVariable(i)">
                <Trash2 class="h-3 w-3" />
              </button>
            </div>
            <p v-if="!form.variables.length" class="text-[11px] text-slate-400 italic">暂无变量</p>
          </div>
        </div>

        <!-- 表 -->
        <div class="card p-5 space-y-3">
          <div class="flex items-center justify-between">
            <h3 class="text-sm font-bold text-slate-800">② 表定义 ({{ tableCount }} 张,共 {{ totalColumns }} 列,{{ totalRows.toLocaleString() }} 行)</h3>
            <button class="btn btn-outline" @click="store.addTable">
              <Plus class="h-4 w-4" /> 添加表
            </button>
          </div>

          <div v-for="(t, ti) in form.tables" :key="ti" class="rounded border border-slate-200 p-3 space-y-3">
            <!-- 表头 -->
            <div class="flex items-center gap-2">
              <span class="text-sm font-bold text-slate-700">表 #{{ ti + 1 }}</span>
              <input v-model="t.name" class="flex-1 sql-font text-sm" placeholder="表名 (如 ods.orders)" />
              <select v-model="t.role" class="text-xs" title="表角色">
                <option value="source">source 来源</option>
                <option value="target">target 目标</option>
                <option value="intermediate">intermediate 中间</option>
                <option value="reference">reference 参考</option>
              </select>
              <label class="text-xs flex items-center gap-1">
                <span class="text-slate-500">行数</span>
                <input v-model.number="t.rows" type="number" min="0" class="w-28 text-xs" />
              </label>
              <button
                v-if="form.tables.length > 1"
                class="text-status-error hover:text-status-error/70"
                title="删除此表"
                @click="store.removeTable(ti)"
              >
                <Trash2 class="h-4 w-4" />
              </button>
            </div>

            <!-- 列 -->
            <div class="space-y-2 pl-4 border-l-2 border-slate-100">
              <div class="flex items-center justify-between">
                <span class="text-xs font-semibold text-slate-500">列 ({{ t.columns.length }})</span>
                <button class="text-xs text-primary hover:underline" @click="store.addColumn(ti)">
                  <Plus class="h-3 w-3 inline" /> 添加列
                </button>
              </div>
              <ColumnEditor
                v-for="(c, ci) in t.columns"
                :key="ci"
                :column="c"
                :index="ci"
                @remove="store.removeColumn(ti, ci)"
              />
            </div>

            <!-- 索引 -->
            <div class="pl-4 border-l-2 border-slate-100">
              <div class="flex items-center justify-between mb-1">
                <span class="text-xs font-semibold text-slate-500">索引 ({{ t.indexes.length }})</span>
                <button class="text-xs text-primary hover:underline" @click="store.addIndex(ti)">
                  <Plus class="h-3 w-3 inline" /> 添加索引
                </button>
              </div>
              <div v-for="(idx, ii) in t.indexes" :key="ii" class="flex items-center gap-1.5 text-xs mb-1">
                <input v-model="idx.columns_text" class="flex-1 sql-font text-xs" placeholder="列名,逗号分隔" />
                <label class="flex items-center gap-1">
                  <input type="checkbox" v-model="idx.unique" />
                  <span>UNIQUE</span>
                </label>
                <button class="text-status-error" @click="store.removeIndex(ti, ii)">
                  <Trash2 class="h-3 w-3" />
                </button>
              </div>
              <p v-if="!t.indexes.length" class="text-[11px] text-slate-400 italic">
                💡 不加索引 = 测试全表扫场景;加索引验证 plan 走 index range
              </p>
            </div>
          </div>
        </div>

        <!-- anomalies / workloads MVP 提示 -->
        <div class="card p-4 bg-status-info-bg/30 text-xs">
          <div class="font-bold text-status-info mb-1">📝 anomalies / workloads 暂未做可视化</div>
          <p class="text-slate-700 leading-relaxed">
            保存后,你可以 ssh 到云服务器(或本地)编辑 <code class="sql-font">config/scenarios/&lt;id&gt;.yml</code> 加这两段:
            <br/>
            • <b>anomalies</b>:偏差注入(missing_rows / value_drift 等)
            <br/>
            • <b>workloads</b>:工作负载(slow_query / compare_task / lineage_script)
            <br/>
            参考 <code class="sql-font">config/scenarios/orders-recon.example.yml</code>。下一轮做可视化。
          </p>
        </div>

        <!-- 保存按钮 + 错误 -->
        <div v-if="store.saveError" class="rounded p-3 bg-status-error-bg text-status-error text-sm flex items-start gap-2">
          <AlertCircle class="h-4 w-4 mt-0.5" />
          <span>{{ store.saveError }}</span>
        </div>
        <div v-if="store.saveResult" class="rounded p-3 bg-status-success-bg text-status-success text-sm flex items-center gap-2">
          <CheckCircle2 class="h-4 w-4" />
          <span>已保存到 <code class="sql-font">config/scenarios/{{ store.saveResult.saved_path }}</code> — 自动返回场景列表...</span>
        </div>
        <div class="flex flex-wrap gap-2">
          <button
            class="btn btn-primary"
            :disabled="store.saving"
            @click="onSave(false)"
          >
            <Save class="h-4 w-4" />
            {{ store.saving ? '保存中…' : '保存到 config/scenarios' }}
          </button>
          <button
            class="btn btn-outline"
            :disabled="store.saving"
            @click="onSave(true)"
            title="同名 yml 已存在时强制覆盖"
          >
            <Save class="h-4 w-4" />
            覆盖保存
          </button>
        </div>
      </div>

      <!-- ─── 右:实时 yml 预览 ──────────────────────────────────── -->
      <div class="space-y-3">
        <div class="card p-4 space-y-2 sticky top-4">
          <div class="flex items-center justify-between">
            <h3 class="text-sm font-bold text-slate-800 flex items-center gap-1.5">
              <FileText class="h-4 w-4 text-primary" />
              实时 yml 预览
            </h3>
            <button class="btn btn-outline h-7 px-2 text-xs" @click="copyYml">
              <Copy class="h-3 w-3" /> 复制
            </button>
          </div>
          <pre class="px-3 py-2 bg-slate-900 text-green-300 rounded text-[11px] sql-font overflow-auto max-h-[calc(100vh-200px)] whitespace-pre">{{ store.ymlPreview }}</pre>
          <p class="text-[10px] text-slate-400 italic leading-relaxed">
            保存时后端 Pydantic 会校验:Literal 闭集(role / gen / dist kind 等)+ extra='forbid' 拦笔误 + id 正则。
          </p>
        </div>
      </div>
    </div>
  </section>
</template>
