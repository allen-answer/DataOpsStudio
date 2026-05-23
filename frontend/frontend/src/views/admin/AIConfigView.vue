<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import {
  Bot,
  CheckCircle2,
  FileText,
  KeyRound,
  Languages,
  RefreshCw,
  Save,
  ShieldCheck,
  Sparkles,
  TestTube2,
  Trash2,
  XCircle,
} from 'lucide-vue-next'
import { apiGet, apiJson, invalidateAIEnabledCache } from '../../api'
import { useNoticeStore } from '../../stores/notice'
import { withStepUpRetry } from '../../utils/stepUpRetry'

interface AIConfigPayload {
  provider?: string
  model?: string
  base_url?: string
  timeout_seconds?: number
  include_raw?: boolean
  enable_inference?: boolean
  enable_auto_translation?: boolean
  api_key_set?: boolean
  [key: string]: unknown
}

interface AITestResult {
  ok: boolean
  status?: string
  error?: string
  [key: string]: unknown
}

const noticeStore = useNoticeStore()

const loading = ref<boolean>(false)
const saving = ref<boolean>(false)
const testing = ref<boolean>(false)
const config = ref<AIConfigPayload | null>(null)
const testResult = ref<AITestResult | null>(null)

const draft = reactive({
  provider: 'off',
  model: '',
  base_url: '',
  api_key: '',
  timeout_seconds: 60,
  include_raw: false,
  enable_inference: false,
  enable_auto_translation: false,
  clear_api_key: false,
})

const PROVIDERS = [
  { value: 'anthropic', label: 'Anthropic / Claude', hint: 'Use /v1/messages protocol with x-api-key.' },
  { value: 'anthropic-compatible', label: 'Anthropic-Compatible', hint: 'For DeepSeek Anthropic API and Claude-compatible gateways.' },
  { value: 'off', label: '关闭（离线规则分析）', hint: '默认模式，不调用外部模型。' },
  { value: 'mock', label: 'Mock（本地演示）', hint: '不需要密钥，用于验证前端和流程。' },
  { value: 'openai', label: 'OpenAI / 兼容接口', hint: '使用 /v1/chat/completions 兼容协议。' },
  { value: 'openai-compatible', label: 'OpenAI-Compatible', hint: '适合私有网关、One API、LiteLLM。' },
  { value: 'ollama', label: 'Ollama（本地模型）', hint: '默认 http://localhost:11434，不需要 API Key。' },
  { value: 'azure', label: 'Azure OpenAI', hint: '按兼容接口配置 Base URL 和 Key。' },
  { value: 'http', label: '自定义 HTTP 兼容', hint: '当前按 OpenAI 兼容格式发送。' },
]

const selectedProvider = computed(() => PROVIDERS.find(item => item.value === draft.provider) || PROVIDERS[0])
const requiresApiKey = computed(() => ['openai', 'openai-compatible', 'azure', 'http', 'anthropic', 'anthropic-compatible'].includes(draft.provider))
const requiresModel = computed(() => draft.provider !== 'off')
const modelPlaceholder = computed(() => {
  if (draft.provider === 'anthropic') return 'claude-sonnet-4-5-20250929'
  if (draft.provider === 'anthropic-compatible') return 'DeepSeek: deepseek-chat'
  if (draft.provider === 'ollama') return 'llama3.1 / qwen2.5'
  return 'deepseek-chat / kimi-k2.5 / gpt-4.1-mini'
})
const baseUrlPlaceholder = computed(() => {
  if (draft.provider === 'anthropic') return 'https://api.anthropic.com'
  if (draft.provider === 'anthropic-compatible') return 'DeepSeek: https://api.deepseek.com/anthropic'
  if (draft.provider === 'ollama') return 'http://localhost:11434'
  return 'DeepSeek: https://api.deepseek.com/v1'
})
const baseUrlHelp = computed(() => {
  if (['anthropic', 'anthropic-compatible'].includes(draft.provider)) {
    return '可以填 Anthropic SDK Base URL，也可以填完整 /v1/messages 地址；系统会自动规整。'
  }
  if (draft.provider === 'ollama') return 'Ollama 默认会调用 /api/generate。'
  return '可以填 SDK Base URL，也可以填完整 chat/completions 地址；系统会自动规整。'
})
const canSave = computed(() => {
  if (!draft.provider) return false
  if (requiresModel.value && !draft.model.trim()) return false
  if (requiresApiKey.value && !draft.api_key.trim() && !config.value?.api_key_set) return false
  return true
})

