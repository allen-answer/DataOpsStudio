<script setup lang="ts">
/**
 * Phase 14 #3 — 旧 /sql-optimize 现已迁移
 *
 * 原混合 view 拆分为 3 个独立入口:
 * - /sql-diagnosis: 真正 SQL 诊断
 * - /scenario-lab: 测试沙盒
 * - /schema-import: schema 反查生成 yml
 *
 * 本页面只展示迁移提示 + 3 个跳转入口。不再加载混合页面的混乱状态。
 * 老 /admin/sandbox URL 仍 redirect 到 /sql-optimize → 由此页引导。
 */
import { Microscope, FlaskConical, FileCode, ArrowRight } from 'lucide-vue-next'
</script>

<template>
  <section class="space-y-6">
    <div class="card p-8 text-center max-w-3xl mx-auto">
      <div class="text-3xl mb-2">📦 旧入口已迁移</div>
      <h2 class="text-xl font-bold text-slate-800 mb-2">
        SQL 优化沙盒已拆分为 3 个独立页面
      </h2>
      <p class="text-sm text-slate-500 mb-6 leading-relaxed">
        原 /sql-optimize 把 SQL 诊断 + 测试沙盒 + schema 导入混在一起,
        Phase 14 #3 拆开了。请按下面三个入口分别使用,功能更聚焦。
      </p>
      <div class="grid grid-cols-1 md:grid-cols-3 gap-3 text-left">
        <a
          href="#/sql-diagnosis"
          class="card p-4 hover:border-primary hover:shadow-md transition-all group"
        >
          <Microscope class="h-8 w-8 text-primary mb-2" />
          <div class="font-bold text-slate-800 mb-1 flex items-center gap-1">
            SQL 诊断
            <ArrowRight class="h-4 w-4 group-hover:translate-x-1 transition-transform" />
          </div>
          <p class="text-xs text-slate-500 leading-relaxed">
            粘 SQL → preflight + EXPLAIN + AI 复核 + plan diff。
            支持 MySQL / DM / Oracle 三方言。
          </p>
        </a>
        <a
          href="#/scenario-lab"
          class="card p-4 hover:border-primary hover:shadow-md transition-all group"
        >
          <FlaskConical class="h-8 w-8 text-primary mb-2" />
          <div class="font-bold text-slate-800 mb-1 flex items-center gap-1">
            场景测试沙盒
            <ArrowRight class="h-4 w-4 group-hover:translate-x-1 transition-transform" />
          </div>
          <p class="text-xs text-slate-500 leading-relaxed">
            yml 模板 → 造数据 + run-all + record + 回归校验。
            仅 sandbox datasource 可写入。
          </p>
        </a>
        <a
          href="#/schema-import"
          class="card p-4 hover:border-primary hover:shadow-md transition-all group"
        >
          <FileCode class="h-8 w-8 text-primary mb-2" />
          <div class="font-bold text-slate-800 mb-1 flex items-center gap-1">
            Schema 导入
            <ArrowRight class="h-4 w-4 group-hover:translate-x-1 transition-transform" />
          </div>
          <p class="text-xs text-slate-500 leading-relaxed">
            从真实库读 information_schema 反向生成 scenario yml。
            生产 ds 默认只能预览。
          </p>
        </a>
      </div>
      <p class="mt-6 text-[11px] text-slate-400">
        📚 详细文档:
        <a href="https://github.com/allen-answer/DataOpsStudio/blob/main/docs/SQL_DIAGNOSIS.md" target="_blank" class="text-primary hover:underline">SQL_DIAGNOSIS</a>
        ·
        <a href="https://github.com/allen-answer/DataOpsStudio/blob/main/docs/SCENARIO_LAB.md" target="_blank" class="text-primary hover:underline">SCENARIO_LAB</a>
        ·
        <a href="https://github.com/allen-answer/DataOpsStudio/blob/main/docs/SCHEMA_IMPORT.md" target="_blank" class="text-primary hover:underline">SCHEMA_IMPORT</a>
      </p>
    </div>
  </section>
</template>
