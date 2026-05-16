# 大数据比对结果落盘方案（设计）

> **本文档只描述设计，不动当前 compare engine 的实现。** 实施切点在 `docs/ROADMAP.md` P1 列出，按切片推进。

---

## 1. 问题

当前 `app/services/runner.py` / `app/compare/engine.py` 的产出形态：

- `compare_rows()` 返回 `{only_source, only_target, diff, same}` 四个 list，**全部行在内存**。
- 结果一次性序列化成 **一个 JSON 文件** 落到 `results/<run_id>.json`。
- 同步导出一份 **完整 Excel** 到 `results/<run_id>.xlsx`。
- 前端 HistoryView 拉 `/results/<file>` 直接全量解析渲染（带 sampling 上限 `RunLimits.export_max_rows`）。

这一套在 ≤ 5 万行 / 桶时没问题，到 50 万行 / 桶就有 4 个具体痛点：

| 痛点 | 触发条件 | 现象 |
|------|---------|-----|
| **内存峰值** | 200 万行 `same` 桶 + 详细字段 | runner 内存 > 4 GB，容器 OOM |
| **JSON 文件巨大** | 单 run > 500 MB | 浏览器 fetch 卡死、`results/` 目录把宿主盘塞满 |
| **Excel 写出慢** | openpyxl 一次写 100 万行 | 单 run 写 Excel 耗时 > 5 min，期间占满 worker thread |
| **二次读不友好** | UI 想分页看 `diff` 桶 | 必须把整 JSON 拉回前端 + JS 切片 |

stream_compare 模式只解决了**对比阶段**的内存，没解决**结果写出 / 读回**的内存。

---

## 2. 目标

> 设计阶段不动 engine，**先把"结果存储"和"engine 计算"解耦**。落地后效果：

1. **runner 内存上限** ≈ O(chunk_size)，跟结果总量无关
2. **单 run 结果总量** 支持到 10⁷（千万级）行 / 桶
3. **二次读分页** 支持随机访问任意 offset + limit
4. **Excel 导出** 可选 / 可异步 / 可只导差异 + 抽样
5. **磁盘占用** 比当前 JSON 小 5×~10×（列存 + 压缩）

---

## 3. 设计原则

- **结果存储跟 compare engine 解耦**：engine 只产 row stream（generator），不知道下游怎么落盘；落盘走独立的 `ResultWriter` 抽象。
- **不动 engine API**：`compare_rows` 跟 `compare_sorted_row_iterators` 现签名保持，新增 `compare_rows_streaming(... writer)` 入口给大场景用。
- **桶分文件**：`only_source / only_target / diff / same` 四个桶各自落独立文件；UI 默认 lazy 加载 + skip `same` 桶。
- **格式分级**：小结果（≤ 50K 行）维持 JSON 不动；大结果走 Parquet。
- **元数据单独存**：`results/<run_id>/meta.json` 只放 summary + 文件清单，UI 拉这个就能渲染 HistoryView 头部，不用拉行数据。
- **Excel 导出按需**：默认不导，UI 显式点「导出 Excel」才异步生成；只导差异 + 抽样 same（默认 sample 1000 行）。
- **向后兼容**：旧 `results/<run_id>.json` 单文件格式仍能读，新 run 走目录格式 `results/<run_id>/`。读侧检测 `os.path.isdir` 派发。

---

## 4. 目录布局

### 现状
```
results/
  <run_id>.json           # 全量结果（含 4 桶 rows）
  <run_id>.xlsx           # 全量 Excel
```

### 新布局
```
results/
  <run_id>/
    meta.json             # summary + 文件清单 + schema + run params
    only_source.parquet   # 仅源端有的行
    only_target.parquet   # 仅目标端有的行
    diff.parquet          # 两端都有但字段有差的行（含 changes 列）
    same.parquet          # 两端完全一致的行（可选；默认仍写，但 UI 默认不拉）
    sample.json           # 每桶头 100 行抽样，给 UI 首屏秒开
    export.xlsx           # 按需生成的 Excel（不存在 = 未导出）
```

`meta.json` 形态：
```json
{
  "run_id": "...",
  "task_id": "...",
  "task_name": "...",
  "started_at": "...",
  "elapsed_seconds": 12.34,
  "source_rows": 1234567,
  "target_rows": 1234600,
  "summary": {
    "only_source": 12,
    "only_target": 45,
    "diff": 678,
    "same": 1233832
  },
  "buckets": [
    {"name": "only_source", "path": "only_source.parquet", "rows": 12,      "bytes": 4096},
    {"name": "only_target", "path": "only_target.parquet", "rows": 45,      "bytes": 6144},
    {"name": "diff",        "path": "diff.parquet",        "rows": 678,     "bytes": 102400},
    {"name": "same",        "path": "same.parquet",        "rows": 1233832, "bytes": 84000000}
  ],
  "schema": {
    "key_columns": ["id"],
    "source_columns": ["id", "name", "amount"],
    "target_columns": ["id", "name", "amount"]
  },
  "format_version": 2,
  "engine_version": "..."
}
```

