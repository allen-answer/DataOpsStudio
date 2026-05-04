"""Debug helper：跑 analyze_sql_lineage on a file 看 parse_errors / 动态 SQL / inference 触发情况。"""
from __future__ import annotations

import json
import sys

from app.lineage.analyzer import analyze_sql_lineage


def main(path: str, dialect: str = "oracle") -> int:
    with open(path, encoding="utf-8") as f:
        sql = f.read()
    print(f"file={path} chars={len(sql)} dialect={dialect}")
    result = analyze_sql_lineage(sql, dialect=dialect)

    parse_errors = result.get("parse_errors", []) or []
    print(f"\nparse_errors: {len(parse_errors)}")
    for i, pe in enumerate(parse_errors[:5]):
        err = (pe.get("error") or "").splitlines()[0][:200]
        sql_excerpt = (pe.get("sql") or "")[:120].replace("\n", " ")
        print(f"  [{i}] error={err}")
        print(f"      sql={sql_excerpt}...")

    dyn = result.get("dynamic_sql_segments", []) or []
    print(f"\ndynamic_sql_segments: {len(dyn)}")
    for i, d in enumerate(dyn[:5]):
        print(f"  [{i}] confidence={d.get('confidence')} source={d.get('source')} sql={(d.get('sql') or '')[:100]}")

    print(f"\ninsert_mappings: {len(result.get('insert_mappings', []))}")
    print(f"graph_edges: {len(result.get('graph_edges', []))}")
    tables_raw = result.get("tables", []) or []
    print(f"tables raw count={len(tables_raw)}, sample={tables_raw[:3]}")
    print(f"variables: {[v.get('name') for v in (result.get('variables', []) or [])[:10]]}")

    print(f"\nwarnings: {len(result.get('warnings', []))}")
    for w in (result.get("warnings", []) or [])[:5]:
        print(f"  - {w.get('type')}: {(w.get('message') or '')[:140]}")

    return 0


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/dws_test.sql"
    dialect = sys.argv[2] if len(sys.argv) > 2 else "oracle"
    sys.exit(main(path, dialect))
