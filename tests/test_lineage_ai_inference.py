"""AI 解析失败兜底测试（Phase 7 双轨 A · P1.4）。

覆盖：
- 白名单约束：AI 返回非白名单表名 → filtered_count++ + edges 不包含
- hallucination 拒绝：dml_type / confidence 枚举越界 → 兜底正常值
- 失败降级：provider exception → ai_inference_error warning，主流程不抛
- short-circuit：parse_errors 空 / provider=off / 表名白名单空 → 不调 HTTP
- 字段白名单：AI 返回不在 column_whitelist 的列 → 过滤
- 端到端：模拟 OpenAI 返回有效 JSON → result.ai_inferred.edges 出 N 条
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from app.services.lineage_ai import LineageAIConfig
from app.services.lineage_ai_inference import infer_from_parse_errors


# ─── short-circuit 路径（不调 HTTP）─────────────────────────────────────────────


def test_empty_parse_errors_no_call():
    config = LineageAIConfig(provider="openai", model="x", api_key="k")
    out = infer_from_parse_errors(
        [],
        table_whitelist={"a", "b"},
        column_whitelist=set(),
        dialect="oracle",
        config=config,
    )
    assert out["edges"] == []
    assert out["trigger_count"] == 0


def test_provider_off_skips():
    out = infer_from_parse_errors(
        [{"sql": "INSERT...", "error": "boom"}],
        table_whitelist={"a"},
        column_whitelist=set(),
        dialect="oracle",
        config=LineageAIConfig(provider="off"),
    )
    assert out["edges"] == []
    assert any(w["type"] == "ai_inference_skipped" for w in out["warnings"])


def test_empty_table_whitelist_skips():
    """没识别到任何表 → AI 推断必 hallucinate，跳过。"""
    out = infer_from_parse_errors(
        [{"sql": "EXECUTE IMMEDIATE p_var", "error": "unsupported"}],
        table_whitelist=set(),
        column_whitelist=set(),
        dialect="oracle",
        config=LineageAIConfig(provider="openai", model="m", api_key="k"),
    )
    assert out["edges"] == []
    assert any("hallucinate" in w["message"] for w in out["warnings"])


def test_unsupported_provider_skips():
    """ollama 等不在 inference 兜底支持列表内 → 跳过 + 不抛错。"""
    out = infer_from_parse_errors(
        [{"sql": "INSERT...", "error": "x"}],
        table_whitelist={"a"},
        column_whitelist=set(),
        dialect="oracle",
        config=LineageAIConfig(provider="ollama"),
    )
    assert out["edges"] == []


# ─── 白名单 / 校验过滤 ────────────────────────────────────────────────────────


def _setup_fake_openai(monkeypatch, fake_response_body):
    """让 _post_json 返回伪造的 OpenAI choices/message/content 结构。"""
    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps({
            "choices": [{
                "message": {"content": json.dumps(fake_response_body, ensure_ascii=False)},
                "finish_reason": "stop",
            }]
        }).encode("utf-8")

    def fake_urlopen(req, timeout):
        return _Resp()

    monkeypatch.setattr(
        "app.services.lineage_ai.urllib.request.urlopen",
        fake_urlopen,
    )


def test_filters_non_whitelist_target_table(monkeypatch):
    """AI 返回了不在白名单的 target → 整条边被过滤。"""
    _setup_fake_openai(monkeypatch, {"edges": [
        {"source_table": "ods.real", "target_table": "fake.invented", "dml_type": "INSERT"},
        {"source_table": "ods.real", "target_table": "dwd.also_real", "dml_type": "INSERT"},
    ]})
    config = LineageAIConfig(
        provider="openai", model="m", api_key="k",
        base_url="https://api.x/v1", timeout_seconds=5,
    )
    out = infer_from_parse_errors(
        [{"sql": "INSERT INTO ...", "error": "unsupported"}],
        table_whitelist={"ods.real", "dwd.also_real"},
        column_whitelist=set(),
        dialect="oracle",
        config=config,
    )
    assert len(out["edges"]) == 1
    assert out["edges"][0]["target_table"] == "dwd.also_real"
    assert out["filtered_count"] == 1


def test_filters_invalid_dml_type_fallback_to_insert(monkeypatch):
    """AI 返回 dml_type='ALIEN' → fallback 到 INSERT，不丢边。"""
    _setup_fake_openai(monkeypatch, {"edges": [
        {"source_table": "a", "target_table": "b", "dml_type": "ALIEN"},
    ]})
    out = infer_from_parse_errors(
        [{"sql": "INSERT...", "error": "x"}],
        table_whitelist={"a", "b"},
        column_whitelist=set(),
        dialect="mysql",
        config=LineageAIConfig(provider="openai", model="m", api_key="k", timeout_seconds=5),
    )
    assert len(out["edges"]) == 1
    assert out["edges"][0]["dml_type"] == "INSERT"


def test_filters_invalid_confidence_fallback_to_low(monkeypatch):
    """AI 推断永远不能 confidence=high；强制 low。"""
    _setup_fake_openai(monkeypatch, {"edges": [
        {"source_table": "a", "target_table": "b", "dml_type": "INSERT", "confidence": "high"},
    ]})
    out = infer_from_parse_errors(
        [{"sql": "INSERT...", "error": "x"}],
        table_whitelist={"a", "b"},
        column_whitelist=set(),
        dialect="mysql",
        config=LineageAIConfig(provider="openai", model="m", api_key="k", timeout_seconds=5),
    )
    assert out["edges"][0]["confidence"] == "low"


def test_filters_non_whitelist_source_to_empty(monkeypatch):
    """source 不在白名单 → 保留 target，source 设空（不丢边）。"""
    _setup_fake_openai(monkeypatch, {"edges": [
        {"source_table": "fake_src", "target_table": "ods.t", "dml_type": "INSERT"},
    ]})
    out = infer_from_parse_errors(
        [{"sql": "INSERT...", "error": "x"}],
        table_whitelist={"ods.t"},
        column_whitelist=set(),
        dialect="mysql",
        config=LineageAIConfig(provider="openai", model="m", api_key="k", timeout_seconds=5),
    )
    assert len(out["edges"]) == 1
    assert out["edges"][0]["source_table"] == ""
    assert out["edges"][0]["target_table"] == "ods.t"


def test_column_whitelist_filters(monkeypatch):
    """source_columns 不在 column_whitelist → 过滤；保留命中的。"""
    _setup_fake_openai(monkeypatch, {"edges": [
        {
            "source_table": "ods.t", "target_table": "dwd.t", "dml_type": "INSERT",
            "source_columns": ["ods.t.id", "ods.t.fake_col"],
            "target_columns": ["id", "alien"],
        },
    ]})
    out = infer_from_parse_errors(
        [{"sql": "INSERT...", "error": "x"}],
        table_whitelist={"ods.t", "dwd.t"},
        column_whitelist={"id"},
        dialect="mysql",
        config=LineageAIConfig(provider="openai", model="m", api_key="k", timeout_seconds=5),
    )
    edge = out["edges"][0]
    # ods.t.id 通过（bare = "id" 命中），fake_col 不命中
    assert any("id" in c for c in edge["source_columns"])
    assert "ods.t.fake_col" not in edge["source_columns"]
    assert "id" in edge["target_columns"]
    assert "alien" not in edge["target_columns"]


# ─── 失败降级 ─────────────────────────────────────────────────────────────────


def test_provider_exception_logged_and_skipped(monkeypatch):
    """provider HTTP 调用抛异常 → 该片段记 warning，不抛错。"""
    def boom(req, timeout):
        raise ConnectionError("collector down")
    monkeypatch.setattr("app.services.lineage_ai.urllib.request.urlopen", boom)

    out = infer_from_parse_errors(
        [
            {"sql": "INSERT a", "error": "x"},
            {"sql": "INSERT b", "error": "y"},
        ],
        table_whitelist={"a", "b"},
        column_whitelist=set(),
        dialect="mysql",
        config=LineageAIConfig(provider="openai", model="m", api_key="k", timeout_seconds=2),
    )
    assert out["edges"] == []
    error_warnings = [w for w in out["warnings"] if w["type"] == "ai_inference_error"]
    assert len(error_warnings) == 2  # 两个片段都报错了


def test_non_dict_response_records_warning(monkeypatch):
    """provider 返回 list 而非 dict → 不抛错，记 warning。"""
    class _BadResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self):
            return json.dumps({
                "choices": [{"message": {"content": json.dumps([1, 2, 3])}}]
            }).encode("utf-8")
    monkeypatch.setattr(
        "app.services.lineage_ai.urllib.request.urlopen",
        lambda req, timeout: _BadResp(),
    )

    out = infer_from_parse_errors(
        [{"sql": "INSERT a", "error": "x"}],
        table_whitelist={"a"},
        column_whitelist=set(),
        dialect="mysql",
        config=LineageAIConfig(provider="openai", model="m", api_key="k", timeout_seconds=2),
    )
    # _loads_json_object 拿到 list 会抛 ValueError → 走 ai_inference_error 通路
    error_warnings = [w for w in out["warnings"] if w["type"] == "ai_inference_error"]
    assert error_warnings


def test_max_fragments_limits_calls(monkeypatch):
    """parse_errors=20，max_fragments=3 → 只调 3 次。"""
    call_count = {"n": 0}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self):
            return json.dumps({"choices": [{"message": {"content": "{\"edges\":[]}"}}]}).encode("utf-8")

    def fake_urlopen(req, timeout):
        call_count["n"] += 1
        return _Resp()

    monkeypatch.setattr("app.services.lineage_ai.urllib.request.urlopen", fake_urlopen)

    parse_errors = [{"sql": f"INSERT {i}", "error": "x"} for i in range(20)]
    out = infer_from_parse_errors(
        parse_errors,
        table_whitelist={"a"},
        column_whitelist=set(),
        dialect="mysql",
        config=LineageAIConfig(provider="openai", model="m", api_key="k", timeout_seconds=2),
        max_fragments=3,
    )
    assert call_count["n"] == 3
    assert out["trigger_count"] == 3
    assert any(w["type"] == "ai_inference_truncated" for w in out["warnings"])


# ─── 端到端：成功路径 ────────────────────────────────────────────────────────


def test_end_to_end_inference_success(monkeypatch):
    """模拟一段 PL/SQL 解析失败 → AI 返回 2 条候选 → 1 条命中白名单 → 输出 1 条。"""
    _setup_fake_openai(monkeypatch, {"edges": [
        {
            "source_table": "ods.src",
            "target_table": "dwd.dim",
            "dml_type": "INSERT",
            "source_columns": ["ods.src.id"],
            "target_columns": ["id"],
            "confidence": "low",
            "reason": "EXECUTE IMMEDIATE 拼出 INSERT INTO dwd.dim",
            "evidence": "EXECUTE IMMEDIATE 'INSERT INTO ' || tab_name || ' SELECT id FROM ods.src'",
        },
        # 第二条命中不存在的表，应被过滤
        {"source_table": "ghost", "target_table": "phantom", "dml_type": "INSERT"},
    ]})
    out = infer_from_parse_errors(
        [{"sql": "EXECUTE IMMEDIATE ...", "error": "Expected END after CASE"}],
        table_whitelist={"ods.src", "dwd.dim"},
        column_whitelist={"id"},
        dialect="oracle",
        config=LineageAIConfig(provider="openai", model="kimi-k2.6", api_key="sk", timeout_seconds=5),
    )
    assert len(out["edges"]) == 1
    edge = out["edges"][0]
    assert edge["source_table"] == "ods.src"
    assert edge["target_table"] == "dwd.dim"
    assert edge["confidence"] == "low"
    assert edge["is_ai_inferred"] is True
    assert "EXECUTE IMMEDIATE" in edge["evidence"]
    assert out["filtered_count"] == 1
    assert out["trigger_count"] == 1


# ─── service 层集成：result.ai_inferred 自动挂载 ─────────────────────────────


def test_service_layer_attaches_ai_inferred_when_enabled(monkeypatch):
    """analyze_json 经过 _attach_ai_enrichment → 触发 inference → result["ai_inferred"] 挂上。"""
    from app.services import lineage_service

    # mock _config 返回 enable_inference=True 的配置
    fake_config = LineageAIConfig(
        provider="openai", model="m", api_key="k", base_url="https://api.x/v1",
        timeout_seconds=5, enable_inference=True,
    )
    monkeypatch.setattr("app.services.lineage_ai._config", lambda: fake_config)

    # mock provider HTTP
    _setup_fake_openai(monkeypatch, {"edges": [
        {"source_table": "users", "target_table": "users_archive", "dml_type": "INSERT"},
    ]})

    # 故意送一段会让 sqlglot 报错的 SQL（用 MERGE Oracle 特有句法但不带 dialect）
    # 简单兜底：直接构造一个 result 模拟解析失败
    result = {
        "tables": [{"name": "users"}, {"name": "users_archive"}],
        "columns": [],
        "insert_mappings": [],
        "parse_errors": [{"sql": "MERGE INTO ... whatever_unparseable", "error": "Expected ..."}],
    }
    lineage_service._attach_ai_inference(result, dialect="oracle", enabled=True)
    inferred = result.get("ai_inferred") or {}
    assert len(inferred.get("edges", [])) == 1
    assert inferred["edges"][0]["target_table"] == "users_archive"


# ─── Phase 2: dynamic_sql_segments 推断 ───────────────────────────────────────


from app.services.lineage_ai_inference import infer_from_dynamic_sql_segments


def test_dynamic_sql_high_confidence_skipped(monkeypatch):
    """confidence='high'（EXECUTE IMMEDIATE 字面量）已含完整 SQL，不送 AI。"""
    called = {"n": 0}

    def fake_urlopen(req, timeout):
        called["n"] += 1
        return None

    monkeypatch.setattr("app.services.lineage_ai.urllib.request.urlopen", fake_urlopen)
    out = infer_from_dynamic_sql_segments(
        [
            {"sql": "INSERT INTO t SELECT * FROM s", "source": "execute_keyword", "confidence": "high"},
            {"sql": "INSERT INTO t SELECT 1", "source": "prepare_var", "confidence": "high"},
        ],
        table_whitelist={"t", "s"},
        column_whitelist=set(),
        dialect="oracle",
        config=LineageAIConfig(provider="openai", model="m", api_key="k"),
    )
    assert called["n"] == 0  # 完全没调 HTTP
    assert out["trigger_count"] == 0
    assert out["edges"] == []


def test_dynamic_sql_unresolved_triggers_ai(monkeypatch):
    """confidence='unresolved'（变量没找到赋值）→ 送 AI；AI 用过程上下文给 target。"""
    _setup_fake_openai(monkeypatch, {"edges": [
        {
            "source_table": "",
            "target_table": "ods.t_orders",
            "dml_type": "INSERT",
            "confidence": "low",
            "reason": "过程名 LOAD_ORDERS + preceding_comment 提到 '订单加载'，目标表是 ods.t_orders",
            "evidence": "EXECUTE IMMEDIATE p_var",
        },
    ]})
    out = infer_from_dynamic_sql_segments(
        [
            {
                "sql": "-- unresolved EXECUTE IMMEDIATE variable: p_var",
                "source": "execute_var_unresolved",
                "confidence": "unresolved",
            },
        ],
        table_whitelist={"ods.t_orders", "ods.src"},
        column_whitelist=set(),
        dialect="oracle",
        config=LineageAIConfig(provider="openai", model="m", api_key="k", timeout_seconds=5),
        procedure_segments=[
            {
                "procedure_name": "LOAD_ORDERS",
                "procedure_kind": "PROCEDURE",
                "preceding_comment": "-- 订单加载",
                "line_start": 100,
            },
        ],
    )
    assert len(out["edges"]) == 1
    edge = out["edges"][0]
    assert edge["target_table"] == "ods.t_orders"
    assert edge["source_kind"] == "dynamic_sql"
    assert edge["source_table"] == ""
    assert edge["confidence"] == "low"


def test_dynamic_sql_filters_non_whitelist_target(monkeypatch):
    """AI 返回 target 不在白名单 → 过滤 + 计数，不收入。"""
    _setup_fake_openai(monkeypatch, {"edges": [
        {"source_table": "", "target_table": "phantom", "dml_type": "INSERT"},
        {"source_table": "", "target_table": "ods.real", "dml_type": "INSERT"},
    ]})
    out = infer_from_dynamic_sql_segments(
        [
            {"sql": "-- unresolved p_var", "source": "execute_var_unresolved", "confidence": "unresolved"},
        ],
        table_whitelist={"ods.real"},
        column_whitelist=set(),
        dialect="oracle",
        config=LineageAIConfig(provider="openai", model="m", api_key="k", timeout_seconds=5),
    )
    assert len(out["edges"]) == 1
    assert out["edges"][0]["target_table"] == "ods.real"
    assert out["filtered_count"] == 1


def test_dynamic_sql_low_confidence_var_concat_triggers(monkeypatch):
    """var_concat + low 置信也触发推断（拼接的 SQL 解析不顺）。"""
    _setup_fake_openai(monkeypatch, {"edges": [
        {"source_table": "src", "target_table": "tgt", "dml_type": "INSERT"},
    ]})
    out = infer_from_dynamic_sql_segments(
        [
            {"sql": "INSERT INTO :tab SELECT FROM src", "source": "var_concat", "confidence": "low"},
        ],
        table_whitelist={"src", "tgt"},
        column_whitelist=set(),
        dialect="oracle",
        config=LineageAIConfig(provider="openai", model="m", api_key="k", timeout_seconds=5),
    )
    assert len(out["edges"]) == 1


def test_dynamic_sql_max_fragments(monkeypatch):
    """20 条候选 + max_fragments=2 → 只调 2 次。"""
    call_count = {"n": 0}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self):
            return json.dumps({"choices": [{"message": {"content": "{\"edges\":[]}"}}]}).encode("utf-8")

    def fake_urlopen(req, timeout):
        call_count["n"] += 1
        return _Resp()

    monkeypatch.setattr("app.services.lineage_ai.urllib.request.urlopen", fake_urlopen)

    segments = [
        {"sql": f"-- p_var_{i}", "source": "execute_var_unresolved", "confidence": "unresolved"}
        for i in range(20)
    ]
    out = infer_from_dynamic_sql_segments(
        segments,
        table_whitelist={"a"},
        column_whitelist=set(),
        dialect="oracle",
        config=LineageAIConfig(provider="openai", model="m", api_key="k", timeout_seconds=2),
        max_fragments=2,
    )
    assert call_count["n"] == 2
    assert out["trigger_count"] == 2
    truncated = [w for w in out["warnings"] if w["type"] == "ai_inference_truncated" and w.get("source_kind") == "dynamic_sql"]
    assert truncated


def test_dynamic_sql_provider_exception_per_segment(monkeypatch):
    """provider 抛异常 → 每个 segment 各记一条 warning，不抛错。"""
    def boom(req, timeout):
        raise ConnectionError("nope")
    monkeypatch.setattr("app.services.lineage_ai.urllib.request.urlopen", boom)

    out = infer_from_dynamic_sql_segments(
        [
            {"sql": "-- p_a", "source": "execute_var_unresolved", "confidence": "unresolved"},
            {"sql": "-- p_b", "source": "execute_var_unresolved", "confidence": "unresolved"},
        ],
        table_whitelist={"a"},
        column_whitelist=set(),
        dialect="oracle",
        config=LineageAIConfig(provider="openai", model="m", api_key="k", timeout_seconds=2),
    )
    assert out["edges"] == []
    error_warnings = [w for w in out["warnings"] if w["type"] == "ai_inference_error"]
    assert len(error_warnings) == 2
    assert all(w.get("source_kind") == "dynamic_sql" for w in error_warnings)


def test_service_layer_merges_parse_errors_and_dynamic_sql(monkeypatch):
    """_attach_ai_inference 同时处理 parse_errors + dynamic_sql_segments，edges 合并。"""
    from app.services import lineage_service

    fake_config = LineageAIConfig(
        provider="openai", model="m", api_key="k", base_url="https://api.x/v1",
        timeout_seconds=5, enable_inference=True,
    )
    monkeypatch.setattr("app.services.lineage_ai._config", lambda: fake_config)

    responses = [
        {"edges": [{"source_table": "src", "target_table": "tgt", "dml_type": "INSERT"}]},
        {"edges": [{"source_table": "", "target_table": "tgt2", "dml_type": "INSERT"}]},
    ]
    response_iter = iter(responses)

    class _Resp:
        def __init__(self, body): self._body = body
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self):
            return json.dumps({
                "choices": [{"message": {"content": json.dumps(self._body, ensure_ascii=False)}}]
            }).encode("utf-8")

    def fake_urlopen(req, timeout):
        return _Resp(next(response_iter))

    monkeypatch.setattr("app.services.lineage_ai.urllib.request.urlopen", fake_urlopen)

    result = {
        "tables": [{"name": "src"}, {"name": "tgt"}, {"name": "tgt2"}],
        "columns": [],
        "insert_mappings": [],
        "parse_errors": [{"sql": "MERGE BAD", "error": "Expected END"}],
        "dynamic_sql_segments": [{
            "sql": "-- unresolved p_t",
            "source": "execute_var_unresolved",
            "confidence": "unresolved",
        }],
        "procedure_segments": [],
    }
    lineage_service._attach_ai_inference(result, dialect="oracle", enabled=True)
    inferred = result["ai_inferred"]
    assert len(inferred["edges"]) == 2
    source_kinds = {e["source_kind"] for e in inferred["edges"]}
    assert source_kinds == {"parse_error", "dynamic_sql"}
    assert inferred["trigger_count"] == 2
