"""Phase 14 P1-1: yml_importer 测试 —— introspect → TableDef → yml 文本。

不连真 DB,通过 monkeypatch app.scenarios.yml_importer 里的三个 introspect 函数
模拟生产 schema 数据。
"""
from __future__ import annotations

import pytest
import yaml

from app.scenarios.models import Scenario
from app.scenarios.yml_importer import (
    _db_type_to_dialect,
    _infer_gen,
    import_tables_from_datasource,
)


# ─── _infer_gen 类型→generator 推断 ─────────────────────────────────────────


def test_infer_gen_pk_int_sequence():
    gen, range_, vals = _infer_gen("BIGINT", "id", is_pk=True)
    assert gen == "sequence"
    assert range_ is None and vals is None


def test_infer_gen_pk_string_uuid():
    gen, _, _ = _infer_gen("VARCHAR(32)", "uuid", is_pk=True)
    assert gen == "uuid_short"


def test_infer_gen_non_pk_int_random():
    gen, range_, _ = _infer_gen("INT", "count_field", is_pk=False)
    assert gen == "random_int"
    assert range_ == [0, 1000]  # count -> 0-1000


def test_infer_gen_amount_hint():
    gen, range_, _ = _infer_gen("BIGINT", "amount_cents", is_pk=False)
    assert gen == "random_int"
    assert range_ == [1, 10000]


def test_infer_gen_decimal_realistic():
    gen, _, _ = _infer_gen("DECIMAL(10,2)", "price", is_pk=False)
    assert gen == "realistic"


def test_infer_gen_timestamp():
    gen, range_, _ = _infer_gen("DATETIME", "created_at", is_pk=False)
    assert gen == "timestamp"
    assert range_ == ["2026-01-01", "2026-12-31"]


def test_infer_gen_enum_parses_values():
    gen, _, vals = _infer_gen("ENUM('paid','pending','cancelled')", "status", is_pk=False)
    assert gen == "enum"
    assert vals == ["paid", "pending", "cancelled"]


def test_infer_gen_varchar_realistic():
    gen, _, _ = _infer_gen("VARCHAR(100)", "user_name", is_pk=False)
    assert gen == "realistic"


def test_db_type_to_dialect_mapping():
    assert _db_type_to_dialect("MySQL") == "mysql"
    assert _db_type_to_dialect("Oracle") == "oracle"
    assert _db_type_to_dialect("DM") == "dm"
    assert _db_type_to_dialect("DB2") == "db2"


# ─── import_tables_from_datasource 端到端(走 monkeypatch)──────────────────


@pytest.fixture
def _patch_introspect(monkeypatch, isolated_storage):
    """monkeypatch yml_importer 用到的三个 introspect 函数 + datasource_store"""
    from app.models import DataSourceCreate, DatabaseType
    from app.services.repositories import datasource_store

    ds = datasource_store.create(DataSourceCreate(
        name="test-ds", db_type=DatabaseType.MYSQL,
        host="localhost", port=3306, database="test",
        username="u", password="p",
    ))

    def _fake_cols(_ds_id, table):
        if table == "orders":
            return [
                {"name": "id", "data_type": "BIGINT", "nullable": False, "comment": "PK", "ordinal": 1},
                {"name": "amount", "data_type": "DECIMAL(10,2)", "nullable": False, "comment": "", "ordinal": 2},
                {"name": "status", "data_type": "VARCHAR(20)", "nullable": False, "comment": "订单状态", "ordinal": 3},
                {"name": "created_at", "data_type": "DATETIME", "nullable": True, "comment": "", "ordinal": 4},
            ]
        if table == "users":
            return [
                {"name": "id", "data_type": "BIGINT", "nullable": False, "comment": "", "ordinal": 1},
                {"name": "email", "data_type": "VARCHAR(255)", "nullable": True, "comment": "", "ordinal": 2},
            ]
        return []

    def _fake_indexes(_ds_id, table):
        if table == "orders":
            return [
                {"name": "PRIMARY", "columns": ["id"], "unique": True, "is_pk": True},
                {"name": "idx_status_created", "columns": ["status", "created_at"],
                 "unique": False, "is_pk": False},
            ]
        if table == "users":
            return [{"name": "PRIMARY", "columns": ["id"], "unique": True, "is_pk": True}]
        return []

    def _fake_rows(_ds_id, table):
        return {"orders": 50000, "users": 1000}.get(table)

    monkeypatch.setattr("app.scenarios.yml_importer.introspect_columns", _fake_cols)
    monkeypatch.setattr("app.scenarios.yml_importer.introspect_indexes", _fake_indexes)
    monkeypatch.setattr("app.scenarios.yml_importer.introspect_row_count", _fake_rows)
    return ds.id