---

## 5. 为什么用 Parquet（不是 CSV / JSONL / SQLite）

| 候选 | 优势 | 劣势 | 结论 |
|------|------|------|------|
| **Parquet** ✅ | 列存 + 压缩；row group 支持 random access；pyarrow 已是依赖 | 不能 append-only 流式写（要 batch flush） | **选** |
| CSV | 最简单，append 友好 | 大文件读慢；类型丢失；压缩需额外手脚 | ✗ |
| JSONL | append 友好 | 体积大；二次读分页要建 index；类型丢失轻 | ✗ |
| SQLite | 真正能 OFFSET LIMIT，事务安全 | 增加运行时复杂度；并发写有锁；schema 演化痛 | ✗ |
| 现行 JSON | 没变化 | 全部痛点不变 | ✗ |

pyarrow 已经在 `requirements.txt` 里（`ParquetReader` 用），新加格式零额外依赖。

### Parquet 写入策略
- **batch flush**：engine 产 row 走 generator，writer 每 `batch_size=10_000` 行 flush 一次到 row group，控制内存。
- **schema 推断**：第一 batch 时 pyarrow 自动推；后续 batch 走相同 schema。`diff` 桶的 `changes` 字段是 nested struct，pyarrow 原生支持。
- **压缩**：默认 `snappy`（速度 + 压缩比平衡）；大结果 (`> 100MB`) 走 `zstd`。
- **stats**：写时启用 column statistics，读时分页可按 row group 跳过。

---

## 6. ResultWriter 抽象

新建 `app/compare/result_writer.py`（不动 engine）：

```python
class ResultWriter(Protocol):
    def write_bucket_row(self, bucket: str, row: dict[str, Any]) -> None: ...
    def finalize(self) -> ResultManifest: ...

class JsonResultWriter:
    """旧路径，整 dict 一次性落盘。结果 <=50K 行用，跟现行行为完全一致。"""

class ParquetResultWriter:
    """新路径，按桶 + batch flush 到 4 个 parquet。"""
    def __init__(self, run_dir: Path, batch_size: int = 10_000): ...
```

`runner.run_task` 根据 `RunLimits.max_rows` 选 writer：

```python
writer = ParquetResultWriter(run_dir) if estimated_total_rows > 50_000 else JsonResultWriter(run_dir)
```

engine 接 writer 而不直接 return 4 个 list：

```python
def compare_rows_streaming(
    source_rows: Iterable[dict],
    target_rows: Iterable[dict],
    key_columns: list[str],
    rules: CompareRules,
    writer: ResultWriter,
) -> CompareSummary:
    for bucket, row in _classify(source_rows, target_rows, key_columns, rules):
        writer.write_bucket_row(bucket, row)
    return writer.summary()
```

> **engine 现 API 不动**：`compare_rows` 维持 `dict[str, list]` 返回，给老调用方（含 inline preview）；新入口 `compare_rows_streaming` 给 runner 用。

---

## 7. 读侧 / API

新增 endpoint：

| Endpoint | 用途 |
|----------|------|
| `GET /api/runs/<run_id>/meta` | 返回 `meta.json`（HistoryView 首屏） |
| `GET /api/runs/<run_id>/bucket/<name>?offset=&limit=` | 分页读某桶 |
| `GET /api/runs/<run_id>/bucket/<name>/columns` | 拿桶字段 schema |
| `POST /api/runs/<run_id>/export-excel` | 异步生成 Excel（jobs.py 接管），返回 job_id |

旧 endpoint 保留：

- `GET /results/<file>` 单文件直读，向后兼容（旧 run 的 `.json` 还能下载）
- 新 run 也支持 `GET /results/<run_id>/<file>` 直读 parquet（给高级用户用 pandas/duckdb 离线分析）

读侧分页用 pyarrow `parquet.ParquetFile.iter_batches(batch_size)`：

```python
def read_bucket(run_dir: Path, bucket: str, offset: int, limit: int) -> list[dict]:
    pq = parquet.ParquetFile(run_dir / f"{bucket}.parquet")
    skipped = 0
    out: list[dict] = []
    for batch in pq.iter_batches(batch_size=min(limit, 5000)):
        rows = batch.to_pylist()
        if skipped + len(rows) <= offset:
            skipped += len(rows)
            continue
        rows = rows[max(0, offset - skipped):]
        out.extend(rows[: limit - len(out)])
        skipped = offset
        if len(out) >= limit:
            break
    return out
```

> ⚠️ 这个简单实现按 row group 顺序扫描；row group 内部不能跳过，所以 offset 很大时仍然要扫前面的 row group。优化方向是按 row group **统计跳过**（pyarrow `read_row_group(i)`），按 row group 行数累计判断是否要读。当前 batch_size 10000 + 默认 chunk 50000 看下来够用，不预先优化。

---

## 8. Excel 导出策略

旧：runner 同步写 Excel，大结果 OOM + 慢。

新：

