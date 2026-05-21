# Compare 流式写出（切片 F 设计 + minimal slice）

> 本文档跟 `docs/COMPARE_RESULT_STORAGE.md` 配套。COMPARE_RESULT_STORAGE
> 设计的目标是「单 run 结果总量支持到 10⁷ 行 / 桶 + runner 内存 ≈
> O(chunk_size)」。切片 B/C/D/E 把"目录化 + 桶分页 + 异步导出"全落地了；
> 但 runner 仍然在内存里攒齐 4 桶才喂 writer，这条最后的瓶颈留给本切片。

---

## 1. 当前非流式的 4 个点

切片 B 落地后，跟"千万级 same 桶不爆内存"的目标之间还有这 4 个未流式化点：

| # | 位置 | 现状 | 内存峰值 |
|---|------|------|----------|
| 1 | `services/runner.py::run_task` | `buckets = compare_rows(...)` 返回完整 `CompareBuckets` dict | O(total_rows × avg_row_size) |
| 2 | `compare/result_writer.py::ParquetResultWriter` | `write_bucket_row` 把行 append 到 `self._buckets[name]` list；`finalize()` 时整桶 `pq.write_table(rows)` 一次写 | O(total_rows × avg_row_size) —— 跟 #1 重复持有 |
| 3 | `services/runner.py::run_task` 收尾 | `samples = {name: rows[:20] for name, rows in buckets.items()}` 仍然依赖完整 buckets dict | O(total_rows) 来自 #1 |
| 4 | `services/excel_export.py::write_excel` | 已经有 `max_rows` 兜底 + parquet 端 `iter_batches` 按需读，但 openpyxl 仍是普通模式（非 `write_only`），整 workbook 在内存攒齐才落盘 | O(min(total_rows, max_rows) × col_width) |

**stream_compare 模式**（`limits.stream_compare=True`）只解决了**对比阶段**
的内存（按主键有序流式归并），**结果写出**还是先攒 4 桶 list。所以
`compare_sorted_row_iterators` 也是返回 `CompareBuckets` dict，跟
`compare_rows` 同形态、同问题。

---

## 2. 渐进式实施（切片 F 本身再拆 4 个 step）

按"破坏面 vs 收益"排序，最小有意义的切片是 **F.1 + F.2 + F.3**（本 PR）；
F.4 是后续单独 PR：

| Step | 内容 | 解决 #1-4 哪个 | 本 PR |
|------|------|----------------|-------|
| **F.1** | `compare/engine.compare_rows_streaming(...)` 生成器，yield `("bucket", row)` 事件 | ✅ 为 #1 解锁——caller 不再被迫拿完整 dict | ✅ |
| **F.2** | `ParquetResultWriter` batch flush：每 N 行往 parquet row group append 一次 | ✅ #2 —— writer 内存 = O(batch_size)，跟总行数无关 | ✅ |
| **F.3** | `runner.run_task` 在 `result_format="parquet"` 时走 streaming 路径：events → writer.feed → 收尾用 writer.samples，**不再持 buckets dict** | ✅ #1 + #3 收尾 —— runner 内存上限 = O(batch_size × bucket 数) | ✅ |
| **F.4** | `services/excel_export.write_excel` 切 openpyxl `write_only=True` + 按 row 流式 append | ✅ #4 —— Excel 导出内存上限 ≈ O(max_rows × col_width) 进一步降到 O(batch × col_width) | ✅ |

**JsonResultWriter 不动**：legacy json 格式天然需要把所有行序列化进同一个
`<run_id>.json` 文件的 `buckets` 字段，不可能流式。runner 在 `result_format=json`
路径下继续走 `compare_rows` 老接口 + `feed_buckets` —— 行为零回归。

---

## 3. 设计决策

### 3.1 `compare_rows_streaming` 跟 `compare_rows` 关系

不重写分类逻辑，让 `compare_rows` 变成 streaming 的薄包装：