function hydrate(nextConfig: AIConfigPayload): void {
  config.value = nextConfig
  draft.provider = nextConfig.provider || 'off'
  draft.model = nextConfig.model || ''
  draft.base_url = nextConfig.base_url || ''
  draft.api_key = ''
  draft.timeout_seconds = Number(nextConfig.timeout_seconds || 20)
  draft.include_raw = Boolean(nextConfig.include_raw)
  draft.enable_inference = Boolean(nextConfig.enable_inference)
  draft.enable_auto_translation = Boolean(nextConfig.enable_auto_translation)
  draft.clear_api_key = false
}

async function reload(): Promise<void> {
  loading.value = true
  try {
    hydrate(await apiGet<AIConfigPayload>('/api/lineage/ai/config'))
  } catch (err: any) {
    noticeStore.setNotice(`加载 AI 配置失败：${err?.message || err}`)
  } finally {
    loading.value = false
  }
}

function payload({ includeKey = true }: { includeKey?: boolean } = {}): Record<string, unknown> {
  const body: Record<string, unknown> = {
    provider: draft.provider,
    model: draft.model.trim(),
    base_url: draft.base_url.trim(),
    timeout_seconds: Number(draft.timeout_seconds || 20),
    include_raw: Boolean(draft.include_raw),
    enable_inference: Boolean(draft.enable_inference),
    enable_auto_translation: Boolean(draft.enable_auto_translation),
  }
  if (includeKey && draft.api_key.trim()) body.api_key = draft.api_key
  if (includeKey && draft.clear_api_key) body.clear_api_key = true
  return body
}

async function saveConfig(): Promise<void> {
  if (!canSave.value) {
    noticeStore.setNotice('请补齐 provider / model / API Key 配置')
    return
  }
  saving.value = true
  try {
    // 端点已挂 step-up；近 300s 没认证 → step_up_required → helper prompt 密码
    // → verify-password → 换新 token → 重试一次
    const result = await withStepUpRetry(
      () => apiJson<AIConfigPayload>('/api/lineage/ai/config', 'PUT', payload()),
    )
    hydrate(result)
    invalidateAIEnabledCache()  // provider 改了 → api.js 错误翻译 hook 重新探测
    noticeStore.setNotice('AI 配置已保存，API Key 已加密落盘')
  } catch (err: any) {
    const msg = String(err?.message || err)
    if (msg === 'step_up_cancelled') {
      // 用户在密码 prompt 点了取消，静默退出
    } else if (msg === 'step_up_verify_failed') {
      noticeStore.setNotice('密码错误，AI 配置保存已取消')
    } else {
      noticeStore.setNotice(`保存 AI 配置失败：${msg}`)
    }
  } finally {
    saving.value = false
  }
}

async function testConnection(): Promise<void> {
  testing.value = true
  testResult.value = null
  try {
    testResult.value = await apiJson<AITestResult>('/api/lineage/ai/test', 'POST', payload())
    noticeStore.setNotice(testResult.value.ok ? 'AI 连接测试成功' : `AI 连接测试失败：${testResult.value.error || '-'}`)
  } catch (err: any) {
    testResult.value = { ok: false, status: 'error', error: err?.message || String(err) }
    noticeStore.setNotice(`AI 连接测试失败：${err?.message || err}`)
  } finally {
    testing.value = false
  }
}

