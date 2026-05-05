import { computed, ref, watch, type Ref } from 'vue'

// 把 LineageGraph 的数据派生 / 筛选 / 聚焦逻辑统一抽到一个 composable，
// 让 G6 实现（components/LineageGraph.vue）和实验中的 Cytoscape 实现
// （components/LineageGraphCytoscape.vue）共享同一份数据流。
//
// 视图组件只负责：根据 visibleData / projectedData 渲染节点/边、绑定交互、
// 把交互（搜索、筛选、聚焦、详情）回写到这里暴露的 ref。
//
// 用法：
//   const graphData = useLineageGraphData(toRef(props, 'groups'), toRef(props, 'edges'))
//
// 命名约定：
//   - allGraphData       —— 没经过任何筛选的全集（按 groups+edges 投出来的 nodes/edges）
//   - filteredBase       —— 经过 role/schema/edge-type/confidence/script 筛选后的子集
//                            （Cytoscape compound 模式直接消费这个，schema 通过 parent 表达）
//   - projectedBase      —— 在 filteredBase 之上做 G6 的 schema combo 投影
//                            （G6 模式消费，把折叠 schema 的多张表合成一个虚拟节点）
//   - graphData / cyData —— 在以上两个 base 上分别再叠加 focal + hop BFS
//
// G6 模式：filteredBase → projectedBase → graphData
// Cytoscape 模式：filteredBase → cyData（compound 由视图自己装配）
//
// S4.A 收尾：迁 .ts。input groups / edges 是 lineage analyze 的 result.graph_groups
// + result.graph_edges —— 后端结构在 LineageReport（暂时未上 codegen），用本地
// 类型描述，等后端 model 收口后再换 codegen 类型。

// ─── 类型 ────────────────────────────────────────────────────────────────────

export interface LineageGroup {
  target_table?: string
  source_tables?: string[]
  dependency_tables?: string[]
}

/** lineage analyze 输出的 edge metadata —— 给 hover 详情用。 */
export interface LineageEdgeMeta {
  source_table?: string
  target_table?: string
  edge_type?: string
  confidence?: string
  file_name?: string
  [key: string]: unknown
}

export type NodeRole = 'source' | 'target' | 'dependency' | 'combo'

export interface NodeData {
  role: NodeRole
  schema: string
  isCombo?: boolean
  count?: number
  matched?: boolean
  [key: string]: unknown
}

export interface GraphNode {
  id: string
  data: NodeData
}

export interface EdgeData {
  details: LineageEdgeMeta[]
  dependency: boolean
  aggregatedCount?: number
}

export interface GraphEdge {
  id: string
  source: string
  target: string
  data: EdgeData
}