```python
def compare_rows_streaming(
    source_rows, target_rows, key_columns, rules=None,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """yield ('bucket_name', row)；分类逻辑跟 compare_rows 等价。"""
    ...

def compare_rows(source_rows, target_rows, key_columns, rules=None) -> CompareBuckets:
    """老 API 不动；内部走 streaming 把事件归到 4 个 list。"""
    out: CompareBuckets = {"only_source": [], "only_target": [], "diff": [], "same": []}
    for bucket, row in compare_rows_streaming(source_rows, target_rows, key_columns, rules):
        out[bucket].append(row)
    return out
```

这样所有老调用方（含测试 + inline preview）行为完全不变 —— `compare_rows`
仍然返回 dict，只是实现下沉到了 streaming。

> **不是真随机访问的限制**：streaming 仍要先建 `source_index` / `target_index`
> 两个 dict（用于查 key）。如果两边都是巨大表，那两个 dict 本身还是 O(N)
> 内存。**真正的 O(batch) 内存上限**要靠 `stream_compare=True` + 有序流式
> 归并（`compare_sorted_row_iterators`），它本身也需要一份 streaming 版本。
> 本切片只解决"对比完后到落盘前"的 buffering 问题，不替代 `stream_compare`。

### 3.2 ParquetResultWriter batch flush

引入 `batch_size` 参数（默认 5000）。`write_bucket_row` 把行 append 到桶
内 buffer；buffer 满 batch_size 时调 `_flush_bucket(name)` 写一个 row group
（通过 `pyarrow.parquet.ParquetWriter.write_table(table)`）。`finalize()`
把所有剩余 buffer flush 完 + 关闭 writer。

关键约束：
- **schema 锁定**：第一个 batch 用 `pa.Table.from_pylist` 推 schema，后续
  batch 强制按这个 schema build。如果后续行 shape 跟首批不一致，flush 时
  pyarrow 会抛错 —— 我们捕获改成显式 `ValueError("row schema drift in ...")`。
- **空桶不开 ParquetWriter**：第一行才 lazy open + 用首行推 schema。
  finalize 时如果某桶完全没行，跟之前一样 `path=null` + `rows=0`。
- **same 桶 count_only 行为不变**：persist_same_bucket=False 时 same 不进
  buffer，只累 count + 头 N 行 sample。
- **空桶后期 flush**：如果只来了 1-2 行，flush 时一个小 row group 也写，
  parquet 文件存在；reader 能读 + bucket count 跟 meta 一致。
- **跟非 streaming 路径兼容**：runner json 路径继续 `feed_buckets()` 全量
  灌进来 + `finalize()`，行为不变 —— 只是 buffer 一次性涨到全量后立即 flush。

### 3.3 runner 样本采集

切片 B 起 `CompareResult.samples` 是 `{name: list[row]}` 前 20 行 / 桶。
streaming 模式下没法在末尾一次性切 [:20]。让 runner 在 events 循环里维护
一个 `samples_buffer: dict[str, list]`，每个桶满 20 行就停止 append（写入
继续走 writer）。结尾 `CompareResult.samples = samples_buffer`，行为外部
等价。

### 3.4 不动的边界

- `compare_sorted_row_iterators`（stream_compare 模式）暂不改 —— 它本来就
  是 streaming friendly 的归并逻辑，但 return 形态还是 dict。后续如果发现
  stream_compare + parquet 联用是常见场景，再让它也吐 events。本切片不做。
- `JsonResultWriter` 完全不动。
- 前端不动 —— writer 协议不变（仍是 `write_bucket_row` + `finalize`），
  manifest 形态不变，meta.json schema 不变。

---

## 4. tradeoff

- **多写小 row group 的开销**：5000 行一个 row group，对千万级桶等于 2000
  个 row group，parquet 文件元数据稍微胖一点。reader 端 `iter_batches` 仍
  能高效跳过。如果实测发现 row group 太碎影响 read 性能，调大默认 batch_size
  到 10_000-20_000。
