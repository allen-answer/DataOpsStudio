/**
 * API 类型 facade —— 把 openapi-typescript 生成的 components.schemas 映射成
 * 友好的 TS 类型别名给 store / view 用。
 *
 * Source of truth：`src/types/api-schema.ts`（自动生成，**不要手改**）。
 *   生成命令：`npm run schema:fetch`（hit dev server 拉 /openapi.json 后生成）
 *
 * 在 store 里写：
 *     import type { ApiUser, ApiDataSource } from '../types/api'
 *     const me = await apiGet<ApiUser>('/api/auth/me')
 *
 * 这一层 facade 的好处：
 * 1. 不用每次都写 `components['schemas']['User']` 那种长式
 * 2. 后端字段名变（如 `display_name` → `displayName`）→ 重新 schema:fetch
 *    后这里类型自动跟着变，所有 caller 立刻有 typecheck 提示
 * 3. 前端单独看就知道有哪些 API 资源（只需 import './types/api'，不用看后端）
 */
import type { components } from './api-schema'

type Schemas = components['schemas']

// ─── 核心资源 ───────────────────────────────────────────────────────────────
// 注：后端没有所有资源都有显式 Update model（很多 endpoint 用 Create model 当 PUT
// payload）。只暴露后端真有的 schema —— 没有的就让 caller 用 Partial<ApiX> 自己拼。
export type ApiUser = Schemas['User']
export type ApiUserRole = ApiUser['role']      // 'admin' | 'editor' | 'viewer'
export type ApiUserCreate = Schemas['UserCreate']
export type ApiUserUpdate = Schemas['UserUpdate']

export type ApiDataSource = Schemas['DataSource']
export type ApiDataSourceCreate = Schemas['DataSourceCreate']

export type ApiCompareTask = Schemas['CompareTask']
export type ApiCompareTaskCreate = Schemas['CompareTaskCreate']

export type ApiWorkflow = Schemas['Workflow']
export type ApiWorkflowCreate = Schemas['WorkflowCreate']
export type ApiWorkflowRun = Schemas['WorkflowRun']
export type ApiWorkflowRunSummary = Schemas['WorkflowRunSummary']
export type ApiWorkflowNode = Schemas['WorkflowNode']
export type ApiWorkflowNodeRun = Schemas['WorkflowNodeRun']
export type ApiWorkflowNodeType = Schemas['WorkflowNodeType']
export type ApiWorkflowStatus = Schemas['WorkflowStatus']
export type ApiWorkflowRunStatus = Schemas['WorkflowRunStatus']

export type ApiWorkflowTemplate = Schemas['WorkflowTemplate']
export type ApiWorkflowTemplateCreate = Schemas['WorkflowTemplateCreate']
export type ApiWorkflowTemplateInstantiate = Schemas['WorkflowTemplateInstantiate']

export type ApiProject = Schemas['Project']
export type ApiProjectCreate = Schemas['ProjectCreate']

export type ApiArtifact = Schemas['Artifact']

// ─── 资产 / aspect（Phase 10）────────────────────────────────────────────────
export type ApiAspectUpsertBody = Schemas['AspectUpsertBody']

// ─── 枚举 ───────────────────────────────────────────────────────────────────
export type ApiDatabaseType = Schemas['DatabaseType']    // MySQL / Oracle / DM / DB2
export type ApiSourceKind = Schemas['SourceKind']        // sql / excel / csv / parquet
export type ApiSqlMode = Schemas['SqlMode']              // single / double
export type ApiAssetKind = Schemas['AssetKind']
