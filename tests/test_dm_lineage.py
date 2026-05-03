from __future__ import annotations

from app.lineage.analyzer import analyze_sql_lineage


def _summary_by(target_summary, table):
    matches = [s for s in target_summary if s["target_table"].lower() == table.lower()]
    assert matches, f"target_summary missing {table}: {[s['target_table'] for s in target_summary]}"
    return matches[0]


def test_dm_aliases_inherit_oracle_parser():
    sql = "INSERT INTO dwd.fact_order SELECT order_id FROM ods.orders"

    for dialect in ("dm", "dm8", "dm_oracle", "dameng", "dameng8"):
        result = analyze_sql_lineage(sql, dialect=dialect)
        assert result["statement_count"] >= 1
        assert any(
            edge["source_table"] == "ods.orders"
            and edge["target_table"] == "dwd.fact_order"
            for edge in result["graph_edges"]
        )


def test_dm_anonymous_block_delete_insert_refresh_mode():
    sql = """
    BEGIN
      DELETE FROM dwd.fact_order WHERE biz_date = :biz_date;
      INSERT INTO dwd.fact_order (order_id, user_id, amount)
      SELECT o.order_id, o.user_id, o.amount
      FROM ods.orders o
      JOIN ods.refunds r ON r.order_id = o.order_id;
    END;
    /
    """

    result = analyze_sql_lineage(sql, dialect="dm")
    summary = _summary_by(result["target_summary"], "dwd.fact_order")

    assert summary["delete_count"] == 1
    assert summary["insert_count"] == 1
    assert summary["delete_before_insert"] is True
    assert summary["refresh_mode"] == "delete_insert_partial"

    edge_sources = {
        edge["source_table"]
        for edge in result["graph_edges"]
        if edge["target_table"] == "dwd.fact_order"
    }
    read_tables = {table["table"] for table in result["tables"]}
    assert "ods.orders" in edge_sources
    assert "ods.refunds" in read_tables


def test_dm_delete_with_subquery_keeps_table_level_edge():
    sql = """
    DELETE FROM dwd.fact_order
    WHERE EXISTS (
      SELECT 1
      FROM ods.cancel_order c
      WHERE c.order_id = dwd.fact_order.order_id
    );
    """

    result = analyze_sql_lineage(sql, dialect="dm")

    summary = _summary_by(result["target_summary"], "dwd.fact_order")
    assert summary["delete_count"] == 1

    assert any(
        edge["edge_type"] == "DELETE"
        and edge["source_table"] == "ods.cancel_order"
        and edge["target_table"] == "dwd.fact_order"
        for edge in result["graph_edges"]
    )
