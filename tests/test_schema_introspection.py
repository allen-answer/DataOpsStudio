from __future__ import annotations

import time

from app.services import schema_introspection


def _seed_cache(count: int, *, age_seconds: float = 0.0) -> None:
    schema_introspection.clear_schema_cache()
    base_ts = time.time() - age_seconds
    for i in range(count):
        key = (f"ds{i}", "", "", "", ())
        schema_introspection._CACHE[key] = (base_ts, {f"t{i}": ["c1"]})


def test_evict_cache_drops_expired_first():
    schema_introspection.clear_schema_cache()
    fresh_ts = time.time()
    stale_ts = fresh_ts - schema_introspection._CACHE_TTL_SECONDS - 60
    schema_introspection._CACHE[("fresh", "", "", "", ())] = (fresh_ts, {"a": ["x"]})
    schema_introspection._CACHE[("stale1", "", "", "", ())] = (stale_ts, {"a": ["x"]})
    schema_introspection._CACHE[("stale2", "", "", "", ())] = (stale_ts, {"a": ["x"]})

    schema_introspection._evict_cache()

    keys = {k[0] for k in schema_introspection._CACHE}
    assert "fresh" in keys
    assert "stale1" not in keys
    assert "stale2" not in keys
    schema_introspection.clear_schema_cache()


def test_evict_cache_falls_back_to_fifo_when_all_fresh():
    schema_introspection.clear_schema_cache()
    overflow = schema_introspection._CACHE_MAX_ENTRIES + 5
    _seed_cache(overflow)

    schema_introspection._evict_cache()

    assert len(schema_introspection._CACHE) == schema_introspection._CACHE_MAX_ENTRIES
    keys = {k[0] for k in schema_introspection._CACHE}
    # First-inserted ds0..ds4 evicted, latest survives.
    assert "ds0" not in keys
    assert f"ds{overflow - 1}" in keys
    schema_introspection.clear_schema_cache()


def test_evict_cache_no_op_when_under_budget():
    schema_introspection.clear_schema_cache()
    _seed_cache(10)
    before = len(schema_introspection._CACHE)

    schema_introspection._evict_cache()

    assert len(schema_introspection._CACHE) == before
    schema_introspection.clear_schema_cache()
