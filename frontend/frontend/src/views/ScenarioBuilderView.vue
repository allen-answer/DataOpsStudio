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
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  FlaskConical, FilePlus2, Save, ChevronLeft, Plus, Trash2, Copy,
  AlertCircle, CheckCircle2, FileText, ClipboardPaste, BookTemplate,
} from 'lucide-vue-next'
import { useScenarioBuilderStore } from '../stores/scenarioBuilder'
import { useNoticeStore } from '../stores/notice'
import ColumnEditor from '../components/scenario-builder/ColumnEditor.vue'
import AnomalyEditor from '../components/scenario-builder/AnomalyEditor.vue'
import WorkloadEditor from '../components/scenario-builder/WorkloadEditor.vue'

const store = useScenarioBuilderStore()
const noticeStore = useNoticeStore()
const router = useRouter()

const form = store.form

// Phase 14 #3 Round 5 — 粘贴 DDL 批量添加列
// per-table 的临时 textarea 内容(open + ddl text)
const ddlState = reactive<Record<number, { open: boolean; text: string }>>({})
function toggleDdlInput(idx: number) {
  if (!ddlState[idx]) ddlState[idx] = { open: false, text: '' }
  ddlState[idx].open = !ddlState[idx].open
}
function parseAndAddFromDdl(idx: number) {
  const state = ddlState[idx]
  if (!state?.text.trim()) return
  const r = store.addColumnsFromDdl(idx, state.text)
  noticeStore.setNotice(
    `已添加 ${r.added} 列${r.pk_count ? ` (${r.pk_count} 个 PK)` : ''}`
    + `${r.index_count ? ` + ${r.index_count} 个索引` : ''}`
    + `${(r as any).fk_count ? ` + ${(r as any).fk_count} 个外键` : ''}`
    + `${r.warnings.length ? ` ⚠ ${r.warnings.length} 行未解析` : ''}`,
  )
  if (r.added > 0) {
    state.text = ''
    state.open = false
  }
}

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

// Round 6 L — 模板列表
const selectedTemplate = computed({
  get: () => '',
  set: (v: string) => { if (v) store.loadFromTemplate(v) },
})
onMounted(() => { store.loadTemplatesList() })

// Round 6 M — metadata csv 上传(覆盖现有 form.tables)
const metadataOpen = ref(false)
const metadataCsv = ref('')
function toggleMetadataOpen() { metadataOpen.value = !metadataOpen.value }
async function submitMetadataCsv() {
  if (!metadataCsv.value.trim()) return
  if (!confirm('⚠ 这会替换当前 form 里的所有表/列。继续?')) return
  const ok = await store.importFromMetadataCsv(metadataCsv.value)
  if (ok) {
    metadataCsv.value = ''
    metadataOpen.value = false
    noticeStore.setNotice('✓ 已从 metadata csv 生成表/列;请补字段类型 + 调 generator')
  }
}
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
      <div class="flex items-center gap-2">
        <label class="flex items-center gap-1 text-xs text-slate-600">
          <BookTemplate class="h-4 w-4 text-primary" />
          <span>📚 从模板加载:</span>
          <select
            :value="selectedTemplate"
            @change="(e: any) => (selectedTemplate = e.target.value)"
            class="sql-font text-xs"
          >
            <option value="">— 选模板 —</option>
            <option
              v-for="t in store.templatesList"
              :key="t.file"
              :value="t.file"
              :title="t.description"
            >{{ t.name }} ({{ t.tables_count }} 表)</option>
          </select>
        </label>
        <a href="#/scenario-lab" class="btn btn-outline">
          <ChevronLeft class="h-4 w-4" /> 取消返回
        </a>
      </div>
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
                <div class="flex items-center gap-2 text-xs">
                  <button
                    class="text-primary hover:underline flex items-center gap-1"
                    title="粘贴 CREATE TABLE / desc 输出,自动解析所有列"
                    @click="toggleDdlInput(ti)"
                  >
                    <ClipboardPaste class="h-3 w-3" /> 粘贴 DDL 批量添加
                  </button>
                  <span class="text-slate-300">|</span>
                  <button class="text-primary hover:underline" @click="store.addColumn(ti)">
                    <Plus class="h-3 w-3 inline" /> 单列添加
                  </button>
                </div>
              </div>

              <!-- DDL 粘贴输入区 -->
              <div
                v-if="ddlState[ti]?.open"
                class="rounded border border-primary bg-primary-light/20 p-3 space-y-2"
              >
                <p class="text-[11px] text-slate-600 leading-relaxed">
                  粘贴 <code class="sql-font">CREATE TABLE</code> 整段 / 列定义片段 / <code class="sql-font">desc</code> 输出。
                  parser 会自动提取列名/类型/PK/NOT NULL,按类型选默认 generator
                  (BIGINT→random_int,VARCHAR→realistic,DATE→timestamp,...)。<br/>
                  <span class="text-[10px] text-slate-400">⚠ 关键列添加后展开手动调 generator 即可,普通列保持默认</span>
                </p>
                <textarea
                  v-model="ddlState[ti].text"
                  rows="6"
                  class="w-full sql-font text-xs"
                  :placeholder="`例如,直接从 SHOW CREATE TABLE 复制:\n\nid BIGINT NOT NULL,\ndata_dt VARCHAR(8) NOT NULL,\ncust_name VARCHAR(200),\nthis_bal DECIMAL(28,8),\noccur_date DATETIME,\nPRIMARY KEY (id),\nKEY idx_dt (data_dt)`"
                />
                <div class="flex gap-2">
                  <button class="btn btn-primary text-xs" @click="parseAndAddFromDdl(ti)">
                    <ClipboardPaste class="h-3.5 w-3.5" /> 解析并添加
                  </button>
                  <button class="btn btn-outline text-xs" @click="toggleDdlInput(ti)">取消</button>
                </div>
              </div>

              <ColumnEditor
                v-for="(c, ci) in t.columns"
                :key="ci"
                :column="c"
                :index="ci"
                :all-tables="form.tables"
                :current-table-name="t.name"
                @remove="store.removeColumn(ti, ci)"
              />
              <p v-if="!t.columns.length" class="text-xs text-slate-400 italic">
                还没列,点上面「粘贴 DDL 批量添加」或「单列添加」开始
              </p>
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

        <!-- Round 6 M — metadata csv 上传 -->
        <div class="card p-4 bg-status-info-bg/20 border-status-info">
          <div class="flex items-center justify-between">
            <div class="text-sm font-bold text-status-info flex items-center gap-1.5">
              📊 从 metadata csv 导入
              <span class="text-[10px] font-normal text-slate-500 normal-case">
                — DBA 给的 ANALYZE 风格脱敏快照
              </span>
            </div>
            <button class="text-xs text-primary hover:underline" @click="toggleMetadataOpen">
              {{ metadataOpen ? '收起' : '展开' }}
            </button>
          </div>
          <div v-if="metadataOpen" class="mt-3 space-y-2">
            <p class="text-[11px] text-slate-600 leading-relaxed">
              请 DBA 提供如下脱敏快照(不含行级数据,法规安全),粘贴 csv:
            </p>
            <pre class="px-2 py-1 bg-slate-100 text-[10px] rounded">table,row_count