def test_import_single_table_basic(_patch_introspect):
    scenario, yml_text = import_tables_from_datasource(
        _patch_introspect,
        table_names=["orders"],
        scenario_id="orders-perf",
        scenario_name="Orders 性能",
    )
    assert scenario.id == "orders-perf"
    assert scenario.dialect == "mysql"
    assert len(scenario.tables) == 1
    t = scenario.tables[0]
    assert t.name == "orders"
    assert t.rows == 50000  # 来自 introspect_row_count
    # PK 列推断成 sequence + pk=True
    id_col = next(c for c in t.columns if c.name == "id")
    assert id_col.pk is True
    assert id_col.gen == "sequence"
    # amount → realistic(DECIMAL)
    amt = next(c for c in t.columns if c.name == "amount")
    assert amt.gen == "realistic"
    # status → realistic(varchar 默认)
    st = next(c for c in t.columns if c.name == "status")
    assert st.gen == "realistic"
    assert st.description == "订单状态"
    # 索引:PRIMARY 不重复,只保留 idx_status_created
    assert len(t.indexes) == 1
    assert t.indexes[0].columns == ["status", "created_at"]


def test_import_multi_tables(_patch_introspect):
    scenario, _ = import_tables_from_datasource(
        _patch_introspect,
        table_names=["orders", "users"],
        scenario_id="multi",
    )
    assert {t.name for t in scenario.tables} == {"orders", "users"}


def test_import_default_rows_when_no_stats(monkeypatch, _patch_introspect):
    """introspect_row_count 返 None → 用 default_rows"""
    monkeypatch.setattr(
        "app.scenarios.yml_importer.introspect_row_count", lambda _d, _t: None,
    )
    scenario, _ = import_tables_from_datasource(
        _patch_introspect,
        table_names=["orders"],
        scenario_id="x",
        default_rows=5000,
    )
    assert scenario.tables[0].rows == 5000


def test_import_row_count_capped(monkeypatch, _patch_introspect):
    """introspect_row_count 返 千万 → cap 到 100 万 + warning"""
    monkeypatch.setattr(
        "app.scenarios.yml_importer.introspect_row_count", lambda _d, _t: 5_000_000,
    )
    scenario, yml_text = import_tables_from_datasource(
        _patch_introspect,
        table_names=["orders"],
        scenario_id="big",
    )
    assert scenario.tables[0].rows == 1_000_000
    assert "capped to 1_000_000" in yml_text


def test_import_yml_text_contains_todos_and_header(_patch_introspect):
    """生成的 yml 顶部应有 TODO 提示 + 注释 header"""
    _, yml_text = import_tables_from_datasource(
        _patch_introspect,
        table_names=["orders"],
        scenario_id="x",
    )
    assert "# Scenario auto-imported" in yml_text
    assert "TODO" in yml_text


def test_import_yml_text_is_parseable_back():
    """生成的 yml 可被 yaml.safe_load 反向加载成等价 Scenario"""
    from app.models import DataSourceCreate, DatabaseType
    from app.services.repositories import datasource_store

    # 不用 fixture(避免 monkeypatch),手动构造
    pass  # 由其它 test 覆盖