export interface GraphSnapshot {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

export type FocusMode = 'neighborhood' | 'upstream' | 'downstream' | 'all'

export interface SelectedItem {
  type: 'node' | 'edge'
  id: string
}

export interface TableRow {
  id: string
  schema: string
  hops: number | string
  direction: string
  upstream: number
  downstream: number
  isCombo: boolean
}

export interface SelectedNodeDetails {
  id: string
  upstream: string[]
  downstream: string[]
  edges: LineageEdgeMeta[]
}

interface AdjacencyMaps {
  out: Map<string, string[]>
  inn: Map<string, string[]>
}


const COMBO_PREFIX = '__combo:'

const schemaName = (name: string): string =>
  (name.includes('.') ? name.split('.').slice(0, -1).join('.') : '(默认)')
const edgeKey = (source: string, target: string): string => `${source}|||${target}`
const unique = <T>(items: (T | null | undefined | false | '')[]): T[] =>
  Array.from(new Set(items.filter(Boolean) as T[]))
const basename = (path: string | undefined | null): string => {
  if (!path) return ''
  const i = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\'))
  return i >= 0 ? path.slice(i + 1) : path
}

function bfsLimited(
  start: string,
  getNeighbors: (node: string) => string[] | undefined,
  depth: number,
): Set<string> {
  const visited = new Set([start])
  let frontier = [start]
  for (let d = 0; d < depth; d++) {
    const next: string[] = []
    for (const node of frontier) {
      for (const neighbor of getNeighbors(node) || []) {
        if (visited.has(neighbor)) continue
        visited.add(neighbor)
        next.push(neighbor)
      }
    }
    if (!next.length) break
    frontier = next
  }
  return visited
}

function buildAdjacency(base: GraphSnapshot): AdjacencyMaps {
  const out = new Map<string, string[]>()
  const inn = new Map<string, string[]>()
  for (const edge of base.edges) {
    out.set(edge.source, [...(out.get(edge.source) || []), edge.target])
    inn.set(edge.target, [...(inn.get(edge.target) || []), edge.source])
  }
  return { out, inn }
}

export function useLineageGraphData(
  groupsRef: Ref<LineageGroup[] | null | undefined>,
  edgesRef: Ref<LineageEdgeMeta[] | null | undefined>,
) {
  // ---------- 筛选 / 聚焦 / 选择状态 ----------
  const searchText = ref<string>('')
  const matchIndex = ref<number>(0)
  const focusMode = ref<FocusMode>('neighborhood')
  const hopDepth = ref<number>(1)
  const clickedFocalId = ref<string>('')
  const roleFilter = ref<NodeRole | 'all'>('all')
  const edgeTypeFilter = ref<string>('all')
  const confidenceFilter = ref<string>('all')
  const scriptFilter = ref<string>('')
  const schemaFilter = ref<string>('all')
  const collapsedSchemas = ref<Set<string>>(new Set())
  const collapsedScripts = ref<Set<string>>(new Set())
  const largeGraphExpanded = ref<boolean>(false)
  const selectedItem = ref<SelectedItem | null>(null)

  // 基础数据：从 groups/edges 投出 nodes/edges，附 role / schema 元信息
  const edgeDetails = computed<Map<string, LineageEdgeMeta[]>>(() => {
    const byKey = new Map<string, LineageEdgeMeta[]>()
    ;(edgesRef.value || []).forEach((edge) => {
      if (!edge.source_table || !edge.target_table) return
      const key = edgeKey(edge.source_table, edge.target_table)
      if (!byKey.has(key)) byKey.set(key, [])
      byKey.get(key)!.push(edge)
    })
    return byKey
  })

  const allGraphData = computed<GraphSnapshot>(() => {
    const nodes = new Map<string, GraphNode>()
    const edges: GraphEdge[] = []
    ;(groupsRef.value || []).forEach((group) => {
      const target = group.target_table
      if (!target) return
      nodes.set(target, { id: target, data: { role: 'target', schema: schemaName(target) } })
      ;(group.source_tables || []).forEach((source) => {
        nodes.set(source, { id: source, data: { role: 'source', schema: schemaName(source) } })
        const details = edgeDetails.value.get(edgeKey(source, target)) || []
        edges.push({ id: edgeKey(source, target), source, target, data: { details, dependency: false } })
      })
      ;(group.dependency_tables || []).forEach((source) => {
        nodes.set(source, { id: source, data: { role: 'dependency', schema: schemaName(source) } })
        const details = edgeDetails.value.get(edgeKey(source, target)) || []
        edges.push({ id: edgeKey(source, target), source, target, data: { details, dependency: true } })
      })
    })
    return { nodes: Array.from(nodes.values()), edges }
  })

  const schemas = computed<string[]>(() => unique(allGraphData.value.nodes.map((node) => node.data.schema)))
  const scripts = computed<string[]>(() => unique((edgesRef.value || []).map((edge) => edge.file_name)))
  const edgeTypes = computed<string[]>(() => unique((edgesRef.value || []).map((edge) => edge.edge_type)))
  const confidences = computed<string[]>(() => unique((edgesRef.value || []).map((edge) => edge.confidence)))

  const schemaCounts = computed<Map<string, number>>(() => {
    const map = new Map<string, number>()
    for (const node of allGraphData.value.nodes) {
      const s = node.data.schema
      map.set(s, (map.get(s) || 0) + 1)
    }
    return map
  })
  const scriptCounts = computed<Map<string, number>>(() => {
    const map = new Map<string, number>()
    for (const edge of edgesRef.value || []) {
      if (!edge.file_name) continue
      map.set(edge.file_name, (map.get(edge.file_name) || 0) + 1)
    }
    return map
  })

  const searchMatches = computed<GraphNode[]>(() => {
    const keyword = searchText.value.trim().toLowerCase()
    if (!keyword) return []
    return allGraphData.value.nodes.filter((node) => node.id.toLowerCase().includes(keyword))
  })
  const matchedNodeId = computed<string>(
    () => searchMatches.value[matchIndex.value % Math.max(searchMatches.value.length, 1)]?.id || ''
  )

  const scriptSearch = ref<string>('')
  const filteredScriptList = computed<string[]>(() => {
    const kw = scriptSearch.value.trim().toLowerCase()
    const list = [...scripts.value].sort(
      (a, b) => (scriptCounts.value.get(b) || 0) - (scriptCounts.value.get(a) || 0)
    )
    if (!kw) return list
    return list.filter((f) => f.toLowerCase().includes(kw))
  })

  const largeGraphLimited = computed<boolean>(
    () => allGraphData.value.nodes.length > 300 && !largeGraphExpanded.value && !searchText.value.trim()
  )

  // 第一层筛选：role / schema / edge-type / confidence / script + 隐藏脚本 + 大图截断
  const filteredBase = computed<GraphSnapshot>(() => {
    let nodes = allGraphData.value.nodes
    let edges = allGraphData.value.edges
    if (roleFilter.value !== 'all') nodes = nodes.filter((node) => node.data.role === roleFilter.value)
    if (schemaFilter.value !== 'all') nodes = nodes.filter((node) => node.data.schema === schemaFilter.value)
    const nodeIds = new Set(nodes.map((node) => node.id))
    edges = edges.filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target))
    if (edgeTypeFilter.value !== 'all') {
      edges = edges.filter((edge) => edge.data.details.some((d) => d.edge_type === edgeTypeFilter.value))
    }
    if (confidenceFilter.value !== 'all') {
      edges = edges.filter((edge) => edge.data.details.some((d) => d.confidence === confidenceFilter.value))
    }
    if (scriptFilter.value) {
      edges = edges.filter((edge) => edge.data.details.some((d) => d.file_name === scriptFilter.value))
    }
    if (collapsedScripts.value.size) {
      edges = edges.filter((edge) => !edge.data.details.some((d) => collapsedScripts.value.has(d.file_name || '')))
    }
    const connected = new Set(edges.flatMap((edge) => [edge.source, edge.target]))
    if (
      edgeTypeFilter.value !== 'all' ||
      confidenceFilter.value !== 'all' ||
      scriptFilter.value ||
      collapsedScripts.value.size
    ) {
      nodes = nodes.filter((node) => connected.has(node.id))
    }
    if (largeGraphLimited.value) {
      const visibleIds = new Set(nodes.slice(0, 300).map((node) => node.id))
      nodes = nodes.filter((node) => visibleIds.has(node.id))
      edges = edges.filter((edge) => visibleIds.has(edge.source) && visibleIds.has(edge.target))
    }
    return { nodes, edges }
  })

  // 第二层（仅 G6）：把折叠 schema 的多个表投成一个 combo 节点
  const projectedBase = computed<GraphSnapshot>(() => {
    const base = filteredBase.value
    if (!collapsedSchemas.value.size) return base
    const isCollapsed = (schema: string): boolean => collapsedSchemas.value.has(schema)
    const projectId = (id: string): string => {
      const node = base.nodes.find((n) => n.id === id)
      if (node && isCollapsed(node.data.schema)) return `${COMBO_PREFIX}${node.data.schema}`
      return id
    }
    const remappedNodes = new Map<string, GraphNode>()
    const comboCounts = new Map<string, number>()
    for (const node of base.nodes) {
      if (isCollapsed(node.data.schema)) {
        const id = `${COMBO_PREFIX}${node.data.schema}`
        comboCounts.set(id, (comboCounts.get(id) || 0) + 1)
        if (!remappedNodes.has(id)) {
          remappedNodes.set(id, { id, data: { role: 'combo', schema: node.data.schema, isCombo: true } })
        }
      } else if (!remappedNodes.has(node.id)) {
        remappedNodes.set(node.id, node)
      }
    }
    for (const [id, count] of comboCounts) {
      const n = remappedNodes.get(id)
      if (n) n.data.count = count
    }
    const remappedEdges = new Map<string, GraphEdge>()
    for (const edge of base.edges) {
      const source = projectId(edge.source)
      const target = projectId(edge.target)
      if (source === target) continue
      const key = `${source}|||${target}`
      const existing = remappedEdges.get(key)
      if (existing) {
        existing.data.aggregatedCount = (existing.data.aggregatedCount || 1) + 1
        existing.data.details = existing.data.details.concat(edge.data.details)
        if (edge.data.dependency) existing.data.dependency = true
      } else {
        remappedEdges.set(key, {
          id: key,
          source,
          target,
          data: { ...edge.data, details: [...edge.data.details], aggregatedCount: 1 },
        })
      }
    }
    return { nodes: Array.from(remappedNodes.values()), edges: Array.from(remappedEdges.values()) }
  })

  // adjacency 永远基于 projectedBase（G6），cytoscape 通过 cyAdjacency 拿
  const adjacencyMaps = computed<AdjacencyMaps>(() => buildAdjacency(projectedBase.value))
  const cyAdjacency = computed<AdjacencyMaps>(() => buildAdjacency(filteredBase.value))

  const autoFocalId = computed<string>(() => {
    const nodes = projectedBase.value.nodes
    if (nodes.length <= 30) return ''
    const degree = new Map<string, number>()
    projectedBase.value.edges.forEach((edge) => {
      degree.set(edge.source, (degree.get(edge.source) || 0) + 1)
      degree.set(edge.target, (degree.get(edge.target) || 0) + 1)
    })
    let bestId = nodes[0]?.id || ''
    let bestDeg = -1
    for (const node of nodes) {
      if (node.data.isCombo) continue
      const d = degree.get(node.id) || 0
      if (d > bestDeg) {
        bestDeg = d
        bestId = node.id
      }
    }
    return bestId
  })

  function presentInProjected(id: string): boolean {
    return !!id && projectedBase.value.nodes.some((node) => node.id === id)
  }
  function presentInFiltered(id: string): boolean {
    return !!id && filteredBase.value.nodes.some((node) => node.id === id)
  }

  const effectiveFocalId = computed<string>(() => {
    if (presentInProjected(clickedFocalId.value)) return clickedFocalId.value
    if (matchedNodeId.value && presentInProjected(matchedNodeId.value)) return matchedNodeId.value
    if (focusMode.value !== 'all') return autoFocalId.value
    return ''
  })

  // G6 视图数据：focal+hop 在 projectedBase 上 BFS
  const graphData = computed<GraphSnapshot>(() => {
    const base = projectedBase.value
    const focal = effectiveFocalId.value
    if (!focal || focusMode.value === 'all') {
      return {
        nodes: base.nodes.map((node) => ({
          ...node,
          data: { ...node.data, matched: !!focal && node.id === focal },
        })),
        edges: base.edges,
      }
    }
    const { out, inn } = adjacencyMaps.value
    const visible = new Set([focal])
    if (focusMode.value !== 'downstream') {
      bfsLimited(focal, (n) => inn.get(n), hopDepth.value).forEach((id) => visible.add(id))
    }
    if (focusMode.value !== 'upstream') {
      bfsLimited(focal, (n) => out.get(n), hopDepth.value).forEach((id) => visible.add(id))
    }
    return {
      nodes: base.nodes
        .filter((node) => visible.has(node.id))
        .map((node) => ({ ...node, data: { ...node.data, matched: node.id === focal } })),
      edges: base.edges.filter((edge) => visible.has(edge.source) && visible.has(edge.target)),
    }
  })

  // Cytoscape 视图数据：focal+hop 在 filteredBase 上 BFS（schema 通过 compound parent 表达）
  const cyData = computed<GraphSnapshot>(() => {
    const base = filteredBase.value
    const focal = effectiveFocalId.value
    const focalForCy = focal && presentInFiltered(focal) ? focal : ''
    if (!focalForCy || focusMode.value === 'all') {
      return {
        nodes: base.nodes.map((node) => ({
          ...node,
          data: { ...node.data, matched: !!focalForCy && node.id === focalForCy },
        })),
        edges: base.edges,
      }
    }
    const { out, inn } = cyAdjacency.value
    const visible = new Set([focalForCy])
    if (focusMode.value !== 'downstream') {
      bfsLimited(focalForCy, (n) => inn.get(n), hopDepth.value).forEach((id) => visible.add(id))
    }
    if (focusMode.value !== 'upstream') {
      bfsLimited(focalForCy, (n) => out.get(n), hopDepth.value).forEach((id) => visible.add(id))
    }
    return {
      nodes: base.nodes
        .filter((node) => visible.has(node.id))
        .map((node) => ({ ...node, data: { ...node.data, matched: node.id === focalForCy } })),
      edges: base.edges.filter((edge) => visible.has(edge.source) && visible.has(edge.target)),
    }
  })

  const visibleStats = computed<{ visible: number; total: number }>(() => ({
    visible: graphData.value.nodes.length,
    total: projectedBase.value.nodes.length,
  }))

  const tableRows = computed<TableRow[]>(() => {
    const base = projectedBase.value
    const focal = effectiveFocalId.value
    const findNode = (id: string): GraphNode | undefined => base.nodes.find((node) => node.id === id)
    if (!focal) {
      return base.nodes
        .map((node) => ({
          id: node.id,
          schema: node.data.schema,
          hops: '-' as string | number,
          direction: '全图',
          upstream: base.edges.filter((edge) => edge.target === node.id).length,
          downstream: base.edges.filter((edge) => edge.source === node.id).length,
          isCombo: !!node.data.isCombo,
        }))
        .sort((a, b) => a.id.localeCompare(b.id))
    }
    const { out, inn } = adjacencyMaps.value
    const focalNode = findNode(focal)
    const rows: TableRow[] = [{
      id: focal,
      schema: focalNode?.data.schema || '-',
      hops: 0,
      direction: '聚焦',
      upstream: (inn.get(focal) || []).length,
      downstream: (out.get(focal) || []).length,
      isCombo: !!focalNode?.data.isCombo,
    }]
    const traverse = (getNeighbors: (id: string) => string[] | undefined, label: string): void => {
      const visited = new Set([focal])
      let frontier = [focal]
      let depth = 0
      while (frontier.length && depth < 10) {
        depth += 1
        const next: string[] = []
        for (const id of frontier) {
          for (const neighbor of getNeighbors(id) || []) {
            if (visited.has(neighbor)) continue
            visited.add(neighbor)
            next.push(neighbor)
            const node = findNode(neighbor)
            rows.push({
              id: neighbor,
              schema: node?.data.schema || '-',
              hops: depth,
              direction: label,
              upstream: (inn.get(neighbor) || []).length,
              downstream: (out.get(neighbor) || []).length,
              isCombo: !!node?.data.isCombo,
            })
          }
        }
        frontier = next
      }
    }
    traverse((id) => inn.get(id), '上游')
    traverse((id) => out.get(id), '下游')
    return rows
  })

  const tableSuggested = computed<boolean>(() => projectedBase.value.nodes.length > 100)

  const selectedNodeDetails = computed<SelectedNodeDetails | null>(() => {
    if (!selectedItem.value || selectedItem.value.type !== 'node') return null
    const id = selectedItem.value.id
    return {
      id,
      upstream: allGraphData.value.edges.filter((edge) => edge.target === id).map((edge) => edge.source),
      downstream: allGraphData.value.edges.filter((edge) => edge.source === id).map((edge) => edge.target),
      edges: (edgesRef.value || []).filter((edge) => edge.source_table === id || edge.target_table === id),
    }
  })

  const selectedEdgeDetails = computed<LineageEdgeMeta[]>(() => {
    if (!selectedItem.value || selectedItem.value.type !== 'edge') return []
    return edgeDetails.value.get(selectedItem.value.id) || []
  })

  // ---------- 助手函数 ----------
  function toggleSet(target: Ref<Set<string>>, value: string): void {
    const next = new Set(target.value)
    if (next.has(value)) next.delete(value)
    else next.add(value)
    target.value = next
  }

  function nextMatch(): void {
    if (!searchMatches.value.length) return
    matchIndex.value = (matchIndex.value + 1) % searchMatches.value.length
  }

  function clearCollapsedScripts(): void {
    collapsedScripts.value = new Set()
  }

  // groups/edges 一变就清掉点击聚焦（避免聚焦在已不存在的节点上）
  watch(
    () => [groupsRef.value, edgesRef.value],
    () => {
      clickedFocalId.value = ''
    },
    { deep: true },
  )

  // 搜索文本变化重置 matchIndex
  watch(searchText, () => {
    matchIndex.value = 0
  })

  // 搜索命中的节点如果属于折叠 schema —— 自动展开该 schema
  watch(matchedNodeId, (id) => {
    if (!id || !collapsedSchemas.value.size) return
    const original = allGraphData.value.nodes.find((node) => node.id === id)
    if (original && collapsedSchemas.value.has(original.data.schema)) {
      toggleSet(collapsedSchemas, original.data.schema)
    }
  })

  return {
    // 状态
    searchText, matchIndex, focusMode, hopDepth, clickedFocalId,
    roleFilter, edgeTypeFilter, confidenceFilter, scriptFilter, schemaFilter,
    collapsedSchemas, collapsedScripts, largeGraphExpanded, selectedItem,
    scriptSearch,

    // 派生
    edgeDetails, allGraphData,
    schemas, scripts, edgeTypes, confidences,
    schemaCounts, scriptCounts,
    searchMatches, matchedNodeId,
    largeGraphLimited, filteredScriptList,
    filteredBase, projectedBase,
    adjacencyMaps, cyAdjacency,
    autoFocalId, effectiveFocalId,
    graphData, cyData,
    visibleStats, tableRows, tableSuggested,
    selectedNodeDetails, selectedEdgeDetails,

    // 助手
    toggleSet, nextMatch, clearCollapsedScripts,
    basename, schemaName, edgeKey,
    COMBO_PREFIX,
  }
}

// 给消费者推断返回 shape 的工具类型 —— LineageGraph.vue / Cytoscape.vue 用
export type LineageGraphData = ReturnType<typeof useLineageGraphData>