1. **默认不导**：写结果时只落 parquet。
2. **UI 点「导出 Excel」**：调 `POST /api/runs/<run_id>/export-excel`，返回 `job_id`。
3. **后台 job** 走 `services/jobs.py` ThreadPoolExecutor：从 parquet 流式读 → openpyxl write_only 模式 → 写完后落 `results/<run_id>/export.xlsx`。
4. **导出限制**：复用 `RunLimits.export_max_rows`，超过用 sampling（diff 全保 + same 抽样 1000 + only_source/only_target 全保到 export_max_rows 上限）。
5. **完成通知**：job 完成走 `services/notifier.py`（如果用户配了 webhook / 企微）。

---

## 9. 向后兼容 / 迁移

### 读侧 detect
```python
def load_run_result(run_id: str):
    run_dir = RESULTS_DIR / run_id
    if run_dir.is_dir() and (run_dir / "meta.json").exists():
        return _load_new_format(run_dir)
    legacy_file = RESULTS_DIR / f"{run_id}.json"
    if legacy_file.exists():
        return _load_legacy_format(legacy_file)
    raise FileNotFoundError(run_id)
```

### 写侧 gate
- 默认 **新 run 都走新格式**（小结果用 JsonResultWriter 但落到目录里 `results/<run_id>/meta.json`，方便统一 detect）
- 环境变量 `DATAOPS_RESULT_FORMAT=legacy` 回退老格式（应急逃生）

### 历史数据
**不主动迁移**。老 `.json` 文件保留原样，新 run 走新目录，时间长了老 run 自然过期被清。

### gitignore
`results/` 整目录已 ignore，无需改。

---

## 10. 影响面 / 风险

| 影响 | 风险等级 | 缓解 |
|------|---------|------|
| `runner.run_task` 行号增加 | 低 | 抽 ResultWriter 反而拆短 |
| 老 run 兼容 | 中 | 写 `_load_legacy_format` 兜底；保留 30 个 commit 跟踪 fallback |
| Excel 导出异步化 | 中 | UI 要改：从「点完就下载」改成「排队 + 通知」；提供同步降级（小结果直接同步） |
| pyarrow batch flush 写入 | 中 | 单测覆盖 batch boundary / schema 推断 / nested struct |
| 前端分页 | 中 | HistoryView 改 lazy loading，不是大改 |
| 磁盘空间（同时存 4 个 parquet） | 低 | parquet + snappy 比 JSON 小 5-10× |

---

## 11. 切片实施（实施时按这个顺序，不是本轮）

1. **切片 A**：`ResultWriter` 协议 + `JsonResultWriter` 等价实现 + runner 切换走 writer。保持单文件 JSON 输出（行为完全不变）。
2. **切片 B**：`ParquetResultWriter` 实现 + `meta.json` 落盘 + runner 按大小切 writer。读侧仍走老 JSON 路径（写新读老用 fallback `_load_legacy_format` 不可能，所以 B 必须连带 D 一起）。
3. **切片 C**：读侧 `load_run_result` detect 新老格式 + `_load_new_format` 实现 + bucket 分页 endpoint。
4. **切片 D**：HistoryView 改用 `/api/runs/<id>/meta` + lazy bucket 分页（首屏不拉行）。
5. **切片 E**：Excel 导出异步化 + `POST /export-excel` + jobs 接管。
6. **切片 F（可选）**：DuckDB 联查桶（高级用户在 UI 写 SQL 查结果，复用 parquet）。

每个切片独立可上线，**B + C + D 必须捆绑一次发布**（否则新 run 用旧 UI 读不出来）。

---

## 12. 不要做的

- **不上 SQLite**：增加依赖 + schema 演化痛 + 并发锁，对当前规模没收益。
- **不做分布式**：单机能撑到 10⁷ 行 / 桶，超过这个规模不是本仓库定位（见 `docs/ROADMAP.md`）。
- **不写 result migration 脚本**：让老 run 自然过期，主动迁移给当前规模不划算。
- **不上传 parquet 到对象存储**：所有结果都本地盘，跟现行架构一致；对象存储以后接 OpenLineage 时再单独设计。
- **不在 parquet 文件里塞 changes 之外的 metadata**：metadata 走 `meta.json`，parquet 只装行数据，单一职责。

---

## 13. 跟相关模块的关系

- `app/compare/engine.py` —— 加 `compare_rows_streaming` 入口，**老 API 不动**
- `app/services/runner.py` —— 主要改造点：选 writer + 调 streaming 入口
- `app/services/history.py` —— `load_run_result` 加 detect 新老格式
- `app/services/jobs.py` —— Excel 导出走异步 job
- `app/api/runs.py` —— 新 endpoint `/meta` + `/bucket/<n>` + `/export-excel`
- `app/api/system.py` —— `/results/<run_id>/<file>` 路径支持目录形式（增加目录 traversal 防御）
- `frontend/.../HistoryView.vue` —— meta 拉头部 + 桶 lazy load + Excel 导出按钮换成异步排队 UI

---

设计先冻结于此。落地时如发现 ADR 跟实际冲突，**优先回来更新本文档**，不要在文档外悄悄改实现。