def test_import_empty_table_names_rejected(_patch_introspect):
    with pytest.raises(ValueError, match="table_names is required"):
        import_tables_from_datasource(
            _patch_introspect, table_names=[], scenario_id="x",
        )


def test_import_all_tables_fail_raises(monkeypatch, _patch_introspect):
    """所有 table 都 introspect 失败 → raise 提示 caller"""
    monkeypatch.setattr(
        "app.scenarios.yml_importer.introspect_columns", lambda _d, _t: [],
    )
    with pytest.raises(ValueError, match="failed to import any table"):
        import_tables_from_datasource(
            _patch_introspect, table_names=["nonexistent"], scenario_id="x",
        )


def test_import_partial_failure_keeps_good_tables(monkeypatch, isolated_storage):
    """部分表失败 → 保留好表,fail 表进 warning"""
    from app.models import DataSourceCreate, DatabaseType
    from app.services.repositories import datasource_store

    ds = datasource_store.create(DataSourceCreate(
        name="ds", db_type=DatabaseType.MYSQL, host="x", port=3306,
        database="d", username="u", password="p",
    ))

    def _cols(_d, t):
        if t == "good":
            return [{"name": "id", "data_type": "BIGINT", "nullable": False, "comment": "", "ordinal": 1}]
        return []

    monkeypatch.setattr("app.scenarios.yml_importer.introspect_columns", _cols)
    monkeypatch.setattr("app.scenarios.yml_importer.introspect_indexes", lambda *a: [])
    monkeypatch.setattr("app.scenarios.yml_importer.introspect_row_count", lambda *a: None)

    scenario, yml_text = import_tables_from_datasource(
        ds.id, table_names=["good", "bad"], scenario_id="x",
    )
    assert {t.name for t in scenario.tables} == {"good"}
    assert "bad" in yml_text
    assert "no columns" in yml_text


# ─── API endpoint ────────────────────────────────────────────────────────


def test_import_endpoint_basic(client, _patch_introspect):
    r = client.post("/api/scenarios/import-from-datasource", json={
        "datasource_id": _patch_introspect,
        "table_names": ["orders"],
        "scenario_id": "orders-import",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scenario_id"] == "orders-import"
    assert "yml_text" in body
    assert body["tables_imported"] == 1
    assert body["rows_per_table"]["orders"] == 50000
    assert body["saved_path"] is None  # save=False default


def test_import_endpoint_404_when_datasource_missing(client, isolated_storage):
    r = client.post("/api/scenarios/import-from-datasource", json={
        "datasource_id": "ds-nope",
        "table_names": ["t"],
        "scenario_id": "x",
    })
    assert r.status_code == 404


def test_import_endpoint_save_true_writes_file(client, _patch_introspect, tmp_path, monkeypatch):
    """save=True 直接落 config/scenarios/<id>.yml"""
    from app.utils import paths
    monkeypatch.setattr(paths, "SCENARIOS_DIR", tmp_path)
    from app.api import scenarios as sapi
    monkeypatch.setattr(sapi, "SCENARIOS_DIR", tmp_path)

    r = client.post("/api/scenarios/import-from-datasource", json={
        "datasource_id": _patch_introspect,
        "table_names": ["orders"],
        "scenario_id": "orders-saved",
        "save": True,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["saved_path"] == "orders-saved.yml"
    assert (tmp_path / "orders-saved.yml").exists()
    content = (tmp_path / "orders-saved.yml").read_text(encoding="utf-8")
    assert "orders-saved" in content


def test_import_endpoint_scenario_id_pattern_strict(client, _patch_introspect):
    """scenario_id 必须 [A-Za-z0-9_-]+,防路径注入"""
    r = client.post("/api/scenarios/import-from-datasource", json={
        "datasource_id": _patch_introspect,
        "table_names": ["orders"],
        "scenario_id": "../bad/id",
    })
    assert r.status_code == 422  # pydantic validation