ods_acc_fundacc,5234560
ods_ast_nor_acc_fund,5180230

table,column,ndv,top_5_values,top_5_freq
ods_acc_fundacc,branch_code,312,"1001|1002|1003|8001|9999","0.18|0.15|0.10|0.05|0.02"
ods_acc_fundacc,source_sys,4,"jzjy|jgkh|rzrq|opt","0.55|0.25|0.15|0.05"</pre>
            <textarea
              v-model="metadataCsv"
              rows="8"
              class="w-full sql-font text-xs"
              placeholder="粘贴 metadata csv 文本..."
            />
            <div class="flex items-center gap-2">
              <button
                class="btn btn-primary text-xs"
                :disabled="store.importingMetadata || !metadataCsv.trim()"
                @click="submitMetadataCsv"
              >
                {{ store.importingMetadata ? '导入中…' : '生成表 / 列(覆盖)' }}
              </button>
              <span v-if="store.metadataError" class="text-xs text-status-error">{{ store.metadataError }}</span>
            </div>
          </div>
        </div>

        <!-- 偏差注入 (anomalies) -->
        <div class="card p-5 space-y-3">
          <div class="flex items-center justify-between">
            <h3 class="text-sm font-bold text-slate-800">
              ③ 偏差注入 ({{ form.anomalies.length }})
              <span class="text-[10px] font-normal text-slate-400 ml-1">
                — 让 source/target 故意有差异,演示对比功能
              </span>
            </h3>
            <button class="btn btn-outline" @click="store.addAnomaly">
              <Plus class="h-4 w-4" /> 添加偏差
            </button>
          </div>
          <AnomalyEditor
            v-for="(a, ai) in form.anomalies"
            :key="ai"
            :anomaly="a"
            :index="ai"
            :all-tables="form.tables"
            @remove="store.removeAnomaly(ai)"
          />
          <p v-if="!form.anomalies.length" class="text-xs text-slate-400 italic">
            暂无偏差注入 — 数据对比 fixture 才需要,纯 SQL 优化 case 可留空
          </p>
        </div>

        <!-- 工作负载 (workloads) -->
        <div class="card p-5 space-y-3">
          <div class="flex items-center justify-between">
            <h3 class="text-sm font-bold text-slate-800">
              ④ 工作负载 ({{ form.workloads.length }})
              <span class="text-[10px] font-normal text-slate-400 ml-1">
                — 跑啥(慢 SQL / 对比 / 血缘)
              </span>
            </h3>
            <div class="flex items-center gap-1.5 text-xs">
              <button class="text-primary hover:underline" @click="store.addWorkload('slow_query')">
                <Plus class="h-3 w-3 inline" /> 慢 SQL
              </button>
              <span class="text-slate-300">|</span>
              <button class="text-primary hover:underline" @click="store.addWorkload('compare_task')">
                <Plus class="h-3 w-3 inline" /> 对比任务
              </button>
              <span class="text-slate-300">|</span>
              <button class="text-primary hover:underline" @click="store.addWorkload('lineage_script')">
                <Plus class="h-3 w-3 inline" /> 血缘脚本
              </button>
            </div>
          </div>
          <WorkloadEditor
            v-for="(w, wi) in form.workloads"
            :key="wi"
            :workload="w"
            :index="wi"
            @remove="store.removeWorkload(wi)"
          />
          <p v-if="!form.workloads.length" class="text-xs text-slate-400 italic">
            暂无 workload — 至少添加一个 slow_query / compare_task 才能跑 fixture
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