- **schema 推断锁定**：第一个 batch 推 schema 意味着第一行的字段决定所有
  后续行的 schema。如果对比的两个 query 字段顺序不稳定（极少），可能漏字段。
  现行 `compare_rows` 也是同一假设（所有行 dict shape 相同），所以不是新
  问题；显式 ValueError 让用户看到 drift 而不是静默成 NaN。
- **stream_compare + streaming writer 联用**：这是真正"内存 O(batch_size)"
  的组合，但需要 `compare_sorted_row_iterators` 也吐 events —— 本切片不做，
  留单独 PR。当前 stream_compare + parquet 联用走 fallback：dict → events
  转换 wrapper，跟非 stream 路径同样内存峰值。

---

## 5. 不属本切片

- F.4 Excel `write_only` 流式写出 ✅（已实现，下方 §7 描述）
- `compare_sorted_row_iterators` events 化
- 写入压缩切到 zstd（行 > 100MB 触发）—— 设计文档 §5 说的，留 perf 调优
- writer.samples 暴露到 manifest 让 runner 不自己维护 buffer
- DuckDB 联查桶（切片 F+ 已经独立到 G）

---

## 7. F.4 落地（Excel `write_only` 流式写出）

### 实现位置
- `app/services/run_result.iter_bucket_rows(run_id, bucket, *, max_rows=None)`：
  行级 generator，基于 pyarrow `ParquetFile.iter_batches`，到 `max_rows` 即
  break 让剩余 batch 不解码。legacy json / count_only sample 走兜底路径。
- `app/services/exporter.write_excel_streaming(path, *, bucket_iter_factory,
  bucket_columns, max_rows)`：`openpyxl.Workbook(write_only=True)`，4 个
  per-bucket sheet + 汇总对照 sheet，行级 `ws.append([WriteOnlyCell, ...])`。
- `app/services/excel_export.build_excel_for_run` parquet 路径切到 streaming：
  `_collect_bucket_columns_from_meta` 通过 pyarrow `ParquetFile.schema_arrow`
  抽 source / target struct 字段名（不解码 row group 数据），传给 writer
  当 header layout 用。

### Excel 输出的行为变化（write_only 必要妥协）
1. **汇总对照 sheet 没有 merged top headers**：write_only 不支持 `merge_cells`。
   改成单 header 行 `["源.col1", "源.col2", ..., "目.col1", ..., "是否存在",
   "差异字段"]`，仍含分桶填色（diff 黄 / only_source 红 / only_target 蓝 /
   same 白）。
2. **per-bucket sheet 字段顺序非 dict 插入序**：pyarrow struct 字段顺序由
   ParquetResultWriter 首批 batch 写入时锁定，稳定但跟老 json 路径的 dict
   key 顺序可能不一致。
3. **不再调 `auto_filter` / `freeze_panes`**：write_only 模式可设但本切片
   暂未启用，避免跟流式 append 顺序冲突。

### 影响范围
- runner 同步落 Excel（`result_format=json` 路径）：完全不变 —— runner 仍
  在 `JsonResultWriter.finalize` 里调老 `write_excel`。
- 异步导出端点 `POST /api/runs/<id>/export-excel`：parquet runs 自动走
  streaming，legacy json runs 仍走老 `write_excel`。
- 测试：legacy 路径回归全过；parquet streaming 路径单独覆盖 5 个新用例
  （round-trip / max_rows 跨桶 / schema 推断 / build_excel 真走 streaming /
  P1 max_rows 兜底跟 streaming 联用不失效）。

---

## 6. 测试契约

`tests/test_compare_engine.py` 加：
- `compare_rows_streaming` 跟 `compare_rows` 在多 fixture 下事件等价
- 流式产出顺序：only_source / diff / same 跟 source_index 同序，only_target
  跟 target_index 同序（跟现行 compare_rows 同语义）

`tests/test_result_writer.py` 加：
- batch_size=3 + 10 行 only_source 跨 4 个 row group flush，meta.json
  rows=10，parquet 文件 row_group_count >= 3
- finalize 不调 write_bucket_row 时（empty bucket）不开 ParquetWriter
- schema drift 行（第二批多 / 少字段）抛 ValueError