function toggleClearApiKey(): void {
  if (!config.value?.api_key_set) return
  if (draft.clear_api_key) {
    draft.clear_api_key = false
    return
  }
  const confirmed = window.confirm('保存配置后将清除已保存的 API Key，确认标记清除吗？')
  if (!confirmed) return
  draft.api_key = ''
  draft.clear_api_key = true
}

onMounted(() => {
  reload()
})
</script>

<template>
  <section class="space-y-6">
    <header class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h2 class="text-2xl font-bold text-slate-800">AI 配置</h2>
        <p class="mt-1 text-sm text-slate-500">
          配置血缘分析的可插拔 AI Provider。默认关闭，离线规则分析不受影响。
        </p>
      </div>
      <button class="btn btn-outline gap-1.5" :disabled="loading" @click="reload">
        <RefreshCw class="h-4 w-4" :class="loading ? 'animate-spin' : ''" />
        刷新
      </button>
    </header>

    <div class="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
      <div class="card p-5">
        <div class="mb-4 flex items-start gap-3">
          <div class="grid h-10 w-10 place-items-center rounded-xl bg-primary-light text-primary">
            <Bot class="h-5 w-5" />
          </div>
          <div class="min-w-0">
            <h3 class="text-sm font-bold text-slate-800">Provider</h3>
            <p class="mt-1 text-xs text-slate-500">{{ selectedProvider.hint }}</p>
          </div>
        </div>

        <div class="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <label class="space-y-1.5">
            <span class="text-xs font-bold uppercase tracking-wider text-slate-500">Provider</span>
            <select v-model="draft.provider">
              <option v-for="item in PROVIDERS" :key="item.value" :value="item.value">{{ item.label }}</option>
            </select>
          </label>

          <label class="space-y-1.5">
            <span class="text-xs font-bold uppercase tracking-wider text-slate-500">Model</span>
            <input v-model="draft.model" :disabled="draft.provider === 'off'" :placeholder="modelPlaceholder" />
          </label>

          <label class="space-y-1.5 lg:col-span-2">
            <span class="text-xs font-bold uppercase tracking-wider text-slate-500">Base URL</span>
            <input v-model="draft.base_url" :placeholder="baseUrlPlaceholder" />
            <p class="text-xs text-slate-500">
              {{ baseUrlHelp }}
            </p>
          </label>

          <label class="space-y-1.5 lg:col-span-2">
            <span class="flex items-center gap-1.5 text-xs font-bold uppercase tracking-wider text-slate-500">
              <KeyRound class="h-3.5 w-3.5" />
              API Key
            </span>
            <input
              v-model="draft.api_key"
              type="password"
              autocomplete="new-password"
              :disabled="!requiresApiKey || draft.clear_api_key"
              :placeholder="config?.api_key_set ? '已保存密钥，留空不修改' : 'sk-...'"
            />
            <p class="text-xs text-slate-500">
              保存后只写入加密密文，接口和页面不会回显明文。
            </p>
          </label>

          <label class="space-y-1.5">
            <span class="text-xs font-bold uppercase tracking-wider text-slate-500">Timeout seconds</span>
            <input v-model.number="draft.timeout_seconds" type="number" min="1" max="120" />
          </label>

          <div class="space-y-3 lg:col-span-2">
            <div class="flex items-center justify-between gap-3">
              <div>
                <h4 class="text-sm font-bold text-slate-800">功能开关</h4>
                <p class="mt-1 text-xs text-slate-500">默认保持克制，按需开启会消耗 token 的能力。</p>
              </div>
              <span class="rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-medium text-slate-500">
                Provider: {{ draft.provider || 'off' }}
              </span>
            </div>

            <label class="flex cursor-pointer items-start justify-between gap-4 rounded-xl border border-slate-200 bg-white p-3 transition hover:border-primary/40 hover:bg-primary-light/40">
              <span class="flex min-w-0 gap-3">
                <span class="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-slate-100 text-slate-600">
                  <FileText class="h-4 w-4" />
                </span>
                <span class="min-w-0">
                  <span class="block text-sm font-semibold text-slate-800">保存 AI 原始响应到报告</span>
                  <span class="mt-1 block text-xs leading-relaxed text-slate-500">
                    用于排查 prompt、模型返回和解析问题；生产环境建议只在定位问题时开启。
                  </span>
                </span>
              </span>
              <input v-model="draft.include_raw" type="checkbox" class="sr-only" />
              <span
                class="relative mt-1 h-6 w-11 shrink-0 rounded-full transition"
                :class="draft.include_raw ? 'bg-primary' : 'bg-slate-300'"
              >
                <span
                  class="absolute top-1 h-4 w-4 rounded-full bg-white shadow transition"
                  :class="draft.include_raw ? 'left-6' : 'left-1'"
                ></span>
              </span>
            </label>

            <label class="flex cursor-pointer items-start justify-between gap-4 rounded-xl border border-slate-200 bg-white p-3 transition hover:border-primary/40 hover:bg-primary-light/40">
              <span class="flex min-w-0 gap-3">
                <span class="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-primary-light text-primary">
                  <Sparkles class="h-4 w-4" />
                </span>
                <span class="min-w-0">
                  <span class="block text-sm font-semibold text-slate-800">启用 AI 解析失败兜底</span>
                  <span class="mt-1 block text-xs leading-relaxed text-slate-500">
                    对静态解析失败的片段推断 source / target 表，输出独立
                    <code class="rounded bg-slate-100 px-1 font-mono text-[10.5px]">ai_inferred</code>
                    字段；白名单约束只允许使用脚本中出现过的表名和字段名。
                  </span>
                </span>
              </span>
              <input v-model="draft.enable_inference" type="checkbox" class="sr-only" />
              <span
                class="relative mt-1 h-6 w-11 shrink-0 rounded-full transition"
                :class="draft.enable_inference ? 'bg-primary' : 'bg-slate-300'"
              >
                <span
                  class="absolute top-1 h-4 w-4 rounded-full bg-white shadow transition"
                  :class="draft.enable_inference ? 'left-6' : 'left-1'"
                ></span>
              </span>
            </label>

            <label class="flex cursor-pointer items-start justify-between gap-4 rounded-xl border border-slate-200 bg-white p-3 transition hover:border-primary/40 hover:bg-primary-light/40">
              <span class="flex min-w-0 gap-3">
                <span class="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-status-info-bg text-status-info">
                  <Languages class="h-4 w-4" />
                </span>
                <span class="min-w-0">
                  <span class="block text-sm font-semibold text-slate-800">错误信息 AI 解释模式</span>
                  <span class="mt-1 block text-xs leading-relaxed text-slate-500">
                    关闭时由用户点击“AI 解释”后再调用翻译接口；开启后 5xx 和长 4xx 错误自动调用
                    <code class="rounded bg-slate-100 px-1 font-mono text-[10.5px]">/api/ai/translate-error</code>
                    并返回中文解释和排查建议。
                  </span>
                </span>
              </span>
              <input v-model="draft.enable_auto_translation" type="checkbox" class="sr-only" />
              <span
                class="relative mt-1 h-6 w-11 shrink-0 rounded-full transition"
                :class="draft.enable_auto_translation ? 'bg-primary' : 'bg-slate-300'"
              >
                <span
                  class="absolute top-1 h-4 w-4 rounded-full bg-white shadow transition"
                  :class="draft.enable_auto_translation ? 'left-6' : 'left-1'"
                ></span>
              </span>
            </label>

            <div class="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-rose-100 bg-rose-50 px-3 py-3">
              <div class="flex min-w-0 gap-3">
                <span class="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-white text-rose-600">
                  <Trash2 class="h-4 w-4" />
                </span>
                <div class="min-w-0">
                  <p class="text-sm font-semibold text-rose-800">清除已保存 API Key</p>
                  <p class="mt-1 text-xs text-rose-700/75">
                    {{ draft.clear_api_key ? '已标记清除，点击保存配置后生效。' : '不会立即删除，确认后需要再保存配置。' }}
                  </p>
                </div>
              </div>
              <button
                type="button"
                class="rounded-lg border px-3 py-1.5 text-xs font-bold transition"
                :class="draft.clear_api_key ? 'border-rose-300 bg-white text-rose-700' : 'border-rose-200 bg-rose-600 text-white hover:bg-rose-700 disabled:cursor-not-allowed disabled:bg-rose-200'"
                :disabled="!config?.api_key_set"
                @click="toggleClearApiKey"
              >
                {{ draft.clear_api_key ? '取消清除' : '标记清除' }}
              </button>
            </div>
          </div>
        </div>

        <div class="mt-5 flex flex-wrap justify-end gap-2">
          <button class="btn btn-outline gap-1.5" :disabled="testing || draft.provider === 'off'" @click="testConnection">
            <TestTube2 class="h-4 w-4" />
            测试连接
          </button>
          <button class="btn btn-primary gap-1.5" :disabled="saving || !canSave" @click="saveConfig">
            <Save class="h-4 w-4" />
            保存配置
          </button>
        </div>
      </div>

      <aside class="space-y-4">
        <div class="card p-5">
          <div class="mb-3 flex items-center gap-2">
            <ShieldCheck class="h-4 w-4 text-emerald-600" />
            <h3 class="text-sm font-bold text-slate-800">安全状态</h3>
          </div>
          <dl class="space-y-3 text-sm">
            <div class="flex justify-between gap-3">
              <dt class="text-slate-500">当前来源</dt>
              <dd class="font-medium text-slate-800">{{ config?.source || '-' }}</dd>
            </div>
            <div class="flex justify-between gap-3">
              <dt class="text-slate-500">Provider</dt>
              <dd class="font-medium text-slate-800">{{ config?.provider || 'off' }}</dd>
            </div>
            <div class="flex justify-between gap-3">
              <dt class="text-slate-500">已配置</dt>
              <dd>
                <span class="status-badge" :class="config?.configured ? 'status-success' : 'status-pending'">
                  {{ config?.configured ? '是' : '否' }}
                </span>
              </dd>
            </div>
            <div class="flex justify-between gap-3">
              <dt class="text-slate-500">API Key</dt>
              <dd>
                <span class="status-badge" :class="config?.api_key_set ? 'status-success' : 'status-pending'">
                  {{ config?.api_key_set ? '已保存' : '未保存' }}
                </span>
              </dd>
            </div>
            <div class="flex justify-between gap-3">
              <dt class="text-slate-500">加密落盘</dt>
              <dd>
                <span class="status-badge" :class="config?.api_key_encrypted ? 'status-success' : 'status-pending'">
                  {{ config?.api_key_encrypted ? '是' : '-' }}
                </span>
              </dd>
            </div>
            <div class="flex justify-between gap-3">
              <dt class="text-slate-500">更新时间</dt>
              <dd class="text-right text-xs text-slate-600">{{ config?.updated_at || '-' }}</dd>
            </div>
          </dl>
        </div>

        <div v-if="testResult" class="card p-5">
          <div class="mb-3 flex items-center gap-2">
            <CheckCircle2 v-if="testResult.ok" class="h-4 w-4 text-emerald-600" />
            <XCircle v-else class="h-4 w-4 text-rose-600" />
            <h3 class="text-sm font-bold text-slate-800">连接测试</h3>
          </div>
          <p class="text-sm" :class="testResult.ok ? 'text-emerald-700' : 'text-rose-700'">
            {{ testResult.ok ? '连接成功' : (testResult.error || '连接失败') }}
          </p>
          <p class="mt-2 text-xs text-slate-500">
            {{ testResult.provider || '-' }} · {{ testResult.model || '-' }} · {{ testResult.elapsed_seconds || 0 }}s
          </p>
          <p v-if="testResult.summary" class="mt-2 rounded-lg bg-slate-50 p-2 text-xs text-slate-600">
            {{ testResult.summary }}
          </p>
        </div>
      </aside>
    </div>
  </section>
</template>
